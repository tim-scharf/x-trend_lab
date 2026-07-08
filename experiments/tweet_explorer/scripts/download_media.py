from __future__ import annotations

import argparse
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from config import MAX_MEDIA_DOWNLOAD_BYTES
from helper import read_json, write_json


def append_media_download(
    history_path: Path,
    *,
    max_bytes: int = MAX_MEDIA_DOWNLOAD_BYTES,
) -> dict[str, Any]:
    history = read_json(history_path)
    run_dir = history_path.parent
    result = download_probe_media(history, run_dir=run_dir, max_bytes=max_bytes)
    now = datetime.now(timezone.utc).isoformat()
    sequence = int(history.get("next_sequence") or len(history.get("steps", [])))
    step = {
        "sequence": sequence,
        "kind": "media_download",
        "created_at": now,
        "status": result["status"],
        "summary": result["summary"],
        "manifest_path": result["manifest_path"],
        "items": result["items"],
    }
    history.setdefault("steps", []).append(step)
    history["next_sequence"] = sequence + 1
    history["updated_at"] = now
    write_json(history_path, history)
    return step


def download_probe_media(
    history: dict[str, Any],
    *,
    run_dir: Path,
    max_bytes: int = MAX_MEDIA_DOWNLOAD_BYTES,
) -> dict[str, Any]:
    media_items = probe_media(history)
    media_dir = run_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    results = []

    for index, media in enumerate(media_items):
        result = download_media_item(media, media_dir=media_dir, index=index, max_bytes=max_bytes)
        results.append(result)

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "max_bytes": max_bytes,
        "items": results,
    }
    manifest_path = media_dir / "media_manifest.json"
    write_json(manifest_path, manifest)
    failures = [item for item in results if item.get("status") != "complete"]
    status = "complete" if not failures else "partial_failure"
    if not results:
        status = "no_media"
    return {
        "status": status,
        "summary": {
            "media_count": len(media_items),
            "downloaded_count": len([item for item in results if item.get("status") == "complete"]),
            "failed_count": len(failures),
            "total_bytes": sum(int(item.get("bytes") or 0) for item in results),
        },
        "manifest_path": str(manifest_path),
        "items": results,
    }


def probe_media(history: dict[str, Any]) -> list[dict[str, Any]]:
    for step in history.get("steps", []):
        if step.get("kind") == "probe":
            media = step.get("media") or []
            if media:
                return [item for item in media if isinstance(item, dict)]
            response_media = ((step.get("response") or {}).get("includes") or {}).get("media") or []
            return [item for item in response_media if isinstance(item, dict)]
    return []


def download_media_item(
    media: dict[str, Any],
    *,
    media_dir: Path,
    index: int,
    max_bytes: int,
) -> dict[str, Any]:
    url = best_media_url(media)
    media_key = str(media.get("media_key") or f"media_{index}")
    base = {
        "media_key": media_key,
        "type": media.get("type"),
        "source_url": url,
    }
    if not url:
        return {**base, "status": "skipped", "reason": "No downloadable media URL found."}

    try:
        with requests.get(url, stream=True, timeout=30) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            path = media_dir / media_filename(media_key, url, content_type)
            bytes_written = write_stream_limited(response, path, max_bytes=max_bytes)
    except Exception as exc:
        return {**base, "status": "failed", "error": str(exc)}

    return {
        **base,
        "status": "complete",
        "path": str(path),
        "bytes": bytes_written,
        "content_type": content_type,
    }


def best_media_url(media: dict[str, Any]) -> str | None:
    if media.get("url"):
        return str(media["url"])

    variants = media.get("variants")
    if isinstance(variants, list):
        playable = [
            item
            for item in variants
            if isinstance(item, dict) and item.get("url") and str(item.get("content_type", "")).startswith("video/")
        ]
        if playable:
            playable.sort(key=lambda item: int(item.get("bit_rate") or 0), reverse=True)
            return str(playable[0]["url"])
        for item in variants:
            if isinstance(item, dict) and item.get("url"):
                return str(item["url"])

    if media.get("preview_image_url"):
        return str(media["preview_image_url"])
    return None


def write_stream_limited(response: requests.Response, path: Path, *, max_bytes: int) -> int:
    written = 0
    with path.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=1024 * 128):
            if not chunk:
                continue
            written += len(chunk)
            if written > max_bytes:
                handle.close()
                path.unlink(missing_ok=True)
                raise ValueError(f"Media download exceeded byte limit ({max_bytes}).")
            handle.write(chunk)
    return written


def media_filename(media_key: str, url: str, content_type: str) -> str:
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix
    if not suffix:
        suffix = mimetypes.guess_extension(content_type.split(";")[0].strip()) or ".bin"
    safe_key = "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in media_key)
    return f"{safe_key}{suffix}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download probe media assets for a tweet_explorer run.")
    parser.add_argument("history_json", type=Path, help="Path to tweet_explorer history.json.")
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=MAX_MEDIA_DOWNLOAD_BYTES,
        help=f"Maximum bytes per media download. Default: {MAX_MEDIA_DOWNLOAD_BYTES}.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    step = append_media_download(args.history_json, max_bytes=args.max_bytes)
    print(step["summary"])


if __name__ == "__main__":
    main()
