# Iota Bot — Render Deploy Guide (with Ludo + saare Mini Games)

Iota ek single Python process hai jo **Telegram bot (long-poll)** + **Ludo
Mini App web server** dono ek saath chalaata hai. Isliye ek hi Render **Web
Service** mein sab kuch deploy ho jaata hai — `/ludo`, `/tictactoe`,
`/rps`, `/hangman`, `/quiz`, `/slots`, `/bluff`, `/werewolf`, `/hack`, card,
bomb, dice, wordgame, village war, economy, AI chat — yani **saare games +
features ek saath**.

---

## Pehle: jo maine abhi verify + setup kiya (0 errors ke liye)

1. **Sabhi 56 Python files** `py_compile` se check kiye — koi syntax error nahi.
2. **Sabhi `bot.py` imports** (har handler ka har function) resolve ho rahe hain — koi missing name nahi.
3. **Ludo engine** simulate kiya (20k+ turns) — koi crash nahi, positions hamesha valid.
4. **Render ke liye fix kiya**:
   - `config.py` gitignored hai (secrets + tuning) → Render pe clone mein nahi aata tha → bot import pe crash hota. Isliye **`config_template.py`** (secret-free, env-driven) add kiya + `start.sh` jo `config.py` generate kar deta hai jab wo missing ho.
   - `webapp/ludo_server.py` mein **`/` aur `/health`** route add kiya (Render health-check ke liye).
   - `WEBAPP_PORT` ab `$PORT` se leta hai (Render ka port).
   - **24/7 keep-alive:** `bot.py` mein `_render_keepalive_job()` har 5 min apne
     public URL ki `/health` route self-ping karta hai (Render free tier ki
     15-min inactivity spin-down rokne ke liye). Render khud `RENDER_EXTERNAL_URL`
     set karta hai; warna `WEBAPP_BASE_URL` use hota hai. Bina URL ke auto-off.
5. `requirements.txt` complete hai (telegram, motor, pymongo, aiohttp, Pillow).

---

## Step 1 — GitHub pe push karo

Render GitHub se clone karta hai. Repo ready hai (`iota/` folder mein sab kuch hai):

```
iota/
 ├─ bot.py
 ├─ config_template.py   ← committed (secret-free)
 ├─ start.sh             ← executable
 ├─ Procfile
 ├─ render.yaml
 ├─ requirements.txt
 ├─ handlers/  utils/  webapp/  assets/
 └─ config.py           ← gitignored, local only (Render pe auto-bani)
```

```bash
git add -A
git commit -m "Add Render deploy config (env-driven config, start.sh, Procfile, render.yaml)"
git push origin main
```

> ⚠️ `config.py` commit mat karo — wo secrets rakhta hai aur gitignored hai.
> Render pe `config_template.py` se `config.py` apne-aap ban jaayega.

---

## Step 2 — Render pe Web Service banao

1. https://dashboard.render.com → **New + → Blueprints** (ya **Web Service**).
2. GitHub repo connect karo, branch `main` select karo.
3. Agar "Blueprints" choose kiya to `render.yaml` auto-detect ho jaayega.
   Warna manually:
   - **Environment:** `Python 3`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `sh start.sh`
   - **Plan:** Free (ya Starter for always-on)
   - **Health Check Path:** `/`

---

## Step 3 — Environment Variables set karo (Render → Environment)

Ye **zaruri** hain (bina inke bot start hi nahi hoga):

| Key | Value |
|-----|-------|
| `BOT_TOKEN` | `@BotFather` se mila bot token |
| `OWNER_ID` | tera Telegram user ID (e.g. `6998484205`) |
| `OWNER_USERNAME` | `@im_ntg` |
| `MONGO_URI` | MongoDB Atlas connection string (pura) |
| `WEBAPP_BASE_URL` | `https://<your-service>.onrender.com` |
| `GROQ_API_KEYS` | comma-separated Groq keys |
| `SARVAM_API_KEY` | Sarvam TTS key (optional) |
| `GIPHY_API_KEY` | GIPHY key (optional) |

Optional providers (blank chhod do to skip): `GEMINI_API_KEYS`,
`OPENROUTER_API_KEYS`, `CLOUDFLARE_API_KEYS`, `CLOUDFLARE_ACCOUNT_ID`.

> `PORT` Render khud deta hai — kuch mat karna.

---

## Step 4 — Deploy + verify

1. **Create Web Service** → Render build + start karega.
2. Logs mein dekho:
   - `📋 Registered N total handlers across M groups` (sab handlers load hue)
   - `🤖 Iota Bot LIVE!`
   - `🎲 Ludo Mini App server running on 0.0.0.0:PORT`
3. Browser mein `https://<your-service>.onrender.com/` open karo → `OK` aana chahiye (health check).

---

## Step 5 — Ludo Mini App ko Telegram se jodo

1. `@BotFather` → `/mybots` → Iota → **Mini App** → **Edit Menu Button / Add Mini App**.
2. URL daalo: `https://<your-service>.onrender.com/ludo`
3. Ab group mein `/ludo` bhejo → "🎮 Play Ludo" button dikhega (Mini App khulega).
4. `WEBAPP_BASE_URL` set hone se pehle `/ludo` classic chat-button mode mein chalta tha — ab full visual mode mein chalega.

---

## Troubleshooting (0 errors rakhne ke liye)

- **Bot start nahi ho raha / `Required env var ...` error:** Step 3 ke vars set karo.
- **`config.py` not found`:** `start.sh` automatically `config_template.py` se bana dega — manually mat banana.
- **`/ludo` ka "Play" button nahi dikhega:** `WEBAPP_BASE_URL` empty hai → set karo + BotFather mein Mini App URL add karo.
- **MongoDB errors:** `MONGO_URI` sahi hai aur Atlas IP access = `0.0.0.0/0` (Allow Access from Anywhere) hai.
- **Health check fail:** server `$PORT` pe bind ho raha hai (auto), thoda wait karo first boot mein.

---

## Local dev (bina env vars ke)

Local pe tera purana `config.py` (gitignored, secrets ke saath) waise hi kaam
karega — `start.sh` sirf tab `config.py` banata hai jab wo **missing** ho.
Local run: `python3 bot.py`.
