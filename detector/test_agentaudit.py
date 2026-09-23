#!/usr/bin/env python3
"""Precision and recall regression for agentaudit.

Built 23 Sep 2026 after measuring the tool against three mature agent frameworks and finding its
precision was close to zero: 453 findings in one repo, and every one sampled was a legitimate idiom.
Tightening a detector is easy; tightening it until it no longer finds the bug is easier. This corpus
is what stops that.

    fixtures/known_bad.py   - failures that actually happened. All must be flagged.
    fixtures/known_good.py  - legitimate idioms taken from real maintained code. None may be flagged.

Run it after every change to a detector.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def count(fixture, flag=None):
    cmd = [sys.executable, os.path.join(HERE, "agentaudit.py"),
           os.path.join(HERE, "fixtures", fixture), "--quiet"] + ([flag] if flag else [])
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    lines = [l for l in out.splitlines() if ".py:" in l and "fixtures" not in l]
    return len(lines), out


def main():
    bad_n, bad_out = count("known_bad.py")
    good_n, good_out = count("known_good.py")

    # Recall: every known failure must still be found.
    EXPECTED_BAD = 3
    recall_ok = bad_n >= EXPECTED_BAD
    # Precision: not one legitimate idiom may be reported.
    precision_ok = good_n == 0

    print(f"  recall    {'ok  ' if recall_ok else 'FAIL'}  {bad_n}/{EXPECTED_BAD} known failures flagged")
    print(f"  precision {'ok  ' if precision_ok else 'FAIL'}  {good_n} false positives on known-good code")
    if not recall_ok:
        print("\n  A detector was narrowed past the bug it exists for. Output:\n" + bad_out)
    if not precision_ok:
        print("\n  A legitimate idiom is being reported. Output:\n" + good_out)
    ok = recall_ok and precision_ok
    print(f"\n  {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
