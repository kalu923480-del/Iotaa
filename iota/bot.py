"""
╔══════════════════════════════════════════╗
║     IOTA BOT  —  @Its_iotabot             ║
║  MongoDB + Dual AI + Full Features      ║
╚══════════════════════════════════════════╝
"""
import logging, asyncio
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, CallbackQueryHandler, PreCheckoutQueryHandler, TypeHandler,
    ChatJoinRequestHandler,
)
from config import BOT_TOKEN, OWNER_ID, OWNER_USERNAME
from utils.mongo_db import create_indexes, ensure_user
from utils.ai_provider import load_model_config_db

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def _install_smallcaps_output():
    """
    Make EVERY user-facing text output render in Iota-style smallcaps
    unicode, WITHOUT editing the ~50 handler files individually.

    We wrap the outbound text methods of the telegram library classes so
    that `text` / `caption` / `question` arguments are passed through
    `utils.fonts.sc_out` (tag/URL/entity-aware smallcaps) right before they
    leave the bot. Covers replies, sends, edits, and callback answers.

    Safe:
    - HTML tags, URLs and HTML entities are skipped (never corrupted).
    - Length is preserved, so MessageEntity offsets stay valid.
    - Already-styled text passes through unchanged (idempotent).
    """
    from utils.fonts import sc_out
    import functools, telegram

    def _wrap(orig, pos_index):
        @functools.wraps(orig)
        def wrapper(self, *args, **kwargs):
            args = list(args)
            if pos_index is not None and pos_index < len(args) \
               and isinstance(args[pos_index], str):
                args[pos_index] = sc_out(args[pos_index])
            for kw in ("text", "caption", "question"):
                if kw in kwargs and isinstance(kwargs[kw], str):
                    kwargs[kw] = sc_out(kwargs[kw])
            return orig(self, *args, **kwargs)
        return wrapper

    # class -> [(method, positional-text-index or None)]
    # None = text is only ever passed as a keyword (caption/question).
    targets = {
        telegram.Bot: [
            ("send_message", 1), ("edit_message_text", 0),
            ("edit_message_caption", 0), ("send_animation", None),
            ("send_photo", None), ("send_video", None),
            ("send_document", None), ("send_audio", None),
            ("send_voice", None), ("send_poll", 1),
        ],
        telegram.Message: [
            ("reply_text", 0), ("reply_html", 0), ("reply_markdown", 0),
            ("reply_markdown_v2", 0), ("reply_animation", None),
            ("reply_photo", None), ("reply_video", None),
            ("reply_document", None), ("reply_audio", None),
            ("reply_voice", None), ("reply_poll", 0),
        ],
        telegram.CallbackQuery: [
            ("edit_message_text", 0), ("edit_message_caption", 0),
            ("answer", 0),
        ],
    }
    for cls, methods in targets.items():
        for attr, idx in methods:
            orig = getattr(cls, attr, None)
            if orig and not getattr(orig, "_iota_sc_wrapped", False):
                wrapped = _wrap(orig, idx)
                wrapped._iota_sc_wrapped = True
                setattr(cls, attr, wrapped)
    logger.info("🔤 Smallcaps output wrapper installed (all text outputs).")


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # ── Imports ───────────────────────────────────────────────────────
    from handlers.start        import start_cmd, help_cmd, menu_callback
    from handlers.economy      import (
        daily_cmd, bal_cmd, rob_cmd, kill_cmd, revive_cmd, protect_cmd,
        give_cmd, toprich_cmd, topkill_cmd, wallet_cmd, rank_cmd, pfp_cmd,
        gems_cmd, claim_cmd, coupons_cmd, create_coupon_cmd, coupon_cmd,
        del_coupon_cmd, coupon_status_cmd, economy_cmd, eco_callback,
        gbal_cmd, gkill_cmd, grob_cmd, grevive_cmd, gprotect_cmd,
        gcheck_cmd, granks_cmd, auto_delete_handler, daily_remind_callback,
        auto_daily_job, weekly_cmd, monthly_cmd
    )
    from handlers.premium import (
        pay_cmd, fpay_cmd, fgems_cmd, setemoji_cmd, check_cmd,
        pay_callback, precheckout_callback, successful_payment_handler
    )
    from handlers.gems_store import (
        gems2coins_cmd, gemstore_cmd, buygem_cmd,
    )
    from handlers.games        import (
        game_menu_cmd, open_cmd, close_cmd, leaders_cmd,
        card_cmd, bet_cmd, flip_cmd, card_callback,
        bomb_cmd, bomb_callback, dice_cmd,
        wordgame_cmd, wordgame_letter_handler, game_list_callback
    )
    from handlers.bluff_game   import (
        bluff_cmd, enter_cmd, drop_cmd, judge_cmd, myhand_cmd, bluffend_cmd
    )
    from handlers.werewolf_game import (
        werewolf_cmd, werewolf_join_cmd, werewolf_callback,
        prowl_cmd, peek_cmd, heal_cmd, vote_cmd, werewolf_end_cmd
    )
    from handlers.connect import (
        connect_cmd, connect_callback, disconnect_cmd, connect_id_cmd
    )
    from handlers.slots_game import slots_cmd
    from handlers.quote_sticker import quote_sticker_cmd
    from handlers.hack_game    import (
        hack_start_cmd, hack_register_cmd, hack_guess_cmd, hack_end_cmd
    )
    from handlers.ludo         import (
        ludo_cmd, ludo_callback
    )
    from handlers.new_commands import (
        calc_cmd, poll_cmd, marry_cmd, marry_callback,
        divorce_cmd, couple_cmd, streak_cmd, confession_cmd,
        trivia_cmd, trivia_callback, afk_cmd, afk_check_handler,
        diceroll_cmd, setbio_cmd, bio_cmd, global_rank_cmd,
        ping_cmd, coinflip_cmd,
    )
    from handlers.extra_games  import (
        tictactoe_cmd, ttt_callback, rps_cmd, rps_callback,
        hangman_cmd, hangman_handler, quiz_cmd, quiz_callback,
        ship_cmd, compliment_cmd, roast_cmd, horoscope_cmd,
        shayari_cmd, meme_cmd, work_cmd, profile_cmd, shop_cmd,
        story_cmd, whatif_cmd, settitle_cmd, top_cmd, ocr_cmd,
        remindme_cmd, stash_cmd, mystash_cmd
    )
    from handlers.fun          import (
        couples_cmd, crush_cmd, love_cmd, look_cmd, brain_cmd,
        stupid_meter_cmd, murder_cmd, slap_cmd, punch_cmd, bite_cmd,
        kiss_cmd, hug_cmd, truth_cmd, dare_cmd, puzzle_cmd,
        valentine_cmd, valentine_cancel_cmd, valentine_stats_cmd,
        valentine_delete_cmd, valentine_message_handler,
        truth_dare_reply_handler,
        fall_cmd, throw_cmd, kick_cmd, highfive_cmd, poke_cmd,
        tickle_cmd, facepalm_cmd, pie_cmd, trip_cmd, freeze_cmd,
        zap_cmd, dancewith_cmd
    )
    from handlers.items        import items_cmd, item_cmd, gift_cmd
    from handlers.village_war  import (
        collect_cmd, storage_cmd, vault_cmd, mines_cmd,
        build_cmd, build_callback, walls_cmd, defense_cmd,
        train_cmd, troops_cmd, kingdom_cmd, spy_cmd,
        attack_cmd, emperors_cmd, settle_cmd, convert_cmd, guide_cmd,
        village_cmd, market_cmd
    )
    from handlers.utility      import (
        translate_cmd, voice_cmd, id_cmd, detail_cmd, owner_cmd,
        admins_cmd, own_cmd, setgroup_cmd, topgroups_cmd,
        last_seen_cmd, promoter_cmd, ref_stats_cmd
    )
    from handlers.intro        import (
        set_intro_cmd, intro_cmd, del_intro_cmd
    )
    from handlers.group_tools  import (
        settag_cmd, deltag_cmd, mytag_cmd, tag_cmd,
        link_cmd, del_link_cmd, chathistory_cmd,
        welcome_back_handler
    )
    from handlers.admin        import dot_admin_handler
    from handlers.welcome      import (
        new_member_handler, left_member_handler, setwelcome_cmd,
        welcome_panel_callback
    )
    from handlers.protection   import (
        protection_handler, anti_raid_handler, anti_bot_handler,
        report_cmd, reports_cmd, prot_cmd, report_callback,
        addword_cmd, removeword_cmd, badwords_cmd
    )
    from handlers.advanced_admin import (
        lock_cmd, unlock_cmd, locks_cmd, lock_enforcement_handler,
        setflood_cmd, floodmode_cmd, flood_check_handler,
        rules_cmd, setrules_cmd, clearrules_cmd,
        setwarnlimit_cmd, setwarnmode_cmd, warnlimit_cmd,
        resetallwarns_cmd, warnings_cmd,
        save_cmd, get_note_handler, notes_cmd, clear_note_cmd,
        setlogchannel_cmd, nolog_cmd,
        cleanservice_cmd, clean_service_handler,
        antichannelpin_cmd, channel_pin_handler,
        disable_cmd, enable_cmd, disabled_cmd,
        silentactions_cmd, admincache_cmd,
        setgoodbye_cmd, captcha_cmd,
        captcha_new_member_handler, captcha_callback,
        announce_cmd, setlang_cmd,
        approve_cmd, unapprove_cmd, approved_cmd
    )
    from handlers.ai_chat      import (
        ai_cmd, dm_message_handler, group_mention_handler,
        clear_my_memory_cmd
    )
    from handlers.owner_panel  import (
        owner_panel_cmd, addcoins_cmd, removecoins_cmd, addgems_cmd,
        addpremium_cmd, removepremium_cmd, broadcast_cmd,
        addcoupon_cmd, ban_user_cmd, unban_user_cmd_owner, stats_cmd,
        stars_stats_cmd, setmodel_cmd, listmodels_cmd,
        scan_cmd, resetuser_cmd, giveall_cmd, maintenance_cmd, dm_cmd,
        premiumlist_cmd, userslist_cmd,
        addsticker_cmd, addstickerpack_cmd, addpack_cmd, stickerpacks_cmd, previewsticker_cmd, clearstickers_cmd,
        ttssettings_cmd, previewtts_cmd, delbroadcast_cmd, broadcasthistory_cmd,
        announce_cmd as owner_announce_cmd,
        globalclose_cmd, globalopen_cmd, premiumgiveaway_cmd,
        refreshmodels_cmd, setmaxtokens_cmd, addapikey_cmd,
        removeapikey_cmd, keypoolstatus_cmd, providerstatus_cmd,
        setpriority_cmd, toggleprovider_cmd
    )
    from handlers.sticker_reply import (
        sticker_reply_handler, gif_reply_handler,
        photo_reaction_handler, emoji_only_handler
    )
    from handlers.join_requests import (
        joinrequests_cmd, acceptjoin_cmd, rejectjoin_cmd,
        acceptall_cmd, rejectall_cmd, join_request_callback,
        chat_join_request_handler,
    )
    from handlers.new_suite import (
        pick_cmd, rand_cmd, uptime_cmd,
        gstats_cmd, adminlist_cmd, chatid_cmd,
        leave_cmd, setbotname_cmd,
    )
    from handlers.fun_text import (
        clap_cmd, uwu_cmd, vapor_cmd, bubble_cmd, regional_cmd,
        leet_cmd, zalgo_cmd, hot_cmd, rate_cmd, nhie_cmd,
    )
    from handlers.legal        import terms_cmd, refund_cmd, rules_legal_cmd
    from handlers.new_features_v2 import (
        pin_cmd, unpin_cmd, purge_cmd, avatar_cmd,
        eightball_cmd, joke_cmd, fact_cmd, riddle_cmd, riddle_reveal_callback, wyr_cmd,
        reverse_cmd, mock_cmd, binary_cmd, morse_cmd, hash_cmd, password_cmd,
        nickname_cmd, birthday_cmd, birthday_daily_loop,
        todo_cmd, countdown_cmd, giveaway_cmd, giveaway_join_callback,
        bank_cmd, deposit_cmd, withdraw_cmd, loan_cmd, repay_cmd,
        networth_cmd, lottery_cmd,
        donate_cmd, repair_cmd, raidlog_cmd, recruit_cmd,
        aijoke_cmd, advice_cmd, roastme_cmd, aistory_cmd,
    )

    # ── /start /help ──────────────────────────────────────────────────
    app.add_handler(CommandHandler("start",  start_cmd))
    app.add_handler(CommandHandler("help",   help_cmd))

    # ── Join Request Manager (admin) ──────────────────────────────────
    for c, f in [
        ("joinrequests", joinrequests_cmd),
        ("acceptjoin",   acceptjoin_cmd),
        ("rejectjoin",   rejectjoin_cmd),
        ("acceptall",    acceptall_cmd),
        ("rejectall",    rejectall_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))

    # ── Extra Fun Commands (text toys + social ratings) ──────────────
    for c, f in [
        ("clap",     clap_cmd),
        ("uwu",      uwu_cmd),
        ("vapor",    vapor_cmd),
        ("bubble",   bubble_cmd),
        ("regional", regional_cmd),
        ("leet",     leet_cmd),
        ("zalgo",    zalgo_cmd),
        ("hot",      hot_cmd),
        ("rate",     rate_cmd),
        ("nhie",     nhie_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))

    # ── Extra Features Suite (user / admin / owner) ──────────────────
    for c, f in [
        ("pick",     pick_cmd),
        ("rand",     rand_cmd),
        ("uptime",   uptime_cmd),
        ("gstats",   gstats_cmd),
        ("adminlist", adminlist_cmd),
        ("chatid",   chatid_cmd),
        ("leave",    leave_cmd),
        ("setbotname", setbotname_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))

    # ── Legal / Terms ─────────────────────────────────────────────────
    for c, f in [
        ("terms", terms_cmd), ("refund", refund_cmd), ("policy", rules_legal_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))

    # ── 🆕 20 new features (v8 addition) ────────────────────────────────
    # Admin: message management
    for c, f in [("pin", pin_cmd), ("unpin", unpin_cmd), ("purge", purge_cmd)]:
        app.add_handler(CommandHandler(c, f))

    # Profile
    app.add_handler(CommandHandler("avatar", avatar_cmd))

    # Fun / trivia
    for c, f in [
        ("8ball", eightball_cmd), ("joke", joke_cmd), ("fact", fact_cmd),
        ("riddle", riddle_cmd), ("wyr", wyr_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))

    # Text toys
    for c, f in [
        ("reverse", reverse_cmd), ("mock", mock_cmd), ("binary", binary_cmd),
        ("morse", morse_cmd), ("hash", hash_cmd), ("password", password_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))

    # Social / utility
    for c, f in [
        ("nickname", nickname_cmd), ("birthday", birthday_cmd),
        ("giveaway", giveaway_cmd), ("todo", todo_cmd), ("countdown", countdown_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))

    # 🆕 Economy extras: bank / loan / lottery (@economy_gate already
    # applied at the function definitions in handlers/new_features_v2.py)
    for c, f in [
        ("bank", bank_cmd), ("deposit", deposit_cmd), ("withdraw", withdraw_cmd),
        ("loan", loan_cmd), ("repay", repay_cmd), ("networth", networth_cmd),
        ("lottery", lottery_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))

    # 🆕 Village extras: donate / repair / raidlog / recruit (@village_gate
    # already applied at the function definitions)
    for c, f in [
        ("donate", donate_cmd), ("repair", repair_cmd),
        ("raidlog", raidlog_cmd), ("recruit", recruit_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))

    # 🆕 AI features: fresh AI-generated content each time (not static lists)
    for c, f in [
        ("aijoke", aijoke_cmd), ("advice", advice_cmd),
        ("roastme", roastme_cmd), ("aistory", aistory_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))

    # ── Economy ───────────────────────────────────────────────────────
    # (open/close enforcement now lives on the functions themselves via
    # @economy_gate in handlers/economy.py — see utils/system_gate.py)
    for c, f in [
        ("daily",daily_cmd),("bal",bal_cmd),("rob",rob_cmd),
        ("kill",kill_cmd),("revive",revive_cmd),("protect",protect_cmd),
        ("give",give_cmd),("toprich",toprich_cmd),("topkill",topkill_cmd),
        ("wallet",wallet_cmd),("rank",rank_cmd),("pfp",pfp_cmd),
        ("gems",gems_cmd),("claim",claim_cmd),("coupons",coupons_cmd),
        ("coupon",coupon_cmd),("create_coupon",create_coupon_cmd),
        ("del_coupon",del_coupon_cmd),("status",coupon_status_cmd),
        ("economy",economy_cmd),
        ("gbal",gbal_cmd),("gkill",gkill_cmd),("grob",grob_cmd),
        ("grevive",grevive_cmd),("gprotect",gprotect_cmd),
        ("gcheck",gcheck_cmd),("granks",granks_cmd),
        ("weekly",weekly_cmd),("monthly",monthly_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))

    # ── Premium ───────────────────────────────────────────────────────
    for c, f in [
        ("pay",pay_cmd),("fpay",fpay_cmd),("fgems",fgems_cmd),
        ("setemoji",setemoji_cmd),("check",check_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))

    # ── Gems Economy (convert + gems-only store) ────────────────────
    for c, f in [
        ("gems2coins", gems2coins_cmd),
        ("gemstore",   gemstore_cmd),
        ("buygem",     buygem_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))

    # ── Games ─────────────────────────────────────────────────────────
    # (open/close enforcement now lives on the functions themselves via
    # @games_gate in handlers/games.py — see utils/system_gate.py)
    for c, f in [
        ("game",game_menu_cmd),("open",open_cmd),("close",close_cmd),
        ("card",card_cmd),("bet",bet_cmd),("flip",flip_cmd),
        ("bomb",bomb_cmd),
        ("ludo",ludo_cmd),("wordgame",wordgame_cmd),("leaders",leaders_cmd),
        ("tictactoe",tictactoe_cmd),("rps",rps_cmd),
        ("hangman",hangman_cmd),("quiz",quiz_cmd),
        ("dice",dice_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))

    # ── 🃏 Bluff Card Game (multiplayer) ────────────────────────────────
    for c, f in [
        ("bluff",    bluff_cmd),
        ("enter",    enter_cmd),
        ("drop",     drop_cmd),
        ("judge",    judge_cmd),
        ("myhand",   myhand_cmd),
        ("bluffend", bluffend_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))

    # ── 🐺 Werewolf (social deduction, multiplayer) ─────────────────────
    for c, f in [
        ("werewolf", werewolf_cmd),
        ("join",     werewolf_join_cmd),
        ("prowl",    prowl_cmd),
        ("peek",     peek_cmd),
        ("heal",     heal_cmd),
        ("vote",     vote_cmd),
        ("wwend",    werewolf_end_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))

    # ── 🔗 Connect (shared AI memory between two users) ─────────────────
    for c, f in [
        ("connect",    connect_cmd),
        ("disconnect", disconnect_cmd),
        ("connect_id", connect_id_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))

    app.add_handler(CommandHandler("slots", slots_cmd))
    app.add_handler(CommandHandler("q", quote_sticker_cmd))

    for c, f in [
        ("hack",     hack_start_cmd),
        ("register", hack_register_cmd),
        ("guess",    hack_guess_cmd),
        ("end",      hack_end_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))

    # ── Fun / Social ──────────────────────────────────────────────────
    for c, f in [
        ("ship",ship_cmd),("compliment",compliment_cmd),("roast",roast_cmd),
        ("horoscope",horoscope_cmd),("shayari",shayari_cmd),("meme",meme_cmd),
        ("work",work_cmd),("profile",profile_cmd),("shop",shop_cmd),
        ("story",story_cmd),("whatif",whatif_cmd),("settitle",settitle_cmd),
        ("top",top_cmd),("ocr",ocr_cmd),("remindme",remindme_cmd),
        ("stash",stash_cmd),("mystash",mystash_cmd),
        ("couples",couples_cmd),("crush",crush_cmd),("love",love_cmd),
        ("look",look_cmd),("brain",brain_cmd),("stupid_meter",stupid_meter_cmd),
        ("murder",murder_cmd),("slap",slap_cmd),("punch",punch_cmd),
        ("bite",bite_cmd),("kiss",kiss_cmd),("hug",hug_cmd),
        ("truth",truth_cmd),("dare",dare_cmd),("puzzle",puzzle_cmd),
        ("valentine",valentine_cmd),("valentine_cancel",valentine_cancel_cmd),
        ("valentine_stats",valentine_stats_cmd),
        ("valentine_delete",valentine_delete_cmd),
        ("fall",fall_cmd),("throw",throw_cmd),("kick",kick_cmd),
        ("highfive",highfive_cmd),("poke",poke_cmd),("tickle",tickle_cmd),
        ("facepalm",facepalm_cmd),("pie",pie_cmd),("trip",trip_cmd),
        ("freeze",freeze_cmd),("zap",zap_cmd),("dancewith",dancewith_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))

    # ── Items ─────────────────────────────────────────────────────────
    for c, f in [("items",items_cmd),("item",item_cmd),("gift",gift_cmd)]:
        app.add_handler(CommandHandler(c, f))

    # ── Village / War ─────────────────────────────────────────────────
    # (open/close enforcement already lives on every one of these via
    # @village_gate in handlers/village_war.py — see utils/system_gate.py)
    for c, f in [
        ("collect",collect_cmd),("storage",storage_cmd),("vault",vault_cmd),
        ("mines",mines_cmd),("build",build_cmd),("walls",walls_cmd),
        ("defense",defense_cmd),("train",train_cmd),("troops",troops_cmd),
        ("kingdom",kingdom_cmd),("spy",spy_cmd),("attack",attack_cmd),
        ("emperors",emperors_cmd),("settle",settle_cmd),
        ("convert",convert_cmd),("guide",guide_cmd),("village",village_cmd),
        ("market",market_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))

    # ── Utility ───────────────────────────────────────────────────────
    for c, f in [
        ("tr",translate_cmd),("voice",voice_cmd),("id",id_cmd),
        ("detail",detail_cmd),("owner",owner_cmd),("admins",admins_cmd),
        ("own",own_cmd),("setgroup",setgroup_cmd),("topgroups",topgroups_cmd),
        ("last_seen",last_seen_cmd),("promoter",promoter_cmd),
        ("ref_stats",ref_stats_cmd),
        ("ai",ai_cmd),("ask",ai_cmd),("clearmemory",clear_my_memory_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))

    # ── Intro ─────────────────────────────────────────────────────────
    for c, f in [
        ("set_intro",set_intro_cmd),("intro",intro_cmd),
        ("del_intro",del_intro_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))

    # ── Group Tools ───────────────────────────────────────────────────
    for c, f in [
        ("settag",settag_cmd),("deltag",deltag_cmd),
        ("mytag",mytag_cmd),("tag",tag_cmd),
        ("link",link_cmd),("del_link",del_link_cmd),
        ("chathistory",chathistory_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))

    # ── Welcome & Protection ──────────────────────────────────────────
    for c, f in [
        ("setwelcome",setwelcome_cmd),("prot",prot_cmd),
        ("report",report_cmd),("reports",reports_cmd),
        ("addword",addword_cmd),("removeword",removeword_cmd),
        ("badwords",badwords_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))

    # ── Advanced Admin ────────────────────────────────────────────────
    for c, f in [
        ("lock",lock_cmd),("unlock",unlock_cmd),("locks",locks_cmd),
        ("setflood",setflood_cmd),("floodmode",floodmode_cmd),
        ("rules",rules_cmd),("setrules",setrules_cmd),
        ("clearrules",clearrules_cmd),
        ("setwarnlimit",setwarnlimit_cmd),("setwarnmode",setwarnmode_cmd),
        ("warnlimit",warnlimit_cmd),("resetallwarns",resetallwarns_cmd),
        ("warnings",warnings_cmd),
        ("save",save_cmd),("notes",notes_cmd),("clear",clear_note_cmd),
        ("logchannel",setlogchannel_cmd),("nolog",nolog_cmd),
        ("cleanservice",cleanservice_cmd),
        ("antichannelpin",antichannelpin_cmd),
        ("disable",disable_cmd),("enable",enable_cmd),
        ("disabled",disabled_cmd),("silentactions",silentactions_cmd),
        ("admincache",admincache_cmd),("setgoodbye",setgoodbye_cmd),
        ("captcha",captcha_cmd),("setlang",setlang_cmd),
        ("approve",approve_cmd),("unapprove",unapprove_cmd),
        ("approved",approved_cmd),
        ("announce",owner_announce_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))

    # ── Owner Panel ───────────────────────────────────────────────────
    for c, f in [
        ("panel",owner_panel_cmd),("addcoins",addcoins_cmd),
        ("removecoins",removecoins_cmd),("addgems",addgems_cmd),
        ("addpremium",addpremium_cmd),("removepremium",removepremium_cmd),
        ("broadcast",broadcast_cmd),("addcoupon",addcoupon_cmd),
        ("banuser",ban_user_cmd),("unbanuser",unban_user_cmd_owner),
        ("botstats",stats_cmd),("starsstats",stars_stats_cmd),
        ("setmodel",setmodel_cmd),("listmodels",listmodels_cmd),
        ("scan",scan_cmd),("resetuser",resetuser_cmd),
        ("giveall",giveall_cmd),("maintenance",maintenance_cmd),
        ("dm",dm_cmd),("premiumlist",premiumlist_cmd),("userslist",userslist_cmd),
        ("addsticker",addsticker_cmd),("addstickerpack",addstickerpack_cmd),("addpack",addpack_cmd),("stickerpacks",stickerpacks_cmd),
        ("previewsticker",previewsticker_cmd),("clearstickers",clearstickers_cmd),
        ("ttssettings",ttssettings_cmd),("previewtts",previewtts_cmd),
        ("delbroadcast",delbroadcast_cmd),("broadcasthistory",broadcasthistory_cmd),
        ("globalclose",globalclose_cmd),("globalopen",globalopen_cmd),
        ("premiumgiveaway",premiumgiveaway_cmd),
        ("refreshmodels",refreshmodels_cmd),("setmaxtokens",setmaxtokens_cmd),
        ("addapikey",addapikey_cmd),("removeapikey",removeapikey_cmd),
        ("keypoolstatus",keypoolstatus_cmd),
        ("providerstatus",providerstatus_cmd),("setpriority",setpriority_cmd),
        ("toggleprovider",toggleprovider_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))

    # ── Dot/Bang Admin prefix ─────────────────────────────────────────
    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(
            r"^[.!](warn|unwarn|warns|mute|imute|dmute|unmute|ban|dban|"
            r"unban|kick|promote|demote|unpromote|demote_all|add|remove|title|"
            r"pin|unpin|d|help|adminlist|report|clearwarn|warnlimit|"
            r"tmute|tban|note|notes|delnote|clearnotes|purge)\b"
        ),
        dot_admin_handler
    ))

    # ── Inline Callbacks ──────────────────────────────────────────────
    # ── New Commands ──────────────────────────────────────────────────
    for c, f in [
        ("calc",        calc_cmd),
        ("poll",        poll_cmd),
        ("marry",       marry_cmd),
        ("divorce",     divorce_cmd),
        ("couple",      couple_cmd),
        ("streak",      streak_cmd),
        ("confession",  confession_cmd),
        ("trivia",      trivia_cmd),
        ("afk",         afk_cmd),
        ("roll",        diceroll_cmd),
        ("setbio",      setbio_cmd),
        ("bio",         bio_cmd),
        ("global_rank", global_rank_cmd),
        ("ping",        ping_cmd),
        ("coinflip",    coinflip_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))

    app.add_handler(CallbackQueryHandler(menu_callback,         pattern=r"^menu_"))
    app.add_handler(CallbackQueryHandler(eco_callback,          pattern=r"^eco_"))
    app.add_handler(CallbackQueryHandler(pay_callback,          pattern=r"^buy_premium_"))
    app.add_handler(CallbackQueryHandler(daily_remind_callback, pattern=r"^remind_daily_"))
    app.add_handler(CallbackQueryHandler(card_callback,         pattern=r"^card_"))
    app.add_handler(CallbackQueryHandler(bomb_callback,         pattern=r"^bomb_"))
    app.add_handler(CallbackQueryHandler(game_list_callback,    pattern=r"^game_"))
    app.add_handler(CallbackQueryHandler(report_callback,       pattern=r"^rep_"))
    app.add_handler(CallbackQueryHandler(ttt_callback,          pattern=r"^ttt_"))
    app.add_handler(CallbackQueryHandler(rps_callback,          pattern=r"^rps_"))
    app.add_handler(CallbackQueryHandler(quiz_callback,         pattern=r"^quiz_"))
    app.add_handler(CallbackQueryHandler(build_callback,        pattern=r"^build_"))
    app.add_handler(CallbackQueryHandler(captcha_callback,      pattern=r"^captcha_"))
    app.add_handler(CallbackQueryHandler(ludo_callback,         pattern=r"^ludo_"))
    app.add_handler(CallbackQueryHandler(marry_callback,        pattern=r"^marry_"))
    app.add_handler(CallbackQueryHandler(trivia_callback,       pattern=r"^trivia_"))
    app.add_handler(CallbackQueryHandler(welcome_panel_callback, pattern=r"^wset_"))
    app.add_handler(CallbackQueryHandler(werewolf_callback,      pattern=r"^ww_"))
    app.add_handler(CallbackQueryHandler(connect_callback,       pattern=r"^conn_"))
    app.add_handler(CallbackQueryHandler(riddle_reveal_callback, pattern=r"^riddle_ans:"))
    app.add_handler(CallbackQueryHandler(giveaway_join_callback, pattern=r"^ga_join:"))
    app.add_handler(CallbackQueryHandler(join_request_callback, pattern=r"^jr_"))

    # ── Payments ──────────────────────────────────────────────────────
    app.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    app.add_handler(MessageHandler(
        filters.SUCCESSFUL_PAYMENT, successful_payment_handler
    ))

    # ── Member events ──────────────────────────────────────────────────
    app.add_handler(MessageHandler(
        filters.StatusUpdate.NEW_CHAT_MEMBERS, new_member_handler
    ))
    app.add_handler(MessageHandler(
        filters.StatusUpdate.NEW_CHAT_MEMBERS, anti_bot_handler
    ))
    app.add_handler(MessageHandler(
        filters.StatusUpdate.NEW_CHAT_MEMBERS, anti_raid_handler
    ))
    app.add_handler(MessageHandler(
        filters.StatusUpdate.NEW_CHAT_MEMBERS, captcha_new_member_handler
    ))
    app.add_handler(MessageHandler(
        filters.StatusUpdate.LEFT_CHAT_MEMBER, left_member_handler
    ))
    app.add_handler(MessageHandler(
        filters.StatusUpdate.LEFT_CHAT_MEMBER, clean_service_handler
    ))

    # ── Chat join requests (captured into MongoDB for admin management) ──
    app.add_handler(ChatJoinRequestHandler(chat_join_request_handler))
    app.add_handler(MessageHandler(
        filters.StatusUpdate.PINNED_MESSAGE, channel_pin_handler
    ))

    # ── Global identity tracker (runs first, on EVERY update) ─────────
    # 🔴 ROOT-CAUSE FIX for /detail showing incomplete history:
    # Previously, name/username changes were only captured when a
    # SPECIFIC command happened to call ensure_user() (e.g. /start,
    # /bal). If a user changed their Telegram name multiple times
    # between running commands, every intermediate name was silently
    # lost — only whatever name was live at the NEXT command call ever
    # got compared, so at most one change could ever be detected no
    # matter how many times they'd actually changed it.
    #
    # This middleware runs on every single update the bot receives
    # (any message, button press, etc.) — not just commands — so a
    # name/username change is captured the moment it's next seen,
    # regardless of which command (if any) the user is running. This
    # matches the "Baka" bot's evident behaviour of always showing a
    # full change history.
    #
    # PERFORMANCE: ensure_user() does a real MongoDB round-trip, so
    # calling it on literally every message in a busy group would be
    # wasteful. We debounce per-user with a short in-memory cache —
    # each user is only re-checked once every 5 minutes at most, which
    # is more than fast enough to catch name changes in practice while
    # keeping database load negligible.
    _identity_last_checked: dict = {}
    _IDENTITY_RECHECK_SECONDS = 300

    async def _track_identity(update, context):
        try:
            u = update.effective_user
            if not u or u.is_bot:
                return
            import time as _time
            last = _identity_last_checked.get(u.id, 0)
            now_ts = _time.time()
            if now_ts - last < _IDENTITY_RECHECK_SECONDS:
                return
            _identity_last_checked[u.id] = now_ts
            await ensure_user(u.id, u.username or "", u.full_name)
            # If they're messaging the bot in DM right now, they can
            # obviously receive DMs again — clear any stale "unreachable"
            # flag from a past broadcast so future broadcasts include them.
            if update.effective_chat and update.effective_chat.type == "private":
                from utils.mongo_db import mark_user_reachable
                await mark_user_reachable(u.id)
        except Exception:
            logger.debug("identity tracker: ensure_user failed", exc_info=True)

    app.add_handler(TypeHandler(Update, _track_identity), group=-2)

    # ── Command execution logger (runs first, never blocks) ───────────
    # Logs every incoming command so issues like "/panel does nothing"
    # are immediately visible in the bot's logs: did the update even
    # arrive? Which user/chat? This makes silent failures traceable.
    async def _log_command(update, context):
        try:
            msg = update.effective_message
            u   = update.effective_user
            if msg and msg.text and msg.text.startswith("/"):
                cmd = msg.text.split()[0]
                logger.info(
                    f"📥 CMD {cmd} | user={u.id if u else '?'} "
                    f"({u.username if u else '?'}) | chat={update.effective_chat.id}"
                )
        except Exception:
            logger.exception("Error in command logger middleware")

    app.add_handler(MessageHandler(filters.COMMAND, _log_command), group=-1)

    # ── Emoji reactions ─────────────────────────────────────────────────
    # Iota can react to messages with a native Telegram emoji reaction
    # (the little tap-to-react bubble), in both DMs and groups — not
    # every message, only when the content clearly calls for it (see
    # utils/reactions.py for the full heuristic and emoji list). Runs as
    # a fire-and-forget background task so a slow/failed reaction call
    # can NEVER delay or block Iota's actual reply to the message.
    from utils.reactions import maybe_react

    async def _react_to_message(update, context):
        msg = update.effective_message
        u = update.effective_user
        if not msg or not u or u.is_bot:
            return
        asyncio.create_task(maybe_react(context.bot, msg))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _react_to_message), group=-1)

    # ── Group message handlers (priority order) ───────────────────────

    # 1. Lock enforcement
    app.add_handler(MessageHandler(
        ~filters.COMMAND & filters.ChatType.GROUPS,
        lock_enforcement_handler
    ), group=1)

    # 2. Flood check
    app.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.GROUPS & ~filters.COMMAND,
        flood_check_handler
    ), group=2)

    # 3. Protection (spam/link)
    app.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.GROUPS & ~filters.COMMAND,
        protection_handler
    ), group=3)

    # 4. Welcome back after dead
    app.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.GROUPS & ~filters.COMMAND,
        welcome_back_handler
    ), group=4)

    # 5b. AI Truth/Dare reply handler (reacts when user replies to a T/D prompt)
    app.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.GROUPS & ~filters.COMMAND,
        truth_dare_reply_handler
    ), group=5)

    # 6. @mention / tag / direct-address AI in groups
    app.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.GROUPS & ~filters.COMMAND,
        group_mention_handler
    ), group=6)

    # 6b. AFK check handler
    app.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.GROUPS & ~filters.COMMAND,
        afk_check_handler
    ), group=7)

    # 7. Note getter (#notename)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        get_note_handler
    ), group=8)

    # 8. Hangman letter
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        hangman_handler
    ), group=9)

    # 9. Word game letter
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        wordgame_letter_handler
    ), group=10)

    # 10. Valentine form (DM)
    app.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND,
        valentine_message_handler
    ), group=11)

    # 11. DM AI auto-reply (lowest priority)
    app.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND,
        dm_message_handler
    ), group=12)

    # ── 📦 Media handlers (sticker/GIF/photo/emoji) ────────────────────
    # Sticker reply — works in DMs always; in groups only when bot is addressed
    app.add_handler(MessageHandler(
        filters.Sticker.ALL,
        sticker_reply_handler
    ), group=14)

    # GIF/animation reply — same rules as sticker
    app.add_handler(MessageHandler(
        filters.ANIMATION,
        gif_reply_handler
    ), group=15)

    # Photo reaction — triggered in DMs or when bot is @tagged/replied-to
    app.add_handler(MessageHandler(
        filters.PHOTO,
        photo_reaction_handler
    ), group=16)

    # Emoji-only messages in DMs
    app.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND,
        emoji_only_handler
    ), group=17)

    # ── Global error handler — prevents silent command failures ───────
    from utils.error_handler import global_error_handler
    app.add_error_handler(global_error_handler)

    # ── Post-init ─────────────────────────────────────────────────────
    async def post_init(application):
        from utils.mongo_db import check_connection
        db_ok = await check_connection()
        if db_ok:
            logger.info("✅ MongoDB connected successfully!")
        else:
            logger.error(
                "❌ MongoDB connection FAILED at startup! "
                "Set a real password in config.py (_MONGO_PASS). "
                "/bal, /daily, /rob, /ludo etc. will NOT work until fixed."
            )
            try:
                await application.bot.send_message(
                    OWNER_ID,
                    "🔌 <b>⚠️ MongoDB Connection Failed!</b>\n\n"
                    "Iota bot started but could NOT connect to your database.\n"
                    "Almost every command (/bal, /daily, /rob, /ludo, etc.) "
                    "will fail until you fix this.\n\n"
                    "👉 Open <code>config.py</code> and set <code>_MONGO_PASS</code> "
                    "to your real MongoDB Atlas password.",
                    parse_mode="HTML"
                )
            except Exception:
                pass
        await create_indexes()
        await load_model_config_db()
        try:
            from utils.sarvam import load_tts_config_db
            await load_tts_config_db()
        except Exception as e:
            logger.warning(f"Failed to load TTS config from DB (using defaults): {e}")
        # Background jobs
        # 🔴 FIX: `protection_alert_job` was called here but never defined
        # or imported anywhere in the codebase — this raised a NameError
        # inside post_init (NOT wrapped in try/except), crashing the
        # entire bot on every single startup, silently, before any
        # handler or command could ever run. Removed the dead call; group
        # protection (anti-spam/anti-raid/anti-bot) already runs inline
        # via its own message handlers in handlers/protection.py and does
        # not need a separate background job.
        asyncio.create_task(auto_daily_job(application.bot))
        asyncio.create_task(birthday_daily_loop(application.bot))
        asyncio.create_task(_memory_cleanup_job())
        asyncio.create_task(_premium_expiry_job(application.bot))
        asyncio.create_task(_connect_expiry_job(application.bot))

        # 🎲 Ludo Mini App web server — runs in-process, no separate
        # hosting needed. Only warns (doesn't crash the bot) if it fails
        # to bind, since the rest of the bot works fine without it —
        # /ludo just falls back to classic chat-button mode.
        try:
            from webapp.ludo_server import run_webapp_server
            from config import WEBAPP_PORT
            asyncio.create_task(run_webapp_server(port=WEBAPP_PORT))
        except Exception as e:
            logger.warning(f"⚠️ Ludo Mini App server failed to start: {e}. "
                            f"/ludo will still work in classic chat mode.")

        logger.info(f"🤖 Iota Bot LIVE! Owner: {OWNER_USERNAME} (ID: {OWNER_ID})")

    app.post_init = post_init

    # ── Handler registration summary ────────────────────────────────────
    # Confirms exactly how many handlers of each type made it onto the
    # Application, and explicitly verifies /panel is among them. If this
    # log doesn't show "/panel" registered, something broke its import or
    # registration BEFORE this point ran — check the traceback above.
    try:
        total_handlers = sum(len(v) for v in app.handlers.values())
        cmd_names = sorted({
            cmd
            for group_handlers in app.handlers.values()
            for h in group_handlers
            if isinstance(h, CommandHandler)
            for cmd in h.commands
        })
        logger.info(f"📋 Registered {total_handlers} total handlers across {len(app.handlers)} groups")
        logger.info(f"📋 Registered {len(cmd_names)} unique commands")
        if "panel" in cmd_names:
            logger.info("✅ /panel command IS registered and ready.")
        else:
            logger.error("❌ /panel command is MISSING from registered handlers! Check imports in main().")
    except Exception:
        logger.exception("Error while summarizing handler registration")

    app.run_polling(drop_pending_updates=True)


