import json
import os

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app import state, telegram_client
from app.agent import run_agent
from app.config import LOGS_DIR, PUBLIC_BASE_URL, WEBHOOK_SECRET
from app.gist_logger import publish_gist
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

    local_log_url = run_logger.log_url(PUBLIC_BASE_URL)
    try:
        with open(run_logger.path, "r") as f:
            log_content = f.read()
    except OSError:
        log_content = ""

    gist_url = publish_gist(
        filename=f"{run_logger.run_id}.jsonl",
        content=log_content or "{}\n",
        description=f"Run log for Telegram chat {chat_id}",
    )
    log_url = gist_url or local_log_url

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
        return {"ok": True}

    chat_id = message["chat"]["id"]
    text = message["text"]

    background_tasks.add_task(_process_message, chat_id, text)
    return {"ok": True}


@app.on_event("startup")
def _maybe_set_webhook():
    if PUBLIC_BASE_URL:
        url = f"{PUBLIC_BASE_URL}/telegram/webhook/{WEBHOOK_SECRET}"
        try:
            telegram_client.set_webhook(url)
        except Exception:
            pass
