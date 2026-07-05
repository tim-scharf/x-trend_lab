from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
RUNTIME_DIR = DATA_DIR / "runtime"
GENERATED_DIR = DATA_DIR / "generated"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
REASONING_DIR = DATA_DIR / "reasoning"
DB_PATH = RUNTIME_DIR / "x_trends.db"


MODEL = "gpt-5.5"
NUM_QUERIES = 30
NUM_EXPLOIT = 5
NUM_EXPLORE = 25
MAX_ATTEMPTS = 3

MAX_COUNT_REQUESTS_PER_RUN = 100
X_COUNTS_COST_PER_REQUEST = 0.005

RUN_INTERVAL_MINUTES = 30
EVAL_LAG_HOURS = 3
BOOTSTRAP_MODE = True

VALID_DOMAINS = [
    "AI",
    "tech",
    "combat_sports",
    "medical_ai",
    "sports_exploration",
    "entertainment_exploration",
    "finance_exploration",
]

SEED_QUERIES = [
    {"query": '"UFC" OR "MMA"', "domain": "combat_sports", "mode": "exploit", "reason": "Bootstrap combat sports seed."},
    {"query": '"OpenAI" OR "ChatGPT"', "domain": "AI", "mode": "exploit", "reason": "Bootstrap AI product seed."},
    {"query": '"Nvidia" OR "Blackwell"', "domain": "tech", "mode": "exploit", "reason": "Bootstrap AI infrastructure seed."},
    {"query": '"developer tools" OR "AI coding"', "domain": "tech", "mode": "exploit", "reason": "Bootstrap developer tooling seed."},
    {"query": '"Netflix" OR "streaming"', "domain": "entertainment_exploration", "mode": "explore", "reason": "Bootstrap entertainment seed."},
    {"query": '"NFL" OR "football"', "domain": "sports_exploration", "mode": "explore", "reason": "Bootstrap football seed."},
    {"query": '"NBA" OR "basketball"', "domain": "sports_exploration", "mode": "explore", "reason": "Bootstrap basketball seed."},
    {"query": '"stock market" OR "earnings"', "domain": "finance_exploration", "mode": "explore", "reason": "Bootstrap finance seed."},
]

for path in [RUNTIME_DIR, GENERATED_DIR, SNAPSHOTS_DIR, REASONING_DIR]:
    path.mkdir(parents=True, exist_ok=True)
