from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def compact_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def summarize_probe(probe: dict[str, Any]) -> dict[str, Any]:
    tweet = compact_or_empty(probe.get("tweet"))
    author = compact_or_empty(probe.get("author"))
    media = probe.get("media") if isinstance(probe.get("media"), list) else []
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
        "media": [summarize_media(item) for item in media],
        "possibly_sensitive": tweet.get("possibly_sensitive"),
        "lang": tweet.get("lang"),
    }


def summarize_media(media: dict[str, Any]) -> dict[str, Any]:
    return {
        key: media.get(key)
        for key in [
            "media_key",
            "type",
            "url",
            "preview_image_url",
            "alt_text",
            "duration_ms",
            "height",
            "width",
            "public_metrics",
        ]
        if key in media
    }


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_text(path: Path, max_chars: int | None = None) -> str:
    text = path.read_text(encoding="utf-8")
    return text[:max_chars] if max_chars else text
