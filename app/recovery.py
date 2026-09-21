"""Deployments whose worker was stopped mid-run (scale-in, restart, crash).

A running job beats on its record every 30 seconds. A deployment that claims to be in flight but
has not moved for `FORGEAPI_STALE_AFTER_SECONDS` is marked failed as *interrupted*, so that
`retry` (or `DELETE`) can take over; the interrupted run's Terraform state lock is released on
that next run. This holds with several replicas: each only needs the shared record store."""

import contextlib
import logging
import threading
from datetime import UTC, datetime, timedelta

from app import audit, db
from app.models import State
from app.settings import settings

BEAT_EVERY = 30  # seconds
IN_FLIGHT = (State.accepted, State.planning, State.applying, State.destroying)
INTERRUPTED = (
    "interrupted: the worker stopped while this was running. "
    "Retry to continue from the existing state, or delete to clean up."
)
_log = logging.getLogger("forgeapi.recovery")


@contextlib.contextmanager
def alive(deployment_id: str):
    """Beat on the record for as long as the block runs."""
    stop = threading.Event()

    def beat() -> None:
        while not stop.wait(BEAT_EVERY):
            with contextlib.suppress(Exception):
                db.touch(deployment_id)

    thread = threading.Thread(target=beat, daemon=True, name=f"beat-{deployment_id}")
    thread.start()
    try:
        yield
    finally:
        stop.set()


def sweep(now: datetime | None = None) -> list[str]:
    """Mark stale in-flight deployments as interrupted. Returns their IDs."""
    now = now or datetime.now(UTC)
    found = []
    for d in db.list_for(None):
        stale_after = timedelta(seconds=settings.stale_after_seconds)
        if d.state in IN_FLIGHT and now - d.updated_at > stale_after:
            db.update(d.id, State.failed, error=INTERRUPTED)
            audit.record_quietly(
                "deployment.state", "interrupted", "recovery", deployment_id=d.id,
                pattern=d.pattern, version=d.version, business_unit=d.business_unit,
                environment=d.environment, detail={"was": str(d.state)},
            )  # fmt: skip
            found.append(d.id)
    return found


def sweep_forever(every: int = 60) -> None:
    """Background loop for a long-lived process (worker or all-in-one)."""

    def loop() -> None:
        while True:
            try:
                for deployment_id in sweep():
                    _log.warning("marked %s as interrupted", deployment_id)
            except Exception as err:  # noqa: BLE001
                _log.warning("recovery sweep failed: %s", type(err).__name__)
            threading.Event().wait(every)

    threading.Thread(target=loop, daemon=True, name="recovery-sweep").start()
