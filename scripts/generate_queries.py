import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import (
    DB_PATH,
    GENERATED_DIR,
    REASONING_DIR,
    MODEL,
    NUM_EXPLOIT,
    NUM_EXPLORE,
    NUM_QUERIES,
    MAX_ATTEMPTS,
)
from scripts.history_evidence import (
    EVIDENCE_PLAN_LATEST,
    EVIDENCE_SELECTED_LATEST,
    select_history_evidence,
)


load_dotenv()

ROOT = Path(__file__).resolve().parents[1]




REASONING_DIR.mkdir(parents=True, exist_ok=True)

RUNTIME_DIR = ROOT / "data" / "runtime"
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

MEMORY_DIR = ROOT / "data" / "memory"
REASONING_MEMORY_LATEST = MEMORY_DIR / "reasoning_memory_latest.json"


def load_score_summary() -> pd.DataFrame:
    """
    Build a current short-term momentum summary from the X counts DB.

    This is NOT delayed realized fitness. It only summarizes current bucket momentum:
    recent_3h vs previous_3h, with a simple low-volume penalty.
    """
    fallback = pd.DataFrame(
        [
            {
                "query": '"OpenAI" OR "Grok"',
                "hourly_buckets": 0,
                "total_7d": 0,
                "recent_3h": 0,
                "recent_6h": 0,
                "prev_3h": 0,
                "velocity": 0.0,
                "acceleration": 0.0,
                "score": 0.0,
            }
        ]
    )

    if not DB_PATH.exists():
        return fallback

    con = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        """
        SELECT query, bucket_start, bucket_end, tweet_count
        FROM counts
        ORDER BY query, bucket_start
        """,
        con,
    )
    con.close()

    if df.empty:
        return fallback

    df["bucket_start"] = pd.to_datetime(df["bucket_start"], utc=True)
    df["bucket_end"] = pd.to_datetime(df["bucket_end"], utc=True)

    rows = []
    for query, group in df.groupby("query", sort=False):
        group = group.sort_values("bucket_start").reset_index(drop=True)
        values = group["tweet_count"].astype(int).tolist()

        hourly_buckets = len(values)
        total_7d = int(sum(values))
        recent_3h = int(sum(values[-3:])) if hourly_buckets >= 3 else int(sum(values))
        prev_3h = int(sum(values[-6:-3])) if hourly_buckets >= 6 else 0
        recent_6h = int(sum(values[-6:])) if hourly_buckets >= 6 else recent_3h

        velocity = recent_3h / (prev_3h + 1)
        acceleration = (recent_3h - prev_3h) / (prev_3h + 1)
        score = (0.7 * velocity) + (0.3 * acceleration)

        min_volume = 50
        if recent_3h < min_volume:
            score *= 0.2

        rows.append(
            {
                "query": query,
                "hourly_buckets": hourly_buckets,
                "total_7d": total_7d,
                "recent_3h": recent_3h,
                "recent_6h": recent_6h,
                "prev_3h": prev_3h,
                "velocity": round(float(velocity), 4),
                "acceleration": round(float(acceleration), 4),
                "score": round(float(score), 4),
            }
        )

    score_df = pd.DataFrame(rows).sort_values(
        ["score", "recent_3h", "total_7d"], ascending=False
    ).reset_index(drop=True)

    return score_df


def build_history_evidence_block(history_df: pd.DataFrame, plan_payload: dict) -> str:
    """
    Format the selected evidence into the generator prompt.
    """
    if history_df.empty:
        return f"""
No historical delayed-fitness records selected.

Selection note:
{plan_payload.get("strategy") or plan_payload.get("error", "")}
"""

    plan = plan_payload.get("plan", plan_payload)

    return f"""
Historical delayed-fitness evidence selected from query_level_history.csv:

Selection strategy:
{plan.get("strategy", "")}

Interpretation:
- future_3h is delayed observed volume after the original snapshot.
- realized_score is delayed fitness.
- burst_trap means t0_3h was positive but future_3h collapsed to 0.
- future_nonzero means the query had some delayed continuation.
- Use this evidence as advisory signal, not as a template to preserve.
- Retire stale query families aggressively when they keep producing zero or weak delayed fitness.
- Do not simply copy winners. Extract the lesson, then try materially different event surfaces.

Selected historical records:
{history_df.to_string(index=False)}
"""


