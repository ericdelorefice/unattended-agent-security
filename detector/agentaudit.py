#!/usr/bin/env python3
"""agentaudit - the mechanical half of an Agent Deployment Review.

Answers the two questions that open every review, from the code rather than from what the team
believes: **what can this agent actually do**, and **where will it fail silently**.

Every check here exists because the failure it looks for actually happened in production. The
bracketed case number cites `teardown/unattended-agent-failures.md`. It finds the shape of a bug,
not the bug - a finding is a place to look, and the review's judgement is still the reviewer's.

READ-ONLY. It parses files and writes nothing but its own report. Point it at a client's repo
without touching it. It sends nothing anywhere.

    python3 agentaudit.py <path> [--json] [--quiet]
"""
import argparse, ast, json, os, re, sys
from collections import defaultdict

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "env", ".mypy_cache",
             ".pytest_cache", "site-packages", "dist", "build", ".tox",
             # Test and example code plays by different rules: a swallowed exception in a fixture is
             # not a production silent failure, and reporting it wastes the reviewer's attention.
             "tests", "test", "testing", "examples", "example", "docs", "benchmarks", "vendor"}

# What a capability actually is: something that reaches outside the process and changes the world,
# or reads a secret. Grouped by blast radius, because "worst case in one run" is the headline finding.
CAPABILITIES = {
    "shell":      (["subprocess.run", "subprocess.Popen", "subprocess.call", "subprocess.check_output",
                    "subprocess.check_call", "os.system", "os.popen", "os.execv", "pty.spawn"],
                   "executes shell commands"),
    "code_exec":  (["eval", "exec", "compile", "__import__", "pickle.load", "pickle.loads",
                    "yaml.load", "marshal.loads"],
                   "executes or deserialises code"),
    "fs_write":   (["os.remove", "os.unlink", "os.rmdir", "shutil.rmtree", "shutil.move",
                    "os.rename", "os.replace", "pathlib.Path.unlink", "os.truncate"],
                   "deletes or moves files"),
    "network":    (["requests.get", "requests.post", "requests.put", "requests.delete", "requests.patch",
                    "urllib.request.urlopen", "httpx.get", "httpx.post", "aiohttp.request",
                    "socket.socket", "http.client.HTTPConnection"],
                   "makes outbound network calls"),
    "send":       (["smtplib.SMTP", "smtplib.SMTP_SSL", "sendmail", "send_message", "twilio",
                    "slack_sdk", "WebClient.chat_postMessage"],
                   "sends messages on someone's behalf"),
    "db_write":   (["execute", "executemany", "commit", "cursor"],
                   "writes to a database"),
}
WRITE_MODES = re.compile(r"^[rbt]*[wax]")
CRED_PAT = re.compile(r"(secret|token|api[_-]?key|password|passwd|credential|\.pem|\.key|auth|"
                      r"\.env|id_rsa|private[_-]?key)", re.I)
# Language a docstring uses when it is telling you what happens when something goes wrong. A
# docstring that never discusses failure is not an explanation of a swallowed exception.
EXPLAINS = re.compile(r"\b(rais(e|es|ing)|fail(s|ure|ing)?|except|error|cannot|can not|unusable|"
                      r"unavailable|missing|absent|treat(ed|ing|s)? .{0,20}as|fall(s|ing)? back|"
                      r"fallback|ignore[sd]?|not something we can|quietly|silently|may be (none|null)|"
                      r"is none|closed or replaced|frozen|embedded)\b", re.I)
# A swallowed exception only matters when the function's answer is a CLAIM someone acts on - is this
# authenticated, is this valid, does this exist, did that succeed. Measured 23 Sep: shape alone cannot
# separate the bug from the idiom. Every false positive sampled across three mature frameworks was a
# legitimate `except: return falsy` - a best-effort delattr, a `-> dict | None` returning None, a TTY
# check. Narrowing to predicates is the difference between a finding and a grep result.
# Operations that cannot fail for an external reason. Deliberately short: anything absent is
# treated as possibly external, which errs toward reporting.
PURE = {"urlparse", "getattr", "setattr", "hasattr", "delattr", "isinstance", "issubclass",
        "len", "bool", "int", "float", "str", "repr", "callable", "abs", "min", "max", "sum",
        "sorted", "any", "all", "list", "dict", "set", "tuple", "isatty", "strip", "lstrip",
        "rstrip", "split", "rsplit", "startswith", "endswith", "lower", "upper", "format",
        "replace", "join", "keys", "values", "items", "index", "count", "match", "search",
        "fullmatch", "findall", "sub", "loads", "dumps", "fromisoformat", "strftime", "strptime",
        "group", "groups", "encode", "decode", "hex", "compile", "next", "iter", "enumerate", "zip"}

