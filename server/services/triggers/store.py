from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...logging_config import logger
from .models import TriggerRecord
from .utils import to_storage_timestamp, utc_now


class TriggerStore:
    """Low-level persistence for triggers backed by SQLite."""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._ensure_directory()
        self._ensure_schema()

    def _ensure_directory(self) -> None:
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "trigger directory creation failed",
                extra={"error": str(exc)},
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        schema_sql = """
        CREATE TABLE IF NOT EXISTS triggers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT NOT NULL,
            payload TEXT NOT NULL,
            start_time TEXT,
            next_trigger TEXT,
            recurrence_rule TEXT,
            timezone TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            last_error TEXT,
            lease_until TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
        index_sql = """
        CREATE INDEX IF NOT EXISTS idx_triggers_agent_next
        ON triggers (agent_name, next_trigger);
        """
        due_index_sql = """
        CREATE INDEX IF NOT EXISTS idx_triggers_next_trigger
        ON triggers (next_trigger);
        """
        with self._lock, self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(schema_sql)
            conn.execute(index_sql)
            conn.execute(due_index_sql)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(triggers)")}
            if "lease_until" not in columns:
                conn.execute("ALTER TABLE triggers ADD COLUMN lease_until TEXT")

    def insert(self, payload: Dict[str, Any]) -> int:
        with self._lock, self._connect() as conn:
            columns = ", ".join(payload.keys())
            placeholders = ", ".join([":" + key for key in payload.keys()])
            sql = f"INSERT INTO triggers ({columns}) VALUES ({placeholders})"
            conn.execute(sql, payload)
            trigger_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            return int(trigger_id)

    def fetch_one(self, trigger_id: int, agent_name: str) -> Optional[TriggerRecord]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM triggers WHERE id = ? AND agent_name = ?",
                (trigger_id, agent_name),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def update(self, trigger_id: int, agent_name: str, fields: Dict[str, Any]) -> bool:
        if not fields:
            return False
        assignments = ", ".join(f"{key} = :{key}" for key in fields.keys())
        sql = (
            f"UPDATE triggers SET {assignments}, updated_at = :updated_at"
            " WHERE id = :trigger_id AND agent_name = :agent_name"
        )
        payload = {
            **fields,
            "updated_at": to_storage_timestamp(utc_now()),
            "trigger_id": trigger_id,
            "agent_name": agent_name,
        }
        with self._lock, self._connect() as conn:
            cursor = conn.execute(sql, payload)
            return cursor.rowcount > 0

    def update_if_lease(
        self,
        trigger_id: int,
        agent_name: str,
        fields: Dict[str, Any],
        lease_until: Optional[str],
    ) -> bool:
        """Update a claimed row only while the caller's lease is still the current one."""
        if not fields or lease_until is None:
            return False
        assignments = ", ".join(f"{key} = :{key}" for key in fields.keys())
        sql = (
            f"UPDATE triggers SET {assignments}, updated_at = :updated_at"
            " WHERE id = :trigger_id AND agent_name = :agent_name"
            " AND lease_until = :expected_lease_until"
        )
        payload = {
            **fields,
            "updated_at": to_storage_timestamp(utc_now()),
            "trigger_id": trigger_id,
            "agent_name": agent_name,
            "expected_lease_until": lease_until,
        }
        with self._lock, self._connect() as conn:
            cursor = conn.execute(sql, payload)
            return cursor.rowcount > 0

    def list_for_agent(self, agent_name: str) -> List[TriggerRecord]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM triggers WHERE agent_name = ? ORDER BY next_trigger IS NULL, next_trigger",
                (agent_name,),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def fetch_due(
        self, agent_name: Optional[str], before_iso: str
    ) -> List[TriggerRecord]:
        sql = (
            "SELECT * FROM triggers WHERE status = 'active' AND next_trigger IS NOT NULL"
            " AND next_trigger <= ? AND (lease_until IS NULL OR lease_until < ?)"
        )
        params: List[Any] = [before_iso, before_iso]
        if agent_name:
            sql += " AND agent_name = ?"
            params.append(agent_name)
        sql += " ORDER BY next_trigger, id"
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_record(row) for row in rows]

    def claim_due(
        self,
        agent_name: Optional[str],
        before_iso: str,
        lease_until_iso: str,
    ) -> List[TriggerRecord]:
        """Atomically lease due triggers; only the worker whose UPDATE wins may run one."""
        select_sql = (
            "SELECT id FROM triggers WHERE status = 'active' AND next_trigger IS NOT NULL"
            " AND next_trigger <= ? AND (lease_until IS NULL OR lease_until < ?)"
        )
        params: List[Any] = [before_iso, before_iso]
        if agent_name:
            select_sql += " AND agent_name = ?"
            params.append(agent_name)
        select_sql += " ORDER BY next_trigger, id"

        claimed: List[TriggerRecord] = []
        updated_at = to_storage_timestamp(utc_now())
        with self._lock, self._connect() as conn:
            ids = [int(row["id"]) for row in conn.execute(select_sql, params).fetchall()]
            for trigger_id in ids:
                update_sql = (
                    "UPDATE triggers SET lease_until = ?, updated_at = ?"
                    " WHERE id = ? AND status = 'active' AND next_trigger IS NOT NULL"
                    " AND next_trigger <= ? AND (lease_until IS NULL OR lease_until < ?)"
                )
                update_params: List[Any] = [lease_until_iso, updated_at, trigger_id, before_iso, before_iso]
                if agent_name:
                    update_sql += " AND agent_name = ?"
                    update_params.append(agent_name)
                cursor = conn.execute(update_sql, update_params)
                if cursor.rowcount != 1:
                    continue
                row = conn.execute("SELECT * FROM triggers WHERE id = ?", (trigger_id,)).fetchone()
                if row:
                    claimed.append(self._row_to_record(row))
        return claimed

    def clear_all(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM triggers")

    def _row_to_record(self, row: sqlite3.Row) -> TriggerRecord:
        data = dict(row)
        return TriggerRecord.model_validate(data)


__all__ = ["TriggerStore"]
