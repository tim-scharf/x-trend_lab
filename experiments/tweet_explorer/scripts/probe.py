from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[3]
X_TWEET_LOOKUP_URL = "https://api.x.com/2/tweets/{tweet_id}"
X_POST_READ_COST_USD = 0.005
X_USER_READ_COST_USD = 0.010

TWEET_FIELDS = [
    "id",
    "text",
    "author_id",
    "created_at",
    "conversation_id",
    "public_metrics",
    "referenced_tweets",
    "entities",
    "context_annotations",
    "lang",
    "possibly_sensitive",
    "attachments",
]

USER_FIELDS = [
    "id",
    "name",
    "username",
    "created_at",
    "description",
    "verified",
    "verified_type",
    "protected",
    "public_metrics",
    "location",
    "profile_image_url",
]


def validate_tweet_id(tweet_id: str) -> None:
    if not re.fullmatch(r"\d{5,30}", tweet_id):
        raise ValueError("tweet_id should be a numeric X Post/Tweet ID.")


def initial_probe(tweet_id: str, bearer_token: str | None = None) -> dict[str, Any]:
    validate_tweet_id(tweet_id)
    token = bearer_token or os.getenv("X_BEARER_TOKEN")
    if not token:
        raise RuntimeError("Missing X_BEARER_TOKEN in .env")

    request = probe_request(tweet_id)
    response = requests.get(
        request["url"],
        headers={"Authorization": f"Bearer {token}"},
        params=request["params"],
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()

    return {
        "tweet_id": tweet_id,
        "request": request,
        "payload": payload,
        "tweet": payload.get("data"),
        "author": first_author(payload),
        "estimated_cost_usd": estimate_probe_cost_usd(payload),
        "cost_basis": {
            "post_read_usd": X_POST_READ_COST_USD,
            "user_read_usd": X_USER_READ_COST_USD,
            "note": "Initial probe fetches one Post plus author expansion when available.",
        },
    }


def probe_request(tweet_id: str) -> dict[str, Any]:
    return {
        "method": "GET",
        "url": X_TWEET_LOOKUP_URL.format(tweet_id=tweet_id),
        "params": {
            "tweet.fields": ",".join(TWEET_FIELDS),
            "expansions": "author_id",
            "user.fields": ",".join(USER_FIELDS),
        },
    }


def first_author(payload: dict[str, Any]) -> dict[str, Any] | None:
    users = (payload.get("includes") or {}).get("users") or []
    if users and isinstance(users[0], dict):
        return users[0]
    return None


def estimate_probe_cost_usd(probe_payload: dict[str, Any]) -> float:
    cost = 0.0
    if probe_payload.get("data"):
        cost += X_POST_READ_COST_USD
    includes = probe_payload.get("includes") or {}
    users = includes.get("users") or []
    if isinstance(users, list):
        cost += len(users) * X_USER_READ_COST_USD
    tweets = includes.get("tweets") or []
    if isinstance(tweets, list):
        cost += len(tweets) * X_POST_READ_COST_USD
    return round(cost, 6)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the initial X Post probe.")
    parser.add_argument("tweet_id", help="Exact X Post/Tweet ID to hydrate.")
    return parser.parse_args()


def main() -> None:
    load_dotenv(ROOT / ".env")
    args = parse_args()
    print(json.dumps(initial_probe(args.tweet_id), indent=2, default=str))


if __name__ == "__main__":
    main()
