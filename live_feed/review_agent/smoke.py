"""Startup gate: validates API key, model ID, and structured output in one call.

Run standalone:
    python -m live_feed.review_agent.smoke
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# ── sys.path bootstrap ────────────────────────────────────────────────────────
_VS_DIR = Path(__file__).parent.parent.parent
if str(_VS_DIR) not in sys.path:
    sys.path.insert(0, str(_VS_DIR))

log = logging.getLogger(__name__)


def smoke_test() -> bool:
    try:
        from live_feed.review_agent.client import build_llm
        from live_feed.review_agent.schemas import HealthCheck

        chain = build_llm().with_structured_output(HealthCheck)
        out = chain.invoke(
            [{"role": "user", "content": "Reply with status set to the literal string ok."}]
        )
        ok = out.status.strip().lower() == "ok"
        if ok:
            log.info("✓ review_agent: LLM reachable (structured output OK)")
        else:
            log.warning("review_agent: unexpected smoke reply: %r", out)
        return ok
    except Exception as exc:
        log.warning("review_agent DISABLED — smoke test failed: %s", exc)
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ok = smoke_test()
    sys.exit(0 if ok else 1)
