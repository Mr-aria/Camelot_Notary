# -*- coding: utf-8 -*-
"""Camelot Telegram Marketplace Bot - Customized Flow with Backup/Restore"""

from __future__ import annotations

import logging
import os
import secrets
import sqlite3
import string
import threading
import json
import io
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, List, Dict, Any

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    ConversationHandler,
)

# -----------------------------
# Configuration
# -----------------------------

BOT_TOKEN = os.getenv("BOT_TOKEN", "توکن_ربات_را_اینجا_بگذارید_یا_در_متغیرهای_محیطی")
OWNER_ID = 1275490079
BANK_BOT_USERNAME = "camelotbank_bot"
TEHRAN = ZoneInfo("Asia/Tehran")
DB_PATH = "bot.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("camelot-bot")

# -----------------------------
# Constants / States
# -----------------------------

BTN_BUY = "🛒 خرید محصول"
BTN_ADD = "➕ افزودن محصول"
BTN_ASSETS = "📦 لیست دارایی های من"
BTN_VITRINE = "🛍 ویترین فروش"
BTN_ADMIN = "🛠 پنل مدیریت"

S_REG_NAME = "reg_name"
S_REG_NID = "reg_nid"
S_REG_ACCOUNT = "reg_account"

S_ADD_NAME = "add_name"
S_ADD_PRICE = "add_price"
S_ADD_LINK = "add_link"

S_BUY_CODE = "buy_code"
S_BUY_RECEIPT = "buy_receipt"

S_EDIT_PRODUCT_FIELD = "edit_product_field"
S_EDIT_PRODUCT_VALUE = "edit_product_value"

S_ADMIN_GET_USER_ID = "admin_get_user_id"
S_ADMIN_EDIT_USER_FIELD = "admin_edit_user_field"
S_ADMIN_EDIT_USER_VALUE = "admin_edit_user_value"
S_ADMIN_BLACKLIST_ADD = "admin_blacklist_add"
S_ADMIN_BLACKLIST_REMOVE = "admin_blacklist_remove"

# New states for backup/restore
S_ADMIN_BACKUP_IMPORT_FILE = "admin_backup_import_file"
S_ADMIN_BACKUP_CONFIRM = "admin_backup_confirm"
S_RESTORE_ACCOUNT_FILE = "restore_account_file"
S_RESTORE_ACCOUNT_CONFIRM = "restore_account_confirm"

# -----------------------------
# SQLite helpers
# -----------------------------

_db_lock = threading.RLock()
_db = sqlite3.connect(DB_PATH, check_same_thread=False)
_db.row_factory = sqlite3.Row

def db_exec(query: str, params: tuple = ()) -> None:
    with _db_lock:
        cur = _db.execute(query, params)
        _db.commit()
        return cur

def db_one(query: str, params: tuple = ()) -> Optional[sqlite3.Row]:
    with _db_lock:
        cur = _db.execute(query, params)
        return cur.fetchone()

def db_all(query: str, params: tuple = ()) -> List[sqlite3.Row]:
    with _db_lock:
        cur = _db.execute(query, params)
        return cur.fetchall()

