# Telegram Bot Backend (Phase 1)

A standalone Python backend that receives Telegram messages (private DMs,
groups, supergroups), stores them in PostgreSQL, and exposes a secure REST
API for a separate Web Panel (hosted elsewhere, e.g. Netlify).

- No webhooks needed - the bot uses **long polling**, works behind any firewall.
- No Gunicorn - runs with **plain `python bot.py`** on a Render Web Service.
- `live.py` keeps the Render Web Service HTTP-alive on `0.0.0.0:$PORT`.
- No frontend code in this repository. The Web Panel is a separate project.

```
telegram-bot/
├── bot.py                  # main entry: starts DB, live server thread, Telegram polling
├── live.py                 # Flask server: /ping, /health, hosts /api/* blueprints
├── config.py               # environment-driven configuration (never hardcodes secrets)
├── requirements.txt
├── runtime.txt             # pins the Python version for Render
├── .env.example
├── database/
│   ├── db.py               # engine, sessions, init, health check
│   └── models.py           # tg_chats, tg_users, tg_messages
├── telegram/
│   └── handlers.py         # update processing, chat/user upserts, dedupe
├── routes/
│   ├── groups.py           # GET /api/groups, messages, POST reply
│   ├── dms.py              # GET /api/dms, messages, POST reply
│   ├── chats.py            # POST /api/chats/<id>/read
│   ├── dashboard.py        # GET /api/dashboard
│   └── status.py           # GET /api/status
├── services/
│   └── telegram_service.py # Telegram Bot HTTP API client (sendMessage, getUpdates)
└── utils/
    ├── auth.py             # Bearer PANEL_API_KEY protection
    └── helpers.py          # parsing, error responses
```

## 1. Requirements

- Python 3.10+ (3.11 recommended)
- PostgreSQL 12+
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

## 2. Local installation

```bash
git clone <your-repo> telegram-bot
cd telegram-bot

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env               # then edit .env
```

Create a local database (or use any hosted PostgreSQL):

```bash
createdb telegram_bot
# or: psql -U postgres -c "CREATE DATABASE telegram_bot;"
```

Fill `.env`:

```ini
TELEGRAM_BOT_TOKEN=1234567890:AA...your-real-token...
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/telegram_bot
PANEL_API_KEY=change-me-to-a-long-random-string
FRONTEND_ORIGIN=http://localhost:3000
PORT=10000
```

## 3. Run locally

```bash
python bot.py
```

Everything runs in one process:

1. PostgreSQL tables are created if missing.
2. The Flask live server starts in a background thread on `0.0.0.0:$PORT`.
3. The Telegram bot starts long polling in a worker thread.

Optional: `python live.py` runs ONLY the HTTP server (bot disabled) -
useful for testing the API without touching Telegram.

## 4. Telegram BotFather setup

1. Open Telegram and talk to **@BotFather**.
2. `/newbot` -> choose a name and a username -> copy the token into
   `TELEGRAM_BOT_TOKEN`. Keep it secret, never commit it.

### Group privacy configuration (IMPORTANT)

By default Telegram bots have **Privacy Mode ON**, which means they only
see commands and replies in groups - NOT normal text messages.

To let the bot read normal group messages do ONE of the following:

**Option A - Disable privacy mode (recommended for this use case):**

1. In @BotFather: `/mybots` -> select your bot
2. **Bot Settings** -> **Group Privacy** -> **Turn off**
   (BotFather replies "Privacy mode is disabled")
3. **Remove the bot from the group and add it back** so the change applies.

**Option B - Make the bot a group admin.** Admins always receive all
messages regardless of privacy mode.

The bot automatically registers every group/supergroup it receives a
message from. Each group stays completely separate (keyed by its real
Telegram chat id, e.g. `-1001234567890`).

## 5. Render deployment (Web Service, no Gunicorn)

1. Push this folder to a GitHub/GitLab repository.
2. Render Dashboard -> **New** -> **Web Service** -> connect the repo.
3. Settings:
   | Setting        | Value                            |
   |----------------|----------------------------------|
   | Runtime        | Python 3                          |
   | Build Command  | `pip install -r requirements.txt` |
   | Start Command  | `python bot.py`                   |
   | Instance Type  | Free (or any)                     |

   (`runtime.txt` pins the Python version; the port is injected by Render
   as `PORT` and `live.py` binds `0.0.0.0:$PORT` automatically.)

