from fastapi import APIRouter, HTTPException
from typing import Dict, Any
from app.services.json_store import load_json, save_json

router = APIRouter()

@router.get("/morning-update")
def get_morning_update():
    news_data = load_json("news_pipeline_data.json") or {}
    mail_settings = load_json("mail_settings.json") or {}
    draft = news_data.get("morning_draft", {})

    return {
        "status": "ready",
        "draft": draft,
        "mail_settings": mail_settings
    }

@router.put("/morning-update")
def save_morning_update(payload: Dict[str, Any]):
    news_data = load_json("news_pipeline_data.json") or {}
    if "draft" in payload:
        news_data["morning_draft"] = payload["draft"]
    else:
        news_data["morning_draft"] = payload

    save_json("news_pipeline_data.json", news_data)
    return {"status": "success", "message": "Morning draft updated successfully"}