def init_db() -> None:
    with _db_lock:
        _db.execute("PRAGMA journal_mode=WAL;")
        _db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                name TEXT,
                national_id TEXT,
                bank_account TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        _db.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                code TEXT PRIMARY KEY,
                seller_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                price INTEGER NOT NULL,
                link TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                sold_to INTEGER,
                sold_at TEXT
            )
            """
        )
        _db.execute(
            """
            CREATE TABLE IF NOT EXISTS purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                buyer_id INTEGER NOT NULL,
                seller_id INTEGER NOT NULL,
                product_code TEXT NOT NULL,
                product_name TEXT NOT NULL,
                price INTEGER NOT NULL,
                seller_account TEXT NOT NULL,
                transaction_code TEXT NOT NULL UNIQUE,
                receipt_text TEXT NOT NULL,
                purchased_at TEXT NOT NULL
            )
            """
        )
        _db.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_buys (
                buyer_id INTEGER PRIMARY KEY,
                product_code TEXT NOT NULL,
                seller_id INTEGER NOT NULL,
                seller_account TEXT NOT NULL,
                price INTEGER NOT NULL,
                transaction_code TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )
            """
        )
        _db.execute(
            """
            CREATE TABLE IF NOT EXISTS blacklist (
                telegram_id INTEGER PRIMARY KEY,
                reason TEXT,
                added_at TEXT NOT NULL
            )
            """
        )
        _db.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        _db.execute(
            """
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER,
                action TEXT NOT NULL,
                details TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        _db.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('bot_status', 'on')")
        _db.commit()

init_db()

# -----------------------------
# Utility helpers
# -----------------------------

def now_tehran() -> str:
    return datetime.now(TEHRAN).strftime("%Y-%m-%d %H:%M:%S")

def fmt_money(amount: int) -> str:
    return f"{amount:,}"

def normalize_text(text: Optional[str]) -> str:
    return (text or "").strip()

def make_code(length: int, chars: str = string.ascii_letters + string.digits) -> str:
    return "".join(secrets.choice(chars) for _ in range(length))

def unique_product_code() -> str:
    while True:
        code = make_code(8)
        if not db_one("SELECT 1 FROM products WHERE code = ?", (code,)):
            return code

def unique_transaction_code() -> str:
    while True:
        code = make_code(12)
        if not db_one("SELECT 1 FROM purchases WHERE transaction_code = ?", (code,)) and not db_one(
            "SELECT 1 FROM pending_buys WHERE transaction_code = ?", (code,)
        ):
            return code

def log_action(telegram_id: Optional[int], action: str, details: str = "") -> None:
    db_exec(
        "INSERT INTO logs(telegram_id, action, details, created_at) VALUES(?, ?, ?, ?)",
        (telegram_id, action, details, now_tehran()),
    )

def bot_is_on() -> bool:
    row = db_one("SELECT value FROM settings WHERE key = 'bot_status'")
    return row["value"] == "on" if row else True

def set_bot_status(status: str) -> None:
    db_exec("INSERT INTO settings(key, value) VALUES('bot_status', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (status,))

def get_user(uid: int) -> Optional[sqlite3.Row]:
    return db_one("SELECT * FROM users WHERE telegram_id = ?", (uid,))

def get_user_display(uid: int) -> str:
    row = get_user(uid)
    return row["name"] if row else str(uid)

def user_is_blacklisted(uid: int) -> bool:
    return db_one("SELECT 1 FROM blacklist WHERE telegram_id = ?", (uid,)) is not None

def is_owner(uid: int) -> bool:
    return uid == OWNER_ID

def user_state(context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    return context.user_data.get("state")

def set_state(context: ContextTypes.DEFAULT_TYPE, state: Optional[str]) -> None:
    if state is None:
        context.user_data.pop("state", None)
    else:
        context.user_data["state"] = state

def clear_temp(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["temp"] = {}

def get_temp(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.user_data.setdefault("temp", {})

def main_menu_kb(uid: int) -> ReplyKeyboardMarkup:
    rows = [
        [BTN_BUY, BTN_ADD], 
        [BTN_ASSETS, BTN_VITRINE]
    ]
    if is_owner(uid):
        rows.append([BTN_ADMIN])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو عملیات", callback_data="cancel_action")]])

def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 لیست کاربران", callback_data="admin_users")],
        [InlineKeyboardButton("🧑‍💼 مدیریت کاربر", callback_data="admin_manage_user")],
        [InlineKeyboardButton("⛔ لیست سیاه", callback_data="admin_blacklist")],
        [InlineKeyboardButton("📄 ثبت لاگ ها", callback_data="admin_logs")],
        [InlineKeyboardButton("🔌 خاموش/روشن", callback_data="admin_toggle_bot")],
        [InlineKeyboardButton("💾 پشتیبان‌گیری و بازیابی", callback_data="admin_backup")],
        [InlineKeyboardButton("❌ بستن پنل", callback_data="cancel_action")],
    ])

def confirm_kb(yes_data: str, no_data: str = "cancel_action") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ آره", callback_data=yes_data),
        InlineKeyboardButton("❌ نه", callback_data=no_data),
    ]])

def product_actions_kb(code: str, seller_id: int, viewer_id: int) -> InlineKeyboardMarkup:
    rows = []
    if viewer_id == seller_id or is_owner(viewer_id):
        rows.append([
            InlineKeyboardButton("✏️ ویرایش", callback_data=f"edit_product:{code}"),
            InlineKeyboardButton("🗑 حذف", callback_data=f"delete_product:{code}"),
        ])
    return InlineKeyboardMarkup(rows)

def admin_user_actions_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("تغییر نام کملوتی", callback_data=f"admin_user_name:{uid}")],
        [InlineKeyboardButton("تغییر شماره حساب", callback_data=f"admin_user_account:{uid}")],
        [InlineKeyboardButton("تغییر یوزرنیم", callback_data=f"admin_user_username:{uid}")],
        [InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_back")],
    ])

def safe_send_chunks(text: str, max_len: int = 3900) -> List[str]:
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, max_len)
        if cut == -1:
            cut = max_len
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks

# ==================== Backup & Restore Functions ====================

def export_full_backup() -> str:
    """Export all tables as JSON."""
    tables = ['users', 'products', 'purchases', 'pending_buys', 'blacklist', 'settings', 'logs']
    data = {}
    with _db_lock:
        for table in tables:
            cursor = _db.execute(f"SELECT * FROM {table}")
            rows = cursor.fetchall()
            # Convert rows to list of dicts
            data[table] = [dict(row) for row in rows]
    return json.dumps(data, indent=2, ensure_ascii=False)

def import_full_backup(json_data: str) -> tuple:
    """
    Restore database from JSON backup.
    Returns (success: bool, message: str)
    """
    try:
        data = json.loads(json_data)
    except json.JSONDecodeError as e:
        return False, f"فایل JSON معتبر نیست: {e}"
    
    # Validate structure
    expected_tables = {'users', 'products', 'purchases', 'pending_buys', 'blacklist', 'settings', 'logs'}
    if not expected_tables.issubset(data.keys()):
        return False, "فایل پشتیبان کامل نیست. جداول مورد نیاز وجود ندارند."
    
    with _db_lock:
        # Begin transaction
        try:
            # Clear existing data
            for table in expected_tables:
                _db.execute(f"DELETE FROM {table}")
            
            # Insert new data
            for table, rows in data.items():
                if not rows:
                    continue
                # Get column names from first row
                columns = list(rows[0].keys())
                placeholders = ','.join(['?' for _ in columns])
                col_names = ','.join(columns)
                for row in rows:
                    values = [row.get(col) for col in columns]
                    _db.execute(f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})", values)
            
            _db.commit()
            return True, "بازیابی با موفقیت انجام شد."
        except Exception as e:
            _db.rollback()
            return False, f"خطا در بازیابی: {str(e)}"

# ==================== Access control ====================

async def check_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    uid = update.effective_user.id if update.effective_user else None
    if uid is None:
        return False
    
    if user_is_blacklisted(uid):
        msg = "شما مسدود شده اید"
        if update.message:
            await update.message.reply_text(msg)
        elif update.callback_query:
            await update.callback_query.answer(msg, show_alert=True)
        return False
        
    if not bot_is_on() and not is_owner(uid):
        msg = "ربات خاموشه"
        if update.message:
            await update.message.reply_text(msg)
        elif update.callback_query:
            await update.callback_query.answer(msg, show_alert=True)
        return False
        
    return True

async def ensure_registered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    uid = update.effective_user.id
    if get_user(uid):
        return True
    # If owner, show restore option
    if is_owner(uid):
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 بازیابی اطلاعات", callback_data="restore_account")],
            [InlineKeyboardButton("📝 ثبت‌نام جدید", callback_data="register_new")]
        ])
        await update.message.reply_text(
            "🏦 **به ثبت‌اسناد کملوت خوش آمدید!**\n\n"
            "شما به عنوان مالک وارد شده‌اید.\n"
            "• اگر قبلاً حساب داشته‌اید و اطلاعات آن را بازیابی کرده‌اید، روی «بازیابی اطلاعات» کلیک کنید.\n"
            "• اگر می‌خواهید حساب جدید بسازید، روی «ثبت‌نام جدید» کلیک کنید.",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        return False
    set_state(context, S_REG_NAME)
    clear_temp(context)
    await update.message.reply_text("نام کملوتی خود را وارد کنید:", reply_markup=ReplyKeyboardRemove())
    log_action(uid, "start_registration", "prompted name")
    return False

# -----------------------------
# Start / Cancel / Admin command
# -----------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return
    uid = update.effective_user.id
    username = (update.effective_user.username or "").strip()
    
    existing = get_user(uid)
    if existing:
        db_exec("UPDATE users SET username = ?, updated_at = ? WHERE telegram_id = ?", (username, now_tehran(), uid))
        set_state(context, None)
        clear_temp(context)
        await update.message.reply_text("به صفحه اصلی خوش آمدید.", reply_markup=main_menu_kb(uid))
        return
        
    await ensure_registered(update, context)

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if not is_owner(uid):
        return
    set_state(context, None)
    clear_temp(context)
    await update.message.reply_text("پنل مدیریت فعال است:", reply_markup=admin_kb())

# -----------------------------
# Registration flow
# -----------------------------

async def handle_registration(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    uid = update.effective_user.id
    state = user_state(context)
    temp = get_temp(context)

    if state == S_REG_NAME:
        temp["name"] = text
        set_state(context, S_REG_NID)
        await update.message.reply_text("کدملی خود را وارد کنید (باید ۶ رقمی و عدد باشد):", reply_markup=ReplyKeyboardRemove())
        return True

    if state == S_REG_NID:
        if not (text.isdigit() and len(text) == 6):
            await update.message.reply_text("خطا: کدملی باید دقیقاً ۶ رقم و فقط عدد باشد. دوباره وارد کنید:", reply_markup=ReplyKeyboardRemove())
            return True
        temp["national_id"] = text
        set_state(context, S_REG_ACCOUNT)
        await update.message.reply_text("شماره حساب بانکی خود را وارد کنید (باید ۶ رقمی و عدد باشد):", reply_markup=ReplyKeyboardRemove())
        return True

    if state == S_REG_ACCOUNT:
        if not (text.isdigit() and len(text) == 6):
            await update.message.reply_text("خطا: شماره حساب باید دقیقاً ۶ رقم و فقط عدد باشد. دوباره وارد کنید:", reply_markup=ReplyKeyboardRemove())
            return True
            
        temp["bank_account"] = text
        now = now_tehran()
        db_exec(
            """
            INSERT INTO users(telegram_id, username, name, national_id, bank_account, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username=excluded.username,
                name=excluded.name,
                national_id=excluded.national_id,
                bank_account=excluded.bank_account,
                updated_at=excluded.updated_at
            """,
            (
                uid,
                update.effective_user.username or "",
                temp.get("name", ""),
                temp.get("national_id", ""),
                temp.get("bank_account", ""),
                now,
                now,
            ),
        )
        log_action(uid, "registered", f"name={temp.get('name','')}, nid={temp.get('national_id','')}, acc={temp.get('bank_account','')}")
        set_state(context, None)
        clear_temp(context)
        await update.message.reply_text("ثبت نام شما با موفقیت انجام شد.", reply_markup=main_menu_kb(uid))
        return True

    return False

# -----------------------------
# Core Features
# -----------------------------

async def handle_add_product(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    uid = update.effective_user.id
    state = user_state(context)
    temp = get_temp(context)

    if state == S_ADD_NAME:
        temp["name"] = text
        set_state(context, S_ADD_PRICE)
        await update.message.reply_text("مبلغ محصول را وارد کن:", reply_markup=cancel_kb())
        return True

    if state == S_ADD_PRICE:
        if not text.isdigit():
            await update.message.reply_text("لطفا مبلغ را به صورت عدد وارد کن:", reply_markup=cancel_kb())
            return True
        temp["price"] = int(text)
        set_state(context, S_ADD_LINK)
        await update.message.reply_text("لینک پست محصول را بفرست:", reply_markup=cancel_kb())
        return True

    if state == S_ADD_LINK:
        temp["link"] = text
        seller_name = get_user_display(uid)
        preview = (f"{seller_name}، آیا مطمئنی که میخوای محصول {temp.get('name')} رو با مبلغ {fmt_money(temp.get('price'))} رو با لینک پست {temp.get('link')} رو به ویترین فروشت اضافه کنی؟")
        await update.message.reply_text(preview, reply_markup=confirm_kb("add_confirm_yes", "cancel_action"))
        return True

    return False

async def verify_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE, message) -> None:
    uid = update.effective_user.id
    temp = get_temp(context)
    tx_code = temp.get("transaction_code")
    
    if not tx_code:
        return

    pending = db_one("SELECT * FROM pending_buys WHERE buyer_id = ?", (uid,))
    if not pending:
        set_state(context, None)
        return

    # Check forward origin
    is_bank_bot = False
    origin = getattr(message, "forward_origin", None)
    if origin and getattr(origin, "sender_user", None):
        if (origin.sender_user.username or "").strip().lstrip("@").lower() == BANK_BOT_USERNAME:
            is_bank_bot = True
            
    for attr in ("forward_from", "forward_from_user"):
        user = getattr(message, attr, None)
        if user and getattr(user, "username", None):
            if user.username.strip().lstrip("@").lower() == BANK_BOT_USERNAME:
                is_bank_bot = True

    if not is_bank_bot:
        log_action(uid, "receipt_failed_origin", "Forward was not from bank bot.")
        await update.message.reply_text("فاکتور ارسالی باید مستقیماً از ربات بانک فوروارد شده باشد. مجدداً فوروارد کنید یا لغو کنید:", reply_markup=cancel_kb())
        return

    text_content = "\n".join(p for p in [message.text, message.caption] if p)
    log_action(uid, "receipt_sent", f"text={text_content}")
    
    # Check transaction code
    if tx_code not in text_content:
        log_action(uid, "receipt_failed_code", "12-char code not in receipt.")
        await update.message.reply_text("کد ۱۲ رقمی گفته شده، در فاکتور و رسید بانکی شما مشاهده نشد. فاکتور معتبر ارسال کنید یا لغو کنید:", reply_markup=cancel_kb())
        return

    # Check seller account
    seller_account = (pending["seller_account"] or "").strip()
    if seller_account and seller_account not in text_content:
        log_action(uid, "receipt_failed_account", "Seller account not in receipt.")
        await update.message.reply_text("فاکتور نامعتبره. شماره حساب فروشنده در رسید یافت نشد.", reply_markup=cancel_kb())
        return

    # Check amount
    expected_price = int(pending["price"])
    # Pattern to find amount: "مبلغ: 4 ART" or "💰 مبلغ: 4 ART"
    import re
    amount_pattern = r"مبلغ:\s*([\d,]+)\s*ART"
    match = re.search(amount_pattern, text_content)
    receipt_amount = None
    if match:
        amount_str = match.group(1).replace(',', '')
        try:
            receipt_amount = int(amount_str)
        except ValueError:
            receipt_amount = None

    if receipt_amount is None or receipt_amount != expected_price:
        log_action(uid, "receipt_failed_amount", f"expected={expected_price}, got={receipt_amount}")
        await update.message.reply_text(
            f"مبلغ درج شده در رسید ({receipt_amount if receipt_amount is not None else 'نامشخص'} ART) با مبلغ محصول ({expected_price} ART) مطابقت ندارد. لطفاً رسید صحیح را ارسال کنید.",
            reply_markup=cancel_kb()
        )
        return

    product = db_one("SELECT * FROM products WHERE code = ?", (pending["product_code"],))
    seller = get_user(int(pending["seller_id"]))
    buyer = get_user(uid)
    purchased_at = now_tehran()
    
    db_exec(
        """
        INSERT INTO purchases(buyer_id, seller_id, product_code, product_name, price, seller_account, transaction_code, receipt_text, purchased_at)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (uid, int(pending["seller_id"]), pending["product_code"], product["name"], int(pending["price"]), seller_account, tx_code, text_content, purchased_at)
    )
    db_exec(
        "UPDATE products SET status = 'sold', sold_to = ?, sold_at = ?, updated_at = ? WHERE code = ?",
        (uid, purchased_at, purchased_at, product["code"])
    )
    db_exec("DELETE FROM pending_buys WHERE buyer_id = ?", (uid,))
    
    await update.message.reply_text("اوکیه و این محصول توسط شما خریداری شد و به لیست دارایی هایتان اضافه شد.", reply_markup=main_menu_kb(uid))
    log_action(uid, "purchase_success", f"code={product['code']}")

    # Notify Seller exactly as requested
    buyer_name = buyer["name"] if buyer else str(uid)
    buyer_acc = buyer["bank_account"] if buyer else "ثبت نشده"
    notify_text = (f"محصول {product['name']} شما با کد یکتای {product['code']}، توسط {buyer_name} و با شماره حساب {buyer_acc} و در تاریخ و ساعت {purchased_at}، خرید کرده است.\n\n"
                   f"درصورت نیامدن پول به حساب شما، می‌توانید شکایت خود را به دادگاه عدالت کملوت ثبت کنید.")
    
    try:
        await context.bot.send_message(chat_id=int(seller["telegram_id"]), text=notify_text)
    except Exception as e:
        logger.error(f"Failed to notify seller: {e}")
        
    set_state(context, None)
    clear_temp(context)

# ==================== Backup/Restore Handlers ====================

async def admin_backup_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show backup/restore menu in admin panel."""
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    if not is_owner(uid):
        await query.edit_message_text("⛔ دسترسی ندارید.")
        return
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 گرفتن پشتیبان", callback_data="admin_backup_export")],
        [InlineKeyboardButton("📤 بازیابی از پشتیبان", callback_data="admin_backup_import")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back")]
    ])
    await query.edit_message_text(
        "💾 **پشتیبان‌گیری و بازیابی**\n\n"
        "• **گرفتن پشتیبان:** یک فایل JSON کامل از تمام اطلاعات بانک تهیه می‌شود.\n"
        "• **بازیابی:** با ارسال فایل پشتیبان، اطلاعات قبلی بازگردانده می‌شود.\n\n"
        "⚠️ **هشدار:** بازیابی تمام اطلاعات فعلی را بازنویسی می‌کند!",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )

async def admin_backup_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Export backup and send as file."""
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    if not is_owner(uid):
        await query.edit_message_text("⛔ دسترسی ندارید.")
        return
    
    await query.edit_message_text("📥 در حال تهیه پشتیبان... لطفاً صبر کنید.", parse_mode='Markdown')
    try:
        json_data = export_full_backup()
        file_obj = io.BytesIO(json_data.encode('utf-8'))
        file_obj.name = f"camelot_market_backup_{datetime.now(TEHRAN).strftime('%Y%m%d_%H%M%S')}.json"
        await context.bot.send_document(
            chat_id=uid,
            document=file_obj,
            caption="💾 **پشتیبان ثبت‌اسناد کملوت**\n\n"
                    f"🕐 تاریخ: {now_tehran()}\n"
                    "📌 این فایل شامل تمام اطلاعات است.\n"
                    "برای بازیابی، از بخش «بازیابی از پشتیبان» استفاده کنید.",
            parse_mode='Markdown'
        )
        log_action(uid, "admin_backup_export", "Backup exported")
        await query.edit_message_text(
            "✅ **پشتیبان با موفقیت تهیه و ارسال شد.**\n\n"
            "فایل JSON را در جای امن نگهداری کنید.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به پنل", callback_data="admin_back")]]),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Error exporting backup: {e}")
        await query.edit_message_text(
            f"❌ خطا در تهیه پشتیبان: {str(e)}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back")]])
        )

