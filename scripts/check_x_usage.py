from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.append(str(Path(__file__).resolve().parents[1]))
from config import X_COUNTS_COST_PER_REQUEST  # noqa: E402


USAGE_URL = "https://api.x.com/2/usage/tweets"


def get_x_usage(days: int = 30) -> dict:
    load_dotenv()
    bearer_token = os.getenv("X_BEARER_TOKEN")
    if not bearer_token:
        raise RuntimeError("Missing X_BEARER_TOKEN in .env")

    response = requests.get(
        USAGE_URL,
        headers={"Authorization": f"Bearer {bearer_token}"},
        params={
            "days": days,
            "usage.fields": ",".join(
                [
                    "cap_reset_day",
                    "daily_client_app_usage",
                    "daily_project_usage",
                    "project_cap",
                    "project_id",
                    "project_usage",
                ]
            ),
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data", {})

    project_cap = data.get("project_cap")
    project_usage = data.get("project_usage")
    remaining = None
    pct_used = None
    estimated_spend = None
    if project_cap is not None and project_usage is not None:
        remaining = max(int(project_cap) - int(project_usage), 0)
        pct_used = (float(project_usage) / float(project_cap)) if float(project_cap) > 0 else None
        estimated_spend = int(project_usage) * X_COUNTS_COST_PER_REQUEST

    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "days": days,
        "project_id": data.get("project_id"),
        "project_cap": project_cap,
        "project_usage": project_usage,
        "project_remaining": remaining,
        "project_pct_used": round(pct_used, 4) if pct_used is not None else None,
        "estimated_spend_usd": round(estimated_spend, 4) if estimated_spend is not None else None,
        "estimated_cost_per_usage_unit_usd": X_COUNTS_COST_PER_REQUEST,
        "cap_reset_day": data.get("cap_reset_day"),
        "raw": payload,
    }


def main() -> None:
    days = 30
    if len(sys.argv) >= 2:
        days = int(sys.argv[1])
    days = max(1, min(days, 90))

    usage = get_x_usage(days=days)
    print(f"Checked at:       {usage['checked_at']}")
    print(f"Window days:      {usage['days']}")
    print(f"Project ID:       {usage.get('project_id') or 'n/a'}")
    print(f"Project cap:      {usage.get('project_cap') if usage.get('project_cap') is not None else 'n/a'}")
    print(f"Project usage:    {usage.get('project_usage') if usage.get('project_usage') is not None else 'n/a'}")
    print(f"Project remaining:{usage.get('project_remaining') if usage.get('project_remaining') is not None else 'n/a'}")
    print(f"Est. spend:       ${usage['estimated_spend_usd']:.2f}" if usage.get("estimated_spend_usd") is not None else "Est. spend:       n/a")
    print(f"Percent used:     {usage['project_pct_used']:.1%}" if usage.get("project_pct_used") is not None else "Percent used:     n/a")
    print(f"Cap reset day:    {usage.get('cap_reset_day') if usage.get('cap_reset_day') is not None else 'n/a'}")
    print()
    print(json.dumps(usage, indent=2, default=str))


if __name__ == "__main__":
    main()
