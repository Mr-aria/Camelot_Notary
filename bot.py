import logging
import sqlite3
import random
import string
import os
from datetime import datetime
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)

# ============ CONFIG ============
BOT_TOKEN = "8935706635:AAGUtYn4AzxueCpdxSE75iTUSrsfNPorqkM"
OWNER_ID = 1275490079
BANK_BOT_USERNAME = "CamelotBank_bot"

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

# ============ DB ============
def init_db():
    conn = sqlite3.connect("database.db")
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE,
            telegram_username TEXT,
            kameloti_name TEXT,
            national_code TEXT,
            bank_account TEXT,
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            unique_code TEXT UNIQUE,
            seller_id INTEGER,
            product_name TEXT,
            price REAL,
            post_link TEXT,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_code TEXT,
            buyer_id INTEGER,
            txn_code TEXT,
            purchased_at TEXT
        );
        CREATE TABLE IF NOT EXISTS pending_buys (
            buyer_id INTEGER PRIMARY KEY,
            product_code TEXT,
            txn_code TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS blacklist (
            telegram_id INTEGER PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER,
            action TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('bot_status', 'on')")
    conn.commit()
    conn.close()

def db():
    return sqlite3.connect("database.db")

def log_action(uid, action):
    conn = db()
    conn.execute("INSERT INTO logs (telegram_id, action) VALUES (?, ?)", (uid, action))
    conn.commit()
    conn.close()

# ============ HELPERS ============
def make_code(length=8):
    chars = string.ascii_letters + string.digits
    return "".join(random.choices(chars, k=length))

def generate_unique_product_code():
    conn = db()
    while True:
        code = make_code(8)
        c = conn.execute("SELECT 1 FROM products WHERE unique_code=?", (code,))
        if not c.fetchone():
            conn.close()
            return code

def is_reg(uid):
    conn = db()
    c = conn.execute("SELECT 1 FROM users WHERE telegram_id=?", (uid,))
    r = c.fetchone()
    conn.close()
    return r is not None

def is_blacklisted(uid):
    conn = db()
    c = conn.execute("SELECT 1 FROM blacklist WHERE telegram_id=?", (uid,))
    r = c.fetchone()
    conn.close()
    return r is not None

def is_bot_active():
    conn = db()
    c = conn.execute("SELECT value FROM settings WHERE key='bot_status'")
    r = c.fetchone()
    conn.close()
    return r[0] == "on" if r else True

def get_tehran_time():
    tz = pytz.timezone('Asia/Tehran')
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

def main_menu():
    kb = [
        [InlineKeyboardButton("🛒 خرید محصول", callback_data="btn_buy"), InlineKeyboardButton("➕ افزودن محصول", callback_data="btn_add")],
        [InlineKeyboardButton("📦 لیست دارایی‌های من", callback_data="btn_assets"), InlineKeyboardButton("🏪 ویترین فروش", callback_data="btn_vitrine")]
    ]
    return InlineKeyboardMarkup(kb)

def admin_menu():
    kb = [
        [InlineKeyboardButton("👥 لیست کاربران", callback_data="admin_users"), InlineKeyboardButton("⚙️ مدیریت کاربر", callback_data="admin_manage_user")],
        [InlineKeyboardButton("🚫 لیست سیاه", callback_data="admin_blacklist"), InlineKeyboardButton("🔴/🟢 خاموش/روشن ربات", callback_data="admin_toggle_bot")],
        [InlineKeyboardButton("📜 ثبت لاگ‌ها", callback_data="admin_logs")]
    ]
    return InlineKeyboardMarkup(kb)

# Middleware Helper
async def check_access(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if is_blacklisted(uid):
        msg = "شما مسدود شده اید."
        if update.callback_query:
            await update.callback_query.answer(msg, show_alert=True)
        else:
            await update.message.reply_text(msg)
        return False

    if not is_bot_active() and uid != OWNER_ID:
        msg = "ربات خاموشه."
        if update.callback_query:
            await update.callback_query.answer(msg, show_alert=True)
        else:
            await update.message.reply_text(msg)
        return False
    return True

# ============ STATES ============
(
    REG_NAME, REG_NATIONAL, REG_BANK,
    ADD_NAME, ADD_PRICE, ADD_LINK, ADD_CONFIRM,
    BUY_CODE, BUY_CONFIRM, BUY_RECEIPT,
    EDIT_PROD_SELECT, EDIT_PROD_FIELD, EDIT_PROD_VALUE,
    ADMIN_MANAGE_ID, ADMIN_MANAGE_OPTION, ADMIN_MANAGE_VALUE,
    ADMIN_BL_ADD, ADMIN_BL_REMOVE
) = range(18)

# ============ START & REGISTRATION ============
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await check_access(update, ctx):
        return ConversationHandler.END

    uid = update.effective_user.id
    log_action(uid, "دستور /start را ارسال کرد")

    if is_reg(uid):
        await update.message.reply_text("به ربات ثبت اسناد و دارایی کملوت خوش آمدید ✅", reply_markup=main_menu())
        return ConversationHandler.END

    await update.message.reply_text("سلام! خوش آمدید.\nلطفاً نام کملوتی خود را وارد کنید:")
    return REG_NAME

async def reg_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["reg_kameloti"] = update.message.text.strip()
    await update.message.reply_text("کد ملی خود را وارد کنید:")
    return REG_NATIONAL

async def reg_national(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["reg_national"] = update.message.text.strip()
    await update.message.reply_text("شماره حساب بانکی خود را وارد کنید:")
    return REG_BANK

async def reg_bank(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    uname = update.effective_user.username or "ندارد"
    bank = update.message.text.strip()
    d = ctx.user_data

    conn = db()
    conn.execute(
        "INSERT INTO users (telegram_id, telegram_username, kameloti_name, national_code, bank_account) VALUES (?,?,?,?,?)",
        (uid, uname, d["reg_kameloti"], d["reg_national"], bank)
    )
    conn.commit()
    conn.close()

    log_action(uid, f"ثبت نام کامل کرد: {d['reg_kameloti']}")
    await update.message.reply_text("ثبت نام شما با موفقیت انجام شد!", reply_markup=main_menu())
    return ConversationHandler.END

# ============ ADD PRODUCT ============
async def add_product_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await check_access(update, ctx):
        return ConversationHandler.END

    await query.edit_message_text("نام محصول را وارد کن:")
    return ADD_NAME

async def add_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["add_name"] = update.message.text.strip()
    await update.message.reply_text("مبلغش رو وارد کن (تومان):")
    return ADD_PRICE

async def add_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        val = float(update.message.text.strip().replace(",", ""))
        ctx.user_data["add_price"] = val
    except ValueError:
        await update.message.reply_text("لطفاً یک عدد معتبر وارد کنید:")
        return ADD_PRICE

    await update.message.reply_text("لینک پستشو بفرست:")
    return ADD_LINK

async def add_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["add_link"] = update.message.text.strip()
    uid = update.effective_user.id

    conn = db()
    u = conn.execute("SELECT kameloti_name FROM users WHERE telegram_id=?", (uid,)).fetchone()
    conn.close()

    kameloti_name = u[0] if u else "کاربر"
    d = ctx.user_data

    msg = (
        f"{kameloti_name}، آیا مطمئنی که میخوای محصول «{d['add_name']}» رو "
        f"با مبلغ {d['add_price']:,.0f} تومان رو با لینک پست {d['add_link']} رو به ویترین فروشت اضافه کنی؟"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("آره", callback_data="add_yes"), InlineKeyboardButton("نه", callback_data="add_no")]
    ])
    await update.message.reply_text(msg, reply_markup=kb)
    return ADD_CONFIRM

async def add_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    if query.data == "add_yes":
        code = generate_unique_product_code()
        d = ctx.user_data
        conn = db()
        conn.execute(
            "INSERT INTO products (unique_code, seller_id, product_name, price, post_link) VALUES (?,?,?,?,?)",
            (code, uid, d["add_name"], d["add_price"], d["add_link"])
        )
        conn.commit()
        conn.close()

        log_action(uid, f"محصول جدید ثبت کرد با کد {code}")
        await query.edit_message_text(f"✅ محصول با موفقیت به ویترین اضافه شد.\nکد یکتای محصول: <code>{code}</code>", parse_mode="HTML", reply_markup=main_menu())
    else:
        await query.edit_message_text("عملیات افزودن محصول لغو شد.", reply_markup=main_menu())

    return ConversationHandler.END

# ============ BUY PRODUCT ============
async def buy_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await check_access(update, ctx):
        return ConversationHandler.END

    await query.edit_message_text("کدیکتای محصول رو وارد کنید:")
    return BUY_CODE

async def buy_code(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip()
    uid = update.effective_user.id

    conn = db()
    prod = conn.execute("SELECT unique_code, product_name, price, post_link, seller_id FROM products WHERE unique_code=? AND status='active'", (code,)).fetchone()
    conn.close()

    if not prod:
        await update.message.reply_text("محصولی با این کد یافت نشد یا قبلاً فروخته شده است. دوباره کد را ارسال کنید:")
        return BUY_CODE

    if prod[4] == uid:
        await update.message.reply_text("شما نمی‌توانید محصول خودتان را بخرید! کد دیگری وارد کنید:")
        return BUY_CODE

    ctx.user_data["buy_prod"] = prod
    msg = f"آیا محصول «{prod[1]}» با قیمت {prod[2]:,.0f} تومان و لینک پست {prod[3]} رو میخوای بخری؟"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("آره", callback_data="buy_yes"), InlineKeyboardButton("نه", callback_data="buy_no")]
    ])
    await update.message.reply_text(msg, reply_markup=kb)
    return BUY_CONFIRM

async def buy_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    if query.data == "buy_no":
        await query.edit_message_text("خرید لغو شد.", reply_markup=main_menu())
        return ConversationHandler.END

    prod = ctx.user_data["buy_prod"]
    seller_id = prod[4]

    conn = db()
    seller = conn.execute("SELECT bank_account FROM users WHERE telegram_id=?", (seller_id,)).fetchone()
    conn.close()

    seller_bank = seller[0] if seller else "ثبت نشده"
    txn_code = make_code(12)

    conn = db()
    conn.execute("INSERT OR REPLACE INTO pending_buys (buyer_id, product_code, txn_code) VALUES (?,?,?)", (uid, prod[0], txn_code))
    conn.commit()
    conn.close()

    msg = (
        f"برو به بانک (@{BANK_BOT_USERNAME}) و مبلغ {prod[2]:,.0f} تومان رو به شماره حساب {seller_bank} بزن.\n\n"
        f"⚠️ <b>نکته بسیار مهم:</b> موقع انتقال وجه به حساب فروشنده، حتماً حتماً این کد ۱۲ کاراکتری رو توی بخش توضیحات وارد کن و عملیات رو انجام بده وگرنه در غیر اینصورت پولت گم میشه و میسوزه و چیزی گیرت نمیاد:\n\n"
        f"<code>{txn_code}</code>\n\n"
        f"پس از واریز، فاکتور/رسید بانکی را <b>حتماً مستقیم از ربات بانک به همینجا فوروارد کنید</b> (به طوری که مشخص باشد از چه کسی فوروارد شده)."
    )
    await query.edit_message_text(msg, parse_mode="HTML")
    return BUY_RECEIPT

async def buy_receipt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    uid = update.effective_user.id
    log_action(uid, "یک فاکتور برای بررسی واریز ارسال/فوروارد کرد")

    # 1. Check if forwarded
    if not msg.forward_from and not msg.forward_from_chat:
        await msg.reply_text("❌ فاکتور ارسالی نامعتبر است! پیام باید حتماً مستقیماً از ربات بانک فوروارد شده باشد.")
        return BUY_RECEIPT

    f_user = msg.forward_from.username if msg.forward_from else ""
    f_chat = msg.forward_from_chat.username if msg.forward_from_chat else ""

    if f_user.lower() != BANK_BOT_USERNAME.lower() and f_chat.lower() != BANK_BOT_USERNAME.lower():
        await msg.reply_text("❌ این پیام از ربات بانک فوروارد نشده است.")
        return BUY_RECEIPT

    receipt_text = msg.text or msg.caption or ""

    conn = db()
    pending = conn.execute("SELECT product_code, txn_code FROM pending_buys WHERE buyer_id=?", (uid,)).fetchone()
    if not pending:
        await msg.reply_text("هیچ خرید معلقی برای شما یافت نشد.", reply_markup=main_menu())
        conn.close()
        return ConversationHandler.END

    prod_code, expected_txn = pending

    # 2. Check 12-char txn code
    if expected_txn not in receipt_text:
        await msg.reply_text("❌ کد۱۲ رقمی گفته شده، در فاکتور و رسید بانکی شما مشاهده نشد.")
        conn.close()
        return BUY_RECEIPT

    # 3. Get product and seller info
    prod = conn.execute("SELECT product_name, price, seller_id FROM products WHERE unique_code=?", (prod_code,)).fetchone()
    seller = conn.execute("SELECT telegram_id, bank_account FROM users WHERE telegram_id=?", (prod[2],)).fetchone()

    # 4. Check seller bank account in receipt
    if seller[1] not in receipt_text:
        await msg.reply_text("❌ فاکتور نامعتبره (شماره حساب فروشنده در رسید یافت نشد).")
        conn.close()
        return BUY_RECEIPT

    # SUCCESS: Complete Purchase
    now_tehran = get_tehran_time()
    conn.execute("UPDATE products SET status='sold' WHERE unique_code=?", (prod_code,))
    conn.execute("INSERT INTO purchases (product_code, buyer_id, txn_code, purchased_at) VALUES (?,?,?,?)", (prod_code, uid, expected_txn, now_tehran))
    conn.execute("DELETE FROM pending_buys WHERE buyer_id=?", (uid,))

    buyer_user = conn.execute("SELECT kameloti_name, bank_account FROM users WHERE telegram_id=?", (uid,)).fetchone()
    conn.commit()
    conn.close()

    log_action(uid, f"محصول {prod_code} را با موفقیت خریداری کرد")

    await msg.reply_text("✅ اوکیه و این محصول توسط شما خریداری شد و به لیست دارایی‌ها اضافه شد.", reply_markup=main_menu())

    # Send Notification to Seller
    try:
        seller_msg = (
            f"🔔 محصول شما با کدیکتای <code>{prod_code}</code>، "
            f"توسط کاربر {buyer_user[0]} و با شماره حساب {buyer_user[1]} "
            f"در تاریخ و ساعت {now_tehran} خریداری شد.\n\n"
            f"درصورت نیامدن پول به حساب شما، می‌توانید شکایت خود را به دادگاه عدالت کملوت ثبت کنید."
        )
        await ctx.bot.send_message(chat_id=seller[0], text=seller_msg, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Failed to notify seller: {e}")

    return ConversationHandler.END

# ============ VITRINE & ASSETS ============
async def show_vitrine(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await check_access(update, ctx):
        return

    conn = db()
    rows = conn.execute("SELECT unique_code, product_name, price, post_link FROM products WHERE status='active'").fetchall()
    conn.close()

    if not rows:
        await query.edit_message_text("🏪 ویترین فروش خالی است.", reply_markup=main_menu())
        return

    msg = "🏪 <b>ویترین فروش کملوت</b>\n\n"
    for code, name, price, link in rows:
        msg += f"🔹 کد: <code>{code}</code> | نام: {name} | قیمت: {price:,.0f} تومان\n🔗 <a href='{link}'>لینک پست</a>\n\n"

    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=main_menu(), disable_web_page_preview=True)

async def show_assets(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await check_access(update, ctx):
        return

    uid = query.from_user.id
    conn = db()
    bought = conn.execute("""
        SELECT p.unique_code, p.product_name, p.price, p.post_link, pu.purchased_at
        FROM purchases pu JOIN products p ON pu.product_code = p.unique_code
        WHERE pu.buyer_id = ?
    """, (uid,)).fetchall()

    my_prods = conn.execute("SELECT unique_code, product_name, price, status FROM products WHERE seller_id=?", (uid,)).fetchall()
    conn.close()

    msg = "<b>📦 لیست دارایی‌های من</b>\n\n"
    msg += "<b>🛒 محصولات خریداری شده:</b>\n"
    if bought:
        for code, name, price, link, dt in bought:
            msg += f"• {name} (کد: <code>{code}</code>) - {price:,.0f} تومان - تاریخ: {dt}\n"
    else:
        msg += "هیچ محصولی خریداری نکرده‌اید.\n"

    msg += "\n<b>🏪 محصولات من در ویترین:</b>\n"
    if my_prods:
        kb = []
        for code, name, price, st in my_prods:
            st_text = "فعال" if st == "active" else "فروخته شده"
            msg += f"• {name} (کد: <code>{code}</code>) - {price:,.0f} تومان [{st_text}]\n"
            if st == "active":
                kb.append([InlineKeyboardButton(f"✏️ ویرایش/حذف {name}", callback_data=f"manage_prod_{code}")])
        kb.append([InlineKeyboardButton("بازگشت", callback_data="back_main")])
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
    else:
        msg += "شما هیچ محصولی برای فروش نگذاشته‌اید."
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=main_menu())

# ============ EDIT / DELETE USER PRODUCT ============
async def manage_prod_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    code = query.data.replace("manage_prod_", "")
    ctx.user_data["edit_code"] = code

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("ویرایش نام", callback_data="ep_name"), InlineKeyboardButton("ویرایش قیمت", callback_data="ep_price")],
        [InlineKeyboardButton("ویرایش لینک", callback_data="ep_link"), InlineKeyboardButton("❌ حذف از ویترین", callback_data="ep_delete")],
        [InlineKeyboardButton("انصراف", callback_data="back_main")]
    ])
    await query.edit_message_text(f"مدیریت محصول با کد <code>{code}</code>:\nلطفاً اقدام مورد نظر را انتخاب کنید:", parse_mode="HTML", reply_markup=kb)
    return EDIT_PROD_OPTION

