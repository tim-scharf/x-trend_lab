from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import EXECUTABLE_TWEET_FIELDS, MEDIA_FIELDS, USER_FIELDS, X_API_BASE_URL
from helper import read_json, write_json


def append_executable_plan(history_path: Path) -> dict[str, Any]:
    history = read_json(history_path)
    compiled = compile_latest_plan(history)
    now = datetime.now(timezone.utc).isoformat()
    sequence = int(history.get("next_sequence") or len(history.get("steps", [])))

    step = {
        "sequence": sequence,
        "kind": "create_plan",
        "created_at": now,
        "status": "complete",
        "source_plan_sequence": compiled["source_plan_sequence"],
        "summary": compiled["summary"],
        "executable_plan": compiled["executable_plan"],
        "blocked_actions": compiled["blocked_actions"],
    }
    history.setdefault("steps", []).append(step)
    history["next_sequence"] = sequence + 1
    history["updated_at"] = now
    history["state"] = "executable_plan_created"
    write_json(history_path, history)
    return step


def compile_latest_plan(history: dict[str, Any]) -> dict[str, Any]:
    plan_step = latest_step(history, "plan")
    probe_step = latest_step(history, "probe")
    plan = plan_step["plan"]
    tweet_id = str(history["tweet_id"])
    author_id = str((probe_step.get("tweet") or {}).get("author_id") or "")
    conversation_id = str(
        (probe_step.get("tweet") or {}).get("conversation_id") or tweet_id
    )
    media_keys = probe_media_keys(probe_step)

    executable_phases = []
    blocked_actions = []
    executable_count = 0

    for phase_index, phase in enumerate(plan.get("phases", [])):
        compiled_actions = []
        for action_index, action in enumerate(phase.get("api_actions", [])):
            compiled = compile_action(
                tweet_id=tweet_id,
                author_id=author_id,
                conversation_id=conversation_id,
                media_keys=media_keys,
                action=action,
            )
            compiled["phase_index"] = phase_index
            compiled["action_index"] = action_index
            if compiled["status"] == "executable":
                executable_count += 1
                compiled_actions.append(compiled)
            else:
                blocked_actions.append(compiled)

        executable_phases.append(
            {
                "phase_index": phase_index,
                "name": phase.get("name"),
                "goal": phase.get("goal"),
                "decision_rule": phase.get("decision_rule"),
                "expected_evidence": phase.get("expected_evidence"),
                "actions": compiled_actions,
            }
        )

    return {
        "source_plan_sequence": plan_step["sequence"],
        "summary": {
            "phase_count": len(executable_phases),
            "executable_action_count": executable_count,
            "blocked_action_count": len(blocked_actions),
        },
        "executable_plan": {
            "tweet_id": tweet_id,
            "phases": executable_phases,
        },
        "blocked_actions": blocked_actions,
    }


def latest_step(history: dict[str, Any], kind: str) -> dict[str, Any]:
    for step in reversed(history.get("steps", [])):
        if step.get("kind") == kind:
            return step
    raise ValueError(f"No {kind!r} step found in history.")


def compile_action(
    *,
    tweet_id: str,
    author_id: str,
    conversation_id: str,
    media_keys: list[str],
    action: dict[str, Any],
) -> dict[str, Any]:
    kind = classify_action(action)
    cap = resource_cap(action)
    base = {
        "kind": kind,
        "status": "blocked",
        "source_action": action,
        "resource_cap": cap,
    }

    if kind == "quote_tweets":
        return {
            **base,
            "status": "executable",
            "request": get(
                f"/2/tweets/{tweet_id}/quote_tweets",
                {
                    "max_results": clamp(cap or 10, 10, 100),
                    "tweet.fields": ",".join(EXECUTABLE_TWEET_FIELDS),
                    **pagination_param(action),
                },
            ),
        }

    if kind == "retweeted_by":
        return {
            **base,
            "status": "executable",
            "request": get(
                f"/2/tweets/{tweet_id}/retweeted_by",
                {
                    "max_results": clamp(cap or 1, 1, 100),
                    "user.fields": ",".join(USER_FIELDS),
                    **pagination_param(action),
                },
            ),
        }

    if kind == "liking_users":
        return {
            **base,
            "status": "executable",
            "request": get(
                f"/2/tweets/{tweet_id}/liking_users",
                {
                    "max_results": clamp(cap or 1, 1, 100),
                    "user.fields": ",".join(USER_FIELDS),
                    **pagination_param(action),
                },
            ),
        }

    if kind == "search_recent":
        return {
            **base,
            "status": "executable",
            "request": get(
                "/2/tweets/search/recent",
                {
                    "query": search_query(tweet_id, conversation_id, action),
                    "max_results": clamp(cap or 10, 10, 100),
                    "sort_order": "relevancy",
                    "tweet.fields": ",".join(EXECUTABLE_TWEET_FIELDS),
                    **pagination_param(action),
                },
            ),
        }

    if kind == "counts_recent":
        return {
            **base,
            "status": "executable",
            "request": get(
                "/2/tweets/counts/recent",
                {
                    "query": counts_query(conversation_id, action),
                    "granularity": "hour",
                },
            ),
        }

    if kind == "usage_tweets":
        return {
            **base,
            "status": "executable",
            "request": get(
                "/2/usage/tweets",
                {
                    "days": 1,
                    "usage.fields": "daily_project_usage,project_usage",
                },
            ),
        }

    if kind == "user_tweets":
        if not author_id:
            return {**base, "reason": "Cannot compile user timeline without author_id."}
        return {
            **base,
            "status": "executable",
            "request": get(
                f"/2/users/{author_id}/tweets",
                {
                    "max_results": clamp(cap or 5, 5, 100),
                    "tweet.fields": ",".join(EXECUTABLE_TWEET_FIELDS),
                    **pagination_param(action),
                },
            ),
        }

    if kind == "media":
        media_key = choose_media_key(action, media_keys)
        if media_key:
            return {
                **base,
                "status": "executable",
                "media_key": media_key,
                "request": get(
                    f"/2/media/{media_key}",
                    {
                        "media.fields": ",".join(MEDIA_FIELDS),
                    },
                ),
            }
        return {
            **base,
            "reason": "No media_key found in probe attachments or planner action text.",
        }

    return {**base, "reason": "Unsupported or unrecognized planned action."}


