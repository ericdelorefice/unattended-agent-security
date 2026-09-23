#!/usr/bin/env python3
"""Tests written as the failures they prevent. Each name is a thing that actually went wrong somewhere."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from guardrails import Approvals, Guard, Ledger, Policy, Rule   # noqa: E402

PASS = []


def check(name, cond):
    PASS.append((name, bool(cond)))
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")


def fresh(tmp, rules, default="deny"):
    return Guard(Policy(rules, default=default), Ledger(os.path.join(tmp, "audit.jsonl")), Approvals(ttl_seconds=60))


def main():
    with tempfile.TemporaryDirectory() as tmp:
        rules = [Rule("read *", "allow"), Rule("send email *", "ask"), Rule("delete *", "deny", "never automatic")]

        g = fresh(tmp, rules)
        check("an unlisted action is refused, not allowed", not g.check("wire", "money").allowed)
        check("the refusal says the default applied", "default" in g.check("wire", "money").why)

        # A permission list is edited by many hands over time. Order must not become a security boundary.
        g = fresh(tmp, [Rule("delete *", "deny"), Rule("delete drafts", "allow")])
        check("deny wins even when an allow is more specific", not g.check("delete", "drafts").allowed)
        g = fresh(tmp, [Rule("delete drafts", "allow"), Rule("delete *", "deny")])
        check("deny wins regardless of rule order", not g.check("delete", "drafts").allowed)

        g = fresh(tmp, rules)
        check("an allowed action passes", g.check("read", "inbox").allowed)
        check("an ask with no approval is refused", not g.check("send email", "cadre").allowed)

        tok = g.approvals.grant("send email cadre")
        check("an approved ask passes", g.check("send email", "cadre", approval=tok).allowed)
        check("the same approval cannot be replayed", not g.check("send email", "cadre", approval=tok).allowed)

        tok2 = g.approvals.grant("send email cadre")
        check("an approval does not transfer to another target",
              not g.check("send email", "everyone", approval=tok2).allowed)

        a = Approvals(ttl_seconds=10)
        t = a.grant("send email x", now=1000.0)
        check("an approval expires on the clock", not a.consume(t, "send email x", now=1011.0)[0])

        # A system that logs only what it did cannot be debugged when it does nothing.
        led = Ledger(os.path.join(tmp, "refusals.jsonl"))
        g2 = Guard(Policy(rules), led)
        g2.check("delete", "everything")
        g2.check("read", "inbox")
        entries = led.entries()
        check("refusals are logged, not just successes", any(e["allowed"] is False for e in entries))
        check("successes are logged too", any(e["allowed"] is True for e in entries))
        check("the ledger verifies when untouched", led.verify()[0])

        # Tamper with the middle line and the chain must notice.
        path = os.path.join(tmp, "refusals.jsonl")
        lines = open(path).read().splitlines()
        lines[0] = lines[0].replace('"allowed":false', '"allowed":true')
        open(path, "w").write("\n".join(lines) + "\n")
        ok, bad = Ledger(path).verify()
        check("an edited entry breaks verification", not ok and bad == 0)

        # Deleting a line must also break it, not merely shorten the file.
        path2 = os.path.join(tmp, "chain.jsonl")
        l2 = Ledger(path2)
        for i in range(4):
            l2.append(action="step", target=str(i), effect="allow", allowed=True, why="")
        lines = open(path2).read().splitlines()
        del lines[1]
        open(path2, "w").write("\n".join(lines) + "\n")
        check("a deleted entry breaks verification", not Ledger(path2).verify()[0])

        # An HMAC ledger cannot simply be recomputed by whoever edited it.
        path3 = os.path.join(tmp, "hmac.jsonl")
        keyed = Ledger(path3, key=b"a-secret-only-the-recorder-has")
        keyed.append(action="x", target="y", effect="allow", allowed=True, why="")
        check("a keyed ledger does not verify without the key", not Ledger(path3).verify()[0])
        check("a keyed ledger verifies with it", Ledger(path3, key=b"a-secret-only-the-recorder-has").verify()[0])

    bad = [n for n, ok in PASS if not ok]
    print(f"\n  {len(PASS) - len(bad)}/{len(PASS)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