async def admin_backup_import_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start import process: ask for file."""
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    if not is_owner(uid):
        await query.edit_message_text("⛔ دسترسی ندارید.")
        return ConversationHandler.END
    
    await query.edit_message_text(
        "📤 **بازیابی از پشتیبان**\n\n"
        "⚠️ **هشدار مهم:**\n"
        "• این عملیات **تمام اطلاعات فعلی** را بازنویسی می‌کند.\n"
        "• قبل از ادامه، حتماً یک پشتیبان جدید بگیرید.\n"
        "• فقط فایل‌های JSON معتبر که توسط ربات تولید شده‌اند قابل قبول هستند.\n\n"
        "لطفاً فایل پشتیبان (JSON) را ارسال کنید.\n"
        "(برای لغو /cancel بزنید)",
        parse_mode='Markdown'
    )
    return S_ADMIN_BACKUP_IMPORT_FILE

async def admin_backup_import_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive the file and ask for confirmation."""
    uid = update.effective_user.id
    if not is_owner(uid):
        await update.message.reply_text("⛔ دسترسی ندارید.")
        return ConversationHandler.END
    
    document = update.message.document
    if not document:
        await update.message.reply_text(
            "❌ لطفاً یک فایل ارسال کنید.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back")]])
        )
        return S_ADMIN_BACKUP_IMPORT_FILE
    
    if not document.file_name.endswith('.json'):
        await update.message.reply_text(
            "❌ فقط فایل‌های JSON معتبر هستند.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back")]])
        )
        return S_ADMIN_BACKUP_IMPORT_FILE
    
    await update.message.reply_text("📥 در حال دریافت فایل...", parse_mode='Markdown')
    try:
        file = await context.bot.get_file(document.file_id)
        file_content = await file.download_as_bytearray()
        json_data = file_content.decode('utf-8')
        context.user_data['backup_json_data'] = json_data
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ بله، بازیابی کن", callback_data="admin_backup_import_confirm")],
            [InlineKeyboardButton("❌ لغو", callback_data="admin_back")]
        ])
        await update.message.reply_text(
            "⚠️ **تأیید نهایی بازیابی**\n\n"
            "آیا از بازنویسی کامل اطلاعات مطمئن هستید؟\n"
            "این عملیات قابل بازگشت نیست!",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        return S_ADMIN_BACKUP_CONFIRM
    except Exception as e:
        logger.error(f"Error receiving backup file: {e}")
        await update.message.reply_text(
            f"❌ خطا در دریافت فایل: {str(e)}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back")]])
        )
        context.user_data.pop('backup_json_data', None)
        return ConversationHandler.END

