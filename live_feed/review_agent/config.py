from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Optional .env for local dev; in Docker the key is injected via env_file (compose).
_default_env = Path(__file__).resolve().parents[3] / "orchestrator" / ".env"
_env_file = os.environ.get("REVIEW_ENV_FILE", str(_default_env))
if Path(_env_file).exists():
    load_dotenv(_env_file)

# ── Directory layout ──────────────────────────────────────────────────────────
LIVE_FEED_DIR = Path(__file__).parent.parent          # live_feed/
CROPS_DIR     = LIVE_FEED_DIR / "crops"
LOGS_DIR      = LIVE_FEED_DIR / "logs"
STATE_DIR     = Path(__file__).parent / "state"
OUT_DIR       = Path(__file__).parent / "out"
DATASET_DIR   = Path(__file__).parent / "dataset"

# ── LLM ───────────────────────────────────────────────────────────────────────
OPENROUTER_MODEL = os.environ.get(
    "OPENROUTER_MODEL", "google/gemini-3-flash-preview"
)

# ── Reconciliation thresholds ─────────────────────────────────────────────────
AUTO_ACCEPT_CONF  = float(os.environ.get("AUTO_ACCEPT_CONF",  "0.85"))
SEND_LOWCONF_ONLY = os.environ.get("SEND_LOWCONF_ONLY", "0") == "1"
IDLE_SECONDS      = int(os.environ.get("IDLE_SECONDS",   "300"))
REVIEW_MIN_FRAMES = int(os.environ.get("REVIEW_MIN_FRAMES", "5"))

# ── Ensure runtime dirs exist ─────────────────────────────────────────────────
for _d in (STATE_DIR, OUT_DIR, DATASET_DIR, CROPS_DIR):
    _d.mkdir(parents=True, exist_ok=True)
