import requests

from app.config import TELEGRAM_BOT_TOKEN

API_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def send_message(chat_id, text: str):
    """Send a plain-text message. Telegram messages max out at 4096 chars;
    the JSON replies we send should always be well under that, but we
    truncate defensively rather than let Telegram silently reject the call.
    """
    if len(text) > 4000:
        text = text[:4000]
    resp = requests.post(
        f"{API_BASE}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=15,
    )
    return resp.json()


def set_webhook(url: str):
    resp = requests.post(f"{API_BASE}/setWebhook", json={"url": url}, timeout=15)
    return resp.json()


def get_webhook_info():
    resp = requests.get(f"{API_BASE}/getWebhookInfo", timeout=15)
    return resp.json()
