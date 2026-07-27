import json
import os
import time
import uuid

from app.config import LOGS_DIR


class RunLogger:
    """Appends structured events to logs/<run_id>.jsonl, one JSON object per line.

    A single run_id is used per Telegram chat_id, so the whole conversation
    (all turns) ends up in one log file, matching the "one JSON object per
    line" requirement while keeping a single stable log_url per chat.
    """

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.path = os.path.join(LOGS_DIR, f"{run_id}.jsonl")

    @classmethod
    def for_chat(cls, chat_id) -> "RunLogger":
        # Stable, filesystem-safe run id per chat.
        run_id = f"chat-{chat_id}"
        return cls(run_id)

    def log(self, event_type: str, **fields):
        record = {
            "ts": time.time(),
            "iso_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "type": event_type,
            **fields,
        }
        with open(self.path, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def log_url(self, public_base_url: str) -> str:
        if public_base_url:
            return f"{public_base_url}/logs/{self.run_id}.jsonl"
        # Fallback so the bot still returns *something* wget-able once deployed,
        # even if PUBLIC_BASE_URL wasn't configured (shouldn't happen in prod).
        return f"/logs/{self.run_id}.jsonl"
