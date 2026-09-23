# Ten ways an unattended agent broke in production

I ran an LLM agent unattended against live systems for months: my own mail, calendar, reminders,
several third-party web accounts, a local speech interface, and a set of scheduled jobs that ran
whether or not I was awake. Not a demo. Not a benchmark. A thing I depended on, that ran at 0445
and told me what my day looked like.

It broke constantly, and almost never in the way the literature says agents break. Nothing below is
a prompt injection, a jailbreak, or a hallucinated fact. Every one of these is a boring engineering
failure made dangerous by the fact that a machine was acting on it with nobody watching.

The interesting property they share: **nine of the ten reported success while failing.** That is the
actual security posture of an unattended agent. It is not that it does the wrong thing; it is that it
does the wrong thing quietly, and the monitoring you built agrees with it.

Each item below has the symptom, the real cause, the measurement that found it, and the control that
would have caught it. The controls are implemented in the companion reference implementation.

---

## 1. The job that failed and stamped itself complete

**Symptom:** nothing. For several days, a scheduled 0445 job produced no output and raised no alarm.

**Cause:** the job wrote its completion timestamp unconditionally at the end of the run. The freshness
monitor read that timestamp, saw it was recent, and stayed quiet. The job had been failing for days
inside a try block whose except printed to a log nobody read.

**Found by:** reading the output file by hand for an unrelated reason and noticing the content was
stale while the timestamp was current.

**Cost:** the single highest-value automation in the system was dead and the health dashboard was green.

**Control:** a liveness stamp must be written *only* on success, and it must record the thing produced,
not the fact of running. Monitoring that asks "did it run?" is nearly worthless; monitoring that asks
"is the output it was supposed to produce actually there and actually new?" is the whole job. If you
write one piece of monitoring for an agent, write that one.

---

## 2. The security control that silently disabled a feature

**Symptom:** the voice interface stopped responding. No error, no banner. The toggle simply did nothing.

**Cause:** the local server generated a fresh CSRF token at startup. An already-open browser page held
the old one. Every POST from that page returned 403. The page's error handling for a failed toggle was
to leave the toggle off, which looks identical to the user deciding not to turn it on.

**Found by:** restarting the server myself for an unrelated fix and noticing the feature I had just been
using was gone. If I had not been the one who restarted it, I would have assumed the feature was flaky.

**Cost:** a security control (CSRF) disabled a safety feature (the interface's off switch behaved like an
on switch that never worked), and the failure mode was indistinguishable from normal use.

**Control:** two things. Persist the token across restarts so a session is not silently invalidated by
an operational event. And make the *client* surface an auth failure loudly rather than degrading into a
plausible-looking resting state. A refused action that looks like an un-taken action is the worst
possible UI for an agent, because it removes the user's ability to tell refusal from inaction.

---

## 3. The dependency that updated itself out from under the agent

**Symptom:** the health check reported the agent was signed out. It was not.

**Cause:** the underlying CLI auto-updated from one patch version to the next. The updater deletes the
old versioned directory. The agent had cached an absolute path into that directory at startup. The
resulting `FileNotFoundError` was caught by a bare `except:` whose fallback return value was "not
authenticated."

**Found by:** the user telling me the report was wrong, and me checking the binary path instead of
believing the health check.

**Cost:** near-miss. The remediation the health check recommended was "log in again," which would have
sent a user to re-authenticate a perfectly valid session — training them to enter credentials in
response to a bug. That is the exact reflex phishing depends on.

**Control:** never cache a path to a thing that updates itself; re-resolve. And never let a bare except
turn "I could not determine X" into "X is false." Those are different answers and only one of them is
honest. An agent that cannot distinguish *failure to check* from *checked and negative* will eventually
tell you a control is off when it is on, or on when it is off.

---

## 4. The backwards default that deleted data

**Symptom:** six records vanished from local state.

