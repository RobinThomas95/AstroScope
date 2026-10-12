"""HTTP endpoints for Module 5 (ADS bibliometrics) and Module 6 (Gemini synthesis).

API keys come from a request header (the user's own key, kept in their browser) or, when the
header is absent, from the server's .env. Keys are never stored server-side.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.config import get_settings
from app.schemas.literature import KeyStatus, LiteratureResult, SynthesisResult
from app.services.ads import get_literature_service
from app.services.gemini import DEFAULT_MODEL, GeminiClient, GeminiError
from app.services.synthesis import get_synthesis_service

router = APIRouter(prefix="/api", tags=["literature"])


def _ads_token(header: str | None) -> tuple[str, str]:
    if header and header.strip():
        return header.strip(), "header"
    server = get_settings().ads_dev_key
    if server:
        return server, "server"
    raise HTTPException(
        status_code=401,
        detail="No ADS token: add yours in the Keys dialog (free at "
        "https://ui.adsabs.harvard.edu/user/settings/token) or set ADS_DEV_KEY on the server.",
    )


def _gemini_key(header: str | None) -> tuple[str, str]:
    if header and header.strip():
        return header.strip(), "header"
    server = get_settings().gemini_api_key
    if server:
        return server, "server"
    raise HTTPException(
        status_code=401,
        detail="No Gemini API key: add yours in the Keys dialog (free tier at "
        "https://aistudio.google.com/apikey) or set GEMINI_API_KEY on the server.",
    )


@router.get("/keys/status", response_model=KeyStatus)
async def key_status() -> KeyStatus:
    s = get_settings()
    return KeyStatus(
        ads_server_key=bool(s.ads_dev_key),
        gemini_server_key=bool(s.gemini_api_key),
        gemini_model=s.gemini_model or DEFAULT_MODEL,
    )


@router.get("/literature", response_model=LiteratureResult)
async def literature(
    q: str = Query(..., min_length=1, description="Target name as resolved"),
    names: str | None = Query(None, description="Comma-separated aliases to search as well"),
    refereed: bool = Query(False),
    max_rows: int = Query(1000, ge=50, le=2000),
    x_ads_token: str | None = Header(default=None, alias="X-ADS-Token"),
) -> LiteratureResult:
    token, source = _ads_token(x_ads_token)
    name_list = [q] + [n.strip() for n in (names or "").split(",") if n.strip()]
    try:
        return await run_in_threadpool(
            get_literature_service().search, q, name_list, token, source, refereed, max_rows
        )
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - remote service
        raise HTTPException(
            status_code=502, detail=f"ADS: {type(exc).__name__}: {exc}"[:500]
        ) from exc


class SynthesisRequest(BaseModel):
    q: str = Field(description="Target name as resolved")
    names: list[str] = Field(default_factory=list)
    refereed: bool = False
    max_rows: int = Field(default=1000, ge=50, le=2000)
    max_abstracts: int = Field(default=60, ge=5, le=150)
    model: str | None = None


@router.post("/synthesis", response_model=SynthesisResult)
async def synthesis(
    body: SynthesisRequest,
    x_ads_token: str | None = Header(default=None, alias="X-ADS-Token"),
    x_gemini_key: str | None = Header(default=None, alias="X-Gemini-Key"),
) -> SynthesisResult:
    token, _ = _ads_token(x_ads_token)
    api_key, key_source = _gemini_key(x_gemini_key)
    model = body.model or get_settings().gemini_model or DEFAULT_MODEL
    name_list = [body.q] + [n for n in body.names if n.strip()]
    try:
        lit = await run_in_threadpool(
            get_literature_service().search,
            body.q,
            name_list,
            token,
            "header",
            body.refereed,
            body.max_rows,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502, detail=f"ADS: {type(exc).__name__}: {exc}"[:500]
        ) from exc
    try:
        return await run_in_threadpool(
            get_synthesis_service().synthesise,
            body.q,
            name_list[1:],
            lit.papers,
            lit.metrics,
            api_key,
            key_source,
            model,
            body.max_abstracts,
        )
    except GeminiError as exc:
        status = 401 if "rejected" in str(exc) else 502
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/llm/models", response_model=list[str])
async def llm_models(x_gemini_key: str | None = Header(default=None, alias="X-Gemini-Key")):
    """Validate a Gemini key and list the models it can use."""
    api_key, _ = _gemini_key(x_gemini_key)
    try:
        return await run_in_threadpool(GeminiClient(api_key).list_models)
    except GeminiError as exc:
        raise HTTPException(
            status_code=401 if "rejected" in str(exc) else 502, detail=str(exc)
        ) from exc
