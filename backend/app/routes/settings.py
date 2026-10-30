from fastapi import APIRouter, HTTPException
from typing import Dict, Any
from app.services.json_store import load_json, save_json

router = APIRouter()

@router.get("/settings")
def get_settings():
    settings = load_json("mail_settings.json") or {}
    return {
        "status": "success",
        "settings": settings
    }

@router.put("/settings")
def update_settings(payload: Dict[str, Any]):
    new_settings = payload.get("settings", payload)
    save_json("mail_settings.json", new_settings)
    return {
        "status": "success",
        "message": "Mail and pipeline settings saved successfully",
        "settings": new_settings
    }
