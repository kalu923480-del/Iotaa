"""
Iota Bot — ENV-DRIVEN CONFIG TEMPLATE  (committed to the repo)

This file is the CANONICAL, secret-free configuration. It is copied to
`config.py` at deploy/start time by `start.sh` ONLY when a real `config.py`
does not already exist (e.g. on Render, where the gitignored local
`config.py` is not in the cloned repo).

All SECRETS and deploy-specific values are read from environment variables.
Set these on Render (or in your shell) — see the deploy guide. If a secret is
missing, the bot will refuse to start with a clear error instead of silently
failing on every command.

All non-secret TUNING constants (economy numbers, items, village, etc.) live
here inline so they are version-controlled and identical everywhere.
"""
import os
from urllib.parse import quote_plus


def _env(name: str, default: str = "") -> str:
    """Read an env var; returns '' (never None) when unset."""
    return os.environ.get(name, default) or ""


def _require(name: str) -> str:
    """Read a required secret; raise a clear error if missing."""
    val = _env(name)
    if not val:
        raise RuntimeError(
            f"❌ Required env var {name} is not set. "
            f"Set it in your deployment environment (e.g. Render dashboard)."
        )
    return val


# ── Bot credentials ───────────────────────────────────────────────────────
BOT_TOKEN = _require("BOT_TOKEN")

# ── Owner identity ────────────────────────────────────────────────────────
OWNER_ID       = int(_env("OWNER_ID", "0") or "0")
OWNER_USERNAME = _env("OWNER_USERNAME", "@im_ntg")
OWNER_NAME     = _env("OWNER_NAME", "ɴᴏᴛʜɪɴɢ")
# Normalize legacy/stale values so a forgotten old username left in the
# deploy env (e.g. Render dashboard) can never resurface as the bot's
# identity. The canonical owner handle is @im_ntg.
if OWNER_USERNAME in ("@Boobies_00", "@Boobies_007", "@owner", ""):
    OWNER_USERNAME = "@im_ntg"
if not OWNER_NAME or OWNER_NAME in ("Owner", ""):
    OWNER_NAME = "ɴᴏᴛʜɪɴɢ"

# ── Bot's own identity ────────────────────────────────────────────────────
BOT_NAME       = _env("BOT_NAME", "Iota")
BOT_USERNAME   = _env("BOT_USERNAME", "Its_iotabot")
BOT_AGE        = int(_env("BOT_AGE", "17") or "17")
# Iota's private-background facts. These are NOT revealed in normal chat —
# the AI only shares them when the user explicitly asks (see ai_chat.py).
BOT_FROM       = _env("BOT_FROM", "Delhi, India")
BOT_DOB        = _env("BOT_DOB", "9 March 2009")

# ── Update channel (leave blank to hide the button) ──────────────────────
UPDATE_CHANNEL_USERNAME = _env("UPDATE_CHANNEL_USERNAME", "")

# ── Ludo Mini App ─────────────────────────────────────────────────────────
# On Render, WEBAPP_BASE_URL should be your live Render URL, e.g.
# https://iota-bot.onrender.com  (set via env). WEBAPP_PORT is taken from
# Render's $PORT so the Mini App server binds to the port Render expects.
WEBAPP_BASE_URL = _env("WEBAPP_BASE_URL", "")
WEBAPP_PORT     = int(_env("PORT", _env("WEBAPP_PORT", "8080")) or "8080")

# ── MongoDB ───────────────────────────────────────────────────────────────
# Preferred: provide a FULL connection string via MONGO_URI.
# Fallback: provide MONGO_USER + MONGO_PASS + MONGO_CLUSTER and we build it.
_MONGO_USER  = _env("MONGO_USER", "kalu923476")
_MONGO_PASS  = _env("MONGO_PASS", "")
_MONGO_CLUSTER = _env("MONGO_CLUSTER", "cluster0.tjpjh4k.mongodb.net")
if _env("MONGO_URI"):
    MONGO_URI = _env("MONGO_URI")
else:
    if not _MONGO_PASS:
        raise RuntimeError(
            "❌ Set MONGO_URI (full connection string) or MONGO_PASS on Render."
        )
    MONGO_URI = (
        f"mongodb+srv://{_MONGO_USER}:{quote_plus(_MONGO_PASS)}"
        f"@{_MONGO_CLUSTER}/iota_bot"
        f"?retryWrites=true&w=majority&appName=Cluster0"
    )
DB_NAME = _env("DB_NAME", "iota_bot")

# ── Sarvam (TTS) ──────────────────────────────────────────────────────────
# Bulbul v3 is the current TTS model (37 voices: 23 male + 14 female).
# `tts_engine.py` also auto-fetches the live voice catalogue from SARVAM_VOICES_URL
# and clones custom voices via SARVAM_TTS_CLONE_URL — both overridable here.
SARVAM_API_KEY       = _env("SARVAM_API_KEY", "")
SARVAM_CHAT_URL      = _env("SARVAM_CHAT_URL", "https://api.sarvam.ai/v1/chat/completions")
SARVAM_TTS_URL       = _env("SARVAM_TTS_URL", "https://api.sarvam.ai/text-to-speech")
SARVAM_VOICES_URL    = _env("SARVAM_VOICES_URL", "https://api.sarvam.ai/voices")
SARVAM_TTS_CLONE_URL = _env("SARVAM_TTS_CLONE_URL", "https://api.sarvam.ai/text-to-speech/clone")

# ── AI Providers ──────────────────────────────────────────────────────────
# Provide keys as a single space/comma/newline-joined string in each env var.
def _list_env(name: str, fallback: list) -> list:
    raw = _env(name)
    if not raw:
        return list(fallback)
    return [k.strip() for k in raw.replace("\n", ",").split(",") if k.strip()]

GROQ_API_KEYS = _list_env("GROQ_API_KEYS", [])
GEMINI_API_KEYS = _list_env("GEMINI_API_KEYS", [])
OPENROUTER_API_KEYS = _list_env("OPENROUTER_API_KEYS", [])
CLOUDFLARE_API_KEYS = _list_env("CLOUDFLARE_API_KEYS", [])
CLOUDFLARE_ACCOUNT_ID = _env("CLOUDFLARE_ACCOUNT_ID", "")

# ── Economy ───────────────────────────────────────────────────────────────
DAILY_NORMAL        = 500
DAILY_PREMIUM       = 1250
DAILY_KILLS_NORMAL  = 200
DAILY_KILLS_PREMIUM = 400
DAILY_ROBS_NORMAL   = 150
DAILY_ROBS_PREMIUM  = 300
ROB_MAX_NORMAL      = 10_000
ROB_MAX_PREMIUM     = 100_000

