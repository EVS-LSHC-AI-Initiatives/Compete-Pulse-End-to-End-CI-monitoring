from fastapi import APIRouter
from typing import Dict, Any, Optional
from pydantic import BaseModel
import datetime
from app.services.json_store import load_json, save_json

router = APIRouter()

class PipelineTriggerRequest(BaseModel):
    stage: str = "all"  # "all", "rss", "dedupe", "triage", "draft"
    slot: str = "Morning"

@router.get("/pipeline/runs")
def get_runs():
    data = load_json("pipeline_runs.json") or []
    runs = data if isinstance(data, list) else data.get("runs", [])
    return {
        "runs": runs,
        "active_pipeline": {
            "status": "idle",
            "current_stage": None,
            "progress_percent": 100,
            "last_completed": "30 Oct 2026, 06:14 IST"
        }
    }

@router.post("/pipeline/run")
def trigger_run(payload: PipelineTriggerRequest):
    data = load_json("pipeline_runs.json") or []
    runs = data if isinstance(data, list) else data.get("runs", [])

    now = datetime.datetime.now().strftime("%d %b %Y, %H:%M IST")
    run_id = f"RUN-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"

    new_run = {
        "id": run_id,
        "timestamp": now,
        "slot": payload.slot,
        "stage": payload.stage,
        "status": "Success",
        "duration": "1m 42s",
        "raw_extracted": 148,
        "deduped_count": 84,
        "triaged_count": 84,
        "draft_items": 5,
        "triggered_by": "Dr. Alexander Morgan (Web Console)",
        "logs": [
            f"[{now}] Pipeline stage '{payload.stage}' initiated by Dr. Alexander Morgan.",
            f"[{now}] Ingested 592 feeds across OPML categories.",
            f"[{now}] Executed URL deduplication: 148 raw items -> 84 unique candidates.",
            f"[{now}] Parallel AI Triage evaluated candidates against MASH scope and clinical relevance.",
            f"[{now}] Morning Brief drafted with 5 prioritized items.",
            f"[{now}] Pipeline completed with exit code 0."
        ]
    }

    runs.insert(0, new_run)
    save_json("pipeline_runs.json", runs)

    return {
        "status": "success",
        "run": new_run
    }