async def edit_prod_option_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    act = query.data
    code = ctx.user_data["edit_code"]

    if act == "ep_delete":
        conn = db()
        conn.execute("DELETE FROM products WHERE unique_code=?", (code,))
        conn.commit()
        conn.close()
        await query.edit_message_text("✅ محصول با موفقیت حذف شد.", reply_markup=main_menu())
        return ConversationHandler.END

    ctx.user_data["edit_field"] = act
    field_name = "نام جدید" if act == "ep_name" else ("قیمت جدید" if act == "ep_price" else "لینک پست جدید")
    await query.edit_message_text(f"مقدار جدید برای {field_name} را وارد کنید (کد یکتا تغییر نمی‌کند):")
    return EDIT_PROD_VALUE

async def edit_prod_value_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    val = update.message.text.strip()
    code = ctx.user_data["edit_code"]
    field = ctx.user_data["edit_field"]

    conn = db()
    if field == "ep_name":
        conn.execute("UPDATE products SET product_name=? WHERE unique_code=?", (val, code))
    elif field == "ep_price":
        try:
            p_val = float(val.replace(",", ""))
            conn.execute("UPDATE products SET price=? WHERE unique_code=?", (p_val, code))
        except ValueError:
            await update.message.reply_text("لطفاً قیمت را به صورت عددی وارد کنید:")
            return EDIT_PROD_VALUE
    elif field == "ep_link":
        conn.execute("UPDATE products SET post_link=? WHERE unique_code=?", (val, code))
    conn.commit()
    conn.close()

    await update.message.reply_text("✅ اطلاعات محصول با موفقیت بروزرسانی شد.", reply_markup=main_menu())
    return ConversationHandler.END