# ── Weekly / Monthly (used by /weekly and /monthly in handlers/economy.py)
# Scaled up from the daily amounts (≈7× and ≈30×) so the bigger cooldown
# feels worth the wait.
WEEKLY_NORMAL       = 3_000
WEEKLY_PREMIUM      = 7_500
MONTHLY_NORMAL      = 15_000
MONTHLY_PREMIUM     = 37_500
HIGH_VALUE_THEFT_THRESHOLD = 5_000
TAX_NORMAL          = 0.10
TAX_PREMIUM         = 0.05
KILL_REWARD_NORMAL  = (100, 200)
KILL_REWARD_PREMIUM = (200, 400)
XP_KILL_NORMAL      = (0,   5)
XP_KILL_PREMIUM     = (10, 20)
XP_ROB_PER_1K       = 1
XP_PER_LEVEL        = 1000
LEVEL_UP_COIN_BASE  = 200
CLAN_CREATE_COST    = 5_000
CLAN_MAX_MEMBERS    = 20
CLAN_NAME_MIN       = 3
CLAN_NAME_MAX       = 24
CLAN_TAG_MIN        = 2
CLAN_TAG_MAX        = 5
EVENT_DEFAULT_HOURS = 6
REVIVE_COST         = 600
PROTECT_1D_COST     = 400
PROTECT_2D_COST     = 1000

# ── Premium ───────────────────────────────────────────────────────────────
PREMIUM_PRICE_COINS    = 50_000
PREMIUM_PRICE_STARS    = 100
PREMIUM_DURATION_DAYS  = 90
GEMS_PRICE_STARS       = 50
GEMS_PRICE_COINS       = 10_000

# ── Premium Banking System ──────────────────────────────────────────────────
# The entire banking system (handlers/banking.py) is PREMIUM-ONLY and modelled
# after real-life retail banking: a demand-deposit bank account (safe from
# /rob), an interest-bearing savings vault, Fixed Deposits (FD), Recurring
# Deposits (RD), a transaction passbook/statement, and user-owned Banks/Branches
# that premium users can open once they hold 10 lakh (1,000,000) coins.
PREMIUM_BANKING_CAP      = 1_000_000   # 10 lakh — max bank balance for a premium user
BANK_OPEN_MIN_BALANCE    = 1_000_000   # need 10 lakh to open your own bank/branch

# Demand-deposit (bank account) interest, compounded daily.
BANK_DAILY_RATE         = 0.005        # 0.5%/day on the bank balance

# Fixed Deposit (FD): total return over the chosen lock-in tenure.
FD_TENURES               = {30: 0.06, 90: 0.12, 180: 0.22, 365: 0.45}
FD_BREAK_PENALTY        = 0.10         # penalty if broken before maturity (on payout)

# Recurring Deposit (RD): a fixed monthly installment, compounded monthly.
RD_MIN_INSTALLMENT      = 1_000
RD_MAX_MONTHS           = 24
RD_MONTHLY_RATE         = 0.01         # 1%/month on contributions
RD_BREAK_PENALTY        = 0.10

# User-owned Banks/Branches: the bank pays its customers interest daily and the
# owner earns a one-time fee on each customer deposit + interest on the reserve.
BANK_CUSTOMER_DAILY_RATE = 0.008       # bank pays its depositors 0.8%/day
BANK_OWNER_DEPOSIT_FEE   = 0.01        # owner keeps 1% of every customer deposit
BANK_RATE_MIN            = 0.003       # owner-settable customer rate bounds
BANK_RATE_MAX            = 0.015

# ── Premium Business Empire (Enterprise Edition) ─────────────────────────────
# The entire business system (handlers/business.py + utils/business_store.py) is
# PREMIUM-ONLY and modelled after a real-life business tycoon / ERP simulation.
#
#   • A premium user opens businesses (one or several, up to their ownership tier)
#     across many categories: food, retail, services, hospitality, health,
#     industry, tech, transport, finance, education, agriculture and leisure.
#   • Each type is DATA-DRIVEN — every economic number lives in BUSINESS_TYPES so
#     adding a new business (or rebalancing) is a config edit, never a code change.
#   • A business earns PASSIVE income into its till (capped), so the owner must
#     /bizcollect regularly. Income scales with LEVEL, hired employees (role
#     bonuses + their skill/efficiency), pooled investor capital, POPULARITY,
#     REPUTATION (customer ratings) and whether the minimum staffing requirement
#     is met. MAINTENANCE and TAXES are deducted on collection, so profit is real.
#   • The owner hires other users as employees (Manager / Chef / Cashier / …).
#     Roles boost income; employees gain XP/level, earn daily salaries (auto-paid
#     from the owner wallet; auto-resign after too many unpaid days). Owners can
#     promote, demote, bonus and penalise staff.
#   • Any user can invest coins to become a shareholder — pro-rata dividends from
#     every /bizcollect plus a small income bonus to the business. 100 investors is
#     a celebrated milestone.
#   • Customers leave 1–5★ ratings & reviews; reputation feeds back into income.
#   • Some businesses are GLOBALLY LIMITED (e.g. one Airport, one Central Bank, one
#     Theme Park). Once taken, no one else can open one and the public sees the
#     owner, value and status.
#   • A rival premium user can /robbiz the owner's UNCOLLECTED till (guards reduce
#     the take) — idle tills are a real risk, exactly like cash on the counter.
# Every coin move is atomic (Motor $inc / gated update_one), so the economy can
# never desync and money can never be duplicated.

# ── Business categories (for UI grouping / filters) ──────────────────────────
BUSINESS_CATEGORIES = {
    "food":        "🍴 Food & Beverage",
    "retail":      "🛍️ Retail & Shops",
    "service":     "🛠️ Services",
    "hospitality": "🏨 Hospitality",
    "health":      "⚕️ Health & Care",
    "industry":    "🏭 Industry & Production",
    "tech":        "💻 Tech & Media",
    "transport":   "🚚 Transport & Logistics",
    "finance":     "🏦 Finance & Real Estate",
    "education":   "🎓 Education",
    "agriculture": "🌾 Agriculture",
    "leisure":     "🎡 Leisure & Entertainment",
}

