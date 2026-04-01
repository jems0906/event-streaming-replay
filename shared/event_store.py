import json
import os
from typing import Any, Dict, List


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _topic_path(store_dir: str, topic: str) -> str:
    safe_topic = topic.replace("/", "_").replace("\\", "_")
    return os.path.join(store_dir, f"{safe_topic}.jsonl")


def append_topic_event(store_dir: str, topic: str, payload: Dict[str, Any]) -> None:
    _ensure_dir(store_dir)
    file_path = _topic_path(store_dir, topic)
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=True) + "\n")


def read_topic_events(store_dir: str, topic: str) -> List[Dict[str, Any]]:
    file_path = _topic_path(store_dir, topic)
    if not os.path.exists(file_path):
        return []

    results: List[Dict[str, Any]] = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return results