def classify_action(action: dict[str, Any]) -> str:
    text = " ".join(
        str(action.get(key, ""))
        for key in ["endpoint_or_method", "purpose", "cost_basis"]
    ).lower()
    endpoint = str(action.get("endpoint_or_method", "")).lower()

    if "/2/media" in endpoint or "media metadata" in text:
        return "media"
    if "/2/usage/tweets" in endpoint:
        return "usage_tweets"
    if "/2/tweets/counts/recent" in endpoint:
        return "counts_recent"
    if "/2/tweets/search/recent" in endpoint or "conversation_id" in text:
        return "search_recent"
    if "/quote_tweets" in endpoint:
        return "quote_tweets"
    if "/retweeted_by" in endpoint:
        return "retweeted_by"
    if "/liking_users" in endpoint:
        return "liking_users"
    if "/2/users/:id/tweets" in endpoint or "/2/users/{id}/tweets" in endpoint:
        return "user_tweets"
    return "unsupported"


def resource_cap(action: dict[str, Any]) -> int:
    try:
        return int(action.get("resource_cap") or 0)
    except Exception:
        return 0


def clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, maximum))


def get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "method": "GET",
        "url": f"{X_API_BASE_URL}{path}",
        "params": params,
    }


def search_query(tweet_id: str, conversation_id: str, action: dict[str, Any]) -> str:
    if action.get("query"):
        return normalize_query(action["query"])

    text = " ".join(str(action.get(key, "")) for key in ["purpose", "endpoint_or_method"]).lower()
    if "exact tweet url" in text or "tweet id" in text or "mentions" in text:
        return f'url:"{tweet_id}" OR "{tweet_id}"'
    return f"conversation_id:{conversation_id} -is:retweet"


def counts_query(conversation_id: str, action: dict[str, Any]) -> str:
    if action.get("query"):
        return normalize_query(action["query"])
    return f"conversation_id:{conversation_id} -is:retweet"


def normalize_query(query: Any) -> str:
    cleaned = " ".join(str(query).split())
    if not cleaned:
        raise ValueError("Planner supplied an empty query.")
    return cleaned[:512]


def pagination_param(action: dict[str, Any]) -> dict[str, str]:
    token = action.get("pagination_token")
    if token:
        return {"pagination_token": str(token)}
    return {}


def probe_media_keys(probe_step: dict[str, Any]) -> list[str]:
    attachments = (probe_step.get("tweet") or {}).get("attachments") or {}
    media_keys = attachments.get("media_keys") or []
    return [str(key) for key in media_keys if key]


def choose_media_key(action: dict[str, Any], media_keys: list[str]) -> str | None:
    action_text = " ".join(
        str(action.get(key, ""))
        for key in ["endpoint_or_method", "purpose", "cost_basis"]
    )
    for match in re.findall(r"\b\d+_[A-Za-z0-9]+\b", action_text):
        if match in media_keys or not media_keys:
            return match
    if len(media_keys) == 1:
        return media_keys[0]
    return media_keys[0] if media_keys else None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compile an LLM plan into executable X request specs.")
    parser.add_argument("history_json", type=Path, help="Path to tweet_explorer history.json.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    step = append_executable_plan(args.history_json)
    print(step["summary"])


if __name__ == "__main__":
    main()
