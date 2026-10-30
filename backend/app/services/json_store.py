import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional
from app.config import DATA_DIR

_locks: Dict[str, threading.Lock] = {}
_cache: Dict[str, Any] = {}
_global_lock = threading.Lock()


def _get_lock(filename: str) -> threading.Lock:
    with _global_lock:
        if filename not in _locks:
            _locks[filename] = threading.Lock()
        return _locks[filename]


def load_json(filename: str) -> Any:
    """Load JSON from disk with caching and thread-safety."""
    lock = _get_lock(filename)
    with lock:
        filepath = DATA_DIR / filename
        if not filepath.exists():
            return None
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            _cache[filename] = data
            return data


def save_json(filename: str, data: Any) -> bool:
    """Atomically write JSON to disk with thread-safety."""
    lock = _get_lock(filename)
    with lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        filepath = DATA_DIR / filename
        tmp_filepath = DATA_DIR / f"{filename}.tmp"

        with open(tmp_filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        # Atomic replace
        os.replace(tmp_filepath, filepath)
        _cache[filename] = data
        return True