# Each type's per-field meaning:
#   name, emoji, category        — display
#   cost                         — opening capital (locked from owner wallet)
#   income_per_hour              — base gross income at Lv1, full staffing, neutral rep
#   max_employees                — hard cap on hired staff
#   min_employees                — staffing requirement for FULL income (fewer = scaled down)
#   store_hours                  — hours of income the till can hold before it stops accruing
#   maintenance_per_day          — daily upkeep (rent, electricity, water, internet, repairs)
#   popularity                   — starting popularity 0–100 (marketing/customer flow)
#   customer_capacity            — max customers served per accrual tick
#   tax_rate                     — business tax on gross collected income
#   license_fee                  — one-time fee paid on top of cost to register
#   global_limit                 — 0 = unlimited; N = at most N such businesses exist
#   risk_factor                  — 0–1, chance-weighting for future random-event exposure
#   description                  — short flavour / what it is
BUSINESS_TYPES = {
    # ── Food & Beverage ──
    "tea_shop":        {"name": "Tea Shop",        "emoji": "🍵", "category": "food", "cost": 500_000,     "income_per_hour": 5_000,  "max_employees": 3,  "min_employees": 1, "store_hours": 12, "maintenance_per_day": 40_000,  "popularity": 55, "customer_capacity": 80,  "tax_rate": 0.05, "license_fee": 50_000,  "global_limit": 0,  "risk_factor": 0.03, "description": "A cosy corner chai stall."},
    "coffee_shop":     {"name": "Coffee Shop",     "emoji": "☕", "category": "food", "cost": 900_000,     "income_per_hour": 9_000,  "max_employees": 4,  "min_employees": 1, "store_hours": 12, "maintenance_per_day": 70_000,  "popularity": 58, "customer_capacity": 120, "tax_rate": 0.05, "license_fee": 90_000,  "global_limit": 0,  "risk_factor": 0.03, "description": "Specialty brews & pastries."},
    "juice_shop":      {"name": "Juice Shop",      "emoji": "🧃", "category": "food", "cost": 600_000,     "income_per_hour": 6_000,  "max_employees": 3,  "min_employees": 1, "store_hours": 12, "maintenance_per_day": 45_000,  "popularity": 52, "customer_capacity": 90,  "tax_rate": 0.05, "license_fee": 60_000,  "global_limit": 0,  "risk_factor": 0.03, "description": "Fresh squeezed goodness."},
    "bakery":          {"name": "Bakery",          "emoji": "🥐", "category": "food", "cost": 1_100_000,   "income_per_hour": 11_000, "max_employees": 4,  "min_employees": 2, "store_hours": 12, "maintenance_per_day": 80_000,  "popularity": 60, "customer_capacity": 140, "tax_rate": 0.05, "license_fee": 110_000, "global_limit": 0,  "risk_factor": 0.04, "description": "Bread, cakes & confectionery."},
    "restaurant":      {"name": "Restaurant",      "emoji": "🍽️", "category": "food", "cost": 2_500_000,   "income_per_hour": 28_000, "max_employees": 6,  "min_employees": 3, "store_hours": 14, "maintenance_per_day": 200_000, "popularity": 65, "customer_capacity": 220, "tax_rate": 0.06, "license_fee": 200_000, "global_limit": 0,  "risk_factor": 0.05, "description": "Full-service dining."},
    "fast_food":       {"name": "Fast Food",       "emoji": "🍔", "category": "food", "cost": 3_000_000,   "income_per_hour": 33_000, "max_employees": 8,  "min_employees": 3, "store_hours": 14, "maintenance_per_day": 230_000, "popularity": 62, "customer_capacity": 300, "tax_rate": 0.06, "license_fee": 250_000, "global_limit": 0,  "risk_factor": 0.05, "description": "Quick burgers & fries."},
    "cafe":            {"name": "Cafe",            "emoji": "🍮", "category": "food", "cost": 1_000_000,   "income_per_hour": 10_000, "max_employees": 4,  "min_employees": 1, "store_hours": 12, "maintenance_per_day": 75_000,  "popularity": 57, "customer_capacity": 130, "tax_rate": 0.05, "license_fee": 100_000, "global_limit": 0,  "risk_factor": 0.03, "description": "Relaxed eats & desserts."},

    # ── Retail & Shops ──
    "supermarket":     {"name": "Super Market",    "emoji": "🛒", "category": "retail", "cost": 4_000_000, "income_per_hour": 46_000, "max_employees": 8, "min_employees": 4, "store_hours": 16, "maintenance_per_day": 350_000, "popularity": 68, "customer_capacity": 400, "tax_rate": 0.07, "license_fee": 300_000, "global_limit": 0, "risk_factor": 0.05, "description": "Groceries for the whole block."},
    "electronics_shop":{"name": "Electronics Shop","emoji": "📱", "category": "retail", "cost": 3_500_000, "income_per_hour": 40_000, "max_employees": 6, "min_employees": 2, "store_hours": 14, "maintenance_per_day": 280_000, "popularity": 60, "customer_capacity": 200, "tax_rate": 0.08, "license_fee": 300_000, "global_limit": 0, "risk_factor": 0.06, "description": "Phones, laptops & gadgets."},
    "jewelry_shop":    {"name": "Jewelry Shop",    "emoji": "💍", "category": "retail", "cost": 6_000_000, "income_per_hour": 70_000, "max_employees": 4, "min_employees": 2, "store_hours": 14, "maintenance_per_day": 400_000, "popularity": 55, "customer_capacity": 120, "tax_rate": 0.10, "license_fee": 500_000, "global_limit": 0, "risk_factor": 0.08, "description": "High-margin precious wares."},
    "clothing_brand":  {"name": "Clothing Brand",  "emoji": "👕", "category": "retail", "cost": 3_200_000, "income_per_hour": 36_000, "max_employees": 6, "min_employees": 3, "store_hours": 14, "maintenance_per_day": 260_000, "popularity": 58, "customer_capacity": 240, "tax_rate": 0.07, "license_fee": 250_000, "global_limit": 0, "risk_factor": 0.05, "description": "Apparel & fashion."},
    "furniture_shop":  {"name": "Furniture Shop",  "emoji": "🪑", "category": "retail", "cost": 2_800_000, "income_per_hour": 30_000, "max_employees": 5, "min_employees": 2, "store_hours": 14, "maintenance_per_day": 220_000, "popularity": 52, "customer_capacity": 160, "tax_rate": 0.07, "license_fee": 200_000, "global_limit": 0, "risk_factor": 0.05, "description": "Home & office furniture."},
    "medical_store":   {"name": "Medical Store",   "emoji": "💊", "category": "retail", "cost": 1_800_000, "income_per_hour": 19_000, "max_employees": 4, "min_employees": 1, "store_hours": 12, "maintenance_per_day": 140_000, "popularity": 60, "customer_capacity": 150, "tax_rate": 0.05, "license_fee": 150_000, "global_limit": 0, "risk_factor": 0.04, "description": "Pharmacy & health supplies."},
    "pet_shop":        {"name": "Pet Shop",        "emoji": "🐾", "category": "retail", "cost": 1_500_000, "income_per_hour": 16_000, "max_employees": 4, "min_employees": 1, "store_hours": 12, "maintenance_per_day": 120_000, "popularity": 57, "customer_capacity": 140, "tax_rate": 0.05, "license_fee": 120_000, "global_limit": 0, "risk_factor": 0.04, "description": "Pets, food & accessories."},

    # ── Services ──
    "garage":          {"name": "Garage",          "emoji": "🔧", "category": "service", "cost": 1_500_000, "income_per_hour": 16_000, "max_employees": 5, "min_employees": 2, "store_hours": 14, "maintenance_per_day": 130_000, "popularity": 58, "customer_capacity": 160, "tax_rate": 0.06, "license_fee": 120_000, "global_limit": 0, "risk_factor": 0.06, "description": "Vehicle repair & service."},
    "car_showroom":    {"name": "Car Showroom",    "emoji": "🚗", "category": "service", "cost": 5_000_000, "income_per_hour": 58_000, "max_employees": 5, "min_employees": 2, "store_hours": 14, "maintenance_per_day": 380_000, "popularity": 56, "customer_capacity": 120, "tax_rate": 0.09, "license_fee": 400_000, "global_limit": 0, "risk_factor": 0.07, "description": "New & used car sales."},
    "bike_showroom":   {"name": "Bike Showroom",   "emoji": "🏍️", "category": "service", "cost": 2_200_000, "income_per_hour": 24_000, "max_employees": 4, "min_employees": 1, "store_hours": 14, "maintenance_per_day": 170_000, "popularity": 55, "customer_capacity": 140, "tax_rate": 0.08, "license_fee": 180_000, "global_limit": 0, "risk_factor": 0.06, "description": "Two-wheelers & gear."},
    "salon":           {"name": "Salon",           "emoji": "💇", "category": "service", "cost": 1_200_000, "income_per_hour": 13_000, "max_employees": 4, "min_employees": 2, "store_hours": 12, "maintenance_per_day": 100_000, "popularity": 56, "customer_capacity": 120, "tax_rate": 0.05, "license_fee": 100_000, "global_limit": 0, "risk_factor": 0.04, "description": "Hair, styling & grooming."},
    "spa":             {"name": "Spa",             "emoji": "🧖", "category": "service", "cost": 2_000_000, "income_per_hour": 22_000, "max_employees": 5, "min_employees": 2, "store_hours": 12, "maintenance_per_day": 160_000, "popularity": 54, "customer_capacity": 100, "tax_rate": 0.06, "license_fee": 160_000, "global_limit": 0, "risk_factor": 0.05, "description": "Relaxation & wellness."},
    "vet":             {"name": "Veterinary Hosp.","emoji": "🐶", "category": "service", "cost": 1_600_000, "income_per_hour": 17_000, "max_employees": 4, "min_employees": 1, "store_hours": 12, "maintenance_per_day": 130_000, "popularity": 58, "customer_capacity": 120, "tax_rate": 0.05, "license_fee": 130_000, "global_limit": 0, "risk_factor": 0.05, "description": "Animal care & clinic."},
    "law_firm":        {"name": "Law Firm",        "emoji": "⚖️", "category": "service", "cost": 4_500_000, "income_per_hour": 52_000, "max_employees": 6, "min_employees": 3, "store_hours": 12, "maintenance_per_day": 340_000, "popularity": 50, "customer_capacity": 100, "tax_rate": 0.10, "license_fee": 400_000, "global_limit": 0, "risk_factor": 0.06, "description": "Legal counsel & representation."},

    # ── Hospitality ──
    "hotel":           {"name": "Hotel",           "emoji": "🏨", "category": "hospitality", "cost": 7_000_000, "income_per_hour": 82_000, "max_employees": 10, "min_employees": 5, "store_hours": 18, "maintenance_per_day": 600_000, "popularity": 66, "customer_capacity": 350, "tax_rate": 0.08, "license_fee": 500_000, "global_limit": 0, "risk_factor": 0.07, "description": "Rooms, dining & events."},
    "resort":          {"name": "Resort",          "emoji": "🏖️", "category": "hospitality", "cost": 9_000_000, "income_per_hour": 105_000, "max_employees": 10, "min_employees": 4, "store_hours": 18, "maintenance_per_day": 720_000, "popularity": 60, "customer_capacity": 300, "tax_rate": 0.09, "license_fee": 700_000, "global_limit": 0, "risk_factor": 0.08, "description": "Beachside luxury escapes."},
    "international_hotel": {"name": "International Hotel", "emoji": "🌐", "category": "hospitality", "cost": 15_000_000, "income_per_hour": 180_000, "max_employees": 14, "min_employees": 6, "store_hours": 20, "maintenance_per_day": 1_100_000, "popularity": 70, "customer_capacity": 500, "tax_rate": 0.10, "license_fee": 1_000_000, "global_limit": 1, "risk_factor": 0.12, "description": "Five-star flagship — globally unique."},
    "cinema":          {"name": "Cinema",          "emoji": "🎬", "category": "hospitality", "cost": 5_500_000, "income_per_hour": 64_000, "max_employees": 8, "min_employees": 2, "store_hours": 16, "maintenance_per_day": 420_000, "popularity": 60, "customer_capacity": 400, "tax_rate": 0.07, "license_fee": 400_000, "global_limit": 5, "risk_factor": 0.06, "description": "Multiplex movie house."},

    # ── Health & Care ──
    "hospital":        {"name": "Hospital",        "emoji": "🏥", "category": "health", "cost": 12_000_000, "income_per_hour": 140_000, "max_employees": 16, "min_employees": 8, "store_hours": 24, "maintenance_per_day": 1_000_000, "popularity": 68, "customer_capacity": 500, "tax_rate": 0.08, "license_fee": 800_000, "global_limit": 10, "risk_factor": 0.10, "description": "Multi-specialty care."},
    "gym":             {"name": "Gym",             "emoji": "💪", "category": "health", "cost": 2_000_000, "income_per_hour": 22_000, "max_employees": 6, "min_employees": 2, "store_hours": 16, "maintenance_per_day": 170_000, "popularity": 57, "customer_capacity": 260, "tax_rate": 0.05, "license_fee": 150_000, "global_limit": 0, "risk_factor": 0.04, "description": "Fitness & training."},

    # ── Tech & Media ──
    "it_company":      {"name": "IT Company",      "emoji": "💻", "category": "tech", "cost": 6_000_000, "income_per_hour": 70_000, "max_employees": 12, "min_employees": 5, "store_hours": 18, "maintenance_per_day": 450_000, "popularity": 58, "customer_capacity": 300, "tax_rate": 0.09, "license_fee": 500_000, "global_limit": 5, "risk_factor": 0.07, "description": "Software services & support."},
    "software_company": {"name": "Software Company","emoji": "🧑‍💻", "category": "tech", "cost": 8_000_000, "income_per_hour": 95_000, "max_employees": 14, "min_employees": 6, "store_hours": 18, "maintenance_per_day": 600_000, "popularity": 56, "customer_capacity": 300, "tax_rate": 0.09, "license_fee": 600_000, "global_limit": 5, "risk_factor": 0.08, "description": "Products & platforms."},
    "game_studio":     {"name": "Game Studio",     "emoji": "🎮", "category": "tech", "cost": 7_000_000, "income_per_hour": 84_000, "max_employees": 12, "min_employees": 5, "store_hours": 18, "maintenance_per_day": 520_000, "popularity": 57, "customer_capacity": 250, "tax_rate": 0.09, "license_fee": 550_000, "global_limit": 5, "risk_factor": 0.09, "description": "Game development."},
    "music_studio":    {"name": "Music Studio",    "emoji": "🎵", "category": "tech", "cost": 2_500_000, "income_per_hour": 27_000, "max_employees": 5, "min_employees": 2, "store_hours": 12, "maintenance_per_day": 190_000, "popularity": 55, "customer_capacity": 120, "tax_rate": 0.06, "license_fee": 200_000, "global_limit": 0, "risk_factor": 0.05, "description": "Recording & production."},

    # ── Industry & Production ──
    "factory":         {"name": "Factory",         "emoji": "🏭", "category": "industry", "cost": 12_000_000, "income_per_hour": 150_000, "max_employees": 12, "min_employees": 6, "store_hours": 24, "maintenance_per_day": 1_000_000, "popularity": 55, "customer_capacity": 400, "tax_rate": 0.10, "license_fee": 800_000, "global_limit": 0, "risk_factor": 0.12, "description": "Mass manufacturing."},
    "mining_company":  {"name": "Mining Company",  "emoji": "⛏️", "category": "industry", "cost": 10_000_000, "income_per_hour": 120_000, "max_employees": 10, "min_employees": 5, "store_hours": 24, "maintenance_per_day": 900_000, "popularity": 50, "customer_capacity": 300, "tax_rate": 0.12, "license_fee": 700_000, "global_limit": 10, "risk_factor": 0.18, "description": "Resource extraction."},
    "diamond_mine":    {"name": "Diamond Mine",    "emoji": "💎", "category": "industry", "cost": 18_000_000, "income_per_hour": 210_000, "max_employees": 12, "min_employees": 6, "store_hours": 24, "maintenance_per_day": 1_400_000, "popularity": 48, "customer_capacity": 250, "tax_rate": 0.15, "license_fee": 1_200_000, "global_limit": 1, "risk_factor": 0.20, "description": "Rare gems — globally unique."},
    "energy_company":  {"name": "Energy Company",  "emoji": "⚡", "category": "industry", "cost": 11_000_000, "income_per_hour": 130_000, "max_employees": 10, "min_employees": 5, "store_hours": 24, "maintenance_per_day": 950_000, "popularity": 52, "customer_capacity": 300, "tax_rate": 0.11, "license_fee": 800_000, "global_limit": 5, "risk_factor": 0.14, "description": "Power generation & supply."},
    "construction_company": {"name": "Construction Co.", "emoji": "🏗️", "category": "industry", "cost": 5_000_000, "income_per_hour": 58_000, "max_employees": 10, "min_employees": 5, "store_hours": 18, "maintenance_per_day": 420_000, "popularity": 53, "customer_capacity": 300, "tax_rate": 0.09, "license_fee": 400_000, "global_limit": 0, "risk_factor": 0.10, "description": "Building & infra."},

    # ── Transport & Logistics ──
    "transport_company": {"name": "Transport Co.", "emoji": "🚚", "category": "transport", "cost": 6_000_000, "income_per_hour": 70_000, "max_employees": 10, "min_employees": 4, "store_hours": 18, "maintenance_per_day": 500_000, "popularity": 54, "customer_capacity": 350, "tax_rate": 0.09, "license_fee": 500_000, "global_limit": 5, "risk_factor": 0.08, "description": "Freight & logistics."},
    "shipping_company": {"name": "Shipping Co.",   "emoji": "🚢", "category": "transport", "cost": 9_000_000, "income_per_hour": 105_000, "max_employees": 10, "min_employees": 5, "store_hours": 20, "maintenance_per_day": 760_000, "popularity": 52, "customer_capacity": 300, "tax_rate": 0.10, "license_fee": 700_000, "global_limit": 5, "risk_factor": 0.09, "description": "Sea freight & ports."},
    "airline":         {"name": "Airline",         "emoji": "✈️", "category": "transport", "cost": 16_000_000, "income_per_hour": 190_000, "max_employees": 16, "min_employees": 8, "store_hours": 20, "maintenance_per_day": 1_300_000, "popularity": 55, "customer_capacity": 400, "tax_rate": 0.11, "license_fee": 1_000_000, "global_limit": 5, "risk_factor": 0.16, "description": "Passenger & cargo flights."},
    "logistics_company": {"name": "Logistics Co.", "emoji": "📦", "category": "transport", "cost": 5_500_000, "income_per_hour": 64_000, "max_employees": 10, "min_employees": 4, "store_hours": 18, "maintenance_per_day": 480_000, "popularity": 53, "customer_capacity": 350, "tax_rate": 0.09, "license_fee": 450_000, "global_limit": 0, "risk_factor": 0.08, "description": "Warehousing & last-mile."},
    "travel_agency":   {"name": "Travel Agency",   "emoji": "🧳", "category": "transport", "cost": 2_000_000, "income_per_hour": 22_000, "max_employees": 5, "min_employees": 2, "store_hours": 12, "maintenance_per_day": 160_000, "popularity": 56, "customer_capacity": 200, "tax_rate": 0.06, "license_fee": 150_000, "global_limit": 0, "risk_factor": 0.05, "description": "Holidays & bookings."},

    # ── Finance & Real Estate ──
    "bank":            {"name": "Bank",            "emoji": "🏦", "category": "finance", "cost": 20_000_000, "income_per_hour": 230_000, "max_employees": 16, "min_employees": 8, "store_hours": 24, "maintenance_per_day": 1_500_000, "popularity": 60, "customer_capacity": 500, "tax_rate": 0.12, "license_fee": 1_500_000, "global_limit": 10, "risk_factor": 0.10, "description": "Retail & commercial banking."},
    "insurance_company": {"name": "Insurance Co.", "emoji": "🛡️", "category": "finance", "cost": 8_000_000, "income_per_hour": 95_000, "max_employees": 10, "min_employees": 5, "store_hours": 18, "maintenance_per_day": 650_000, "popularity": 55, "customer_capacity": 300, "tax_rate": 0.11, "license_fee": 600_000, "global_limit": 5, "risk_factor": 0.09, "description": "Risk & protection products."},
    "real_estate":     {"name": "Real Estate Co.", "emoji": "🏘️", "category": "finance", "cost": 6_500_000, "income_per_hour": 76_000, "max_employees": 8, "min_employees": 4, "store_hours": 18, "maintenance_per_day": 520_000, "popularity": 54, "customer_capacity": 250, "tax_rate": 0.10, "license_fee": 500_000, "global_limit": 0, "risk_factor": 0.07, "description": "Property brokerage."},
    "central_bank":    {"name": "Central Bank",    "emoji": "🏛️", "category": "finance", "cost": 40_000_000, "income_per_hour": 460_000, "max_employees": 20, "min_employees": 10, "store_hours": 24, "maintenance_per_day": 2_500_000, "popularity": 62, "customer_capacity": 600, "tax_rate": 0.12, "license_fee": 3_000_000, "global_limit": 1, "risk_factor": 0.10, "description": "The economy's reserve — globally unique."},

    # ── Education ──
    "school":          {"name": "School",          "emoji": "🏫", "category": "education", "cost": 4_000_000, "income_per_hour": 46_000, "max_employees": 10, "min_employees": 5, "store_hours": 16, "maintenance_per_day": 350_000, "popularity": 62, "customer_capacity": 400, "tax_rate": 0.04, "license_fee": 300_000, "global_limit": 0, "risk_factor": 0.04, "description": "Primary & secondary."},
    "college":         {"name": "College",         "emoji": "🎓", "category": "education", "cost": 8_000_000, "income_per_hour": 95_000, "max_employees": 14, "min_employees": 7, "store_hours": 18, "maintenance_per_day": 700_000, "popularity": 60, "customer_capacity": 500, "tax_rate": 0.04, "license_fee": 600_000, "global_limit": 5, "risk_factor": 0.05, "description": "Higher education."},
    "university":      {"name": "University",      "emoji": "📚", "category": "education", "cost": 14_000_000, "income_per_hour": 165_000, "max_employees": 18, "min_employees": 9, "store_hours": 18, "maintenance_per_day": 1_200_000, "popularity": 61, "customer_capacity": 600, "tax_rate": 0.04, "license_fee": 1_000_000, "global_limit": 5, "risk_factor": 0.05, "description": "Research & degrees."},

    # ── Agriculture ──
    "farm":            {"name": "Farm",            "emoji": "🌾", "category": "agriculture", "cost": 3_000_000, "income_per_hour": 33_000, "max_employees": 8, "min_employees": 3, "store_hours": 18, "maintenance_per_day": 240_000, "popularity": 50, "customer_capacity": 300, "tax_rate": 0.03, "license_fee": 200_000, "global_limit": 0, "risk_factor": 0.07, "description": "Crops & produce."},
    "fish_farm":       {"name": "Fish Farm",       "emoji": "🐟", "category": "agriculture", "cost": 2_400_000, "income_per_hour": 26_000, "max_employees": 6, "min_employees": 2, "store_hours": 18, "maintenance_per_day": 200_000, "popularity": 48, "customer_capacity": 220, "tax_rate": 0.03, "license_fee": 180_000, "global_limit": 0, "risk_factor": 0.08, "description": "Aquaculture."},
    "poultry_farm":    {"name": "Poultry Farm",    "emoji": "🐔", "category": "agriculture", "cost": 2_000_000, "income_per_hour": 22_000, "max_employees": 6, "min_employees": 2, "store_hours": 18, "maintenance_per_day": 180_000, "popularity": 48, "customer_capacity": 200, "tax_rate": 0.03, "license_fee": 150_000, "global_limit": 0, "risk_factor": 0.08, "description": "Eggs & poultry."},

    # ── Leisure & Entertainment ──
    "theme_park":      {"name": "Theme Park",     "emoji": "🎢", "category": "leisure", "cost": 20_000_000, "income_per_hour": 240_000, "max_employees": 20, "min_employees": 8, "store_hours": 20, "maintenance_per_day": 1_600_000, "popularity": 65, "customer_capacity": 800, "tax_rate": 0.10, "license_fee": 1_500_000, "global_limit": 1, "risk_factor": 0.15, "description": "Rides & attractions — globally unique."},
    "zoo":             {"name": "Zoo",             "emoji": "🦁", "category": "leisure", "cost": 9_000_000, "income_per_hour": 100_000, "max_employees": 12, "min_employees": 5, "store_hours": 18, "maintenance_per_day": 800_000, "popularity": 58, "customer_capacity": 500, "tax_rate": 0.07, "license_fee": 700_000, "global_limit": 5, "risk_factor": 0.09, "description": "Wildlife & exhibits."},
}

