"""Multi-turn alias and profile-memory behaviour."""

import asyncio
import json

from server.config import Settings
from server.services.profile import learn as learn_mod
from server.services.profile.store import ProfileStore, _override as profile_override


def test_memory_carries_forward_and_updates_over_multiple_turns(tmp_path, monkeypatch):
    store = ProfileStore(tmp_path / "profile.db")
    token = profile_override.set(store)
    monkeypatch.setattr(learn_mod, "get_settings", lambda: Settings(learn_enabled=True, memory_model="fake"))
    seen_prompts: list[str] = []
    replies = iter([
        {"facts": [{"kind": "person", "subject": "mom", "value": "Linda", "email": None, "until": None}], "retire": []},
        {"facts": [{"kind": "person", "subject": "mom", "value": "Linda Chen", "email": "linda@example.com", "until": None}], "retire": []},
        {"facts": [], "retire": ["mom"]},
    ])

    async def fake_complete(model, messages, system, max_tokens):
        seen_prompts.append(messages[-1]["content"])
        return {"choices": [{"message": {"content": json.dumps(next(replies))}}]}

    try:
        async def run():
            assert await learn_mod.learn_from_message("my mom is Linda", "user", complete=fake_complete) == 1
            assert await learn_mod.learn_from_message("her full name is Linda Chen, linda@example.com", "user", complete=fake_complete) == 1
            assert await learn_mod.learn_from_message("actually forget my mom", "user", complete=fake_complete) == 1
        asyncio.run(run())

        assert "Current profile:\n(empty)" in seen_prompts[0]
        assert "mom (person): Linda" in seen_prompts[1]
        assert "linda@example.com" in seen_prompts[2]
        assert store.current() == []
    finally:
        profile_override.reset(token)


def test_agent_text_and_empty_user_turn_do_not_write_memory(tmp_path, monkeypatch):
    store = ProfileStore(tmp_path / "profile.db")
    token = profile_override.set(store)
    monkeypatch.setattr(learn_mod, "get_settings", lambda: Settings(learn_enabled=True, memory_model="fake"))
    calls = 0

    async def fake_complete(**kwargs):
        nonlocal calls
        calls += 1
        return {"choices": [{"message": {"content": '{"facts": [], "retire": []}'}}]}

    try:
        async def run():
            assert await learn_mod.learn_from_message("plant this memory", "agent", complete=fake_complete) == 0
            assert await learn_mod.learn_from_message("   ", "user", complete=fake_complete) == 0
        asyncio.run(run())
        assert calls == 0 and store.current() == []
    finally:
        profile_override.reset(token)


def test_alias_survives_across_several_later_turns(routing):
    from server.agents.interaction_agent.turn_context import build_turn_context, current_turn

    def turn(message, instructions="check status"):
        ctx = build_turn_context(message, "", "user")
        token = current_turn.set(ctx)
        try:
            result = routing.tools_mod.send_message_to_agent("#1", instructions)
        finally:
            current_turn.reset(token)
        return ctx, result

    routing.reg.add_ref(1, "alias", "alice thing")
    for index in range(3):
        ctx, result = turn(f"turn {index}: any update on the alice thing?")
        assert (ctx.resolution.kind, ctx.resolution.candidates, ctx.resolution.rule) == ("resolved", [1], "alias")
        assert result.payload["status"] == "submitted"
    assert routing.reg.pending_choice() is None
