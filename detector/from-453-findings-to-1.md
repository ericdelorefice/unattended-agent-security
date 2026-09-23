# From 453 findings to 1

Notes on making a silent-failure detector worth reading.

---

I built a static analyser for the failure described in the teardown next to this file: an agent that
reports success while failing. It looks for three shapes in Python. A check that answers "no" when it
could not check. A completion marker written whether or not the work completed. An absolute threshold on
a resource the platform sizes on demand.

It worked well on the codebase it was built from, which is exactly the problem. **A detector validated
on the code that inspired it has not been validated.**

**The short version:** pointed at three mature agent frameworks it produced 453, 341 and 29 findings.
I read a sample and every one was a legitimate idiom. Four changes took those numbers to 0, 1 and 0,
and the one that survived is a real bug with a pull request against it. The changes are not clever. What
made them possible was reading the findings one at a time and writing down why each was wrong.

## What the first version did

| Codebase | Python files | Findings |
|---|---|---|
| The agent it was built from | 42 | 21 |
| pydantic-ai | 778 | 29 |
| openai-agents-python | 955 | **453** |
| crewAI | 1,383 | **341** |

Three findings from pydantic-ai, read in full, were all false positives. A TTY check. A documented
decision to treat an unusable `__eq__` as "not equal". A `finally` block that deliberately captures a
partial response after a stream is interrupted.

Handing a client 453 findings at that precision would end the engagement. The patterns are real
patterns; they are also how correct Python is often written.

## The four changes

**1. Documented means deliberate.** Every false positive carried a comment beside the handler or a
docstring that discussed the failure. The one real bug carried neither. A swallowed exception with an
explanation is a decision; one without is an accident. Comments do not survive into the AST, so this
reads the source lines and the enclosing function's docstring directly.

This alone was not enough, and the way it failed is the useful part: the first version looked only two
lines above the handler and missed both pydantic-ai cases, whose explanations sat in a six-line
docstring further up. A 38% reduction looked like success and was not.

**2. Only claims count.** `except: return False` is ordinary Python. It is a *bug* only when the
function's answer is a claim someone acts on - `is_*`, `has_*`, `verify_*`, `logged_in`. Narrowing to
predicates did most of the work.

**3. A liveness stamp is not cleanup.** My own state pattern included the word `cursor`, so
`finally: cursor.close()` - the textbook correct use of `finally` - produced ten of twelve findings in
one report. The detector now requires a completion marker *and* a persistent write.

**4. Pure computation may answer "no". External systems may not.** This is the rule that finished it,
and it came from reading the five survivors. Four were pure local computation - `urlparse`, `getattr`, a
`TypeGuard` - where a raising check genuinely means "no" and there is no third state to return. The
fifth reached a database over the network, where a failure means *unknown* and never *no*. A sixth was
external but logged the error, which makes it recoverable.

Detecting "reaches outside" positively is a losing game; `cluster.buckets().get_bucket()` looks like
nothing in particular. The inverse is tractable: a short allowlist of operations that cannot fail for an
external reason, and everything else is treated as possibly external.

| Codebase | before | after |
|---|---|---|
| The agent it was built from | 21 | **2** |
| pydantic-ai | 29 | **0** |
| openai-agents-python | 453 | **0** |
| crewAI | 341 | **1** |

## The one that was real

```python
def _check_bucket_exists(self) -> bool:
    """Check if the bucket exists in the linked Couchbase cluster."""
    bucket_manager = self.cluster.buckets()
    try:
        bucket_manager.get_bucket(self.bucket_name)
        return True
    except Exception:
        return False
```

The caller turns `False` into `"Bucket X does not exist. Please create the bucket before searching."`

So a timeout, an authentication failure or an unreachable node all tell the user to create a bucket that
is already there. Two sibling methods in the same file, `_check_scope_and_collection_exists` and
`_check_index_exists`, both let real errors surface. This was the only one of the three that swallowed.

The same shape is in a second project's Couchbase integration, found the same way.

## The corpus, which matters more than the numbers

Tightening a detector is easy. Tightening it until it no longer finds the bug is easier, and I did it:
one narrowing pass lost two of the three known failures, and I would have shipped it.

So there are two fixture files. `known_bad.py` holds failures that actually happened and must all be
flagged. `known_good.py` holds legitimate idioms lifted from real maintained code and none may be
flagged. Every detector change runs against both.

One fixture moved between them during this work. `has_permission` returning `False` when the ACL check
raises was in the known-bad set, and it does not belong there: denying on error is **fail closed**, which
is the right answer for an authorization check even though the shape matches the bug. The shape is not
the bug. The consequence is.

## What is still not proven

Zero findings in two mature frameworks may mean they are clean, or may mean the detector is now too
narrow to find anything. The corpus proves it still finds the three shapes it knows. It cannot prove
there is not a fourth shape it never looks for.

And one correction, recorded because the process is the point: the first version of the Couchbase fix
caught `BucketNotFoundException`, because that is what the project's test suite mocked by name. The
manager actually raises `BucketDoesNotExistException`, and the two are sibling classes, so neither
catches the other - the fix would have swapped one bug for another. A reviewer caught it. The test
passed either way only because the suite maps every mocked couchbase exception to the same class.

**A test that cannot fail is not evidence.** When a fix turns on the name of a third-party exception,
read the dependency's source, at the oldest version the project supports.
