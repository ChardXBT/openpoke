"""Strong common-fork baseline for bounded agent routing.

The duplicate threshold defaults to 0.62 on a normalized lexical/semantic
similarity.  Tune it only on the development split by balancing duplicate
creation against wrong reuse, then freeze it before evaluating held-out data.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Callable, Optional

from evals.harness.transcript import render_current_turn, render_history
from evals.schema import PreparedTurn, Scenario, World, WorldAgent
from server.services.execution.working_set import STATE_LABELS

_EMBEDDING_MODEL = "text-embedding-3-small"
_EMBEDDING_CACHE_DIR = Path(__file__).parents[1] / ".cache" / "embeddings"
_HTTP_CLIENT = None  # shared httpx.Client for embedding requests
_RRF_K = 60
_DEFAULT_DUP_THRESHOLD = 0.62
_TOKEN_RE = re.compile(r"#?\w+(?:[.@+-]\w+)*", re.UNICODE)

_UPSTREAM_DIR = Path(__file__).parent / "upstream"
_UPSTREAM_PROMPT = (_UPSTREAM_DIR / "system_prompt.md").read_text(encoding="utf-8").strip()
_UPSTREAM_TOOLS = json.loads((_UPSTREAM_DIR / "tools.json").read_text(encoding="utf-8"))

_CARD_GUIDANCE = """

Agent cards and routing

