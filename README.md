# Unattended agent security

Field notes, a reference implementation and a checklist for LLM agents that act on live systems
**without a human watching each action.**

I ran one for months against my own mail, calendar, reminders, several third-party web accounts and a
local speech interface, with scheduled jobs that fired at 0445 whether or not I was awake. It broke
constantly, and almost never in the way the literature says agents break. No prompt injections. No
jailbreaks. No hallucinated facts. Ten boring engineering failures, made dangerous by the fact that a
machine was acting on them with nobody watching.

The finding that surprised me most:

> **Nine of the ten failures reported success while failing.**

That is the real security posture of an unattended agent. It is not that it does the wrong thing — it
is that it does the wrong thing quietly, and the monitoring you built agrees with it.

## Contents

**[The teardown](teardown/unattended-agent-failures.md)** — ten failures, each with the symptom, the
real cause, the measurement that found it, and the control that prevents it. Among them: a scheduled
job that stamped itself complete after crashing, so the freshness monitor stayed green for days. A
CSRF token that rotated on restart and silently disabled a safety feature, in a way indistinguishable
from the user choosing not to use it. A dependency auto-update that left a dead binary path, whose
health check then advised re-authenticating a perfectly valid session. An HTML-stripping bug that
manufactured an 81% signal where the truth was 2%, and nearly drove a real decision.

**[agent-guardrails/](agent-guardrails/)** — a small, dependency-free reference implementation of the
controls the teardown argues for:

- **Default-deny policy**, where *deny beats allow regardless of rule order* — so a permission list
  edited by several people over a year never makes ordering a security boundary.
- **A hash-chained audit ledger** that logs refusals, no-ops and could-not-determines alongside
  successes, and fails verification if an entry is edited or deleted. Optionally keyed, for when the
  recorder and the reader are different parties.
- **Single-use, expiring, target-bound approvals** for consequential actions — not a standing
  "may send email" that outlives the reason it was granted.

```
python3 agent-guardrails/test_guardrails.py    # 17 tests, each named for the failure it prevents
```

**[The checklist](checklist/agent-deployment-checklist.md)** — one page, derived from the other two.
Every item cites the case that produced it. Written to be run through before an agent goes unattended,
not after.

## The short version

If you are putting an agent somewhere it will act without a human in the loop:

1. **Make refusals loud and logged.** A system that logs only what it did cannot be debugged the day
   it does nothing.
2. **Monitor outputs, not runs.** "Did the job execute" is nearly free to satisfy and nearly worthless.
3. **Never let "I could not check" collapse into "no."** One grep for bare excepts. Highest-value hour
   on this list.
4. **Default to preserving.** Anything destructive should have to prove it observed absence, not merely
   fail to observe presence.
5. **Sample the output in the modality a human would judge it in.** Proxy metrics get gamed by your own
   system without either of you intending it.
6. **Require a human for the consequential and narrow set**, with approvals that expire.

Every failure in the teardown would have happened with a perfect model. That is the part I did not
expect going in, and it is the part most worth passing on: **the model is rarely the weak component.**
The weak components are the ones they have always been — error handling, monitoring, defaults and
thresholds — and what the agent changes is that now they run at 0445 with nobody watching, and they are
very good at telling you they are fine.

## Provenance

The failures are from a system I built and operated on my own machine, against my own accounts. No
client data, no third-party data and no personal data appears anywhere in this repository. Cases have
been described at the level of the engineering fault, which is the level that transfers.

MIT licensed — use the guardrails code, take the checklist into your own repo, no attribution required.
