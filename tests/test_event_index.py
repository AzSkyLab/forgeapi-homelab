"""Operation event pages stay narrow as unrelated audit history grows."""

import sqlite3
from contextlib import contextmanager

import pytest

from app import ledger
from app.settings import settings


def test_sparse_operation_events_use_bounded_sqlite_work(monkeypatch):
    target = "op_target"
    with ledger.connect() as con:
        first = con.execute(
            "INSERT INTO events (operation_id, actor, action, outcome, timestamp) "
            "VALUES (?, 'local', 'test', 'accepted', '2026-10-01T00:00:00Z')",
            (target,),
        ).lastrowid
        con.executemany(
            "INSERT INTO events (operation_id, actor, action, outcome, timestamp) "
            "VALUES (?, 'local', 'other', 'accepted', '2026-10-01T00:00:00Z')",
            ((f"op_other_{number}",) for number in range(4000)),
        )
        last = con.execute(
            "INSERT INTO events (operation_id, actor, action, outcome, timestamp) "
            "VALUES (?, 'local', 'test', 'succeeded', '2026-10-01T00:00:00Z')",
            (target,),
        ).lastrowid
        con.executemany(
            "INSERT INTO events (operation_id, actor, action, outcome, timestamp) "
            "VALUES (?, 'local', 'other', 'accepted', '2026-10-01T00:00:00Z')",
            ((f"op_other_{number}",) for number in range(4000, 8000)),
        )

    original_connect = ledger.connect

    @contextmanager
    def limited_connect(*, write=True):
        with original_connect(write=write) as con:
            calls = 0

            def progress():
                nonlocal calls
                calls += 100
                return int(calls > 2000)

            con.set_progress_handler(progress, 100)
            try:
                yield con
            finally:
                con.set_progress_handler(None, 0)

    monkeypatch.setattr(ledger, "connect", limited_connect)
    assert [event["seq"] for event in ledger.events(target, 0, 1)] == [first]
    assert [event["seq"] for event in ledger.events(target, first, 1)] == [last]
    assert ledger.events(target, last, 1) == []


def test_existing_event_rows_get_index_without_losing_append_only_rules():
    path = settings.data_dir / "operations.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as con:
        con.execute(
            "CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT, "
            "actor TEXT NOT NULL, action TEXT NOT NULL, outcome TEXT NOT NULL, "
            "timestamp TEXT NOT NULL)"
        )
        con.execute(
            "INSERT INTO events VALUES (7, 'op_existing', 'local', 'test', 'accepted', 'old')"
        )
        con.executescript(
            "CREATE TRIGGER events_no_update BEFORE UPDATE ON events "
            "BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;"
            "CREATE TRIGGER events_no_delete BEFORE DELETE ON events "
            "BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;"
        )
        original = tuple(con.execute("SELECT * FROM events WHERE seq=7").fetchone())
        assert con.execute("PRAGMA index_list(events)").fetchall() == []

    with ledger.connect() as con:
        indexes = [row[1] for row in con.execute("PRAGMA index_list(events)")]
        assert "events_by_operation_seq" in indexes
        assert con.execute("SELECT count(*) FROM events").fetchone()[0] == 1
        assert tuple(con.execute("SELECT * FROM events WHERE seq=7").fetchone()) == original
        appended = con.execute(
            "INSERT INTO events (operation_id, actor, action, outcome, timestamp) "
            "VALUES ('op_existing', 'local', 'test', 'accepted', 'new')"
        ).lastrowid
        assert appended > 7
    assert [event["seq"] for event in ledger.events("op_existing", 0, 10)] == [7, appended]
    with pytest.raises(sqlite3.IntegrityError), ledger.connect() as con:
        con.execute("UPDATE events SET outcome='changed' WHERE seq=7")
    with pytest.raises(sqlite3.IntegrityError), ledger.connect() as con:
        con.execute("DELETE FROM events WHERE seq=7")
    assert [event["outcome"] for event in ledger.events("op_existing", 0, 10)] == [
        "accepted",
        "accepted",
    ]
