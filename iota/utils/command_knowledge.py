"""
Iota Bot — Command Knowledge Base (user-facing only)

Single source of truth for "what can Iota do" — used by BOTH:
  - /help's interactive menu (handlers/start.py)
  - the AI chat's self-knowledge (handlers/ai_chat.py's system prompt),
    so users can ask Iota herself "what commands do you have?" or
    "how do I use /rob?" and get an accurate, in-character answer.

🔒 CRITICAL SAFETY RULE — READ BEFORE EDITING THIS FILE:
Only include commands available to REGULAR USERS, GROUP ADMINS, or
GROUP-LEVEL features. NEVER add anything from the owner panel
(handlers/owner_panel.py) — broadcast, /setmodel, /scan, /resetuser,
/giveall, /maintenance, /dm, /addcoins, ban/unban, stars stats, etc.
Those must stay completely invisible to the AI's self-knowledge, or a
user could ask Iota "what owner commands exist?" and she'd have no way
to know NOT to answer, because the information would already be sitting
right there in her own prompt. Keeping owner commands out of this file
entirely means there is nothing for the AI to leak, even by mistake —
this is a stronger guarantee than trying to prompt-instruct the AI not
to repeat something it can already see.

Having ONE file that both /help and the AI draw from also means a
command added here shows up correctttly in both places automatically,
and a command that's accidentally missed is missed in both places
consistently (fail-safe, not fail-open).
"""

# Grouped by user-facing category. Kept in sync with handlers/start.py's
# /help menu content — if you add a command there, add it here too.
COMMAND_GROUPS = {
    "💰 Economy": [
        "/daily — claim your daily coins (free: manual, premium: auto too)",
        "/bal — check balance, rank, and status",
        "/rob <amount> (reply to someone) — rob coins from another user",
        "/kill (reply to someone) — take someone out for an hour",
        "/revive — revive yourself or someone else",
        "/protect 1d/2d — buy protection from robbery/kills",
        "/check — check protection time (private, DM only)",
        "/give — give coins to another user",
        "/wallet — deposit/withdraw between balance and wallet",
        "/toprich — richest players leaderboard",
        "/shop — spend your coins on items",
        "/slots <bet> — (disabled) use /bet or /roulette for PvP coin games",
        "/lottery buy — (disabled) use /bet or /roulette for PvP coin games",
    ],
    "🏦 Banking & Market": [
        "/bank — wallet + bank + savings overview and net worth",
        "/deposit <amount|all> — move coins wallet → bank (safe from /rob)",
        "/withdraw <amount|all> — move coins bank → wallet",
        "/transfer <@user|reply> <amount> — send coins to another user",
        "/savings deposit|withdraw <amount|all> — earn 2%/day interest on savings",
        "/loan <amount> — borrow coins (10% interest, due in 24h)",
        "/repay <amount|all> — repay an outstanding loan",
        "/networth — your total wealth (wallet + bank + savings − loan)",
        "/bazaar — buy & sell items for coins (catalog + player market); "
        "subcommands: buy <item> [qty], sell <item> [qty], "
        "list <item> <price> [qty], listings [page], buyid <id>, mine, cancel <id>",
    ],
    "🎮 Games": [
        "/card — card game (PvP)",
        "/bomb — bomb-passing game (PvP, free)",
        "/bluff — bluff card game (PvP)",
        "/werewolf — social deduction game (5-10 players, PvP)",
        "/hack — password hacking mini-game (PvP, min 250)",
        "/wordgame — word guess (free)",
        "/hangman — hangman (free)",
        "/quiz — AI-powered quiz (free)",
        "/tictactoe — tic tac toe (free PvP)",
        "/rps — (disabled vs Iota; use /tictactoe or /chess)",
        "/ludo — ludo (chat mode or visual Mini App, PvP)",
        "/leaders — leaderboard",
        "/roulette <amount> [coins|gems] — bid-elimination tournament "
        "(host starts, friends /rjoin to enter, then DM /bid each round; "
        "lowest bidder eliminated every round, last player wins the pot)",
        "/wheel — (disabled) use /bet or /roulette for PvP coin games",
        "/dice — (disabled vs Iota, use /bet or /roulette for PvP)",
    ],
    "🧸 Friends & Social": [
        "/slap /punch /kiss /hug /bite — fun action commands",
        "/murder /couples /crush /love /ship — social/fun commands",
        "/compliment /roast /truth /dare — fun interactions",
        "/puzzle /shayari /meme — entertainment",
        "/valentine — valentine event", "/last_seen — check when a user was last active",
    ],
    "🏰 Village War": [
        "/village — full empire dashboard", "/collect — harvest resources",
        "/storage /vault — check resources", "/build — upgrade buildings",
        "/walls /defense — fortify your village", "/train /troops — build your army",
        "/kingdom /spy — scout targets", "/attack — raid other players",
        "/settle /convert — manage currency", "/emperors — leaderboard",
        "/guide — full village war walkthrough",
    ],
    "🔗 Connect": [
        "/connect (reply to someone, or /connect @username) — request to share "
        "AI memory with a friend so Iota remembers consistently for both of you",
        "/disconnect — end an active connection early",
        "/connect_id — see your current connection status",
    ],
    "🤖 AI Chat": [
        "/ai <message> or /ask <message> — chat with Iota's AI",
        "/clearmemory — reset your AI chat memory",
        "In DMs: just message directly. In groups: @mention, reply to Iota, or say her name.",
    ],
    "🎭 Quote Stickers": [
        "/q (reply to a message) — turn that message into a Telegram-style quote sticker",
        "/q r — also show the reply-context bubble above the quote",
        "/q <1-10> — quote a short thread (last N messages)",
    ],
    "🛠️ Group Admin (admins only)": [
        ".warn .ban .mute .kick — moderation actions (admins only)",
        "/setwelcome — customize the welcome message (admins only)",
        "/setrules — set group rules (admins only)",
        "/lock /unlock — lock down message types (admins only)",
        "/close /open — enable/disable gaming commands in a group (admins only)",
    ],
    "🤝 Promoter": [
        "/promoter — get your referral link",
        "/ref_stats — check your referral earnings",
        "/refer_top — top promoters leaderboard",
    ],
    "🆕 Extra Tools": [
        "/pin /unpin /purge — message management (admins only)",
        "/avatar — see a user's profile photo",
        "/8ball /joke /fact /riddle /wyr — fun & trivia",
        "/reverse /mock /binary /morse /hash /password — text toys",
        "/nickname — set what Iota calls you",
        "/birthday DD-MM — Iota wishes you on that day",
        "/todo — personal to-do list",
        "/countdown EventName YYYY-MM-DD — days-left counter",
        "/giveaway <minutes> <prize> — run a giveaway (admins only)",
    ],
}


