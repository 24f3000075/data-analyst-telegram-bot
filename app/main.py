import json
import os

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app import state, telegram_client
from app.agent import run_agent
from app.config import LOGS_DIR, PUBLIC_BASE_URL, WEBHOOK_SECRET
from app.logger import RunLogger

app = FastAPI(title="Telegram Data-Analyst Agent")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def root():
    return {"status": "ok", "service": "telegram-data-analyst-bot"}


@app.get("/logs/{run_id}.jsonl")
def get_log(run_id: str):
    # run_id is constrained to "chat-<digits>" by RunLogger.for_chat; guard
    # against path traversal regardless.
    safe_name = os.path.basename(run_id) + ".jsonl"
    path = os.path.join(LOGS_DIR, safe_name)
    if not os.path.isfile(path):
        return PlainTextResponse("", status_code=404)
    with open(path, "r") as f:
        content = f.read()
    return PlainTextResponse(content, media_type="application/x-ndjson")


def _process_message(chat_id, text: str):
    run_logger = RunLogger.for_chat(chat_id)
    run_logger.log("incoming_message", chat_id=chat_id, text=text)

    state.append_user_message(chat_id, text)
    history = state.get_history(chat_id)

    result = run_agent(history, run_logger)

    log_url = run_logger.log_url(PUBLIC_BASE_URL)
    reply_obj = {"answer": result["answer"], "log_url": log_url}
    reply_text = json.dumps(reply_obj)

    run_logger.log("outgoing_message", chat_id=chat_id, reply=reply_obj)
    state.append_assistant_message(chat_id, reply_text)

    telegram_client.send_message(chat_id, reply_text)


@app.post("/telegram/webhook/{secret}")
async def telegram_webhook(secret: str, request: Request, background_tasks: BackgroundTasks):
    if secret != WEBHOOK_SECRET:
        return JSONResponse({"ok": False, "error": "bad secret"}, status_code=403)

    update = await request.json()
    message = update.get("message") or update.get("edited_message")
    if not message or "text" not in message:
        # Nothing actionable (e.g. non-text message, membership update, etc.)
        return {"ok": True}

    chat_id = message["chat"]["id"]
    text = message["text"]

    # Respond to Telegram immediately; do the (possibly slow) agent work in
    # the background so Telegram doesn't consider the webhook delivery failed.
    background_tasks.add_task(_process_message, chat_id, text)
    return {"ok": True}


@app.on_event("startup")
def _maybe_set_webhook():
    if PUBLIC_BASE_URL:
        url = f"{PUBLIC_BASE_URL}/telegram/webhook/{WEBHOOK_SECRET}"
        try:
            telegram_client.set_webhook(url)
        except Exception:
            # Don't crash startup if Telegram is unreachable at boot; the
            # README's manual curl command is the fallback.
            pass
