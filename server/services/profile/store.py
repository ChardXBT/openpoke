"""What Poke knows about its one user: people, preferences, places, routines, facts.

Each fact is (kind, subject, value[, email]). Writing follows the add / update / no-op
pattern: the same subject with a new value supersedes the old one, which is kept with an
``invalid_at`` date instead of contradicting it (so "moved to Toronto" replaces Vancouver
without losing when). ``forget`` hard-deletes a subject, history included.

Only things the user said (or confirmed) are written here: never email bodies or agent
output, which are untrusted. The store is local SQLite next to the agent registry.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional

KINDS = ("person", "preference", "place", "routine", "fact")
ALWAYS_SHOWN = ("preference", "routine")
MAX_VALUE = 300
MAX_ALWAYS = 8
MAX_RELEVANT = 5

_WORD = re.compile(r"[0-9a-z]+")
_FILLER = frozenset("my the a an our".split())

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    value TEXT NOT NULL,
    email TEXT,
    source TEXT NOT NULL DEFAULT 'user',
    created_at TEXT NOT NULL,
    invalid_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_facts_current ON facts (subject, invalid_at);
"""


def _now_iso(clock: Callable[[], datetime]) -> str:
    return clock().astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def subject_key(subject: str) -> str:
    """'My Dentist' -> 'dentist'; 'Home City' -> 'home city'."""
    words = [w for w in _WORD.findall((subject or "").casefold()) if w not in _FILLER]
    return " ".join(words)


@dataclass
class Fact:
    id: int
    kind: str
    subject: str
    value: str
    email: Optional[str]
    source: str
    created_at: str
    invalid_at: Optional[str]

    def line(self) -> str:
        email = f" <{self.email}>" if self.email else ""
        return f"- {self.subject} ({self.kind}): {self.value}{email}"


def _row(r: sqlite3.Row) -> Fact:
    return Fact(int(r["id"]), r["kind"], r["subject"], r["value"], r["email"], r["source"],
                r["created_at"], r["invalid_at"])


class ProfileStore:
    def __init__(self, db_path: Path, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, timeout=30, isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._db() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            yield self._conn

    # ------------------------------------------------------------------ writes
    def remember(self, kind: str, subject: str, value: str, email: Optional[str] = None, source: str = "user") -> str:
        """Returns "add", "update", "noop" or "skip"."""
        key = subject_key(subject)
        text = " ".join((value or "").split())[:MAX_VALUE]
        if not key or not text:
            return "skip"
        kind = kind if kind in KINDS else "fact"
        mail = (email or "").strip().casefold() or None
        now = _now_iso(self._clock)
        with self._db() as conn:
            current = conn.execute(
                "SELECT * FROM facts WHERE subject = ? AND invalid_at IS NULL ORDER BY id DESC", (key,)
            ).fetchone()
            if current is not None:
                same_value = current["value"].casefold() == text.casefold()
                same_mail = (current["email"] or None) == mail or mail is None
                if same_value and same_mail:
                    return "noop"
                conn.execute("UPDATE facts SET invalid_at = ? WHERE id = ?", (now, current["id"]))
                if mail is None:
                    mail = current["email"]
            conn.execute(
                "INSERT INTO facts (kind, subject, value, email, source, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (kind, key, text, mail, source, now),
            )
        return "update" if current is not None else "add"

    def forget(self, subject: str) -> int:
        key = subject_key(subject)
        if not key:
            return 0
        with self._db() as conn:
            return conn.execute("DELETE FROM facts WHERE subject = ?", (key,)).rowcount

    # ------------------------------------------------------------------ reads
    def current(self) -> list[Fact]:
        with self._db() as conn:
            rows = conn.execute("SELECT * FROM facts WHERE invalid_at IS NULL ORDER BY id").fetchall()
        return [_row(r) for r in rows]

    def history(self, subject: str) -> list[Fact]:
        with self._db() as conn:
            rows = conn.execute("SELECT * FROM facts WHERE subject = ? ORDER BY id", (subject_key(subject),)).fetchall()
        return [_row(r) for r in rows]

    def person(self, subject: str) -> Optional[Fact]:
        with self._db() as conn:
            row = conn.execute(
                "SELECT * FROM facts WHERE subject = ? AND kind = 'person' AND invalid_at IS NULL ORDER BY id DESC",
                (subject_key(subject),),
            ).fetchone()
        return _row(row) if row else None

    def people(self) -> list[Fact]:
        return [f for f in self.current() if f.kind == "person"]

    def relevant(self, text: str, limit: int = MAX_RELEVANT) -> list[Fact]:
        """Current facts whose subject (or a word of it) or email appears in ``text``."""
        words = set(_WORD.findall((text or "").casefold()))
        lowered = (text or "").casefold()
        scored = []
        for fact in self.current():
            subject_words = set(fact.subject.split())
            hits = len(subject_words & words) + (2 if fact.subject in lowered else 0)
            if fact.email and fact.email in lowered:
                hits += 3
            if hits:
                scored.append((hits, fact.id, fact))
        scored.sort(key=lambda t: (-t[0], -t[1]))
        return [f for _, _, f in scored[:limit]]

    def render_block(self, text: str) -> str:
        """The ``<about_user>`` body: always-on preferences/routines + facts relevant to the message."""
        facts = self.current()
        if not facts:
            return "None"
        always = [f for f in reversed(facts) if f.kind in ALWAYS_SHOWN][:MAX_ALWAYS]
        shown = {f.id for f in always}
        relevant = [f for f in self.relevant(text) if f.id not in shown]
        lines = [f.line() for f in always + relevant]
        hidden = len(facts) - len(lines)
        if hidden > 0:
            lines.append(f"(+{hidden} more facts on file; call list_about_user to see them all)")
        return "\n".join(lines) if lines else "None"


_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_store: Optional[ProfileStore] = None
_store_lock = threading.Lock()
_override: ContextVar[Optional[ProfileStore]] = ContextVar("openpoke_profile_override", default=None)


def get_profile_store() -> ProfileStore:
    global _store
    override = _override.get()
    if override is not None:
        return override
    with _store_lock:
        if _store is None:
            _store = ProfileStore(_DATA_DIR / "profile.db")
        return _store


@contextmanager
def use_profile_store(store: ProfileStore) -> Iterator[ProfileStore]:
    token = _override.set(store)
    try:
        yield store
    finally:
        _override.reset(token)
