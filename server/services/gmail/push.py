"""Gmail Pub/Sub push handling.

This module is deliberately small and injectable: the offline tests use a fake Gmail API,
while production uses Composio's authenticated proxy. Push is a fast path for replies on
threads Poke already owns; the polling watcher remains the fallback and owns importance
classification for unrelated mail.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import quote

from .processing import build_processed_email
from ...logging_config import logger


class GmailApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def parse_history_id(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("history id must be a positive integer")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and value.isdigit():
        number = int(value)
    else:
        raise ValueError("history id must be a positive integer")
    if number <= 0:
        raise ValueError("history id must be positive")
    return number


def _parse_epoch_ms(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("watch expiration must be epoch milliseconds")
    try:
        number = int(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("watch expiration must be epoch milliseconds") from exc
    if number <= 0:
        raise ValueError("watch expiration must be positive")
    return number


class PushStateStore:
    """Small durable state file for watch registration, history id and processed message ids."""

    def __init__(self, path: Path | str, processed_limit: int = 500) -> None:
        self.path = Path(path)
        self.processed_limit = processed_limit
        self._lock = threading.RLock()
        self._state: dict[str, Any] = {"processed_message_ids": []}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._state.update(loaded)
            except (OSError, ValueError) as exc:
                logger.warning("gmail push state unreadable; starting empty", extra={"error": str(exc)})

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._state, separators=(",", ":"), sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    @property
    def last_history_id(self) -> Optional[int]:
        try:
            value = self._state.get("history_id")
            return parse_history_id(value) if value is not None else None
        except ValueError:
            return None

    @property
    def email_address(self) -> Optional[str]:
        value = self._state.get("email_address")
        return value.casefold() if isinstance(value, str) and value else None

    @property
    def watch_expiration_ms(self) -> Optional[int]:
        try:
            value = self._state.get("watch_expiration_ms")
            return _parse_epoch_ms(value) if value is not None else None
        except ValueError:
            return None

    @property
    def watch_topic(self) -> Optional[str]:
        value = self._state.get("watch_topic")
        return value.strip() if isinstance(value, str) and value.strip() else None

    @property
    def processed_ids(self) -> set[str]:
        values = self._state.get("processed_message_ids")
        return {str(v) for v in values} if isinstance(values, list) else set()

    def set_history(self, history_id: int, email_address: str) -> None:
        number = parse_history_id(history_id)
        email = (email_address or "").strip().casefold()
        if not email:
            raise ValueError("email address is required")
        with self._lock:
            self._state["history_id"] = str(number)
            self._state["email_address"] = email
            self._write()

    def update_watch(
        self,
        history_id: int,
        expiration_ms: int,
        topic_name: str,
        email_address: str,
        *,
        preserve_existing_history: bool = False,
    ) -> None:
        number = parse_history_id(history_id)
        expiration = _parse_epoch_ms(expiration_ms)
        if not topic_name.strip():
            raise ValueError("topic name is required")
        email = (email_address or "").strip().casefold()
        if not email:
            raise ValueError("email address is required")
        with self._lock:
            updates = {
                "watch_expiration_ms": str(expiration),
                "watch_topic": topic_name,
                "email_address": email,
            }
            # When renewing, the response is the mailbox's current id, not a cursor whose
            # intervening messages have been consumed. Keep the existing cursor so the next
            # notification lists those messages; only seed it when there is no usable cursor yet.
            if not preserve_existing_history or self.last_history_id is None:
                updates["history_id"] = str(number)
            self._state.update(updates)
            self._write()

    def mark_processed(self, message_ids: list[str] | set[str] | tuple[str, ...]) -> None:
        clean = [str(x) for x in message_ids if str(x)]
        if not clean:
            return
        with self._lock:
            existing = [str(x) for x in self._state.get("processed_message_ids", []) if str(x)]
            seen = set(existing)
            for message_id in clean:
                if message_id not in seen:
                    existing.append(message_id)
                    seen.add(message_id)
            self._state["processed_message_ids"] = existing[-self.processed_limit:]
            self._write()


def decode_pubsub_notification(payload: Any) -> tuple[Optional[str], Optional[int]]:
    """Return ``(email, history_id)`` or ``(None, None)`` for a malformed envelope."""
    try:
        if not isinstance(payload, dict) or not isinstance(payload.get("message"), dict):
            return None, None
        encoded = payload["message"].get("data")
        if not isinstance(encoded, str) or not encoded:
            return None, None
        padded = encoded + "=" * (-len(encoded) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        data = json.loads(decoded.decode("utf-8"))
        if not isinstance(data, dict):
            return None, None
        email = str(data.get("emailAddress") or "").strip().casefold()
        history_id = parse_history_id(data.get("historyId"))
        if not email or "@" not in email:
            return None, None
        return email, history_id
    except (UnicodeEncodeError, UnicodeDecodeError, binascii.Error, ValueError, TypeError, json.JSONDecodeError):
        return None, None


def _header(payload: dict[str, Any], name: str) -> str:
    for entry in payload.get("headers") or []:
        if isinstance(entry, dict) and str(entry.get("name") or "").casefold() == name:
            return str(entry.get("value") or "")
    return ""


def normalize_api_message(raw: dict[str, Any]) -> dict[str, Any]:
    """Translate the Gmail API's message shape into the repo's existing parser input."""
    payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
    try:
        millis = int(str(raw.get("internalDate") or "0"))
    except (TypeError, ValueError):
        millis = 0
    try:
        timestamp = datetime.fromtimestamp(max(0, millis) / 1000, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        timestamp = datetime.now(timezone.utc).isoformat()
    return {
        "messageId": str(raw.get("id") or ""),
        "threadId": str(raw.get("threadId") or ""),
        "messageTimestamp": timestamp,
        "subject": _header(payload, "subject") or "No Subject",
        "sender": _header(payload, "from") or "Unknown Sender",
        "to": _header(payload, "to") or "Unknown Recipient",
        "labelIds": list(raw.get("labelIds") or []),
        "payload": payload,
        "textBody": str(raw.get("snippet") or ""),
        "attachmentList": [],
    }


def _summary_for(email) -> str:
    text = " ".join((email.clean_text or "").split())[:240]
    subject = email.subject or "No Subject"
    sender = email.sender or "Unknown Sender"
    return f"{sender} wrote ({subject}): {text or '(no readable body)'}"


def _extract_address(text: str) -> str:
    match = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", text or "")
    return match.group(0).casefold() if match else ""


class GmailPushService:
    """Consume one Pub/Sub notification at a time and dispatch owned-thread replies."""

    def __init__(
        self,
        gateway: Any,
        store: PushStateStore,
        *,
        dispatcher: Optional[Callable[[str, Any], Any]] = None,
        registry: Any = None,
        seen_store: Any = None,
    ) -> None:
        self.gateway = gateway
        self.store = store
        self._dispatcher = dispatcher or self._default_dispatch
        self._registry = registry
        self._seen_store = seen_store
        self._lock = asyncio.Lock()

    async def _default_dispatch(self, summary: str, email) -> None:
        from .importance_watcher import get_important_email_watcher

        await get_important_email_watcher()._dispatch_summary(summary, email)

    def _registry_obj(self):
        if self._registry is not None:
            return self._registry() if callable(self._registry) else self._registry
        from ..execution.registry import get_agent_registry

        return get_agent_registry()

    def _thread_owned(self, thread_id: str) -> bool:
        registry = self._registry_obj()
        if registry is None or not thread_id:
            return False
        return bool(registry.owners("thread", thread_id))

    def _profile_email(self) -> Optional[str]:
        if not hasattr(self.gateway, "get_profile"):
            return None
        profile = self.gateway.get_profile()
        if not isinstance(profile, dict):
            raise GmailApiError("Gmail profile response was not an object")
        email = str(profile.get("emailAddress") or "").strip().casefold()
        if not email:
            raise GmailApiError("Gmail profile response did not include emailAddress")
        return email

    def _seen_contains(self, message_id: str) -> bool:
        store = self._seen_store
        if store is None:
            return False
        try:
            if hasattr(store, "is_seen"):
                return bool(store.is_seen(message_id))
            return message_id in store
        except Exception:
            return False

    def _mark_seen(self, message_id: str) -> None:
        store = self._seen_store
        if store is None:
            return
        try:
            if hasattr(store, "mark_seen"):
                store.mark_seen([message_id])
            elif hasattr(store, "add"):
                store.add(message_id)
        except Exception:
            logger.debug("gmail push: could not mark watcher seen state", extra={"message_id": message_id})

    def _added_messages(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ordered = sorted(entries, key=lambda item: parse_history_id(item.get("id", "1")) if str(item.get("id", "")).isdigit() else 1)
        messages: dict[str, dict[str, Any]] = {}
        for entry in ordered:
            for added in entry.get("messagesAdded") or []:
                if not isinstance(added, dict) or not isinstance(added.get("message"), dict):
                    continue
                message = added["message"]
                message_id = str(message.get("id") or "")
                if message_id:
                    messages[message_id] = message
        return list(messages.values())

    async def _collect_history(self, start_history_id: int) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        page_token = None
        for _page in range(100):
            response = await asyncio.to_thread(self.gateway.list_history, start_history_id, page_token=page_token)
            if not isinstance(response, dict):
                raise GmailApiError("Gmail history response was not an object")
            entries.extend(x for x in response.get("history") or [] if isinstance(x, dict))
            page_token = response.get("nextPageToken") or None
            if not page_token:
                return entries
        raise GmailApiError("Gmail history pagination exceeded 100 pages")

    async def handle_pubsub(self, payload: Any) -> dict[str, Any]:
        async with self._lock:
            email_address, history_id = decode_pubsub_notification(payload)
            if email_address is None or history_id is None:
                return {"status": "invalid"}
            known_email = self.store.email_address
            if known_email and email_address != known_email:
                return {"status": "invalid", "reason": "wrong_account"}
            if not known_email and hasattr(self.gateway, "get_profile"):
                profile_email = await asyncio.to_thread(self._profile_email)
                if profile_email and email_address != profile_email:
                    return {"status": "invalid", "reason": "wrong_account"}

            last = self.store.last_history_id
            if last is None:
                self.store.set_history(history_id, email_address)
                return {"status": "seeded", "history_id": history_id}
            if history_id == last:
                return {"status": "duplicate", "history_id": history_id}
            if history_id < last:
                return {"status": "stale", "history_id": history_id, "last_history_id": last}

            try:
                entries = await self._collect_history(last)
            except GmailApiError as exc:
                if exc.status_code == 404:
                    self.store.set_history(history_id, email_address)
                    return {"status": "resynced", "history_id": history_id}
                raise

            message_count = 0
            dispatch_count = 0
            for added in self._added_messages(entries):
                message_id = str(added.get("id") or "")
                if not message_id or message_id in self.store.processed_ids:
                    continue
                labels = {str(x).upper() for x in added.get("labelIds") or []}
                message_count += 1
                thread_id = str(added.get("threadId") or "")
                owned_inbox = "INBOX" in labels and self._thread_owned(thread_id) and not self._seen_contains(message_id)
                if not owned_inbox:
                    # Ignored additions are still consumed by this cursor, while the polling watcher
                    # remains responsible for classifying unrelated inbox mail.
                    self.store.mark_processed([message_id])
                    continue
                try:
                    raw = await asyncio.to_thread(self.gateway.get_message, message_id)
                except GmailApiError as exc:
                    if exc.status_code != 404:
                        raise
                    self.store.mark_processed([message_id])
                    continue
                email = build_processed_email(normalize_api_message(raw), query="gmail-push")
                if email is None:
                    self.store.mark_processed([message_id])
                    continue
                sender_address = _extract_address(email.sender)
                if sender_address and sender_address == email_address:
                    self.store.mark_processed([message_id])
                    continue
                # Mark before dispatch: once the message is in the dispatch path, Pub/Sub retries
                # must not surface it twice. A failure while fetching/parsing above is retried.
                self.store.mark_processed([message_id])
                self._mark_seen(message_id)
                await self._dispatcher(_summary_for(email), email)
                dispatch_count += 1

            self.store.set_history(history_id, email_address)
            return {
                "status": "processed",
                "history_id": history_id,
                "messages": message_count,
                "dispatched": dispatch_count,
            }

    def register_or_renew_watch(
        self,
        topic_name: str,
        *,
        now_ms: Optional[int] = None,
        renew_before_ms: int = 24 * 60 * 60 * 1000,
    ) -> dict[str, Any]:
        topic = (topic_name or "").strip()
        if not topic:
            raise ValueError("topic name is required")
        now = int(now_ms if now_ms is not None else datetime.now(timezone.utc).timestamp() * 1000)
        current_expiration = self.store.watch_expiration_ms
        current = (
            current_expiration is not None
            and current_expiration - now > renew_before_ms
            and self.store.watch_topic == topic
        )
        if current:
            return {"action": "current", "expiration_ms": current_expiration}

        email = self.store.email_address
        if not email and hasattr(self.gateway, "get_profile"):
            try:
                email = self._profile_email()
            except GmailApiError as exc:
                raise ValueError(str(exc)) from exc
        if not email:
            raise ValueError("Gmail push state has no account email; process a notification seed first")

        response = self.gateway.watch(topic, ["INBOX"])
        if not isinstance(response, dict):
            raise ValueError("Gmail watch response was not an object")
        history_id = parse_history_id(response.get("historyId"))
        expiration = _parse_epoch_ms(response.get("expiration"))
        if expiration <= now:
            raise ValueError("Gmail watch expiration is already in the past")
        self.store.update_watch(history_id, expiration, topic, email, preserve_existing_history=True)
        return {
            "action": "renewed" if current_expiration is not None else "registered",
            "history_id": history_id,
            "expiration_ms": expiration,
        }


_GMAIL_API_ROOT = "https://gmail.googleapis.com/gmail/v1/users/me"


class ComposioGmailProxyGateway:
    """Raw Gmail API through Composio's connected-account proxy."""

    def __init__(self, user_id: str, connected_account_id: Optional[str] = None) -> None:
        self.user_id = user_id
        self.connected_account_id = connected_account_id

    def _connected_account_id(self) -> Optional[str]:
        if self.connected_account_id:
            return self.connected_account_id
        try:
            from .client import _get_composio_client

            client = _get_composio_client()
            items = client.connected_accounts.list(user_ids=[self.user_id], toolkit_slugs=["GMAIL"], statuses=["ACTIVE"])
            data = getattr(items, "data", None)
            if data is None and isinstance(items, dict):
                data = items.get("data")
            if data is None and isinstance(items, dict):
                data = items.get("items")
            if data is None and not isinstance(items, dict):
                data = getattr(items, "items", None)
            if data:
                first = data[0]
                self.connected_account_id = getattr(first, "id", None) or (first.get("id") if isinstance(first, dict) else None)
        except Exception as exc:
            logger.debug("gmail push: connected account lookup failed", extra={"error": str(exc)})
        return self.connected_account_id

    def _proxy(self, method: str, endpoint: str, *, body: Any = None, query: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        from .client import _get_composio_client, _normalize_tool_response

        parameters = [
            {"name": key, "value": str(value), "type": "query"}
            for key, value in (query or {}).items()
            if value is not None
        ]
        kwargs: dict[str, Any] = {
            "endpoint": endpoint,
            "method": method,
            "body": body,
            "parameters": parameters,
            "user_id": self.user_id,
        }
        account_id = self._connected_account_id()
        if account_id:
            kwargs["connected_account_id"] = account_id
        result = _get_composio_client().tools.proxy(**kwargs)
        payload = _normalize_tool_response(result)
        status_code = int(payload.get("status") or payload.get("status_code") or 200)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        if status_code >= 400:
            raise GmailApiError(f"Gmail API {method} {endpoint} failed with HTTP {status_code}", status_code=status_code)
        return data if isinstance(data, dict) else {}

    def watch(self, topic_name: str, label_ids: list[str]) -> dict[str, Any]:
        return self._proxy("POST", f"{_GMAIL_API_ROOT}/watch", body={
            "topicName": topic_name,
            "labelIds": label_ids,
            "labelFilterBehavior": "include",
        })

    def get_profile(self) -> dict[str, Any]:
        return self._proxy("GET", f"{_GMAIL_API_ROOT}/profile")

    def list_history(self, start_history_id: int, page_token: Optional[str] = None) -> dict[str, Any]:
        return self._proxy("GET", f"{_GMAIL_API_ROOT}/history", query={
            "startHistoryId": str(start_history_id),
            "maxResults": "500",
            "pageToken": page_token,
        })

    def get_message(self, message_id: str) -> dict[str, Any]:
        return self._proxy("GET", f"{_GMAIL_API_ROOT}/messages/{quote(message_id, safe='')}", query={"format": "full"})


class GmailPushWatchRenewer:
    """Background loop that keeps the seven-day Gmail watch registration alive."""

    def __init__(
        self,
        topic_name: str,
        *,
        service_factory: Optional[Callable[[], GmailPushService]] = None,
        interval_seconds: float = 3600.0,
        renew_before_ms: int = 24 * 60 * 60 * 1000,
    ) -> None:
        if service_factory is None:
            service_factory = get_gmail_push_service
        self.topic_name = topic_name
        self._service_factory = service_factory
        self._interval_seconds = interval_seconds
        self._renew_before_ms = renew_before_ms
        self._task: Optional[asyncio.Task[None]] = None
        self._running = False
        self._lock = asyncio.Lock()

    async def check_once(self) -> dict[str, Any]:
        service = self._service_factory()
        return await asyncio.to_thread(service.register_or_renew_watch, self.topic_name, renew_before_ms=self._renew_before_ms)

    async def start(self) -> None:
        async with self._lock:
            if self._task and not self._task.done():
                return
            self._running = True
            self._task = asyncio.get_running_loop().create_task(self._run(), name="gmail-push-watch-renewer")

    async def stop(self) -> None:
        async with self._lock:
            self._running = False
            if self._task:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
                self._task = None

    async def _run(self) -> None:
        try:
            while self._running:
                try:
                    result = await self.check_once()
                    logger.info("gmail push watch registration checked", extra={"action": result.get("action")})
                except Exception as exc:
                    logger.warning("gmail push watch registration check failed", extra={"error": str(exc)})
                await asyncio.sleep(self._interval_seconds)
        except asyncio.CancelledError:
            raise


_renewer_lock = threading.Lock()
_renewer: Optional[GmailPushWatchRenewer] = None


def get_gmail_push_watch_renewer() -> GmailPushWatchRenewer:
    global _renewer
    from ...config import get_settings

    topic = get_settings().gmail_push_topic
    if not topic:
        raise RuntimeError("OPENPOKE_GMAIL_PUBSUB_TOPIC is not configured")
    with _renewer_lock:
        if _renewer is None or _renewer.topic_name != topic:
            _renewer = GmailPushWatchRenewer(topic)
        return _renewer


_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_DEFAULT_PUSH_PATH = _DATA_DIR / "gmail_push_state.json"
_service_lock = threading.Lock()
_service: Optional[GmailPushService] = None


def build_default_push_service() -> GmailPushService:
    from .client import get_active_gmail_user_id
    from .importance_watcher import get_important_email_watcher

    from ...config import get_settings

    if get_settings().fake_gmail:
        raise RuntimeError("Gmail push requires a real Gmail connection, not the demo mailbox")
    user_id = get_active_gmail_user_id()
    if not user_id:
        raise RuntimeError("Gmail is not connected")
    watcher = get_important_email_watcher()
    return GmailPushService(
        ComposioGmailProxyGateway(user_id),
        PushStateStore(_DEFAULT_PUSH_PATH),
        seen_store=watcher._seen_store,
    )


def get_gmail_push_service() -> GmailPushService:
    global _service
    if _service is not None:
        return _service
    with _service_lock:
        if _service is None:
            _service = build_default_push_service()
    return _service


def set_gmail_push_service(service: Optional[GmailPushService]) -> None:
    global _service
    with _service_lock:
        _service = service


__all__ = [
    "ComposioGmailProxyGateway",
    "GmailApiError",
    "GmailPushService",
    "GmailPushWatchRenewer",
    "PushStateStore",
    "build_default_push_service",
    "decode_pubsub_notification",
    "get_gmail_push_service",
    "get_gmail_push_watch_renewer",
    "normalize_api_message",
    "parse_history_id",
    "set_gmail_push_service",
]
