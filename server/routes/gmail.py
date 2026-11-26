from __future__ import annotations

import asyncio
import hmac
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from ..config import Settings, get_settings
from ..models import GmailConnectPayload, GmailDisconnectPayload, GmailStatusPayload
from ..services import disconnect_account, fetch_status, initiate_connect
from ..services.gmail.push import get_gmail_push_service

router = APIRouter(prefix="/gmail", tags=["gmail"])


@router.post("/connect")
# Initiate Gmail OAuth connection flow through Composio
async def gmail_connect(payload: GmailConnectPayload, settings: Settings = Depends(get_settings)) -> JSONResponse:
    return initiate_connect(payload, settings)


@router.post("/status")
# Check the current Gmail connection status and user information
async def gmail_status(payload: GmailStatusPayload) -> JSONResponse:
    return fetch_status(payload)


@router.post("/disconnect")
# Disconnect Gmail account and clear cached profile data
async def gmail_disconnect(payload: GmailDisconnectPayload) -> JSONResponse:
    return disconnect_account(payload)


@router.post("/push")
# Acknowledge a Gmail Pub/Sub push notification and process owned-thread replies.
async def gmail_pubsub_push(
    payload: Dict[str, Any] = Body(...),
    token: Optional[str] = Query(default=None),
    settings: Settings = Depends(get_settings),
) -> dict:
    expected = settings.gmail_push_shared_secret
    if expected and not hmac.compare_digest(token or "", expected):
        raise HTTPException(status_code=401, detail="Invalid push token")
    try:
        service = get_gmail_push_service()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    # Malformed or foreign-account notifications are acknowledged rather than retried forever;
    # unexpected Gmail/API failures remain 5xx so Pub/Sub retries them.
    return await service.handle_pubsub(payload)


@router.post("/push/watch")
# Register or renew Gmail's seven-day watch on the configured Pub/Sub topic.
async def gmail_push_watch(settings: Settings = Depends(get_settings)) -> dict:
    if not settings.gmail_push_topic:
        raise HTTPException(status_code=400, detail="OPENPOKE_GMAIL_PUBSUB_TOPIC is not configured")
    try:
        service = get_gmail_push_service()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        return await asyncio.to_thread(service.register_or_renew_watch, settings.gmail_push_topic)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