async def admin_backup_import_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute the import."""
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    if not is_owner(uid):
        await query.edit_message_text("⛔ دسترسی ندارید.")
        return
    
    json_data = context.user_data.get('backup_json_data')
    if not json_data:
        await query.edit_message_text("❌ خطا: داده‌های پشتیبان یافت نشد.")
        return
    
    await query.edit_message_text("🔄 در حال بازیابی اطلاعات... لطفاً صبر کنید.", parse_mode='Markdown')
    try:
        success, message = import_full_backup(json_data)
        if success:
            log_action(uid, "admin_backup_import", "Restore successful")
            await query.edit_message_text(
                "✅ **بازیابی با موفقیت انجام شد!**\n\n"
                "تمام اطلاعات به نسخه پشتیبان بازگردانده شد.\n"
                "لطفاً ربات را ری‌استارت کنید تا تغییرات اعمال شوند.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به پنل", callback_data="admin_back")]]),
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(
                f"❌ **خطا در بازیابی:**\n{message}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back")]])
            )
    except Exception as e:
        logger.error(f"Error during restore: {e}")
        await query.edit_message_text(
            f"❌ خطا در بازیابی: {str(e)}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back")]])
        )
    context.user_data.pop('backup_json_data', None)

# ==================== Restore Account for Owner ====================

async def restore_account_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start restore account flow (only for owner)."""
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    if not is_owner(uid):
        await query.edit_message_text("⛔ دسترسی ندارید.")
        return ConversationHandler.END
    
    await query.edit_message_text(
        "📤 **بازیابی اطلاعات از فایل بکاپ**\n\n"
        "⚠️ **هشدار مهم:**\n"
        "• این عملیات **تمام اطلاعات فعلی** بانک را بازنویسی می‌کند.\n"
        "• فقط فایل‌های JSON معتبر که توسط ربات تولید شده‌اند قابل قبول هستند.\n\n"
        "لطفاً فایل بکاپ (JSON) را ارسال کنید.\n"
        "(برای لغو /cancel بزنید)",
        parse_mode='Markdown'
    )
    return S_RESTORE_ACCOUNT_FILE