# ============ ADMIN PANEL ============
async def admin_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != OWNER_ID:
        return

    await update.message.reply_text("👑 **پنل مدیریت مالک**", parse_mode="Markdown", reply_markup=admin_menu())

async def admin_buttons(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if uid != OWNER_ID:
        return

    data = query.data

    if data == "admin_users":
        conn = db()
        users = conn.execute("SELECT telegram_id, telegram_username, kameloti_name, national_code, bank_account FROM users").fetchall()
        conn.close()
        msg = "👥 <b>لیست کلیه کاربران:</b>\n\n"
        for u in users:
            msg += f"🆔 {u[0]} | @{u[1]} | نام: {u[2]} | کدملی: {u[3]} | حساب: {u[4]}\n"
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=admin_menu())

    elif data == "admin_toggle_bot":
        conn = db()
        curr = conn.execute("SELECT value FROM settings WHERE key='bot_status'").fetchone()[0]
        new_st = "off" if curr == "on" else "on"
        conn.execute("UPDATE settings SET value=? WHERE key='bot_status'", (new_st,))
        conn.commit()
        conn.close()
        st_txt = "روشـن" if new_st == "on" else "خامـوش"
        await query.edit_message_text(f"وضعیت ربات تغییر یافت به: <b>{st_txt}</b>", parse_mode="HTML", reply_markup=admin_menu())

    elif data == "admin_logs":
        conn = db()
        logs = conn.execute("SELECT telegram_id, action, timestamp FROM logs ORDER BY id DESC LIMIT 25").fetchall()
        conn.close()
        msg = "📜 <b>آخرین لاگ‌های سیستم:</b>\n\n"
        for l in logs:
            msg += f"[{l[2]}] کاربر {l[0]}: {l[1]}\n"
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=admin_menu())