# Employee roles a business owner can hire other users into. Beyond the original
# six, the enterprise edition adds many real-world roles. Each role carries:
#   name, emoji           — display
#   income_bonus          — fraction added to the business's income (at Lv1 skill)
#   daily_salary          — paid to the employee once per day (auto, owner wallet)
#   skill                 — base efficiency 0–1 (higher = more of income_bonus realised)
#   xp_per_day            — XP gained per day employed (drives levelling)
#   category              — for UI grouping
# A `guard` role also reduces how much a robber can steal (see BUSINESS_GUARD_PROTECT).
BUSINESS_ROLES = {
    "manager":          {"name": "Manager",          "emoji": "🧑‍💼", "income_bonus": 0.15, "daily_salary": 8_000,   "skill": 0.85, "xp_per_day": 12, "category": "management"},
    "assistant_manager": {"name": "Assistant Manager","emoji": "🧑‍💼", "income_bonus": 0.10, "daily_salary": 6_000,   "skill": 0.75, "xp_per_day": 10, "category": "management"},
    "accountant":       {"name": "Accountant",       "emoji": "🧮", "income_bonus": 0.08, "daily_salary": 5_500,   "skill": 0.80, "xp_per_day": 10, "category": "management"},
    "hr":               {"name": "HR",               "emoji": "🤝", "income_bonus": 0.06, "daily_salary": 5_000,   "skill": 0.70, "xp_per_day": 9,  "category": "management"},
    "marketing_head":   {"name": "Marketing Head",   "emoji": "📣", "income_bonus": 0.12, "daily_salary": 7_000,   "skill": 0.80, "xp_per_day": 11, "category": "management"},
    "sales_manager":    {"name": "Sales Manager",    "emoji": "📈", "income_bonus": 0.12, "daily_salary": 6_500,   "skill": 0.80, "xp_per_day": 11, "category": "management"},
    "supervisor":       {"name": "Supervisor",       "emoji": "👷", "income_bonus": 0.07, "daily_salary": 4_500,   "skill": 0.70, "xp_per_day": 9,  "category": "operations"},
    "chef":             {"name": "Chef",             "emoji": "👨‍🍳", "income_bonus": 0.10, "daily_salary": 5_000,   "skill": 0.80, "xp_per_day": 9,  "category": "operations"},
    "cashier":          {"name": "Cashier",          "emoji": "💰", "income_bonus": 0.08, "daily_salary": 4_000,   "skill": 0.70, "xp_per_day": 8,  "category": "operations"},
    "waiter":           {"name": "Waiter",           "emoji": "🧑‍🍽️", "income_bonus": 0.05, "daily_salary": 3_000,   "skill": 0.60, "xp_per_day": 7,  "category": "operations"},
    "receptionist":     {"name": "Receptionist",     "emoji": "🛎️", "income_bonus": 0.05, "daily_salary": 3_200,   "skill": 0.65, "xp_per_day": 7,  "category": "operations"},
    "cleaner":          {"name": "Cleaner",          "emoji": "🧹", "income_bonus": 0.03, "daily_salary": 2_000,   "skill": 0.55, "xp_per_day": 6,  "category": "operations"},
    "driver":           {"name": "Driver",           "emoji": "🚗", "income_bonus": 0.04, "daily_salary": 2_800,   "skill": 0.60, "xp_per_day": 7,  "category": "operations"},
    "delivery_boy":     {"name": "Delivery Boy",     "emoji": "🛵", "income_bonus": 0.04, "daily_salary": 2_600,   "skill": 0.60, "xp_per_day": 7,  "category": "operations"},
    "warehouse_staff":  {"name": "Warehouse Staff",  "emoji": "📦", "income_bonus": 0.04, "daily_salary": 2_700,   "skill": 0.60, "xp_per_day": 7,  "category": "operations"},
    "guard":            {"name": "Security Guard",   "emoji": "💂", "income_bonus": 0.04, "daily_salary": 2_500,   "skill": 0.65, "xp_per_day": 7,  "category": "security"},
    "mechanic":         {"name": "Mechanic",         "emoji": "🔧", "income_bonus": 0.09, "daily_salary": 4_800,   "skill": 0.75, "xp_per_day": 9,  "category": "technical"},
    "engineer":         {"name": "Engineer",         "emoji": "⚙️", "income_bonus": 0.12, "daily_salary": 7_500,   "skill": 0.85, "xp_per_day": 11, "category": "technical"},
    "technician":       {"name": "Technician",       "emoji": "🛠️", "income_bonus": 0.07, "daily_salary": 4_200,   "skill": 0.72, "xp_per_day": 9,  "category": "technical"},
    "developer":        {"name": "Developer",        "emoji": "👨‍💻", "income_bonus": 0.13, "daily_salary": 8_500,   "skill": 0.85, "xp_per_day": 11, "category": "technical"},
    "doctor":           {"name": "Doctor",           "emoji": "🩺", "income_bonus": 0.14, "daily_salary": 9_000,   "skill": 0.90, "xp_per_day": 12, "category": "care"},
    "nurse":            {"name": "Nurse",            "emoji": "🧑‍⚕️", "income_bonus": 0.08, "daily_salary": 4_500,   "skill": 0.75, "xp_per_day": 9,  "category": "care"},
    "teacher":          {"name": "Teacher",          "emoji": "🧑‍🏫", "income_bonus": 0.09, "daily_salary": 4_000,   "skill": 0.78, "xp_per_day": 9,  "category": "care"},
    "vet":              {"name": "Veterinarian",     "emoji": "🐶", "income_bonus": 0.09, "daily_salary": 4_500,   "skill": 0.78, "xp_per_day": 9,  "category": "care"},
    "pilot":            {"name": "Pilot",            "emoji": "✈️", "income_bonus": 0.13, "daily_salary": 9_500,   "skill": 0.88, "xp_per_day": 12, "category": "technical"},
    "lawyer":           {"name": "Lawyer",           "emoji": "⚖️", "income_bonus": 0.11, "daily_salary": 7_000,   "skill": 0.85, "xp_per_day": 11, "category": "management"},
}