4. Create a PostgreSQL database: **New** -> **PostgreSQL**, copy its
   **Internal Database URL**.
5. Add environment variables (Render -> service -> **Environment**):

   | Key                  | Value                                   |
   |----------------------|-----------------------------------------|
   | `TELEGRAM_BOT_TOKEN` | token from @BotFather                   |
   | `DATABASE_URL`       | Render PostgreSQL Internal Database URL |
   | `PANEL_API_KEY`      | long random secret (`openssl rand -hex 32`) |
   | `FRONTEND_ORIGIN`    | e.g. `https://my-panel.netlify.app`     |
   | `PORT`               | leave unset - Render provides it        |

6. Deploy. In the logs you should see
   `Telegram bot started (long polling, timeout=30s)` and
   `Flask live server started in background thread`.

> Only ONE running instance may poll a bot token. Do not run the same
> token locally and on Render at the same time (Telegram returns 409
> Conflict; the backend logs it and retries).

## 6. Uptime endpoints (public)

```bash
curl https://YOUR-APP.onrender.com/ping
# {"pong":true,"service":"telegram-bot"}

curl https://YOUR-APP.onrender.com/health
# {"status":"ok","service":"telegram-bot","telegram_configured":true,
#  "bot_polling":true,"database":"connected","uptime_seconds":123,"time":"..."}
```

No tokens, passwords or secrets are ever returned.

## 7. Web Panel REST API

All `/api/*` endpoints require the header:

```
Authorization: Bearer <PANEL_API_KEY>
```

(`X-API-Key: <key>` is also accepted.) Wrong/missing keys get `401`.
CORS allows only `FRONTEND_ORIGIN` (or localhost dev defaults when unset).

Base URL: `https://YOUR-APP.onrender.com`

### Groups

```bash
# List groups (search by title or chat id via ?q=, page via ?limit=&offset=)
curl -H "Authorization: Bearer $PANEL_API_KEY" \
  "https://YOUR-APP.onrender.com/api/groups?q=gaming"

# Message history of a group (newest first; search ?q=; cursor ?before_id=)
curl -H "Authorization: Bearer $PANEL_API_KEY" \
  "https://YOUR-APP.onrender.com/api/groups/-1001234567890/messages?limit=50"

# Reply into the group (body: {"text": "Hello!"})
curl -X POST -H "Authorization: Bearer $PANEL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text":"Hello from the panel!"}' \
  "https://YOUR-APP.onrender.com/api/groups/-1001234567890/reply"
```

### Private DMs

```bash
# List private conversations (?q= searches username/first/last name/user id)
curl -H "Authorization: Bearer $PANEL_API_KEY" \
  "https://YOUR-APP.onrender.com/api/dms?q=rahul"

curl -H "Authorization: Bearer $PANEL_API_KEY" \
  "https://YOUR-APP.onrender.com/api/dms/5123456789/messages"

curl -X POST -H "Authorization: Bearer $PANEL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text":"Hello!"}' \
  "https://YOUR-APP.onrender.com/api/dms/5123456789/reply"
```

### Read / unread

Incoming messages raise `unread_count`. Clear it per chat:

```bash
curl -X POST -H "Authorization: Bearer $PANEL_API_KEY" \
  "https://YOUR-APP.onrender.com/api/chats/-1001234567890/read"
```

### Dashboard & status

```bash
curl -H "Authorization: Bearer $PANEL_API_KEY" \
  "https://YOUR-APP.onrender.com/api/dashboard"
# {"ok":true,"total_groups":1,"total_dms":1,"total_messages":3,
#  "unread_messages":2,"incoming_messages":2,"outgoing_messages":1,
#  "recent_activity":[ ... ]}

curl -H "Authorization: Bearer $PANEL_API_KEY" \
  "https://YOUR-APP.onrender.com/api/status"
# {"ok":true,"backend":"online","service":"telegram-bot",
#  "telegram_configured":true,"telegram_connected":true,
#  "bot_username":"YourBot","bot_polling":true,"database":"connected",
#  "healthy":true,"time":"..."}   (HTTP 503 when something is degraded)
```

