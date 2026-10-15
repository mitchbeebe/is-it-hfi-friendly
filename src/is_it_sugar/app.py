import hashlib
import logging
import math
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typesafe_sdk import AsyncTypeSafeClient, TypeSafeBadRequestError, TypeSafeError

from . import analyze, images, prefs, sugars
from .cache import Cache
from .models import Result
from .ratelimit import RateLimiter

load_dotenv()
log = logging.getLogger("uvicorn.error")
STATIC = Path(__file__).parent / "static"
MAX_IMAGE_BYTES = 16 * 1024 * 1024
RESULT_TTL = 7 * 86400
# Cached results are keyed by a fingerprint of the code and data that produce them, so any change
# to the analysis retires old results instead of serving them in an outdated shape.
RESULT_VERSION = hashlib.sha256(
    b"".join(
        (Path(__file__).parent / f).read_bytes()
        for f in (
            "analyze.py",
            "afcd.py",
            "known.py",
            "off.py",
            "sugars.py",
            "ingredients.py",
            "models.py",
            "data/afcd.json",
        )
    )
).hexdigest()[:12]
# Generous: a grocery run can easily be dozens of checks. This guards the shared USDA key
# (3,600 requests/hour) and TypeSafe spend against scripts, not people.
LIMITS = [(30, 60), (300, 86400)]


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.cache = Cache(os.environ.get("IS_IT_SUGAR_CACHE", ".cache.sqlite3"))
    app.state.cache.drop_other_versions(("text", "image"), RESULT_VERSION)
    app.state.cache.drop_other_versions(("ingredient",), analyze.INGREDIENT_VERSION)
    app.state.limiter = RateLimiter(LIMITS)
    async with AsyncTypeSafeClient() as ts, httpx.AsyncClient(timeout=20) as http:
        app.state.ts, app.state.http = ts, http
        yield
    app.state.cache.close()


app = FastAPI(title="Is it HFI-friendly?", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


# A header the hosting proxy sets to the real client address and clients can't forge
# (Fly.io: Fly-Client-IP). Unset locally, where uvicorn already applies X-Forwarded-For from
# a proxy on 127.0.0.1, such as Tailscale Funnel.
CLIENT_IP_HEADER = os.environ.get("CLIENT_IP_HEADER")


def rate_limit(request: Request, response: Response) -> None:
    ip = (CLIENT_IP_HEADER and request.headers.get(CLIENT_IP_HEADER)) or (
        request.client.host if request.client else "unknown"
    )
    ok, remaining, retry = request.app.state.limiter.hit(ip)
    if not ok:
        wait = math.ceil(retry)
        when = f"{math.ceil(wait / 60)} minute{'s' if wait > 60 else ''}" if wait < 3600 else "a few hours"
        raise HTTPException(
            429, f"You've hit the limit for checks. Try again in {when}.", headers={"Retry-After": str(wait)}
        )
    response.headers["X-RateLimit-Remaining"] = str(remaining)


class TextQuery(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    fdc_id: int | None = None
    allow: list[str] = Field(default=[], max_length=prefs.MAX_TERMS)
    avoid: list[str] = Field(default=[], max_length=prefs.MAX_TERMS)
    # Measured fructose load (g per 100 g) below which a food is Caution rather than Avoid; 0 turns it off.
    trace_limit: float = Field(default=1.0, ge=0, le=20)


class SugarName(BaseModel):
    name: str
    names: list[str]
    status: sugars.Status


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.post("/api/check/text", dependencies=[Depends(rate_limit)])
async def check_text(q: TextQuery) -> Result:
    cache: Cache = app.state.cache
    key = f"{' '.join(q.text.lower().split())}|{q.fdc_id}"
    if (hit := cache.get(f"text:{RESULT_VERSION}", key, RESULT_TTL)) is not None:
        result = Result.model_validate(hit)
    else:
        result = await _run(analyze.check_text(app.state.ts, app.state.http, q.text, q.fdc_id, cache))
        cache.set(f"text:{RESULT_VERSION}", key, result.model_dump(mode="json"))
    return prefs.apply(result, q.allow, q.avoid, query=q.text, trace_limit=q.trace_limit)


@app.post("/api/check/image", dependencies=[Depends(rate_limit)])
async def check_image(
    image: Annotated[UploadFile, File()],
    allow: Annotated[list[str] | None, Form()] = None,
    avoid: Annotated[list[str] | None, Form()] = None,
) -> Result:
    data = await image.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "That image is too large. Try a smaller photo.")
    try:
        data = images.prepare(data)
    except images.BadImage as e:
        raise HTTPException(415, str(e))

    # Avoided terms become questions about the photo, so they're part of the cache key.
    avoid_terms = list(prefs.terms(avoid or []).values())
    cache: Cache = app.state.cache
    key = hashlib.sha256(data).hexdigest() + "|" + "|".join(sorted(prefs.key(t) for t in avoid_terms))
    if (hit := cache.get(f"image:{RESULT_VERSION}", key, RESULT_TTL)) is not None:
        result = Result.model_validate(hit)
    else:
        result = await _run(analyze.check_image(app.state.ts, data, avoid_terms))
        cache.set(f"image:{RESULT_VERSION}", key, result.model_dump(mode="json"))
    return prefs.apply(result, allow or [], avoid_terms)


@app.get("/api/sugars")
async def sugar_table() -> list[SugarName]:
    """The BU table's names, so Settings can warn before allowing a sugar BU marks as not tolerated."""
    return [SugarName(name=e.name, names=list(e.names), status=e.status) for e in sugars.TABLE]


async def _run(coro) -> Result:
    try:
        return await coro
    except analyze.NotAFood as e:
        raise HTTPException(422, str(e))
    except TypeSafeBadRequestError:
        raise HTTPException(422, "Couldn't use that input. If it's a photo, try a larger, clearer JPEG or PNG.")
    except TypeSafeError as e:
        log.exception("TypeSafe request failed")
        raise HTTPException(502, f"The classifier couldn't process that request ({e.__class__.__name__}).")
    except httpx.HTTPError:
        log.exception("Food database request failed")
        raise HTTPException(502, "Couldn't reach the food databases. Try again in a moment.")
