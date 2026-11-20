"""SQLite-backed registry of execution agents.

Replaces the bare-name ``roster.json`` as the source of truth. Every agent gets a
stable integer id (shown to the model as ``#12``), an open-loop state, a one-line
note, and references (contacts, threads, drafts, triggers, subjects, aliases)
captured when its tools run. An FTS5 index over name, topic, note and refs backs
``search_agents`` and the reference resolver.

Agent *names* remain the key for execution logs and triggers, so existing data
keeps working; ``roster.json`` is migrated once on first open.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

from ...logging_config import logger

OPEN_EXCLUDED_STATES = ("done",)
CASEFOLD_REF_KINDS = {"contact", "contact_name", "subject", "alias"}

_NON_ALNUM = re.compile(r"[^0-9a-z]+")
_HASH_ID = re.compile(r"^#(\d+)$")
_HASH_PREFIX = re.compile(r"^#(\d+)\b")
_MIN_ID, _MAX_ID = 0, 2**63 - 1  # SQLite INTEGER range (ids are positive)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def normalize_name(name: str) -> str:
    """Casefold and collapse punctuation/whitespace so near-identical names collide."""
    return _NON_ALNUM.sub(" ", (name or "").casefold()).strip()


def normalize_ref(kind: str, value: str) -> str:
    cleaned = (value or "").strip()
    return cleaned.casefold() if kind in CASEFOLD_REF_KINDS else cleaned


@dataclass
class AgentRecord:
    id: int
    name: str
    name_key: str
    created_at: str
    last_active_at: str
    state: str
    state_note: str
    topic: str
    request_count: int
    archived: bool
    has_active_trigger: bool


@dataclass(frozen=True)
class Ref:
    kind: str
    value: str


_SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    last_active_at TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'done',
    state_note TEXT NOT NULL DEFAULT '',
    topic TEXT NOT NULL DEFAULT '',
    request_count INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    has_active_trigger INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_agents_activity ON agents (archived, last_active_at);
CREATE INDEX IF NOT EXISTS idx_agents_name ON agents (name);
CREATE TABLE IF NOT EXISTS agent_refs (
    agent_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (agent_id, kind, value)
);
CREATE INDEX IF NOT EXISTS idx_refs_lookup ON agent_refs (kind, value);
CREATE VIRTUAL TABLE IF NOT EXISTS agents_fts USING fts5(
    name, topic, state_note, refs_text, tokenize = 'unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

_COLUMNS = (
    "id, name, name_key, created_at, last_active_at, state, state_note, topic, "
    "request_count, archived, has_active_trigger"
)


def _row_to_record(row: sqlite3.Row) -> AgentRecord:
    return AgentRecord(
        id=int(row["id"]),
        name=row["name"],
        name_key=row["name_key"],
        created_at=row["created_at"],
        last_active_at=row["last_active_at"],
        state=row["state"],
        state_note=row["state_note"],
        topic=row["topic"],
        request_count=int(row["request_count"]),
        archived=bool(row["archived"]),
        has_active_trigger=bool(row["has_active_trigger"]),
    )


class AgentRegistry:
    """Thread-safe registry. One short-lived connection per call, serialized by a lock."""

    def __init__(
        self,
        db_path: Path,
        legacy_roster_path: Optional[Path] = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._db_path = Path(db_path)
        self._clock = clock
        self._lock = threading.RLock()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # One shared connection, serialized by the lock: opening a connection per
        # call costs ~20 ms on Windows, which adds up on every tool call.
        self._conn = sqlite3.connect(
            self._db_path, timeout=30, isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
        if legacy_roster_path is not None:
            self._migrate_legacy(Path(legacy_roster_path))

    # ------------------------------------------------------------------ plumbing
    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Yield the shared connection while holding the registry lock. Do not close it."""
        with self._lock:
            yield self._conn

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def clone_in_memory(self, clock: Optional[Callable[[], datetime]] = None) -> "AgentRegistry":
        """An independent in-memory copy (evals: one per scenario so tool calls never leak)."""
        copy = AgentRegistry(Path(":memory:"), clock=clock or self._clock)
        with self._lock, copy._lock:
            self._conn.backup(copy._conn)
        return copy

    @property
    def clock(self) -> Callable[[], datetime]:
        return self._clock

    def _now(self) -> str:
        return to_iso(self._clock())

    def _reindex(self, conn: sqlite3.Connection, agent_id: int) -> None:
        row = conn.execute(
            "SELECT name, topic, state_note FROM agents WHERE id = ?", (agent_id,)
        ).fetchone()
        conn.execute("DELETE FROM agents_fts WHERE rowid = ?", (agent_id,))
        if row is None:
            return
        refs = conn.execute(
            "SELECT value FROM agent_refs WHERE agent_id = ? ORDER BY created_at, value", (agent_id,)
        ).fetchall()
        refs_text = " ".join(r["value"] for r in refs)
        conn.execute(
            "INSERT INTO agents_fts (rowid, name, topic, state_note, refs_text) VALUES (?, ?, ?, ?, ?)",
            (agent_id, row["name"], row["topic"], row["state_note"], refs_text),
        )

    def _migrate_legacy(self, legacy: Path) -> None:
        with self.connection() as conn:
            done = conn.execute("SELECT value FROM meta WHERE key = 'legacy_migrated'").fetchone()
        if done or not legacy.exists():
            return
        try:
            data = json.loads(legacy.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(f"registry: legacy roster unreadable, leaving it untouched: {exc}")
            return
        if isinstance(data, list):
            for name in data:
                if isinstance(name, str) and normalize_name(name):
                    self.get_or_create(name)
        with self.connection() as conn:
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('legacy_migrated', '1')")

    # ------------------------------------------------------------------ records
    def get_or_create(self, name: str, topic: str = "") -> tuple[AgentRecord, bool]:
        key = normalize_name(name)
        if not key:
            raise ValueError("agent name must contain at least one letter or digit")
        now = self._now()
        with self.connection() as conn:
            row = conn.execute(f"SELECT {_COLUMNS} FROM agents WHERE name_key = ?", (key,)).fetchone()
            if row is not None:
                if topic and not row["topic"]:
                    conn.execute("UPDATE agents SET topic = ? WHERE id = ?", (topic.strip(), row["id"]))
                    self._reindex(conn, row["id"])
                    row = conn.execute(f"SELECT {_COLUMNS} FROM agents WHERE id = ?", (row["id"],)).fetchone()
                return _row_to_record(row), False
            cursor = conn.execute(
                "INSERT INTO agents (name, name_key, created_at, last_active_at, topic) VALUES (?, ?, ?, ?, ?)",
                (name.strip(), key, now, now, (topic or "").strip()),
            )
            agent_id = int(cursor.lastrowid)
            self._reindex(conn, agent_id)
            row = conn.execute(f"SELECT {_COLUMNS} FROM agents WHERE id = ?", (agent_id,)).fetchone()
            return _row_to_record(row), True

    def get(self, agent_id: int) -> Optional[AgentRecord]:
        if not _MIN_ID <= int(agent_id) <= _MAX_ID:  # "#999…9" would overflow SQLite INTEGER
            return None
        with self.connection() as conn:
            row = conn.execute(f"SELECT {_COLUMNS} FROM agents WHERE id = ?", (agent_id,)).fetchone()
        return _row_to_record(row) if row else None

    def get_many(self, agent_ids: Iterable[int]) -> list[AgentRecord]:
        ids = [i for i in dict.fromkeys(int(i) for i in agent_ids) if _MIN_ID <= i <= _MAX_ID]
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self.connection() as conn:
            rows = conn.execute(f"SELECT {_COLUMNS} FROM agents WHERE id IN ({placeholders})", ids).fetchall()
        by_id = {int(r["id"]): _row_to_record(r) for r in rows}
        return [by_id[i] for i in ids if i in by_id]

    def find(self, ref: str) -> Optional[AgentRecord]:
        """Look an agent up by ``#id``, exact name, or normalized name."""
        text = (ref or "").strip()
        if not text:
            return None
        match = _HASH_ID.match(text)
        if match:
            return self.get(int(match.group(1)))
        # Models often write "#15 Air Canada Refund": the id wins over the trailing name.
        prefixed = _HASH_PREFIX.match(text)
        if prefixed:
            rec = self.get(int(prefixed.group(1)))
            if rec is not None:
                return rec
            text = text[prefixed.end():].strip(" -–—:·|") or text  # unknown id: try the name
        with self.connection() as conn:
            row = conn.execute(f"SELECT {_COLUMNS} FROM agents WHERE name = ?", (text,)).fetchone()
            if row is None:
                key = normalize_name(text)
                if key:
                    row = conn.execute(f"SELECT {_COLUMNS} FROM agents WHERE name_key = ?", (key,)).fetchone()
        return _row_to_record(row) if row else None

    def touch(
        self,
        agent_id: int,
        *,
        state: Optional[str] = None,
        note: Optional[str] = None,
        count_request: bool = False,
    ) -> None:
        sets = ["last_active_at = ?", "archived = 0"]
        params: list[object] = [self._now()]
        if state is not None:
            sets.append("state = ?")
            params.append(state)
        if note is not None:
            sets.append("state_note = ?")
            params.append(note.strip())
        if count_request:
            sets.append("request_count = request_count + 1")
        params.append(agent_id)
        with self.connection() as conn:
            conn.execute(f"UPDATE agents SET {', '.join(sets)} WHERE id = ?", params)
            if note is not None:
                self._reindex(conn, agent_id)

    def set_state(self, agent_id: int, state: str, note: Optional[str] = None) -> None:
        """Change state without counting it as user-facing activity."""
        with self.connection() as conn:
            if note is None:
                conn.execute("UPDATE agents SET state = ? WHERE id = ?", (state, agent_id))
            else:
                conn.execute(
                    "UPDATE agents SET state = ?, state_note = ? WHERE id = ?", (state, note.strip(), agent_id)
                )
                self._reindex(conn, agent_id)

    def set_archived(self, agent_id: int, archived: bool) -> None:
        with self.connection() as conn:
            conn.execute("UPDATE agents SET archived = ? WHERE id = ?", (1 if archived else 0, agent_id))

    def set_trigger_flag(self, agent_id: int, active: bool) -> None:
        with self.connection() as conn:
            conn.execute("UPDATE agents SET has_active_trigger = ? WHERE id = ?", (1 if active else 0, agent_id))

    # ------------------------------------------------------------------ refs
    def add_ref(self, agent_id: int, kind: str, value: str) -> None:
        cleaned = normalize_ref(kind, value)
        if not cleaned:
            return
        with self.connection() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO agent_refs (agent_id, kind, value, created_at) VALUES (?, ?, ?, ?)",
                (agent_id, kind, cleaned, self._now()),
            )
            if cursor.rowcount:
                self._reindex(conn, agent_id)

    def refs(self, agent_id: int) -> list[Ref]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT kind, value FROM agent_refs WHERE agent_id = ? ORDER BY created_at, kind, value",
                (agent_id,),
            ).fetchall()
        return [Ref(r["kind"], r["value"]) for r in rows]

    def owners(self, kind: str, value: str, include_archived: bool = True) -> list[AgentRecord]:
        cleaned = normalize_ref(kind, value)
        if not cleaned:
            return []
        archived_clause = "" if include_archived else "AND a.archived = 0"
        with self.connection() as conn:
            rows = conn.execute(
                f"SELECT {', '.join('a.' + c.strip() for c in _COLUMNS.split(','))} "
                "FROM agent_refs r JOIN agents a ON a.id = r.agent_id "
                f"WHERE r.kind = ? AND r.value = ? {archived_clause} "
                "ORDER BY a.last_active_at DESC",
                (kind, cleaned),
            ).fetchall()
        return [_row_to_record(r) for r in rows]

    # ------------------------------------------------------------------ views
    def open_items(self, limit: int = 25) -> list[AgentRecord]:
        placeholders = ",".join("?" for _ in OPEN_EXCLUDED_STATES)
        with self.connection() as conn:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM agents WHERE archived = 0 AND state NOT IN ({placeholders}) "
                "ORDER BY last_active_at DESC, id DESC LIMIT ?",
                (*OPEN_EXCLUDED_STATES, limit),
            ).fetchall()
        return [_row_to_record(r) for r in rows]

    def count_open(self) -> int:
        placeholders = ",".join("?" for _ in OPEN_EXCLUDED_STATES)
        with self.connection() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM agents WHERE archived = 0 AND state NOT IN ({placeholders})",
                OPEN_EXCLUDED_STATES,
            ).fetchone()
        return int(row[0])

    def recent(
        self, limit: int = 5, exclude: Iterable[int] = frozenset(), only_closed: bool = False
    ) -> list[AgentRecord]:
        """Most recently active unarchived agents; ``only_closed`` keeps just ``done`` ones."""
        skip = set(exclude)
        state_clause = "AND state = 'done'" if only_closed else ""
        with self.connection() as conn:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM agents WHERE archived = 0 {state_clause} "
                "ORDER BY last_active_at DESC, id DESC LIMIT ?",
                (limit + len(skip),),
            ).fetchall()
        out = [_row_to_record(r) for r in rows if int(r["id"]) not in skip]
        return out[:limit]

    def count(self, include_archived: bool = True) -> int:
        clause = "" if include_archived else "WHERE archived = 0"
        with self.connection() as conn:
            return int(conn.execute(f"SELECT COUNT(*) FROM agents {clause}").fetchone()[0])

    # ------------------------------------------------------------------ small key/value state
    def get_meta(self, key: str) -> Optional[str]:
        with self.connection() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: Optional[str]) -> None:
        with self.connection() as conn:
            if value is None:
                conn.execute("DELETE FROM meta WHERE key = ?", (key,))
            else:
                conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))

    def pending_choice(self, max_age_hours: float = 24.0) -> Optional[dict]:
        """The last "which one?" question Poke had to ask, if recent: {phrase, candidates, at}."""
        raw = self.get_meta("pending_choice")
        if not raw:
            return None
        try:
            data = json.loads(raw)
            asked = parse_iso(data["at"])
        except Exception:
            return None
        if (self._clock() - asked).total_seconds() > max_age_hours * 3600:
            return None
        return data

    def set_pending_choice(self, phrase: str, candidates: list[int]) -> None:
        self.set_meta("pending_choice", json.dumps({"phrase": phrase, "candidates": candidates, "at": self._now()}))

    def names(self) -> list[str]:
        with self.connection() as conn:
            return [r["name"] for r in conn.execute("SELECT name FROM agents ORDER BY id").fetchall()]

    # ------------------------------------------------------------------ bulk (evals, migration)
    def bulk_insert(self, records: list[AgentRecord], refs: dict[int, list[Ref]]) -> None:
        with self.connection() as conn:
            conn.execute("BEGIN")
            try:
                conn.executemany(
                    f"INSERT INTO agents ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            r.id, r.name, r.name_key or normalize_name(r.name), r.created_at,
                            r.last_active_at, r.state, r.state_note, r.topic, r.request_count,
                            1 if r.archived else 0, 1 if r.has_active_trigger else 0,
                        )
                        for r in records
                    ],
                )
                conn.executemany(
                    "INSERT OR IGNORE INTO agent_refs (agent_id, kind, value, created_at) VALUES (?, ?, ?, ?)",
                    [
                        (agent_id, ref.kind, normalize_ref(ref.kind, ref.value), self._now())
                        for agent_id, items in refs.items()
                        for ref in items
                        if normalize_ref(ref.kind, ref.value)
                    ],
                )
                refs_text: dict[int, list[str]] = {}
                for agent_id, items in refs.items():
                    refs_text[agent_id] = [normalize_ref(x.kind, x.value) for x in items]
                conn.executemany(
                    "INSERT INTO agents_fts (rowid, name, topic, state_note, refs_text) VALUES (?, ?, ?, ?, ?)",
                    [
                        (r.id, r.name, r.topic, r.state_note, " ".join(refs_text.get(r.id, [])))
                        for r in records
                    ],
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise


_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_DB_PATH = _DATA_DIR / "execution_agents" / "agents.db"
_LEGACY_ROSTER = _DATA_DIR / "execution_agents" / "roster.json"

_registry: Optional[AgentRegistry] = None
_registry_lock = threading.Lock()
# Per-task override (evals run many isolated scenarios concurrently in one process).
_registry_override: ContextVar[Optional[AgentRegistry]] = ContextVar("openpoke_registry_override", default=None)


def get_agent_registry() -> AgentRegistry:
    global _registry
    override = _registry_override.get()
    if override is not None:
        return override
    with _registry_lock:
        if _registry is None:
            _registry = AgentRegistry(_DB_PATH, legacy_roster_path=_LEGACY_ROSTER)
        return _registry


@contextmanager
def use_registry(registry: AgentRegistry) -> Iterator[AgentRegistry]:
    """Route ``get_agent_registry()`` to ``registry`` for the current task/context only."""
    token = _registry_override.set(registry)
    try:
        yield registry
    finally:
        _registry_override.reset(token)


def set_agent_registry(registry: Optional[AgentRegistry]) -> None:
    """Swap the process-wide registry (tests and evals)."""
    global _registry
    with _registry_lock:
        _registry = registry
