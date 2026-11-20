"""Learn lasting facts about the user from what the user says.

Runs after a user turn, in the background, on a cheap model. Only the user's own messages
are read: email notifications and agent results are untrusted text, and letting them write
"memories" would let anyone who emails the user plant instructions.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ...config import get_settings
from ...logging_config import logger
from ...openrouter_client import request_chat_completion
from .store import KINDS, ProfileStore, get_profile_store

EXTRACT_SYSTEM = """You keep a small private profile of ONE person: the user of a personal assistant.
From the user's message, extract only LASTING facts about the user themself:
- people in their life, with the relationship as the subject ("mom", "boss", "dentist", "partner"),
  their name as the value, and their email if the message gives it;
- stable preferences (food, drinks, how they like emails written, how they like to be texted);
- places (home city, office, gym), routines (gym Mon/Wed/Fri 7am), and personal facts (job, school, birthday).
Ignore one-off requests, questions, plans for today, and facts about other people that are not their
relationship to the user. Never invent anything. Reply with JSON only:
{"facts": [{"kind": "person|preference|place|routine|fact", "subject": "...", "value": "...", "email": "...or null"}]}
or {"facts": []} when there is nothing lasting."""

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.S)


def apply_extraction(store: ProfileStore, reply: str) -> int:
    """Write the facts in a model reply; returns how many were added or updated."""
    text = _FENCE.sub("", (reply or "").strip())
    try:
        data: Any = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return 0
    facts = data.get("facts") if isinstance(data, dict) else None
    if not isinstance(facts, list):
        return 0
    written = 0
    for item in facts[:10]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "fact")
        action = store.remember(
            kind if kind in KINDS else "fact",
            str(item.get("subject") or ""),
            str(item.get("value") or ""),
            email=(str(item["email"]) if item.get("email") else None),
            source="learned",
        )
        written += action in ("add", "update")
    return written


async def learn_from_message(message: str, message_type: str) -> int:
    """Extract and store lasting facts from a user message (never from agent or email text)."""
    settings = get_settings()
    if message_type != "user" or not settings.learn_enabled or not (message or "").strip():
        return 0
    try:
        response = await request_chat_completion(
            model=settings.memory_model,
            messages=[{"role": "user", "content": f"User message:\n{message.strip()[:2000]}"}],
            system=EXTRACT_SYSTEM,
            max_tokens=400,
        )
        reply = ((response.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        written = apply_extraction(get_profile_store(), reply)
        if written:
            logger.info(f"profile: learned {written} fact(s) from the user's message")
        return written
    except Exception as exc:  # learning must never break a turn
        logger.debug(f"profile: learning skipped: {exc}")
        return 0
