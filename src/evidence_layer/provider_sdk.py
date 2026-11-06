"""Live-capable provider adapters for Phase 6 roster v1.

Three provider families, one request shape each, taken from the frozen
operational matrix (`docs/phase6-operational-model-matrix-v1.md`):

- OpenAI Responses (`gpt-6.1-sol`, `gpt-6.1-terra`, `gpt-6-luna`)
- Anthropic Messages (`claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5`)
- DeepSeek Chat Completions (`deepseek-flash`, `deepseek-v4-pro`)

Nothing here runs unless `PHASE6_LIVE_SDK=1` and the provider key is set. The
module imports no network code at load time. Tests use `httpx.MockTransport`.
Keys are read from the environment at client construction, are never placed in a
request body, a reply, an error, or a repr. Each call performs exactly one HTTP
request; retries belong to the orchestrator, which records every attempt.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

from evidence_layer.provider_execution import ProviderReply, ProviderRequestError
from evidence_layer.provider_usage import visible_text_for

LIVE_FLAG = "PHASE6_LIVE_SDK"
REQUEST_TIMEOUT_SECONDS = 120.0
ANTHROPIC_VERSION = "2026-10-21"

ROSTER_PROVIDER: dict[str, str] = {
    "gpt-6.1-sol": "openai",
    "gpt-6.1-terra": "openai",
    "gpt-6-luna": "openai",
    "claude-opus-5-5": "anthropic",
    "claude-sonnet-5-5": "anthropic",
    "claude-haiku-5-5": "anthropic",
    "deepseek-flash": "deepseek",
    "deepseek-v4-pro": "deepseek",
}

KEY_ENV: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}

ENDPOINT: dict[str, str] = {
    "openai": "https://api.openai.com/v1/responses",
    "anthropic": "https://api.anthropic.com/v1/messages",
    "deepseek": "https://api.deepseek.com/chat/completions",
}


def live_enabled(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return env.get(LIVE_FLAG) == "1"


@dataclass(frozen=True)
class HttpRequest:
    url: str
    headers: dict[str, str]
    body: dict[str, Any]


def build_http_request(model_id: str, prompt: str, hard_output_cap: int, api_key: str) -> HttpRequest:
    """Build the provider request for one candidate. The key only enters headers."""
    if model_id not in ROSTER_PROVIDER:
        raise ValueError(f"{model_id} is not in roster v1")
    if not prompt or hard_output_cap <= 0:
        raise ValueError("prompt and a positive output cap are required")
    provider = ROSTER_PROVIDER[model_id]
    if provider == "openai":
        return HttpRequest(
            url=ENDPOINT["openai"],
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            body={
                "model": model_id,
                "input": prompt,
                "max_output_tokens": hard_output_cap,
                "reasoning": {"effort": "low"},
                "store": False,
                "prompt_cache_options": {"mode": "explicit"},
            },
        )
    if provider == "anthropic":
        return HttpRequest(
            url=ENDPOINT["anthropic"],
            headers={
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "Content-Type": "application/json",
            },
            body={
                "model": model_id,
                "max_tokens": hard_output_cap,
                "messages": [{"role": "user", "content": prompt}],
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": "medium"},
            },
        )
    return HttpRequest(
        url=ENDPOINT["deepseek"],
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        body={
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": hard_output_cap,
            "temperature": 0,
            "top_p": 1,
            "thinking": {"type": "disabled"},
        },
    )


_LENGTH_STOPS = {"openai": "max_output_tokens", "anthropic": "max_tokens", "deepseek": "length"}


def _stop_reason(provider: str, data: Mapping[str, Any]) -> str | None:
    if provider == "openai":
        details = data.get("incomplete_details")
        if data.get("status") == "incomplete" and isinstance(details, Mapping):
            return details.get("reason")
        return None
    if provider == "anthropic":
        value = data.get("stop_reason")
        return value if isinstance(value, str) else None
    choices = data.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
        value = choices[0].get("finish_reason")
        return value if isinstance(value, str) else None
    return None


def _raw_output_tokens(provider: str, data: Mapping[str, Any]) -> int | None:
    usage = data.get("usage")
    if not isinstance(usage, Mapping):
        return None
    key = "completion_tokens" if provider == "deepseek" else "output_tokens"
    value = usage.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


_SAFE_CODE = None


def _safe_error_code(response: Any) -> str | None:
    """Extract a short enum-like provider error code; never the message text."""
    import re

    try:
        data = response.json()
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    error = data.get("error")
    candidates = []
    if isinstance(error, dict):
        candidates = [error.get("code"), error.get("type"), error.get("status")]
    elif isinstance(data.get("type"), str):
        candidates = [data.get("type")]
    for value in candidates:
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", value):
            return value
    return None


def parse_http_response(
    model_id: str, hard_output_cap: int, status_code: int, data: Mapping[str, Any]
) -> ProviderReply:
    """Map a provider JSON body to a ProviderReply without inventing fields.

    `model` is copied only when the provider returns it. A stop at the length
    limit is a success only if the reported output already reaches the cap, in
    which case the orchestrator records `output_cap_reached`; a length stop below
    the cap is a partial response.
    """
    provider = ROSTER_PROVIDER[model_id]
    returned = data.get("model")
    payload: dict[str, Any] = {
        "model": returned if isinstance(returned, str) else None,
        "usage": data.get("usage"),
        "output_text": visible_text_for(provider, data),
    }
    if provider == "anthropic":
        payload["content"] = data.get("content")
    elif provider == "openai":
        payload["output"] = data.get("output")
        payload["output_text"] = visible_text_for(provider, data)
    else:
        payload["choices"] = data.get("choices")

    stop = _stop_reason(provider, data)
    if stop is not None and stop == _LENGTH_STOPS[provider]:
        reported = _raw_output_tokens(provider, data)
        outcome = "success" if reported is not None and reported >= hard_output_cap else "partial"
    elif provider == "anthropic" and stop == "refusal":
        outcome = "refusal"
    else:
        outcome = "success"
    return ProviderReply(payload, outcome=outcome)


class LiveProviderClient:
    """One candidate, one HTTP request per call, no internal retries."""

    def __init__(
        self,
        model_id: str,
        *,
        api_key: str,
        transport: Any | None = None,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        if model_id not in ROSTER_PROVIDER:
            raise ValueError(f"{model_id} is not in roster v1")
        if not api_key:
            raise ValueError("provider API key is missing")
        self._model_id = model_id
        self._api_key = api_key
        self._transport = transport
        self._timeout = timeout

    def __repr__(self) -> str:
        return f"LiveProviderClient(model={self._model_id!r}, key=<redacted>)"

    @classmethod
    def from_environment(cls, model_id: str, *, environ: Mapping[str, str] | None = None) -> "LiveProviderClient":
        env = os.environ if environ is None else environ
        if not live_enabled(env):
            raise RuntimeError(f"live provider calls are disabled; set {LIVE_FLAG}=1 to opt in")
        provider = ROSTER_PROVIDER.get(model_id)
        if provider is None:
            raise ValueError(f"{model_id} is not in roster v1")
        key = env.get(KEY_ENV[provider], "")
        if not key:
            raise RuntimeError(f"{KEY_ENV[provider]} is not set")
        return cls(model_id, api_key=key)

    async def generate(self, request: Mapping[str, object]) -> ProviderReply:
        import httpx

        prompt = request.get("prompt")
        cap = request.get("hard_output_cap")
        if not isinstance(prompt, str) or not isinstance(cap, int) or isinstance(cap, bool):
            raise ProviderRequestError("request_rejected")
        http = build_http_request(self._model_id, prompt, cap, self._api_key)
        async with httpx.AsyncClient(
            timeout=self._timeout,
            transport=self._transport,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            try:
                response = await client.post(http.url, headers=http.headers, json=http.body)
            except httpx.TimeoutException:
                raise ProviderRequestError("timeout") from None
            except httpx.HTTPError:
                raise ProviderRequestError("transport_error") from None
        if response.status_code >= 400:
            code = _safe_error_code(response)
            if response.status_code == 429:
                raise ProviderRequestError("rate_limited", response.status_code, code)
            if response.status_code >= 500:
                raise ProviderRequestError("transport_error", response.status_code, code)
            raise ProviderRequestError("request_rejected", response.status_code, code)
        try:
            data = response.json()
        except ValueError:
            raise ProviderRequestError("unknown_error") from None
        if not isinstance(data, dict):
            raise ProviderRequestError("unknown_error")
        return parse_http_response(self._model_id, cap, response.status_code, data)