def load_reasoning_memory() -> dict:
    if not REASONING_MEMORY_LATEST.exists():
        return {}

    try:
        return json.loads(REASONING_MEMORY_LATEST.read_text())
    except Exception:
        return {}


def compact_items(items: list[dict], fields: list[str]) -> str:
    lines = []
    for item in items:
        parts = [str(item.get(field, "")).strip() for field in fields if item.get(field)]
        if parts:
            lines.append("- " + " | ".join(parts))
    return "\n".join(lines)


def build_reasoning_memory_block(memory: dict) -> str:
    if not memory:
        return """
No reasoning memory available.
"""

    source_window = memory.get("source_window", {})

    return f"""
Reasoning memory distilled from prior generated-query reasons and delayed outcomes:

Updated at: {memory.get("updated_at", "unknown")}
Source window: {json.dumps(source_window, default=str)}

Summary:
{memory.get("prompt_summary", "")}

Exploit guidance:
{compact_items(memory.get("exploit_guidance", []), ["mechanism", "rule", "confidence"])}

Explore guidance:
{compact_items(memory.get("explore_guidance", []), ["mechanism", "query_shape", "reason", "confidence"])}

Avoid guidance:
{compact_items(memory.get("avoid_guidance", []), ["failure_mode", "reason"])}

Decay rules:
{chr(10).join("- " + str(rule) for rule in memory.get("decay_rules", []))}

Query-shape rules:
{chr(10).join("- " + str(rule) for rule in memory.get("query_shape_rules", []))}
"""


def build_prompt(score_df: pd.DataFrame, history_block: str = "", reasoning_memory_block: str = "") -> str:
    prompt_df = score_df[
        ["query", "hourly_buckets", "total_7d", "recent_3h", "prev_3h", "velocity", "score"]
    ].head(80)

    table = prompt_df.to_string(index=False)

    return f"""
You are helping evolve an X/Twitter topic-count sampling strategy.

Goal:
Find early trend candidates using X recent-counts queries, not individual posts.

Current query performance:
{table}

Historical delayed-fitness evidence:
{history_block}

Reasoning memory:
{reasoning_memory_block}

Interpretation rules:
- Queries with huge volume may be too broad and noisy.
- Queries with near-zero volume are too narrow.
- Good queries should have enough volume to measure, but not be so broad that they are always noisy.
- Prefer X search syntax that works with the counts endpoint.
- Use quoted phrases and OR groups when they improve precision, but do not force every query into the same three-parenthesis pattern.
- Prefer specific entity-event combinations over generic evergreen topics, but only when the entity/event is plausibly active now.
- During exploration, take real chances: introduce new domains, new event mechanisms, and less obvious phrasing.
- A risky explore query that teaches something is better than another safe mutation of a stale family.
- Avoid overusing generic recency tails like today/now/overnight unless they materially improve the search.
- Avoid pulling individual tweets.
- Avoid malformed search strings. Do not over-escape quotes.
- Avoid private, credential-related, unsafe, adult, or spammy terms.

Scoring hints:
- recent_3h = newest 3 hourly buckets
- prev_3h = the 3 buckets before that
- velocity compares recent_3h against prev_3h
- score is a short-term momentum score, not just raw volume
- huge evergreen topics may still be too broad even with strong score
- prefer measurable but not permanently noisy queries

Historical evidence hints:
- Delayed realized fitness matters more than current momentum.
- Historical evidence is not a mandate to stay in the same domains.
- Preserve a query family only when the current table or delayed evidence still justifies it.
- Avoid repeating burst traps or no-signal forms unless the new query changes the underlying bet.
- Prefer entity + event verb + status qualifier only when that structure fits the idea naturally.
- If historical evidence is clumpy, deliberately break out of the clump instead of making near-neighbor variants.
- Retire or sharply reduce families that appear repeatedly in prior batches without strong delayed fitness.

Reasoning memory hints:
- Treat reasoning memory as general policy learned from prior model reasons and delayed scoring.
- Prefer reusable mechanisms over copying named domains from memory.
- Follow decay and avoid guidance unless current counts or historical evidence clearly justify a fresh test.
- When memory conflicts with current momentum, make the query materially different and explain why in the reason.

Domain labeling:
- For each query, choose a concise domain label.
- Reuse an existing domain label if it clearly fits.
- If no existing label fits, create a new lowercase snake_case domain.
- The domain is metadata only; the query text is what matters most.
- Good examples: ai, consumer_tech, geopolitics, gaming_outages, sports_injuries, entertainment, finance, crypto, macro, legal.

Exploration rule:
Generate exactly {NUM_QUERIES} queries:
- Exactly {NUM_EXPLOIT} queries must be mode "exploit": refine or mutate existing promising clusters.
- Exactly {NUM_EXPLORE} queries must be mode "explore": test adjacent or new domains.
- At least half of explore queries should be non-obvious bets that are not direct mutations of the current top rows.
- Include some exploratory queries that are intentionally sparse but plausible early detectors.

Prefer a mix of:
- event queries
- rumor / leak / injury / lawsuit / release phrasing
- more specific mutations of currently strong clusters
- at least a few historically informed mutations
- at least a few exploratory candidates not directly copied from evidence
- at least a few fresh domains or event surfaces absent from the current top rows
- varied query shapes, including some simpler two-clause queries when appropriate

Return only data matching the JSON schema. No markdown. No code fences.
"""


