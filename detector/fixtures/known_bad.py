"""Known-BAD fixtures. Every function here reproduces a failure that actually happened.
agentaudit must flag every one of these. If it stops, precision work has gone too far."""
import json, os, subprocess, time

STAMP = "/tmp/last_run"


def is_authenticated(binary):
    # Case #3, the real one: a failed check answers the question negatively.
    try:
        out = subprocess.run([binary, "auth", "status"], capture_output=True, text=True).stdout
        return bool(json.loads(out).get("loggedIn"))
    except Exception:
        return False


def run_daily_job():
    # Case #1, the real one: the completion marker is written whatever happened.
    try:
        produce_the_report()
    except Exception:
        pass
    finally:
        json.dump({"last_success": time.time()}, open(STAMP, "w"))


def check_acl(u, a): return True
def produce_the_report(): raise RuntimeError("boom")


def _check_bucket_exists(cluster, name):
    # A predicate over an EXTERNAL system. A timeout, an auth failure and a genuinely absent
    # bucket all become "it does not exist". Real, found in crewAI 23 Sep 2026.
    try:
        cluster.buckets().get_bucket(name)
        return True
    except Exception:
        return False


class Store:
    def exists(self):
        # Reaches the cluster. A timeout answers "no".
        try:
            return self.client.get_collection(self.name) is not None
        except Exception:
            return False

    def recreate(self):
        # Real, found in agno 23 Sep 2026: a failed read leads to a drop.
        if not self.exists():
            self.client.drop_collection(self.name)
            self.client.create_collection(self.name)