# ── Ownership tiers (Premium-only) ──────────────────────────────────────────
# Maps a user's premium tier to the maximum number of businesses they may own.
# Tier is stored on the user doc as `premium_tier` (default "premium" when the
# user is premium). `BUSINESS_MAX_BUSINESSES_DEFAULT` is the fallback for any
# premium user without an explicit tier.
BUSINESS_OWNERSHIP_TIERS = {
    "premium":       1,    # Normal Premium
    "premium_plus":  3,    # Premium Plus
    "vip":           10,   # VIP
}
BUSINESS_MAX_BUSINESSES_DEFAULT = 1   # fallback for premium users with no tier set

# ── Stricter unlock rules ───────────────────────────────────────────────────
# A user may open a business only if ALL of these hold:
#   • is_premium is True
#   • balance >= cost + license_fee
#   • they own fewer than their tier's max businesses
#   • the type's global_limit (if any) is not already exhausted
#   • at least BUSINESS_OPEN_COOLDOWN seconds have passed since they last opened
#     (or closed) a business.
# The "100 investors" requirement from the brief is treated as a celebrated
# MILESTONE (BUSINESS_INVESTOR_MILESTONE) — not a hard gate — so the first
# business in the ecosystem can exist.
BUSINESS_OPEN_COOLDOWN    = 24 * 3600   # 24h between opening/closing businesses
BUSINESS_MIN_OPEN_COINS   = 500_000     # informational floor shown in help/UX

