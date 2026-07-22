"""
╔══════════════════════════════════════════╗
║     IOTA BOT  —  @Its_iotabot             ║
║  MongoDB + Dual AI + Full Features      ║
╚══════════════════════════════════════════╝
"""
import logging, asyncio, os, time
from telegram import Update
from telegram.ext import ContextTypes
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, CallbackQueryHandler, PreCheckoutQueryHandler, TypeHandler,
    ChatJoinRequestHandler, ApplicationHandlerStop, ChatMemberHandler,
)
from pymongo import ReturnDocument
import aiohttp
from config import BOT_TOKEN, OWNER_ID, OWNER_USERNAME, WEBAPP_BASE_URL
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
    from utils.fonts import sc, sc_all
    import functools, telegram

    def _wrap(orig, pos_index):
        @functools.wraps(orig)
        def wrapper(self, *args, **kwargs):
            args = list(args)
            # Only HTML-mode sends need entity-safe escaping. Plain text and
            # Markdown must keep their literal '<' / '&' (escaping them would
            # render as "&amp;" / "&lt;" to the user). `sc_all` does both the
            # smallcaps styling AND the Telegram HTML escaping (stray '<',
            # unescaped '&', unsupported tags); `sc` does styling only.
            # `reply_html` sets parse_mode="HTML" internally, so detect it by
            # method name too — the kwarg alone isn't visible at wrap time.
            is_html = (kwargs.get("parse_mode") == "HTML") or (orig.__name__ == "reply_html")
            def _proc(t: str) -> str:
                return sc_all(t) if is_html else sc(t)
            if pos_index is not None and pos_index < len(args) \
               and isinstance(args[pos_index], str):
                args[pos_index] = _proc(args[pos_index])
            for kw in ("text", "caption", "question"):
                if kw in kwargs and isinstance(kwargs[kw], str):
                    kwargs[kw] = _proc(kwargs[kw])
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
    # 🔴 Validate config BEFORE building the app so a missing MONGO_URI /
    # BOT_TOKEN surfaces as one clear message instead of a cryptic crash
    # later (e.g. inside /bal). Fail fast, fail loud.
    try:
        from utils.config_check import validate_config
        validate_config()
    except RuntimeError as e:
        logger.error(str(e))
        raise

    app = Application.builder().token(BOT_TOKEN).build()

    # Install the Iota smallcaps output wrapper BEFORE any handler can send
    # a message, so EVERY user-facing string is styled consistently. (This
    # was previously defined but never invoked — leaving the bot's branded
    # output system dead. See _install_smallcaps_output above.)
    try:
        _install_smallcaps_output()
    except Exception as e:
        logger.warning(f"⚠️ Failed to install smallcaps output wrapper: {e}")

    # ── Imports ───────────────────────────────────────────────────────
    from handlers.start        import start_cmd, help_cmd, menu_callback
    from handlers.commands_list import commands_cmd, commands_callback
    from handlers.economy      import (
        daily_cmd, bal_cmd, rob_cmd, kill_cmd, revive_cmd, protect_cmd,
        give_cmd, toprich_cmd, topkill_cmd, wallet_cmd, rank_cmd, pfp_cmd,
        gems_cmd, claim_cmd, coupons_cmd, create_coupon_cmd, coupon_cmd,
        del_coupon_cmd, coupon_status_cmd, economy_cmd, eco_callback,
        gbal_cmd, gkill_cmd, grob_cmd, grevive_cmd, gprotect_cmd,
        gcheck_cmd, granks_cmd, daily_remind_callback,
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
        leaderboard_callback,
        games_hub_cmd, games_hub_callback,
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
    from handlers.whisper import (
        whisper_cmd, whisper_read_callback,
        whisper_compose_callback, whisper_dm_handler,
    )
    from handlers.slots_game import slots_cmd
    from handlers.quote_sticker import quote_sticker_cmd
    from handlers.iota_roulette import roulette_cmd, rjoin_cmd, bid_cmd
    from handlers.iota_wheel import wheel_cmd
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
        zap_cmd, dancewith_cmd,
        pat_cmd, cuddle_cmd, lick_cmd, bonk_cmd, glare_cmd, feed_cmd,
        beer_cmd, cry_cmd, blush_cmd, wave_cmd, wink_cmd, dance_cmd,
        sleep_cmd, simp_cmd, sus_cmd,
        boop_cmd, ruffle_cmd, smooch_cmd, tackle_cmd, pinch_cmd,
        fistbump_cmd, bearhug_cmd, foreheadkiss_cmd, spin_cmd, dab_cmd,
        flex_cmd, nom_cmd, squish_cmd, yeet_cmd, stare_cmd, laugh_cmd,
        shoulderpunch_cmd, cheekpinch_cmd, headrub_cmd, grouphug_cmd,
        suplex_cmd, boopboop_cmd, cheekkiss_cmd, headpat_cmd,
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
        admins_cmd, own_cmd, setgroup_cmd, topgroups_cmd, removegroup_cmd,
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
    from handlers.group_panel  import (
        mygroups_cmd, gpanel_cmd, usegroup_cmd, clearactive_cmd, group_dm_callback,
    )
    from handlers.admin        import dot_admin_handler
    from handlers.welcome      import (
        new_member_handler, left_member_handler, setwelcome_cmd,
        welcome_panel_callback, soft_welcome_handler,
    )
    from handlers.protection   import (
        protection_handler, anti_raid_handler, anti_bot_handler,
        report_cmd, reports_cmd, prot_cmd, report_callback,
        protstatus_cmd,
        addword_cmd, removeword_cmd, badwords_cmd,
        blockname_cmd, unblockname_cmd, namelist_cmd,
        linkban_cmd, linkallow_cmd, linkdeny_cmd,
        linkallowlist_cmd, linkcheck_cmd,
    )
    from handlers.nsfw_filter  import nsfw_media_handler
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
        setlang_cmd,
        approve_cmd, unapprove_cmd, approved_cmd
    )
    from handlers.group_extras import (
        zombies_cmd, staff_cmd, joindate_cmd,
        banlist_cmd, kickme_cmd, tmute_cmd, tban_cmd, ghostban_cmd,
        botlist_cmd, kickbots_cmd,
        silence_cmd, unsilence_cmd,
        nightmode_cmd, forcesub_cmd,
        welcomebtn_cmd, chatrank_cmd,
        exportgconfig_cmd, importgconfig_cmd,
        modlog_cmd,
        group_gates_handler,
    )
    from handlers.ai_chat      import (
        ai_cmd, dm_message_handler, group_mention_handler,
        clear_my_memory_cmd
    )
    from handlers.owner_panel  import (
        owner_panel_cmd, addcoins_cmd, removecoins_cmd, addgems_cmd,
        addpremium_cmd, removepremium_cmd, broadcast_cmd, forward_cmd,
        addcoupon_cmd, delcoupon_cmd, ban_user_cmd, unban_user_cmd_owner, stats_cmd,
        stars_stats_cmd, setmodel_cmd, listmodels_cmd,
        scan_cmd, resetuser_cmd, giveall_cmd, maintenance_cmd, dm_cmd,
        premiumlist_cmd, userslist_cmd,
        addsticker_cmd, addstickerpack_cmd, addpack_cmd, stickerpacks_cmd, previewsticker_cmd, clearstickers_cmd,
        ttssettings_cmd, previewtts_cmd, delbroadcast_cmd, broadcasthistory_cmd,
        ttsvoices_cmd, ttsrefresh_cmd, clonevoice_cmd, clonedvoices_cmd, delclone_cmd,
        addclone_cmd,
        announce_cmd as owner_announce_cmd,
        globalclose_cmd, globalopen_cmd, premiumgiveaway_cmd,
        refreshmodels_cmd, setmaxtokens_cmd, addapikey_cmd,
        removeapikey_cmd, keypoolstatus_cmd, providerstatus_cmd,
        setpriority_cmd, toggleprovider_cmd,
        addprovider_cmd, delprovider_cmd, setprovider_cmd,
        providers_cmd, listkeys_cmd, testprovider_cmd, providerhelp_cmd,
    )
    from handlers.owner_newsys import (
        lockdown_cmd, global_unlock_cmd, slowall_cmd, lockall_cmd, unlockall_cmd,
        shieldstatus_cmd, massban_cmd, massunban_cmd, banfrom_cmd, unbanfrom_cmd,
        cleanbots_cmd, botgate_cmd, allowedbots_cmd, watchuser_cmd, unwatch_cmd,
        watchlist_cmd, suslist_cmd, schedbroadcast_cmd, schedmsg_cmd, remindall_cmd,
        scheds_cmd, cancelsched_cmd, autoreply_cmd, autoreplies_cmd, delautoreply_cmd,
        blackword_cmd, blackwords_cmd, delblackword_cmd, growth_cmd, retention_cmd,
        latency_cmd, health_cmd, pingall_cmd, deadgroups_cmd, online_cmd,
        commandstats_cmd, errorlog_cmd,         sudoadd_cmd, sudoremove_cmd, stafflist_cmd,
        handover_cmd, whereis_cmd, common_cmd, economystats_cmd,
        rain_cmd, reseteco_cmd, dbstats_cmd, exportcsv_cmd, backup_cmd, vacuum_cmd,
        indexes_cmd,         persona_cmd, defaultwelcome_cmd, forcewelcome_cmd, botbio_cmd,
        setmenu_cmd, logchat_cmd, notify_cmd, alert_cmd, ownersys_cmd,
    )
    from handlers.owner_systems import (
        leavegroup_cmd, leaveallgroups_cmd, groupslist_cmd, groupscount_cmd,
        chatinfo_cmd, osetrules_cmd, antispam_cmd, cleandb_cmd, userinfo_cmd,
        exportusers_cmd, getfile_cmd, botinfo_cmd, sysinfo_cmd, logs_cmd,
        restart_cmd, osetbotname_cmd, setbotdesc_cmd, setbotpic_cmd,
        setbotcommands_cmd, opurge_cmd,
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
    from handlers.filters import (
        filter_cmd, filters_cmd, stop_cmd, clearfilters_cmd, filter_enforcement_handler
    )
    from handlers.group_control import (
        setgtitle_cmd, setgdesc_cmd, setgpic_cmd, slowmode_cmd,
        invitelink_cmd, revoke_cmd, del_cmd
    )
    from handlers.gban import (
        gban_cmd, ungban_cmd, gbanlist_cmd, gban_join_handler
    )
    from handlers.new_features_v2 import (
        pin_cmd, unpin_cmd, purge_cmd, avatar_cmd,
        eightball_cmd, joke_cmd, fact_cmd, riddle_cmd, riddle_reveal_callback, wyr_cmd,
        reverse_cmd, mock_cmd, binary_cmd, morse_cmd, hash_cmd, password_cmd,
        nickname_cmd, birthday_cmd, birthday_daily_loop,
        todo_cmd, countdown_cmd, giveaway_cmd, giveaway_join_callback,
        lottery_cmd,
        donate_cmd, repair_cmd, raidlog_cmd, recruit_cmd,
        aijoke_cmd, advice_cmd, roastme_cmd, aistory_cmd,
    )

    # ── 🆕 Net-new systems & commands (missing features) ───────────────
    from handlers.progress import (
        achievements_cmd, dailyquest_cmd, claimquest_callback,
        stats_cmd, gleaders_cmd, level_cmd, toplevel_cmd,
    )
    from handlers.clans import (
        clan_cmd, clancreate_cmd, clanjoin_cmd, clanleave_cmd,
        clankick_cmd, clandisband_cmd, clandeposit_cmd, clanbank_cmd,
        clantop_cmd, clanmembers_cmd, clandesc_cmd, clantransfer_cmd,
    )
    from handlers.events import (
        events_cmd, startevent_cmd, stopevent_cmd, eventlist_cmd,
    )
    from handlers.extras2 import (
        weather_cmd, currency_cmd, wiki_cmd, define_cmd, short_cmd,
        time_cmd, sticky_cmd, feedback_cmd, schedule_cmd, rss_cmd,
        repin_sticky_handler, rehydrate_schedules, rss_check_loop,
    )
    from handlers.connect_four import connect4_cmd, connect4_callback
    from handlers.inline_query import inline_query_handler
    from handlers.uno import uno_cmd, uno_callback
    from handlers.chess import chess_cmd, chess_callback, chess_move_handler
    from handlers.tournament import tournament_cmd, tournament_callback
    from handlers.spectator import watch_cmd, unwatch_cmd

    # ── Banking system (canonical module) ────────────────────────────
    from handlers.banking import (
        bank_cmd, deposit_cmd, withdraw_cmd, loan_cmd, repay_cmd,
        transfer_cmd, savings_cmd, networth_cmd, fd_cmd, rd_cmd,
        statement_cmd, openbank_cmd, mybank_cmd, bankinfo_cmd, banks_cmd,
        bankdeposit_cmd, bankwithdraw_cmd, setbankname_cmd, setbankrate_cmd,
        closebank_cmd,
    )
    from handlers.business import (
        business_cmd, openbusiness_cmd, bizcollect_cmd, bizupgrade_cmd,
        bizinfo_cmd, businesses_cmd, hire_cmd, bizfire_cmd, bizjob_cmd,
        bizquit_cmd, bizinvest_cmd, bizdivest_cmd, bizinvestments_cmd,
        robbiz_cmd, bizrename_cmd, bizclose_cmd, biz_offer_callback,
        business_maintenance_loop, biztypes_cmd, bizstats_cmd,
        bizemployees_cmd, bizpromote_cmd, bizdemote_cmd, bizbonus_cmd,
        bizpenalty_cmd, bizrate_cmd, bizselect_cmd,
    )
    # 🆕 Business Empire (premium-only). Mirrors the banking commands block
    # above; every handler is gated by @premium_gate + @economy_gate internally.
    for c, f in [
        ("business", business_cmd), ("openbusiness", openbusiness_cmd),
        ("bizcollect", bizcollect_cmd), ("bizupgrade", bizupgrade_cmd),
        ("bizinfo", bizinfo_cmd), ("businesses", businesses_cmd),
        ("hire", hire_cmd), ("bizfire", bizfire_cmd), ("bizjob", bizjob_cmd),
        ("bizquit", bizquit_cmd), ("bizinvest", bizinvest_cmd),
        ("bizdivest", bizdivest_cmd), ("bizinvestments", bizinvestments_cmd),
        ("robbiz", robbiz_cmd), ("bizrename", bizrename_cmd),
        ("bizclose", bizclose_cmd), ("biztypes", biztypes_cmd),
        ("bizstats", bizstats_cmd), ("bizemployees", bizemployees_cmd),
        ("bizpromote", bizpromote_cmd), ("bizdemote", bizdemote_cmd),
        ("bizbonus", bizbonus_cmd), ("bizpenalty", bizpenalty_cmd),
        ("bizrate", bizrate_cmd), ("bizselect", bizselect_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))
    from handlers.marketplace import bazaar_cmd

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

    # 🆕 Economy extras: bank / loan / lottery / savings / transfer / bazaar
    # (@economy_gate already applied at the function definitions)
    for c, f in [
        ("bank", bank_cmd), ("deposit", deposit_cmd), ("withdraw", withdraw_cmd),
        ("loan", loan_cmd), ("repay", repay_cmd), ("networth", networth_cmd),
        ("transfer", transfer_cmd), ("savings", savings_cmd),
        ("fd", fd_cmd), ("rd", rd_cmd), ("statement", statement_cmd),
        ("openbank", openbank_cmd), ("mybank", mybank_cmd), ("bankinfo", bankinfo_cmd),
        ("banks", banks_cmd), ("bankdeposit", bankdeposit_cmd),
        ("bankwithdraw", bankwithdraw_cmd), ("setbankname", setbankname_cmd),
        ("setbankrate", setbankrate_cmd), ("closebank", closebank_cmd),
        ("lottery", lottery_cmd), ("bazaar", bazaar_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))

    # 🆕 Business Empire (premium-only). Every handler is gated internally by
    # @premium_gate + @economy_gate, so they behave exactly like the banking
    # commands. The till fills passively; a rival can /robbiz the uncollected income.
    for c, f in [
        ("business", business_cmd), ("openbusiness", openbusiness_cmd),
        ("bizcollect", bizcollect_cmd), ("bizupgrade", bizupgrade_cmd),
        ("bizinfo", bizinfo_cmd), ("businesses", businesses_cmd),
        ("hire", hire_cmd), ("bizfire", bizfire_cmd), ("bizjob", bizjob_cmd),
        ("bizquit", bizquit_cmd), ("bizinvest", bizinvest_cmd),
        ("bizdivest", bizdivest_cmd), ("bizinvestments", bizinvestments_cmd),
        ("robbiz", robbiz_cmd), ("bizrename", bizrename_cmd),
        ("bizclose", bizclose_cmd), ("biztypes", biztypes_cmd),
        ("bizstats", bizstats_cmd), ("bizemployees", bizemployees_cmd),
        ("bizpromote", bizpromote_cmd), ("bizdemote", bizdemote_cmd),
        ("bizbonus", bizbonus_cmd), ("bizpenalty", bizpenalty_cmd),
        ("bizrate", bizrate_cmd), ("bizselect", bizselect_cmd),
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

    # ── 🆕 Net-new: progress / info / games (missing features) ──────────
    for c, f in [
        ("achievements", achievements_cmd), ("dailyquest", dailyquest_cmd),
        ("stats", stats_cmd), ("gleaders", gleaders_cmd),
        ("level", level_cmd), ("toplevel", toplevel_cmd),
        ("weather", weather_cmd), ("currency", currency_cmd),
        ("wiki", wiki_cmd), ("define", define_cmd), ("short", short_cmd),
        ("time", time_cmd), ("sticky", sticky_cmd), ("feedback", feedback_cmd),
        ("schedule", schedule_cmd), ("rss", rss_cmd),
        ("connect4", connect4_cmd),
        ("uno", uno_cmd),
        ("chess", chess_cmd),
        ("tournament", tournament_cmd),
        ("watch", watch_cmd),
        ("unwatch", unwatch_cmd),
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

    # ── Clans ─────────────────────────────────────────────────────────
    for c, f in [
        ("clan", clan_cmd), ("clancreate", clancreate_cmd),
        ("clanjoin", clanjoin_cmd), ("clanleave", clanleave_cmd),
        ("clankick", clankick_cmd), ("clandisband", clandisband_cmd),
        ("clandeposit", clandeposit_cmd), ("clanbank", clanbank_cmd),
        ("clantop", clantop_cmd), ("clanmembers", clanmembers_cmd),
        ("clandesc", clandesc_cmd), ("clantransfer", clantransfer_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))

    # ── Events ───────────────────────────────────────────────────────
    for c, f in [
        ("events", events_cmd), ("startevent", startevent_cmd),
        ("stopevent", stopevent_cmd), ("eventlist", eventlist_cmd),
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
        ("games",games_hub_cmd),
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

    # ── 🎰 Iota Roulette + 🎡 Iota Wheel (Iota mini-game series) ─────────
    app.add_handler(CommandHandler("roulette", roulette_cmd))
    app.add_handler(CommandHandler("rjoin", rjoin_cmd))
    app.add_handler(CommandHandler("bid", bid_cmd))
    app.add_handler(CommandHandler("wheel", wheel_cmd))

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
        ("pat",pat_cmd),("cuddle",cuddle_cmd),("lick",lick_cmd),
        ("bonk",bonk_cmd),("glare",glare_cmd),("feed",feed_cmd),
        ("beer",beer_cmd),("cry",cry_cmd),("blush",blush_cmd),
        ("wave",wave_cmd),("wink",wink_cmd),("dance",dance_cmd),
        ("sleep",sleep_cmd),("simp",simp_cmd),("sus",sus_cmd),
        ("boop",boop_cmd),("ruffle",ruffle_cmd),("smooch",smooch_cmd),
        ("tackle",tackle_cmd),("pinch",pinch_cmd),("fistbump",fistbump_cmd),
        ("bearhug",bearhug_cmd),("foreheadkiss",foreheadkiss_cmd),
        ("spin",spin_cmd),("dab",dab_cmd),("flex",flex_cmd),
        ("nom",nom_cmd),("squish",squish_cmd),("yeet",yeet_cmd),
        ("stare",stare_cmd),("laugh",laugh_cmd),
        ("shoulderpunch",shoulderpunch_cmd),("cheekpinch",cheekpinch_cmd),
        ("headrub",headrub_cmd),("grouphug",grouphug_cmd),
        ("suplex",suplex_cmd),("boopboop",boopboop_cmd),
        ("cheekkiss",cheekkiss_cmd),("headpat",headpat_cmd),
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
        ("removegroup",removegroup_cmd),
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

    # ── DM Group Management Panel ──────────────────────────────────────
    for c, f in [
        ("mygroups", mygroups_cmd),
        ("gpanel",   gpanel_cmd),
        ("usegroup", usegroup_cmd),
        ("clearactive", clearactive_cmd),
        ("ungroup",  clearactive_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))
    app.add_handler(CallbackQueryHandler(group_dm_callback, pattern=r"^gdm_"))

    # ── Welcome & Protection ──────────────────────────────────────────
    for c, f in [
        ("prot",prot_cmd),("protstatus",protstatus_cmd),
        ("report",report_cmd),("reports",reports_cmd),
        ("addword",addword_cmd),("removeword",removeword_cmd),
        ("badwords",badwords_cmd),
        ("blockname",blockname_cmd),("unblockname",unblockname_cmd),("namelist",namelist_cmd),
        ("linkban", linkban_cmd),
        ("linkallow", linkallow_cmd),
        ("linkdeny", linkdeny_cmd),
        ("linkallowlist", linkallowlist_cmd),
        ("linkcheck", linkcheck_cmd),
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

    # ── 🆕 Powerful Admin Systems (filters / group control / GBAN) ──────
    for c, f in [
        ("filter",      filter_cmd),
        ("filters",     filters_cmd),
        ("stop",        stop_cmd),
        ("clearfilters",clearfilters_cmd),
        ("setgtitle",   setgtitle_cmd),
        ("setgdesc",    setgdesc_cmd),
        ("setgpic",     setgpic_cmd),
        ("slowmode",    slowmode_cmd),
        ("invitelink",  invitelink_cmd),
        ("revoke",      revoke_cmd),
        ("del",         del_cmd),
        ("gban",        gban_cmd),
        ("ungban",      ungban_cmd),
        ("gbanlist",    gbanlist_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))

    # ── Owner Panel ───────────────────────────────────────────────────
    for c, f in [
        ("panel",owner_panel_cmd),("addcoins",addcoins_cmd),
        ("removecoins",removecoins_cmd),("addgems",addgems_cmd),
        ("addpremium",addpremium_cmd),("removepremium",removepremium_cmd),
        ("broadcast",broadcast_cmd),("addcoupon",addcoupon_cmd),
        ("forward",forward_cmd),
        ("delcoupon",delcoupon_cmd),
        ("banuser",ban_user_cmd),("unbanuser",unban_user_cmd_owner),
        ("botstats",stats_cmd),("starsstats",stars_stats_cmd),
        ("setmodel",setmodel_cmd),("listmodels",listmodels_cmd),
        ("scan",scan_cmd),("resetuser",resetuser_cmd),
        ("giveall",giveall_cmd),("maintenance",maintenance_cmd),
        ("dm",dm_cmd),("premiumlist",premiumlist_cmd),("userslist",userslist_cmd),
        ("addsticker",addsticker_cmd),("addstickerpack",addstickerpack_cmd),("addpack",addpack_cmd),("stickerpacks",stickerpacks_cmd),
        ("previewsticker",previewsticker_cmd),("clearstickers",clearstickers_cmd),
        ("ttssettings",ttssettings_cmd),("previewtts",previewtts_cmd),
        ("ttsvoices",ttsvoices_cmd),("ttsrefresh",ttsrefresh_cmd),
        ("clonevoice",clonevoice_cmd),("clonedvoices",clonedvoices_cmd),
        ("delclone",delclone_cmd),("addclone",addclone_cmd),
        ("delbroadcast",delbroadcast_cmd),("broadcasthistory",broadcasthistory_cmd),
        ("globalclose",globalclose_cmd),("globalopen",globalopen_cmd),
        ("premiumgiveaway",premiumgiveaway_cmd),
        ("refreshmodels",refreshmodels_cmd),("setmaxtokens",setmaxtokens_cmd),
        ("addapikey",addapikey_cmd),("removeapikey",removeapikey_cmd),
        ("keypoolstatus",keypoolstatus_cmd),
        ("providerstatus",providerstatus_cmd),("providers",providers_cmd),
        ("setpriority",setpriority_cmd),("toggleprovider",toggleprovider_cmd),
        ("addprovider",addprovider_cmd),("delprovider",delprovider_cmd),
        ("setprovider",setprovider_cmd),("listkeys",listkeys_cmd),
        ("testprovider",testprovider_cmd),("providerhelp",providerhelp_cmd),
        ("whisper",whisper_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))

    # ── 🆕 Owner Systems (new powerful subsystems) ────────────────────
    for c, f in [
        ("ownersys",ownersys_cmd),("lockdown",lockdown_cmd),("globalunlock",global_unlock_cmd),
        ("slowall",slowall_cmd),("lockall",lockall_cmd),("unlockall",unlockall_cmd),
        ("shieldstatus",shieldstatus_cmd),
        ("massban",massban_cmd),("massunban",massunban_cmd),("banfrom",banfrom_cmd),
        ("unbanfrom",unbanfrom_cmd),("cleanbots",cleanbots_cmd),
        ("botgate",botgate_cmd),("allowedbots",allowedbots_cmd),
        ("watchuser",watchuser_cmd),("unwatch",unwatch_cmd),("watchlist",watchlist_cmd),
        ("suslist",suslist_cmd),
        ("schedbroadcast",schedbroadcast_cmd),("schedmsg",schedmsg_cmd),
        ("remindall",remindall_cmd),("scheds",scheds_cmd),("cancelsched",cancelsched_cmd),
        ("autoreply",autoreply_cmd),("autoreplies",autoreplies_cmd),
        ("delautoreply",delautoreply_cmd),
        ("blackword",blackword_cmd),("blackwords",blackwords_cmd),
        ("delblackword",delblackword_cmd),
        ("growth",growth_cmd),("retention",retention_cmd),("latency",latency_cmd),
        ("health",health_cmd),("pingall",pingall_cmd),("deadgroups",deadgroups_cmd),
        ("online",online_cmd),("commandstats",commandstats_cmd),("errorlog",errorlog_cmd),
        ("sudoadd",sudoadd_cmd),("sudoremove",sudoremove_cmd),("stafflist",stafflist_cmd),
        ("handover",handover_cmd),("whereis",whereis_cmd),("common",common_cmd),
        ("economystats",economystats_cmd),("rain",rain_cmd),
        ("reseteco",reseteco_cmd),
        ("dbstats",dbstats_cmd),("exportcsv",exportcsv_cmd),("backup",backup_cmd),
        ("vacuum",vacuum_cmd),("indexes",indexes_cmd),
        ("persona",persona_cmd),("defaultwelcome",defaultwelcome_cmd),
        ("forcewelcome",forcewelcome_cmd),("botbio",botbio_cmd),("setmenu",setmenu_cmd),
        ("logchat",logchat_cmd),("notify",notify_cmd),("alert",alert_cmd),
        # Master command catalog + /payment DM-only alias
        ("commands",commands_cmd),("payment",pay_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))

    # ── Extended owner systems (handlers/owner_systems.py) ──────────────
    for c, f in [
        ("leavegroup",leavegroup_cmd),("leaveallgroups",leaveallgroups_cmd),
        ("groupslist",groupslist_cmd),("groupscount",groupscount_cmd),
        ("chatinfo",chatinfo_cmd),("osetrules",osetrules_cmd),
        ("antispam",antispam_cmd),("cleandb",cleandb_cmd),
        ("userinfo",userinfo_cmd),("exportusers",exportusers_cmd),
        ("getfile",getfile_cmd),("botinfo",botinfo_cmd),("sysinfo",sysinfo_cmd),
        ("logs",logs_cmd),("restart",restart_cmd),("osetbotname",osetbotname_cmd),
        ("setbotdesc",setbotdesc_cmd),("setbotpic",setbotpic_cmd),
        ("setbotcommands",setbotcommands_cmd),("opurge",opurge_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))

    # ── Group Extras (new unique group-admin systems) ───────────────────
    for c, f in [
        ("zombies", zombies_cmd), ("staff", staff_cmd),
        ("joindate", joindate_cmd), ("banlist", banlist_cmd),
        ("kickme", kickme_cmd), ("tmute", tmute_cmd), ("tban", tban_cmd),
        ("ghostban", ghostban_cmd), ("botlist", botlist_cmd),
        ("kickbots", kickbots_cmd), ("silence", silence_cmd),
        ("unsilence", unsilence_cmd), ("nightmode", nightmode_cmd),
        ("forcesub", forcesub_cmd), ("welcomebtn", welcomebtn_cmd),
        ("chatrank", chatrank_cmd), ("exportgconfig", exportgconfig_cmd),
        ("importgconfig", importgconfig_cmd), ("modlog", modlog_cmd),
    ]:
        app.add_handler(CommandHandler(c, f))

    # ── Passive owner-systems enforcement (auto-reply / blackwords / bot-gate)
    from handlers.owner_newsys import (
        autoreply_handler, blackword_handler, botgate_handler,
    )
    app.add_handler(MessageHandler(
        filters.StatusUpdate.NEW_CHAT_MEMBERS, botgate_handler
    ), group=2)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, blackword_handler
    ), group=2)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, autoreply_handler
    ), group=3)

    # ── Group gate checks (nightmode + forcesub) — all non-command messages
    # (text + media) so nightmode can block stickers/photos/links in captions.
    app.add_handler(MessageHandler(
        ~filters.COMMAND & ~filters.StatusUpdate.ALL & ~filters.UpdateType.EDITED_MESSAGE,
        group_gates_handler,
    ), group=4)

    # ── Command-usage analytics (feeds /commandstats) ───────────────────
    # Runs alongside the real command handlers; just counts invocations.
    async def _track_command_stat(update, context):
        try:
            msg = update.effective_message
            if not msg or not msg.text or not msg.text.startswith("/"):
                return
            cmd = msg.text[1:].split("@", 1)[0].split(" ", 1)[0].lower()
            if cmd:
                from utils.mongo_db import bump_command_stat
                await bump_command_stat(cmd)
        except Exception:
            pass
    app.add_handler(MessageHandler(filters.COMMAND, _track_command_stat), group=-1)

    # ── Dot/Bang Admin prefix ─────────────────────────────────────────
    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(
        r"^[.!](warn|unwarn|warns|mute|imute|dmute|unmute|ban|dban|"
        r"unban|kick|promote|demote|unpromote|demote_all|add|remove|title|"
        r"whisper|"
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
    app.add_handler(CallbackQueryHandler(leaderboard_callback,   pattern=r"^lb_"))
    app.add_handler(CallbackQueryHandler(games_hub_callback,      pattern=r"^gh_"))
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
    # 🆕 Master /commands catalog navigation (category menu + file download)
    from handlers.commands_list import commands_callback
    app.add_handler(CallbackQueryHandler(commands_callback, pattern=r"^cmds_"))
    app.add_handler(CallbackQueryHandler(werewolf_callback,      pattern=r"^ww_"))
    app.add_handler(CallbackQueryHandler(connect_callback,       pattern=r"^conn_"))
    app.add_handler(CallbackQueryHandler(whisper_read_callback,   pattern=r"^wsp:"))
    app.add_handler(CallbackQueryHandler(whisper_compose_callback, pattern=r"^wspc$"))
    app.add_handler(CallbackQueryHandler(riddle_reveal_callback, pattern=r"^riddle_ans:"))
    app.add_handler(CallbackQueryHandler(giveaway_join_callback, pattern=r"^ga_join:"))
    app.add_handler(CallbackQueryHandler(join_request_callback, pattern=r"^jr_"))
    app.add_handler(CallbackQueryHandler(claimquest_callback, pattern=r"^pq_"))
    app.add_handler(CallbackQueryHandler(connect4_callback,  pattern=r"^cf_"))
    app.add_handler(CallbackQueryHandler(uno_callback,      pattern=r"^uno_"))
    app.add_handler(CallbackQueryHandler(chess_callback,    pattern=r"^ch_"))
    app.add_handler(CallbackQueryHandler(tournament_callback, pattern=r"^tour_"))
    # 🏢 Business Empire job-offer accept/decline buttons (mailbox style).
    app.add_handler(CallbackQueryHandler(biz_offer_callback,  pattern=r"^biz(acc|dec):"))
    app.add_handler(CallbackQueryHandler(biz_offer_callback, pattern=r"^biz(acc|dec):"))

    # ── Inline queries (net-new) ────────────────────────────────────────
    from telegram.ext import InlineQueryHandler
    app.add_handler(InlineQueryHandler(inline_query_handler))

    # ── Payments ──────────────────────────────────────────────────────
    app.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    app.add_handler(MessageHandler(
        filters.SUCCESSFUL_PAYMENT, successful_payment_handler
    ))

    # ── Group auto-tracking ───────────────────────────────────────────
    # Whenever the bot is added to ANY chat (admin OR non-admin), record the
    # group in group_settings so it is reachable by /broadcast, /announce and
    # the welcome system. Previously groups were only created lazily when an
    # admin ran an advanced-admin command, so non-admin groups were invisible
    # to broadcasts and welcome settings could not persist.
    app.add_handler(ChatMemberHandler(
        _track_group_membership, ChatMemberHandler.MY_CHAT_MEMBER
    ))

    # ── Member events ──────────────────────────────────────────────────
    # 🆕 GBAN enforcement MUST run before the welcome handler so a globally
    # banned user is booted before Iota welcomes them. Runs at group -3.
    app.add_handler(MessageHandler(
        filters.StatusUpdate.NEW_CHAT_MEMBERS, gban_join_handler
    ), group=-3)
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
    # Soft welcome: first message from a user (covers groups where bot is
    # NOT admin and Telegram never delivers NEW_CHAT_MEMBERS). Text-only.
    # Low priority group so protection/locks run first.
    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS
        & ~filters.COMMAND
        & ~filters.StatusUpdate.ALL
        & ~filters.UpdateType.EDITED_MESSAGE,
        soft_welcome_handler,
    ), group=12)

    # ── Chat join requests (captured into MongoDB for admin management) ──
    app.add_handler(ChatJoinRequestHandler(chat_join_request_handler))
    app.add_handler(MessageHandler(
        filters.StatusUpdate.PINNED_MESSAGE, channel_pin_handler
    ))
    # 🆕 Sticky: re-pin the group's sticky notice whenever something else is pinned
    app.add_handler(MessageHandler(
        filters.StatusUpdate.PINNED_MESSAGE, repin_sticky_handler
    ), group=13)

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
            # Track last-seen so /retention, /online and similar analytics
            # are meaningful.
            try:
                from utils.mongo_db import get_db, is_watched, touch_watched_activity
                await get_db().users.update_one(
                    {"_id": u.id}, {"$set": {"last_seen": int(now_ts)}}, upsert=False
                )
                # Watched users: record their latest activity for /watchlist.
                if await is_watched(u.id):
                    await touch_watched_activity(u.id, update.effective_chat.id)
            except Exception:
                pass
            # If they're messaging the bot in DM right now, they can
            # obviously receive DMs again — clear any stale "unreachable"
            # flag from a past broadcast so future broadcasts include them.
            if update.effective_chat and update.effective_chat.type == "private":
                from utils.mongo_db import mark_user_reachable
                await mark_user_reachable(u.id)
        except Exception:
            logger.debug("identity tracker: ensure_user failed", exc_info=True)

    # ── Cross-instance update de-duplication ─────────────────────────────
    # 🔴 ROOT-CAUSE FIX for "commands fire twice / on past messages" and the
    #    "Conflict: terminated by other getUpdates request" 409 errors:
    #    when two bot processes share the SAME bot token (e.g. a second
    #    Render instance that didn't shut down, or a leftover deploy), BOTH
    #    poll Telegram and each update gets handled by every instance. That
    #    double-processing is exactly why a single ".promote" can show BOTH
    #    a success ("…Promoted To Junior Admin") AND an error ("Make me an
    #    admin…" / "…is not an admin here!") — one instance acted on fresh
    #    state, the other on a different/older state.
    #
    #    We stamp every update's globally-unique update_id into a SHARED
    #    MongoDB collection (so the lock works ACROSS processes, not just
    #    within one). find_one_and_update with $setOnInsert + upsert is
    #    atomic: the FIRST instance to arrive inserts and proceeds; every
    #    other instance finds the row already there and stops the dispatch
    #    chain via ApplicationHandlerStop. A TTL index auto-expires rows so
    #    the collection stays tiny. Best-effort: if Mongo is unavailable we
    #    simply let the update through (never block the bot).
    async def _dedup_update(update, context):
        uid = getattr(update, "update_id", None)
        if uid is None:
            return
        try:
            from utils.mongo_db import get_db
            db = get_db()
            # Create the TTL index once (auto-expires rows after 90s so the
            # collection stays tiny). Best-effort. 🔴 Kept SHORT on purpose:
            # a long TTL made the dedup suppress any update re-fetched after a
            # restart (Telegram re-delivers recent updates when offset isn't
            # flushed in time), which silently dropped DMs. 90s only catches
            # genuine rapid duplicate deliveries, never a legit re-fetch.
            if not getattr(_dedup_update, "_indexed", False):
                try:
                    await db.update_dedup.create_index("t", expireAfterSeconds=90)
                    _dedup_update._indexed = True
                except Exception:
                    logger.debug("dedup TTL index create failed", exc_info=True)
            prior = await db.update_dedup.find_one_and_update(
                {"_id": uid},
                {"$setOnInsert": {"t": time.time()}},
                upsert=True,
                return_document=ReturnDocument.BEFORE,
            )
            if prior is not None:
                logger.debug(f"⏭️ Dedup: skipping already-processed update {uid}")
                raise ApplicationHandlerStop()
        except ApplicationHandlerStop:
            raise
        except Exception:
            logger.debug("dedup check failed; letting update through", exc_info=True)

    app.add_handler(TypeHandler(Update, _dedup_update), group=-10)

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
    ), group=4)

    # 3. Protection (flood/link/name/mention/caps/repeat/zalgo/…)
    # Not text-only: newmember gate + name filter must also catch stickers/media.
    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS
        & ~filters.COMMAND
        & ~filters.StatusUpdate.ALL
        & ~filters.UpdateType.EDITED_MESSAGE,
        protection_handler,
    ), group=5)

    # 3b. NSFW media filter (stickers + photos)
    app.add_handler(MessageHandler(
        (filters.Sticker.ALL | filters.PHOTO) & filters.ChatType.GROUPS,
        nsfw_media_handler
    ), group=3)

    # 4. Welcome back after dead
    app.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.GROUPS & ~filters.COMMAND,
        welcome_back_handler
    ), group=6)

    # 4b. 🆕 Admin filters (keyword auto-responders) — fire before the
    # AI mention handler so a filter reply isn't delayed by AI chatter.
    app.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.GROUPS & ~filters.COMMAND,
        filter_enforcement_handler
    ), group=7)

    # 5b. AI Truth/Dare reply handler (reacts when user replies to a T/D prompt)
    app.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.GROUPS & ~filters.COMMAND,
        truth_dare_reply_handler
    ), group=8)

    # 6. @mention / tag / direct-address AI in groups
    app.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.GROUPS & ~filters.COMMAND,
        group_mention_handler
    ), group=9)

    # 6b. AFK check handler
    app.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.GROUPS & ~filters.COMMAND,
        afk_check_handler
    ), group=10)

    # 7. Note getter (#notename)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        get_note_handler
    ), group=11)

    # 8. Hangman letter
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        hangman_handler
    ), group=12)

    # 9. Word game letter
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        wordgame_letter_handler
    ), group=13)

    # 9b. 🆕 Chess move (reply with algebraic notation, e.g. e2e4)
    app.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.GROUPS & ~filters.COMMAND,
        chess_move_handler
    ), group=21)

    # 10. Valentine form (DM)
    app.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND,
        valentine_message_handler
    ), group=14)

    # 11. Whisper private compose (captures the secret the sender types in DM,
    #       registered just before the AI auto-reply so it can pre-empt it
    #       during a compose session — the secret must never reach the model).
    app.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND,
        whisper_dm_handler
    ), group=15)

    # 11. 🔴 DM AI auto-reply.
    # CRITICAL: python-telegram-bot runs AT MOST ONE handler per group
    # (process_update `break`s after the first matching handler). This
    # handler MUST be in its OWN group, otherwise the whisper_dm_handler
    # registered just above (same filter, group 12) wins and this AI reply
    # handler NEVER runs — which is exactly why Iota went silent in DMs.
    app.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND,
        dm_message_handler
    ), group=16)

    # ── 📦 Media handlers (sticker/GIF/photo/emoji) ────────────────────
    # Sticker reply — works in DMs always; in groups only when bot is addressed
    app.add_handler(MessageHandler(
        filters.Sticker.ALL,
        sticker_reply_handler
    ), group=17)

    # GIF/animation reply — same rules as sticker
    app.add_handler(MessageHandler(
        filters.ANIMATION,
        gif_reply_handler
    ), group=18)

    # Photo reaction — triggered in DMs or when bot is @tagged/replied-to
    app.add_handler(MessageHandler(
        filters.PHOTO,
        photo_reaction_handler
    ), group=19)

    # Emoji-only messages in DMs
    app.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND,
        emoji_only_handler
    ), group=20)

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
            from utils.tts_engine import load_tts_config_db, load_voices_db, fetch_voices
            await load_tts_config_db()
            await load_voices_db()
            # Auto-fetch the live voice catalogue in the background so the
            # very first /voice or /ttsvoices uses the freshest list (and so
            # voice cloning works even if the DB had no cached list).
            asyncio.create_task(fetch_voices(force=False))
        except Exception as e:
            logger.warning(f"Failed to load TTS config/voices from DB (using defaults): {e}")

        # ── Single-instance guard ──────────────────────────────────────────
        # Guarantee exactly one process polls Telegram. Secondaries wait and
        # take over if the primary dies, so we never hit the fatal
        # "Conflict: Terminated by other getUpdates request" crash.
        try:
            from utils.instance_lock import ensure_single_instance
            await ensure_single_instance(application)
        except Exception as e:
            logger.warning(f"Instance-lock setup failed ({e}); proceeding without it.")
        # Background jobs
        # 🔴 FIX: a previous commit removed the `protection_alert_job`
        # call from here because it was undefined — but the REAL job
        # already lived in handlers/alerts.py and was simply never
        # imported/wired in. Now it IS imported and launched here, so
        # users get DM warnings when their /protect shield is about to
        # expire (6h / 2h / 30m before), instead of that code being dead.
        from handlers.alerts import protection_alert_job
        asyncio.create_task(protection_alert_job(application.bot))
        asyncio.create_task(auto_daily_job(application.bot))
        asyncio.create_task(birthday_daily_loop(application.bot))
        asyncio.create_task(_memory_cleanup_job())
        asyncio.create_task(_premium_expiry_job(application.bot))
        asyncio.create_task(_connect_expiry_job(application.bot))
        # 🏦 Banking maintenance: daily savings interest + loan overdue penalty
        from handlers.banking import banking_maintenance_loop
        asyncio.create_task(banking_maintenance_loop(application.bot))
        # 🏢 Business Empire maintenance: hourly till accrual + daily salary payouts.
        from handlers.business import business_maintenance_loop
        asyncio.create_task(business_maintenance_loop(application.bot))
        # 📅 Lazy-clean expired global events periodically
        async def _events_cleanup_job():
            while True:
                try:
                    from utils.events import cleanup_expired_events
                    await cleanup_expired_events()
                except Exception:
                    pass
                await asyncio.sleep(600)
        asyncio.create_task(_events_cleanup_job())
        # 🔁 Keep a Render/pass free Web Service awake 24/7 by self-pinging
        # its own /health route (prevents the 15-min inactivity spin-down
        # that would otherwise kill the long-poll bot too).
        asyncio.create_task(_render_keepalive_job())

        # ── 🆕 Net-new system jobs ──────────────────────────────────────
        # Enforce Telegram's 64-byte callback_data limit across ALL handlers.
        from utils.callback_codec import install_callback_guard
        install_callback_guard()
        # Re-create pending /schedule jobs lost on restart.
        await rehydrate_schedules(application)
        # Periodic RSS feed checker (posts new items to subscribed chats).
        asyncio.create_task(rss_check_loop(application))
        # 🆕 Owner-systems scheduler: fires /schedbroadcast, /schedmsg and
        # /remindall jobs when their due time arrives.
        from handlers.owner_newsys import run_scheduler_iteration
        asyncio.create_task(_scheduler_job(application.bot))

        # 🎲 Sweep abandoned game lobbies so in-memory state can't leak.
        from utils.game_lobby import lobby_expiry_job
        asyncio.create_task(lobby_expiry_job(application.bot))

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

        # 🎰 Optional Roulette Mini App — only starts when explicitly enabled
        # (ROULETTE_MINIAPP=1) AND a public WEBAPP_BASE_URL is configured.
        # Fully optional: the in-chat /roulette stays the canonical version.
        if os.environ.get("ROULETTE_MINIAPP") and WEBAPP_BASE_URL:
            try:
                from webapp.roulette_server import run_roulette_server
                asyncio.create_task(run_roulette_server(port=WEBAPP_PORT + 11))
            except Exception as e:
                logger.warning(f"⚠️ Roulette Mini App server failed to start: {e}.")

        logger.info(f"🤖 Iota Bot LIVE! Owner: {OWNER_USERNAME} (ID: {OWNER_ID})")

    app.post_init = post_init

    # ── 🔴 ROOT-CAUSE FIX: never react to PAST / stale messages ──────────
    # `drop_pending_updates=True` below only clears the backlog ONCE at
    # startup. But on network reconnects / long-poll timeouts Telegram can
    # REDISPLAY old updates (sometimes minutes/hours old), and any handler
    # then acts on a message the user already moved on from — e.g. the bot
    # suddenly "responds" to a promote/demote command from long ago. This
    # guard intercepts EVERY update BEFORE dispatch and silently drops any
    # whose effective message is older than the threshold, so the bot can
    # never react to a past command or message no matter the cause.
    from datetime import datetime, timezone as _tz
    _STALE_UPDATE_SECONDS = 120  # ignore anything older than 2 minutes

    _orig_process_update = app.process_update

    async def _guarded_process_update(update):
        try:
            chat = update.effective_chat
            # 🔴 DMs are ALWAYS answered. Never drop a private message for
            # being "stale" — the bot may have been spun down / restarted and
            # the user's DM was simply queued. Only GROUP chats keep the
            # staleness guard (its real purpose: don't react to an old admin
            # command like a stale .promote from hours ago).
            if chat is not None and chat.type == "private":
                return await _orig_process_update(update)
            msg = update.effective_message
            if msg is not None and msg.date is not None:
                # msg.date is timezone-aware UTC; compare against now UTC.
                age = (datetime.now(_tz.utc) - msg.date).total_seconds()
                if age > _STALE_UPDATE_SECONDS:
                    logger.debug(
                        f"⏩ Skipped stale update ({age:.0f}s old) — "
                        f"not reacting to past message."
                    )
                    return
        except Exception:
            logger.debug("staleness guard: date check failed", exc_info=True)
        return await _orig_process_update(update)

    app.process_update = _guarded_process_update

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

    # The cross-instance dedup collection's TTL index is created lazily on
    # first use (see _dedup_update) so we don't need an async context here.

    # ── Crash-proof polling loop ──────────────────────────────────────────
    # If a SECOND process is somehow polling the same token (a stale pre-fix
    # instance left alive by the host, or a second scaled deployment),
    # Telegram aborts with a 409 Conflict. We must NOT let that hard-crash
    # the bot or spam the owner. Instead: log it, back off, and re-run
    # run_polling (which re-runs post_init → re-checks the single-instance
    # lock). The instance that legitimately owns the lock keeps polling;
    # the other waits, so the bot stays up and self-heals once the stray
    # instance is gone. Only a clean stop exits the loop.
    try:
        from telegram.error import Conflict, TerminatedByOtherGetUpdates
    except ImportError:  # older PTB (e.g. 21.3) only exports base Conflict
        from telegram.error import Conflict
        TerminatedByOtherGetUpdates = Conflict
    while True:
        try:
            # 🔴 Do NOT drop pending updates. On Render's free tier the bot
            # spins down after inactivity; any DM sent while it was down is
            # queued by Telegram and must still be answered on wake-up.
            # `drop_pending_updates=True` was silently discarding those DMs
            # (and every DM re-fetched after a restart), which looked exactly
            # like "Iota doesn't reply in DM". Private chats are also exempt
            # from the staleness guard below, so a queued DM is always handled.
            app.run_polling()
            break  # clean shutdown
        except (Conflict, TerminatedByOtherGetUpdates) as e:
            logger.warning(
                "⚠️ Telegram Conflict during polling (another getUpdates "
                f"instance): {e}. Backing off 15s and re-checking the "
                "instance lock — the bot will resume automatically."
            )
            import time as _t
            _t.sleep(15)
        except KeyboardInterrupt:
            logger.info("🛑 Polling stopped by operator.")
            break
        except Exception as e:
            # Any other fatal polling error: log, wait, retry instead of
            # dying — a single transient failure shouldn't take the bot down.
            logger.exception(f"💥 Polling loop error: {e}. Retrying in 15s.")
            import time as _t
            _t.sleep(15)