async def restore_account_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive file for restore account."""
    uid = update.effective_user.id
    if not is_owner(uid):
        await update.message.reply_text("⛔ دسترسی ندارید.")
        return ConversationHandler.END
    
    document = update.message.document
    if not document:
        await update.message.reply_text(
            "❌ لطفاً یک فایل ارسال کنید.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="cancel_action")]])
        )
        return S_RESTORE_ACCOUNT_FILE
    
    if not document.file_name.endswith('.json'):
        await update.message.reply_text(
            "❌ فقط فایل‌های JSON معتبر هستند.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="cancel_action")]])
        )
        return S_RESTORE_ACCOUNT_FILE
    
    await update.message.reply_text("📥 در حال دریافت و بررسی فایل... لطفاً صبر کنید.", parse_mode='Markdown')
    try:
        file = await context.bot.get_file(document.file_id)
        file_content = await file.download_as_bytearray()
        json_data = file_content.decode('utf-8')
        context.user_data['backup_json_data'] = json_data
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ بله، بازیابی کن", callback_data="restore_account_confirm")],
            [InlineKeyboardButton("❌ لغو", callback_data="cancel_action")]
        ])
        await update.message.reply_text(
            "⚠️ **تأیید نهایی بازیابی**\n\n"
            "آیا از بازنویسی کامل اطلاعات مطمئن هستید؟\n"
            "این عملیات قابل بازگشت نیست!",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        return S_RESTORE_ACCOUNT_CONFIRM
    except Exception as e:
        logger.error(f"Error receiving restore file: {e}")
        await update.message.reply_text(
            f"❌ خطا در دریافت فایل: {str(e)}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="cancel_action")]])
        )
        context.user_data.pop('backup_json_data', None)
        return ConversationHandler.END

async def restore_account_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute restore for owner."""
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    if not is_owner(uid):
        await query.edit_message_text("⛔ دسترسی ندارید.")
        return
    
    json_data = context.user_data.get('backup_json_data')
    if not json_data:
        await query.edit_message_text("❌ خطا: داده‌های پشتیبان یافت نشد.")
        return
    
    await query.edit_message_text("🔄 در حال بازیابی اطلاعات... لطفاً صبر کنید.", parse_mode='Markdown')
    try:
        success, message = import_full_backup(json_data)
        if success:
            log_action(uid, "restore_account", "Restore successful via start")
            await query.edit_message_text(
                "✅ **بازیابی با موفقیت انجام شد!**\n\n"
                "تمام اطلاعات به نسخه پشتیبان بازگردانده شد.\n"
                "لطفاً دوباره /start بزنید.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 شروع مجدد", callback_data="restart_bot")]]),
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(
                f"❌ **خطا در بازیابی:**\n{message}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="cancel_action")]])
            )
    except Exception as e:
        logger.error(f"Error during restore account: {e}")
        await query.edit_message_text(
            f"❌ خطا در بازیابی: {str(e)}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="cancel_action")]])
        )
    context.user_data.pop('backup_json_data', None)

