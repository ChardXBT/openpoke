"""Trigger leasing: due reminders are claimed atomically before they run."""

import asyncio
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from server.agents.execution_agent.runtime import ExecutionResult
from server.services.trigger_scheduler import TriggerScheduler
import server.services.trigger_scheduler as scheduler_mod
from server.services.triggers.service import TriggerService
from server.services.triggers.store import TriggerStore
from server.services.triggers.utils import build_recurrence, to_storage_timestamp


def _service(tmp_path):
    return TriggerService(TriggerStore(tmp_path / "triggers.db"))


def _due_trigger(service: TriggerService, agent_name: str = "Reminder"):
    return service.create_trigger(
        agent_name=agent_name,
        payload="remind the user",
        start_time=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
    )


def test_concurrent_claims_hand_trigger_to_one_worker(tmp_path):
    service = _service(tmp_path)
    _due_trigger(service)
    now = datetime.now(timezone.utc)
    claims = []

    def claim():
        # Separate store objects model separate scheduler workers sharing one database.
        claims.extend(_service(tmp_path).claim_due_triggers(before=now + timedelta(seconds=1)))

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(claims) == 1
    assert claims[0].lease_until is not None


def test_active_lease_blocks_reclaim_until_it_expires(tmp_path):
    service = _service(tmp_path)
    trigger = _due_trigger(service)
    now = datetime.now(timezone.utc)

    first = service.claim_due_triggers(before=now + timedelta(seconds=1))
    assert [t.id for t in first] == [trigger.id]
    assert service.claim_due_triggers(before=now + timedelta(seconds=2)) == []

    expired = to_storage_timestamp(datetime.now(timezone.utc) - timedelta(seconds=1))
    service._store.update(trigger.id, trigger.agent_name, {"lease_until": expired})
    second = service.claim_due_triggers(before=now + timedelta(seconds=2))
    assert [t.id for t in second] == [trigger.id]


def test_two_schedulers_racing_fire_one_trigger_once(tmp_path, monkeypatch):
    service = _service(tmp_path)
    trigger = _due_trigger(service)
    calls: list[str] = []

    class FakeExecutionBatchManager:
        async def execute_agent(self, agent_name, instructions):
            calls.append(agent_name)
            await asyncio.sleep(0.02)
            return ExecutionResult(agent_name=agent_name, success=True, response="done")

    monkeypatch.setattr(scheduler_mod, "ExecutionBatchManager", FakeExecutionBatchManager)

    async def run():
        first = TriggerScheduler()
        second = TriggerScheduler()
        first._service = _service(tmp_path)
        second._service = _service(tmp_path)
        await asyncio.gather(first._poll_once(), second._poll_once())
        await asyncio.sleep(0.08)

    asyncio.run(run())

    assert calls == [trigger.agent_name]
    stored = service._store.fetch_one(trigger.id, trigger.agent_name)
    assert stored is not None and stored.status == "completed"


def test_recurring_next_fire_is_written_before_execution(tmp_path):
    service = _service(tmp_path)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    start = now - timedelta(hours=2)
    recurrence = build_recurrence("RRULE:FREQ=DAILY", start, ZoneInfo("UTC"))
    timestamp = to_storage_timestamp(now)
    trigger_id = service._store.insert({
        "agent_name": "Recurring Reminder",
        "payload": "remind again",
        "start_time": to_storage_timestamp(start),
        "next_trigger": to_storage_timestamp(now - timedelta(minutes=1)),
        "recurrence_rule": recurrence,
        "timezone": "UTC",
        "status": "active",
        "last_error": None,
        "created_at": timestamp,
        "updated_at": timestamp,
    })
    trigger = service._store.fetch_one(trigger_id, "Recurring Reminder")
    assert trigger is not None

    claimed = service.claim_due_triggers(before=now)
    assert len(claimed) == 1
    prepared = service.prepare_for_execution(claimed[0], fired_at=now)

    assert prepared.next_trigger != trigger.next_trigger
    assert prepared.next_trigger is not None and prepared.next_trigger > to_storage_timestamp(now)
    assert prepared.lease_until is not None  # still claimed while the run is in flight
    service.complete_execution(prepared)
    finished = service._store.fetch_one(trigger_id, "Recurring Reminder")
    assert finished is not None and finished.lease_until is None


def test_existing_trigger_database_gains_lease_column_and_due_index(tmp_path):
    db = tmp_path / "triggers.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE triggers (id INTEGER PRIMARY KEY AUTOINCREMENT, agent_name TEXT NOT NULL, "
        "payload TEXT NOT NULL, start_time TEXT, next_trigger TEXT, recurrence_rule TEXT, timezone TEXT, "
        "status TEXT NOT NULL DEFAULT 'active', last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.close()

    TriggerStore(db)

    conn = sqlite3.connect(db)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(triggers)")}
    indexes = {row[1] for row in conn.execute("PRAGMA index_list(triggers)")}
    conn.close()
    assert "lease_until" in columns
    assert "idx_triggers_next_trigger" in indexes