**Cause:** a diff-and-commit step compared a fresh pull against the stored state. One upstream source
had not been re-pulled that run. Items from unre-pulled sources were treated as *absent* rather than
*unknown*, so the commit removed them.

**Found by:** noticing a count drop, then reading the diff output that had scrolled past earlier.

**Cost:** real data loss, recovered only because the source system still had it.

**Control:** in any reconcile step, the set of sources you actually refreshed is a first-class input.
Absent-from-a-source-you-did-not-read is not absent. Default to preserving. Anything that deletes on
the basis of a negative observation needs to prove it made the observation.

---

## 5. The data-cleaning bug that manufactured a finding

**Symptom:** a scanner reported that 81% of job postings in a sample required a specific compliance
standard. That number was the basis of a real decision about what to build.

**Cause:** the HTML stripper unescaped entities *after* removing tags instead of before. One rich-text
editor emits a `data-ccp-props` attribute containing escaped JSON. Stripping tags left the attribute
payload behind as text; unescaping it afterwards produced a literal string containing the token being
searched for. It matched in 400 of 400 postings regardless of content.

**Found by:** the number being too good. 81% was not plausible, so I printed the matched span for ten
postings and found the same attribute noise in all ten.

**Cost:** the true rate was 2%. A 40x error, in the direction of "yes, build this."

**Control:** unescape before stripping, obviously. But the general control is that any surprisingly
strong signal must be traceable to the specific bytes that produced it, and you should look at those
bytes before you act. An agent that reports aggregates without keeping a path back to evidence will
eventually hand you a confident number built entirely out of markup.

---

## 6. The metric that stood in for the thing that mattered

**Symptom:** an automated clip selector, asked to pick the most engaging opening moment from source
video, chose a ratings card and a title screen.

**Cause:** the score was motion energy plus scene-change rate. Those are measurable. "Hook" is not.
A ratings card fading into a title screen has enormous frame-to-frame delta and scores brilliantly.

**Found by:** extracting a still from the chosen start frame and looking at it. One image.

**Cost:** several wasted render cycles and an output that was obviously bad to any human and invisible
to every metric in the pipeline.

**Control:** when an agent optimises a proxy, sample the artifact in the modality a human would judge it
in, and look. A cheap human-modality spot check catches a whole class of proxy-gaming that no amount of
additional metric engineering will. Add a hard guard for the known-degenerate case too — here, blown or
black frames at either luma extreme.

---

## 7. The threshold that could never fire

**Symptom:** memory pressure alerting never triggered, on a machine that was in fact under sustained
memory pressure.

**Cause:** the thresholds were absolute: alert above N gigabytes of swap, or below M gigabytes free.
This OS sizes its swap file dynamically, growing it on demand. Swap usage therefore tracked demand and
free space stayed pinned near zero by design. Neither threshold could ever be crossed in the direction
that meant trouble.

**Found by:** the system thrashing badly enough to be obvious, while the health file said everything
was fine.

**Cost:** see the next one — this is what made it possible.

**Control:** know whether the resource you are thresholding is fixed or elastic. For elastic resources
the meaningful signal is a ratio or a rate of change, never an absolute. More generally: a threshold
that has never fired is not evidence of health, it is an untested branch. Test alerts by forcing them.

---

## 8. The performance complaint that was three different problems

**Symptom:** "the voice is really slow and sounds off."

**Cause:** three unrelated things, and the obvious one was wrong.
- Measured pace was 165-168 words per minute against a 165 target. The *setting* everyone would reach
  for first was already correct.
- A separate heavy render job was running at 400% CPU, competing for the same cores.
- The speech model's resident process had been paged out under memory pressure (see 7). Generation was
  running at **4.52x realtime cold against 0.55x warm** — an 8x throughput collapse. Restarting it
  restored 1.26x immediately.

**Found by:** measuring each layer separately instead of adjusting the parameter named in the complaint.
Words per minute, CPU contention, and cold-vs-warm throughput are three different numbers.

