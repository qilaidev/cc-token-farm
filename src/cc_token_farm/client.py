from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

ANTHROPIC_VERSION = "2023-06-01"


@dataclass
class RequestResult:
    ok: bool
    status: int | None
    latency_ms: float
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    error: str = ""
    preview: str = ""
    raw_usage: dict[str, Any] | None = None


def http_json(
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any] | None,
    timeout: float,
) -> tuple[int, dict[str, Any] | str, float]:
    data = None
    req_headers = dict(headers)
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
        req_headers.setdefault("Content-Length", str(len(data)))

    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            latency = (time.perf_counter() - t0) * 1000
            status = resp.getcode() or 0
            text = raw.decode("utf-8", errors="replace")
            try:
                return status, json.loads(text) if text else {}, latency
            except json.JSONDecodeError:
                return status, text, latency
    except urllib.error.HTTPError as e:
        latency = (time.perf_counter() - t0) * 1000
        raw = e.read() if e.fp else b""
        text = raw.decode("utf-8", errors="replace")
        try:
            payload: dict[str, Any] | str = json.loads(text) if text else {"error": str(e)}
        except json.JSONDecodeError:
            payload = text or str(e)
        return e.code, payload, latency
    except Exception as e:  # noqa: BLE001
        latency = (time.perf_counter() - t0) * 1000
        return 0, str(e), latency


def check_proxy(proxy: str, timeout: float = 3.0) -> tuple[bool, str]:
    parsed = urlparse(proxy)
    if not parsed.scheme or not parsed.hostname:
        return False, f"invalid proxy URL: {proxy}"
    candidates = [
        urljoin(proxy.rstrip("/") + "/", "v1/models"),
        urljoin(proxy.rstrip("/") + "/", "health"),
        proxy.rstrip("/") + "/",
    ]
    last_err = ""
    for url in candidates:
        status, body, _ = http_json("GET", url, {}, None, timeout)
        if status == 0:
            last_err = str(body)
            continue
        return True, f"proxy reachable {proxy} ({url} → HTTP {status})"
    return False, f"cannot connect to proxy {proxy}: {last_err}"


def pad_text(base: str, target_chars: int) -> str:
    if target_chars <= 0 or len(base) >= target_chars:
        return base
    unit = " alpha bravo charlie delta echo foxtrot golf hotel india juliet "
    need = target_chars - len(base)
    reps = (need // len(unit)) + 1
    return base + "\n\n[pad]\n" + (unit * reps)[:need]


def build_user_prompt(prompt: str, prompt_chars: int, seq: int) -> str:
    body = prompt or "Reply with exactly one word: ok"
    body = f"[farm#{seq}] {body}"
    return pad_text(body, prompt_chars)


def default_cache_system(min_chars: int = 12000) -> str:
    """Long fixed system text to encourage Anthropic prompt-cache hits."""
    base = (
        "You are a concise assistant used only for token-usage traffic generation. "
        "Always reply with a single short word. Do not explain. "
    )
    # ~4 chars/token → 12k chars ≈ 3k tokens (enough for many cache thresholds)
    filler = ("lorem ipsum dolor sit amet consectetur adipiscing elit " * 400)
    text = base + "Context block: " + filler
    if len(text) < min_chars:
        text += " " + ("pad " * ((min_chars - len(text)) // 4 + 1))
    return text[: max(min_chars, len(base) + 100)]


def build_anthropic_payload(
    model: str,
    user_text: str,
    max_tokens: int,
    system: str | None,
    stream: bool,
    enable_cache: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "stream": stream,
        "messages": [{"role": "user", "content": user_text}],
    }
    if system:
        if enable_cache:
            payload["system"] = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            payload["system"] = system
    return payload


def build_openai_payload(
    model: str,
    user_text: str,
    max_tokens: int,
    system: str | None,
    stream: bool,
) -> dict[str, Any]:
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user_text})
    return {
        "model": model,
        "max_tokens": max_tokens,
        "stream": stream,
        "messages": messages,
    }


def parse_usage(fmt: str, body: dict[str, Any] | str) -> tuple[int, int, int, int, dict | None]:
    if not isinstance(body, dict):
        return 0, 0, 0, 0, None
    usage = body.get("usage") or {}
    if not isinstance(usage, dict):
        return 0, 0, 0, 0, None

    if fmt == "anthropic":
        return (
            int(usage.get("input_tokens") or 0),
            int(usage.get("output_tokens") or 0),
            int(usage.get("cache_read_input_tokens") or 0),
            int(usage.get("cache_creation_input_tokens") or 0),
            usage,
        )

    cache_read = int(usage.get("cache_read_input_tokens") or 0)
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        cache_read = cache_read or int(details.get("cached_tokens") or 0)
    return (
        int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
        int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
        cache_read,
        int(usage.get("cache_creation_input_tokens") or 0),
        usage,
    )


def _preview(body: dict[str, Any] | str, fmt: str, limit: int = 80) -> str:
    if not isinstance(body, dict):
        return str(body).replace("\n", " ")[:limit]
    if fmt == "anthropic":
        content = body.get("content")
        if isinstance(content, list) and content:
            t = content[0].get("text") if isinstance(content[0], dict) else str(content[0])
            return (t or "")[:limit].replace("\n", " ")
        err = body.get("error")
        return str(err or body)[:limit]
    choices = body.get("choices")
    if isinstance(choices, list) and choices:
        msg = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(msg, dict):
            return (msg.get("content") or "")[:limit].replace("\n", " ")
    err = body.get("error")
    return str(err or body)[:limit]


class ProxyClient:
    def __init__(
        self,
        proxy: str,
        api_key: str,
        timeout: float = 120.0,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.proxy = proxy.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.extra_headers = dict(extra_headers or {})

    def send(
        self,
        *,
        fmt: str,
        model: str,
        user_text: str,
        max_tokens: int,
        system: str | None = None,
        stream: bool = False,
        enable_cache: bool = False,
    ) -> RequestResult:
        if fmt == "anthropic":
            url = urljoin(self.proxy + "/", "v1/messages")
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            }
            if enable_cache:
                headers["anthropic-beta"] = "prompt-caching-2024-07-31"
            payload = build_anthropic_payload(
                model, user_text, max_tokens, system, stream, enable_cache
            )
        else:
            url = urljoin(self.proxy + "/", "v1/chat/completions")
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "content-type": "application/json",
            }
            payload = build_openai_payload(model, user_text, max_tokens, system, stream)

        headers.update(self.extra_headers)
        status, body, latency = http_json("POST", url, headers, payload, self.timeout)

        if status == 0:
            return RequestResult(
                ok=False, status=None, latency_ms=latency, model=model, error=str(body)
            )

        ok = 200 <= status < 300
        if stream and isinstance(body, str):
            ok = ok and ("data:" in body or not body.strip())
            return RequestResult(
                ok=ok,
                status=status,
                latency_ms=latency,
                model=model,
                error="" if ok else body[:200],
                preview=body[:80].replace("\n", " "),
            )

        inp, out, cr, cc, usage = parse_usage(fmt, body if isinstance(body, dict) else {})
        err = ""
        if not ok:
            if isinstance(body, dict):
                err = json.dumps(body.get("error") or body, ensure_ascii=False)[:300]
            else:
                err = str(body)[:300]
        return RequestResult(
            ok=ok,
            status=status,
            latency_ms=latency,
            model=model,
            input_tokens=inp,
            output_tokens=out,
            cache_read_tokens=cr,
            cache_creation_tokens=cc,
            error=err,
            preview=_preview(body, fmt),
            raw_usage=usage,
        )
