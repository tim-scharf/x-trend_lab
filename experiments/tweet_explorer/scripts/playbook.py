from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from config import DEFAULT_X_BUDGET_USD, MAX_OPENAI_CALLS, ROOT, RUNS_DIR
from cost_verifier import verify_latest_executable_plan
from create_plan import compile_latest_plan
from download_media import download_probe_media
from execute_plan import apply_execution_budget, execute_latest_verified_plan
from helper import compact_or_empty, summarize_probe, write_json
from planner import create_plan as create_openai_plan
from probe import initial_probe
from summarize_history import summarize_history


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the tweet exploration loop.")
    parser.add_argument("tweet_id", help="Exact X Post/Tweet ID to probe.")
    parser.add_argument(
        "--x-budget-usd",
        type=float,
        default=DEFAULT_X_BUDGET_USD,
        help=f"Maximum X API budget for this exploration run. Default: {DEFAULT_X_BUDGET_USD}.",
    )
    parser.add_argument(
        "--max-openai-calls",
        type=int,
        default=MAX_OPENAI_CALLS,
        help=f"Maximum OpenAI planning/reassessment calls allowed. Default: {MAX_OPENAI_CALLS}.",
    )
    parser.add_argument(
        "--no-memory-summary",
        action="store_true",
        help="Disable the final OpenAI history compression step.",
    )
    return parser.parse_args()


def run_initial_probe(
    tweet_id: str,
    x_budget_usd: float,
    max_openai_calls: int,
    *,
    memory_summary: bool = True,
) -> Path:
    if x_budget_usd <= 0:
        raise ValueError("x_budget_usd must be greater than zero.")
    if max_openai_calls < 0:
        raise ValueError("max_openai_calls must be non-negative.")

    probe = initial_probe(tweet_id)
    now = datetime.now(timezone.utc)
    created_at = now.isoformat()
    run_id = now.strftime("%Y%m%d_%H%M%S")
    run_dir = RUNS_DIR / tweet_id / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    x_spent = float(probe["estimated_cost_usd"])
    x_remaining = round(x_budget_usd - x_spent, 6)
    state = "probed" if x_remaining >= 0 else "x_budget_exceeded_by_probe"
    steps = [
        {
            "sequence": 0,
            "kind": "probe",
            "created_at": created_at,
            "status": "complete",
            "x_cost_usd": x_spent,
            "openai_calls_used": 0,
            "request": probe["request"],
            "response": probe["payload"],
            "tweet": compact_or_empty(probe.get("tweet")),
            "author": compact_or_empty(probe.get("author")),
            "media": probe.get("media") or [],
            "cost_basis": probe["cost_basis"],
            "summary": summarize_probe(probe),
        }
    ]
    openai_calls_used = 0

    history = {
        "schema_version": 1,
        "tweet_id": tweet_id,
        "run_id": run_id,
        "created_at": created_at,
        "updated_at": created_at,
        "state": state,
        "next_sequence": len(steps),
        "budget": {
            "x": {
                "budget_usd": x_budget_usd,
                "spent_usd": x_spent,
                "remaining_usd": x_remaining,
            },
            "openai": {
                "max_calls": max_openai_calls,
                "calls_used": openai_calls_used,
                "summary_calls_used": 0,
            },
        },
        "steps": steps,
    }

    append_probe_media_download(history, steps, run_dir)
    write_json(run_dir / "history.json", history)

    loop_states = {"probed", "executed", "execution_partial_failure"}
    while history["state"] in loop_states:
        openai_calls_used = int(history["budget"]["openai"]["calls_used"])
        x_remaining = float(history["budget"]["x"]["remaining_usd"])
        if x_remaining <= 0:
            history["state"] = "x_budget_depleted"
            break
        if openai_calls_used >= max_openai_calls:
            history["state"] = "openai_call_cap_reached"
            break

        plan_created_at = datetime.now(timezone.utc).isoformat()
        plan_record = create_openai_plan(probe, x_budget_usd, history=history)
        openai_calls_used += 1
        history["budget"]["openai"]["calls_used"] = openai_calls_used
        steps.append(
            {
                "sequence": len(steps),
                "kind": "plan",
                "created_at": plan_created_at,
                "status": "complete",
                "x_cost_usd": 0.0,
                "openai_calls_used": 1,
                "total_openai_calls_used": openai_calls_used,
                "model": plan_record["model"],
                "plan": plan_record["plan"],
                "usage": plan_record["usage"],
                "summary": {
                    "strategy_summary": plan_record["plan"].get("strategy_summary"),
                    "estimated_additional_x_cost_usd": plan_record["plan"].get(
                        "estimated_additional_x_cost_usd"
                    ),
                    "budget_fit": plan_record["plan"].get("budget_fit"),
                    "phase_count": len(plan_record["plan"].get("phases", [])),
                },
            }
        )
        history["state"] = "planned"
        history["updated_at"] = plan_created_at
        history["next_sequence"] = len(steps)
        write_json(run_dir / "history.json", history)

        compiled = compile_latest_plan(history)
        compiled_created_at = datetime.now(timezone.utc).isoformat()
        steps.append(
            {
                "sequence": len(steps),
                "kind": "create_plan",
                "created_at": compiled_created_at,
                "status": "complete",
                "source_plan_sequence": compiled["source_plan_sequence"],
                "summary": compiled["summary"],
                "executable_plan": compiled["executable_plan"],
                "blocked_actions": compiled["blocked_actions"],
            }
        )
        history["state"] = "executable_plan_created"
        history["updated_at"] = compiled_created_at
        history["next_sequence"] = len(steps)
        write_json(run_dir / "history.json", history)

        verification = verify_latest_executable_plan(history)
        verified_created_at = datetime.now(timezone.utc).isoformat()
        steps.append(
            {
                "sequence": len(steps),
                "kind": "cost_verification",
                "created_at": verified_created_at,
                "status": "complete",
                "source_create_plan_sequence": verification["source_create_plan_sequence"],
                "summary": verification["summary"],
                "cost_estimate": verification["cost_estimate"],
                "warnings": verification["warnings"],
            }
        )
        fits_budget = verification["summary"]["fits_remaining_x_budget"]
        if not fits_budget:
            history["state"] = "cost_rejected"
        else:
            history["state"] = "cost_verified"
        history["updated_at"] = verified_created_at
        history["next_sequence"] = len(steps)
        write_json(run_dir / "history.json", history)

        if history["state"] == "cost_rejected":
            break

        execution = execute_latest_verified_plan(history)
        executed_created_at = datetime.now(timezone.utc).isoformat()
        steps.append(
            {
                "sequence": len(steps),
                "kind": "execute_plan",
                "created_at": executed_created_at,
                "status": execution["status"],
                "dry_run": False,
                "source_cost_verification_sequence": execution[
                    "source_cost_verification_sequence"
                ],
                "summary": execution["summary"],
                "results": execution["results"],
            }
        )
        apply_execution_budget(history, execution)
        history["state"] = execution["state"]
        history["updated_at"] = executed_created_at
        history["next_sequence"] = len(steps)
        write_json(run_dir / "history.json", history)

        if execution["summary"]["attempted_action_count"] == 0:
            history["state"] = "no_new_executable_actions"
            break

    if memory_summary:
        append_memory_summary(history, steps)
    write_json(run_dir / "history.json", history)
    return run_dir


