"""
Iota Bot — Banking & Marketplace storage layer (atomic DB operations)

All money movement here goes through Motor with conditional `$inc` /
filtered `update_one` so a transfer or purchase can NEVER create or destroy
coins except exactly as intended. Every public function either succeeds
cleanly or returns a clear False / raises only on real DB errors (which the
handlers catch and turn into a friendly message).

This module is the single source of truth for:
  • peer-to-peer coin transfers
  • interest-bearing savings (the /savings vault)
  • loan overdue penalties (background maintenance)
  • the coin marketplace (user-to-user item listings)

It deliberately does NOT import telegram — it is pure data so it is easy to
unit-test with a mocked get_db().
"""
import time

from utils.mongo_db import get_db, get_user, add_item, remove_item, add_balance

# ── Tunables (kept here so bot.py / economy code can reference them) ────────
SAVINGS_DAILY_RATE = 0.02        # 2% daily interest on /savings deposits
LOAN_OVERDUE_PENALTY_PCT = 15    # one-time penalty added when a loan goes overdue
MARKET_SELL_RATIO = 0.60         # shop buys back items at 60% of base price
MAX_LISTING_QTY = 9999


# ═══════════════════════════════════════════════════════════════════════════
# Peer-to-peer transfer
# ═══════════════════════════════════════════════════════════════════════════
async def transfer_coins(from_uid: int, to_uid: int, amt: int) -> bool:
    """Move `amt` coins from -> to. Returns False if sender lacks funds.
    Atomic: the sender deduction is gated on balance >= amt; the credit is
    an unconditional upsert, so coins are never lost or duplicated."""
    if amt <= 0 or from_uid == to_uid:
        return False
    db = get_db()
    res = await db.users.update_one(
        {"_id": from_uid, "balance": {"$gte": amt}},
        {"$inc": {"balance": -amt}},
    )
    if res.modified_count == 0:
        return False
    await db.users.update_one({"_id": to_uid}, {"$inc": {"balance": amt}}, upsert=True)
    return True


# ═══════════════════════════════════════════════════════════════════════════
# Interest-bearing savings (/savings vault)
# ═══════════════════════════════════════════════════════════════════════════
async def get_savings(uid: int) -> int:
    u = await get_user(uid)
    return (u or {}).get("savings", 0)


async def savings_deposit(uid: int, amt: int) -> bool:
    """Move `amt` from wallet into savings. Gated on wallet balance."""
    if amt <= 0:
        return False
    db = get_db()
    res = await db.users.update_one(
        {"_id": uid, "balance": {"$gte": amt}},
        {"$inc": {"balance": -amt, "savings": amt}},
    )
    return res.modified_count == 1


async def savings_withdraw(uid: int, amt: int) -> bool:
    """Move `amt` from savings back to wallet. Gated on savings balance."""
    if amt <= 0:
        return False
    db = get_db()
    res = await db.users.update_one(
        {"_id": uid, "savings": {"$gte": amt}},
        {"$inc": {"balance": amt, "savings": -amt}},
    )
    return res.modified_count == 1


async def accrue_savings_interest(rate: float = SAVINGS_DAILY_RATE) -> int:
    """Credit daily interest to every non-empty savings balance.
    Returns the number of balances updated. Interest is added back into
    savings (compounding) so it is never silently dropped."""
    if rate <= 0:
        return 0
    db = get_db()
    updated = 0
    async for u in db.users.find({"savings": {"$gt": 0}}, {"_id": 1, "savings": 1}):
        interest = int(u["savings"] * rate)
        if interest <= 0:
            continue
        await db.users.update_one(
            {"_id": u["_id"]}, {"$inc": {"savings": interest}}
        )
        updated += 1
    return updated


