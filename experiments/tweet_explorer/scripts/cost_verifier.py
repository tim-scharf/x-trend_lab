from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from config import (
    X_COUNTS_RECENT_REQUEST_COST_USD,
    X_LIKE_READ_COST_USD,
    X_MEDIA_READ_FALLBACK_COST_USD,
    X_POST_READ_COST_USD,
    X_USER_READ_COST_USD,
)
from create_plan import latest_step
from helper import read_json, write_json


def append_cost_verification(history_path: Path) -> dict[str, Any]:
    history = read_json(history_path)
    verification = verify_latest_executable_plan(history)
    now = datetime.now(timezone.utc).isoformat()
    sequence = int(history.get("next_sequence") or len(history.get("steps", [])))

    step = {
        "sequence": sequence,
        "kind": "cost_verification",
        "created_at": now,
        "status": "complete",
        "source_create_plan_sequence": verification["source_create_plan_sequence"],
        "summary": verification["summary"],
        "cost_estimate": verification["cost_estimate"],
        "warnings": verification["warnings"],
    }
    history.setdefault("steps", []).append(step)
    history["next_sequence"] = sequence + 1
    history["updated_at"] = now
    history["state"] = "cost_verified"
    write_json(history_path, history)
    return step


def verify_latest_executable_plan(history: dict[str, Any]) -> dict[str, Any]:
    create_plan_step = latest_step(history, "create_plan")
    executable_plan = create_plan_step["executable_plan"]
    budget = history.get("budget", {}).get("x", {})
    remaining_budget = float(budget.get("remaining_usd") or 0)

    phases = []
    warnings = []
    total = 0.0
    action_count = 0

    for phase in executable_plan.get("phases", []):
        verified_actions = []
        phase_total = 0.0
        for action in phase.get("actions", []):
            estimate = estimate_action(action)
            action_count += 1
            phase_total += estimate["estimated_cost_usd"]
            total += estimate["estimated_cost_usd"]
            if estimate["confidence"] != "high":
                warnings.append(
                    {
                        "phase_index": phase.get("phase_index"),
                        "action_index": action.get("action_index"),
                        "kind": action.get("kind"),
                        "message": estimate["warning"],
                    }
                )
            verified_actions.append({**action, "verified_cost": estimate})

        phases.append(
            {
                "phase_index": phase.get("phase_index"),
                "name": phase.get("name"),
                "estimated_cost_usd": round(phase_total, 6),
                "actions": verified_actions,
            }
        )

    total = round(total, 6)
    return {
        "source_create_plan_sequence": create_plan_step["sequence"],
        "summary": {
            "phase_count": len(phases),
            "action_count": action_count,
            "estimated_total_x_cost_usd": total,
            "remaining_x_budget_usd": remaining_budget,
            "fits_remaining_x_budget": total <= remaining_budget,
            "warning_count": len(warnings),
        },
        "cost_estimate": {
            "currency": "USD",
            "basis": "Pure Python estimate from local X API pricing artifacts.",
            "pricing": {
                "post_read_per_resource": X_POST_READ_COST_USD,
                "user_read_per_resource": X_USER_READ_COST_USD,
                "like_read_per_resource": X_LIKE_READ_COST_USD,
                "counts_recent_per_request": X_COUNTS_RECENT_REQUEST_COST_USD,
                "media_read_fallback_per_resource": X_MEDIA_READ_FALLBACK_COST_USD,
            },
            "phases": phases,
            "total_estimated_cost_usd": total,
        },
        "warnings": warnings,
    }


def estimate_action(action: dict[str, Any]) -> dict[str, Any]:
    kind = action.get("kind")
    request = action.get("request") or {}
    params = request.get("params") or {}
    max_results = int(params.get("max_results") or action.get("resource_cap") or 1)
    path = urlparse(str(request.get("url") or "")).path

    if kind in {"quote_tweets", "search_recent", "user_tweets"}:
        return cost(
            resource_type="post",
            units=max_results,
            unit_cost=X_POST_READ_COST_USD,
            confidence="high",
            basis=f"{max_results} Post resources x Posts: Read",
        )

    if kind == "retweeted_by":
        return cost(
            resource_type="user",
            units=max_results,
            unit_cost=X_USER_READ_COST_USD,
            confidence="high",
            basis=f"{max_results} User resources x User: Read",
        )

    if kind == "liking_users":
        return cost(
            resource_type="user",
            units=max_results,
            unit_cost=X_USER_READ_COST_USD,
            confidence="medium",
            basis=f"{max_results} returned User resources x User: Read",
            alternate_basis={
                "resource_type": "like",
                "units": max_results,
                "unit_cost_usd": X_LIKE_READ_COST_USD,
                "estimated_cost_usd": round(max_results * X_LIKE_READ_COST_USD, 6),
                "basis": "If X bills this endpoint as Like: Read instead of User: Read.",
            },
            warning=(
                "Endpoint returns users who liked the post. Verifier uses conservative "
                "User: Read pricing; docs also list cheaper Like: Read pricing."
            ),
        )

    if kind == "counts_recent":
        return cost(
            resource_type="request",
            units=1,
            unit_cost=X_COUNTS_RECENT_REQUEST_COST_USD,
            confidence="high",
            basis="1 request x Counts: Recent",
        )

    if kind == "media":
        return cost(
            resource_type="media",
            units=1,
            unit_cost=X_MEDIA_READ_FALLBACK_COST_USD,
            confidence="low",
            basis="1 media resource x local media-read fallback",
            warning=(
                "Pricing artifacts do not expose a specific Media: Read line. "
                "Using a conservative local fallback."
            ),
        )

    if kind == "usage_tweets" or path == "/2/usage/tweets":
        return cost(
            resource_type="request",
            units=1,
            unit_cost=0.0,
            confidence="low",
            basis="Usage endpoint price is not listed as a billable read in local artifacts.",
            warning="Usage endpoint billing is not explicit in the pricing artifact.",
        )

    return cost(
        resource_type="unknown",
        units=1,
        unit_cost=0.0,
        confidence="unknown",
        basis="No verifier rule for this action kind.",
        warning=f"No cost rule for action kind {kind!r}.",
    )


def cost(
    *,
    resource_type: str,
    units: int,
    unit_cost: float,
    confidence: str,
    basis: str,
    alternate_basis: dict[str, Any] | None = None,
    warning: str | None = None,
) -> dict[str, Any]:
    estimate = {
        "resource_type": resource_type,
        "units": units,
        "unit_cost_usd": unit_cost,
        "estimated_cost_usd": round(units * unit_cost, 6),
        "confidence": confidence,
        "basis": basis,
    }
    if alternate_basis:
        estimate["alternate_basis"] = alternate_basis
    if warning:
        estimate["warning"] = warning
    return estimate


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify X API cost for an executable plan.")
    parser.add_argument("history_json", type=Path, help="Path to tweet_explorer history.json.")
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append the cost verification step to history.json.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.append:
        step = append_cost_verification(args.history_json)
        print(step["summary"])
        return

    history = read_json(args.history_json)
    verification = verify_latest_executable_plan(history)
    print(json.dumps(verification, indent=2, default=str))


if __name__ == "__main__":
    main()
