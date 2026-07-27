# Telegram Data-Analyst Agent Bot

An LLM agent (Claude, via the Anthropic API) that lives behind a Telegram bot.
Message it a data-analysis question (MOSPI or any public dataset) and it will:

1. Reason about what's needed.
2. Use tools — web search, URL fetching, and sandboxed Python/pandas execution — to
   actually pull and crunch the data.
3. Reply with **exactly one JSON object**: `{"answer": <shape you asked for>, "log_url": "<public jsonl log>"}`.

Every step (inputs, tool calls, tool outputs, final answer) is appended to a
per-conversation JSONL run log, which the bot serves itself at a public URL —
no external logging service needed.

---

## 1. Architecture

```
Telegram  --webhook-->  FastAPI app (app/main.py)
                             |
                             v
                        agent.py  --loop-->  Anthropic Claude API
                             |                 |  \
                             |                 |   \-- server tool: web_search
                             |                 \-- client tools (tools.py):
                             |                        - fetch_url
                             |                        - run_python (pandas/numpy sandbox)
                             v
                        logger.py --> logs/<run_id>.jsonl  (served at /logs/<run_id>.jsonl)
```

- **Webhook, not polling.** Telegram POSTs each message to `/telegram/webhook/<secret>`.
  This is what lets a free web-service host stay reachable without a background worker.
- **Per-chat conversation memory** is kept in-process (`app/state.py`) so multi-turn
  questions ("now filter to just Bihar", "what about the second row") have context.
  It resets on redeploy — fine for the short-lived conversations this bot handles.
- **Logs are just files on disk**, served by the same app. `log_url` =
  `https://<your-host>/logs/<run_id>.jsonl`.

---

## 2. Local setup

```bash
git clone <this-repo-url>
cd telegram-data-analyst-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in the values, see below
```

Required env vars (`.env`):

| Var | Where to get it |
|---|---|
| `TELEGRAM_BOT_TOKEN` | From @BotFather (see step 3) |
| `ANTHROPIC_API_KEY` | https://console.anthropic.com |
| `PUBLIC_BASE_URL` | Your deployed URL, e.g. `https://your-app.onrender.com` (leave blank locally) |
| `WEBHOOK_SECRET` | Any random string you make up, used in the webhook path |

Run it locally with a tunnel (for testing only — for grading, deploy it, see step 4):

```bash
uvicorn app.main:app --reload --port 8000
# in another terminal:
ngrok http 8000
# then set the webhook to the ngrok URL, see step 3.4
```

---

## 3. Create the Telegram bot (BotFather)

1. Open Telegram, search for **@BotFather**, start a chat.
2. Send `/newbot`, give it a display name, then a **username ending in `bot`**
   (e.g. `my_data_analyst_bot`) — this is what you register for grading.
3. BotFather replies with an **API token** — put it in `TELEGRAM_BOT_TOKEN`.
4. Point Telegram at your deployed app (after step 4, once you have a live URL):

   ```bash
   curl "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook?url=https://<your-host>/telegram/webhook/<WEBHOOK_SECRET>"
   ```

   The app also tries to set this automatically on startup if `PUBLIC_BASE_URL`
   is set (see `app/main.py`'s startup hook) — the curl command is your fallback/check.
5. Verify: `curl "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getWebhookInfo"`
   should show your URL with no pending errors.

Note: the grading account messages your bot as a **real Telegram user**, not
another bot — nothing special needed on your end for that, just make sure the
bot isn't private/restricted (default @BotFather bots accept messages from anyone).

---

## 4. Deploy (recommended: Render, free tier)

Render's free **Web Service** tier is the easiest zero-cost option for a
webhook-based bot like this one, since it just needs to answer HTTP requests
(no always-on worker process required, unlike long-polling).

1. Push this repo to your own public GitHub repo.
2. Go to https://render.com → New → **Web Service** → connect your repo.
3. Runtime: Python 3. Build command: `pip install -r requirements.txt`.
   Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   (or just let Render read `render.yaml` / `Procfile`, already included).
4. Add the env vars from `.env.example` in the Render dashboard
   (`PUBLIC_BASE_URL` = the `.onrender.com` URL Render gives you).
5. Deploy. Then run the `setWebhook` curl command from step 3.4.

**Free-tier gotcha:** Render's free web services spin down after ~15 minutes
of no HTTP traffic, and the first request after that takes ~30-60s to wake up.
For grading, that first message may be slow but will still get answered —
webhook delivery + FastAPI just needs to respond within Telegram's retry window.
To avoid cold starts entirely during your grading window, add a free uptime
pinger (e.g. https://uptimerobot.com) hitting `GET /health` every 5 minutes.

**Alternatives:** Railway, Fly.io, or any VPS work identically — just point
`PUBLIC_BASE_URL` at wherever the app ends up and set the webhook the same way.

---

## 5. Testing against the official grading harness

```bash
git clone https://github.com/Jivraj-18/tds-p1-t2-2026-telegram-bot
cd tds-p1-t2-2026-telegram-bot
# follow its README to point it at your bot username
# add your own questions to evals/questions.json to sanity-check locally
```

---

## 6. Repo layout

```
app/
  main.py            FastAPI app: webhook endpoint, log-serving endpoint, health check
  agent.py            The Claude tool-use loop that actually answers the question
  tools.py            Tool implementations: fetch_url, run_python, web_search wiring
  logger.py           Appends structured JSONL events to logs/<run_id>.jsonl
  state.py            In-memory per-chat conversation history
  telegram_client.py  Thin wrapper around the Telegram Bot HTTP API
  config.py           Env var loading
requirements.txt
Procfile
render.yaml
.env.example
```

## 7. Register for grading

Registration string: `<your-github-repo-url>, <your_bot_username>`
(bot username must end in `bot`, matching what you created in step 3).
