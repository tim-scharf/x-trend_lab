from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from config import ROOT
from create_plan import latest_step
from helper import read_json, write_json


def append_execution(
    history_path: Path,
    *,
    dry_run: bool = False,
    force: bool = False,
    max_actions: int | None = None,
) -> dict[str, Any]:
    history = read_json(history_path)
    execution = execute_latest_verified_plan(
        history,
        dry_run=dry_run,
        force=force,
        max_actions=max_actions,
    )
    now = datetime.now(timezone.utc).isoformat()
    sequence = int(history.get("next_sequence") or len(history.get("steps", [])))

    step = {
        "sequence": sequence,
        "kind": "execute_plan",
        "created_at": now,
        "status": execution["status"],
        "dry_run": dry_run,
        "source_cost_verification_sequence": execution[
            "source_cost_verification_sequence"
        ],
        "summary": execution["summary"],
        "results": execution["results"],
    }
    history.setdefault("steps", []).append(step)
    history["next_sequence"] = sequence + 1
    history["updated_at"] = now
    history["state"] = "execution_dry_run" if dry_run else execution["state"]

    if not dry_run:
        apply_execution_budget(history, execution)

    write_json(history_path, history)
    return step


def execute_latest_verified_plan(
    history: dict[str, Any],
    *,
    dry_run: bool = False,
    force: bool = False,
    max_actions: int | None = None,
) -> dict[str, Any]:
    verification_step = latest_step(history, "cost_verification")
    source_sequence = verification_step["sequence"]
    ensure_safe_to_execute(history, verification_step, force=force)

    token = None
    if not dry_run:
        token = os.getenv("X_BEARER_TOKEN")
        if not token:
            raise RuntimeError("Missing X_BEARER_TOKEN in .env")

    results = []
    seen_requests = prior_request_signatures(history)
    attempted = 0
    succeeded = 0
    failed = 0
    skipped = 0
    estimated_attempted_cost = 0.0

    for phase in verification_step.get("cost_estimate", {}).get("phases", []):
        phase_result = {
            "phase_index": phase.get("phase_index"),
            "name": phase.get("name"),
            "actions": [],
        }
        for action in phase.get("actions", []):
            signature = request_signature(action.get("request"))
            if signature in seen_requests and not force:
                skipped += 1
                phase_result["actions"].append(
                    skipped_action(action, "request already executed")
                )
                continue
            if max_actions is not None and attempted >= max_actions:
                skipped += 1
                phase_result["actions"].append(skipped_action(action, "max_actions reached"))
                continue

            attempted += 1
            verified_cost = action.get("verified_cost") or {}
            estimated_attempted_cost += float(
                verified_cost.get("estimated_cost_usd") or 0.0
            )

            if dry_run:
                succeeded += 1
                phase_result["actions"].append(dry_run_action(action))
                continue

            action_result = execute_action(action, token=token)
            if action_result["status"] == "complete":
                succeeded += 1
                seen_requests.add(signature)
            else:
                failed += 1
            phase_result["actions"].append(action_result)

        results.append(phase_result)

    estimated_attempted_cost = round(estimated_attempted_cost, 6)
    status = "complete" if failed == 0 else "partial_failure"
    if dry_run:
        status = "dry_run"

    return {
        "source_cost_verification_sequence": source_sequence,
        "status": status,
        "state": "executed" if failed == 0 else "execution_partial_failure",
        "summary": {
            "attempted_action_count": attempted,
            "succeeded_action_count": succeeded,
            "failed_action_count": failed,
            "skipped_action_count": skipped,
            "estimated_attempted_x_cost_usd": estimated_attempted_cost,
        },
        "results": results,
    }


def ensure_safe_to_execute(
    history: dict[str, Any],
    verification_step: dict[str, Any],
    *,
    force: bool,
) -> None:
    summary = verification_step.get("summary") or {}
    if not summary.get("fits_remaining_x_budget") and not force:
        raise RuntimeError("Verified plan does not fit remaining X budget. Use --force to override.")

    source_sequence = verification_step.get("sequence")
    existing = [
        step
        for step in history.get("steps", [])
        if step.get("kind") == "execute_plan"
        and step.get("source_cost_verification_sequence") == source_sequence
        and not step.get("dry_run")
    ]
    if existing and not force:
        raise RuntimeError(
            "This cost-verified plan has already been executed. Use --force to run it again."
        )