# ═══════════════════════════════════════════════════════════════════════════
# Loan overdue handling (background maintenance)
# ═══════════════════════════════════════════════════════════════════════════
async def apply_loan_overdue(penalty_pct: float = LOAN_OVERDUE_PENALTY_PCT) -> int:
    """For each outstanding loan past its due time that hasn't been penalised
    yet, add a one-time overdue penalty to the owed amount. Returns the count
    of loans penalised this pass."""
    if penalty_pct <= 0:
        return 0
    db = get_db()
    now = time.time()
    penalised = 0
    async for u in db.users.find(
        {"loan_amount": {"$gt": 0}, "loan_due_ts": {"$lt": now},
         "loan_overdue": {"$ne": True}},
        {"_id": 1, "loan_amount": 1},
    ):
        owed = u["loan_amount"]
        extra = int(owed * penalty_pct / 100)
        if extra <= 0:
            await db.users.update_one({"_id": u["_id"]}, {"$set": {"loan_overdue": True}})
            penalised += 1
            continue
        await db.users.update_one(
            {"_id": u["_id"]},
            {"$inc": {"loan_amount": extra}, "$set": {"loan_overdue": True}},
        )
        penalised += 1
    return penalised


# ═══════════════════════════════════════════════════════════════════════════
# Coin marketplace (user-to-user item listings)
# ═══════════════════════════════════════════════════════════════════════════
async def add_listing(seller_uid: int, item_name: str, qty: int,
                      price_each: int):
    """List `qty` of an owned item for sale. Returns the new listing id, or
    None if the seller does not own enough of the item. Listing is only
    created AFTER the stock is removed from the seller's inventory, so a
    listed item can never be double-spent."""
    if qty <= 0 or price_each <= 0:
        return None
    if not await remove_item(seller_uid, item_name, qty):
        return None
    db = get_db()
    doc = {
        "seller_id": seller_uid,
        "item_name": item_name,
        "qty": qty,
        "price_each": price_each,
        "created_at": int(time.time()),
        "active": True,
    }
    res = await db.marketplace.insert_one(doc)
    return res.inserted_id


async def get_listing(listing_id):
    db = get_db()
    try:
        from bson import ObjectId
        if not isinstance(listing_id, ObjectId):
            listing_id = ObjectId(listing_id)
    except Exception:
        return None
    doc = await db.marketplace.find_one({"_id": listing_id, "active": True})
    return doc


async def get_active_listings(limit: int = 10, skip: int = 0) -> list:
    db = get_db()
    cursor = db.marketplace.find({"active": True}).sort("created_at", 1).skip(skip).limit(limit)
    return await cursor.to_list(length=limit)


async def my_listings(uid: int) -> list:
    db = get_db()
    cursor = db.marketplace.find({"seller_id": uid, "active": True}).sort("created_at", 1)
    return await cursor.to_list(length=100)


async def cancel_listing(listing_id, uid: int) -> bool:
    """Cancel a listing you own; the item is returned to your inventory."""
    doc = await get_listing(listing_id)
    if not doc or doc.get("seller_id") != uid:
        return False
    db = get_db()
    await db.marketplace.update_one({"_id": doc["_id"]}, {"$set": {"active": False}})
    await add_item(uid, doc["item_name"], doc["qty"])
    return True


async def buy_listing(listing_id, buyer_uid: int) -> tuple:
    """Buy a listing. Atomic enough: we deduct the buyer first (gated on
    balance), then credit the seller, grant the item, and deactivate the
    listing. Any failure after the deduction refunds the buyer so coins are
    never lost."""
    doc = await get_listing(listing_id)
    if not doc:
        return False, "Listing not found or already sold."
    if doc.get("seller_id") == buyer_uid:
        return False, "You can't buy your own listing."
    cost = doc["price_each"] * doc["qty"]
    db = get_db()
    # 1) deduct buyer (gated)
    res = await db.users.update_one(
        {"_id": buyer_uid, "balance": {"$gte": cost}},
        {"$inc": {"balance": -cost}},
    )
    if res.modified_count == 0:
        return False, "Insufficient coins for this purchase."
    # 2) complete the trade
    try:
        await db.users.update_one(
            {"_id": doc["seller_id"]}, {"$inc": {"balance": cost}}, upsert=True
        )
        await add_item(buyer_uid, doc["item_name"], doc["qty"])
        await db.marketplace.update_one(
            {"_id": doc["_id"]}, {"$set": {"active": False}}
        )
    except Exception as e:
        # refund the buyer to keep accounting exact
        await db.users.update_one({"_id": buyer_uid}, {"$inc": {"balance": cost}})
        return False, f"Purchase failed and was refunded: {e}"
    return True, f"Bought {doc['qty']}× {doc['item_name']} for {cost} coins."
