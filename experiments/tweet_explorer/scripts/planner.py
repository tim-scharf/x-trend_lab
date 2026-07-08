from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from config import (
    MAX_DOC_EXCERPT_CHARS,
    PLANNER_MODEL,
    X_API_COST_PROFILE_PATH,
    X_API_PRICING_PATH,
    X_API_RATE_LIMITS_PATH,
    X_API_USAGE_BILLING_PATH,
)
from helper import read_json, read_text


def load_x_docs() -> dict[str, Any]:
    for path in [
        X_API_COST_PROFILE_PATH,
        X_API_PRICING_PATH,
        X_API_USAGE_BILLING_PATH,
        X_API_RATE_LIMITS_PATH,
    ]:
        if not path.exists():
            raise FileNotFoundError(f"Missing X API artifact: {path}")

    return {
        "cost_profile": compact_cost_profile(read_json(X_API_COST_PROFILE_PATH)),
        "pricing_excerpt": read_text(X_API_PRICING_PATH, max_chars=MAX_DOC_EXCERPT_CHARS),
        "usage_billing_excerpt": read_text(
            X_API_USAGE_BILLING_PATH,
            max_chars=MAX_DOC_EXCERPT_CHARS,
        ),
        "rate_limits_excerpt": read_text(
            X_API_RATE_LIMITS_PATH,
            max_chars=MAX_DOC_EXCERPT_CHARS,
        ),
    }


def compact_cost_profile(profile: dict[str, Any]) -> dict[str, Any]:
    useful_names = {
        "Posts: Read",
        "User: Read",
        "Following/Followers: Read",
        "Like: Read",
        "Counts: Recent",
        "Counts: All",
        "Trends",
    }
    useful_costs = [
        row
        for row in profile.get("official_costs_extracted", [])
        if row.get("name") in useful_names
    ]
    return {
        "generated_at": profile.get("generated_at"),
        "official_costs_extracted": useful_costs,
        "local_fallback_costs": profile.get("local_fallback_costs"),
        "billing_relevant_lines": profile.get("billing_relevant_lines", [])[:40],
        "rate_limits": profile.get("tracer_relevant_rate_limits", [])[:40],
        "llm_guidance": profile.get("llm_guidance", []),
    }


def response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "tweet_id",
            "strategy_summary",
            "estimated_additional_x_cost_usd",
            "budget_fit",
            "phases",
            "stop_rules",
            "deferred_actions",
            "assumptions",
            "risks",
        ],
        "properties": {
            "tweet_id": {"type": "string"},
            "strategy_summary": {"type": "string"},
            "estimated_additional_x_cost_usd": {"type": "number"},
            "budget_fit": {
                "type": "string",
                "enum": ["under_budget", "at_budget", "over_budget", "cannot_estimate"],
            },
            "phases": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "name",
                        "goal",
                        "api_actions",
                        "estimated_cost_usd",
                        "decision_rule",
                        "expected_evidence",
                    ],
                    "properties": {
                        "name": {"type": "string"},
                        "goal": {"type": "string"},
                        "api_actions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "endpoint_or_method",
                                    "purpose",
                                    "cost_basis",
                                    "estimated_cost_usd",
                                    "resource_cap",
                                    "pagination_token",
                                    "query",
                                ],
                                "properties": {
                                    "endpoint_or_method": {"type": "string"},
                                    "purpose": {"type": "string"},
                                    "cost_basis": {"type": "string"},
                                    "estimated_cost_usd": {"type": "number"},
                                    "resource_cap": {"type": "integer"},
                                    "pagination_token": {
                                        "type": ["string", "null"],
                                        "description": "Use a next_token from prior execution when requesting the next page; otherwise null or omitted.",
                                    },
                                    "query": {
                                        "type": ["string", "null"],
                                        "description": "For /2/tweets/search/recent and /2/tweets/counts/recent only: the exact X query string to run. Use null for non-query endpoints.",
                                    },
                                },
                            },
                        },
                        "estimated_cost_usd": {"type": "number"},
                        "decision_rule": {"type": "string"},
                        "expected_evidence": {"type": "string"},
                    },
                },
            },
            "stop_rules": {"type": "array", "items": {"type": "string"}},
            "deferred_actions": {"type": "array", "items": {"type": "string"}},
            "assumptions": {"type": "array", "items": {"type": "string"}},
            "risks": {"type": "array", "items": {"type": "string"}},
        },
    }


