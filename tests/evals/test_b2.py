from __future__ import annotations

from collections import Counter

from evals.schema import Gold, Scenario, Turn, World, WorldAgent


def _agent(
    agent_id: int,
    name: str,
    topic: str,
    note: str,
    last_active: str,
    refs: list[tuple[str, str]] | None = None,
) -> WorldAgent:
    return WorldAgent(
        id=agent_id,
        name=name,
        topic=topic,
        created_at="2026-11-01T09:00:00Z",
        last_active_at=last_active,
        state="waiting_on_user",
        state_note=note,
        refs=refs or [],
    )


def _world() -> World:
    agents = [
        _agent(
            1,
            "Email to Alice <Q3>",
            "Draft the Q3 deck email for Alice",
            "Q3 deck draft awaits approval",
            "2026-11-20T14:00:00Z",
            [("contact", "alice@acme.com"), ("draft", "d-q3")],
        ),
        _agent(
            2,
            "Tokyo ramen research",
            "Find a quiet ramen restaurant in Tokyo",
            "Shortlist includes the distinctive word yuzu",
            "2026-11-20T13:00:00Z",
            [("subject", "Tokyo dinner")],
        ),
        _agent(
            3,
            "Invoice follow-up",
            "Follow up with Bob about invoice 402",
            "Waiting for Bob's payment date",
            "2026-11-20T12:00:00Z",
            [("thread", "thread-402")],
        ),
        _agent(
            4,
            "Conference travel",
            "Arrange conference travel to Berlin",
            "Hotel options collected",
            "2026-11-19T12:00:00Z",
        ),
    ]
    return World(
        size=len(agents),
        seed=781,
        now="2026-11-20T15:00:00Z",
        contacts=[],
        agents=agents,
    )


def _scenario(
    message: str = "Please find the yuzu ramen shortlist",
    history: list[Turn] | None = None,
) -> Scenario:
    return Scenario(
        id="dev-b2-001",
        family=1,
        split="dev",
        history=history or [],
        message=message,
        event=None,
        gold=Gold(kind="agent", agent_id=2),
        irreversible=False,
    )


def _card_lines(prepared) -> list[str]:
    content = prepared.messages[0]["content"]
    agents_block = content.split("<agents>\n", 1)[1].split("\n</agents>", 1)[0]
    return [line for line in agents_block.splitlines() if line.startswith("#")]


def test_topk_contains_gold_for_needle(tmp_path):
    from evals.setups.b2_common import B2Common

    prepared = B2Common(k=1, recent=0, embeddings=False).prepare(
        _world(), _scenario(), tmp_path
    )

    assert _card_lines(prepared) == [
        "#2 Tokyo ramen research · Shortlist includes the distinctive word yuzu · last active 2h ago"
    ]


def test_cards_bounded(tmp_path):
    from evals.setups.b2_common import B2Common

    prepared = B2Common(k=2, recent=1, embeddings=False).prepare(
        _world(), _scenario(), tmp_path
    )

    assert len(_card_lines(prepared)) <= 3
    assert len(_card_lines(prepared)) == len(set(_card_lines(prepared)))


def test_message_layout_matches_upstream(tmp_path):
    from evals.harness.transcript import render_history
    from evals.setups.b2_common import B2Common

    history = [
        Turn(
            role="user",
            text="Find dinner in Tokyo",
            ts="2026-11-20T12:00:00Z",
        ),
        Turn(
            role="poke",
            text="I am checking.",
            ts="2026-11-20T12:00:01Z",
        ),
    ]
    scenario = _scenario("Show me the yuzu option", history)
    prepared = B2Common(k=1, recent=0, embeddings=False).prepare(
        _world(), scenario, tmp_path
    )
    expected = (
        f"<conversation_history>\n{render_history(history, _world())}\n</conversation_history>\n\n"
        "<agents>\n"
        "#2 Tokyo ramen research · Shortlist includes the distinctive word yuzu · last active 2h ago\n"
        "</agents>\n\n"
        "<new_user_message>\nShow me the yuzu option\n</new_user_message>"
    )

    assert prepared.messages == [{"role": "user", "content": expected}]
    assert prepared.prompt_chars == len(prepared.system_prompt) + len(expected)


def test_search_tool_returns_cards(tmp_path):
    from evals.setups.b2_common import B2Common

    prepared = B2Common(embeddings=False).prepare(_world(), _scenario(), tmp_path)

    result = prepared.handle_tool("search_agents", {"query": "invoice 402", "limit": 1})

    assert result == {
        "agents": [
            "#3 Invoice follow-up · Waiting for Bob&#x27;s payment date · last active 3h ago"
        ]
    }


def test_duplicate_guard_triggers_and_create_new_bypasses(tmp_path):
    from evals.setups.b2_common import B2Common

    prepared = B2Common(embeddings=False).prepare(_world(), _scenario(), tmp_path)
    args = {
        "agent_name": "Alice quarterly deck email",
        "instructions": "Draft the Q3 deck email for Alice and await approval",
    }

    guarded = prepared.handle_tool("send_message_to_agent", args)
    bypassed = prepared.handle_tool(
        "send_message_to_agent", {**args, "create_new": True}
    )

    assert guarded["status"] == "possible_duplicate"
    assert guarded["candidates"][0].startswith("#1 Email to Alice")
    assert bypassed == {"status": "submitted", "new_agent_created": True}


def test_hash_id_resolves(tmp_path):
    from evals.setups.b2_common import B2Common

    prepared = B2Common(embeddings=False).prepare(_world(), _scenario(), tmp_path)

    assert prepared.resolve_target("#3") == 3
    assert prepared.resolve_target("#999") is None
    assert prepared.resolve_target("TOKYO RAMEN RESEARCH") == 2
    assert prepared.handle_tool(
        "send_message_to_agent",
        {"agent_name": "#3", "instructions": "Check for a reply"},
    ) == {"status": "submitted", "agent": "#3 Invoice follow-up"}


def test_embedding_cache_used(tmp_path, monkeypatch):
    import evals.setups.b2_common as b2_module
    from evals.setups.b2_common import B2Common

    cache_dir = tmp_path / "embedding-cache"
    monkeypatch.setattr(b2_module, "_EMBEDDING_CACHE_DIR", cache_dir)
    seen: list[str] = []

    def fake_embedder(texts: list[str]) -> list[list[float]]:
        seen.extend(texts)
        return [
            [float(len(text)), float(sum(map(ord, text)) % 101), 1.0]
            for text in texts
        ]

    setup = B2Common(embeddings=True, embedder=fake_embedder)
    world = _world()
    scenario = _scenario()
    setup.prepare(world, scenario, tmp_path)
    first_seen = list(seen)
    setup.prepare(world, scenario, tmp_path)

    assert first_seen
    assert seen == first_seen
    assert Counter(seen).most_common(1)[0][1] == 1
    assert len(list(cache_dir.glob("*.json"))) == len(set(seen))
