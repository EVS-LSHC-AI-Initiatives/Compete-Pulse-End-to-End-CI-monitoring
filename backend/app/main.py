from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import ALLOWED_ORIGINS
from app.routes import (
    overview,
    news,
    morning_update,
    clinical_trials,
    feeds,
    pipeline,
    settings,
)

app = FastAPI(
    title="CompetePulse — MASH CI Monitoring Agent API",
    version="2.4.0",
    description="REST API service for CompetePulse CI platform persisting state directly into JSON",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/healthz")
def healthz():
    return {"status": "ok", "app": "CompetePulse MASH CI Monitoring Agent", "storage": "json"}

# Register routes under /api
app.include_router(overview.router, prefix="/api", tags=["Overview"])
app.include_router(news.router, prefix="/api", tags=["News Triage"])
app.include_router(morning_update.router, prefix="/api", tags=["Morning Update"])
app.include_router(clinical_trials.router, prefix="/api", tags=["Clinical Trials"])
app.include_router(feeds.router, prefix="/api", tags=["Feed Health"])
app.include_router(pipeline.router, prefix="/api", tags=["Pipeline Orchestrator"])
app.include_router(settings.router, prefix="/api", tags=["Settings"])
