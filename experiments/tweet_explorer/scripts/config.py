from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT_DIR = Path(__file__).resolve().parents[1]
RUNS_DIR = EXPERIMENT_DIR / "runs"
X_API_ARTIFACT_DIR = ROOT / "artifacts" / "x_api"
X_API_COST_PROFILE_PATH = X_API_ARTIFACT_DIR / "x_api_cost_profile.json"
X_API_PRICING_PATH = X_API_ARTIFACT_DIR / "sources" / "pricing.md"
X_API_USAGE_BILLING_PATH = X_API_ARTIFACT_DIR / "sources" / "usage_billing.md"
X_API_RATE_LIMITS_PATH = X_API_ARTIFACT_DIR / "sources" / "rate_limits.md"

DEFAULT_X_BUDGET_USD = 2.0
MAX_OPENAI_CALLS = 4
PLANNER_MODEL = "gpt-5.4-nano"
SUMMARY_MODEL = "gpt-5.4-nano"
MAX_DOC_EXCERPT_CHARS = 8000
MAX_MEDIA_DOWNLOAD_BYTES = 50 * 1024 * 1024

X_TWEET_LOOKUP_URL = "https://api.x.com/2/tweets/{tweet_id}"
X_API_BASE_URL = "https://api.x.com"
X_POST_READ_COST_USD = 0.005
X_USER_READ_COST_USD = 0.010
X_LIKE_READ_COST_USD = 0.001
X_COUNTS_RECENT_REQUEST_COST_USD = 0.005
X_MEDIA_READ_FALLBACK_COST_USD = 0.005

EXECUTABLE_TWEET_FIELDS = [
    "id",
    "text",
    "author_id",
    "created_at",
    "conversation_id",
    "public_metrics",
    "referenced_tweets",
    "lang",
    "possibly_sensitive",
]

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

MEDIA_FIELDS = [
    "alt_text",
    "duration_ms",
    "height",
    "media_key",
    "preview_image_url",
    "public_metrics",
    "type",
    "url",
    "variants",
    "width",
]