### Message payloads (sender, replies, media)

Every message object includes:

- `type` / `message_type`: `"text"`, `"sticker"` or `"voice"`
- `sender`: `{"user_id", "first_name", "last_name", "username", "display_name"}`
  (real Telegram profile data; `display_name` falls back
  first+last name -> first name -> username -> user id)
- `reply_to_message_id` + `reply_to`: when the Telegram message was a
  reply, the original message (with its sender and text) is embedded
- `sticker`: `{file_id, file_unique_id, width, height, emoji, set_name,
  is_animated, is_video, url}`
- `voice`: `{file_id, file_unique_id, duration, mime_type, file_size, url}`

Panel replies quote a Telegram message:

```bash
curl -X POST -H "Authorization: Bearer $PANEL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text":"Got it!","reply_to_message_id":50}' \
  ".../api/groups/-1001234567890/reply"
```

`reply_to_message_id` must be the REAL Telegram message_id and belong to
that chat, otherwise the request is rejected with 400.

### Media proxy (stickers / voice notes)

A Telegram `file_id` is not a browser URL and downloading requires the
bot token - so media is served through a backend proxy:

```
GET /api/media/<file_id>?exp=<unix>&sig=<hmac>
```

Message payloads already contain ready-to-use signed `url` values
(`sticker.url` / `voice.url`) that work directly in `<img>` / `<audio>`
tags. URLs are HMAC-signed server-side (secret: `MEDIA_SIGNING_SECRET`,
falling back to `PANEL_API_KEY`) and expire after 6 hours; invalid or
expired links get 403, and expired Telegram files get 410. The bot token
never leaves the server. Static stickers arrive as `image/webp`, video
stickers as `video/webm`, animated stickers as `application/x-tgsticker`
(raw Lottie bytes - render with a Lottie player on the panel side),
voice notes as `audio/ogg`.

### Endpoint aliases

The same handlers are reachable under two path styles, so either naming
works from the panel:

| Action | Paths (equivalent) |
|---|---|
| Send text / real reply | `POST /api/groups/<chat_id>/reply` · `POST /api/groups/<chat_id>/messages` |
| Send sticker | `POST /api/groups/<chat_id>/sticker` · `.../stickers` |
| Send voice | `POST /api/groups/<chat_id>/voice` |

Identical aliases exist under `/api/dms/<chat_id>/...`. `GET
/api/groups/<chat_id>/messages` (history) is unchanged - only the POST
method is new on that path.

Media messages also expose flat `file_id` / `file_unique_id` keys next to
the nested `sticker` / `voice` objects, and the operator flag is
published under both `mentions_operator` and `mentioned_operator`.

### Mentions

Telegram entities are stored verbatim (original offsets/lengths, UTF-16
based - correct even after emoji). Each message also carries a resolved
`mentions` array:

```json
{"type":"mention","offset":4,"length":10,"text":"@rahul_dev",
 "username":"rahul_dev","user_id":6981270107,"display_name":"Rahul",
 "known_user":true,"is_operator":false}
```

- `mention` entities resolve against known users by username; unknown
  ones keep the raw `@username` and invent nothing (`known_user:false`).
- `text_mention` (users without a username) resolves via the inline
  Telegram user object, so a real name is shown instead of a numeric id.
- Render by slicing `text` with the UTF-16 offsets (or use the `mentions`
  entries directly) - the original text is never modified.

Optional operator identity (`OPERATOR_TELEGRAM_USER_ID` and/or
`OPERATOR_TELEGRAM_USERNAME`) flags `mentions_operator: true` on messages
that mention the operator, plus the optional filter
`GET /api/groups/<chat_id>/messages?mentions_only=true`. With no operator
configured, mentions still render normally and the flag stays `false`.

### Jump to the original message

Reply previews come from `reply_to` (with `available: true|false`). When
the original is not in the loaded window:

```bash
GET /api/chats/<chat_id>/messages/<telegram_message_id>          # one message + newer_count
GET /api/chats/<chat_id>/messages/context?around_id=<id>&limit=25 # window around it
```

