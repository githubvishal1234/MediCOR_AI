"""
Whether this deployment is running more than one process of the app --
and making that fact loud, because everything about it is currently quiet.

WHY THIS EXISTS

Two pieces of this application hold state in the process rather than in
the database, and both fail *silently* when a second process appears:

  * `rate_limit._attempts` is a module-level dict. Four workers means
    four independent counters, so a login limit of 5 becomes 20 and the
    brute-force protection is quietly a quarter as strong as configured.

  * `realtime.ConnectionManager` holds WebSockets in memory. A dashboard
    connected to worker A never sees an event broadcast by worker B, so
    the live call list is missing calls -- and looks merely idle.

Neither raises. Nothing in a log says it. The dashboard does not look
broken, it looks quiet, which is the worst possible presentation of a
bug: the operator's own eyes confirm the wrong answer. That is what this
module is for. It does not fix the limitation -- Redis is the
prerequisite for that, and it is a real piece of work -- it makes the
limitation announce itself instead of being discovered months later by
someone wondering why a rate limit did not hold.

TWO DETECTORS, BECAUSE NEITHER IS SUFFICIENT ALONE

`configured_worker_count()` reads the process's own command line and
environment. It catches `uvicorn --workers 4` and `WEB_CONCURRENCY=4`,
which is overwhelmingly how this happens, and it catches them *at
startup*, before a single request has been served under a broken rate
limit. What it cannot see is two processes started separately -- two
terminals, two containers, a stale server someone forgot to stop --
each of which honestly reports one worker.

`report_peer()` covers exactly that. The scheduler already writes a row
per job per time bucket and lets a UNIQUE constraint decide the winner;
since 0031 that row also carries the winner's `instance_id`. A process
that loses the race can therefore read who beat it, and *a different
instance id is proof of a second process* -- not an inference, an
observation. It costs one SELECT on a path that was already doing
nothing but rolling back.

WHY THE STARTUP CHECK IS FATAL IN PRODUCTION AND NOT IN DEVELOPMENT

Same reasoning as the SECRET_KEY check above it in `main.py`. In
production the consequence is a weakened authentication control on a
system holding patient data, and there is no reading under which
continuing is better than stopping. In development someone may well be
experimenting, and killing their server over it teaches them to stop
reading warnings.

THE OVERRIDE IS DELIBERATELY NAMED FOR WHAT IT DOES

`ALLOW_MULTIPLE_WORKERS=true` downgrades the error to a warning. It does
NOT make multiple workers safe, and nothing here pretends otherwise --
it exists because the detection below parses a command line, and a
parser that can be wrong must not be the only thing standing between an
operator and a running system.
"""

import logging
import os
import re
import sys
import uuid
from datetime import datetime
from typing import Optional

logger = logging.getLogger("instance")

#: Identifies this process for the lifetime of this process. Deliberately
#: not the pid: pids are reused, and two containers routinely hold the
#: same one, which is precisely the case this needs to tell apart.
INSTANCE_ID = uuid.uuid4().hex[:16]

#: When this process started, in the same clock `ScheduledJobRun.started_at`
#: uses (naive UTC). This is what separates a genuine second process from
#: this process's own previous incarnation -- see `report_peer`.
PROCESS_STARTED_AT = datetime.utcnow()

#: Peer instance ids observed via the scheduler. Module-level, and that
#: is fine here for the same reason it is *not* fine for a rate limit:
#: this set only ever grows, is never authoritative, and a per-process
#: copy still produces the warning in every process that has a peer.
_PEERS: set[str] = set()

# Anchored: this matches one argv token exactly, so `--workersfoo` is
# not a worker flag.
_WORKER_ARG = re.compile(r"^(?:--workers|-w)(?:=(\d+))?$")
# Unanchored, for scanning a flag string that lives inside one env var.
_WORKER_IN_STRING = re.compile(r"(?:--workers|-w)[= ](\d+)")


def configured_worker_count() -> int:
    """
    How many workers this process was asked to run, as best it can tell.

    Returns 1 when nothing says otherwise, which is both the default and
    the only supported value.

    KNOWN BLIND SPOT, stated rather than discovered: a gunicorn config
    *file* (`-c gunicorn.conf.py` setting `workers = 4`) is not read
    here. Importing an arbitrary config file to inspect it is worse than
    the gap, and `report_peer` below catches that deployment anyway --
    one tick later rather than at startup.
    """
    counts = [1]

    argv = sys.argv[1:]
    for i, arg in enumerate(argv):
        m = _WORKER_ARG.match(arg)
        if not m:
            continue
        # Either `--workers=4` (captured) or `--workers 4` (next token).
        raw = m.group(1)
        if raw is None:
            raw = argv[i + 1] if i + 1 < len(argv) else ""
        if raw.isdigit():
            counts.append(int(raw))

    for var in ("WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS"):
        raw = (os.environ.get(var) or "").strip()
        if raw.isdigit():
            counts.append(int(raw))

    # gunicorn reads its own flags out of this one, as a single string.
    for m in _WORKER_IN_STRING.finditer(os.environ.get("GUNICORN_CMD_ARGS", "")):
        counts.append(int(m.group(1)))

    return max(counts)


