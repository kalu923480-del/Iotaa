# Iota Economy Bot — Alag Render Account pe Deploy (24/7)

Sirf **`iota/`** folder. Music bot isme nahi hai — wo alag account pe chalao  
(`IotaXMusic/DEPLOY.md`) taaki free-tier load alag rahe.

---

## Account A setup (sirf economy bot)

### 1) Render account
Naya / alag Render account → GitHub connect → repo: same monorepo OK.

### 2) New Web Service (manual — clearest)

| Field | Value |
|-------|--------|
| **Root Directory** | `iota` |
| **Runtime** | Python 3 |
| **Build Command** | `pip install -U pip && pip install -r requirements.txt && python3 -c "from utils.font_manager import ensure_fonts; ensure_fonts()"` |
| **Start Command** | `sh start.sh` |
| **Health Check Path** | `/health` |
| **Plan** | Free |

### 3) Environment variables

| Key | Required | Notes |
|-----|----------|--------|
| `BOT_TOKEN` | yes | economy bot token |
| `OWNER_ID` | yes | aapka Telegram ID |
| `OWNER_USERNAME` | yes | e.g. `@im_ntg` |
| `MONGO_URI` | yes | full `mongodb+srv://...` |
| `WEBAPP_BASE_URL` | after 1st deploy | `https://YOUR-service.onrender.com` |
| `PORT` | auto | Render sets this |
| `GROQ_API_KEYS` | optional | AI |
| `GIPHY_API_KEY` | optional | GIFs |

### 4) Deploy → set WEBAPP_BASE_URL → redeploy once

Logs me keep-alive dikhega (self-ping `/health` every 5 min = 24/7 free).

### 5) Test
Telegram → `/start` `/ping` `/ludo`

---

## Blueprint alternative

**New → Blueprint** → repo → **Root Directory = `iota`** → uses `iota/render.yaml`.

Secrets fill karo → Apply.

---

## Important

- Is service me **music bot mat chalao**
- Secrets sirf is account ke Environment me
- `config.py` git se nahi aata — `start.sh` template se banata hai