# ADMIN MANAGE USER CONVERSATION
async def admin_manage_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("آیدی عددی تلگرامی کاربر را وارد کنید:")
    return ADMIN_MANAGE_ID

async def admin_manage_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        t_id = int(update.message.text.strip())
        ctx.user_data["adm_target_uid"] = t_id
    except ValueError:
        await update.message.reply_text("آیدی عددی معتبر نیست. مجدداً وارد کنید:")
        return ADMIN_MANAGE_ID

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("تغییر نام کملوتی", callback_data="adm_u_name")],
        [InlineKeyboardButton("تغییر شماره حساب", callback_data="adm_u_bank")],
        [InlineKeyboardButton("تغییر یوزرنیم", callback_data="adm_u_uname")]
    ])
    await update.message.reply_text(f"کاربر {t_id} انتخاب شد. حالا میخوای چیکارش کنیم؟", reply_markup=kb)
    return ADMIN_MANAGE_OPTION

async def admin_manage_option(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["adm_u_field"] = query.data
    await query.edit_message_text("مقدار جدید را وارد کنید:")
    return ADMIN_MANAGE_VALUE

async def admin_manage_value(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    val = update.message.text.strip()
    t_id = ctx.user_data["adm_target_uid"]
    field = ctx.user_data["adm_u_field"]

    conn = db()
    if field == "adm_u_name":
        conn.execute("UPDATE users SET kameloti_name=? WHERE telegram_id=?", (val, t_id))
    elif field == "adm_u_bank":
        conn.execute("UPDATE users SET bank_account=? WHERE telegram_id=?", (val, t_id))
    elif field == "adm_u_uname":
        conn.execute("UPDATE users SET telegram_username=? WHERE telegram_id=?", (val, t_id))
    conn.commit()
    conn.close()

    await update.message.reply_text("✅ اطلاعات کاربر با موفقیت بروز شد.", reply_markup=admin_menu())
    return ConversationHandler.END

# ADMIN BLACKLIST CONVERSATION
async def admin_bl_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ افزودن به لیست سیاه", callback_data="bl_add")],
        [InlineKeyboardButton("➖ خروج از لیست سیاه", callback_data="bl_remove")]
    ])
    conn = db()
    bl = conn.execute("SELECT telegram_id FROM blacklist").fetchall()
    conn.close()
    msg = "🚫 <b>لیست سیاه فعلی:</b>\n" + "\n".join([str(b[0]) for b in bl])
    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=kb)
    return ADMIN_BL_ADD

