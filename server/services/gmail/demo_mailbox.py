"""A local, fake Gmail for demos and trying OpenPoke without connecting real email.

Enabled with ``OPENPOKE_FAKE_GMAIL=1``. Every Gmail tool OpenPoke uses is answered from a
small JSON mailbox (seeded with a few realistic threads) in the same shape Composio
returns, so execution agents, the email search task and the important-email watcher all
work unchanged. Nothing is ever sent anywhere: "sent" mail lands in an outbox you can read.
"""

from __future__ import annotations

import base64
import json
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

ME = "you@demo.local"

_SEED = [
    # (thread, sender, subject, body, hours_ago)
    ("thr_alice_deck", "Alice Chen <alice.chen@acme.com>", "Q3 deck - comments by Friday?",
     "Hi! Could you send your comments on the Q3 deck by Friday? Slide 4 still needs numbers.", 30),
    ("thr_alice_lunch", "Alice Chen <alice.chen@acme.com>", "Lunch Thursday?",
     "Want to grab lunch Thursday? I'm free 12-2.", 26),
    ("thr_bob_402", "Bob Stone <bob@northwind.com>", "Invoice #402",
     "Attached is invoice #402 for the October work. Payment terms are net 15.", 50),
    ("thr_aircanada", "Air Canada Refunds <refunds@aircanada.example>", "Your refund request AC8812",
     "We received your refund request AC8812 for the cancelled YYZ-LIS flight. Expect 7-10 business days.", 72),
    ("thr_landlord", "Priya Landlord <priya@rentals.example>", "Lease renewal",
     "Your lease ends Jan 31. Let me know by Dec 15 if you'd like to renew for another year.", 96),
]

_QUERY_LABEL = re.compile(r"\blabel:(\S+)")
_QUERY_NEWER = re.compile(r"\bnewer_than:(\d+)([mhd])")
_QUERY_FROM = re.compile(r"\bfrom:(\S+)")
_WORD = re.compile(r"[0-9a-z#]+")


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _address(sender: str) -> str:
    match = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", sender or "")
    return match.group(0).casefold() if match else (sender or "").casefold()


