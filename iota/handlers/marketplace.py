"""
Iota Bot — Coin Marketplace (handler module)

The place to BUY and SELL things with coins. Two markets in one command:

  CATALOG (shop <-> you, fixed prices)
    /bazaar buy  <item> [qty]   — buy an item from the shop into your inventory
    /bazaar sell <item> [qty]   — sell an owned item back to the shop (60% of base)

  PLAYER MARKET (users trading with each other, any price)
    /bazaar list   <item> <price_each> [qty]  — list an owned item for sale
    /bazaar listings [page]                    — browse what's for sale
    /bazaar buyid <listing_id>                 — buy someone's listing
    /bazaar mine                                — your active listings
    /bazaar cancel <listing_id>                — cancel & get your item back

All coins move through utils.banking_store (atomic), every command is
@economy_gate gated, and failures always produce a clear message — never a
silent crash.
"""
from telegram import Update
from telegram.ext import ContextTypes

from config import ITEMS
from utils.mongo_db import (
    ensure_user, get_user, get_items, add_item, remove_item,
    add_balance, deduct_balance,
)
from utils.banking_store import (
    add_listing, get_active_listings, my_listings, cancel_listing,
    buy_listing, MARKET_SELL_RATIO,
)
from utils.helpers import mention, fmt
from utils.fonts import sc
from utils.system_gate import economy_gate

logger = __import__("logging").getLogger(__name__)

PAGE_SIZE = 10


def _parse_int(raw, default=1):
    try:
        v = int(str(raw))
        return v if v > 0 else default
    except (ValueError, TypeError):
        return default


async def _owned_qty(uid: int, item_name: str) -> int:
    for r in (await get_items(uid)):
        if r["item_name"] == item_name:
            return r["quantity"]
    return 0


def _catalog_text() -> str:
    lines = [f"{emoji} {name.replace('_',' ').title()} — {fmt(price)}"
             for name, (emoji, price) in ITEMS.items()]
    return "\n".join(lines)


@economy_gate
async def bazaar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    u = update.effective_user
    await ensure_user(u.id, u.username or "", u.full_name)
    args = context.args or []
    if not args:
        await msg.reply_html(
            f"🛒 <b>{sc('Bazaar — Coin Market')}</b>\n\n"
            f"{sc('Catalog')} (shop prices):\n{_catalog_text()}\n\n"
            f"🛍️ {sc('Buy')}: /bazaar buy &lt;item&gt; [qty]\n"
            f"💱 {sc('Sell to shop')}: /bazaar sell &lt;item&gt; [qty]  "
            f"({int(MARKET_SELL_RATIO*100)}% of price)\n\n"
            f"👥 {sc('Player market')}:\n"
            f"  /bazaar list &lt;item&gt; &lt;price&gt; [qty]\n"
            f"  /bazaar listings [page]\n"
            f"  /bazaar buyid &lt;id&gt;\n"
            f"  /bazaar mine | /bazaar cancel &lt;id&gt;"
        )
        return

    sub = args[0].lower()
    if sub == "buy":
        await _catalog_buy(u, msg, args[1:])
    elif sub == "sell":
        await _catalog_sell(u, msg, args[1:])
    elif sub == "list":
        await _list_item(u, msg, args[1:])
    elif sub == "listings":
        await _browse_listings(u, msg, args[1:])
    elif sub == "buyid":
        await _buy_listing(u, msg, args[1:])
    elif sub == "mine":
        await _my_listings(u, msg)
    elif sub == "cancel":
        await _cancel_listing(u, msg, args[1:])
    else:
        await msg.reply_html("🛒 " + sc("Unknown subcommand. Use /bazaar for help."))


async def _catalog_buy(u, msg, rest):
    if not rest:
        await msg.reply_html("🛍️ " + sc("Usage: /bazaar buy <item> [qty]")); return
    item = rest[0].lower()
    if item not in ITEMS:
        await msg.reply_html(f"❌ {sc('Unknown item!')} {sc('See')} /bazaar"); return
    qty = _parse_int(rest[1]) if len(rest) > 1 else 1
    emoji, price = ITEMS[item]
    cost = price * qty
    d = await get_user(u.id)
    if d.get("balance", 0) < cost:
        await msg.reply_html(f"❌ {sc('Need')} {fmt(cost)}, {sc('you have')} {fmt(d.get('balance',0))}."); return
    await deduct_balance(u.id, cost)
    await add_item(u.id, item, qty)
    await msg.reply_html(f"{emoji} {sc('Bought')} {qty}× {item.replace('_',' ').title()} {sc('for')} {fmt(cost)} {sc('coins!')}")


