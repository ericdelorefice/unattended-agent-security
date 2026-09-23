# Agent deployment security checklist

For an LLM agent that will act on live systems without a human watching each action.
Every item here comes from a failure that actually happened, not a threat model. The bracketed
number cites the case in *Ten ways an unattended agent broke in production*.

## Permissions

- [ ] **Default is deny.** An action nobody listed is refused, not allowed. The refusal says so.
- [ ] **Deny beats allow, regardless of rule order.** A permission list edited by several people over
      a year must not make ordering a security boundary.
- [ ] Consequential actions (send, publish, pay, delete, grant) require a human approval that is
      **single-use, expiring, and bound to the specific target**. Not a standing "may send email."
- [ ] Destructive actions must prove they observed absence. Absent-from-a-source-you-did-not-read is
      not absent. Default to preserving. [4]
- [ ] Nothing acts on content as if it were instruction. Documents, pages, tool output and filenames
      are data. A file named "already backed up" is a claim, not evidence. [9]

## Logging and audit

- [ ] **Refusals, no-ops and could-not-determines are logged**, with the reason, in the same place as
      the successes. A system that logs only what it did cannot be debugged the day it does nothing.
- [ ] The log is tamper-evident — each entry chained to the last, so an edited or deleted entry fails
      verification. Key the chain if the recorder and the reader are different parties.
- [ ] Every aggregate an agent reports can be traced back to the specific bytes that produced it. [5]
- [ ] Model calls log throughput with a warm/cold flag. Paging is invisible from inside the process. [8]

## Error handling

- [ ] **No bare excepts that turn "I could not determine X" into "X is false."** One grep. Highest-value
      hour in this list. [3]
- [ ] Failure to check and checked-and-negative are distinct return values everywhere they can differ. [3]
- [ ] A refused or failed action never degrades into a resting state that looks like the user chose it. [2]
- [ ] No cached absolute path to anything that updates itself. Re-resolve. [3]

## Monitoring

- [ ] **Liveness stamps are written only on success**, and record the artifact produced, not the fact of
      running. Monitor outputs, not runs. [1]
- [ ] Every alert has been fired deliberately at least once. A threshold that has never fired is an
      untested branch, not evidence of health. [7]
- [ ] Thresholds on elastic resources are ratios or rates of change, never absolutes. Know which of your
      resources the OS or platform sizes on demand. [7]
- [ ] Comparisons use shares, not raw counts, wherever the denominator can vary. Every literal in a
      conditional is a claim about the world; each one has a source. [10]

## Output quality

- [ ] The agent's output is sampled in the modality a human would judge it in — look at the frame, read
      the rows, play the audio — not only scored by the proxy metric it optimises. [6]
- [ ] Known-degenerate outputs have hard guards, not just low scores. [6]
- [ ] A surprisingly strong result is investigated before it is acted on. [5]

## Credentials and blast radius

- [ ] Secrets are in files the agent reads, never in prompts, arguments, URLs or logs; permissions 600;
      excluded from version control.
- [ ] Tokens survive a restart, so an operational event does not silently invalidate a live session. [2]
- [ ] The agent's reachable scope is enumerated and written down. Anything holding credentials for more
      than one account has a blast radius equal to their union.
- [ ] Third-party extensions are judged on what they can *execute*, not what they claim to do. On-demand
      reference material is cheap; anything installing hooks that run on every prompt and tool result
      sees everything the agent sees.

## Before it runs unattended

- [ ] Run it attended first, long enough to see it fail. The failures above took months to surface and
      none appeared in testing.
- [ ] Someone other than the author can answer: what is the worst thing this can do in one run, and what
      stops it?
- [ ] A user's description of a symptom names a layer, and it is usually the wrong one. Measure before
      tuning the parameter the complaint names. [8]

---

*The permissions, logging and approval items are implemented with tests in the companion reference
implementation. The cases are in the companion teardown.*