def execute_action(action: dict[str, Any], *, token: str) -> dict[str, Any]:
    request = action.get("request") or {}
    method = str(request.get("method") or "GET").upper()
    url = str(request.get("url") or "")
    params = request.get("params") or {}
    started_at = datetime.now(timezone.utc).isoformat()

    try:
        response = requests.request(
            method,
            url,
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=30,
        )
        payload = response_json_or_text(response)
    except requests.RequestException as exc:
        return {
            "kind": action.get("kind"),
            "status": "request_error",
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "request": request,
            "verified_cost": action.get("verified_cost"),
            "error": str(exc),
        }

    status = "complete" if 200 <= response.status_code < 300 else "http_error"
    return {
        "kind": action.get("kind"),
        "status": status,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "request": request,
        "verified_cost": action.get("verified_cost"),
        "http_status": response.status_code,
        "resource_counts": resource_counts(payload),
        "response": payload,
    }


def response_json_or_text(response: requests.Response) -> dict[str, Any] | str:
    try:
        return response.json()
    except ValueError:
        return response.text


def resource_counts(payload: dict[str, Any] | str) -> dict[str, int]:
    if not isinstance(payload, dict):
        return {"data": 0, "users": 0, "tweets": 0, "media": 0, "errors": 0}

    data = payload.get("data")
    includes = payload.get("includes") or {}
    return {
        "data": len(data) if isinstance(data, list) else int(isinstance(data, dict)),
        "users": len(includes.get("users") or []),
        "tweets": len(includes.get("tweets") or []),
        "media": len(includes.get("media") or []),
        "errors": len(payload.get("errors") or []),
    }


def dry_run_action(action: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": action.get("kind"),
        "status": "dry_run",
        "request": action.get("request"),
        "verified_cost": action.get("verified_cost"),
    }


def skipped_action(action: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "kind": action.get("kind"),
        "status": "skipped",
        "reason": reason,
        "request": action.get("request"),
        "verified_cost": action.get("verified_cost"),
    }


def prior_request_signatures(history: dict[str, Any]) -> set[tuple[str, str, str]]:
    signatures = set()
    for step in history.get("steps", []):
        if step.get("kind") != "execute_plan" or step.get("dry_run"):
            continue
        for phase in step.get("results", []):
            for action in phase.get("actions", []):
                if action.get("status") in {"complete", "http_error", "request_error"}:
                    signatures.add(request_signature(action.get("request")))
    return signatures


def request_signature(request: dict[str, Any] | None) -> tuple[str, str, str]:
    request = request or {}
    method = str(request.get("method") or "GET").upper()
    url = str(request.get("url") or "")
    params = request.get("params") or {}
    params_key = "&".join(f"{key}={params[key]}" for key in sorted(params))
    return method, url, params_key


def apply_execution_budget(history: dict[str, Any], execution: dict[str, Any]) -> None:
    x_budget = history.setdefault("budget", {}).setdefault("x", {})
    spent = float(x_budget.get("spent_usd") or 0.0)
    budget = float(x_budget.get("budget_usd") or 0.0)
    attempted = float(execution["summary"].get("estimated_attempted_x_cost_usd") or 0.0)
    new_spent = round(spent + attempted, 6)
    x_budget["spent_usd"] = new_spent
    x_budget["remaining_usd"] = round(budget - new_spent, 6)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Execute the latest verified X API plan.")
    parser.add_argument("history_json", type=Path, help="Path to tweet_explorer history.json.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Append the execution step without calling X.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Override budget/duplicate-execution safety checks.",
    )
    parser.add_argument(
        "--max-actions",
        type=int,
        default=None,
        help="Execute at most this many actions from the verified plan.",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv(ROOT / ".env")
    args = parse_args()
    step = append_execution(
        args.history_json,
        dry_run=args.dry_run,
        force=args.force,
        max_actions=args.max_actions,
    )
    print(step["summary"])


if __name__ == "__main__":
    main()
