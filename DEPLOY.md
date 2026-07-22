# Iota — Render 24/7 Deploy Guide (Economy Bot + Music Bot)

Ye repo **do bots** chalaata hai:

| Service | Path | Kya karta hai |
|---------|------|----------------|
| **iota-bot** | `iota/` | Economy, games, AI chat, Ludo Mini App |
| **iota-music** | `IotaXMusic/` | Group VC music (`/play`, skip, queue, …) |

Dono **Render free Web Service** pe 24/7 ke liye setup hain:
- `/health` HTTP endpoint
- har ~5 min **self-ping** (free tier sleep rokne ke liye)

---

## 0) Pehle GitHub pe push

```bash
git add -A
git status   # ensure .env / cookies.txt / *.session NOT staged
git commit -m "deploy: render 24/7 for iota + music"
git push origin main
```

**Kabhi mat push karo:**
- `iota/config.py` (local secrets)
- `IotaXMusic/.env`
- `IotaXMusic/IotaXMedia/assets/cookies.txt`
- `*.session` files

---

## 1) Render pe Blueprint deploy (recommended)

1. [Render Dashboard](https://dashboard.render.com) → **New** → **Blueprint**
2. GitHub repo select karo: `kalu923480-del/Iotaa` (ya apna fork)
3. Root `render.yaml` auto-detect hoga → **Apply**
4. Do services banengi: `iota-bot` + `iota-music`
5. Har service ke **Environment** me secrets bharo (neeche table)

### iota-bot env (economy)

| Key | Example / notes |
|-----|-----------------|
| `BOT_TOKEN` | @BotFather token |
| `OWNER_ID` | aapka Telegram numeric ID |
| `OWNER_USERNAME` | `@im_ntg` |
| `MONGO_URI` | full Atlas `mongodb+srv://...` |
| `WEBAPP_BASE_URL` | deploy ke baad: `https://iota-bot.onrender.com` |
| `GROQ_API_KEYS` | optional AI |
| `GIPHY_API_KEY` | optional GIFs |

### iota-music env

| Key | Example / notes |
|-----|-----------------|
| `API_ID` / `API_HASH` | https://my.telegram.org |
| `BOT_TOKEN` | music bot token (`@Iotamusicbot`) |
| `OWNER_ID` | aapka user ID |
| `STRING_SESSION` | `python3 session.py` se |
| `MONGO_DB_URI` | music Mongo URI |
| `LOGGER_ID` | private log group ID (negative), ya `0` |
| `COOKIE_URL` | **zaroori YouTube ke liye** — raw Netscape cookies URL |
| `BOT_USERNAME` | `Iotamusicbot` |

`COOKIE_URL` kaise banao:
1. Browser se YouTube cookies export (Netscape `cookies.txt`)
2. Pastebin/Batbin pe **Unlisted** upload
3. **Raw** link copy → `COOKIE_URL=https://pastebin.com/raw/XXXX`

---

## 2) Manual deploy (agar Blueprint nahi)

### A) Economy bot (iota-bot)

- **New Web Service** → repo → root dir: `iota`
- Runtime: **Python 3.12**
- Build: `pip install -r requirements.txt`
- Start: `sh start.sh`
- Health check path: `/health`
- Env vars: upar wala iota-bot table

Deploy ke baad:
```
WEBAPP_BASE_URL=https://<your-iota-bot>.onrender.com
```
set karke **Manual Deploy** dubara.

### B) Music bot (iota-music) — Docker

- **New Web Service** → repo → root dir: `IotaXMusic`
- Environment: **Docker**
- Dockerfile path: `./Dockerfile`
- Health check path: `/health`
- Env vars: upar wala iota-music table

Docker isliye: `ffmpeg` + `deno` YouTube playback ke liye chahiye.

---

## 3) 24/7 kaise guarantee

| Layer | Economy (`iota-bot`) | Music (`iota-music`) |
|-------|----------------------|----------------------|
| Health route | `/` + `/health` (ludo server) | `/` + `/health` (`keep_alive.py`) |
| Self-ping | `_render_keepalive_job` har 5 min | `render_keepalive_job` har 5 min |
| Free sleep | self-ping se block | self-ping se block |

Agar free instance phir bhi so jaye:
- Render dashboard me service **awake** dikhao
- Logs me `Keep-alive self-ping` line check karo
- External cron (UptimeRobot) se bhi `https://YOUR.onrender.com/health` har 5 min hit kar sakte ho

**Paid Starter ($7)** = no sleep, better for serious 24/7.

---

## 4) Post-deploy checks

### Economy
```
Telegram → bot → /start
/ping
/ludo   (Mini App tab open hona chahiye agar WEBAPP_BASE_URL set hai)
```

### Music
```
Bot ko group me admin + VC rights
Assistant account ko group me add
/play udi udi
```

Logs (Render → Logs):
- Music: `YouTube cookies loaded…` + `Started Successfully`
- Economy: `MongoDB connected` / bot started

---

## 5) Local se env template

```bash
# economy
cp iota/config_template.py iota/config.py   # only if missing; secrets via env on Render

# music
cp IotaXMusic/sample.env IotaXMusic/.env
# fill .env, then:
cd IotaXMusic && bash start.sh
```

---

## 6) Common failures

| Problem | Fix |
|---------|-----|
| Music: Sign in / not a bot | Fresh `COOKIE_URL` (Netscape cookies) |
| Music: no VC sound | `STRING_SESSION` + assistant in group + VC on |
| Economy: commands fail | `MONGO_URI` / password + Atlas IP allow `0.0.0.0/0` |
| Service sleeps | free tier; confirm self-ping / use UptimeRobot |
| Build fail music | Docker runtime select karo (not plain Python) |
| Health check fail | `PORT` env = Render inject; code already uses `$PORT` |

---

## 7) Security checklist

- [ ] Bot tokens sirf Render env me
- [ ] Mongo password strong + Atlas network open for Render
- [ ] Cookies kabhi git me nahi
- [ ] Session string private
- [ ] Agar secrets leak hue → **rotate** BotFather token + Mongo password + session

---

## 8) Architecture (short)

```
GitHub (Iotaa)
   │
   ├─ Render Web: iota-bot     ──► Telegram long-poll + Ludo HTTPS + /health ping
   │
   └─ Render Web: iota-music   ──► Pyrogram bot + assistant + PyTgCalls
                                   + ffmpeg/deno + /health ping
```

Dono alag services = alag free instances = alag sleep timers, lekin har ek apna keep-alive chalaata hai.
