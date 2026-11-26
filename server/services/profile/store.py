"""What Poke knows about its one user: people, preferences, places, routines, facts.

Each fact is (kind, subject, value[, email]). Writing follows the add / update / no-op
pattern: the same subject with a new value supersedes the old one, which is kept with an
``invalid_at`` date instead of contradicting it (so "moved to Toronto" replaces Vancouver
without losing when). A fact can also be temporary ("in Lisbon until Friday"): it is stored
with a future ``invalid_at`` and drops out on its own. ``retire`` ends a fact that no longer
holds (history kept); ``forget`` hard-deletes a subject, history included.

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

from ..tenancy import current_namespace, data_root, multi_tenant_enabled, scoped_data_dir

KINDS = ("person", "preference", "place", "routine", "fact")
ALWAYS_SHOWN = ("preference", "routine")
MAX_VALUE = 300
MAX_ALWAYS = 8
MAX_RELEVANT = 5
SMALL_PROFILE = 15  # a profile this small is shown whole every turn

_WORD = re.compile(r"[0-9a-z]+")
_FILLER = frozenset("my the a an our".split())
# One spelling per relationship, so "my mother" and "mom" are the same subject.
_CANON = {"mother": "mom", "mum": "mom", "mommy": "mom", "mama": "mom", "mam": "mom",
          "father": "dad", "daddy": "dad", "papa": "dad", "spouse": "partner"}
# Words that point at the same person in a request ("email my wife" finds the partner).
_RELATED = [
    frozenset("partner wife husband girlfriend boyfriend gf bf fiance fiancee".split()),
    frozenset("boss manager supervisor".split()),
    frozenset("doctor gp physician".split()),
]
_NAME_STOP = frozenset("the and for with from dr mr mrs ms miss".split())

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
    """'My Dentist' -> 'dentist'; 'Home City' -> 'home city'; 'my mother' -> 'mom'."""
    words = [_CANON.get(w, w) for w in _WORD.findall((subject or "").casefold()) if w not in _FILLER]
    return " ".join(words)


def _until_iso(until: Optional[str]) -> Optional[str]:
    """'2026-11-27' -> the end of that day (UTC); a full ISO time is kept; anything else -> None."""
    text = (until or "").strip()
    if not text:
        return None
    try:
        if len(text) == 10:
            moment = datetime.strptime(text, "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
        else:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
            moment = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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

    @property
    def temporary(self) -> bool:
        """A current fact with an end date (current facts never carry a past one)."""
        return self.invalid_at is not None

    def line(self) -> str:
        email = f" <{self.email}>" if self.email else ""
        until = f" (until {self.invalid_at[:10]})" if self.invalid_at else ""
        return f"- {self.subject} ({self.kind}): {self.value}{email}{until}"


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
    def remember(self, kind: str, subject: str, value: str, email: Optional[str] = None, source: str = "user",
                 until: Optional[str] = None) -> str:
        """Returns "add", "update", "noop" or "skip". ``until`` (a date) makes the fact temporary."""
        key = subject_key(subject)
        text = " ".join((value or "").split())[:MAX_VALUE]
        if not key or not text:
            return "skip"
        kind = kind if kind in KINDS else "fact"
        mail = (email or "").strip().casefold() or None
        now = _now_iso(self._clock)
        ends = _until_iso(until)
        if until and (ends is None or ends <= now):
            return "skip"  # an unreadable or already-past end date
        with self._db() as conn:
            current = conn.execute(
                "SELECT * FROM facts WHERE subject = ? AND (invalid_at IS NULL OR invalid_at > ?) ORDER BY id DESC",
                (key, now),
            ).fetchone()
            if current is not None:
                same_value = current["value"].casefold() == text.casefold()
                same_mail = (current["email"] or None) == mail or mail is None
                if same_value and same_mail and current["invalid_at"] == ends:
                    return "noop"
                conn.execute("UPDATE facts SET invalid_at = ? WHERE id = ?", (now, current["id"]))
                if mail is None:
                    mail = current["email"]
            conn.execute(
                "INSERT INTO facts (kind, subject, value, email, source, created_at, invalid_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (kind, key, text, mail, source, now, ends),
            )
        return "update" if current is not None else "add"

    def retire(self, subject: str) -> int:
        """End a subject's current fact because it no longer holds; its history is kept."""
        key = subject_key(subject)
        if not key:
            return 0
        now = _now_iso(self._clock)
        with self._db() as conn:
            return conn.execute(
                "UPDATE facts SET invalid_at = ? WHERE subject = ? AND (invalid_at IS NULL OR invalid_at > ?)",
                (now, key, now),
            ).rowcount

    def forget(self, subject: str) -> int:
        key = subject_key(subject)
        if not key:
            return 0
        with self._db() as conn:
            return conn.execute("DELETE FROM facts WHERE subject = ?", (key,)).rowcount

    # ------------------------------------------------------------------ reads
    def current(self) -> list[Fact]:
        now = _now_iso(self._clock)
        with self._db() as conn:
            rows = conn.execute(
                "SELECT * FROM facts WHERE invalid_at IS NULL OR invalid_at > ? ORDER BY id", (now,)
            ).fetchall()
        return [_row(r) for r in rows]

    def history(self, subject: str) -> list[Fact]:
        with self._db() as conn:
            rows = conn.execute("SELECT * FROM facts WHERE subject = ? ORDER BY id", (subject_key(subject),)).fetchall()
        return [_row(r) for r in rows]

    def person(self, subject: str) -> Optional[Fact]:
        key = subject_key(subject)
        matches = [f for f in self.current() if f.kind == "person" and f.subject == key]
        return matches[-1] if matches else None

    def people(self) -> list[Fact]:
        return [f for f in self.current() if f.kind == "person"]

    def relevant(self, text: str, limit: int = MAX_RELEVANT) -> list[Fact]:
        """Current facts the message points at: by subject ("my mother" finds mom, "my wife" the
        partner), by a person's name ("email Sarah" finds the sister) or by email address."""
        lowered = (text or "").casefold()
        tokens = [_CANON.get(w, w) for w in _WORD.findall(lowered)]
        words = set(tokens)
        for group in _RELATED:
            if words & group:
                words |= group
        padded = f" {' '.join(tokens)} "
        scored = []
        for fact in self.current():
            subject_words = set(fact.subject.split())
            hits = len(subject_words & words) + (2 if f" {fact.subject} " in padded else 0)
            if fact.kind == "person":
                names = {w for w in _WORD.findall(fact.value.casefold()) if len(w) >= 3 and w not in _NAME_STOP}
                hits += 2 * len(names & words)
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
        if len(facts) <= SMALL_PROFILE:
            return "\n".join(f.line() for f in facts)
        always = [f for f in reversed(facts) if f.kind in ALWAYS_SHOWN or f.temporary][:MAX_ALWAYS]
        shown = {f.id for f in always}
        relevant = [f for f in self.relevant(text) if f.id not in shown]
        lines = [f.line() for f in always + relevant]
        hidden = len(facts) - len(lines)
        if hidden > 0:
            lines.append(f"(+{hidden} more facts on file; call list_about_user to see them all)")
        return "\n".join(lines) if lines else "None"


def _data_dir() -> Path:
    return scoped_data_dir(data_root())


_store: Optional[ProfileStore] = None
_tenant_stores: dict[str, ProfileStore] = {}
_store_lock = threading.Lock()
_override: ContextVar[Optional[ProfileStore]] = ContextVar("openpoke_profile_override", default=None)


def get_profile_store() -> ProfileStore:
    global _store
    override = _override.get()
    if override is not None:
        return override
    with _store_lock:
        if multi_tenant_enabled():
            namespace = current_namespace()
            if namespace not in _tenant_stores:
                _tenant_stores[namespace] = ProfileStore(_data_dir() / "profile.db")
            return _tenant_stores[namespace]
        if _store is None:
            _store = ProfileStore(_data_dir() / "profile.db")
        return _store


@contextmanager
def use_profile_store(store: ProfileStore) -> Iterator[ProfileStore]:
    token = _override.set(store)
    try:
        yield store
    finally:
        _override.reset(token)
