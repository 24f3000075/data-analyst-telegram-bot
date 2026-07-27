"""
Very small in-process conversation store.

Keyed by Telegram chat_id -> list of {"role": "user"|"assistant", "content": str}.
This is intentionally simple (no external DB): grading conversations are short
and happen within one deploy's uptime. If you want durability across restarts,
swap this for a tiny SQLite table without changing the rest of the app.
"""

from collections import defaultdict
from threading import Lock

_lock = Lock()
_history: dict = defaultdict(list)

MAX_TURNS_KEPT = 20  # cap memory growth for very long test conversations


def get_history(chat_id) -> list:
    with _lock:
        return list(_history[chat_id])


def append_user_message(chat_id, text: str):
    with _lock:
        _history[chat_id].append({"role": "user", "content": text})
        _history[chat_id] = _history[chat_id][-MAX_TURNS_KEPT:]


def append_assistant_message(chat_id, text: str):
    with _lock:
        _history[chat_id].append({"role": "assistant", "content": text})
        _history[chat_id] = _history[chat_id][-MAX_TURNS_KEPT:]
