"""Search over execution agents: exact references first, then wording, state and recency.

Used by the Interaction Agent's ``search_agents`` tool and the spawn-or-reuse check.
FTS5 does the wording match; exact references (email addresses, thread/draft ids,
``#id``) come from the ``agent_refs`` index and always outrank wording. Results are
collapsed so one contact cannot fill the list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .embeddings import Embedder, cached_embeddings, openai_embedder
from .registry import AgentRecord, AgentRegistry, normalize_ref, parse_iso
from ...logging_config import logger

RECENCY_HALF_LIFE_DAYS = 30.0
MAX_PER_CONTACT = 2
_CANDIDATE_POOL = 200
_EXACT_REF_BONUS = 10.0
_STATE_WEIGHT_OPEN = 1.0
_STATE_WEIGHT_DONE = 0.85
_STATE_WEIGHT_ARCHIVED = 0.7
# bm25 column weights: name, topic, state_note, refs_text
_BM25_WEIGHTS = (3.0, 2.0, 1.5, 2.0)

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_HASH_ONLY = re.compile(r"^\s*#(\d+)\s*$")
_WORD = re.compile(r"[0-9a-z]+")
_ID_LIKE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{4,}$")
_STOPWORDS = frozenset(
    "a an and are about any as at be by can did do does for from get got has have he her him his "
    "how i if in is it its just me my of on or our please re she so that the their them they this "
    "to up us was we what when where which who will with you your".split()
)

# Small deterministic paraphrase lattice for the everyday wording personal assistants see.
# It is deliberately narrow: exact lexical matches score at full strength below, while these
# neighbours only provide a lower-confidence recall path when the query shares no words.
_PARAPHRASE_GROUPS = (
    frozenset("japan japanese tokyo kyoto osaka shinjuku".split()),
    frozenset("food foods restaurant restaurants dinner lunch ramen sushi eat eats meal meals".split()),
    frozenset("invoice invoices bill bills billing payment payments unpaid owed".split()),
    frozenset("email emails mail mails message messages letter".split()),
    frozenset("flight flights plane airline travel trip trips ticket tickets".split()),
    frozenset("document documents doc docs paperwork file files visa".split()),
    frozenset("gift gifts present presents birthday".split()),
    frozenset("apartment apartments lease leases rental rentals housing".split()),
    frozenset("mom mother mum".split()),
    frozenset("dad father".split()),
)


@dataclass
class SearchHit:
    agent: AgentRecord
    score: float
    hidden_for_contact: int = 0


def _query_tokens(text: str) -> list[str]:
    tokens = []
    for token in _WORD.findall((text or "").casefold()):
        if len(token) < 2 or token in _STOPWORDS:
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens[:24]


def _paraphrase_tokens(tokens: list[str]) -> list[str]:
    expanded: list[str] = []
    original = set(tokens)
    for token in tokens:
        for group in _PARAPHRASE_GROUPS:
            if token not in group:
                continue
            for neighbour in sorted(group):
                if neighbour not in original and len(neighbour) >= 2 and neighbour not in expanded:
                    expanded.append(neighbour)
            break
    return expanded[:24]


def _fts_query(text_or_tokens) -> str:
    """Build a safe FTS5 query: every token quoted, prefix-matched, OR-joined."""
    tokens = text_or_tokens if isinstance(text_or_tokens, list) else _query_tokens(text_or_tokens)
    return " OR ".join(f'"{t}"*' for t in tokens[:24])


def _recency(last_active_at: str, now: datetime) -> float:
    try:
        days = max(0.0, (now - parse_iso(last_active_at)).total_seconds() / 86400.0)
    except ValueError:
        return 0.5
    return 0.5 ** (days / RECENCY_HALF_LIFE_DAYS)


def _state_weight(rec: AgentRecord) -> float:
    if rec.archived:
        return _STATE_WEIGHT_ARCHIVED
    return _STATE_WEIGHT_DONE if rec.state == "done" else _STATE_WEIGHT_OPEN



def _semantic_scores(
    reg: AgentRegistry,
    query: str,
    embedder: Embedder,
    limit: int = _CANDIDATE_POOL,
) -> dict[int, float]:
    """Cosine similarity over cached card embeddings; empty when embeddings are unavailable."""
    try:
        import numpy as np

        with reg.connection() as conn:
            rows = conn.execute(
                "SELECT id, name, topic, state_note FROM agents ORDER BY id LIMIT 20000"
            ).fetchall()
            ref_rows = conn.execute("SELECT agent_id, value FROM agent_refs ORDER BY agent_id, created_at").fetchall()
        refs: dict[int, list[str]] = {}
        for row in ref_rows:
            refs.setdefault(int(row["agent_id"]), []).append(row["value"])
        ids = [int(row["id"]) for row in rows]
        cards = [
            " ".join(part for part in [row["name"], row["topic"], row["state_note"], " ".join(refs.get(int(row["id"]), []))] if part)
            for row in rows
        ]
        vectors = cached_embeddings([query, *cards], embedder)
        if len(vectors) != len(cards) + 1:
            return {}
        matrix = np.asarray(vectors[1:], dtype=np.float32)
        query_vector = np.asarray(vectors[0], dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        matrix /= np.where(norms == 0, 1.0, norms)
        query_norm = float(np.linalg.norm(query_vector)) or 1.0
        similarities = matrix @ (query_vector / query_norm)
        order = np.argsort(-similarities)[:limit]
        return {ids[int(index)]: float(similarities[int(index)]) for index in order if float(similarities[int(index)]) > 0.0}
    except Exception as exc:  # embeddings are optional recall; lexical search must never break
        logger.debug(f"agent semantic search skipped: {exc}")
        return {}


def exact_ref_matches(reg: AgentRegistry, text: str) -> list[AgentRecord]:
    """Agents whose stored references appear verbatim in ``text`` (emails, thread/draft ids)."""
    found: dict[int, AgentRecord] = {}
    for email in _EMAIL.findall(text):
        for rec in reg.owners("contact", email):
            found.setdefault(rec.id, rec)
    for raw in text.split():
        token = raw.strip(".,;:!?()[]{}\"'")
        if _ID_LIKE.match(token) and any(ch.isdigit() for ch in token):
            for kind in ("thread", "draft", "trigger"):
                for rec in reg.owners(kind, token):
                    found.setdefault(rec.id, rec)
    return list(found.values())


def _primary_contacts(reg: AgentRegistry, ids: list[int]) -> dict[int, str]:
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    with reg.connection() as conn:
        rows = conn.execute(
            f"SELECT agent_id, value FROM agent_refs WHERE kind = 'contact' AND agent_id IN ({placeholders}) "
            "ORDER BY created_at, value",
            ids,
        ).fetchall()
    contacts: dict[int, str] = {}
    for row in rows:
        contacts.setdefault(int(row["agent_id"]), row["value"])
    return contacts


def search_agents(
    reg: AgentRegistry,
    query: str,
    include_archived: bool = False,
    limit: int = 8,
    now: Optional[datetime] = None,
    embedder: Optional[Embedder] = None,
) -> list[SearchHit]:
    text = (query or "").strip()
    if not text or limit <= 0:
        return []
    moment = now or reg.clock()

    only_id = _HASH_ONLY.match(text)
    if only_id:
        rec = reg.get(int(only_id.group(1)))
        return [SearchHit(rec, _EXACT_REF_BONUS)] if rec else []

    scores: dict[int, float] = {}
    records: dict[int, AgentRecord] = {}

    for rec in exact_ref_matches(reg, text):
        records[rec.id] = rec
        scores[rec.id] = _EXACT_REF_BONUS + _recency(rec.last_active_at, moment)

    tokens = _query_tokens(text)
    lexical_passes = [(tokens, 1.0), (_paraphrase_tokens(tokens), 0.45)]
    for pass_tokens, confidence in lexical_passes:
        fts = _fts_query(pass_tokens)
        if not fts:
            continue
        weights = ", ".join(str(w) for w in _BM25_WEIGHTS)
        with reg.connection() as conn:
            rows = conn.execute(
                f"SELECT rowid AS id, bm25(agents_fts, {weights}) AS rank FROM agents_fts "
                "WHERE agents_fts MATCH ? ORDER BY rank LIMIT ?",
                (fts, _CANDIDATE_POOL),
            ).fetchall()
        if rows:
            best = max(-float(r["rank"]) for r in rows) or 1.0
            for rec in reg.get_many(int(r["id"]) for r in rows):
                records.setdefault(rec.id, rec)
            for row in rows:
                agent_id = int(row["id"])
                rec = records.get(agent_id)
                if rec is None:
                    continue
                text_score = max(0.0, -float(row["rank"])) / best
                blended = confidence * text_score * (0.6 + 0.4 * _recency(rec.last_active_at, moment)) * _state_weight(rec)
                scores[agent_id] = scores.get(agent_id, 0.0) + blended

    active_embedder = embedder if embedder is not None else openai_embedder()
    if active_embedder is not None:
        semantic = _semantic_scores(reg, text, active_embedder)
        if semantic:
            for rec in reg.get_many(semantic):
                records.setdefault(rec.id, rec)
            for agent_id, similarity in semantic.items():
                rec = records.get(agent_id)
                if rec is None:
                    continue
                semantic_score = similarity * (0.6 + 0.4 * _recency(rec.last_active_at, moment)) * _state_weight(rec)
                scores[agent_id] = scores.get(agent_id, 0.0) + 0.55 * semantic_score

    ranked = sorted(
        (records[i] for i in scores if include_archived or not records[i].archived),
        key=lambda r: (-scores[r.id], r.id),
    )
    contacts = _primary_contacts(reg, [r.id for r in ranked[:_CANDIDATE_POOL]])

    hits: list[SearchHit] = []
    shown_per_contact: dict[str, int] = {}
    hidden_per_contact: dict[str, int] = {}
    last_hit_for_contact: dict[str, SearchHit] = {}
    for rec in ranked:
        contact = contacts.get(rec.id)
        if contact:
            if shown_per_contact.get(contact, 0) >= MAX_PER_CONTACT:
                hidden_per_contact[contact] = hidden_per_contact.get(contact, 0) + 1
                continue
            shown_per_contact[contact] = shown_per_contact.get(contact, 0) + 1
        if len(hits) < limit:
            hit = SearchHit(rec, round(scores[rec.id], 4))
            hits.append(hit)
            if contact:
                last_hit_for_contact[contact] = hit
        elif contact:
            hidden_per_contact[contact] = hidden_per_contact.get(contact, 0) + 1
    for contact, hidden in hidden_per_contact.items():
        if contact in last_hit_for_contact:
            last_hit_for_contact[contact].hidden_for_contact = hidden
    return hits


__all__ = ["SearchHit", "search_agents", "exact_ref_matches", "normalize_ref"]