def build_planner_prompt(
    probe: dict[str, Any],
    x_budget_usd: float,
    x_docs: dict[str, Any],
    history: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    probe_cost = float(probe.get("estimated_cost_usd") or 0)
    budget_state = (history or {}).get("budget", {}).get("x", {})
    already_spent = float(budget_state.get("spent_usd") or probe_cost)
    remaining_budget = float(
        budget_state.get("remaining_usd")
        if budget_state.get("remaining_usd") is not None
        else round(x_budget_usd - already_spent, 6)
    )
    system_prompt = (
        "You are an X API investigation planner. Given one hydrated X Post probe, "
        "design the next budgeted X API inspection strategy. "
        "Do not execute calls. Return only JSON matching the schema. "
        "Treat the supplied X API docs and cost profile as authoritative. "
        "Actively use the available budget to learn something useful. "
        "Prefer sampled fanout and staged optionality over crawls. "
        "Do not propose full follower, liker, reply, or repost crawls unless the budget clearly supports them. "
        "On replans, evaluate the latest execution evidence first and propose only genuinely new work."
    )
    user_prompt = f"""
X budget:
- total_x_budget_usd: {x_budget_usd:.6f}
- already_spent_x_usd: {already_spent:.6f}
- remaining_x_budget_usd: {remaining_budget:.6f}

Planning rules:
- The probe has already happened. Do not repeat the exact tweet lookup.
- estimated_additional_x_cost_usd must include only new X API work after existing history.
- Additional X work must fit inside remaining_x_budget_usd.
- Prefer the first executable tranche to spend at most 25-35% of remaining_x_budget_usd.
- This is an exploration experiment: if remaining_x_budget_usd can support a useful sampled tranche, propose one.
- On the first planning call, do not return zero api_actions unless the tweet is unavailable/protected/deleted or remaining budget is below the cheapest useful action.
- On later planning calls, keep exploring with new pages, new query angles, or targeted context until the next marginal tranche has low value or the hard caps stop you.
- If Prior run history summary includes latest_compressed_memory, treat it as the current case memory and update your plan from that memory plus the latest execution evidence.
- Treat unused X budget as lost opportunity. You are not required to spend every cent, but ending with most of the X budget unused is a bad outcome unless evidence is exhausted.
- With 4 OpenAI calls total, each planning move should normally spend a meaningful tranche: roughly 20-35% of remaining_x_budget_usd when useful evidence is available.
- For high-engagement tweets, spending only a few cents while leaving more than half the budget unused is usually too timid.
- Use concrete endpoints/methods and resource caps.
- If endpoint minimums or billing are uncertain, choose the conservative estimate and say so.
- For endpoints with a minimum max_results of 10, any one request returning Posts costs at least 10 * $0.005 = $0.050.
- For /2/tweets/search/recent and /2/tweets/counts/recent, set query to the exact X query string you want executed. Use query to test hypotheses from returned payloads, such as conversation reply volume, repeated phrases, URLs, quoted authors, or suspected amplification patterns.
- Prefer cheap /2/tweets/counts/recent checks when a count can decide whether a follow-up sample is worth spending Posts: Read budget.
- When building a search/count query from prior payload text, keep it narrow, explain the hypothesis in purpose, and include conversation_id:{probe.get("tweet", {}).get("conversation_id") or probe.get("tweet_id")} unless intentionally looking outside the conversation.
- Do not propose requests that duplicate an already executed request unless the evidence explicitly requires a refresh.
- If prior successful responses include next_token values, use pagination_token for a next page instead of repeating the same first-page request.
- Do not retry failed requests unless you can explain why changing the endpoint/params should fix the failure.
- If prior evidence is sufficient, return no api_actions and explain the stop condition in strategy_summary/stop_rules.
- For monster tweets, sample aggressively enough to learn the discourse shape. For small tweets, still run one cheap check if budget permits.

Hydrated probe:
{json.dumps(probe, indent=2, default=str)}

Prior run history summary:
{json.dumps(compact_history_for_planner(history), indent=2, default=str)}

Compact X cost profile:
{json.dumps(x_docs["cost_profile"], indent=2, default=str)}

Pricing docs excerpt:
{x_docs["pricing_excerpt"]}

Usage/billing docs excerpt:
{x_docs["usage_billing_excerpt"]}

Rate-limit docs excerpt:
{x_docs["rate_limits_excerpt"]}
"""
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def create_plan(
    probe: dict[str, Any],
    x_budget_usd: float,
    *,
    history: dict[str, Any] | None = None,
    x_docs: dict[str, Any] | None = None,
    model: str = PLANNER_MODEL,
) -> dict[str, Any]:
    from openai import OpenAI

    docs = x_docs or load_x_docs()
    prompt = build_planner_prompt(probe, x_budget_usd, docs, history=history)
    client = OpenAI()
    response = client.responses.create(
        model=model,
        input=prompt,
        text={
            "format": {
                "type": "json_schema",
                "name": "tweet_explorer_plan",
                "schema": response_schema(),
            }
        },
    )
    return {
        "model": model,
        "prompt": prompt,
        "plan": json.loads(response.output_text),
        "usage": usage_to_dict(getattr(response, "usage", None)),
    }


def usage_to_dict(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    if isinstance(usage, dict):
        return usage
    return json.loads(json.dumps(usage, default=str))


def probe_from_history(history: dict[str, Any]) -> dict[str, Any]:
    for step in history.get("steps", []):
        if step.get("kind") == "probe":
            return {
                "tweet_id": history.get("tweet_id"),
                "request": step.get("request"),
                "payload": step.get("response"),
                "tweet": step.get("tweet"),
                "author": step.get("author"),
                "estimated_cost_usd": step.get("x_cost_usd"),
                "cost_basis": step.get("cost_basis"),
            }
    raise ValueError("No probe step found in history.")


def compact_history_for_planner(history: dict[str, Any] | None) -> dict[str, Any]:
    if not history:
        return {"steps": []}

    compact_steps = []
    for step in history.get("steps", []):
        kind = step.get("kind")
        compact: dict[str, Any] = {
            "sequence": step.get("sequence"),
            "kind": kind,
            "status": step.get("status"),
            "summary": step.get("summary"),
        }
        if kind == "history_summary":
            compact["audience"] = step.get("audience")
            compact["model"] = step.get("model")
        if kind == "execute_plan":
            compact["results"] = compact_execution_results(step)
        if kind == "cost_verification":
            compact["warnings"] = step.get("warnings")
        compact_steps.append(compact)

    return {
        "state": history.get("state"),
        "budget": history.get("budget"),
        "already_attempted_requests": attempted_requests(history),
        "available_pagination_tokens": available_pagination_tokens(history),
        "failed_requests": failed_requests(history),
        "evidence_digest": evidence_digest(history),
        "latest_compressed_memory": latest_history_summary(history),
        "steps": compact_steps,
    }


def latest_history_summary(history: dict[str, Any]) -> dict[str, Any] | None:
    for step in reversed(history.get("steps", [])):
        if step.get("kind") == "history_summary" and step.get("status") == "complete":
            return {
                "sequence": step.get("sequence"),
                "audience": step.get("audience"),
                "model": step.get("model"),
                "summary": step.get("summary"),
            }
    return None


def attempted_requests(history: dict[str, Any]) -> list[dict[str, Any]]:
    requests = []
    seen = set()
    for step in history.get("steps", []):
        if step.get("kind") != "execute_plan":
            continue
        for phase in step.get("results", []):
            for action in phase.get("actions", []):
                request = action.get("request")
                if not request:
                    continue
                key = request_key(request)
                if key in seen:
                    continue
                seen.add(key)
                requests.append(
                    {
                        "kind": action.get("kind"),
                        "status": action.get("status"),
                        "http_status": action.get("http_status"),
                        "request": request,
                    }
                )
    return requests


def available_pagination_tokens(history: dict[str, Any]) -> list[dict[str, Any]]:
    tokens = []
    for step in history.get("steps", []):
        if step.get("kind") != "execute_plan":
            continue
        for phase in step.get("results", []):
            for action in phase.get("actions", []):
                response = action.get("response")
                if not isinstance(response, dict):
                    continue
                token = (response.get("meta") or {}).get("next_token")
                if not token:
                    continue
                tokens.append(
                    {
                        "kind": action.get("kind"),
                        "request": action.get("request"),
                        "pagination_token": token,
                        "result_count": (response.get("meta") or {}).get("result_count"),
                    }
                )
    return tokens


def failed_requests(history: dict[str, Any]) -> list[dict[str, Any]]:
    failures = []
    for step in history.get("steps", []):
        if step.get("kind") != "execute_plan":
            continue
        for phase in step.get("results", []):
            for action in phase.get("actions", []):
                if action.get("status") not in {"http_error", "request_error"}:
                    continue
                failures.append(
                    {
                        "kind": action.get("kind"),
                        "status": action.get("status"),
                        "http_status": action.get("http_status"),
                        "request": action.get("request"),
                        "response_preview": response_preview(action.get("response")),
                    }
                )
    return failures


def request_key(request: dict[str, Any]) -> str:
    params = request.get("params") or {}
    params_key = "&".join(f"{key}={params[key]}" for key in sorted(params))
    return f"{request.get('method', 'GET')} {request.get('url')}?{params_key}"


def compact_execution_results(step: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    for phase in step.get("results", []):
        for action in phase.get("actions", []):
            results.append(
                {
                    "phase_index": phase.get("phase_index"),
                    "phase_name": phase.get("name"),
                    "kind": action.get("kind"),
                    "status": action.get("status"),
                    "request": action.get("request"),
                    "resource_counts": action.get("resource_counts"),
                    "response_preview": response_preview(action.get("response")),
                }
            )
    return results


def evidence_digest(history: dict[str, Any]) -> dict[str, Any]:
    action_counts: Counter[str] = Counter()
    text_counts: Counter[str] = Counter()
    count_queries = []
    samples = []

    for step in history.get("steps", []):
        if step.get("kind") != "execute_plan":
            continue
        for phase in step.get("results", []):
            for action in phase.get("actions", []):
                if action.get("status") != "complete":
                    continue
                kind = str(action.get("kind") or "unknown")
                action_counts[kind] += 1
                request = action.get("request") or {}
                params = request.get("params") or {}
                payload = action.get("response")

                if kind == "counts_recent" and isinstance(payload, dict):
                    meta = payload.get("meta") or {}
                    count_queries.append(
                        {
                            "query": params.get("query"),
                            "total_tweet_count": meta.get("total_tweet_count"),
                        }
                    )

                for item in response_items(payload):
                    text = str(item.get("text") or "").strip()
                    if not text:
                        continue
                    text_counts[text] += 1
                    if len(samples) < 20:
                        samples.append(
                            {
                                "kind": kind,
                                "query": params.get("query"),
                                "id": item.get("id"),
                                "text": text,
                                "metrics": item.get("public_metrics") or {},
                            }
                        )

    repeated_texts = [
        {"text": text, "count": count}
        for text, count in text_counts.most_common(10)
        if count > 1
    ]
    return {
        "completed_action_counts": dict(action_counts),
        "counts_recent_results": count_queries[-10:],
        "repeated_texts": repeated_texts,
        "sample_posts": samples[-20:],
    }


def response_items(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def response_preview(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return str(payload)[:500] if payload is not None else None

    preview: dict[str, Any] = {}
    if payload.get("meta"):
        preview["meta"] = payload.get("meta")
    if payload.get("errors"):
        preview["errors"] = payload.get("errors")[:3]
    data = payload.get("data")
    if isinstance(data, list):
        preview["data"] = [compact_resource(item) for item in data[:5]]
    elif isinstance(data, dict):
        preview["data"] = compact_resource(data)
    includes = payload.get("includes") or {}
    if includes.get("users"):
        preview["users"] = [compact_resource(user) for user in includes["users"][:5]]
    if includes.get("tweets"):
        preview["tweets"] = [compact_resource(tweet) for tweet in includes["tweets"][:5]]
    if includes.get("media"):
        preview["media"] = [compact_resource(media) for media in includes["media"][:5]]
    return preview


def compact_resource(resource: dict[str, Any]) -> dict[str, Any]:
    keep = [
        "id",
        "text",
        "author_id",
        "created_at",
        "conversation_id",
        "public_metrics",
        "username",
        "name",
        "verified",
        "verified_type",
        "description",
        "media_key",
        "type",
        "url",
        "preview_image_url",
    ]
    return {key: resource.get(key) for key in keep if key in resource}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create an X API investigation plan from a probe.")
    parser.add_argument("history_json", type=Path, help="Path to a tweet_explorer history.json.")
    parser.add_argument(
        "--x-budget-usd",
        type=float,
        help="Override total X budget. Defaults to history budget.",
    )
    parser.add_argument("--model", default=PLANNER_MODEL, help=f"Planner model. Default: {PLANNER_MODEL}.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    history = read_json(args.history_json)
    budget = args.x_budget_usd or float(history["budget"]["x"]["budget_usd"])
    record = create_plan(probe_from_history(history), budget, history=history, model=args.model)
    print(json.dumps(record["plan"], indent=2, default=str))


if __name__ == "__main__":
    main()