PREDICATE = re.compile(r"^(is|has|can|should|was|did|are|check|verify|validate|ensure|test|supports?|"
                       r"allows?|exists?|contains?|matches?|needs?|requires?|logged|authenticated)_|"
                       r"_(ok|valid|exists|enabled|allowed|ready|authenticated|supported)$|"
                       r"^(logged_in|is_?tty|available|enabled)$", re.I)
# NOT "cursor" or "lock": `finally: cursor.close()` and `finally: lock.release()` are the textbook
# CORRECT use of finally, and flagging cleanup as a liveness stamp is backwards. Measured 23 Sep:
# including "cursor" put 10 false positives into a 12-finding report. A liveness stamp is a
# completion marker written to PERSISTENT storage, which is a much narrower thing.
STATE_PAT = re.compile(r"(stamp|last[_-]?(run|check|seen|success)|checkpoint|heartbeat|mark(ed)?_?(done|complete)|completed_at|updated_at)", re.I)
PERSIST = re.compile(r"(json\.dump|write_text|\.write\(|save\(|commit\(|set_state|store\(|persist)", re.I)
# Byte-scale constants: an absolute threshold on a resource the platform may size on its own. [7]
BIG_CONST = 1024 * 1024 * 64


def dotted(node):
    """Best-effort dotted name for a call target: requests.get, os.path.join, self.run."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


class Finding:
    __slots__ = ("kind", "case", "file", "line", "detail", "confidence")

    def __init__(self, kind, case, file, line, detail, confidence="probable"):
        self.kind, self.case, self.file, self.line = kind, case, file, line
        self.detail, self.confidence = detail, confidence

    def as_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}


class Auditor(ast.NodeVisitor):
    def __init__(self, path, rel, lines=None):
        self.path, self.rel = path, rel
        self.lines = lines or []
        self.findings, self.caps = [], defaultdict(list)
        self.fnstack = []

    def explained(self, node, look_back=2):
        """Is there a comment inside this block, or just above it, explaining the choice?

        Measured 23 Sep 2026 against four codebases: every false positive read in full carried a
        comment or docstring saying why the exception was swallowed, and the one real bug carried
        none. A documented swallow is a decision; an undocumented one is an accident. Comments do
        not survive into the AST, so this reads the source lines directly.
        """
        # An explanation can also sit in the enclosing function's docstring rather than beside the
        # handler. Measured 23 Sep: pydantic-ai's _display.py explains in a six-line docstring why
        # stderr may be missing, and _merge.py's says "treating an unusable __eq__ as no". Looking
        # only beside the handler missed both, which is why this second check exists.
        for fn in reversed(self.fnstack):
            doc = ast.get_docstring(fn) or ""
            if len(doc) > 40 and EXPLAINS.search(doc):
                return True
        if not self.lines:
            return False
        start = max(0, getattr(node, "lineno", 1) - 1 - look_back)
        end = min(len(self.lines), getattr(node, "end_lineno", getattr(node, "lineno", 1)))
        for ln in self.lines[start:end]:
            stripped = ln.strip()
            if stripped.startswith("#") and len(stripped) > 12:       # not "# noqa" or "# type:"
                return True
            if "  # " in ln and len(ln.split("  # ", 1)[1].strip()) > 12:
                return True
        return False

    def add(self, *a, **kw):
        self.findings.append(Finding(*a, **kw))

    # --- capabilities -------------------------------------------------------
    def visit_Call(self, node):
        name = dotted(node.func)
        tail = name.rsplit(".", 1)[-1]
        for cap, (names, _) in CAPABILITIES.items():
            if name in names or (cap == "db_write" and tail in names and "cursor" in name.lower()):
                self.caps[cap].append((self.rel, node.lineno, name))
        if tail == "open" and node.args:
            mode = next((a.value for a in node.args[1:2] if isinstance(a, ast.Constant)
                         and isinstance(a.value, str)), None)
            kw = next((k.value.value for k in node.keywords if k.arg == "mode"
                       and isinstance(k.value, ast.Constant)), None)
            mode = kw or mode or "r"
            target = node.args[0]
            literal = target.value if isinstance(target, ast.Constant) and isinstance(target.value, str) else ""
            if WRITE_MODES.match(mode):
                self.caps["fs_write"].append((self.rel, node.lineno, f"open(..., {mode!r})"))
            if literal and CRED_PAT.search(literal):
                self.caps["credentials"].append((self.rel, node.lineno, literal))

        # [5] unescape AFTER stripping tags reintroduces attribute payload as text.
        if tail in ("unescape",):
            self.add("unescape_order", 5, self.rel, node.lineno,
                     "html.unescape() here - confirm it runs BEFORE tags are stripped, not after. "
                     "Unescaping last turns attribute payload into matchable text.", "check")
        self.generic_visit(node)

    def provably_local(self, node):
        """Does this try block do ONLY pure, local computation?

        Detecting "reaches outside" positively is a losing game - `cluster.buckets().get_bucket()`
        looks like nothing in particular. The inverse is tractable: a short allowlist of operations
        that genuinely cannot fail for an external reason. Anything else is treated as possibly
        external, which errs toward reporting and is the right direction for a review.

        Measured 23 Sep 2026 by reading five survivors: the four false positives were urlparse,
        getattr and a TypeGuard - pure computation, where a raising check genuinely means "no" and
        there is no third state. The one true positive made a network call.
        """
        for n in ast.walk(node):
            if not isinstance(n, ast.Call):
                continue
            name = dotted(n.func)
            tail = name.rsplit(".", 1)[-1]
            if tail not in PURE:
                return False
        return True

    # --- silent failure -----------------------------------------------------
    def visit_Try(self, node):
        for h in node.handlers:
            bare = h.type is None
            broad = isinstance(h.type, ast.Name) and h.type.id in ("Exception", "BaseException")
            if not (bare or broad):
                continue
            body = h.body
            reraises = any(isinstance(n, ast.Raise) for n in ast.walk(h))
            logs = any(isinstance(n, ast.Call) and
                       re.search(r"(log|print|warn|error|exc_info|capture)", dotted(n.func), re.I)
                       for n in ast.walk(h))
            # [3] the dangerous shape: swallow the error AND answer the question negatively.
            for n in body:
                if isinstance(n, ast.Return):
                    v = n.value
                    if reraises:
                        break
                    # A bare `return None` is ambiguous: it is the bug when it is silent, and the CORRECT
                    # fix when it is a deliberate unknown sentinel that got logged. `return False` / 0 / ""
                    # is never a fix - it is a definite wrong answer. Learned 23 Sep: the tool flagged the
                    # very patch that fixed the bug it was built to find.
                    sentinel = v is None or (isinstance(v, ast.Constant) and v.value is None)
                    definite = isinstance(v, ast.Constant) and v.value in (False, 0, "")
                    fname = self.fnstack[-1].name if self.fnstack else ""
                    is_claim = bool(PREDICATE.search(fname))
                    # External reach is what makes a falsy answer a lie. And a handler that LOGS is
                    # recoverable: someone can see why. Both conditions, or it is not reported.
                    # Reported only when the check could have failed for an external reason AND
                    # the handler is silent. A logged failure is recoverable; a pure computation
                    # that raises genuinely means "no".
                    if ((definite or sentinel) and not self.provably_local(node) and not logs
                            and not self.explained(h) and is_claim):
                        self.add("could_not_check_is_no", 3, self.rel, n.lineno,
                                 f"{'bare except' if bare else 'except ' + h.type.id} returns "
                                 f"{'None' if v is None else ast.unparse(v)}"
                                 + (" and logs nothing" if sentinel else "") +
                                 ". 'I could not determine X' and 'X is false' become the same answer to "
                                 "the caller. A tri-state (True/False/None) that logs is the fix.",
                                 "likely" if definite else "check")
                        break
            # Off by default. At 355 of 453 findings in one framework and a sampled precision near
            # zero, reporting every unlogged handler buries the findings that matter. --all brings
            # it back for a reviewer who wants the raw list.
            if SHOW_ALL and not logs and not reraises and not self.explained(h):
                self.add("silent_swallow", 3, self.rel, h.lineno,
                         f"{'bare except' if bare else 'except ' + h.type.id} neither logs nor re-raises. "
                         "A refusal nobody records cannot be debugged.", "likely")
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        self._stamp_check(node)
        self._threshold_check(node)
        self.fnstack.append(node)
        self.generic_visit(node)
        self.fnstack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def _stamp_check(self, fn):
        """[1] A liveness stamp written unconditionally: the monitor then reports a dead job as healthy."""
        has_try = any(isinstance(n, ast.Try) for n in fn.body)
        if not has_try:
            return
        # a stamp written in `finally`, or at function level after the try, runs even when the work failed
        suspects = []
        for n in fn.body:
            if isinstance(n, ast.Try):
                for f in n.finalbody:
                    suspects += [(c, "finally") for c in ast.walk(f) if isinstance(c, ast.Call)]
        for c, where in suspects:
            src = ast.unparse(c) if hasattr(ast, "unparse") else dotted(c.func)
            # Both halves required: it must look like a completion marker AND like a write that
            # outlives the process. Either alone is cleanup or bookkeeping, not a monitoring lie.
            if STATE_PAT.search(src) and PERSIST.search(src) and not self.explained(c, look_back=3):
                self.add("unconditional_stamp", 1, self.rel, c.lineno,
                         f"state written in `{where}` inside {fn.name}() - it runs on the failure path too. "
                         "A liveness stamp must be written only on success, and record the artifact produced.",
                         "likely")

    def _threshold_check(self, fn):
        """[7] An absolute threshold on an elastic resource is a branch that can never fire."""
        for n in ast.walk(fn):
            if not isinstance(n, ast.Compare):
                continue
            for c in [n.left] + list(n.comparators):
                if isinstance(c, ast.Constant) and isinstance(c.value, (int, float)) and c.value >= BIG_CONST:
                    ctx = ast.unparse(n) if hasattr(ast, "unparse") else ""
                    if re.search(r"(swap|mem|ram|disk|free|avail|size|space|quota|usage)", ctx, re.I):
                        self.add("absolute_threshold", 7, self.rel, n.lineno,
                                 f"absolute threshold `{ctx[:90]}`. If the platform sizes this resource on "
                                 "demand, the branch can never fire. Use a ratio or a rate of change.",
                                 "likely")


def walk(root):
    if os.path.isfile(root):
        yield root, os.path.basename(root)
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for f in filenames:
            if f.endswith(".py"):
                full = os.path.join(dirpath, f)
                yield full, os.path.relpath(full, root)


def audit(root):
    findings, caps, files, errors = [], defaultdict(list), 0, []
    for full, rel in walk(root):
        try:
            src = open(full, encoding="utf-8", errors="replace").read()
            tree = ast.parse(src, filename=rel)
        except SyntaxError as e:
            errors.append((rel, f"could not parse: {e.msg} (line {e.lineno})"))
            continue
        files += 1
        a = Auditor(full, rel, src.splitlines())
        a.visit(tree)
        findings += a.findings
        for k, v in a.caps.items():
            caps[k] += v
    return findings, caps, files, errors


SHOW_ALL = False

ORDER = ["could_not_check_is_no", "unconditional_stamp", "absolute_threshold",
         "silent_swallow", "unescape_order"]


def report(root, findings, caps, files, errors, quiet=False):
    print(f"\nagentaudit  {os.path.abspath(root)}")
    print(f"{files} Python files parsed" + (f", {len(errors)} unparseable" for _ in [0]).__next__()
          if errors else f"{files} Python files parsed")
    print("\n== what this agent can actually do ==")
    if not caps:
        print("  nothing outside the process was detected.")
    labels = {**{k: v[1] for k, v in CAPABILITIES.items()},
              "credentials": "reads credential files", "fs_write": "writes, moves or deletes files"}
    for cap, hits in sorted(caps.items(), key=lambda kv: -len(kv[1])):
        where = ", ".join(sorted({h[0] for h in hits})[:3])
        more = f" +{len({h[0] for h in hits}) - 3} more" if len({h[0] for h in hits}) > 3 else ""
        print(f"  {labels.get(cap, cap):<34} {len(hits):>4} call sites   {where}{more}")
        if not quiet:
            for rel, line, name in sorted(hits)[:3]:
                print(f"      {rel}:{line}  {name}")

    print("\n== where it will fail silently ==")
    if not findings:
        print("  no patterns matched.")
    by_kind, seen = defaultdict(list), set()
    for f in findings:
        key = (f.kind, f.file, f.line)
        if key in seen:
            continue
        seen.add(key)
        by_kind[f.kind].append(f)
    for kind in ORDER:
        group = by_kind.get(kind)
        if not group:
            continue
        print(f"\n  [{kind}]  case #{group[0].case}  -  {len(group)} site(s)")
        for f in sorted(group, key=lambda f: (f.file, f.line))[:8]:
            print(f"    {f.file}:{f.line}  ({f.confidence})")
            print(f"      {f.detail}")
        if len(group) > 8:
            print(f"    ... and {len(group) - 8} more")
    for rel, msg in errors:
        print(f"\n  ! {rel}: {msg}")
    print(f"\n  {len(findings)} finding(s). Each is a place to look, not a confirmed bug.\n")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("path")
    p.add_argument("--json", action="store_true")
    p.add_argument("--quiet", action="store_true", help="capability counts without example sites")
    p.add_argument("--all", action="store_true", help="include every unlogged handler (noisy; see the docstring)")
    a = p.parse_args()
    globals()["SHOW_ALL"] = a.all
    if not os.path.exists(a.path):
        sys.exit(f"no such path: {a.path}")
    findings, caps, files, errors = audit(a.path)
    if a.json:
        print(json.dumps({"root": os.path.abspath(a.path), "files": files,
                          "capabilities": {k: [list(x) for x in v] for k, v in caps.items()},
                          "findings": [f.as_dict() for f in findings],
                          "unparseable": errors}, indent=2))
    else:
        report(a.path, findings, caps, files, errors, a.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