# ── Background jobs ────────────────────────────────────────────────────────────

async def _memory_cleanup_job():
    """Delete AI memories older than 30 days — runs daily."""
    while True:
        try:
            await asyncio.sleep(86400)  # once a day
            from utils.ai_memory import cleanup_old_memories
            deleted = await cleanup_old_memories()
            logger.info(f"🗑️ Memory cleanup: {deleted} old memories deleted")
        except Exception:
            pass


async def _premium_expiry_job(bot):
    """Check premium expirations every hour — notify and downgrade."""
    import time
    while True:
        try:
            await asyncio.sleep(3600)
            now = int(time.time())
            from utils.mongo_db import get_db
            db = get_db()
            # Find users whose premium expired
            expired = await db.users.find(
                {"is_premium": True,
                 "premium_until": {"$gt": 0, "$lt": now}}
            ).to_list(10000)
            for u in expired:
                await db.users.update_one(
                    {"_id": u["_id"]},
                    {"$set": {"is_premium": False}}
                )
                try:
                    await bot.send_message(
                        u["_id"],
                        "💓 Your Iota Premium has expired!\n"
                        "Renew with /pay or /fpay to keep all benefits 🌟",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass
            # Warn users expiring in 24h
            warn_before = now + 86400
            expiring = await db.users.find(
                {"is_premium": True,
                 "premium_until": {"$gt": now, "$lt": warn_before}}
            ).to_list(10000)
            for u in expiring:
                try:
                    rem = u["premium_until"] - now
                    await bot.send_message(
                        u["_id"],
                        f"⚠️ Your Premium expires in "
                        f"<b>{rem//3600}h {(rem%3600)//60}m</b>!\n"
                        f"Renew: /pay or /fpay",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass
        except Exception:
            pass


async def _connect_expiry_job(bot):
    """
    Every 5 minutes, close out any /connect pairs whose sync duration
    has elapsed and DM both users that it ended — see
    utils/connect.py:expire_due_connections for the actual logic.
    Checked more frequently than premium (every 5 min vs hourly) since
    connections are a much shorter-lived, more interactive feature —
    users should find out promptly, not up to an hour late.
    """
    while True:
        try:
            await asyncio.sleep(300)
            from utils.connect import expire_due_connections
            await expire_due_connections(bot)
        except Exception:
            logger.exception("_connect_expiry_job: unexpected error in loop")


if __name__ == "__main__":
    main()
