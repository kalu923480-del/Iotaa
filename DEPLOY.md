# Iota Monorepo — Separate Render Accounts (24/7)

**Load kam = har bot alag free instance / alag Render account.**

| Bot | Folder | Deploy guide | Root Directory | Runtime |
|-----|--------|--------------|----------------|---------|
| Economy / games / AI | `iota/` | [iota/DEPLOY.md](iota/DEPLOY.md) | `iota` | Python |
| Music VC | `IotaXMusic/` | [IotaXMusic/DEPLOY.md](IotaXMusic/DEPLOY.md) | `IotaXMusic` | **Docker** |

Root `render.yaml` has **no services** — so a root Blueprint will not start both by mistake.

```
GitHub monorepo (Iotaa)
        │
        ├─► Render Account A  →  Root: iota         →  iota-bot      (Python + /health)
        │
        └─► Render Account B  →  Root: IotaXMusic   →  iota-music    (Docker + /health)
```

Same GitHub repo can connect to both accounts — only **Root Directory** differs.

---

## Quick start

### Account A — Economy (`iota`)
1. Render A → **New Web Service** → this repo  
2. **Root Directory:** `iota`  
3. Build: `pip install -U pip && pip install -r requirements.txt && python3 -c "from utils.font_manager import ensure_fonts; ensure_fonts()"`  
4. Start: `sh start.sh`  
5. Health: `/health`  
6. Env: `BOT_TOKEN`, `OWNER_ID`, `MONGO_URI`  
7. After first deploy: `WEBAPP_BASE_URL=https://YOUR-iota-bot.onrender.com` → redeploy  

### Account B — Music (`IotaXMusic`)
1. Render B (different account) → **New Web Service**  
2. **Root Directory:** `IotaXMusic`  
3. **Environment: Docker** · Dockerfile `./Dockerfile`  
4. Health: `/health`  
5. Env: `API_ID`, `API_HASH`, `BOT_TOKEN`, `OWNER_ID`, `STRING_SESSION`, `MONGO_DB_URI`  
6. Cookies: Secret File `cookies.txt` **or** `COOKIE_URL`  
7. Details: [IotaXMusic/DEPLOY.md](IotaXMusic/DEPLOY.md)

---

## 24/7 (both bots)

| Feature | Economy (`iota`) | Music (`IotaXMusic`) |
|---------|------------------|----------------------|
| `/health` | yes | yes |
| Self-ping every 5 min | yes (`RENDER_EXTERNAL_URL` / `WEBAPP_BASE_URL`) | yes (`RENDER_EXTERNAL_URL` / `KEEPALIVE_URL`) |
| Extra monitor | UptimeRobot → `/health` | UptimeRobot → `/health` |

Free tier may still restart; for serious 24/7 VC use a paid instance.

---

## Security

Never git-commit: `.env`, `cookies.txt`, `*.session`, secret `config.py` values.  
If leaked: rotate tokens / Mongo / sessions.
