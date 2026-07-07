from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.append(str(Path(__file__).resolve().parents[1]))
from config import X_COUNTS_COST_PER_REQUEST  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = ROOT / "artifacts" / "x_api"
SOURCE_DIR = ARTIFACT_DIR / "sources"

DOCS = [
    {
        "id": "docs_index",
        "url": "https://docs.x.com/llms.txt",
        "filename": "docs_index.llms.txt",
        "purpose": "Top-level X Developer Platform LLM index.",
    },
    {
        "id": "pricing",
        "url": "https://docs.x.com/x-api/getting-started/pricing.md",
        "filename": "pricing.md",
        "purpose": "Official X API pricing page.",
    },
    {
        "id": "usage_billing",
        "url": "https://docs.x.com/x-api/fundamentals/post-cap.md",
        "filename": "usage_billing.md",
        "purpose": "Usage tracking, post caps, and billing semantics.",
    },
    {
        "id": "usage_endpoint",
        "url": "https://docs.x.com/x-api/usage/get-usage.md",
        "filename": "usage_endpoint.md",
        "purpose": "GET /2/usage/tweets endpoint fields.",
    },
    {
        "id": "rate_limits",
        "url": "https://docs.x.com/x-api/fundamentals/rate-limits.md",
        "filename": "rate_limits.md",
        "purpose": "Current endpoint rate limits.",
    },
    {
        "id": "recent_search",
        "url": "https://docs.x.com/x-api/posts/search-recent-posts.md",
        "filename": "recent_search.md",
        "purpose": "GET /2/tweets/search/recent reference.",
    },
    {
        "id": "recent_counts",
        "url": "https://docs.x.com/x-api/posts/get-count-of-recent-posts.md",
        "filename": "recent_counts.md",
        "purpose": "GET /2/tweets/counts/recent reference.",
    },
    {
        "id": "search_operators",
        "url": "https://docs.x.com/x-api/posts/search/integrate/operators.md",
        "filename": "search_operators.md",
        "purpose": "Search query operators.",
    },
    {
        "id": "openapi",
        "url": "https://docs.x.com/openapi.json",
        "filename": "openapi.json",
        "purpose": "Machine-readable OpenAPI contract.",
    },
]


def fetch(url: str) -> str:
    request = Request(url, headers={"User-Agent": "x-trend-lab-doc-refresh/1.0"})
    with urlopen(request, timeout=45) as response:
        return response.read().decode("utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def money_values(text: str) -> list[str]:
    return sorted(set(re.findall(r"\$\s?\d+(?:,\d{3})*(?:\.\d+)?", text)))


def parse_markdown_cost_rows(text: str) -> list[dict]:
    rows = []
    current_section = None
    cost_sections = {"Read operations", "Write operations", "Webhook events"}
    for line in text.splitlines():
        if line.startswith("### "):
            current_section = line.removeprefix("### ").strip()
            continue
        if current_section not in cost_sections or not line.startswith("|") or "$" not in line:
            continue
        cells = [cell.strip().replace("**", "").strip("`") for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        name = re.sub(r"\s+", " ", cells[0]).strip()
        cost_text = re.sub(r"\s+", " ", cells[1]).replace("\\$", "$").strip()
        match = re.search(r"\$(\d+(?:,\d{3})*(?:\.\d+)?)", cost_text)
        if not match:
            continue
        unit = None
        if "per resource" in cost_text.lower():
            unit = "resource"
        elif "per request" in cost_text.lower():
            unit = "request"
        elif "per event" in cost_text.lower():
            unit = "event"
        rows.append(
            {
                "section": current_section,
                "name": name,
                "unit_cost_usd": float(match.group(1).replace(",", "")),
                "unit": unit,
                "raw_cost": cost_text,
            }
        )
    return rows


def find_lines(text: str, patterns: list[str]) -> list[str]:
    lines = []
    lowered_patterns = [pattern.lower() for pattern in patterns]
    for line in text.splitlines():
        lower = line.lower()
        if any(pattern in lower for pattern in lowered_patterns):
            clean = re.sub(r"\s+", " ", line).strip()
            if clean:
                lines.append(clean)
    return lines


def build_cost_profile(downloaded: dict[str, dict]) -> dict:
    pricing_text = downloaded.get("pricing", {}).get("text", "")
    billing_text = downloaded.get("usage_billing", {}).get("text", "")
    rate_text = downloaded.get("rate_limits", {}).get("text", "")
    usage_text = downloaded.get("usage_endpoint", {}).get("text", "")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_urls": {
            doc["id"]: doc["url"]
            for doc in DOCS
            if doc["id"] in downloaded and downloaded[doc["id"]].get("ok")
        },
        "local_fallback_costs": {
            "recent_counts_request_usd": X_COUNTS_COST_PER_REQUEST,
            "note": "Configured in config.py; used when official pricing text cannot be parsed into a concrete per-endpoint unit price.",
        },
        "official_costs_extracted": parse_markdown_cost_rows(pricing_text),
        "pricing_money_values_seen": money_values(pricing_text),
        "pricing_relevant_lines": find_lines(
            pricing_text,
            ["pricing", "$", "post", "request", "search", "counts", "usage", "credit", "cap"],
        )[:120],
        "billing_relevant_lines": find_lines(
            billing_text,
            ["billing", "usage", "deduplic", "24", "cap", "post", "request", "charge", "cost"],
        )[:120],
        "usage_endpoint_fields": find_lines(
            usage_text,
            ["project_usage", "project_cap", "cap_reset_day", "daily_project_usage", "daily_client_app_usage"],
        )[:80],
        "tracer_relevant_rate_limits": find_lines(
            rate_text,
            [
                "/2/tweets/search/recent",
                "/2/tweets/counts/recent",
                "/2/tweets ",
                "/2/tweets/:id",
                "/2/usage/tweets",
            ],
        )[:80],
        "llm_guidance": [
            "Prefer counts endpoints for cheap event mapping before fetching full posts.",
            "Use recent search only for sampled windows or targets with nonzero counts.",
            "Use /2/usage/tweets to check project usage and cap; account-level dollar balance may not be exposed by API.",
            "Treat official pricing as volatile; refresh this artifact before optimizing spend-sensitive strategies.",
        ],
    }


