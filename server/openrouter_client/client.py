from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import httpx

from ..config import get_settings

OpenRouterBaseURL = "https://openrouter.ai/api/v1"
OpenAIChatURL = "https://api.openai.com/v1/chat/completions"


class OpenRouterError(RuntimeError):
    """Raised when the OpenRouter API returns an error response."""


def _headers(*, api_key: Optional[str] = None) -> Dict[str, str]:
    settings = get_settings()
    key = (api_key or settings.llm_api_key or "").strip()
    if not key:
        raise OpenRouterError("Missing OpenRouter API key")

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    return headers


def _build_messages(messages: List[Dict[str, str]], system: Optional[str]) -> List[Dict[str, str]]:
    if system:
        return [{"role": "system", "content": system}, *messages]
    return messages


def _handle_response_error(exc: httpx.HTTPStatusError) -> None:
    response = exc.response
    detail: str
    try:
        payload = response.json()
        detail = payload.get("error") or payload.get("message") or json.dumps(payload)
    except Exception:
        detail = response.text
    raise OpenRouterError(f"OpenRouter request failed ({response.status_code}): {detail}") from exc


async def request_chat_completion(
    *,
    model: str,
    messages: List[Dict[str, str]],
    system: Optional[str] = None,
    api_key: Optional[str] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    base_url: str = OpenRouterBaseURL,
    max_tokens: int = 2048,
) -> Dict[str, Any]:
    """Request a chat completion and return the raw JSON payload."""

    settings = get_settings()
    if settings.llm_provider == "anthropic":
        from .anthropic_provider import anthropic_chat_completion

        key = (api_key or settings.llm_api_key or "").strip()
        if not key:
            raise OpenRouterError("Missing ANTHROPIC_API_KEY")
        try:
            return await anthropic_chat_completion(api_key=key, model=model, messages=messages, system=system,
                                                   tools=tools, max_tokens=max_tokens,
                                                   workspace_id=settings.anthropic_workspace_id)
        except Exception as exc:
            raise OpenRouterError(f"Anthropic request failed: {exc}") from exc

    payload: Dict[str, object] = {
        "model": model,
        "messages": _build_messages(messages, system),
        "stream": False,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools

    url = f"{base_url.rstrip('/')}/chat/completions"
    if settings.llm_provider == "openai":
        # OpenAI directly: its own model ids, max_completion_tokens, and no reasoning (chat
        # completions refuses function tools combined with reasoning on newer models).
        url = OpenAIChatURL
        payload["model"] = model.split("/", 1)[1] if model.startswith("openai/") else model
        payload["max_completion_tokens"] = payload.pop("max_tokens")
        payload["reasoning_effort"] = "none"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                url,
                headers=_headers(api_key=api_key),
                json=payload,
                timeout=60.0,  # Set reasonable timeout instead of None
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                _handle_response_error(exc)
            return response.json()
        except httpx.HTTPStatusError as exc:  # pragma: no cover - handled above
            _handle_response_error(exc)
        except httpx.HTTPError as exc:
            raise OpenRouterError(f"OpenRouter request failed: {exc}") from exc

    raise OpenRouterError("OpenRouter request failed: unknown error")


__all__ = ["OpenRouterError", "request_chat_completion", "OpenRouterBaseURL"]