def build_help_text() -> str:
    """Full plain-text summary (e.g. /help). Prefer build_help_text_compact for AI."""
    lines = []
    for category, cmds in COMMAND_GROUPS.items():
        lines.append(category)
        for c in cmds:
            lines.append(f"  {c}")
    return "\n".join(lines)


def build_help_text_compact() -> str:
    """
    Token-cheap feature blurb for the AI system prompt.
    Full command dump was ~2–4k tokens per turn and burned free-tier limits
    in 3–4 messages.
    """
    lines = [
        "Main features users can ask about (short answers only):",
        "• Economy: /daily /bal /rob /kill /protect /give /shop",
        "• Bank: /bank /deposit /withdraw /transfer /loan /bazaar",
        "• PvP Games (users only, money games min bet 250): /bet /card /roulette /ludo /hack /bluff /werewolf /tictactoe /chess /uno /connect4 /bomb",
        "• Free games: /bomb /hangman /quiz /wordgame /chess /uno /connect4",
        "• Fun: /slap /kiss /hug /truth /dare /meme",
        "• Social: /valentine /last_seen /connect",
        "• Village: village war cmds if they ask",
        "• Group admin (if they are admin): /setwelcome /setrules /prot /lock /warn",
        "• Disabled vs-Iota games: use PvP alternatives like /bet /roulette /ludo",
        "Owner/admin internal tools: you do NOT know them — say you don't know.",
    ]
    return "\n".join(lines)