def main() -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)

    downloaded: dict[str, dict] = {}
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "artifact_dir": str(ARTIFACT_DIR),
        "sources": [],
    }

    for doc in DOCS:
        record = {
            "id": doc["id"],
            "url": doc["url"],
            "filename": f"sources/{doc['filename']}",
            "purpose": doc["purpose"],
            "ok": False,
        }
        try:
            text = fetch(doc["url"])
        except (HTTPError, URLError, TimeoutError) as exc:
            record["error"] = str(exc)
            downloaded[doc["id"]] = {"ok": False, "text": ""}
        else:
            write_text(SOURCE_DIR / doc["filename"], text)
            record["ok"] = True
            record["bytes"] = len(text.encode("utf-8"))
            downloaded[doc["id"]] = {"ok": True, "text": text}
        manifest["sources"].append(record)

    ok_count = sum(1 for source in manifest["sources"] if source["ok"])
    if ok_count == 0:
        print("Fetched 0 sources; leaving existing X API artifacts unchanged.")
        for source in manifest["sources"]:
            print(f"- {source['id']}: failed: {source.get('error', 'unknown error')}")
        sys.exit(1)

    cost_profile = build_cost_profile(downloaded)
    write_text(ARTIFACT_DIR / "manifest.json", json.dumps(manifest, indent=2))
    write_text(ARTIFACT_DIR / "x_api_cost_profile.json", json.dumps(cost_profile, indent=2))

    readme = [
        "# X API Artifact Bundle",
        "",
        "Generated from official X documentation URLs for LLM planning and spend-aware API choices.",
        "",
        "Primary files:",
        "",
        "- `manifest.json`: source URL, purpose, fetch status, and local filename.",
        "- `x_api_cost_profile.json`: compact spend/rate-limit summary extracted from downloaded docs.",
        "- `sources/`: raw official Markdown, `llms.txt`, and OpenAPI source files.",
        "",
        "Refresh with:",
        "",
        "```bash",
        "python scripts/refresh_x_api_artifacts.py",
        "```",
    ]
    write_text(ARTIFACT_DIR / "README.md", "\n".join(readme) + "\n")

    print(f"Wrote {ARTIFACT_DIR}")
    print(f"Fetched {ok_count}/{len(DOCS)} sources")
    for source in manifest["sources"]:
        status = "ok" if source["ok"] else f"failed: {source.get('error', 'unknown error')}"
        print(f"- {source['id']}: {status}")


if __name__ == "__main__":
    main()