async def restart_bot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Restart bot placeholder."""
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    if not is_owner(uid):
        await query.edit_message_text("⛔ دسترسی ندارید.")
        return
    await query.edit_message_text(
        "🔄 **ربات در حال ری‌استارت است...**\n\n"
        "لطفاً چند ثانیه صبر کنید و سپس دوباره /start بزنید.",
        parse_mode='Markdown'
    )
    # In real scenario, you might trigger a restart mechanism.
    # For now, just inform user.

# -----------------------------
# Handlers
# -----------------------------

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not await check_access(update, context):
        return

    uid = update.effective_user.id
    data = query.data or ""

    if data == "cancel_action":
        log_action(uid, "cancelled_action", f"State was {user_state(context)}")
        set_state(context, None)
        clear_temp(context)
        await query.message.delete()
        await context.bot.send_message(chat_id=uid, text="عملیات لغو شد. به منوی اصلی بازگشتید.", reply_markup=main_menu_kb(uid))
        return

    if data == "add_confirm_yes":
        temp = get_temp(context)
        code = unique_product_code()
        db_exec(
            "INSERT INTO products(code, seller_id, name, price, link, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (code, uid, temp["name"], int(temp["price"]), temp["link"], now_tehran(), now_tehran())
        )
        log_action(uid, "product_added", f"code={code}")
        set_state(context, None)
        await query.edit_message_text(f"محصول با موفقیت اضافه شد.\n\nکد یکتا: {code}\nنام: {temp['name']}")
        await context.bot.send_message(chat_id=uid, text="به صفحه اصلی بازگشتید.", reply_markup=main_menu_kb(uid))
        return

    if data == "buy_confirm_yes":
        temp = get_temp(context)
        product_code = temp.get("buy_product_code")
        if not product_code:
            await query.edit_message_text("خطا: کد محصول یافت نشد. دوباره تلاش کنید.")
            return

        product = db_one("SELECT * FROM products WHERE code = ?", (product_code,))
        if not product or product["status"] != "active":
            await query.edit_message_text("این محصول دیگر موجود نیست.")
            return

        seller = get_user(int(product["seller_id"]))
        if not seller:
            await query.edit_message_text("فروشنده این محصول در سیستم یافت نشد.")
            return

        seller_account = seller["bank_account"] or ""
        tx_code = unique_transaction_code()
        
        db_exec("DELETE FROM pending_buys WHERE buyer_id = ?", (uid,))
        db_exec(
            "INSERT INTO pending_buys(buyer_id, product_code, seller_id, seller_account, price, transaction_code, created_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (uid, product_code, int(product["seller_id"]), seller_account, int(product["price"]), tx_code, now_tehran())
        )
        
        temp["transaction_code"] = tx_code
        set_state(context, S_BUY_RECEIPT)
        log_action(uid, "buy_initiated", f"code={product_code}, tx={tx_code}")

        msg = (
            f"✅ مرحله بعد:\n\n"
            f"۱. به ربات بانک (@{BANK_BOT_USERNAME}) بروید.\n"
            f"۲. مبلغ {fmt_money(int(product['price']))} تومان را به شماره حساب زیر واریز کنید:\n"
            f"<code>{seller_account}</code>\n\n"
            f"⚠️ <b>حتماً حتماً</b> در بخش توضیحات انتقال وجه، کد ۱۲ کاراکتری زیر را وارد کنید:\n"
            f"<code>{tx_code}</code>\n\n"
            f"اگر این کد را وارد نکنید، پول شما گم می‌شود و قابل پیگیری نخواهد بود.\n\n"
            f"۳. پس از انجام انتقال، <b>فاکتور (رسید)</b> را از ربات بانک به <b>همین گفتگو</b> فوروارد کنید.\n"
            f"توجه: فاکتور باید مستقیماً از ربات بانک فوروارد شده باشد تا معتبر باشد.\n\n"
            f"برای لغو عملیات، دکمه زیر را بزنید."
        )

        try:
            await context.bot.send_message(
                chat_id=uid,
                text=msg,
                parse_mode=ParseMode.HTML,
                reply_markup=cancel_kb()
            )
            await query.message.delete()
        except Exception as e:
            logger.error(f"Error sending purchase instruction: {e}")
            await query.edit_message_text(
                "خطا در ارسال دستورالعمل خرید. لطفاً دوباره تلاش کنید یا با پشتیبانی تماس بگیرید.",
                reply_markup=confirm_kb("buy_confirm_yes", "cancel_action")
            )
        return

    # Delete Product
    if data.startswith("delete_product:"):
        code = data.split(":")[1]
        db_exec("DELETE FROM products WHERE code = ?", (code,))
        log_action(uid, "product_deleted", f"code={code}")
        await query.edit_message_text("محصول با موفقیت حذف شد.")
        return

    # Edit Product
    if data.startswith("edit_product:"):
        code = data.split(":")[1]
        temp = get_temp(context)
        temp["edit_product_code"] = code
        
        await query.edit_message_text(
            "کدام بخش را میخواهید ویرایش کنید؟",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("نام", callback_data=f"edit_field:name:{code}")],
                [InlineKeyboardButton("مبلغ", callback_data=f"edit_field:price:{code}")],
                [InlineKeyboardButton("لینک", callback_data=f"edit_field:link:{code}")],
                [InlineKeyboardButton("❌ لغو", callback_data="cancel_action")]
            ])
        )
        return

    if data.startswith("edit_field:"):
        _, field, code = data.split(":")
        temp = get_temp(context)
        temp["edit_product_code"] = code
        temp["edit_field"] = field
        set_state(context, S_EDIT_PRODUCT_VALUE)
        await query.edit_message_text("مقدار جدید را وارد کنید:", reply_markup=cancel_kb())
        return

    # Admin callbacks
    if data == "admin_users":
        rows = db_all("SELECT * FROM users ORDER BY created_at DESC")
        text = "\n".join(f"ID: {r['telegram_id']} | نام: {r['name']} | کدملی: {r['national_id']} | حساب: {r['bank_account']} | یوزرنیم: @{r['username']}" for r in rows)
        for chunk in safe_send_chunks(text or "هیچ کاربری نیست."):
            await query.message.reply_text(chunk)
        return

    if data == "admin_manage_user":
        set_state(context, S_ADMIN_GET_USER_ID)
        await query.edit_message_text("آیدی عددی تلگرامی کاربر را وارد کن:", reply_markup=cancel_kb())
        return

    if data == "admin_blacklist":
        await query.edit_message_text(
            "لیست سیاه:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("افزودن", callback_data="bl_add"), InlineKeyboardButton("خروج از لیست سیاه", callback_data="bl_rem")],
                [InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_back")]
            ])
        )
        return
        
    if data == "bl_add":
        set_state(context, S_ADMIN_BLACKLIST_ADD)
        await query.edit_message_text("آیدی عددی را بفرست:", reply_markup=cancel_kb())
        return
        
    if data == "bl_rem":
        set_state(context, S_ADMIN_BLACKLIST_REMOVE)
        await query.edit_message_text("آیدی عددی برای خروج از لیست سیاه را بفرست:", reply_markup=cancel_kb())
        return

    if data == "admin_logs":
        rows = db_all("SELECT * FROM logs ORDER BY id DESC LIMIT 50")
        text = "\n".join(f"[{r['created_at']}] ID: {r['telegram_id']} | Action: {r['action']} | Det: {r['details']}" for r in rows)
        for chunk in safe_send_chunks(text or "لاگی وجود ندارد."):
            await query.message.reply_text(chunk)
        return

    if data == "admin_toggle_bot":
        new_status = "off" if bot_is_on() else "on"
        set_bot_status(new_status)
        log_action(uid, "toggled_bot", f"status={new_status}")
        await query.edit_message_text(f"وضعیت ربات: {new_status}", reply_markup=admin_kb())
        return

    if data == "admin_back":
        # Return to admin panel
        await query.edit_message_text("پنل مدیریت:", reply_markup=admin_kb())
        return

    # Admin Backup
    if data == "admin_backup":
        await admin_backup_menu(update, context)
        return

    if data == "admin_backup_export":
        await admin_backup_export(update, context)
        return

    if data == "admin_backup_import":
        # This is a callback, but we need to start conversation; we'll handle via separate handler
        await admin_backup_import_start(update, context)
        return

    if data == "admin_backup_import_confirm":
        await admin_backup_import_confirm(update, context)
        return

    # Restore account (from registration)
    if data == "restore_account":
        await restore_account_start(update, context)
        return

    if data == "restore_account_confirm":
        await restore_account_confirm(update, context)
        return

    if data == "restart_bot":
        await restart_bot_callback(update, context)
        return

    # Admin User Edit fields
    if data.startswith("admin_user_name:"):
        get_temp(context)["admin_target_user_id"] = int(data.split(":")[1])
        get_temp(context)["admin_target_field"] = "name"
        set_state(context, S_ADMIN_EDIT_USER_VALUE)
        await query.edit_message_text("نام جدید کملوتی را وارد کن:", reply_markup=cancel_kb())
        return
    if data.startswith("admin_user_account:"):
        get_temp(context)["admin_target_user_id"] = int(data.split(":")[1])
        get_temp(context)["admin_target_field"] = "bank_account"
        set_state(context, S_ADMIN_EDIT_USER_VALUE)
        await query.edit_message_text("شماره حساب جدید را وارد کن:", reply_markup=cancel_kb())
        return
    if data.startswith("admin_user_username:"):
        get_temp(context)["admin_target_user_id"] = int(data.split(":")[1])
        get_temp(context)["admin_target_field"] = "username"
        set_state(context, S_ADMIN_EDIT_USER_VALUE)
        await query.edit_message_text("یوزرنیم جدید را وارد کن:", reply_markup=cancel_kb())
        return

    # Register new (from start)
    if data == "register_new":
        # Start registration flow
        context.user_data.clear()
        context.user_data['register_step'] = S_REG_NAME
        context.user_data['username'] = update.effective_user.username or ""
        await query.edit_message_text(
            "📝 **ثبت‌نام جدید**\n\n"
            "لطفاً نام کملوتی خود را وارد کنید:\n"
            "(برای لغو /cancel بزنید)",
            parse_mode='Markdown'
        )
        # We need to set state to registration; but since it's a callback, we'll handle in handle_message?
        # Better to set state and reply with text; then next message will be handled by handle_message.
        set_state(context, S_REG_NAME)
        clear_temp(context)
        return

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not await check_access(update, context):
        return

    uid = update.effective_user.id
    text = normalize_text(update.message.text or update.message.caption)

    # Check if we are in admin backup import file state (conversation)
    state = user_state(context)
    if state == S_ADMIN_BACKUP_IMPORT_FILE:
        # This is handled by the conversation handler, but we also need to catch document messages
        # Actually we'll rely on the conversation handler; but we can handle text messages for cancellation
        if text in ["لغو", "بازگشت"]:
            set_state(context, None)
            clear_temp(context)
            await update.message.reply_text("❌ عملیات لغو شد.", reply_markup=main_menu_kb(uid))
            return
        # Otherwise, user should send a file; we'll let the conversation handler catch it.
        return

    if state == S_RESTORE_ACCOUNT_FILE:
        # Similar, handled by conversation handler
        if text in ["لغو", "بازگشت"]:
            set_state(context, None)
            clear_temp(context)
            await update.message.reply_text("❌ عملیات لغو شد.", reply_markup=main_menu_kb(uid))
            return
        return

    if text in ["لغو", "بازگشت"]:
        log_action(uid, "cancelled_action_text", f"State was {user_state(context)}")
        set_state(context, None)
        clear_temp(context)
        await update.message.reply_text("عملیات لغو شد. به منوی اصلی بازگشتید.", reply_markup=main_menu_kb(uid))
        return

    # Auth Flow Check
    if not get_user(uid):
        if await handle_registration(update, context, text):
            return
        return

    # Handle Active States
    if state:
        if state in {S_REG_NAME, S_REG_NID, S_REG_ACCOUNT}:
            if await handle_registration(update, context, text): return
            
        if state in {S_ADD_NAME, S_ADD_PRICE, S_ADD_LINK}:
            if await handle_add_product(update, context, text): return
            
        if state == S_BUY_CODE:
            product = db_one("SELECT * FROM products WHERE code = ?", (text,))
            if not product:
                await update.message.reply_text("محصولی با این کد پیدا نشد.", reply_markup=cancel_kb())
                return
            if product["status"] != "active":
                await update.message.reply_text("این محصول قبلاً فروخته شده است.", reply_markup=cancel_kb())
                return
                
            get_temp(context)["buy_product_code"] = product["code"]
            msg = f"محصول {product['name']} با قیمت {fmt_money(int(product['price']))} و لینک پست {product['link']} رو میخوای بخری؟"
            await update.message.reply_text(msg, reply_markup=confirm_kb("buy_confirm_yes", "cancel_action"))
            return
            
        if state == S_BUY_RECEIPT:
            await verify_receipt(update, context, update.message)
            return
            
        if state == S_EDIT_PRODUCT_VALUE:
            temp = get_temp(context)
            field = temp.get("edit_field")
            code = temp.get("edit_product_code")
            if field == "price":
                if not text.isdigit():
                    await update.message.reply_text("باید عدد وارد کنید:")
                    return
                db_exec(f"UPDATE products SET {field} = ? WHERE code = ?", (int(text), code))
            else:
                db_exec(f"UPDATE products SET {field} = ? WHERE code = ?", (text, code))
            log_action(uid, "product_edited", f"code={code}, field={field}, val={text}")
            set_state(context, None)
            await update.message.reply_text("ویرایش انجام شد.", reply_markup=main_menu_kb(uid))
            return

        # Admin States
        if state == S_ADMIN_GET_USER_ID:
            row = get_user(int(text))
            if not row:
                await update.message.reply_text("کاربر پیدا نشد.", reply_markup=cancel_kb())
                return
            info = f"آیدی: {row['telegram_id']}\nنام: {row['name']}\nکدملی: {row['national_id']}\nحساب: {row['bank_account']}"
            await update.message.reply_text(info, reply_markup=admin_user_actions_kb(int(text)))
            set_state(context, None)
            return

        if state == S_ADMIN_EDIT_USER_VALUE:
            temp = get_temp(context)
            target = temp["admin_target_user_id"]
            field = temp["admin_target_field"]
            db_exec(f"UPDATE users SET {field} = ? WHERE telegram_id = ?", (text, target))
            log_action(uid, "admin_edit_user", f"target={target}, field={field}, val={text}")
            set_state(context, None)
            await update.message.reply_text("اطلاعات کاربر آپدیت شد.")
            return
            
        if state == S_ADMIN_BLACKLIST_ADD:
            db_exec("INSERT OR REPLACE INTO blacklist(telegram_id, reason, added_at) VALUES(?, ?, ?)", (int(text), "Manual Add", now_tehran()))
            log_action(uid, "admin_blacklist_add", f"target={text}")
            set_state(context, None)
            await update.message.reply_text("به لیست سیاه افزوده شد.")
            return
            
        if state == S_ADMIN_BLACKLIST_REMOVE:
            db_exec("DELETE FROM blacklist WHERE telegram_id = ?", (int(text),))
            log_action(uid, "admin_blacklist_remove", f"target={text}")
            set_state(context, None)
            await update.message.reply_text("از لیست سیاه حذف شد.")
            return

        # These states are handled by conversation handlers, but in case of text we ignore
        if state in (S_ADMIN_BACKUP_IMPORT_FILE, S_RESTORE_ACCOUNT_FILE):
            # Already handled above
            return

    # Handle Main Menu Buttons
    if text == BTN_ADD:
        set_state(context, S_ADD_NAME)
        clear_temp(context)
        await update.message.reply_text("نام محصول را وارد کن:", reply_markup=cancel_kb())
        return
        
    if text == BTN_BUY:
        set_state(context, S_BUY_CODE)
        clear_temp(context)
        await update.message.reply_text("کدیکتای محصول رو وارد کنید:", reply_markup=cancel_kb())
        return
        
    if text == BTN_VITRINE:
        rows = db_all("SELECT * FROM products WHERE status = 'active' AND seller_id = ? ORDER BY created_at DESC", (uid,))
        if not rows:
            await update.message.reply_text("ویترین شما خالی است. فقط محصولاتی که شما اضافه کرده‌اید اینجا نمایش داده می‌شوند.")
            return
        for r in rows:
            seller = get_user(r["seller_id"])
            seller_name = seller["name"] if seller else str(r["seller_id"])
            msg = f"کد: {r['code']}\nنام: {r['name']}\nقیمت: {fmt_money(int(r['price']))} تومان\nلینک: {r['link']}\nفروشنده: {seller_name}"
            await update.message.reply_text(msg, reply_markup=product_actions_kb(r["code"], r["seller_id"], uid))
        return
        
    if text == BTN_ASSETS:
        rows = db_all("SELECT p.* FROM purchases pu JOIN products p ON p.code = pu.product_code WHERE pu.buyer_id = ? ORDER BY pu.purchased_at DESC", (uid,))
        if not rows:
            await update.message.reply_text("دارایی برای شما یافت نشد.")
            return
        for r in rows:
            msg = f"کد: {r['code']}\nنام: {r['name']}\nقیمت: {fmt_money(int(r['price']))}\nلینک: {r['link']}"
            await update.message.reply_text(msg)
        return
        
    if text == BTN_ADMIN and is_owner(uid):
        await admin_cmd(update, context)
        return

    await update.message.reply_text("لطفاً یک گزینه انتخاب کنید:", reply_markup=main_menu_kb(uid))

# ==================== Conversation Handlers ====================

# Admin Backup Import Conversation
admin_backup_import_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(admin_backup_import_start, pattern="^admin_backup_import$")],
    states={
        S_ADMIN_BACKUP_IMPORT_FILE: [MessageHandler(filters.Document.ALL, admin_backup_import_file)],
        S_ADMIN_BACKUP_CONFIRM: [CallbackQueryHandler(admin_backup_import_confirm, pattern="^admin_backup_import_confirm$")],
    },
    fallbacks=[CommandHandler("start", start), CommandHandler("cancel", cancel)],
)

# Restore Account Conversation (for owner during registration)
restore_account_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(restore_account_start, pattern="^restore_account$")],
    states={
        S_RESTORE_ACCOUNT_FILE: [MessageHandler(filters.Document.ALL, restore_account_file)],
        S_RESTORE_ACCOUNT_CONFIRM: [CallbackQueryHandler(restore_account_confirm, pattern="^restore_account_confirm$")],
    },
    fallbacks=[CommandHandler("start", start), CommandHandler("cancel", cancel)],
)

# ==================== Main ====================

def main() -> None:
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("cancel", cancel))
    
    # Callback handlers
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    # Message handler (for all non-command messages)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))
    
    # Conversation handlers for backup/restore
    app.add_handler(admin_backup_import_conv)
    app.add_handler(restore_account_conv)
    
    logger.info("Bot started.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()