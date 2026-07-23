"""Central game economy + PvP rules for Iota."""
try:
    from config import GAME_MIN_BET, GAME_MAX_BET, GAME_FEE_PERCENT
except Exception:
    GAME_MIN_BET = 250
    GAME_MAX_BET = 100_000
    GAME_FEE_PERCENT = 5

PVP_GAMES_HINT = (
    "🎮 <b>PvP Games</b> (users vs users only):\n"
    f"• /bet &lt;amount&gt; — card duel (min {GAME_MIN_BET})\n"
    f"• /roulette &lt;amount&gt; — bid battle (min {GAME_MIN_BET})\n"
    f"• /ludo [bet] — 2–4 players (free or min {GAME_MIN_BET})\n"
    f"• /hack &lt;reward&gt; — puzzle race (min {GAME_MIN_BET})\n"
    "• Free PvP: /card · /bluff · /werewolf · /chess · /uno · /connect4 · /tictactoe · /bomb\n"
)


def parse_bet(args, default=None):
    """Return (amount, error_html). amount None if error."""
    if not args:
        if default is not None:
            return default, None
        return None, f"❌ Usage: /bet <amount>\nBet range: {GAME_MIN_BET} – {GAME_MAX_BET}"
    try:
        amount = int(args[0])
    except ValueError:
        return None, "❌ Invalid amount! Bet must be a whole number (no decimals/letters)."
    ok, err = validate_bet(amount)
    if not ok:
        return None, err
    return amount, None


def validate_bet(amount: int):
    """Check min/max. Returns (ok, error_or_empty)."""
    if amount < GAME_MIN_BET:
        return False, f"❌ Minimum bet is {GAME_MIN_BET}!"
    if amount > GAME_MAX_BET:
        return False, f"❌ Maximum bet is {GAME_MAX_BET}!"
    return True, ""


def pot_payout(pot: int, *, premium: bool = False) -> int:
    """Apply fee unless premium."""
    if premium:
        return pot
    return int(pot * (100 - GAME_FEE_PERCENT) / 100)


def bot_game_disabled_msg(game_name: str) -> str:
    """HTML message: this is bot/solo-money, use PvP instead."""
    return (
        f"🚫 <b>{game_name} (vs Iota) is disabled.</b>\n"
        f"Iota no longer plays money games against a single user — "
        f"all competitive coin games are <b>PvP only</b>.\n\n"
        f"{PVP_GAMES_HINT}"
    )


async def ensure_can_afford(uid, amount) -> tuple:
    """Check balance via get_user. Returns (ok, balance, err)."""
    from utils.mongo_db import get_user
    d = await get_user(uid)
    if d.get("balance", 0) < amount:
        return False, d.get("balance", 0), f"❌ Need {amount}, you have {d.get('balance', 0)}"
    return True, d.get("balance", 0), ""
