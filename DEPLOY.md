# Iota Monorepo — Alag-Alag Render Accounts (recommended)

**Load kam = har bot alag free instance / alag Render account.**

| Bot | Folder | Deploy guide | Render Root Directory |
|-----|--------|--------------|------------------------|
| Economy / games / AI | `iota/` | **[iota/DEPLOY.md](iota/DEPLOY.md)** | `iota` |
| Music VC | `IotaXMusic/` | **[IotaXMusic/DEPLOY.md](IotaXMusic/DEPLOY.md)** | `IotaXMusic` |

Root `render.yaml` me **koi service nahi** — galti se dono ek account pe na chalein.

```
GitHub monorepo (Iotaa)
        │
        ├─► Render Account A  →  Root Dir: iota         →  iota-bot
        │
        └─► Render Account B  →  Root Dir: IotaXMusic   →  iota-music (Docker)
```

Same GitHub repo dono accounts se connect ho sakta hai — bas **Root Directory** alag rakho.

---

## Quick start

### Account A — Economy
1. Render A → New Web Service → repo  
2. Root Directory: **`iota`**  
3. Build / Start / Health: see [iota/DEPLOY.md](iota/DEPLOY.md)  
4. Env: `BOT_TOKEN`, `OWNER_ID`, `MONGO_URI`, then `WEBAPP_BASE_URL`

### Account B — Music
1. Render B → New Web Service → **same** repo (optional)  
2. Root Directory: **`IotaXMusic`**  
3. **Docker** + Dockerfile `./Dockerfile`  
4. Env: `API_ID`, `API_HASH`, `BOT_TOKEN`, `STRING_SESSION`, `MONGO_DB_URI`, `COOKIE_URL`  
5. Details: [IotaXMusic/DEPLOY.md](IotaXMusic/DEPLOY.md)

---

## 24/7 (dono me built-in)

- `/health` endpoint  
- Self-ping every ~5 minutes (Render free sleep rokne ke liye)  
- Extra: UptimeRobot → `https://YOUR.onrender.com/health` every 5 min  

---

## Security

Kabhi git me mat daalo: `.env`, `cookies.txt`, `*.session`, `iota/config.py` secrets.

Agar leak ho: BotFather token / Mongo password / session rotate karo.
