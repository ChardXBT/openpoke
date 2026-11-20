"""Endpoints for demos and for seeing what Poke is tracking.

- ``GET /api/v1/agents/open``: the open items and recent agents Poke sees each turn.
- ``GET /api/v1/me``: what Poke has learned about the user (``DELETE /api/v1/me/{subject}`` forgets).
- Demo mailbox only (``OPENPOKE_FAKE_GMAIL=1``): ``POST /api/v1/demo/email`` delivers an incoming
  email and runs the important-email watcher now; ``GET /api/v1/demo/mailbox`` shows the outbox and
  drafts; ``POST /api/v1/demo/reset`` re-seeds the mailbox.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import get_settings
from ..services.execution.registry import get_agent_registry
from ..services.execution.working_set import STATE_LABELS
from ..services.profile.store import get_profile_store

router = APIRouter(tags=["demo"])


@router.get("/agents/open")
def open_agents() -> dict:
    reg = get_agent_registry()

    def card(rec) -> dict:
        return {"id": rec.id, "name": rec.name, "state": rec.state, "label": STATE_LABELS.get(rec.state, rec.state),
                "note": rec.state_note or rec.topic, "last_active_at": rec.last_active_at}

    open_items = reg.open_items(25)
    return {"open": [card(r) for r in open_items],
            "recent": [card(r) for r in reg.recent(5, exclude={r.id for r in open_items}, only_closed=True)],
            "total": reg.count()}


@router.get("/me")
def about_me() -> dict:
    return {"facts": [{"kind": f.kind, "subject": f.subject, "value": f.value, "email": f.email, "since": f.created_at}
                      for f in get_profile_store().current()]}


@router.delete("/me/{subject}")
def forget_me(subject: str) -> dict:
    return {"deleted": get_profile_store().forget(subject)}


class IncomingEmail(BaseModel):
    sender: str
    subject: str
    body: str
    thread_id: str | None = None


def _require_demo() -> None:
    if not get_settings().fake_gmail:
        raise HTTPException(status_code=404, detail="Demo mailbox is off (set OPENPOKE_FAKE_GMAIL=1).")


@router.post("/demo/email")
async def deliver_email(payload: IncomingEmail) -> dict:
    _require_demo()
    from ..services.gmail.demo_mailbox import get_demo_mailbox
    from ..services.gmail.importance_watcher import get_important_email_watcher

    message = get_demo_mailbox().inject(payload.sender, payload.subject, payload.body, payload.thread_id)
    asyncio.get_running_loop().create_task(get_important_email_watcher()._poll_once())  # check the inbox now
    return {"delivered": message["messageId"], "thread_id": message["threadId"]}


@router.get("/demo/mailbox")
def mailbox() -> dict:
    _require_demo()
    from ..services.gmail.demo_mailbox import get_demo_mailbox

    box = get_demo_mailbox()
    return {"outbox": box.outbox(), "drafts": list(box.drafts().values()),
            "inbox": [{"from": m["sender"], "subject": m["subject"], "thread_id": m["threadId"], "at": m["messageTimestamp"]}
                      for m in box.messages() if "INBOX" in m["labelIds"]][:20]}


@router.post("/demo/reset")
def reset_mailbox() -> dict:
    _require_demo()
    from ..services.gmail.demo_mailbox import get_demo_mailbox

    get_demo_mailbox().reset()
    return {"ok": True}