# ── Group auto-tracking handler ────────────────────────────────────────────────

async def _track_group_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Record a chat in group_settings the moment the bot joins it (admin or
    not), so it becomes reachable by broadcasts/announces and welcome. Marks
    it inactive when the bot leaves. Best-effort: any DB error is swallowed so
    this never interferes with normal message handling.

    Also tracks whether the bot currently holds admin rights (`bot_is_admin`),
    because a NON-admin bot cannot receive `new_chat_members` / `left_chat_member`
    service messages from Telegram — so welcome, goodbye, captcha, anti-raid,
    etc. silently CANNOT work until the bot is promoted. When the bot is added
    without admin we send a one-time onboarding note explaining exactly that,
    and when it IS admin (or gets promoted later) we confirm it's ready.
    """
    mcu = update.my_chat_member
    if not mcu:
        return
    chat = update.effective_chat
    if chat is None or chat.type not in ("group", "supergroup"):
        return
    status = getattr(mcu.new_chat_member, "status", None)
    try:
        from utils.mongo_db import (
            ensure_group_settings, set_group_inactive, get_group_settings,
        )
        if status in ("member", "administrator", "creator"):
            is_admin = status in ("administrator", "creator")
            # Was this an already-tracked/active group (e.g. a routine
            # member-status refresh), or a genuine new join / re-join?
            prev = await get_group_settings(chat.id)
            was_active = bool(prev and prev.get("active"))
            await ensure_group_settings(
                chat.id, chat.title or "", active=True, bot_is_admin=is_admin
            )
            logger.info(
                f"📥 Tracked group {chat.id} ({chat.title}) — admin={is_admin}"
            )
            # Only greet on a real new join / re-join (not on every
            # status refresh), so the group isn't spammed.
            if not was_active:
                await _send_group_onboarding(context, chat.id, is_admin, chat.title)
        elif status in ("left", "kicked"):
            await set_group_inactive(chat.id)
    except Exception:
        logger.debug("group auto-tracking failed", exc_info=True)


async def _send_group_onboarding(context: ContextTypes.DEFAULT_TYPE, cid: int,
                                 is_admin: bool, title: str):
    """One-time note when the bot is added to a group.

    A non-admin bot cannot see new-member joins, so we tell the admins up
    front exactly why welcome/anti-spam won't fire and how to enable them.
    """
    from utils.safe_html import safe_html
    t = safe_html(title or "this group")
    if is_admin:
        text = (
            f"👋 <b>Hey {t}!</b>\n\n"
            f"I'm <b>Iota</b> and I have admin rights here ✅\n"
            f"Welcome messages, anti-spam, captcha, anti-raid and more are "
            f"ready to go.\n\n"
            f"• Customise welcome: <code>/setwelcome</code>\n"
            f"• Set rules: <code>/setrules</code>\n"
            f"• Full admin help: <code>/help</code>"
        )
    else:
        text = (
            f"👋 <b>Hey {t}!</b>\n\n"
            f"I was added <b>without admin rights</b>.\n"
            f"• <b>Welcome still works</b> as <b>text-only soft welcome</b> on a "
            f"member's <b>first message</b> (GIF off by default).\n"
            f"• Instant join-welcome, captcha, anti-raid need admin rights "
            f"(Telegram hides join events from non-admin bots).\n\n"
            f"🔧 <b>Full power:</b> promote me to admin "
            f"(even minimal privileges).\n\n"
            f"I'll still respond to commands like "
            f"<code>/help</code> and <code>/setwelcome</code>."
        )
    try:
        from utils.telegram_safe import safe_call
        await safe_call(
            lambda: context.bot.send_message(cid, text, parse_mode="HTML"),
            label="onboarding",
        )
    except Exception:
        pass


# ── Background jobs ────────────────────────────────────────────────────────────

async def _scheduler_job(bot):
    """Run due scheduled owner jobs every ~15s. Self-healing loop."""
    from handlers.owner_newsys import run_scheduler_iteration
    while True:
        try:
            await asyncio.sleep(15)
            await run_scheduler_iteration(bot)
        except Exception:
            logger.debug("scheduler job loop error", exc_info=True)


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


async def _render_keepalive_job(interval: int = 300):
    """
    Keep a Render (or any PaaS) free-tier Web Service awake 24/7.

    Free-tier web services spin down after ~15 min of NO inbound HTTP —
    which would also kill the Telegram long-poll bot (it has no incoming
    HTTP of its own). We ping OUR OWN public URL's /health route every
    `interval` seconds. That inbound request counts as activity, so the
    service never idles out and the bot stays live. It's self-sustaining:
    the bot pings itself → activity → no spin-down → bot keeps pinging.

    Only runs when a public URL is known:
      • RENDER_EXTERNAL_URL  (auto-injected by Render)
      • else WEBAPP_BASE_URL  (set in config.py)
    On a local/dev machine with no URL it cleanly disables itself.
    """
    url = os.environ.get("RENDER_EXTERNAL_URL") or WEBAPP_BASE_URL
    if not url:
        logger.info("ℹ️ Keep-alive self-ping disabled (no public URL configured).")
        return
    url = url.rstrip("/") + "/health"
    logger.info(f"🔁 Keep-alive self-ping enabled → {url} every {interval}s")
    while True:
        try:
            await asyncio.sleep(interval)
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    logger.debug(f"🔁 Keep-alive ping {url} → HTTP {r.status}")
        except Exception as e:
            logger.debug(f"keep-alive ping failed: {e}")


if __name__ == "__main__":
    main()