_EXPLANATION = (
    "This application holds two pieces of state in the process and not in "
    "the database: rate-limit counters (app/rate_limit.py) and dashboard "
    "WebSocket connections (app/realtime.py). With {n} processes each gets "
    "its own copy, so login rate limits are effectively {n}x more "
    "permissive than configured and dashboards miss live events broadcast "
    "by the others. Neither failure raises an error or looks broken on "
    "screen. Run a single worker. Horizontal scaling needs Redis for both, "
    "which is not built."
)


def check_worker_configuration(is_production: bool, allow_override: bool) -> int:
    """
    Startup gate. Returns the detected worker count.

    Raises `RuntimeError` in production, so a deployment that would
    silently weaken an authentication control refuses to start instead.
    """
    n = configured_worker_count()
    if n <= 1:
        return n

    message = _EXPLANATION.format(n=n)

    if is_production and not allow_override:
        raise RuntimeError(
            f"Refusing to start: {n} workers were requested. {message} "
            "If this detection is wrong, set ALLOW_MULTIPLE_WORKERS=true "
            "-- but note that flag suppresses the check, it does not make "
            "multiple workers safe."
        )

    logger.error("Running %d workers is not supported. %s", n, message)
    return n


def report_peer(
    peer_id: str, job_name: str, claimed_at: Optional[datetime] = None
) -> bool:
    """
    Called by the scheduler when another instance won a job claim.

    Returns True the first time a given peer is seen, so the caller can
    log at ERROR without producing a line every tick forever.

    TWO THINGS HAVE TO BE TRUE, AND THE SECOND WAS LEARNED THE HARD WAY

    Losing a claim is not on its own evidence of anything, so the first
    filter is that the winning id is not ours.

    That is not sufficient. `INSTANCE_ID` is per *process*, so a restart
    produces a new one -- and a restart inside a live time bucket loses
    the claim to its own predecessor, which then looks exactly like a
    second process. A `uvicorn --reload` server reported two "peers"
    against an empty machine within minutes of this shipping. An alarm
    that cries wolf is worse than no alarm, because the real one is then
    ignored.

    So the second filter is *when*: a claim made before this process
    started belongs to a previous incarnation, and a genuinely concurrent
    process is one that goes on claiming buckets **after** we booted.
    That distinguishes the two cleanly without a heartbeat, a lease or a
    second table -- and two processes started at the same moment still
    see each other, because each keeps claiming while the other runs.

    An unknown `claimed_at` (NULL on rows written before 0031) is treated
    as too old to judge, which errs towards silence. A guard that reports
    an ambiguity as a fault is the failure this whole function is
    guarding against.
    """
    if not peer_id or peer_id == INSTANCE_ID:
        return False
    if claimed_at is None or claimed_at < PROCESS_STARTED_AT:
        return False
    if peer_id in _PEERS:
        return False
    _PEERS.add(peer_id)
    logger.error(
        "Another instance of this application (%s) is running against the "
        "same database; this one is %s. Detected because it won the '%s' "
        "scheduler claim. %s",
        peer_id, INSTANCE_ID, job_name, _EXPLANATION.format(n=len(_PEERS) + 1),
    )
    return True


def peer_count() -> int:
    """Other instances this process has observed. 0 is the healthy answer."""
    return len(_PEERS)


def status() -> dict:
    """
    What `/health` reports about this constraint.

    It lives here rather than inline in `main.py` because the test suite
    never imports `app.main` -- that module bootstraps the real schema at
    import time, which is why every router test builds its own bare
    `FastAPI()` instead. A payload only reachable through `main` would be
    a payload with no test.

    `configured` is recomputed rather than cached from startup. It reads
    argv and the environment, neither of which changes inside a process,
    so the answer is identical -- and not holding a module-level copy is
    one less thing to go stale.
    """
    return {
        "supported": 1,
        "configured": configured_worker_count(),
        "peers": peer_count(),
    }
