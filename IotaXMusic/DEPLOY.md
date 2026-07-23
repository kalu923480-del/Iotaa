# Iota Music Bot — Render Deploy (24/7)

Sirf **`IotaXMusic/`** folder. Economy bot alag account pe: [`iota/DEPLOY.md`](../iota/DEPLOY.md).

---

## Account B — Music only (recommended)

### 1) Alag Render account
Economy wale se **different** Render account (load split / free-tier sleep alag).

### 2) New Web Service → Docker

| Field | Value |
|-------|--------|
| **Root Directory** | `IotaXMusic` |
| **Environment** | **Docker** |
| **Dockerfile Path** | `./Dockerfile` |
| **Docker Context** | `.` |
| **Health Check Path** | `/health` |
| **Plan** | Free (or paid for always-on) |

Docker includes `ffmpeg` + `deno` (YouTube downloads).

### 3) Environment variables

| Key | Required | Notes |
|-----|----------|--------|
| `API_ID` | **yes** | https://my.telegram.org |
| `API_HASH` | **yes** | my.telegram.org |
| `BOT_TOKEN` | **yes** | @BotFather |
| `OWNER_ID` | **yes** | your Telegram user id |
| `STRING_SESSION` | **yes** | run `python3 session.py` locally |
| `MONGO_DB_URI` | **yes** | MongoDB Atlas URI |
| `LOGGER_ID` | optional | log group id, or `0` |
| `BOT_USERNAME` | yes | e.g. `Iotamusicbot` |
| `COOKIE_URL` | recommended | raw Pastebin Netscape cookies |
| `COOKIE_FILE` | optional | default `/etc/secrets/cookies.txt` |
| `KEEPALIVE_URL` | optional | only if self-ping missing after deploy |
| `PORT` | auto | Render injects `8080` |

### 4) Cookies (YouTube play)

**Option A — Secret File (best on Render)**  
1. Service → Environment → **Secret Files**  
2. Filename: `cookies.txt` (Netscape format)  
3. Bot reads `/etc/secrets/cookies.txt` automatically  

**Option B — COOKIE_URL**  
1. Export YouTube Netscape cookies  
2. Pastebin Unlisted → **Raw** URL  
3. `COOKIE_URL=https://pastebin.com/raw/XXXX`

### 5) Deploy → logs

Expect:
```
YouTube cookies loaded…
Health server listening on 0.0.0.0:8080
Keep-alive self-ping → https://….onrender.com/health every 300s
Iota Music Robot Started Successfully…
```

If keep-alive says disabled: set  
`KEEPALIVE_URL=https://YOUR-SERVICE.onrender.com` → Manual Deploy.

### 6) Test
1. Add bot + assistant as **admin** in a group  
2. Start voice chat  
3. `/play udi udi`  
4. Open `https://YOUR-SERVICE.onrender.com/health` → `{"ok":true,...}`

---

## 24/7 (built-in, same idea as iota economy bot)

| Layer | What |
|-------|------|
| Health | `GET /health` + `GET /` |
| Self-ping | every **5 min** using `RENDER_EXTERNAL_URL` |
| Fallback | set `KEEPALIVE_URL` after first deploy |
| Extra | [UptimeRobot](https://uptimerobot.com) → same `/health` every 5 min |

Free tier can still restart occasionally; paid instance is more stable for VC.

---

## Blueprint alternative

**New → Blueprint** → repo → **Root Directory = `IotaXMusic`**  
→ uses only `IotaXMusic/render.yaml` (economy service will **not** be created).

---

## Local session string

```bash
cd IotaXMusic
python3 session.py
# paste STRING_SESSION into Render env
```

---

## Security

Never commit: `.env`, `cookies.txt`, `*.session`  
If leaked: rotate BotFather token, Mongo password, and session string.

---

## Do not

- Run **iota** economy bot on this same free service  
- Put both bots in one free instance (sleep + RAM issues)