def response_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "strategy_notes": {"type": "string"},
            "queries": {
                "type": "array",
                "minItems": NUM_QUERIES,
                "maxItems": NUM_QUERIES,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "query": {"type": "string"},
                        "domain": {
                            "type": "string",
                            "description": (
                                "Concise topic/domain label. Reuse an existing label if it fits; "
                                "otherwise create a new lowercase snake_case label."
                            ),
                        },
                        "mode": {"type": "string", "enum": ["exploit", "explore"]},
                        "reason": {"type": "string"},
                    },
                    "required": ["query", "domain", "mode", "reason"],
                },
            },
        },
        "required": ["strategy_notes", "queries"],
    }


def validate_payload(payload: dict) -> None:
    queries = payload.get("queries", [])
    if len(queries) != NUM_QUERIES:
        raise ValueError(f"Expected {NUM_QUERIES} queries, got {len(queries)}")

    exploit = sum(q["mode"] == "exploit" for q in queries)
    explore = sum(q["mode"] == "explore" for q in queries)
    if exploit != NUM_EXPLOIT or explore != NUM_EXPLORE:
        raise ValueError(
            f"Expected {NUM_EXPLOIT} exploit / {NUM_EXPLORE} explore, got {exploit}/{explore}"
        )

    for q in queries:
        domain = q.get("domain", "").strip()
        if not domain:
            raise ValueError("Empty domain found in generated payload")
        if len(domain) > 50:
            raise ValueError(f"Domain label too long: {domain}")
        if not re.match(r"^[A-Za-z0-9_ -]+$", domain):
            raise ValueError(f"Suspicious domain label: {domain}")

    deduped = {q["query"].strip() for q in queries}
    if len(deduped) != len(queries):
        raise ValueError("Duplicate queries found in generated payload")


def generate_queries(prompt: str) -> dict:
    from openai import OpenAI

    client = OpenAI()
    last_err = None

    for _ in range(MAX_ATTEMPTS):
        try:
            response = client.responses.create(
                model=MODEL,
                input=prompt,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "query_generation",
                        "schema": response_schema(),
                    }
                },
            )
            payload = json.loads(response.output_text)
            validate_payload(payload)
            return payload
        except Exception as e:
            last_err = e

    raise RuntimeError(
        f"Failed to generate valid query payload after {MAX_ATTEMPTS} attempts: {last_err}"
    )


