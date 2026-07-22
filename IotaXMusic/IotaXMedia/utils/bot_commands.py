# Authored By Iota Coders © 2025
"""Curated Telegram command menu for Iota Music Bot.

Normal users (BotCommandScopeDefault) see only public + admin commands.
The bot owner (BotCommandScopeChat(OWNER_ID)) additionally sees the
owner/sudo-only commands, so those stay hidden from everyone else.
"""
from pyrogram.types import BotCommand

# ── Shown to everyone in the / menu ──────────────────────────────────────
DEFAULT_COMMANDS = [
    BotCommand("start", "˹ sᴛᴀʀᴛ ᴛʜᴇ ʙᴏᴛ"),
    BotCommand("help", "˹ ɢᴇᴛ ʜᴇʟᴩ"),
    BotCommand("settings", "˹ ᴏᴘᴇɴ sᴇᴛᴛɪɴɢs"),
    BotCommand("play", "˹ ᴘʟᴀʏ ᴀ sᴏɴɢ/ᴠɪᴅᴇᴏ"),
    BotCommand("vplay", "˹ ᴘʟᴀʏ ᴠɪᴅᴇᴏ"),
    BotCommand("stream", "˹ sᴛʀᴇᴀᴍ ᴀ ʟɪᴠᴇ/ᴜʀʟ"),
    BotCommand("pause", "˹ ᴘᴀᴜsᴇ ᴛʜᴇ sᴛʀᴇᴀᴍ"),
    BotCommand("resume", "˹ ʀᴇsᴜᴍᴇ ᴛʜᴇ sᴛʀᴇᴀᴍ"),
    BotCommand("skip", "˹ sᴋɪᴘ ᴛʜᴇ ᴄᴜʀʀᴇɴᴛ ᴛʀᴀᴄᴋ"),
    BotCommand("end", "˹ ᴇɴᴅ ᴛʜᴇ sᴛʀᴇᴀᴍ"),
    BotCommand("queue", "˹ sʜᴏᴡ ᴛʜᴇ ǫᴜᴇᴜᴇ"),
    BotCommand("loop", "˹ ʟᴏᴏᴩ ᴛʜᴇ ᴄᴜʀʀᴇɴᴛ sᴏɴɢ"),
    BotCommand("shuffle", "˹ sʜᴜғғʟᴇ ᴛʜᴇ ǫᴜᴇᴜᴇ"),
    BotCommand("seek", "˹ sᴇᴇᴋ ɪɴ ᴛʜᴇ sᴛʀᴇᴀᴍ"),
    BotCommand("speed", "˹ ᴄʜᴀɴɢᴇ ᴘʟᴀʏʙᴀᴄᴋ sᴘᴇᴇᴅ"),
    BotCommand("playmode", "˹ sᴇᴛ ᴛʜᴇ ᴘʟᴀʏ ᴍᴏᴅᴇ"),
    BotCommand("channelplay", "˹ ᴘʟᴀʏ ᴠɪᴀ ᴄʜᴀɴɴᴇʟ"),
    BotCommand("auth", "˹ ᴀᴜᴛʜ ᴀ ᴜsᴇʀ"),
    BotCommand("unauth", "˹ ʀᴇᴍᴏᴠᴇ ᴀᴜᴛʜ"),
    BotCommand("userbotjoin", "˹ ᴊᴏɪɴ ᴀssɪsᴛᴀɴᴛ ᴛᴏ ᴄʜᴀᴛ"),
    BotCommand("lang", "˹ ᴄʜᴀɴɢᴇ ʟᴀɴɢᴜᴀɢᴇ"),
    BotCommand("info", "˹ ᴜsᴇʀ ɪɴғᴏ"),
    BotCommand("id", "˹ ɢᴇᴛ ɪᴅs"),
    BotCommand("stats", "˹ ʙᴏᴛ sᴛᴀᴛs"),
    BotCommand("gstats", "˹ ɢʟᴏʙᴀʟ sᴛᴀᴛs"),
    BotCommand("activevc", "˹ ᴀᴄᴛɪᴠᴇ ᴠᴏɪᴄᴇ ᴄʜᴀᴛs"),
    BotCommand("activev", "˹ ᴀᴄᴛɪᴠᴇ ᴠɪᴅᴇᴏ ᴄʜᴀᴛs"),
    BotCommand("admincache", "˹ ʀᴇғʀᴇsʜ ᴀᴅᴍɪɴ ᴄᴀᴄʜᴇ"),
    BotCommand("purge", "˹ ᴅᴇʟᴇᴛᴇ ᴍsɢs"),
    BotCommand("del", "˹ ᴅᴇʟᴇᴛᴇ ᴀ ᴍsɢ"),
    BotCommand("promote", "˹ ᴘʀᴏᴍᴏᴛᴇ ᴀɴ ᴀᴅᴍɪɴ"),
    BotCommand("fullpromote", "˹ ꜰᴜʟʟ ᴘʀᴏᴍᴏᴛᴇ"),
    BotCommand("demote", "˹ ᴅᴇᴍᴏᴛᴇ ᴀɴ ᴀᴅᴍɪɴ"),
    BotCommand("ban", "˹ ʙᴀɴ ᴀ ᴜsᴇʀ"),
    BotCommand("unban", "˹ ᴜɴʙᴀɴ ᴀ ᴜsᴇʀ"),
    BotCommand("mute", "˹ ᴍᴜᴛᴇ ᴀ ᴜsᴇʀ"),
    BotCommand("unmute", "˹ ᴜɴᴍᴜᴛᴇ ᴀ ᴜsᴇʀ"),
    BotCommand("kick", "˹ ᴋɪᴄᴋ ᴀ ᴜsᴇʀ"),
    BotCommand("tmute", "˹ ᴛᴇᴍᴩ ᴍᴜᴛᴇ"),
    BotCommand("tban", "˹ ᴛᴇᴍᴩ ʙᴀɴ"),
    BotCommand("pin", "˹ ᴘɪɴ ᴀ ᴍsɢ"),
    BotCommand("unpin", "˹ ᴜɴᴘɪɴ ᴀ ᴍsɢ"),
    BotCommand("setphoto", "˹ sᴇᴛ ᴄʜᴀᴛ ᴘɪᴄ"),
    BotCommand("welcome", "˹ ᴛᴏɢɢʟᴇ ᴡᴇʟᴄᴏᴍᴇ"),
    BotCommand("tagall", "˹ ᴛᴀɢ ᴀʟʟ ᴍᴇᴍʙᴇʀs"),
    BotCommand("shayari", "˹ sᴇɴᴅ sʜᴀʏᴀʀɪ"),
    BotCommand("stickerid", "˹ ɢᴇᴛ sᴛɪᴄᴋᴇʀ ɪᴅ"),
    BotCommand("speedtest", "˹ ʀᴜɴ ᴀ sᴘᴇᴇᴅ ᴛᴇsᴛ"),
    BotCommand("github", "˹ ɢɪᴛʜᴜʙ ʟɪɴᴋ"),
    BotCommand("font", "˹ ᴄᴏɴᴠᴇʀᴛ ᴛᴏ ꜰᴏɴᴛ"),
    BotCommand("ip", "˹ ʟᴏᴏᴋᴜᴘ ᴀɴ ɪᴩ"),
    BotCommand("groupdata", "˹ sᴇɴᴅ ɢʀᴏᴜᴩ ɪɴғᴏ"),
    BotCommand("genpassword", "˹ ɢᴇɴᴇʀᴀᴛᴇ ᴀ ᴩᴀss"),
    BotCommand("quote", "˹ ɢᴇɴᴇʀᴀᴛᴇ ᴀ ǫᴜᴏᴛᴇ"),
    BotCommand("tiny", "˹ ᴍᴀᴋᴇ ᴀ ᴛɪɴʏ sᴛɪᴄᴋᴇʀ"),
    BotCommand("couple", "˹ ᴄᴏᴜᴘʟᴇ ᴏꜰ ᴛʜᴇ ᴅᴀʏ"),
    BotCommand("repo", "˹ ɢᴇᴛ ᴛʜᴇ sᴏᴜʀᴄᴇ"),
    BotCommand("link", "˹ ɢᴇᴛ ɪɴᴠɪᴛᴇ ʟɪɴᴋ"),
    BotCommand("telegraph", "˹ ᴜᴩʟᴏᴀᴅ ᴛᴏ ᴛᴇʟᴇɢʀᴀᴩʜ"),
    BotCommand("short", "˹ sʜᴏʀᴛᴇɴ ᴀ ᴜʀʟ"),
    BotCommand("utag", "˹ ᴛᴀɢ ᴀʟʟ ᴜsᴇʀs"),
    BotCommand("cancel", "˹ ᴄᴀɴᴄᴇʟ ᴀssɪsᴛᴀɴᴛ"),
    BotCommand("admins", "˹ ʟɪsᴛ ᴀᴅᴍɪɴs"),
    BotCommand("zombies", "˹ ʀᴇᴍᴏᴠᴇ ᴢᴏᴍʙɪᴇs"),
    BotCommand("vcinfo", "˹ ᴠᴏɪᴄᴇ ᴄʜᴀᴛ ɪɴғᴏ"),
    BotCommand("volume", "˹ sᴇᴛ ᴠᴄ ᴠᴏʟᴜᴍᴇ"),
    BotCommand("volup", "˹ ᴠᴏʟᴜᴍᴇ ᴜᴘ"),
    BotCommand("voldown", "˹ ᴠᴏʟᴜᴍᴇ ᴅᴏᴡɴ"),
    BotCommand("ai", "˹ ᴀsᴋ ɪᴏᴛᴀ ᴀɪ"),
]

