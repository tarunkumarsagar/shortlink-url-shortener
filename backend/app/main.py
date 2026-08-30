from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.analytics import router as analytics_router
from app.api.auth import router as auth_router
from app.api.urls import router as urls_router

app = FastAPI(
    title="ShortLink API",
    version="0.1.0",
    description="URL shortener with an analytics pipeline.",
)

# Permissive CORS for local development (the static dashboard is
# served from a different origin/port than the API). Tighten this to
# a specific allowed origin list before any real deployment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check():
    """Liveness check -- 'is the process running at all'. Distinct from
    a future /ready check, which will verify DB/Redis/queue connectivity
    once those exist (Phase 2+)."""
    return {"status": "ok"}


# IMPORTANT: registered LAST, deliberately. urls_router contains a
# catch-all `GET /{short_code}` route. FastAPI/Starlette matches routes
# in REGISTRATION ORDER, not by specificity -- a greedy path parameter
# registered before a specific path will shadow it. This bit us during
# manual testing: /health returned 404 because /{short_code} matched
# "health" as a short code first. Lesson: specific routes before
# catch-all routes, always.
app.include_router(auth_router)
app.include_router(analytics_router)
app.include_router(urls_router)
