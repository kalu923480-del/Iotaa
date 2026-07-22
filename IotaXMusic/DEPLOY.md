# Iota Music Bot — Alag Render Account pe Deploy (24/7)

Sirf **`IotaXMusic/`** folder. Economy bot isme nahi — wo alag account pe  
(`iota/DEPLOY.md`) taaki free-tier sleep/load alag rahe.

---

## Account B setup (sirf music bot)

### 1) Alag Render account
Economy wale se **different** Render login/account use karo (load split).

### 2) New Web Service — Docker (recommended)

| Field | Value |
|-------|--------|
| **Root Directory** | `IotaXMusic` |
| **Environment** | **Docker** |
| **Dockerfile Path** | `./Dockerfile` |
| **Docker Context** | `.` |
| **Health Check Path** | `/health` |
| **Plan** | Free |

Docker = `ffmpeg` + `deno` included (YouTube ke liye zaroori).

### 3) Environment variables

| Key | Required | Notes |
|-----|----------|--------|
| `API_ID` | yes | my.telegram.org |
| `API_HASH` | yes | my.telegram.org |
| `BOT_TOKEN` | yes | @Iotamusicbot token |
| `OWNER_ID` | yes | aapka user ID |
| `STRING_SESSION` | yes | `python3 session.py` se |
| `MONGO_DB_URI` | yes | music Mongo URI |
| `LOGGER_ID` | recommended | log group ID, ya `0` |
| `COOKIE_URL` | **yes for YT** | raw Netscape cookies URL |
| `BOT_USERNAME` | yes | `Iotamusicbot` |
| `PORT` | auto | Render injects |

### COOKIE_URL
1. Browser se YouTube Netscape `cookies.txt` export  
2. Pastebin Unlisted → **Raw** link  
3. `COOKIE_URL=https://pastebin.com/raw/XXXX`

### 4) Deploy → logs check

Expect:
```
YouTube cookies loaded…
Iota Music Robot Started Successfully…
Health server listening…
```

### 5) Test
Group me bot + assistant admin → VC on → `/play udi udi`

---

## Blueprint alternative

**New → Blueprint** → same repo → **Root Directory = `IotaXMusic`**  
→ uses `IotaXMusic/render.yaml` only (economy service create nahi hoga).

---

## Important

- Is account pe **iota economy bot mat chalao**
- `.env` / `cookies.txt` / `*.session` git me mat daalo
- Free tier: bot `/health` self-ping karta hai (24/7). Optional: UptimeRobot same URL
