"""Minimal Google Gemini client (generateContent REST API) with JSON-mode output."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "auto"  # resolved against the models the key can use

_EXCLUDE = (
    "embedding",
    "tts",
    "image",
    "audio",
    "live",
    "vision",
    "aqa",
    "imagen",
    "veo",
    "learnlm",
    "computer",
    "robotics",
    "native",
)
_VERSION = re.compile(r"gemini-(\d+(?:\.\d+)?)")


def rank_models(names: list[str]) -> list[str]:
    """Text-capable Gemini models, best first: newest version, flash over pro (fast + cheap),
    full over lite, stable over preview/experimental."""

    def key(name: str):
        n = name.lower()
        m = _VERSION.search(n)
        version = float(m.group(1)) if m else 0.0
        return (
            -version,
            0 if "flash" in n else 1,
            1 if "lite" in n else 0,
            1 if any(t in n for t in ("preview", "exp", "latest", "-0", "-1")) else 0,
            len(n),
        )

    usable = [
        n
        for n in names
        if n.lower().startswith("gemini") and not any(x in n.lower() for x in _EXCLUDE)
    ]
    return sorted(usable, key=key)


class GeminiError(RuntimeError):
    pass


_model_cache: dict[str, tuple[float, list[str]]] = {}
_model_lock = threading.Lock()


class GeminiClient:
    def __init__(self, api_key: str, model: str | None = DEFAULT_MODEL, timeout: float = 180.0):
        if not api_key:
            raise ValueError("A Gemini API key is required")
        self.api_key = api_key
        self.requested_model = (model or DEFAULT_MODEL).strip()
        self.model = self.requested_model
        self.timeout = timeout

    def available_models(self, ttl: float = 3600.0) -> list[str]:
        """Models the key can use (cached per key)."""
        k = self.api_key[-8:]
        now = time.monotonic()
        with _model_lock:
            hit = _model_cache.get(k)
            if hit and hit[0] > now:
                return list(hit[1])
        models = self.list_models()
        with _model_lock:
            _model_cache[k] = (now + ttl, models)
        return models

    def resolve_model(self) -> str:
        """Turn 'auto' (or an unavailable name) into a model the key can use."""
        models = self.available_models()
        ranked = rank_models(models)
        if self.requested_model and self.requested_model != "auto":
            if self.requested_model in models or not models:
                self.model = self.requested_model
                return self.model
            log.warning(
                "Gemini model %s not available; using %s",
                self.requested_model,
                ranked[0] if ranked else "?",
            )
        if not ranked:
            raise GeminiError("This key lists no text-capable Gemini models")
        self.model = ranked[0]
        return self.model

    def list_models(self) -> list[str]:
        with httpx.Client(timeout=30) as client:
            res = client.get(f"{GEMINI_API}/models", params={"key": self.api_key, "pageSize": 100})
        self._check(res)
        return [
            m["name"].removeprefix("models/")
            for m in res.json().get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
        ]

    @staticmethod
    def _check(res: httpx.Response) -> None:
        if res.status_code == 200:
            return
        detail = ""
        try:
            detail = res.json().get("error", {}).get("message", "")
        except Exception:  # noqa: BLE001
            detail = res.text[:300]
        if res.status_code in (401, 403):
            raise GeminiError(f"Gemini rejected the API key ({res.status_code}): {detail}")
        if res.status_code == 429:
            raise GeminiError(f"Gemini rate limit / quota exceeded (429): {detail}")
        if res.status_code == 404:
            raise GeminiError(f"Gemini model not found (404): {detail}")
        raise GeminiError(f"Gemini error {res.status_code}: {detail}")

    def generate_json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        temperature: float = 0.3,
        max_output_tokens: int = 8192,
    ) -> tuple[dict, dict]:
        """Run a prompt in JSON mode; returns (parsed JSON, usage metadata)."""
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_output_tokens,
                "responseMimeType": "application/json",
            },
        }
        if schema:
            body["generationConfig"]["responseSchema"] = schema
        if self.model == "auto" or self.model == self.requested_model and self.model != "auto":
            try:
                self.resolve_model()
            except GeminiError:
                if self.model == "auto":
                    raise
        url = f"{GEMINI_API}/models/{self.model}:generateContent"
        with httpx.Client(timeout=self.timeout) as client:
            res = client.post(url, params={"key": self.api_key}, json=body)
        self._check(res)
        data = res.json()
        candidates = data.get("candidates") or []
        if not candidates:
            reason = data.get("promptFeedback", {}).get("blockReason", "no candidates")
            raise GeminiError(f"Gemini returned no answer ({reason})")
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts).strip()
        finish = candidates[0].get("finishReason")
        if not text:
            raise GeminiError(f"Gemini returned empty text (finishReason={finish})")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            # Occasionally the model wraps JSON in code fences despite JSON mode.
            stripped = text.strip("`").removeprefix("json").strip()
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                raise GeminiError(
                    f"Gemini did not return valid JSON ({exc}); finishReason={finish}"
                ) from exc
        return parsed, data.get("usageMetadata", {})
