"""Minimal Google Gemini client (generateContent REST API) with JSON-mode output."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-2.5-flash"


class GeminiError(RuntimeError):
    pass


class GeminiClient:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, timeout: float = 180.0):
        if not api_key:
            raise ValueError("A Gemini API key is required")
        self.api_key = api_key
        self.model = model or DEFAULT_MODEL
        self.timeout = timeout

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
