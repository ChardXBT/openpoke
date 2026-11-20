"""Claude directly through the Anthropic SDK (``OPENPOKE_PROVIDER=anthropic``).

OpenPoke's agents speak the OpenAI chat format (OpenRouter's). This module translates one
request into a Messages API call and the reply back into that format, so nothing above the
client changes:

- ``system`` -> ``system``; ``tool`` messages -> ``tool_result`` blocks in one user turn;
  assistant ``tool_calls`` -> ``tool_use`` blocks; function tools -> ``input_schema`` tools.
- The reply's text -> ``message.content``, ``tool_use`` -> ``message.tool_calls``.

Thinking is turned off where the model allows it, so the Interaction Agent runs the same way
as on the other providers (and no thinking blocks need to be carried across turns).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def _thinking_for(model: str) -> Optional[Dict[str, str]]:
    if model.startswith("claude-sonnet-5-5"):
        return {"type": "between_tools"}  # Sonnet 5.5 rejects "disabled"; this is its off switch
    if model.startswith(("claude-haiku-5-5", "claude-sonnet-5", "claude-opus-4", "claude-sonnet-4", "claude-haiku-4")):
        return {"type": "disabled"}
    return None  # models where thinking can't be turned off run adaptive


def _tools(tools: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    out = []
    for tool in tools or []:
        fn = tool.get("function") or {}
        if fn.get("name"):
            out.append({"name": fn["name"], "description": fn.get("description") or "",
                        "input_schema": fn.get("parameters") or {"type": "object", "properties": {}}})
    return out


def _arguments(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def to_anthropic_messages(messages: List[Dict[str, Any]]) -> tuple[str, List[Dict[str, Any]]]:
    """OpenAI-format messages -> (system text, Messages API messages). Same-role turns merge."""
    system_parts: List[str] = []
    out: List[Dict[str, Any]] = []

    def add(role: str, blocks: List[Dict[str, Any]]) -> None:
        if not blocks:
            return
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": list(blocks)})

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        text = content if isinstance(content, str) else json.dumps(content) if content else ""
        if role == "system":
            system_parts.append(text)
        elif role == "tool":
            add("user", [{"type": "tool_result", "tool_use_id": str(msg.get("tool_call_id") or ""),
                          "content": text or "(empty)"}])
        elif role == "assistant":
            blocks: List[Dict[str, Any]] = [{"type": "text", "text": text}] if text.strip() else []
            for call in msg.get("tool_calls") or []:
                fn = call.get("function") or {}
                blocks.append({"type": "tool_use", "id": str(call.get("id") or fn.get("name")),
                               "name": fn.get("name") or "", "input": _arguments(fn.get("arguments"))})
            add("assistant", blocks)
        else:
            add("user", [{"type": "text", "text": text or "(empty)"}])
    if out and out[0]["role"] != "user":  # the Messages API starts with a user turn
        out.insert(0, {"role": "user", "content": [{"type": "text", "text": "(conversation continues)"}]})
    return "\n\n".join(p for p in system_parts if p), out


def to_openai_response(message: Any) -> Dict[str, Any]:
    """A Messages API reply -> the OpenAI chat-completion shape the agents read."""
    texts, calls = [], []
    for block in message.content:
        if block.type == "text":
            texts.append(block.text)
        elif block.type == "tool_use":
            calls.append({"id": block.id, "type": "function",
                          "function": {"name": block.name, "arguments": json.dumps(block.input)}})
    reply: Dict[str, Any] = {"role": "assistant", "content": "".join(texts)}
    if calls:
        reply["tool_calls"] = calls
    usage = getattr(message, "usage", None)
    return {
        "id": getattr(message, "id", ""),
        "model": getattr(message, "model", ""),
        "choices": [{"index": 0, "message": reply, "finish_reason": getattr(message, "stop_reason", None)}],
        "usage": {"prompt_tokens": getattr(usage, "input_tokens", 0) or 0,
                  "completion_tokens": getattr(usage, "output_tokens", 0) or 0},
    }


async def anthropic_chat_completion(
    *,
    api_key: str,
    model: str,
    messages: List[Dict[str, Any]],
    system: Optional[str] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    max_tokens: int = 2048,
    client: Any = None,
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    import anthropic

    model = model.split("/", 1)[1].replace(".", "-") if model.startswith("anthropic/") else model
    extra_system, converted = to_anthropic_messages(messages)
    params: Dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": converted,
        "system": "\n\n".join(p for p in (system or "", extra_system) if p),
    }
    if not params["system"]:
        params.pop("system")
    converted_tools = _tools(tools)
    if converted_tools:
        params["tools"] = converted_tools
    thinking = _thinking_for(model)
    if thinking:
        params["thinking"] = thinking
    headers = {"anthropic-workspace-id": workspace_id} if workspace_id else None
    client = client or anthropic.AsyncAnthropic(api_key=api_key, timeout=60.0, default_headers=headers)
    message = await client.messages.create(**params)
    return to_openai_response(message)