def save_reasoning_file(
    payload: dict,
    prompt: str,
    score_df: pd.DataFrame,
    generated_filename: str,
    evidence_df: pd.DataFrame,
    evidence_plan_payload: dict,
    reasoning_memory_payload: dict,
) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    reason_payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model_used": MODEL,
        "generated_file": generated_filename,
        "strategy_notes": payload.get("strategy_notes", ""),
        "query_count": len(payload.get("queries", [])),
        "queries": payload.get("queries", []),
        "seed_ideas_used": [],
        "score_summary_rows": score_df.to_dict(orient="records"),
        "history_evidence_plan": evidence_plan_payload,
        "history_evidence_rows": evidence_df.to_dict(orient="records"),
        "history_evidence_row_count": int(len(evidence_df)),
        "reasoning_memory": {
            "enabled": bool(reasoning_memory_payload),
            "file": str(REASONING_MEMORY_LATEST),
            "updated_at": reasoning_memory_payload.get("updated_at"),
            "source_window": reasoning_memory_payload.get("source_window"),
        },
        "prompt": prompt,
    }

    path = REASONING_DIR / f"reasoning_{timestamp}.json"
    with path.open("w") as f:
        json.dump(reason_payload, f, indent=2, default=str)
    return path


def save_query_generation(
    payload: dict,
    evidence_plan_payload: dict,
    evidence_df: pd.DataFrame,
    reasoning_memory_payload: dict,
) -> Path:
    created_at = datetime.now(timezone.utc).isoformat()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    payload["model_used"] = MODEL
    payload["created_at"] = created_at
    payload["generation_settings"] = {
        "num_queries": NUM_QUERIES,
        "num_exploit": NUM_EXPLOIT,
        "num_explore": NUM_EXPLORE,
        "history_evidence_enabled": True,
        "history_evidence_rows": int(len(evidence_df)),
        "reasoning_memory_enabled": bool(reasoning_memory_payload),
        "reasoning_memory_updated_at": reasoning_memory_payload.get("updated_at"),
    }

    payload["history_evidence"] = {
        "selected_rows": int(len(evidence_df)),
        "plan_strategy": (
            evidence_plan_payload.get("plan", {}).get("strategy")
            if isinstance(evidence_plan_payload.get("plan"), dict)
            else evidence_plan_payload.get("strategy")
        ),
        "plan_file": str(EVIDENCE_PLAN_LATEST),
        "selected_file": str(EVIDENCE_SELECTED_LATEST),
    }

    payload["reasoning_memory"] = {
        "enabled": bool(reasoning_memory_payload),
        "file": str(REASONING_MEMORY_LATEST),
        "updated_at": reasoning_memory_payload.get("updated_at"),
        "source_window": reasoning_memory_payload.get("source_window"),
    }

    path = GENERATED_DIR / f"generated_queries_{timestamp}.json"
    with path.open("w") as f:
        json.dump(payload, f, indent=2, default=str)
    return path


def main() -> None:
    max_evidence_records = 100
    if len(sys.argv) >= 2:
        max_evidence_records = min(int(sys.argv[1]), 100)

    score_df = load_score_summary()

    evidence_df, evidence_plan_payload = select_history_evidence(
        max_records=max_evidence_records
    )
    history_block = build_history_evidence_block(evidence_df, evidence_plan_payload)
    reasoning_memory_payload = load_reasoning_memory()
    reasoning_memory_block = build_reasoning_memory_block(reasoning_memory_payload)

    prompt = build_prompt(
        score_df,
        history_block=history_block,
        reasoning_memory_block=reasoning_memory_block,
    )
    payload = generate_queries(prompt)

    generated_path = save_query_generation(
        payload,
        evidence_plan_payload,
        evidence_df,
        reasoning_memory_payload,
    )
    reasoning_path = save_reasoning_file(
        payload=payload,
        prompt=prompt,
        score_df=score_df,
        generated_filename=generated_path.name,
        evidence_df=evidence_df,
        evidence_plan_payload=evidence_plan_payload,
        reasoning_memory_payload=reasoning_memory_payload,
    )

    print(f"History evidence rows selected: {len(evidence_df)}")
    print(f"History evidence latest CSV: {EVIDENCE_SELECTED_LATEST}")
    print(f"History evidence latest plan: {EVIDENCE_PLAN_LATEST}")
    print(f"Reasoning memory enabled: {bool(reasoning_memory_payload)}")
    if reasoning_memory_payload:
        print(f"Reasoning memory updated at: {reasoning_memory_payload.get('updated_at')}")
    print(f"Saved generated queries to: {generated_path}")
    print(f"Saved reasoning log to: {reasoning_path}")
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
