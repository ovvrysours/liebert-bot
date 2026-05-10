"""
🖤 LIEBERT TEAM BOT — Railway Ready v2.0
pip install aiogram aiohttp aiosqlite python-dotenv
"""

import asyncio, logging, time, os, aiosqlite, aiohttp, hashlib, json, base64
from datetime import datetime
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════
#  ⚙️  CONFIG  — Railway Variables dan o'qiladi
# ═══════════════════════════════════════════════════
BOT_TOKEN         = os.getenv("BOT_TOKEN", "")
ADMIN_IDS         = [int(x) for x in os.getenv("ADMIN_IDS", "8560458125,8505877130").split(",")]

PAYME_MERCHANT_ID = os.getenv("PAYME_MERCHANT_ID", "")
PAYME_KEY         = os.getenv("PAYME_KEY", "")

CLICK_MERCHANT_ID = os.getenv("CLICK_MERCHANT_ID", "")
CLICK_SERVICE_ID  = os.getenv("CLICK_SERVICE_ID", "")
CLICK_SECRET_KEY  = os.getenv("CLICK_SECRET_KEY", "")

TON_WALLET_ADDR   = os.getenv("TON_WALLET_ADDR", "")
TON_API_KEY       = os.getenv("TON_API_KEY", "")

CARD_NUMBER       = os.getenv("CARD_NUMBER", "8600 XXXX XXXX XXXX")
CARD_OWNER        = os.getenv("CARD_OWNER", "LIEBERT TEAM")

DB_PATH           = "liebert.db"
WEBHOOK_PORT      = int(os.getenv("PORT", 8080))

# ═══════════════════════════════════════════════════
#  💰  NARXLAR
# ═══════════════════════════════════════════════════
STARS = {
    50:   {"buy": 7_000,   "sell": 11_000},
    100:  {"buy": 13_000,  "sell": 20_000},
    500:  {"buy": 63_000,  "sell": 95_000},
    1000: {"buy": 125_000, "sell": 185_000},
    2500: {"buy": 300_000, "sell": 450_000},
    5000: {"buy": 590_000, "sell": 870_000},
}
PREMIUM = {
    "1_oy":  {"buy": 30_000,  "sell": 40_000,  "label": "1 oy"},
    "3_oy":  {"buy": 150_000, "sell": 175_000, "label": "3 oy"},
    "6_oy":  {"buy": 210_000, "sell": 240_000, "label": "6 oy"},
    "12_oy": {"buy": 270_000, "sell": 300_000, "label": "12 oy"},
}

# ═══════════════════════════════════════════════════
#  🗄️  DATABASE
# ═══════════════════════════════════════════════════
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id  INTEGER PRIMARY KEY,
            username     TEXT,
            full_name    TEXT,
            total_spent  INTEGER DEFAULT 0,
            orders_count INTEGER DEFAULT 0,
            created_at   TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS orders (
            order_id    TEXT PRIMARY KEY,
            user_id     INTEGER,
            product     TEXT,
            variant     TEXT,
            target_user TEXT,
            price_som   INTEGER,
            cost_som    INTEGER,
            profit      INTEGER,
            pay_method  TEXT,
            status      TEXT DEFAULT 'pending',
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            done_at     TEXT
        );
        CREATE TABLE IF NOT EXISTS payments (
            pay_id     TEXT PRIMARY KEY,
            order_id   TEXT,
            method     TEXT,
            amount     INTEGER,
            status     TEXT DEFAULT 'pending',
            raw        TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """)
        await db.commit()
    log.info("✅ DB tayyor")

async def upsert_user(tid, username, full_name):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users(telegram_id,username,full_name) VALUES(?,?,?)
            ON CONFLICT(telegram_id) DO UPDATE SET
              username=excluded.username, full_name=excluded.full_name
        """, (tid, username, full_name))
        await db.commit()