# Employees can boost income by at most this combined fraction.
BUSINESS_EMP_BONUS_CAP = 2.00
# Even an under-staffed business (below min_employees) still operates at this
# fraction of capacity — the owner keeps the doors open, so a till can always
# accrue and be held / robbed (never 0).
BUSINESS_STAFFING_FLOOR = 0.30
# Employee auto-resigns after this many consecutive unpaid salary days.
BUSINESS_SALARY_UNPAID_MAX = 3
# Promotions / demotions.
BUSINESS_EMP_MAX_LEVEL   = 10
BUSINESS_EMP_LEVEL_BONUS = 0.04   # each employee level adds +4% realised efficiency
BUSINESS_PROMOTE_COST    = 25_000 # owner pays this from wallet to promote an employee
BUSINESS_DEMOTE_PENALTY  = 0.10   # efficiency multiplier applied on demotion
BUSINESS_BONUS_MIN       = 1_000  # minimum one-off bonus an owner can give
BUSINESS_XP_PER_LEVEL    = 100    # XP needed to reach the next employee level

# Upgrades: each level multiplies income & the till cap. Max level + per-level
# income step, and the cost of the next level = base cost × level × cost factor.
BUSINESS_MAX_LEVEL       = 10
BUSINESS_LEVEL_INCOME_STEP = 0.25        # +25% income per level above 1
BUSINESS_UPGRADE_COST_FACTOR = 0.60      # next-level cost = type_cost × level × 0.60