class DemoMailbox:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()
        self._state: dict[str, Any] = {}
        if self._path.exists():
            try:
                self._state = json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                self._state = {}
        if not self._state.get("messages"):
            self._seed()

    # ------------------------------------------------------------------ storage
    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._state, indent=1), encoding="utf-8")

    def _next(self, prefix: str) -> str:
        self._state["seq"] = int(self._state.get("seq", 1000)) + 1
        return f"{prefix}{self._state['seq']:x}"

    def _message(self, thread: str, sender: str, to: str, subject: str, body: str, when: datetime,
                 labels: list[str]) -> dict:
        return {
            "messageId": self._next("msg_"), "threadId": thread, "messageTimestamp": _iso(when),
            "subject": subject, "sender": sender, "to": to, "labelIds": labels,
            "messageText": body, "preview": {"body": body[:120]},
            "payload": {"body": {"data": base64.urlsafe_b64encode(body.encode()).decode()}},
        }

    def _seed(self) -> None:
        now = datetime.now(timezone.utc)
        self._state = {"me": ME, "seq": 1000, "messages": [], "drafts": {}, "outbox": []}
        for thread, sender, subject, body, hours in _SEED:
            self._state["messages"].append(
                self._message(thread, sender, ME, subject, body, now - timedelta(hours=hours), ["INBOX"]))
        self._save()

    # ------------------------------------------------------------------ views
    def messages(self) -> list[dict]:
        return sorted(self._state["messages"], key=lambda m: m["messageTimestamp"], reverse=True)

    def outbox(self) -> list[dict]:
        return list(self._state.get("outbox", []))

    def drafts(self) -> dict:
        return dict(self._state.get("drafts", {}))

    # ------------------------------------------------------------------ demo controls
    def inject(self, sender: str, subject: str, body: str, thread_id: Optional[str] = None,
               reply_to_last: bool = False) -> dict:
        """Deliver an incoming email (for showing the watcher and reply routing).

        ``reply_to_last``: when you have emailed this sender, the email is their reply on the thread
        of the last one you sent them (subject "Re: ..."), the way a real reply arrives.
        """
        with self._lock:
            if reply_to_last and not thread_id:
                sent = [m for m in self.messages() if "SENT" in m["labelIds"] and _address(m["to"]) == _address(sender)]
                if sent:
                    thread_id = sent[0]["threadId"]
                    subject = "Re: " + re.sub(r"^(re|fwd?):\s*", "", sent[0]["subject"], flags=re.I)
            if not thread_id:
                base = re.sub(r"^(re|fwd?):\s*", "", subject or "", flags=re.I).strip().casefold()
                thread_id = next((m["threadId"] for m in self._state["messages"]
                                  if m["subject"].strip().casefold() == base
                                  or re.sub(r"^(re|fwd?):\s*", "", m["subject"], flags=re.I).casefold() == base),
                                 None) or self._next("thr_")
            msg = self._message(thread_id, sender, ME, subject, body, datetime.now(timezone.utc),
                                ["INBOX", "UNREAD", "IMPORTANT"])
            self._state["messages"].append(msg)
            self._save()
            return msg

    def reset(self) -> None:
        with self._lock:
            self._seed()

    # ------------------------------------------------------------------ Gmail tools
    def execute(self, tool: str, args: dict) -> dict:
        with self._lock:
            handler = getattr(self, "_t_" + tool.removeprefix("GMAIL_").lower(), None)
            if handler is None:
                return {"successful": False, "error": f"{tool} is not available in the demo mailbox"}
            return handler(args or {})

    def _t_fetch_emails(self, args: dict) -> dict:
        query = str(args.get("query") or "")
        limit = int(args.get("max_results") or 20)
        msgs = self.messages()
        label = _QUERY_LABEL.search(query)
        if label:
            msgs = [m for m in msgs if label.group(1).upper() in m["labelIds"]]
        newer = _QUERY_NEWER.search(query)
        if newer:
            unit = {"m": 60, "h": 3600, "d": 86400}[newer.group(2)]
            cutoff = _iso(datetime.now(timezone.utc) - timedelta(seconds=int(newer.group(1)) * unit))
            msgs = [m for m in msgs if m["messageTimestamp"] >= cutoff]
        sender = _QUERY_FROM.search(query)
        if sender:
            msgs = [m for m in msgs if sender.group(1).casefold() in m["sender"].casefold()]
        words = [w for w in _WORD.findall(_QUERY_FROM.sub(" ", _QUERY_LABEL.sub(" ", _QUERY_NEWER.sub(" ", query.casefold()))))
                 if len(w) > 2 and w not in {"and", "the", "from", "subject"}]
        if words:
            msgs = [m for m in msgs if any(w in f"{m['subject']} {m['messageText']} {m['sender']}".casefold() for w in words)]
        return {"successful": True, "data": {"messages": msgs[:limit]}}

    def _t_create_email_draft(self, args: dict) -> dict:
        draft_id = self._next("dr_")
        thread = args.get("thread_id") or self._next("thr_")
        self._state["drafts"][draft_id] = {
            "id": draft_id, "to": _address(args.get("recipient_email", "")), "subject": args.get("subject", ""),
            "body": args.get("body", ""), "threadId": thread,
        }
        self._save()
        return {"successful": True, "data": {"id": draft_id, "message": {"threadId": thread}}}

    def _send(self, to: str, subject: str, body: str, thread: str) -> dict:
        msg = self._message(thread, ME, to, subject, body, datetime.now(timezone.utc), ["SENT"])
        self._state["messages"].append(msg)
        self._state["outbox"].append({"to": to, "subject": subject, "body": body, "threadId": thread,
                                      "at": msg["messageTimestamp"]})
        self._save()
        return {"successful": True, "data": {"id": msg["messageId"], "threadId": thread}}

    def _t_send_draft(self, args: dict) -> dict:
        draft = self._state["drafts"].pop(str(args.get("draft_id", "")), None)
        if draft is None:
            return {"successful": False, "error": "draft not found"}
        return self._send(draft["to"], draft["subject"], draft["body"], draft["threadId"])

    def _t_reply_to_thread(self, args: dict) -> dict:
        thread = str(args.get("thread_id", ""))
        subject = next((m["subject"] for m in self._state["messages"] if m["threadId"] == thread), "")
        return self._send(_address(args.get("recipient_email", "")), f"Re: {subject}".strip(),
                          str(args.get("message_body", "")), thread or self._next("thr_"))

    def _t_forward_message(self, args: dict) -> dict:
        original = next((m for m in self._state["messages"] if m["messageId"] == args.get("message_id")), None)
        if original is None:
            return {"successful": False, "error": "message not found"}
        body = f"{args.get('additional_text') or ''}\n\n---------- Forwarded ----------\n{original['messageText']}"
        return self._send(_address(args.get("recipient_email", "")), f"Fwd: {original['subject']}", body,
                          original["threadId"])

    def _t_delete_draft(self, args: dict) -> dict:
        found = self._state["drafts"].pop(str(args.get("draft_id", "")), None)
        self._save()
        return {"successful": found is not None, **({} if found else {"error": "draft not found"})}

    def _t_list_drafts(self, args: dict) -> dict:
        return {"successful": True, "data": {"drafts": list(self._state["drafts"].values())}}

    def _people(self, query: str = "") -> list[dict]:
        seen: dict[str, str] = {}
        for m in self._state["messages"]:
            if m["sender"] != ME:
                seen.setdefault(_address(m["sender"]), m["sender"].split("<")[0].strip())
        q = query.casefold()
        return [{"names": [{"displayName": name}], "emailAddresses": [{"value": addr}]}
                for addr, name in seen.items() if not q or q in addr or q in name.casefold()]

    def _t_search_people(self, args: dict) -> dict:
        return {"successful": True, "data": {"people": self._people(str(args.get("query", "")))}}

    def _t_get_contacts(self, args: dict) -> dict:
        return {"successful": True, "data": {"connections": self._people()}}

    def _t_get_people(self, args: dict) -> dict:
        return {"successful": True, "data": {"people": self._people()}}

    def _t_get_profile(self, args: dict) -> dict:
        return {"successful": True, "data": {"emailAddress": self._state.get("me", ME), "messagesTotal": len(self._state["messages"])}}


_DATA = Path(__file__).resolve().parent.parent.parent / "data" / "demo_mailbox.json"
_mailbox: Optional[DemoMailbox] = None
_lock = threading.Lock()


def get_demo_mailbox() -> DemoMailbox:
    global _mailbox
    with _lock:
        if _mailbox is None:
            _mailbox = DemoMailbox(_DATA)
        return _mailbox