**Cost:** near-miss, and the interesting kind. Had I "fixed" the pace setting as reported, I would have
slowed speech that was already correct, felt like I had responded, and left the actual 8x problem in
place — now harder to find because the obvious symptom had been papered over.

**Control:** a user's description of a symptom names a layer, and it is usually the wrong layer. Never
tune the parameter the complaint names until you have measured it. For agents specifically: log the
throughput of every model call with a warm/cold flag. Paging is invisible from inside the process and
looks exactly like the model getting slower.

---

## 9. The 94%-accurate claim used as a delete authorization

**Symptom:** a folder named to assert its own contents were redundant — the name said the files had
already been copied elsewhere — sitting in the way of a cleanup.

**Cause:** the claim was mostly true. 945 of 964 documents did exist elsewhere. Nineteen did not.
Among those nineteen were identity paperwork and an exported credential file.

**Found by:** refusing to act on the folder's name and hashing the contents against the rest of the
filesystem. Name-based comparison first to narrow it, content hashes to settle it.

**Cost:** none, because nothing was deleted. Had it been trusted, a 98% accurate claim would have
destroyed the 2% that mattered most — which is the normal shape of this failure, since the files that
are hardest to re-obtain are exactly the ones least likely to have been routinely duplicated.

**Control:** never treat metadata, filenames, or a previous process's assertion as evidence about
content. Verify against the content. And understand that accuracy percentages are the wrong frame for
destructive actions: what matters is the cost of the residual, and for deletion the residual is always
the irreplaceable tail.

---

## 10. The pass/fail bar that was hardcoded to the wrong denominator

**Symptom:** none yet. Caught before it fired.

**Cause:** a pass threshold written as "14 or better", for scored items assumed to be out of 15. Some were
out of 14. A perfect 14/14 passes, fine — but a 13/14 (93%) fails while a 13/15 (87%) passes.

**Found by:** a review pass that asked, for every comparison against a constant, where the constant
came from.

**Cost:** zero. Included because it is the cheapest possible instance of the most common agent bug I
found: **a number learned from one example, frozen into a comparison, and applied to a population.**
Items 5, 7 and 10 are all that bug wearing different clothes.

**Control:** compare shares, not raw counts, wherever the denominator can vary. Treat every literal in
a conditional as a claim about the world that needs a source.

---

## What I would tell you to do first

If you are putting an agent somewhere it will act without a human in the loop, in order:

1. **Make refusals loud and logged.** Every system above that hurt me was quiet. An agent that logs only
   what it did cannot be debugged the day it does nothing. Log the refusals, the no-ops and the
   could-not-determines, with the reason, in the same place as the successes.
2. **Monitor outputs, not runs.** "Did the job execute" is nearly free to satisfy and nearly worthless.
   "Is the artifact it exists to produce present and fresh" is the real question.
3. **Never let "I could not check" collapse into "no."** This is one grep for bare excepts and it is the
   highest-value hour you will spend.
4. **Default to preserving.** Any destructive or removing action should have to prove it observed
   absence, not merely fail to observe presence.
5. **Sample the output in the modality a human would judge it in.** Look at the frame. Read the ten rows.
   Play the audio. Proxy metrics get gamed by your own system without either of you intending it.
6. **Require a human for the consequential and narrow set**, with single-use, expiring, target-bound
   approvals — not a blanket "yes, you may send email" that outlives the reason it was granted.

None of this is about the model. Every failure here would have happened with a perfect model. That is
the part I did not expect going in, and it is the part I would most want someone deploying an agent to
hear: **the model is rarely the weak component.** The weak components are the same ones they have always
been — error handling, monitoring, defaults, and thresholds — and what the agent changes is that now
they run at 0445 with nobody watching, and they are very good at telling you they are fine.

---

*The controls described here are implemented, with tests, in the companion reference implementation:
a default-deny policy engine, a hash-chained audit ledger that logs refusals as well as actions, and
single-use expiring approvals for consequential operations.*