# ── Owner / sudo-only commands (added to the owner's / menu only) ────────
OWNER_COMMANDS = [
    BotCommand("gban", "ɢʟᴏʙᴀʟ ʙᴀɴ ᴀ ᴜsᴇʀ"),
    BotCommand("ungban", "ʀᴇᴍᴏᴠᴇ ᴀ ɢʟᴏʙᴀʟ ʙᴀɴ"),
    BotCommand("gbannedusers", "ʟɪsᴛ ɢʟᴏʙᴀʟʟʏ ʙᴀɴɴᴇᴅ"),
    BotCommand("sudoers", "ʟɪsᴛ sᴜᴅᴏᴇʀs"),
    BotCommand("addsudo", "ᴀᴅᴅ ᴀ sᴜᴅᴏᴇʀ"),
    BotCommand("delsudo", "ʀᴇᴍᴏᴠᴇ ᴀ sᴜᴅᴏᴇʀ"),
    BotCommand("maintenance", "ᴛᴏɢɢʟᴇ ᴍᴀɪɴᴛᴇɴᴀɴᴄᴇ"),
    BotCommand("logger", "ᴛᴏɢɢʟᴇ ʟᴏɢɢᴇʀ"),
    BotCommand("restart", "ʀᴇsᴛᴀʀᴛ ᴛʜᴇ ʙᴏᴛ"),
    BotCommand("update", "ᴜᴩᴅᴀᴛᴇ ᴛʜᴇ ʙᴏᴛ"),
    BotCommand("getlog", "ɢᴇᴛ ᴛʜᴇ ʟᴏɢ ꜰɪʟᴇ"),
    BotCommand("block", "ɢʟᴏʙᴀʟʟʏ ʙʟᴏᴄᴋ ᴀ ᴜsᴇʀ"),
    BotCommand("unblock", "ᴜɴʙʟᴏᴄᴋ ᴀ ᴜsᴇʀ"),
    BotCommand("blacklistchat", "ʙʟᴀᴄᴋʟɪsᴛ ᴀ ᴄʜᴀᴛ"),
    BotCommand("whitelistchat", "ᴡʜɪᴛᴇʟɪsᴛ ᴀ ᴄʜᴀᴛ"),
    BotCommand("blacklistedchats", "ʟɪsᴛ ʙʟᴀᴄᴋʟɪsᴛᴇᴅ ᴄʜᴀᴛs"),
    BotCommand("autoend", "ᴛᴏɢɢʟᴇ ᴀᴜᴛᴏ-ᴇɴᴅ sᴛʀᴇᴀᴍ"),
    BotCommand("post", "ꜰᴏʀᴡᴀʀᴅ ᴛᴏ ᴛʜᴇ ᴘᴏsᴛ ɢʀᴏᴜᴩ"),
    BotCommand("eval", "ᴇxᴇᴄᴜᴛᴇ ᴩʏᴛʜᴏɴ (ᴏᴡɴᴇʀ)"),
    BotCommand("sh", "ʀᴜɴ ᴀ sʜᴇʟʟ ᴄᴍᴅ (ᴏᴡɴᴇʀ)"),
    BotCommand("botschk", "ᴄʜᴇᴄᴋ ʙᴏᴛs sᴛᴀᴛᴜs"),
    BotCommand("backup", "ʙᴀᴄᴋᴜᴩ ᴛʜᴇ ᴅᴀᴛᴀʙᴀsᴇ"),
    BotCommand("deleteall", "ᴅᴇʟᴇᴛᴇ ᴀʟʟ ᴍsɢs (ᴏᴡɴᴇʀ)"),
    BotCommand("addwelcome", "ᴜᴘʟᴏᴀᴅ ᴡᴇʟᴄᴏᴍᴇ ʙɢ (ᴏᴡɴᴇʀ)"),
    BotCommand("listwelcome", "ʟɪsᴛ ᴡᴇʟᴄᴏᴍᴇ ʙɢs (ᴏᴡɴᴇʀ)"),
    BotCommand("welcomepack", "ᴡᴇʟᴄᴏᴍᴇ ᴘᴀᴄᴋ sᴛᴀᴛs (ᴏᴡɴᴇʀ)"),
    BotCommand("delwelcome", "ᴅᴇʟᴇᴛᴇ ᴡᴇʟᴄᴏᴍᴇ ʙɢ (ᴏᴡɴᴇʀ)"),
    BotCommand("setwelcome", "sᴇᴛ ᴅᴇғᴀᴜʟᴛ ᴡᴇʟᴄᴏᴍᴇ (ᴏᴡɴᴇʀ)"),
    BotCommand("setupic", "sᴇᴛ ᴜᴘɪᴄ.ᴘɴɢ (ᴏᴡɴᴇʀ)"),
    BotCommand("settiny", "sᴇᴛ ᴛɪɴʏ.ᴘɴɢ (ᴏᴡɴᴇʀ)"),
    BotCommand("setcouple", "sᴇᴛ ᴄᴏᴜᴘʟᴇ.ᴘɴɢ (ᴏᴡɴᴇʀ)"),
]


def owner_command_list():
    """DEFAULT + OWNER commands, de-duplicated by command name."""
    seen, out = set(), []
    for c in list(DEFAULT_COMMANDS) + list(OWNER_COMMANDS):
        if c.command not in seen:
            seen.add(c.command)
            out.append(c)
    return out
