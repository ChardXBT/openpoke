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
    assert "lease_token" in columns
    assert "idx_triggers_next_trigger" in indexes


def test_stale_recurring_worker_cannot_clear_a_newer_lease(tmp_path, monkeypatch):
    import server.services.triggers.service as trigger_service_mod

    service = _service(tmp_path)
    base = datetime.now(timezone.utc).replace(microsecond=0)
    start = base - timedelta(hours=2)
    recurrence = build_recurrence("RRULE:FREQ=DAILY", start, ZoneInfo("UTC"))
    timestamp = to_storage_timestamp(base)
    trigger_id = service._store.insert({
        "agent_name": "Recurring Reminder",
        "payload": "remind again",
        "start_time": to_storage_timestamp(start),
        "next_trigger": to_storage_timestamp(base - timedelta(minutes=1)),
        "recurrence_rule": recurrence,
        "timezone": "UTC",
        "status": "active",
        "last_error": None,
        "created_at": timestamp,
        "updated_at": timestamp,
    })

    monkeypatch.setattr(trigger_service_mod, "utc_now", lambda: base)
    first = service.claim_due_triggers(before=base)[0]
    expired = to_storage_timestamp(base - timedelta(seconds=1))
    service._store.update(trigger_id, first.agent_name, {"lease_until": expired})

    later = base + timedelta(seconds=130)
    monkeypatch.setattr(trigger_service_mod, "utc_now", lambda: later)
    second = service.claim_due_triggers(before=later)[0]
    assert second.lease_until != first.lease_until

    import pytest
    from server.services.triggers.service import TriggerLeaseLostError

    with pytest.raises(TriggerLeaseLostError):
        service.prepare_for_execution(first, fired_at=later)

    service.release_lease(first)
    stored = service._store.fetch_one(trigger_id, first.agent_name)
    assert stored is not None and stored.lease_until == second.lease_until

    service.complete_execution(first)
    stored = service._store.fetch_one(trigger_id, first.agent_name)
    assert stored is not None and stored.lease_until == second.lease_until


def test_claims_get_unique_fencing_tokens_and_renewal_extends_the_lease(tmp_path, monkeypatch):
    import server.services.triggers.service as trigger_service_mod

    service = _service(tmp_path)
    trigger = _due_trigger(service)
    base = datetime.now(timezone.utc).replace(microsecond=0)
    monkeypatch.setattr(trigger_service_mod, "utc_now", lambda: base)

    first = service.claim_due_triggers(before=base)[0]
    assert first.lease_token
    original_until = first.lease_until

    later = base + timedelta(seconds=30)
    monkeypatch.setattr(trigger_service_mod, "utc_now", lambda: later)
    assert service.renew_lease(first, lease_seconds=120)
    renewed = service._store.fetch_one(trigger.id, trigger.agent_name)
    assert renewed is not None
    assert renewed.lease_token == first.lease_token
    assert renewed.lease_until is not None and renewed.lease_until > (original_until or "")

    expired = to_storage_timestamp(later - timedelta(seconds=1))
    service._store.update(trigger.id, trigger.agent_name, {"lease_until": expired})
    second = service.claim_due_triggers(before=later)[0]
    assert second.lease_token != first.lease_token
    assert not service.renew_lease(first, lease_seconds=120)
    assert service.renew_lease(second, lease_seconds=120)


def test_stale_one_shot_worker_cannot_complete_or_fail_over_a_newer_claim(tmp_path):
    service = _service(tmp_path)
    trigger = _due_trigger(service)
    now = datetime.now(timezone.utc)

    first = service.claim_due_triggers(before=now)[0]
    expired = to_storage_timestamp(now - timedelta(seconds=1))
    service._store.update(trigger.id, trigger.agent_name, {"lease_until": expired})
    second = service.claim_due_triggers(before=now)[0]
    assert second.lease_token != first.lease_token

    service.complete_execution(first)
    stored = service._store.fetch_one(trigger.id, trigger.agent_name)
    assert stored is not None
    assert stored.status == "active"
    assert stored.next_trigger is not None
    assert stored.lease_token == second.lease_token

    service.fail_execution(first, "stale failure")
    stored = service._store.fetch_one(trigger.id, trigger.agent_name)
    assert stored is not None
    assert stored.status == "active"
    assert stored.next_trigger is not None
    assert stored.lease_token == second.lease_token


def test_renewal_loss_cancels_slow_execution_before_completion(tmp_path, monkeypatch):
    service = _service(tmp_path)
    trigger = _due_trigger(service)
    started = asyncio.Event()
    finished = asyncio.Event()

    class SlowExecutionBatchManager:
        async def execute_agent(self, agent_name, instructions):
            started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                finished.set()
                raise
            return ExecutionResult(agent_name=agent_name, success=True, response="done")

    monkeypatch.setattr(scheduler_mod, "ExecutionBatchManager", SlowExecutionBatchManager)

    async def run():
        scheduler = TriggerScheduler(lease_seconds=1, heartbeat_interval_seconds=0.01)
        scheduler._service = service
        claimed = service.claim_due_triggers(before=datetime.now(timezone.utc))[0]
        original_renew = service.renew_lease
        calls = 0

        def expire_then_lose(record, lease_seconds):
            nonlocal calls
            calls += 1
            expired = to_storage_timestamp(datetime.now(timezone.utc) - timedelta(seconds=1))
            service._store.update(record.id, record.agent_name, {"lease_until": expired})
            service.claim_due_triggers(before=datetime.now(timezone.utc))
            return original_renew(record, lease_seconds=lease_seconds)

        monkeypatch.setattr(service, "renew_lease", expire_then_lose)
        task = asyncio.create_task(scheduler._execute_trigger(claimed))
        await asyncio.wait_for(started.wait(), timeout=1)
        await asyncio.wait_for(finished.wait(), timeout=1)
        await asyncio.wait_for(task, timeout=1)
        assert calls == 1

    asyncio.run(run())

    stored = service._store.fetch_one(trigger.id, trigger.agent_name)
    assert stored is not None and stored.status == "active"
    assert started.is_set() and finished.is_set()
