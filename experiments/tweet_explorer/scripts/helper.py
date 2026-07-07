from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def compact_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def summarize_probe(probe: dict[str, Any]) -> dict[str, Any]:
    tweet = compact_or_empty(probe.get("tweet"))
    author = compact_or_empty(probe.get("author"))
    return {
        "tweet_id": probe.get("tweet_id"),
        "text": tweet.get("text"),
        "created_at": tweet.get("created_at"),
        "conversation_id": tweet.get("conversation_id"),
        "author_id": tweet.get("author_id"),
        "author_username": author.get("username"),
        "author_name": author.get("name"),
        "tweet_metrics": tweet.get("public_metrics") or {},
        "author_metrics": author.get("public_metrics") or {},
        "possibly_sensitive": tweet.get("possibly_sensitive"),
        "lang": tweet.get("lang"),
    }


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path