# Closing a business refunds this fraction of its ORIGINAL opening cost to the
# owner (like selling the premises); every investor's principal is returned in full.
BUSINESS_SELL_REFUND = 0.50

# Investors: other users put coins in to become shareholders. On each /bizcollect
# a fraction of the AFTER-TAX profit is split among investors pro-rata to their
# stake. Their pooled capital also gives the business a small income bonus
# (capped), and they can /divest their principal back (a small early-exit fee).
BUSINESS_INVESTOR_PROFIT_SHARE = 0.20    # 20% of after-tax profit shared to investors
BUSINESS_INVEST_PER_BONUS      = 200_000 # every 2 lakh invested = +1% income …
BUSINESS_INVEST_BONUS_MAX      = 0.30    # … capped at +30%
BUSINESS_INVEST_MIN            = 10_000  # minimum single investment
BUSINESS_DIVEST_FEE            = 0.05    # 5% fee when pulling investment out early
BUSINESS_INVESTOR_MILESTONE    = 100     # "100 investors" celebrated badge

# Economy shaping of income.
BUSINESS_POPULARITY_START = 50       # popularity floored here for brand-new businesses
BUSINESS_REPUTATION_FLOOR = 0.30     # reputation multiplier floor (bad ratings still earn)
BUSINESS_CUSTOMERS_PER_TICK = 1.0    # customers served per accrual tick scale (tuned in code)
BUSINESS_DEFAULT_RISK = 0.05         # risk_factor default when a type omits it