async def _catalog_sell(u, msg, rest):
    if not rest:
        await msg.reply_html("💱 " + sc("Usage: /bazaar sell <item> [qty]")); return
    item = rest[0].lower()
    if item not in ITEMS:
        await msg.reply_html(f"❌ {sc('Unknown item!')} {sc('See')} /bazaar"); return
    qty = _parse_int(rest[1]) if len(rest) > 1 else 1
    owned = await _owned_qty(u.id, item)
    if owned < qty:
        await msg.reply_html(f"❌ {sc('You only own')} {owned}× {item.replace('_',' ').title()}."); return
    emoji, price = ITEMS[item]
    payout = int(price * MARKET_SELL_RATIO) * qty
    await remove_item(u.id, item, qty)
    await add_balance(u.id, payout)
    await msg.reply_html(f"{emoji} {sc('Sold')} {qty}× {item.replace('_',' ').title()} {sc('for')} {fmt(payout)} {sc('coins!')}")


async def _list_item(u, msg, rest):
    if len(rest) < 2:
        await msg.reply_html("📝 " + sc("Usage: /bazaar list <item> <price_each> [qty]")); return
    item = rest[0].lower()
    if item not in ITEMS:
        await msg.reply_html(f"❌ {sc('Unknown item!')} {sc('See')} /bazaar"); return
    price_each = _parse_int(rest[1])
    qty = _parse_int(rest[2]) if len(rest) > 2 else 1
    if price_each <= 0:
        await msg.reply_html("❌ " + sc("Price must be positive.")); return
    owned = await _owned_qty(u.id, item)
    if owned < qty:
        await msg.reply_html(f"❌ {sc('You only own')} {owned}× {item.replace('_',' ').title()}."); return
    lid = await add_listing(u.id, item, qty, price_each)
    if not lid:
        await msg.reply_html("❌ " + sc("Could not list item (check you own it).")); return
    await msg.reply_html(
        f"📝 {sc('Listed')} {qty}× {item.replace('_',' ').title()} "
        f"{sc('at')} {fmt(price_each)} {sc('each')} (total {fmt(price_each*qty)}).\n"
        f"🆔 {sc('Listing')}: <code>{lid}</code>\n"
        f"{sc('Buyers use')}: /bazaar buyid {lid}"
    )


async def _browse_listings(u, msg, rest):
    page = _parse_int(rest[0]) if rest else 1
    skip = (page - 1) * PAGE_SIZE
    rows = await get_active_listings(limit=PAGE_SIZE, skip=skip)
    if not rows:
        await msg.reply_html(f"🗂️ {sc('No active listings on page')} {page}. {sc('Be the first')}: /bazaar list"); return
    out = [f"🗂️ <b>{sc('Bazaar Listings')} — {sc('Page')} {page}</b>\n"]
    for r in rows:
        seller = await get_user(r["seller_id"])
        sname = (seller or {}).get("full_name", f"User {r['seller_id']}")
        out.append(
            f"🆔 <code>{r['_id']}</code>\n"
            f"  {r['item_name'].replace('_',' ').title()} × {r['qty']} — "
            f"{fmt(r['price_each'])} {sc('each')} (={fmt(r['price_each']*r['qty'])})\n"
            f"  {sc('by')} {sname}"
        )
    out.append(f"\n{sc('Next')}: /bazaar listings {page+1}  ·  {sc('Buy')}: /bazaar buyid <id>")
    await msg.reply_html("\n".join(out))


async def _buy_listing(u, msg, rest):
    if not rest:
        await msg.reply_html("🛍️ " + sc("Usage: /bazaar buyid <listing_id>")); return
    ok, note = await buy_listing(rest[0], u.id)
    if ok:
        await msg.reply_html("✅ " + sc(note))
    else:
        await msg.reply_html("❌ " + sc(note))


async def _my_listings(u, msg):
    rows = await my_listings(u.id)
    if not rows:
        await msg.reply_html(f"📭 {sc('You have no active listings.')}"); return
    out = [f"📋 <b>{sc('Your Listings')}</b>\n"]
    for r in rows:
        out.append(
            f"🆔 <code>{r['_id']}</code> — {r['item_name'].replace('_',' ').title()} "
            f"× {r['qty']} @ {fmt(r['price_each'])}"
        )
    out.append(f"\n{sc('Cancel')}: /bazaar cancel <id>")
    await msg.reply_html("\n".join(out))


async def _cancel_listing(u, msg, rest):
    if not rest:
        await msg.reply_html("🚫 " + sc("Usage: /bazaar cancel <listing_id>")); return
    if await cancel_listing(rest[0], u.id):
        await msg.reply_html("🚫 " + sc("Listing cancelled, item returned to your inventory."))
    else:
        await msg.reply_html("❌ " + sc("Could not cancel (not found or not yours)."))