async def admin_bl_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "bl_add":
        ctx.user_data["bl_action"] = "add"
        await query.edit_message_text("آیدی عددی کاربر جهت مسدودسازی را وارد کنید:")
    else:
        ctx.user_data["bl_action"] = "remove"
        await query.edit_message_text("آیدی عددی کاربر جهت آنبلاک را وارد کنید:")
    return ADMIN_BL_REMOVE

async def admin_bl_execute(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        t_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("آیدی عددی نامعتبر است.")
        return ConversationHandler.END

    act = ctx.user_data.get("bl_action")
    conn = db()
    if act == "add":
        conn.execute("INSERT OR IGNORE INTO blacklist (telegram_id) VALUES (?)", (t_id,))
        await update.message.reply_text(f"کاربر {t_id} به لیست سیاه اضافه شد.")
    else:
        conn.execute("DELETE FROM blacklist WHERE telegram_id=?", (t_id,))
        await update.message.reply_text(f"کاربر {t_id} از لیست سیاه حذف شد.")
    conn.commit()
    conn.close()
    return ConversationHandler.END

# ============ CANCEL & BACK ============
async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("عملیات لغو شد.", reply_markup=main_menu())
    return ConversationHandler.END

async def back_main_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("صفحه اصلی:", reply_markup=main_menu())

# ============ MAIN APP ============
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    # 1. Registration Conv
    reg_conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            REG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name)],
            REG_NATIONAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_national)],
            REG_BANK: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_bank)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # 2. Add Product Conv
    add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_product_start, pattern="^btn_add$")],
        states={
            ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_name)],
            ADD_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_price)],
            ADD_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_link)],
            ADD_CONFIRM: [CallbackQueryHandler(add_confirm, pattern="^(add_yes|add_no)$")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # 3. Buy Conv
    buy_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(buy_start, pattern="^btn_buy$")],
        states={
            BUY_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, buy_code)],
            BUY_CONFIRM: [CallbackQueryHandler(buy_confirm, pattern="^(buy_yes|buy_no)$")],
            BUY_RECEIPT: [MessageHandler(filters.ALL & ~filters.COMMAND, buy_receipt)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # 4. User Product Edit/Delete Conv
    EDIT_PROD_OPTION = 20
    edit_prod_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(manage_prod_callback, pattern="^manage_prod_")],
        states={
            EDIT_PROD_OPTION: [CallbackQueryHandler(edit_prod_option_handler, pattern="^(ep_name|ep_price|ep_link|ep_delete)$")],
            EDIT_PROD_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_prod_value_handler)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(back_main_cb, pattern="^back_main$")],
    )

    # 5. Admin Manage User Conv
    adm_user_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_manage_start, pattern="^admin_manage_user$")],
        states={
            ADMIN_MANAGE_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_manage_id)],
            ADMIN_MANAGE_OPTION: [CallbackQueryHandler(admin_manage_option, pattern="^adm_u_")],
            ADMIN_MANAGE_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_manage_value)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # 6. Admin Blacklist Conv
    adm_bl_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_bl_start, pattern="^admin_blacklist$")],
        states={
            ADMIN_BL_ADD: [CallbackQueryHandler(admin_bl_choice, pattern="^(bl_add|bl_remove)$")],
            ADMIN_BL_REMOVE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_bl_execute)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Register Handlers
    app.add_handler(reg_conv)
    app.add_handler(add_conv)
    app.add_handler(buy_conv)
    app.add_handler(edit_prod_conv)
    app.add_handler(adm_user_conv)
    app.add_handler(adm_bl_conv)

    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CallbackQueryHandler(show_vitrine, pattern="^btn_vitrine$"))
    app.add_handler(CallbackQueryHandler(show_assets, pattern="^btn_assets$"))
    app.add_handler(CallbackQueryHandler(back_main_cb, pattern="^back_main$"))
    app.add_handler(CallbackQueryHandler(admin_buttons, pattern="^admin_"))

    print("🤖 Bot is running successfully...")
    app.run_polling()

if __name__ == "__main__":
    main()