Both return `404 {"reason":"not_stored"}` when the original predates the
bot or was deleted, so the panel can show a graceful
"Original message not loaded" state.

### Sending stickers and voice from the panel

```bash
# Sticker (re-send any file_id the bot has seen). Optional real reply target.
curl -X POST -H "Authorization: Bearer $PANEL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"file_id":"CAACAgIA...","reply_to_message_id":64}' \
  ".../api/groups/-1001234567890/sticker"

# Recent stickers for the picker (bots cannot read your personal
# sticker library - these are stickers the bot has actually seen)
curl -H "Authorization: Bearer $PANEL_API_KEY" ".../api/stickers/recent?limit=30"

# Voice note (multipart upload, field name "voice")
curl -X POST -H "Authorization: Bearer $PANEL_API_KEY" \
  -F "voice=@note.ogg" -F "reply_to_message_id=64" -F "duration=7" \
  ".../api/dms/5123456789/voice"
```

The same endpoints exist under `/api/groups/...` and `/api/dms/...`.
Telegram requires **OGG/Opus** for voice notes: `.ogg/.oga/.opus`
uploads are streamed straight through; other formats (browser
MediaRecorder WebM/Opus) are converted automatically **if ffmpeg is
installed on the host**, otherwise a clear 400 is returned. On Render's
plain Python runtime ffmpeg is usually absent - either record/convert to
OGG/Opus in the browser, or add ffmpeg to the build.

Outgoing messages are stored with their real `message_type`
(`text` / `sticker` / `voice`) - never all as `"text"`.

### Emojis

Normal Unicode emojis are plain Telegram text: the panel's emoji picker
should insert the character into the composer and send it through the
existing text reply endpoint. No backend conversion happens, and emoji
never become stickers.

### Reactions - intentionally not implemented

`allowed_updates` is limited to `["message"]`. `message_reaction` and
`message_reaction_count` are deliberately NOT subscribed (they require
the bot to be a group administrator). No reaction data is received,
stored or faked, and the bot works as a **normal group member**.

### Safety guarantees

- Replies are validated: a **group reply** can only be sent to a
  group/supergroup chat id, a **DM reply** only to a private chat id.
  The exact Telegram chat id supplied is used - Group A's reply can never
  land in Group B.
- `UNIQUE(telegram_chat_id, telegram_message_id)` makes storage idempotent;
  Telegram redeliveries never create duplicate rows.
- Errors are returned as JSON (`{"ok": false, "error": "..."}`) with proper
  HTTP codes - never stack traces.

### Example error responses

```json
{ "ok": false, "error": "Unauthorized: invalid API key" }                 // 401
{ "ok": false, "error": "Field 'text' is required and cannot be empty" }  // 400
{ "ok": false, "error": "Chat not found for this chat id" }               // 404
{ "ok": false, "error": "Telegram rate limit reached. Try again later.",
  "retry_after": 7 }                                                      // 429
{ "ok": false, "error": "Telegram refused: the bot was blocked by the user
   or removed from the chat (Forbidden: bot was blocked by the user)" }   // 403
```

## 8. Message flow

**Group message:** Telegram -> bot identifies chat + user -> creates/updates
the group row -> creates/updates the user row -> stores the message ->
`unread_count + 1` -> done. **No automatic reply.**

**Private DM:** same flow, stored as a chat with `type="private"`, fully
separate from groups.

**Panel reply:** panel POSTs with the Bearer key -> backend validates key,
chat id and chat type -> sends via Telegram Bot API -> stores the
outgoing message (`direction="outgoing"`, `sender_type="admin"`) ->
returns the result as JSON.

## 9. Security notes

- Real secrets live only in environment variables (`.env` is git-ignored).
- The bot token and API key are never logged and never appear in responses.
- Database errors are logged by exception class only - never with the DSN.
- All `/api/*` endpoints require the panel API key.
- CORS is restricted to `FRONTEND_ORIGIN` (no production wildcard).

## 10. Later phases (designed for)

- Media messages (`message_type` column is ready).
- Outgoing `reply_to` quoting (column already exists).
- python-telegram-bot can replace the raw `requests` client inside
  `services/telegram_service.py` without touching the rest. (Note: if you
  install it, rename the local `telegram/` package to avoid shadowing.)
