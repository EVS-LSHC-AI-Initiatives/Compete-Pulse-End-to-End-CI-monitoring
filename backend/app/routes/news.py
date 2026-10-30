from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from pydantic import BaseModel
from app.services.json_store import load_json, save_json

router = APIRouter()

class ArticleUpdate(BaseModel):
    disposition: Optional[str] = None
    priority: Optional[str] = None
    notes: Optional[str] = None
    selected_for_draft: Optional[bool] = None

@router.get("/news")
def get_news(
    search: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    scope: Optional[str] = Query(None),
    disposition: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200)
):
    data = load_json("news_pipeline_data.json") or {}
    items = data.get("triaged_items", [])

    filtered = items

    if search:
        s = search.lower()
        filtered = [
            item for item in filtered
            if s in str(item.get("title", "")).lower()
            or s in str(item.get("source", "")).lower()
            or s in str(item.get("summary", "")).lower()
            or s in str(item.get("id", "")).lower()
        ]

    if priority and priority != "All":
        filtered = [item for item in filtered if item.get("priority") == priority]

    if scope and scope != "All":
        filtered = [item for item in filtered if item.get("mash_scope") == scope]

    if disposition and disposition != "All":
        filtered = [item for item in filtered if item.get("disposition") == disposition]

    if category and category != "All":
        filtered = [item for item in filtered if item.get("feed_category") == category]

    total = len(filtered)
    start = (page - 1) * page_size
    end = start + page_size
    paged = filtered[start:end]

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": paged
    }

@router.get("/news/{item_id}")
def get_article(item_id: str):
    data = load_json("news_pipeline_data.json") or {}
    items = data.get("triaged_items", [])
    for item in items:
        if str(item.get("id")) == str(item_id):
            return item
    raise HTTPException(status_code=404, detail=f"Article {item_id} not found")

@router.patch("/news/{item_id}")
def update_article(item_id: str, patch: ArticleUpdate):
    data = load_json("news_pipeline_data.json") or {}
    items = data.get("triaged_items", [])
    found = False

    for item in items:
        if str(item.get("id")) == str(item_id):
            found = True
            if patch.disposition is not None:
                item["disposition"] = patch.disposition
            if patch.priority is not None:
                item["priority"] = patch.priority
            if patch.notes is not None:
                item["reviewer_notes"] = patch.notes
            if patch.selected_for_draft is not None:
                item["selected_for_draft"] = patch.selected_for_draft
            break

    if not found:
        raise HTTPException(status_code=404, detail=f"Article {item_id} not found")

    save_json("news_pipeline_data.json", data)
    return {"status": "success", "id": item_id}