async def create_order(order_id, user_id, product, variant, target_user, price, cost, method):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO orders(order_id,user_id,product,variant,target_user,
                               price_som,cost_som,profit,pay_method)
            VALUES(?,?,?,?,?,?,?,?,?)
        """, (order_id, user_id, product, variant, target_user, price, cost, price-cost, method))
        await db.commit()

async def set_order_done(order_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE orders SET status='completed', done_at=? WHERE order_id=?",
            (datetime.now().isoformat(), order_id))
        c = await db.execute(
            "SELECT user_id, price_som FROM orders WHERE order_id=?", (order_id,))
        row = await c.fetchone()
        if row:
            await db.execute("""
                UPDATE users SET total_spent=total_spent+?, orders_count=orders_count+1
                WHERE telegram_id=?
            """, (row[1], row[0]))
        await db.commit()

async def get_order(order_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        c = await db.execute("SELECT * FROM orders WHERE order_id=?", (order_id,))
        r = await c.fetchone()
        return dict(r) if r else None

async def save_payment(pay_id, order_id, method, amount, raw):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO payments(pay_id,order_id,method,amount,raw) VALUES(?,?,?,?,?)",
            (pay_id, order_id, method, amount, json.dumps(raw, default=str)))
        await db.commit()

async def get_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        c = await db.execute("""
            SELECT COUNT(*) total,
              SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) done,
              COALESCE(SUM(CASE WHEN status='completed' THEN price_som END),0) revenue,
              COALESCE(SUM(CASE WHEN status='completed' THEN cost_som  END),0) cost,
              COALESCE(SUM(CASE WHEN status='completed' THEN profit    END),0) profit
            FROM orders
        """)
        return dict(await c.fetchone())

async def get_today_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        today = datetime.now().strftime("%Y-%m-%d")
        c = await db.execute("""
            SELECT COUNT(*) total,
              COALESCE(SUM(profit),0) profit,
              COALESCE(SUM(price_som),0) revenue
            FROM orders WHERE status='completed' AND done_at LIKE ?
        """, (f"{today}%",))
        return dict(await c.fetchone())

# ═══════════════════════════════════════════════════
#  🌐  TON BALANS
# ═══════════════════════════════════════════════════
async def get_ton_balance() -> float:
    if not TON_WALLET_ADDR or not TON_API_KEY:
        return 0.0
    url = f"https://toncenter.com/api/v2/getAddressBalance?address={TON_WALLET_ADDR}"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers={"X-API-Key": TON_API_KEY},
                             timeout=aiohttp.ClientTimeout(total=10)) as r:
                data = await r.json()
                if data.get("ok"):
                    return int(data["result"]) / 1e9
    except Exception as e:
        log.warning(f"TON xato: {e}")
    return 0.0

# ═══════════════════════════════════════════════════
#  💳  PAYME WEBHOOK
# ═══════════════════════════════════════════════════
def payme_auth(auth_header: str) -> bool:
    try:
        encoded = auth_header.split(" ")[1]
        decoded = base64.b64decode(encoded).decode()
        _, key = decoded.split(":")
        return key == PAYME_KEY
    except Exception:
        return False

async def handle_payme(request: web.Request) -> web.Response:
    try:
        body   = await request.json()
        method = body.get("method", "")
        params = body.get("params", {})
        rid    = body.get("id", 1)

        if not payme_auth(request.headers.get("Authorization", "")):
            return web.json_response({"id": rid, "error": {"code": -32504, "message": "Forbidden"}})

        if method == "CheckPerformTransaction":
            order = await get_order(params.get("account", {}).get("order_id"))
            if not order:
                return web.json_response({"id": rid, "error": {"code": -31050, "message": "Order not found"}})
            if params.get("amount", 0) != order["price_som"] * 100:
                return web.json_response({"id": rid, "error": {"code": -31001, "message": "Wrong amount"}})
            return web.json_response({"id": rid, "result": {"allow": True}})

        elif method == "CreateTransaction":
            pay_id   = params["id"]
            order_id = params["account"]["order_id"]
            await save_payment(pay_id, order_id, "payme", params["amount"] // 100, params)
            return web.json_response({"id": rid, "result": {
                "create_time": int(time.time()*1000), "transaction": pay_id, "state": 1}})

        elif method == "PerformTransaction":
            pay_id = params["id"]
            async with aiosqlite.connect(DB_PATH) as db:
                c = await db.execute("SELECT order_id FROM payments WHERE pay_id=?", (pay_id,))
                row = await c.fetchone()
            if row:
                await payment_confirmed(row[0], "Payme")
            return web.json_response({"id": rid, "result": {
                "perform_time": int(time.time()*1000), "transaction": pay_id, "state": 2}})

        elif method == "CancelTransaction":
            return web.json_response({"id": rid, "result": {
                "cancel_time": int(time.time()*1000), "transaction": params["id"], "state": -1}})

        elif method == "CheckTransaction":
            return web.json_response({"id": rid, "result": {
                "create_time": 0, "perform_time": 0, "cancel_time": 0,
                "transaction": params["id"], "state": 2, "reason": None}})

    except Exception as e:
        log.error(f"Payme xato: {e}")
    return web.json_response({"error": "internal"}, status=500)

# ═══════════════════════════════════════════════════
#  💳  CLICK WEBHOOK
# ═══════════════════════════════════════════════════
def click_verify(data: dict) -> bool:
    sign = hashlib.md5((
        f"{data.get('click_trans_id')}{CLICK_SERVICE_ID}{CLICK_SECRET_KEY}"
        f"{data.get('merchant_trans_id')}{data.get('amount')}"
        f"{data.get('action')}{data.get('sign_time')}"
    ).encode()).hexdigest()
    return sign == data.get("sign_string", "")

async def handle_click(request: web.Request) -> web.Response:
    try:
        data     = dict(await request.post())
        action   = int(data.get("action", -1))
        order_id = data.get("merchant_trans_id", "")
        pay_id   = data.get("click_trans_id", "")
        amount   = float(data.get("amount", 0))

        if not click_verify(data):
            return web.json_response({"error": -1, "error_note": "SIGN CHECK FAILED"})

        order = await get_order(order_id)
        if not order:
            return web.json_response({"error": -5, "error_note": "Order not found"})

        if action == 0:
            return web.json_response({
                "click_trans_id": pay_id, "merchant_trans_id": order_id,
                "merchant_prepare_id": order_id, "error": 0, "error_note": "Success"})
        elif action == 1:
            await save_payment(pay_id, order_id, "click", int(amount), data)
            await payment_confirmed(order_id, "Click")
            return web.json_response({
                "click_trans_id": pay_id, "merchant_trans_id": order_id,
                "merchant_confirm_id": int(time.time()), "error": 0, "error_note": "Success"})

    except Exception as e:
        log.error(f"Click xato: {e}")
    return web.json_response({"error": -9, "error_note": "Internal error"})

# ═══════════════════════════════════════════════════
#  ✅  TO'LOV TASDIQLANGANDA
# ═══════════════════════════════════════════════════
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))

async def payment_confirmed(order_id: str, method: str):
    order = await get_order(order_id)
    if not order or order["status"] == "completed":
        return
    log.info(f"✅ To'lov: {order_id} | {method}")

    emoji    = "⭐" if order["product"] == "Stars" else "💎"
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Bajarildi",
                             callback_data=f"admin_done_{order['user_id']}_{order_id}"),
        InlineKeyboardButton(text="❌ Rad etish",
                             callback_data=f"admin_rej_{order['user_id']}_{order_id}"),
    ]])

    for admin in ADMIN_IDS:
        try:
            await bot.send_message(admin,
                f"{emoji} <b>{order['product']} — {method}</b>\n\n"
                f"👤 Kimga: <code>@{order['target_user']}</code>\n"
                f"📦 Paket: <b>{order['variant']}</b>\n"
                f"💰 Sotuv: {order['price_som']:,} so'm\n"
                f"💸 Xarid: {order['cost_som']:,} so'm\n"
                f"📈 Foyda: <b>{order['profit']:,} so'm</b>\n"
                f"🆔 <code>{order_id}</code>\n\n"
                f"⚡ Fragment.com dan yuboring, so'ng ✅ bosing!",
                reply_markup=admin_kb)
        except Exception:
            pass

    try:
        await bot.send_message(order["user_id"],
            f"✅ <b>To'lov qabul qilindi!</b>\n\n"
            f"{emoji} {order['product']} — {order['variant']}\n"
            f"👤 @{order['target_user']}\n\n"
            f"⏳ Tez orada yetkaziladi! 🖤")
    except Exception:
        pass

# ═══════════════════════════════════════════════════
#  🤖  BOT HANDLERS
# ═══════════════════════════════════════════════════
dp = Dispatcher(storage=MemoryStorage())

class Order(StatesGroup):
    username         = State()
    choosing_payment = State()
    waiting_proof    = State()

def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="⭐ Telegram Stars"),  KeyboardButton(text="💎 Telegram Premium")],
        [KeyboardButton(text="📊 Narxlar"),          KeyboardButton(text="👤 Mening Hisobim")],
        [KeyboardButton(text="📢 Kanal"),             KeyboardButton(text="❓ Yordam")],
    ], resize_keyboard=True)

def stars_kb():
    rows = [[InlineKeyboardButton(
        text=f"⭐ {amt:,} Stars — {p['sell']:,} so'm",
        callback_data=f"stars_{amt}"
    )] for amt, p in STARS.items()]
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def premium_kb():
    rows = [[InlineKeyboardButton(
        text=f"💎 {p['label']} — {p['sell']:,} so'm",
        callback_data=f"prem_{key}"
    )] for key, p in PREMIUM.items()]
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def payment_kb(order_id, price):
    rows = []
    if CLICK_SERVICE_ID:
        click_url = (
            f"https://my.click.uz/services/pay?"
            f"service_id={CLICK_SERVICE_ID}&merchant_id={CLICK_MERCHANT_ID}"
            f"&amount={price}&transaction_param={order_id}"
        )
        rows.append([InlineKeyboardButton(text="💳 Click orqali to'lash", url=click_url)])
    if PAYME_MERCHANT_ID:
        enc = base64.b64encode(
            f"m={PAYME_MERCHANT_ID};ac.order_id={order_id};a={price*100}".encode()
        ).decode()
        rows.append([InlineKeyboardButton(text="💳 Payme orqali to'lash",
                                          url=f"https://checkout.paycom.uz/{enc}")])
    rows.append([InlineKeyboardButton(text="🏦 Karta (qo'lda chek)",
                                      callback_data=f"manual_{order_id}")])
    rows.append([InlineKeyboardButton(text="❌ Bekor", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ── START ──────────────────────────────────────────
@dp.message(CommandStart())
async def start(msg: types.Message):
    await upsert_user(msg.from_user.id, msg.from_user.username, msg.from_user.full_name)
    await msg.answer(
        f"🖤 <b>Liebert Team</b>ga xush kelibsiz!\n\n"
        f"Telegram <b>Stars</b> va <b>Premium</b>ni eng arzon narxda taqdim etamiz.\n\n"
        f"Kerakli xizmatni tanlang 👇",
        reply_markup=main_kb())

# ── STARS ──────────────────────────────────────────
@dp.message(F.text == "⭐ Telegram Stars")
async def stars_menu(msg: types.Message):
    await msg.answer("⭐ <b>Telegram Stars</b>\nMiqdorni tanlang:", reply_markup=stars_kb())

@dp.callback_query(F.data.startswith("stars_"))
async def stars_select(cb: types.CallbackQuery, state: FSMContext):
    amt = int(cb.data.split("_")[1])
    p   = STARS[amt]
    await state.update_data(product="Stars", variant=f"{amt:,} Stars",
                            price=p["sell"], cost=p["buy"])
    await cb.message.edit_text(
        f"⭐ <b>{amt:,} Stars</b>\n💰 Narx: <b>{p['sell']:,} so'm</b>\n\n"
        f"Kimning akkauntiga yuboramiz?\n<i>Username kiriting (@ siz):</i>")
    await state.set_state(Order.username)
    await cb.answer()

# ── PREMIUM ────────────────────────────────────────
@dp.message(F.text == "💎 Telegram Premium")
async def premium_menu(msg: types.Message):
    await msg.answer("💎 <b>Telegram Premium</b>\nMuddat tanlang:", reply_markup=premium_kb())

@dp.callback_query(F.data.startswith("prem_"))
async def prem_select(cb: types.CallbackQuery, state: FSMContext):
    key = cb.data.split("_", 1)[1]
    p   = PREMIUM[key]
    await state.update_data(product="Premium", variant=p["label"],
                            price=p["sell"], cost=p["buy"])
    await cb.message.edit_text(
        f"💎 <b>Premium {p['label']}</b>\n💰 Narx: <b>{p['sell']:,} so'm</b>\n\n"
        f"Kimning akkauntiga yuboramiz?\n<i>Username kiriting (@ siz):</i>")
    await state.set_state(Order.username)
    await cb.answer()

# ── USERNAME & BUYURTMA ────────────────────────────
@dp.message(Order.username)
async def get_username(msg: types.Message, state: FSMContext):
    username = msg.text.lstrip("@").strip()
    if not username or len(username) < 3:
        return await msg.answer("❌ Username noto'g'ri. Qayta kiriting:")

    data     = await state.get_data()
    order_id = f"LB{msg.from_user.id}{int(time.time())}"
    await state.update_data(target=username, order_id=order_id)
    await create_order(order_id, msg.from_user.id, data["product"], data["variant"],
                       username, data["price"], data["cost"], "pending")

    await msg.answer(
        f"📋 <b>Buyurtma</b>\n\n"
        f"📦 {data['product']} — {data['variant']}\n"
        f"👤 Kimga: @{username}\n"
        f"💰 To'lov: <b>{data['price']:,} so'm</b>\n"
        f"🆔 <code>{order_id}</code>\n\n"
        f"To'lov usulini tanlang 👇",
        reply_markup=payment_kb(order_id, data["price"]))
    await state.set_state(Order.choosing_payment)

# ── QO'LDA KARTA ────────────────────────────────────
@dp.callback_query(F.data.startswith("manual_"))
async def manual_pay(cb: types.CallbackQuery, state: FSMContext):
    order_id = cb.data.split("_", 1)[1]
    order    = await get_order(order_id)
    if not order:
        return await cb.answer("Buyurtma topilmadi")
    await cb.message.edit_text(
        f"🏦 <b>Karta orqali To'lov</b>\n\n"
        f"💰 Summa: <b>{order['price_som']:,} so'm</b>\n\n"
        f"Karta: <code>{CARD_NUMBER}</code>\n"
        f"Egasi: {CARD_OWNER}\n\n"
        f"⚠️ <b>To'lovdan keyin chek rasmini yuboring!</b>")
    await state.set_state(Order.waiting_proof)
    await cb.answer()

@dp.message(Order.waiting_proof)
async def receive_proof(msg: types.Message, state: FSMContext):
    data     = await state.get_data()
    order_id = data.get("order_id", "")
    caption  = f"📸 Chek\n👤 {msg.from_user.full_name}\n🆔 {order_id}"
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Tasdiqlash",
                             callback_data=f"admin_done_{msg.from_user.id}_{order_id}"),
        InlineKeyboardButton(text="❌ Rad etish",
                             callback_data=f"admin_rej_{msg.from_user.id}_{order_id}"),
    ]])
    for admin in ADMIN_IDS:
        try:
            if msg.photo:
                await bot.send_photo(admin, msg.photo[-1].file_id,
                                     caption=caption, reply_markup=admin_kb)
            else:
                await bot.send_message(admin, caption + f"\nTxID: {msg.text}",
                                       reply_markup=admin_kb)
        except Exception:
            pass
    await msg.answer("✅ <b>Chek qabul qilindi!</b>\n\nAdmin tez orada tasdiqlaydi. 🖤",
                     reply_markup=main_kb())
    await state.clear()

@dp.callback_query(F.data == "cancel")
async def cancel(cb: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("❌ Bekor qilindi.")
    await cb.answer()

@dp.callback_query(F.data == "back")
async def go_back(cb: types.CallbackQuery):
    await cb.message.delete()
    await cb.answer()

# ═══════════════════════════════════════════════════
#  🛡️  ADMIN
# ═══════════════════════════════════════════════════
@dp.callback_query(F.data.startswith("admin_done_"))
async def admin_confirm(cb: types.CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        return await cb.answer("Ruxsat yo'q!")
    parts    = cb.data.split("_")
    user_id  = int(parts[2])
    order_id = parts[3]
    order    = await get_order(order_id)
    await set_order_done(order_id)
    try:
        await bot.send_message(user_id,
            f"✅ <b>Buyurtmangiz bajarildi!</b>\n\n"
            f"📦 {order['product']} — {order['variant']}\n"
            f"👤 @{order['target_user']}\n\nRahmat! 🖤")
    except Exception:
        pass
    await cb.message.edit_text(
        cb.message.text + f"\n\n✅ BAJARILDI | {datetime.now().strftime('%H:%M')}")
    await cb.answer("✅ Tasdiqlandi!")

@dp.callback_query(F.data.startswith("admin_rej_"))
async def admin_reject(cb: types.CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        return await cb.answer("Ruxsat yo'q!")
    parts    = cb.data.split("_")
    user_id  = int(parts[2])
    order_id = parts[3]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE orders SET status='rejected' WHERE order_id=?", (order_id,))
        await db.commit()
    try:
        await bot.send_message(user_id,
            f"❌ <b>Buyurtma rad etildi.</b>\n"
            f"Admin: @liebert_admin\n<i>ID: {order_id}</i>")
    except Exception:
        pass
    await cb.message.edit_text(cb.message.text + "\n\n❌ RAD ETILDI")
    await cb.answer("Rad etildi.")

# ── ADMIN BUYRUQLARI ────────────────────────────────
@dp.message(Command("stats"))
async def cmd_stats(msg: types.Message):
    if msg.from_user.id not in ADMIN_IDS:
        return
    s   = await get_stats()
    t   = await get_today_stats()
    ton = await get_ton_balance()
    await msg.answer(
        f"📊 <b>Statistika</b>\n\n"
        f"<b>Bugun:</b>\n"
        f"📦 Buyurtma: {t['total']}\n"
        f"💰 Daromad: {t['revenue']:,} so'm\n"
        f"📈 Foyda: <b>{t['profit']:,} so'm</b>\n\n"
        f"<b>Jami:</b>\n"
        f"📦 Jami: {s['total']} | ✅ {s['done']}\n"
        f"💰 Daromad: {s['revenue']:,} so'm\n"
        f"💸 Xarajat: {s['cost']:,} so'm\n"
        f"📈 Foyda: <b>{s['profit']:,} so'm</b>\n\n"
        f"💎 TON: <b>{ton:.3f} TON</b>")

@dp.message(Command("narxlar"))
async def cmd_prices(msg: types.Message):
    if msg.from_user.id not in ADMIN_IDS:
        return
    t = "📋 <b>Narxlar</b>\n\n<b>⭐ Stars:</b>\n"
    for amt, p in STARS.items():
        t += f"  {amt:,}: {p['buy']:,}→{p['sell']:,} | 📈 <b>{p['sell']-p['buy']:,}</b>\n"
    t += "\n<b>💎 Premium:</b>\n"
    for key, p in PREMIUM.items():
        t += f"  {p['label']}: {p['buy']:,}→{p['sell']:,} | 📈 <b>{p['sell']-p['buy']:,}</b>\n"
    await msg.answer(t)

@dp.message(Command("broadcast"))
async def cmd_broadcast(msg: types.Message):
    if msg.from_user.id not in ADMIN_IDS:
        return
    text = msg.text.replace("/broadcast", "").strip()
    if not text:
        return await msg.answer("Ishlatish: /broadcast [xabar matni]")
    async with aiosqlite.connect(DB_PATH) as db:
        c = await db.execute("SELECT telegram_id FROM users")
        users = await c.fetchall()
    sent = fail = 0
    for (uid,) in users:
        try:
            await bot.send_message(uid, text)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            fail += 1
    await msg.answer(f"📢 Yuborildi: {sent} | Xato: {fail}")

# ── INFO SAHIFALAR ──────────────────────────────────
@dp.message(F.text == "📊 Narxlar")
async def prices_page(msg: types.Message):
    t = "📊 <b>Narxlar</b>\n\n⭐ <b>Stars:</b>\n"
    for amt, p in STARS.items():
        t += f"  {amt:,} Stars — <b>{p['sell']:,} so'm</b>\n"
    t += "\n💎 <b>Premium:</b>\n"
    for key, p in PREMIUM.items():
        t += f"  {p['label']} — <b>{p['sell']:,} so'm</b>\n"
    await msg.answer(t)

@dp.message(F.text == "👤 Mening Hisobim")
async def my_account(msg: types.Message):
    async with aiosqlite.connect(DB_PATH) as db:
        c = await db.execute(
            "SELECT COUNT(*),COALESCE(SUM(price_som),0) FROM orders WHERE user_id=? AND status='completed'",
            (msg.from_user.id,))
        r = await c.fetchone()
    await msg.answer(
        f"👤 <b>Mening Hisobim</b>\n\n"
        f"Ism: {msg.from_user.full_name}\n"
        f"ID: <code>{msg.from_user.id}</code>\n"
        f"@{msg.from_user.username or '—'}\n\n"
        f"📦 Buyurtmalar: {r[0]}\n"
        f"💰 Jami sarflagan: {r[1]:,} so'm")

@dp.message(F.text == "❓ Yordam")
async def help_page(msg: types.Message):
    await msg.answer(
        "❓ <b>Yordam</b>\n\n📞 Admin: @liebert_admin\n🕐 24/7 onlayn\n📢 @liebert_team")

@dp.message(F.text == "📢 Kanal")
async def channel_page(msg: types.Message):
    await msg.answer("📢 Bizning kanal:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📢 @liebert_team", url="https://t.me/liebert_team")]]))

# ═══════════════════════════════════════════════════
#  🚀  ISHGA TUSHURISH
# ═══════════════════════════════════════════════════
async def main():
    await init_db()
    app = web.Application()
    app.router.add_post("/payme", handle_payme)
    app.router.add_post("/click", handle_click)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", WEBHOOK_PORT).start()
    log.info(f"🌐 Webhook: port {WEBHOOK_PORT}")
    log.info("🖤 Liebert Bot ishga tushdi!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
