import asyncio
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from lab import ocr, providers

STATIC = Path(__file__).parent / "static"
HOSTS = ("localhost", "127.0.0.1", "captcha-lab.test")
IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}
CSP = (
    "default-src 'none'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; "
    "form-action 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; "
    "script-src 'self' https://www.google.com https://www.gstatic.com https://js.hcaptcha.com "
    "https://*.hcaptcha.com https://challenges.cloudflare.com; "
    "frame-src https://www.google.com https://recaptcha.google.com https://*.hcaptcha.com "
    "https://challenges.cloudflare.com; connect-src 'self' https://www.google.com "
    "https://*.hcaptcha.com https://challenges.cloudflare.com"
)


class Token(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4096)]


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv(STATIC.parents[1] / ".env")
    app.state.scenarios = providers.scenarios()
    minimum = float(os.getenv("RECAPTCHA_V3_MIN_SCORE", "0.5"))
    if not 0 <= minimum <= 1:
        raise ValueError("RECAPTCHA_V3_MIN_SCORE must be between 0 and 1")
    app.state.minimum = minimum
    app.state.ocr_slots = asyncio.Semaphore(2)
    async with httpx.AsyncClient(
        timeout=5, follow_redirects=False, trust_env=False,
        limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
    ) as client:
        app.state.client = client
        yield


app = FastAPI(title="CAPTCHA Lab", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(HOSTS))
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def browser_security(request: Request, call_next):
    if request.method not in ("GET", "HEAD"):
        origin = request.headers.get("origin")
        fetch_site = request.headers.get("sec-fetch-site")
        if (origin is not None and origin != str(request.base_url).rstrip("/")) or (
            fetch_site is not None and fetch_site not in ("same-origin", "none")
        ):
            response = JSONResponse({"detail": "Cross-origin requests are disabled."}, status_code=403)
        else:
            response = await call_next(request)
    else:
        response = await call_next(request)
    response.headers.update({
        "Content-Security-Policy": CSP,
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
    })
    return response


async def bounded_body(request: Request, limit: int):
    body = bytearray()
    try:
        async with asyncio.timeout(10):
            async for chunk in request.stream():
                if len(body) + len(chunk) > limit:
                    raise HTTPException(413, "Request body is too large.")
                body.extend(chunk)
    except TimeoutError as exc:
        raise HTTPException(408, "Request body timed out.") from exc
    return bytes(body)


@app.get("/")
async def dashboard():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
async def health():
    return {"status": "ok", "ocr_available": shutil.which("tesseract") is not None}


@app.get("/api/config")
async def config():
    return {
        "scenarios": {
            name: {"site_key": scenario.site_key if scenario.secret else "", "expected": scenario.expected}
            for name, scenario in app.state.scenarios.items()
        },
        "v3_action": providers.ACTION,
    }


@app.post("/api/verify/{name}")
async def verify(request: Request, name: str):
    scenario = app.state.scenarios.get(name)
    if scenario is None:
        raise HTTPException(404, "Unknown scenario.")
    if not scenario.site_key or not scenario.secret:
        raise HTTPException(409, "Configure the dedicated reCAPTCHA v3 development keys.")
    if name == "recaptcha-v3" and request.url.hostname != "localhost":
        raise HTTPException(400, "Open reCAPTCHA v3 on localhost.")
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise HTTPException(415, "Send an application/json body.")
    raw = await bounded_body(request, 8192)
    try:
        token = Token.model_validate_json(raw).token
    except ValidationError as exc:
        raise HTTPException(422, "Supply one nonempty token of at most 4096 characters.") from exc
    try:
        async with asyncio.timeout(8):
            return await providers.verify(app.state.client, name, scenario, token, app.state.minimum)
    except (httpx.HTTPError, ValueError, TimeoutError) as exc:
        raise HTTPException(502, "Siteverify timed out or returned an invalid response.") from exc


@app.get("/api/ocr/sample")
def sample():
    return Response(ocr.sample_image(), media_type="image/png")


@app.post("/api/ocr")
async def recognize(request: Request):
    if request.headers.get("content-type", "").split(";")[0].strip().lower() not in IMAGE_TYPES:
        raise HTTPException(415, "Send PNG, JPEG or WebP image bytes.")
    slots = app.state.ocr_slots
    if slots.locked():
        raise HTTPException(429, "OCR is busy. Try again shortly.")
    async with slots:
        raw = await bounded_body(request, ocr.MAX_BYTES)
        try:
            result = await run_in_threadpool(ocr.recognize, raw)
        except ocr.InvalidImage as exc:
            raise HTTPException(422, str(exc)) from exc
        except ocr.OcrUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
    return {"text": result}
