#!/usr/bin/env python3
"""Bounded permissions, a tamper-evident audit trail, and human approval for consequential actions.

A small reference implementation of the three controls an agent needs before it is allowed to act on anything
that matters. It is deliberately about 200 lines: every one of these is easy to describe and easy to get
subtly wrong, and the failures are quiet.

Each control here exists because the absence of it caused a real, observed failure in a system that ran
unattended against live mail, calendars and third-party web accounts for several months:

  DEFAULT DENY        A permission check that defaults to allow will one day be asked about something nobody
                      enumerated, and will say yes. The observed case was not a permission check at all but the
                      same shape: a reconciliation step decided which records to keep with
                      `ok.get(source, True)`, a source nobody had listed hit the True default, and the routine
                      deleted a fortnight of the user's data while reporting success. Unknown must mean no.
  DENY BEATS ALLOW    Rules get added over time by different people. If evaluation is first-match-wins, the
                      order of a list silently becomes a security boundary. Denies are evaluated first here, so
                      adding an allow can never widen an existing prohibition.
  LOG THE REFUSALS    A system that logs only what it did cannot be debugged when it does nothing. The observed
                      case: a request was accepted, the work silently failed, and the log showed the request
                      followed by nothing - with no way to tell answered from errored from hung.
  SINGLE USE, EXPIRING A human approval that can be replayed is not an approval, it is a key. Approvals here
                      are consumed on use and expire on a clock.
  CHAIN THE LEDGER    An append-only file is only append-only if something notices when it is not. Each entry
                      commits to the previous one, so a deletion or an edit anywhere breaks verification.

No dependencies beyond the standard library. No network. Nothing here is specific to any provider or product.
"""
from __future__ import annotations

import fnmatch
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Iterable


# --------------------------------------------------------------------------- policy

@dataclass(frozen=True)
class Rule:
    """One decision about one shape of action. `pattern` is matched with fnmatch against "<action> <target>"."""
    pattern: str
    effect: str          # "allow" | "deny" | "ask"
    why: str = ""

    def __post_init__(self):
        if self.effect not in ("allow", "deny", "ask"):
            raise ValueError(f"effect must be allow, deny or ask, not {self.effect!r}")


@dataclass
class Policy:
    rules: list[Rule] = field(default_factory=list)
    default: str = "deny"          # the whole point; changing this is a decision, not a default

    def decide(self, action: str, target: str = "") -> tuple[str, str]:
        """Returns (effect, why). Denies are considered before anything else, so rule order cannot widen a
        prohibition - adding an allow to the end of a list is always safe."""
        subject = f"{action} {target}".strip()
        for effect in ("deny", "ask", "allow"):
            for r in self.rules:
                if r.effect == effect and fnmatch.fnmatch(subject, r.pattern):
                    return effect, r.why or f"matched {r.pattern!r}"
        return self.default, "no rule matched, so the default applied"


# --------------------------------------------------------------------------- ledger

class Ledger:
    """Append-only JSON lines, each entry committing to the hash of the one before it.

    The chain is what makes it append-only in practice rather than by convention: remove or edit any line and
    every later line fails verification, which `verify()` reports with the first bad index.
    """

    def __init__(self, path: str, key: bytes | None = None):
        self.path = path
        self.key = key            # optional: HMAC instead of a bare hash, so the chain cannot be recomputed
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def _digest(self, prev: str, body: str) -> str:
        msg = (prev + body).encode()
        return hmac.new(self.key, msg, hashlib.sha256).hexdigest() if self.key else hashlib.sha256(msg).hexdigest()

    def _last_hash(self) -> str:
        last = ""
        if os.path.exists(self.path):
            with open(self.path) as f:
                for line in f:
                    if line.strip():
                        try:
                            last = json.loads(line)["hash"]
                        except Exception:
                            break
        return last

    def append(self, **fields) -> dict:
        prev = self._last_hash()
        entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **fields, "prev": prev}
        body = json.dumps(entry, sort_keys=True, separators=(",", ":"))
        entry["hash"] = self._digest(prev, body)
        with open(self.path, "a") as f:
            f.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
        return entry

    def entries(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        out = []
        with open(self.path) as f:
            for line in f:
                if line.strip():
                    out.append(json.loads(line))
        return out

    def verify(self) -> tuple[bool, int | None]:
        """(ok, first_bad_index). Recomputes the chain; any edit or deletion shows up here."""
        prev = ""
        for i, e in enumerate(self.entries()):
            claimed = e.get("hash")
            body = {k: v for k, v in e.items() if k != "hash"}
            if body.get("prev") != prev:
                return False, i
            recomputed = self._digest(prev, json.dumps(body, sort_keys=True, separators=(",", ":")))
            if not hmac.compare_digest(str(claimed), recomputed):
                return False, i
            prev = claimed
        return True, None


# --------------------------------------------------------------------------- approvals

class Approvals:
    """Single-use, expiring grants for actions the policy marks "ask".

    Scoped to one subject. A grant for "send email" does not authorise "delete email", and a grant used once
    cannot be used again - which is the difference between an approval and a key.
    """

    def __init__(self, ttl_seconds: int = 300):
        self.ttl = ttl_seconds
        self._open: dict[str, tuple[str, float]] = {}

    def grant(self, subject: str, now: float | None = None) -> str:
        token = secrets.token_urlsafe(16)
        self._open[token] = (subject, (now if now is not None else time.time()) + self.ttl)
        return token

    def consume(self, token: str, subject: str, now: float | None = None) -> tuple[bool, str]:
        now = now if now is not None else time.time()
        found = self._open.get(token)
        if not found:
            return False, "no such approval, or it was already used"
        granted_subject, expires = found
        if now > expires:
            del self._open[token]
            return False, "the approval expired"
        if granted_subject != subject:
            return False, f"the approval was for {granted_subject!r}, not {subject!r}"
        del self._open[token]                      # single use: consumed whether or not the action succeeds
        return True, "approved"


# --------------------------------------------------------------------------- the gate

@dataclass
class Decision:
    allowed: bool
    effect: str
    why: str


class Guard:
    """The one place an agent's actions pass through."""

    def __init__(self, policy: Policy, ledger: Ledger, approvals: Approvals | None = None):
        self.policy, self.ledger, self.approvals = policy, ledger, approvals or Approvals()

    def check(self, action: str, target: str = "", approval: str | None = None,
              now: float | None = None) -> Decision:
        effect, why = self.policy.decide(action, target)
        subject = f"{action} {target}".strip()
        allowed = effect == "allow"
        if effect == "ask":
            if approval is None:
                allowed, why = False, "needs a human approval, and none was supplied"
            else:
                allowed, why = self.approvals.consume(approval, subject, now=now)
        # Logged whatever the outcome. A refusal that leaves no trace is indistinguishable from a hang.
        self.ledger.append(action=action, target=target, effect=effect, allowed=allowed, why=why)
        return Decision(allowed, effect, why)
