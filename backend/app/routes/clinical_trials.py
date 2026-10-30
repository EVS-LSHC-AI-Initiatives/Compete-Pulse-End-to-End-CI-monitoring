from fastapi import APIRouter, Query
from typing import Optional
from app.services.json_store import load_json

router = APIRouter()

@router.get("/trials")
def get_trials(
    phase: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None)
):
    data = load_json("clinical_trials_data.json") or {}
    profiles = data.get("trial_profiles", [])

    filtered = profiles
    if phase and phase != "All":
        filtered = [t for t in filtered if phase.lower() in t.get("phase", "").lower()]
    if status and status != "All":
        filtered = [t for t in filtered if status.lower() in t.get("status", "").lower()]
    if search:
        s = search.lower()
        filtered = [
            t for t in filtered
            if s in t.get("nct_id", "").lower()
            or s in t.get("title", "").lower()
            or s in t.get("sponsor", "").lower()
            or s in t.get("drug", "").lower()
        ]

    return {
        "total": len(filtered),
        "trials": filtered,
        "search_queries": data.get("search_queries", []),
        "change_detection_diffs": data.get("change_detection_diffs", []),
        "metadata": data.get("metadata", {})
    }

@router.get("/trials/diffs")
def get_trial_diffs():
    data = load_json("clinical_trials_data.json") or {}
    return {
        "diffs": data.get("change_detection_diffs", []),
        "count": len(data.get("change_detection_diffs", []))
    }

@router.get("/trials/queries")
def get_trial_queries(
    search: Optional[str] = Query(None),
    category: Optional[str] = Query(None)
):
    data = load_json("clinical_trials_data.json") or {}
    queries = data.get("search_queries", [])

    filtered = queries
    if search:
        s = search.lower()
        filtered = [
            q for q in filtered
            if s in q.get("query", "").lower() or s in q.get("tag", "").lower()
        ]
    if category and category != "All":
        filtered = [q for q in filtered if q.get("category") == category]

    return {
        "total": len(filtered),
        "queries": filtered
    }