# Robbery: a rival PREMIUM user can steal a slice of income left uncollected in
# the till. Guards reduce the take; a cooldown limits how often you can rob.
BUSINESS_ROB_PCT         = 0.20          # base: steal 20% of the uncollected till
BUSINESS_ROB_MAX         = 250_000       # hard cap on a single business robbery
BUSINESS_GUARD_PROTECT   = 0.05          # each Security Guard cuts the take by 5%
BUSINESS_ROB_COOLDOWN    = 6 * 3600      # 6h between business robberies (per robber)
BUSINESS_ROB_MIN_TILL    = 5_000         # till must hold at least this to be worth robbing

# ── Card ──────────────────────────────────────────────────────────────────
CARD_FEE_PERCENT = 5
CARD_XP_WIN      = 250
CARD_XP_LOSS     = 50
CARD_MIN_BET     = 10
CARD_MAX_BET     = 100_000
CARD_LOBBY_TIMEOUT_SECONDS = 90

# ── Items ─────────────────────────────────────────────────────────────────
ITEMS = {
    "rose":         ("🌹", 500),
    "chocolate":    ("🍫", 800),
    "ring":         ("💍", 2000),
    "teddy":        ("🧸", 1500),
    "pizza":        ("🍕", 600),
    "surprise_box": ("🎁", 2500),
    "puppy":        ("🐶", 3000),
    "cake":         ("🎂", 1000),
    "love_letter":  ("💌", 400),
    "cat":          ("🐱", 2500),
    "tulip":        ("🌷", 1500),
    "bmw":          ("🏎", 5000),
    "diamond":      ("💎", 8000),
    "crown":        ("👑", 15000),
}

# ── Village ───────────────────────────────────────────────────────────────
MINE_INTERVAL   = 3600
CITIZEN_START   = 50
TROOP_TYPES = {
    "warriors": {"hp":50,  "damage":15, "cost_coins":100},
    "archers":  {"hp":30,  "damage":25, "cost_coins":150},
    "knights":  {"hp":100, "damage":20, "cost_coins":200},
    "mages":    {"hp":40,  "damage":40, "cost_coins":300},
}
WALL_TYPES = {
    "wood":  {"hp":300,  "cost_wood":200},
    "stone": {"hp":700,  "cost_stone":300},
    "iron":  {"hp":1500, "cost_iron":400},
}
DEFENSE_TYPES = {
    "archer_tower": {"hp":300, "damage":20, "cost_coins":500},
    "cannon":       {"hp":500, "damage":35, "cost_coins":800},
}
MARKET_PRICES = {"wood": 50, "stone": 120, "iron": 300}
MARKET_SELL_RATIO = 0.7

GLOBAL_COUPONS: dict = {
    "nobi10":  1000,
    "iota50":  5000,
    "welcome": 500,
}

# ── GIFs (optional — get your own free GIPHY key) ─────────────────────────
GIPHY_API_KEY = _env("GIPHY_API_KEY", "")
