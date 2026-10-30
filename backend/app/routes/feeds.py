from fastapi import APIRouter, Query
from typing import Optional
from app.services.json_store import load_json

router = APIRouter()

@router.get("/feeds")
def get_feeds(
    category: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500)
):
    data = load_json("feed_health_data.json") or {}
    feeds = data.get("feeds", [])

    filtered = feeds
    if category and category != "All":
        filtered = [f for f in filtered if f.get("category") == category]
    if status and status != "All":
        filtered = [f for f in filtered if f.get("status") == status]
    if search:
        s = search.lower()
        filtered = [
            f for f in filtered
            if s in f.get("title", "").lower()
            or s in f.get("xml_url", "").lower()
            or s in f.get("html_url", "").lower()
        ]

    total = len(filtered)
    start = (page - 1) * page_size
    end = start + page_size

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "summary": data.get("summary", {}),
        "categories": data.get("categories", []),
        "feeds": filtered[start:end]
    }

@router.get("/feeds/stats")
def get_feed_stats():
    data = load_json("feed_health_data.json") or {}
    return {
        "summary": data.get("summary", {}),
        "categories": data.get("categories", [])
    }