The <agents> block is a bounded working set of relevant and recently active agents. Each card has a stable #id, a name, and its latest work note. Reuse an existing agent whenever it owns the relevant work. Pass its #id as agent_name so similar names cannot be confused. If no visible card fits, call search_agents before creating a new agent. Create a new agent only when the search results do not describe the requested work. If creation returns possible_duplicate, reuse a listed candidate unless the user clearly requested separate work; set create_new=true only for an intentional separate agent.
""".rstrip()


def _tokens(text: str) -> list[str]:
    return [match.group(0).casefold() for match in _TOKEN_RE.finditer(text)]


def _card_text(agent: WorldAgent) -> str:
    refs = " ".join(f"{kind} {value}" for kind, value in agent.refs)
    return " ".join(part for part in (agent.name, agent.topic, agent.state_note, refs) if part)


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _relative_age(now: str, then: str) -> str:
    seconds = max(0, int((_parse_utc(now) - _parse_utc(then)).total_seconds()))
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days < 30:
        return f"{days}d ago"
    months = days // 30
    if months < 12:
        return f"{months}mo ago"
    return f"{days // 365}y ago"


def _format_card(agent: WorldAgent, now: str) -> str:
    # Same status text as B1 and Ours (state label, note or topic, archived flag), so B2 is
    # not judged on thinner cards than the setups it is compared with.
    label = STATE_LABELS.get(agent.state, agent.state)
    detail = agent.state_note or agent.topic
    status = f"{'archived, ' if agent.archived else ''}{label}" + (f": {detail}" if detail else "")
    return (
        f"#{agent.id} {escape(agent.name, quote=False)} · "
        f"{escape(status, quote=False)} · "
        f"last active {_relative_age(now, agent.last_active_at)}"
    )


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


@dataclass
class _Index:
    agents: list[WorldAgent]
    texts: list[str]
    term_counts: list[Counter[str]]
    document_frequency: Counter[str]
    average_length: float
    embeddings: Optional[list[list[float]]]


class B2Common:
    """Agent-card retrieval baseline used as evaluation setup B2."""

    name = "b2_common"

    def __init__(
        self,
        k: int = 12,
        recent: int = 5,
        embeddings: bool = True,
        embedder: Optional[Callable[[list[str]], list[list[float]]]] = None,
        dup_threshold: float | None = None,
    ) -> None:
        if k < 0 or recent < 0:
            raise ValueError("k and recent must be non-negative")
        self.k = k
        self.recent = recent
        self.embeddings = embeddings
        # Rows must say which B2 ran: lexical-only is a weaker baseline, not the same one.
        self.name = "b2_common" if embeddings else "b2_common-lexical"
        self.embedder = embedder or self._openai_embed
        self.dup_threshold = (
            _DEFAULT_DUP_THRESHOLD if dup_threshold is None else dup_threshold
        )
        if not 0.0 <= self.dup_threshold <= 1.0:
            raise ValueError("dup_threshold must be between 0 and 1")
        self._indexes: dict[tuple[int, int], _Index] = {}

    def prepare(self, world: World, scenario: Scenario, workdir: Path) -> PreparedTurn:
        del workdir  # The setup is deterministic apart from the shared embedding cache.
        index = self._index_for(world)
        latest_text, message_type = render_current_turn(scenario.message, scenario.event)
        current_query = scenario.event.summary if scenario.event else latest_text
        previous_user_turns = [
            turn.text for turn in scenario.history if turn.role == "user"
        ][-2:]
        query = "\n".join([current_query, *previous_user_turns])

        ranked = self._rank(index, query)
        selected = [agent for agent, _ in ranked[: self.k]]
        selected_ids = {agent.id for agent in selected}
        recent = sorted(
            (agent for agent in world.agents if agent.id not in selected_ids),
            key=lambda agent: (agent.last_active_at, -agent.id),
            reverse=True,
        )[: self.recent]
        cards = selected + recent
        agents_text = "\n".join(_format_card(agent, world.now) for agent in cards) or "None"

        history = render_history(scenario.history, world).strip() or "None"
        current_tag = "new_agent_message" if message_type == "agent" else "new_user_message"
        user_message = (
            f"<conversation_history>\n{history}\n</conversation_history>\n\n"
            f"<agents>\n{agents_text}\n</agents>\n\n"
            f"<{current_tag}>\n{latest_text.strip()}\n</{current_tag}>"
        )
        system_prompt = _UPSTREAM_PROMPT + _CARD_GUIDANCE
        tools = self._tools()
        agents_by_id = {agent.id: agent for agent in world.agents}
        names = {agent.name: agent.id for agent in world.agents}
        folded_names = {agent.name.casefold(): agent.id for agent in world.agents}

        def resolve_target(name_or_id: str) -> Optional[int]:
            candidate = str(name_or_id).strip()
            id_match = re.match(r"#(\d+)\b", candidate)  # "#15" or "#15 Air Canada Refund"
            if id_match and int(id_match.group(1)) in agents_by_id:
                return int(id_match.group(1))
            if candidate in names:
                return names[candidate]
            return folded_names.get(candidate.casefold())

        def handle_tool(name: str, args: dict) -> dict:
            if name == "search_agents":
                limit = args.get("limit", 8)
                if not isinstance(limit, int) or isinstance(limit, bool):
                    limit = 8
                limit = max(1, min(limit, 50))
                hits = self._rank(index, str(args.get("query", "")))[:limit]
                return {"agents": [_format_card(agent, world.now) for agent, _ in hits]}

            if name == "send_message_to_agent":
                agent_name = str(args.get("agent_name", ""))
                target_id = resolve_target(agent_name)
                if target_id is not None:
                    target = agents_by_id[target_id]
                    return {
                        "status": "submitted",
                        "agent": f"#{target.id} {target.name}",
                    }

                if args.get("create_new") is not True:
                    duplicate_query = " ".join(
                        (agent_name, str(args.get("instructions", "")))
                    ).strip()
                    candidates = self._duplicate_candidates(index, duplicate_query)
                    if candidates and candidates[0][1] >= self.dup_threshold:
                        return {
                            "status": "possible_duplicate",
                            "candidates": [
                                _format_card(agent, world.now)
                                for agent, _ in candidates[:3]
                            ],
                        }
                return {"status": "submitted", "new_agent_created": True}

            if name in {"send_message_to_user", "send_draft", "wait"}:
                return {"status": "success"}
            return {"status": "error", "error": f"Unknown tool: {name}"}

        return PreparedTurn(
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_message}],
            tools=tools,
            handle_tool=handle_tool,
            resolve_target=resolve_target,
            prompt_chars=len(system_prompt) + len(user_message),
        )

    def _index_for(self, world: World) -> _Index:
        cache_key = (world.seed, world.size)
        cached = self._indexes.get(cache_key)
        if cached is not None:
            return cached

        texts = [_card_text(agent) for agent in world.agents]
        term_counts = [Counter(_tokens(text)) for text in texts]
        document_frequency: Counter[str] = Counter()
        for counts in term_counts:
            document_frequency.update(counts.keys())
        average_length = (
            sum(sum(counts.values()) for counts in term_counts) / len(term_counts)
            if term_counts
            else 0.0
        )
        vectors = self._embedding_matrix(texts) if self.embeddings else None
        index = _Index(
            agents=list(world.agents),
            texts=texts,
            term_counts=term_counts,
            document_frequency=document_frequency,
            average_length=average_length,
            embeddings=vectors,
        )
        self._indexes[cache_key] = index
        return index

    def _bm25_scores(self, index: _Index, query: str) -> list[float]:
        query_terms = _tokens(query)
        count = len(index.agents)
        if count == 0:
            return []
        scores: list[float] = []
        for counts in index.term_counts:
            document_length = sum(counts.values())
            score = 0.0
            for term in query_terms:
                frequency = counts.get(term, 0)
                if frequency == 0:
                    continue
                document_frequency = index.document_frequency[term]
                inverse_frequency = math.log(
                    1.0 + (count - document_frequency + 0.5) / (document_frequency + 0.5)
                )
                length_ratio = (
                    document_length / index.average_length
                    if index.average_length
                    else 0.0
                )
                denominator = frequency + 1.2 * (1.0 - 0.75 + 0.75 * length_ratio)
                score += inverse_frequency * frequency * 2.2 / denominator
            scores.append(score)
        return scores

    def _rank(self, index: _Index, query: str) -> list[tuple[WorldAgent, float]]:
        bm25 = self._bm25_scores(index, query)
        bm25_order = sorted(range(len(index.agents)), key=lambda i: (-bm25[i], i))
        fused = {
            document: 1.0 / (_RRF_K + rank)
            for rank, document in enumerate(bm25_order, start=1)
        }

        if self.embeddings and index.embeddings is not None and index.agents:
            semantic = (index.embeddings @ self._unit_vector(query)).tolist()
            semantic_order = sorted(
                range(len(index.agents)), key=lambda i: (-semantic[i], i)
            )
            for rank, document in enumerate(semantic_order, start=1):
                fused[document] = fused.get(document, 0.0) + 1.0 / (
                    _RRF_K + rank
                )

        order = sorted(fused, key=lambda i: (-fused[i], i))
        return [(index.agents[i], fused[i]) for i in order]

    def _duplicate_candidates(
        self, index: _Index, query: str
    ) -> list[tuple[WorldAgent, float]]:
        query_counts = Counter(_tokens(query))
        semantic_all = None
        if self.embeddings and index.embeddings is not None:
            semantic_all = (index.embeddings @ self._unit_vector(query)).tolist()
        candidates: list[tuple[WorldAgent, float]] = []
        for position, agent in enumerate(index.agents):
            lexical = self._weighted_token_cosine(index, query_counts, position)
            if semantic_all is not None:
                semantic = max(0.0, semantic_all[position])
                similarity = 0.45 * lexical + 0.55 * semantic
            else:
                similarity = lexical
            candidates.append((agent, similarity))
        return sorted(candidates, key=lambda item: (-item[1], item[0].id))

    @staticmethod
    def _weighted_token_cosine(
        index: _Index, query_counts: Counter[str], position: int
    ) -> float:
        document_counts = index.term_counts[position]
        count = len(index.agents)

        def weights(counts: Counter[str]) -> dict[str, float]:
            weighted: dict[str, float] = {}
            for term, frequency in counts.items():
                document_frequency = index.document_frequency.get(term, 0)
                inverse_frequency = math.log(
                    1.0 + (count + 1.0) / (document_frequency + 1.0)
                )
                weighted[term] = (1.0 + math.log(frequency)) * inverse_frequency
            return weighted

        query_weights = weights(query_counts)
        document_weights = weights(document_counts)
        terms = query_weights.keys() & document_weights.keys()
        numerator = sum(query_weights[term] * document_weights[term] for term in terms)
        query_norm = math.sqrt(sum(value * value for value in query_weights.values()))
        document_norm = math.sqrt(
            sum(value * value for value in document_weights.values())
        )
        if query_norm == 0.0 or document_norm == 0.0:
            return 0.0
        return numerator / (query_norm * document_norm)

    def _embedding_matrix(self, texts: list[str]):
        """Row-normalized float32 matrix, filled in chunks so peak memory stays small
        (20,000 x 1,536 Python floats would be ~1 GB; this is ~120 MB)."""
        import numpy as np

        matrix = None
        for start in range(0, len(texts), 1000):
            chunk = np.asarray(self._embeddings_for(texts[start : start + 1000]), dtype=np.float32)
            if matrix is None:
                matrix = np.empty((len(texts), chunk.shape[1]), dtype=np.float32)
            matrix[start : start + len(chunk)] = chunk
        if matrix is None:
            return None
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        matrix /= np.where(norms == 0, 1.0, norms)
        return matrix

    def _unit_vector(self, text: str):
        import numpy as np

        vector = np.asarray(self._embeddings_for([text])[0], dtype=np.float32)
        norm = float(np.linalg.norm(vector))
        return vector / norm if norm else vector

    def _embeddings_for(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        _EMBEDDING_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        vectors: list[Optional[list[float]]] = [None] * len(texts)
        misses: dict[str, list[int]] = {}
        paths: dict[str, Path] = {}

        for position, text in enumerate(texts):
            digest = hashlib.sha256(
                f"{_EMBEDDING_MODEL}\0{text}".encode("utf-8")
            ).hexdigest()
            path = _EMBEDDING_CACHE_DIR / f"{digest}.json"
            paths[text] = path
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(value, list) or not all(
                    isinstance(item, (int, float)) for item in value
                ):
                    raise ValueError("invalid cached vector")
                vectors[position] = [float(item) for item in value]
            except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
                misses.setdefault(text, []).append(position)

        missing_texts = list(misses)
        for start in range(0, len(missing_texts), 128):
            batch = missing_texts[start : start + 128]
            embedded = self.embedder(batch)
            if len(embedded) != len(batch):
                raise ValueError("embedder returned the wrong number of vectors")
            for text, vector in zip(batch, embedded):
                clean_vector = [float(item) for item in vector]
                paths[text].write_text(
                    json.dumps(clean_vector, separators=(",", ":")),
                    encoding="utf-8",
                )
                for position in misses[text]:
                    vectors[position] = clean_vector

        if any(vector is None for vector in vectors):
            raise RuntimeError("embedding cache did not produce every requested vector")
        return [vector for vector in vectors if vector is not None]

    @staticmethod
    def _openai_embed(texts: list[str]) -> list[list[float]]:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required when embeddings are enabled")
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - deployment dependency check
            raise RuntimeError("httpx is required for OpenAI embeddings") from exc

        global _HTTP_CLIENT
        if _HTTP_CLIENT is None:  # one client: building an SSL context per call costs ~0.7 s on Windows
            _HTTP_CLIENT = httpx.Client(timeout=60.0)
        response = _HTTP_CLIENT.post(
            "https://api.openai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": _EMBEDDING_MODEL, "input": texts},
            timeout=60.0,
        )
        response.raise_for_status()
        payload = response.json()
        rows = sorted(payload["data"], key=lambda row: row["index"])
        return [[float(item) for item in row["embedding"]] for row in rows]

    @staticmethod
    def _tools() -> list[dict]:
        tools = copy.deepcopy(_UPSTREAM_TOOLS)
        send_tool = next(
            tool
            for tool in tools
            if tool["function"]["name"] == "send_message_to_agent"
        )
        properties = send_tool["function"]["parameters"]["properties"]
        properties["agent_name"]["description"] = (
            "Existing agent name or stable #id to reuse, or a descriptive name for "
            "new work. Prefer a #id from agent cards or search results."
        )
        properties["create_new"] = {
            "type": "boolean",
            "description": (
                "Set true only to create a separate agent after reviewing a "
                "possible_duplicate result."
            ),
        }
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "search_agents",
                    "description": (
                        "Search all agent cards when the bounded working set does "
                        "not show the relevant prior work."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Work, person, topic, or reference to find.",
                            },
                            "limit": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 50,
                                "description": "Maximum results to return (default 8).",
                            },
                        },
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                },
            }
        )
        return tools
