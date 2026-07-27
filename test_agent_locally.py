"""
Quick local sanity check that doesn't touch Telegram at all -- exercises the
same agent.run_agent() the webhook calls, so you can validate your
ANTHROPIC_API_KEY / tool loop before wiring up Telegram.

Usage:
    python test_agent_locally.py "Which state has the highest maternal mortality rate based on MOSPI data?"
"""

import sys

from app.agent import run_agent
from app.logger import RunLogger

if __name__ == "__main__":
    question = sys.argv[1] if len(sys.argv) > 1 else (
        "What is 2 + 2? Reply with ONLY this JSON object and nothing else: "
        '{"answer": <number>, "log_url": "<url>"}'
    )
    logger = RunLogger("local-test")
    result = run_agent([{"role": "user", "content": question}], logger)
    print("\n=== RESULT ===")
    print(result)
    print(f"\nFull run log written to: {logger.path}")