def append_memory_summary(history: dict, steps: list[dict]) -> None:
    summary_created_at = datetime.now(timezone.utc).isoformat()
    sequence = len(steps)
    try:
        record = summarize_history(history, audience="llm")
        status = "complete"
        summary = record["summary"]
        usage = record["usage"]
        model = record["model"]
    except Exception as exc:
        status = "failed"
        summary = {"error": str(exc)}
        usage = {}
        model = None

    steps.append(
        {
            "sequence": sequence,
            "kind": "history_summary",
            "created_at": summary_created_at,
            "status": status,
            "model": model,
            "audience": "llm",
            "summary": summary,
            "usage": usage,
        }
    )
    openai_budget = history.setdefault("budget", {}).setdefault("openai", {})
    openai_budget["summary_calls_used"] = int(openai_budget.get("summary_calls_used") or 0) + 1
    history["updated_at"] = summary_created_at
    history["next_sequence"] = len(steps)


def append_probe_media_download(history: dict, steps: list[dict], run_dir: Path) -> None:
    created_at = datetime.now(timezone.utc).isoformat()
    sequence = len(steps)
    try:
        result = download_probe_media(history, run_dir=run_dir)
        status = result["status"]
        summary = result["summary"]
        manifest_path = result["manifest_path"]
        items = result["items"]
    except Exception as exc:
        status = "failed"
        summary = {"error": str(exc)}
        manifest_path = None
        items = []

    steps.append(
        {
            "sequence": sequence,
            "kind": "media_download",
            "created_at": created_at,
            "status": status,
            "summary": summary,
            "manifest_path": manifest_path,
            "items": items,
        }
    )
    history["updated_at"] = created_at
    history["next_sequence"] = len(steps)


def main() -> None:
    load_dotenv(ROOT / ".env")
    args = parse_args()
    run_dir = run_initial_probe(
        args.tweet_id,
        args.x_budget_usd,
        args.max_openai_calls,
        memory_summary=not args.no_memory_summary,
    )
    print(json.dumps({"run_dir": str(run_dir), "history": str(run_dir / "history.json")}, indent=2))


if __name__ == "__main__":
    main()
