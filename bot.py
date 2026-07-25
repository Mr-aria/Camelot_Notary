from pathlib import Path
code = r'''# -*- coding: utf-8 -*-
"""Camelot Telegram Marketplace Bot

Features:
- Registration with Persian name, national ID, bank account
- Product marketplace: add / edit / delete / browse
- Buy flow with unique 12-char transaction code
- Receipt verification from forwarded bank bot message
- Owner admin panel: users, user management, blacklist, bot status, logs
- Persistent SQLite storage

Requirements:
- python-telegram-bot>=20

Set environment variables on Railway:
- BOT_TOKEN   (required)
- OWNER_ID    (optional, defaults to 1275490079)
- BANK_BOT_USERNAME (optional, defaults to CamelotBank_bot)
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
import random
import secrets
import sqlite3
import string
import threading
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, List, Tuple, Dict, Any

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
)

# -----------------------------
# Configuration
# -----------------------------

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "1275490079"))
BANK_BOT_USERNAME = os.getenv("BANK_BOT_USERNAME", "CamelotBank_bot").strip().lstrip("@").lower()
TEHRAN = ZoneInfo("Asia/Tehran")
DB_PATH = os.getenv("DB_PATH", "bot.db")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required")

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
BTN_CANCEL = "❌ لغو"
BTN_BACK = "⬅️ بازگشت"
BTN_YES = "✅ آره"
BTN_NO = "❌ نه"

S_REG_NAME = "reg_name"
S_REG_NID = "reg_nid"
S_REG_ACCOUNT = "reg_account"

S_ADD_NAME = "add_name"
S_ADD_PRICE = "add_price"
S_ADD_LINK = "add_link"
S_ADD_CONFIRM = "add_confirm"

S_BUY_CODE = "buy_code"
S_BUY_RECEIPT = "buy_receipt"

S_EDIT_PRODUCT_FIELD = "edit_product_field"
S_EDIT_PRODUCT_VALUE = "edit_product_value"

S_ADMIN_GET_USER_ID = "admin_get_user_id"
S_ADMIN_EDIT_USER_FIELD = "admin_edit_user_field"
S_ADMIN_EDIT_USER_VALUE = "admin_edit_user_value"
S_ADMIN_BLACKLIST_ADD = "admin_blacklist_add"
S_ADMIN_BLACKLIST_REMOVE = "admin_blacklist_remove"

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
        _db.execute("PRAGMA synchronous=NORMAL;")
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


def is_digit_str(s: str) -> bool:
    return s.isdigit()


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


def get_setting(key: str, default: str = "") -> str:
    row = db_one("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    db_exec("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def bot_is_on() -> bool:
    return get_setting("bot_status", "on") == "on"


def set_bot_status(status: str) -> None:
    set_setting("bot_status", status)


def get_user(uid: int) -> Optional[sqlite3.Row]:
    return db_one("SELECT * FROM users WHERE telegram_id = ?", (uid,))


def get_user_display(uid: int) -> str:
    row = get_user(uid)
    if not row:
        return str(uid)
    return row["name"] or row["username"] or str(uid)


def user_is_blacklisted(uid: int) -> bool:
    return db_one("SELECT 1 FROM blacklist WHERE telegram_id = ?", (uid,)) is not None


def is_owner(uid: int) -> bool:
    return uid == OWNER_ID


def require_owner(uid: int) -> bool:
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


def main_menu_kb() -> ReplyKeyboardMarkup:
    rows = [[BTN_BUY, BTN_ADD], [BTN_ASSETS, BTN_VITRINE]]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(BTN_CANCEL, callback_data="cancel_action")]])


def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("👥 لیست کاربران", callback_data="admin_users")],
            [InlineKeyboardButton("🧑‍💼 مدیریت کاربر", callback_data="admin_manage_user")],
            [InlineKeyboardButton("⛔ لیست سیاه", callback_data="admin_blacklist")],
            [InlineKeyboardButton("📄 ثبت لاگ ها", callback_data="admin_logs")],
            [InlineKeyboardButton("🔌 خاموش/روشن", callback_data="admin_toggle_bot")],
            [InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_back")],
        ]
    )


def confirm_kb(yes_data: str, no_data: str = "cancel_action") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(BTN_YES, callback_data=yes_data),
            InlineKeyboardButton(BTN_NO, callback_data=no_data),
        ]]
    )


def product_actions_kb(code: str, seller_id: int, viewer_id: int) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("🛒 خرید", callback_data=f"buy_product:{code}")]]
    if viewer_id == seller_id or is_owner(viewer_id):
        rows.append([
            InlineKeyboardButton("✏️ ویرایش", callback_data=f"edit_product:{code}"),
            InlineKeyboardButton("🗑 حذف", callback_data=f"delete_product:{code}"),
        ])
    return InlineKeyboardMarkup(rows)


def admin_user_actions_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("تغییر نام کملوتی", callback_data=f"admin_user_name:{uid}")],
            [InlineKeyboardButton("تغییر شماره حساب", callback_data=f"admin_user_account:{uid}")],
            [InlineKeyboardButton("تغییر یوزرنیم", callback_data=f"admin_user_username:{uid}")],
            [InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_back")],
        ]
    )


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


def product_summary(row: sqlite3.Row) -> str:
    seller = get_user(row["seller_id"])
    seller_name = seller["name"] if seller else str(row["seller_id"])
    return (
        f"کد: {row['code']}\n"
        f"نام: {row['name']}\n"
        f"قیمت: {fmt_money(int(row['price']))} تومان\n"
        f"لینک: {row['link']}\n"
        f"فروشنده: {seller_name}\n"
        f"وضعیت: {row['status']}"
    )


def user_summary(row: sqlite3.Row) -> str:
    return (
        f"آیدی: {row['telegram_id']}\n"
        f"یوزرنیم: @{row['username']}" if row["username"] else f"آیدی: {row['telegram_id']}\nیوزرنیم: -"
    )

# -----------------------------
# Access control
# -----------------------------

async def check_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    uid = update.effective_user.id if update.effective_user else None
    if uid is None:
        return False
    if uid == OWNER_ID:
        return True
    if user_is_blacklisted(uid):
        if update.message:
            await update.message.reply_text("⛔ شما مسدود شده‌اید و دسترسی به ربات ندارید.")
        elif update.callback_query:
            await update.callback_query.answer("شما مسدود شده‌اید", show_alert=True)
        return False
    if not bot_is_on():
        if update.message:
            await update.message.reply_text("🔌 ربات خاموش است.")
        elif update.callback_query:
            await update.callback_query.answer("ربات خاموش است", show_alert=True)
        return False
    return True


async def ensure_registered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    uid = update.effective_user.id
    if get_user(uid):
        return True
    set_state(context, S_REG_NAME)
    clear_temp(context)
    await update.message.reply_text(
        "سلام 🌱\nبرای شروع، لطفاً *نام کملوتی* خود را وارد کنید.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardRemove(),
    )
    log_action(uid, "start_registration", "prompted name")
    return False

# -----------------------------
# Messaging helpers
# -----------------------------

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, welcome: bool = False) -> None:
    uid = update.effective_user.id
    row = get_user(uid)
    name = row["name"] if row else update.effective_user.first_name
    text = f"سلام {name} 👋\nبه صفحه اصلی خوش آمدی."
    if welcome:
        text = f"ثبت‌نام شما با موفقیت انجام شد ✅\n{text}"
    if update.message:
        await update.message.reply_text(text, reply_markup=main_menu_kb())
    elif update.callback_query:
        await update.callback_query.edit_message_text(text)
        await update.callback_query.message.reply_text("منوی اصلی:", reply_markup=main_menu_kb())


async def send_admin_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = "پنل مدیریت فعال است. یکی از گزینه‌ها را انتخاب کن:"
    if update.message:
        await update.message.reply_text(text, reply_markup=admin_kb())
    else:
        await update.callback_query.edit_message_text(text, reply_markup=admin_kb())


async def send_vitrine(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    rows = db_all("SELECT * FROM products WHERE status = 'active' ORDER BY created_at DESC")
    if not rows:
        text = "ویترین فعلاً خالی است."
        if update.message:
            await update.message.reply_text(text, reply_markup=main_menu_kb())
        else:
            await update.callback_query.edit_message_text(text)
        return
    for row in rows:
        await update.message.reply_text(product_summary(row), reply_markup=product_actions_kb(row["code"], row["seller_id"], uid))


async def send_my_assets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    rows = db_all(
        """
        SELECT p.* FROM purchases pu
        JOIN products p ON p.code = pu.product_code
        WHERE pu.buyer_id = ?
        ORDER BY pu.purchased_at DESC
        """,
        (uid,),
    )
    if not rows:
        await update.message.reply_text("فعلاً هیچ دارایی خریداری‌شده‌ای نداری.", reply_markup=main_menu_kb())
        return
    for row in rows:
        await update.message.reply_text(
            f"دارایی خریداری‌شده\n\n{product_summary(row)}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت", callback_data="noop")]]),
        )

# -----------------------------
# Start / Cancel / Admin command
# -----------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return
    uid = update.effective_user.id
    username = (update.effective_user.username or "").strip()
    now = now_tehran()
    existing = get_user(uid)
    if existing:
        db_exec("UPDATE users SET username = ?, updated_at = ? WHERE telegram_id = ?", (username, now, uid))
        log_action(uid, "start", "existing user")
        set_state(context, None)
        clear_temp(context)
        await show_main_menu(update, context, welcome=False)
        return
    await ensure_registered(update, context)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    set_state(context, None)
    clear_temp(context)
    log_action(uid, "cancel", "cancelled current flow")
    await update.message.reply_text("لغو شد.", reply_markup=main_menu_kb() if get_user(uid) else ReplyKeyboardRemove())


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if not require_owner(uid):
        return
    set_state(context, None)
    clear_temp(context)
    log_action(uid, "admin_open", "opened admin panel")
    await send_admin_home(update, context)

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
        await update.message.reply_text("حالا *کد ملی* را وارد کن.", parse_mode=ParseMode.MARKDOWN)
        log_action(uid, "registration_name", text)
        return True

    if state == S_REG_NID:
        if not (is_digit_str(text) and len(text) >= 8):
            await update.message.reply_text("کد ملی نامعتبر است. فقط عدد وارد کن.")
            return True
        temp["national_id"] = text
        set_state(context, S_REG_ACCOUNT)
        await update.message.reply_text("در نهایت *شماره حساب بانکی* را وارد کن.", parse_mode=ParseMode.MARKDOWN)
        log_action(uid, "registration_national_id", text)
        return True

    if state == S_REG_ACCOUNT:
        if len(text) < 8:
            await update.message.reply_text("شماره حساب/شبا نامعتبر است. دوباره وارد کن.")
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
        log_action(uid, "registration_complete", f"name={temp.get('name','')}")
        set_state(context, None)
        clear_temp(context)
        await show_main_menu(update, context, welcome=True)
        return True

    return False

# -----------------------------
# Product add / edit / buy flows
# -----------------------------

async def start_add_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    set_state(context, S_ADD_NAME)
    clear_temp(context)
    log_action(uid, "add_product_start", "start add product flow")
    await update.message.reply_text("نام محصول را وارد کن.", reply_markup=ReplyKeyboardRemove())


async def handle_add_product(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    uid = update.effective_user.id
    state = user_state(context)
    temp = get_temp(context)

    if state == S_ADD_NAME:
        temp["name"] = text
        set_state(context, S_ADD_PRICE)
        await update.message.reply_text("مبلغ محصول را به تومان وارد کن. فقط عدد.")
        log_action(uid, "add_product_name", text)
        return True

    if state == S_ADD_PRICE:
        if not is_digit_str(text):
            await update.message.reply_text("فقط عدد وارد کن.")
            return True
        price = int(text)
        if price <= 0:
            await update.message.reply_text("مبلغ باید بزرگ‌تر از صفر باشد.")
            return True
        temp["price"] = price
        set_state(context, S_ADD_LINK)
        await update.message.reply_text("لینک پست را بفرست.")
        log_action(uid, "add_product_price", str(price))
        return True

    if state == S_ADD_LINK:
        if not (text.startswith("http://") or text.startswith("https://")):
            await update.message.reply_text("لینک معتبر وارد کن (http/https).")
            return True
        temp["link"] = text
        set_state(context, S_ADD_CONFIRM)
        preview = (
            f"نام کملوتی: {get_user_display(uid)}\n"
            f"نام محصول: {temp.get('name')}\n"
            f"مبلغ: {fmt_money(int(temp.get('price', 0)))} تومان\n"
            f"لینک پست: {temp.get('link')}\n\n"
            f"آیا مطمئنی که می‌خواهی این محصول را به ویترین فروش اضافه کنی؟"
        )
        await update.message.reply_text(preview, reply_markup=confirm_kb("add_product_confirm_yes", "add_product_confirm_no"))
        log_action(uid, "add_product_link", text)
        return True

    return False


async def finalize_add_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    temp = get_temp(context)
    code = unique_product_code()
    now = now_tehran()
    db_exec(
        """
        INSERT INTO products(code, seller_id, name, price, link, status, created_at, updated_at)
        VALUES(?, ?, ?, ?, ?, 'active', ?, ?)
        """,
        (code, uid, temp["name"], int(temp["price"]), temp["link"], now, now),
    )
    log_action(uid, "add_product_confirmed", f"code={code} name={temp['name']}")
    set_state(context, None)
    clear_temp(context)
    await update.callback_query.edit_message_text(
        f"محصول اضافه شد ✅\n\nکد یکتا: {code}\nنام: {temp['name']}\nمبلغ: {fmt_money(int(temp['price']))} تومان",
    )
    await update.callback_query.message.reply_text("منوی اصلی:", reply_markup=main_menu_kb())


async def start_buy_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_state(context, S_BUY_CODE)
    clear_temp(context)
    log_action(update.effective_user.id, "buy_start", "asked for product code")
    await update.message.reply_text("کد یکتای محصول را وارد کن.", reply_markup=ReplyKeyboardRemove())


async def ask_buy_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE, product: sqlite3.Row) -> None:
    temp = get_temp(context)
    temp["buy_product_code"] = product["code"]
    temp["buy_product_name"] = product["name"]
    temp["buy_price"] = int(product["price"])
    temp["buy_seller_id"] = int(product["seller_id"])
    temp["buy_seller_account"] = get_user(product["seller_id"])["bank_account"] if get_user(product["seller_id"]) else ""
    text = (
        f"محصول: {product['name']}\n"
        f"قیمت: {fmt_money(int(product['price']))} تومان\n"
        f"لینک پست: {product['link']}\n\n"
        f"آیا می‌خواهی این محصول را بخری؟"
    )
    await update.message.reply_text(text, reply_markup=confirm_kb("buy_confirm_yes", "buy_confirm_no"))


async def create_pending_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    temp = get_temp(context)
    product_code = temp["buy_product_code"]
    product = db_one("SELECT * FROM products WHERE code = ?", (product_code,))
    if not product or product["status"] != "active":
        set_state(context, None)
        clear_temp(context)
        await update.callback_query.edit_message_text("این محصول دیگر قابل خرید نیست.")
        return

    seller = get_user(int(product["seller_id"]))
    seller_account = seller["bank_account"] if seller else ""
    tx_code = unique_transaction_code()
    db_exec("DELETE FROM pending_buys WHERE buyer_id = ?", (uid,))
    db_exec(
        """
        INSERT INTO pending_buys(buyer_id, product_code, seller_id, seller_account, price, transaction_code, created_at)
        VALUES(?, ?, ?, ?, ?, ?, ?)
        """,
        (uid, product_code, int(product["seller_id"]), seller_account, int(product["price"]), tx_code, now_tehran()),
    )
    temp["transaction_code"] = tx_code
    set_state(context, S_BUY_RECEIPT)
    log_action(uid, "buy_confirmed", f"product={product_code} tx={tx_code}")
    buyer_name = get_user_display(uid)
    msg = (
        f"برو به بانک @{BANK_BOT_USERNAME} و مبلغ {fmt_money(int(product['price']))} تومان را به شماره حساب فروشنده بزن.\n\n"
        f"خیلی مهم: در بخش توضیحات/شناسه/بابت، این کد 12 کاراکتری را وارد کن:\n"
        f"{tx_code}\n\n"
        f"بعد از انتقال وجه، رسید را *فوروارد* کن تا بررسی شود.\n"
        f"خریدار: {buyer_name}"
    )
    await update.callback_query.edit_message_text(msg, reply_markup=cancel_kb(), parse_mode=ParseMode.MARKDOWN)


def extract_text_from_message(message) -> str:
    parts = [message.text or "", message.caption or ""]
    return "\n".join(p for p in parts if p)


def forwarded_from_bank_bot(message) -> bool:
    origin = getattr(message, "forward_origin", None)
    if origin and getattr(origin, "sender_user", None):
        username = (origin.sender_user.username or "").strip().lstrip("@").lower()
        if username == BANK_BOT_USERNAME:
            return True
    # backward-compatible fallbacks
    for attr in ("forward_from", "forward_from_user"):
        user = getattr(message, attr, None)
        if user and getattr(user, "username", None):
            if user.username.strip().lstrip("@").lower() == BANK_BOT_USERNAME:
                return True
    return False


async def verify_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE, message) -> None:
    uid = update.effective_user.id
    temp = get_temp(context)
    tx_code = temp.get("transaction_code")
    if not tx_code:
        await update.message.reply_text("هیچ خرید معلقی برای بررسی وجود ندارد.")
        return

    pending = db_one("SELECT * FROM pending_buys WHERE buyer_id = ?", (uid,))
    if not pending:
        set_state(context, None)
        clear_temp(context)
        await update.message.reply_text("خرید معلقی پیدا نشد.", reply_markup=main_menu_kb())
        return

    text = extract_text_from_message(message)
    if not forwarded_from_bank_bot(message):
        log_action(uid, "receipt_invalid_source", text[:500])
        await update.message.reply_text("این پیام به‌نظر نمی‌رسد از ربات بانک فوروارد شده باشد.")
        return

    if tx_code not in text:
        log_action(uid, "receipt_missing_tx", text[:500])
        await update.message.reply_text("کد ۱۲ کاراکتری گفته‌شده در رسید شما مشاهده نشد.")
        return

    product = db_one("SELECT * FROM products WHERE code = ?", (pending["product_code"],))
    seller = get_user(int(pending["seller_id"]))
    seller_account = (pending["seller_account"] or "").strip()
    if not product or not seller:
        await update.message.reply_text("اطلاعات محصول یا فروشنده معتبر نیست.")
        return

    if seller_account and seller_account not in text:
        log_action(uid, "receipt_missing_seller_account", text[:500])
        await update.message.reply_text("فاکتور نامعتبر است؛ شماره حساب فروشنده در رسید پیدا نشد.")
        return

    buyer = get_user(uid)
    purchased_at = now_tehran()
    receipt_blob = text[:4000]
    db_exec(
        """
        INSERT INTO purchases(buyer_id, seller_id, product_code, product_name, price, seller_account, transaction_code, receipt_text, purchased_at)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            uid,
            int(pending["seller_id"]),
            pending["product_code"],
            product["name"],
            int(pending["price"]),
            seller_account,
            tx_code,
            receipt_blob,
            purchased_at,
        ),
    )
    db_exec(
        "UPDATE products SET status = 'sold', sold_to = ?, sold_at = ?, updated_at = ? WHERE code = ?",
        (uid, purchased_at, purchased_at, product["code"]),
    )
    db_exec("DELETE FROM pending_buys WHERE buyer_id = ?", (uid,))
    log_action(uid, "receipt_verified", f"product={product['code']} tx={tx_code}")

    buyer_name = buyer["name"] if buyer else get_user_display(uid)
    seller_name = seller["name"] if seller else str(seller["telegram_id"])
    await update.message.reply_text("پرداخت تأیید شد ✅\nاین محصول به دارایی‌های شما اضافه شد.", reply_markup=main_menu_kb())

    # Notify seller
    notify_text = (
        f"محصول شما خریداری شد ✅\n\n"
        f"محصول: {product['name']}\n"
        f"کد محصول: {product['code']}\n"
        f"خریدار: {buyer_name}\n"
        f"آیدی عددی خریدار: {uid}\n"
        f"شماره حساب خریدار: {buyer['bank_account'] if buyer else '-'}\n"
        f"تاریخ و ساعت: {purchased_at}\n\n"
        f"در صورت نیامدن پول به حساب شما، می‌توانید شکایت خود را ثبت کنید."
    )
    try:
        await context.bot.send_message(chat_id=int(seller["telegram_id"]), text=notify_text)
    except Exception as exc:
        logger.exception("Failed to notify seller: %s", exc)

    set_state(context, None)
    clear_temp(context)


async def start_edit_product(update: Update, context: ContextTypes.DEFAULT_TYPE, product_code: str) -> None:
    uid = update.effective_user.id
    product = db_one("SELECT * FROM products WHERE code = ?", (product_code,))
    if not product:
        await update.callback_query.edit_message_text("محصول پیدا نشد.")
        return
    if int(product["seller_id"]) != uid and not is_owner(uid):
        await update.callback_query.answer("شما اجازه ویرایش این محصول را ندارید", show_alert=True)
        return
    temp = get_temp(context)
    temp["edit_product_code"] = product_code
    await update.callback_query.edit_message_text(
        f"کدام بخش محصول {product['name']} را می‌خواهی ویرایش کنی؟",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("نام", callback_data=f"edit_field:name:{product_code}")],
                [InlineKeyboardButton("مبلغ", callback_data=f"edit_field:price:{product_code}")],
                [InlineKeyboardButton("لینک", callback_data=f"edit_field:link:{product_code}")],
                [InlineKeyboardButton("⬅️ بازگشت", callback_data="back_to_vitrine")],
            ]
        ),
    )


async def apply_product_edit(update: Update, context: ContextTypes.DEFAULT_TYPE, field: str, product_code: str, value: str) -> None:
    uid = update.effective_user.id
    product = db_one("SELECT * FROM products WHERE code = ?", (product_code,))
    if not product:
        await update.message.reply_text("محصول پیدا نشد.")
        return
    if int(product["seller_id"]) != uid and not is_owner(uid):
        await update.message.reply_text("اجازه ویرایش این محصول را نداری.")
        return
    now = now_tehran()
    if field == "name":
        db_exec("UPDATE products SET name = ?, updated_at = ? WHERE code = ?", (value, now, product_code))
    elif field == "price":
        if not is_digit_str(value) or int(value) <= 0:
            await update.message.reply_text("مبلغ نامعتبر است.")
            return
        db_exec("UPDATE products SET price = ?, updated_at = ? WHERE code = ?", (int(value), now, product_code))
    elif field == "link":
        if not (value.startswith("http://") or value.startswith("https://")):
            await update.message.reply_text("لینک معتبر نیست.")
            return
        db_exec("UPDATE products SET link = ?, updated_at = ? WHERE code = ?", (value, now, product_code))
    else:
        await update.message.reply_text("فیلد نامعتبر است.")
        return
    log_action(uid, "product_edited", f"code={product_code} field={field} value={value}")
    set_state(context, None)
    clear_temp(context)
    await update.message.reply_text("ویرایش انجام شد ✅", reply_markup=main_menu_kb())


async def delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE, product_code: str) -> None:
    uid = update.effective_user.id
    product = db_one("SELECT * FROM products WHERE code = ?", (product_code,))
    if not product:
        await update.callback_query.edit_message_text("محصول پیدا نشد.")
        return
    if int(product["seller_id"]) != uid and not is_owner(uid):
        await update.callback_query.answer("اجازه حذف این محصول را نداری", show_alert=True)
        return
    db_exec("DELETE FROM products WHERE code = ?", (product_code,))
    log_action(uid, "product_deleted", f"code={product_code}")
    await update.callback_query.edit_message_text("محصول حذف شد.")

# -----------------------------
# Admin flows
# -----------------------------

async def admin_show_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = db_all("SELECT * FROM users ORDER BY created_at DESC")
    if not rows:
        await update.callback_query.edit_message_text("هیچ کاربری ثبت نشده است.", reply_markup=admin_kb())
        return
    text_parts = []
    for r in rows:
        text_parts.append(
            f"آیدی: {r['telegram_id']} | یوزرنیم: @{r['username'] if r['username'] else '-'} | نام: {r['name']} | کدملی: {r['national_id']} | حساب: {r['bank_account']}"
        )
    for chunk in safe_send_chunks("\n".join(text_parts)):
        await update.callback_query.message.reply_text(chunk)
    await update.callback_query.message.reply_text("پایان لیست کاربران.", reply_markup=admin_kb())
    log_action(update.effective_user.id, "admin_list_users", f"count={len(rows)}")


async def admin_ask_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_state(context, S_ADMIN_GET_USER_ID)
    clear_temp(context)
    await update.callback_query.edit_message_text("آیدی عددی تلگرامی کاربر را وارد کن:")


async def admin_open_user(update: Update, context: ContextTypes.DEFAULT_TYPE, uid_text: str) -> None:
    if not is_digit_str(uid_text):
        await update.message.reply_text("آیدی باید عدد باشد.")
        return
    target_uid = int(uid_text)
    row = get_user(target_uid)
    if not row:
        await update.message.reply_text("این کاربر در دیتابیس پیدا نشد.", reply_markup=admin_kb())
        return
    temp = get_temp(context)
    temp["admin_target_user_id"] = target_uid
    info = (
        f"آیدی: {row['telegram_id']}\n"
        f"یوزرنیم: @{row['username']}" if row["username"] else f"آیدی: {row['telegram_id']}\nیوزرنیم: -"
    )
    info += f"\nنام: {row['name']}\nکدملی: {row['national_id']}\nحساب: {row['bank_account']}"
    await update.message.reply_text(info, reply_markup=admin_user_actions_kb(target_uid))
    log_action(update.effective_user.id, "admin_open_user", f"target={target_uid}")


async def admin_blacklist_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.edit_message_text(
        "لیست سیاه:",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("افزودن", callback_data="blacklist_add")],
                [InlineKeyboardButton("حذف", callback_data="blacklist_remove")],
                [InlineKeyboardButton("نمایش", callback_data="blacklist_list")],
                [InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_back")],
            ]
        ),
    )


async def admin_blacklist_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = db_all("SELECT * FROM blacklist ORDER BY added_at DESC")
    if not rows:
        await update.callback_query.edit_message_text("لیست سیاه خالی است.", reply_markup=admin_kb())
        return
    text = "\n".join(
        f"آیدی: {r['telegram_id']} | دلیل: {r['reason'] or '-'} | زمان: {r['added_at']}" for r in rows
    )
    for chunk in safe_send_chunks(text):
        await update.callback_query.message.reply_text(chunk)
    await update.callback_query.message.reply_text("پایان لیست سیاه.", reply_markup=admin_kb())


async def admin_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = db_all("SELECT * FROM logs ORDER BY id DESC LIMIT 100")
    if not rows:
        await update.callback_query.edit_message_text("هیچ لاگی ثبت نشده است.", reply_markup=admin_kb())
        return
    text = "\n".join(
        f"[{r['created_at']}] uid={r['telegram_id']} | {r['action']} | {r['details']}" for r in rows
    )
    for chunk in safe_send_chunks(text):
        await update.callback_query.message.reply_text(chunk)
    await update.callback_query.message.reply_text("پایان لاگ‌ها.", reply_markup=admin_kb())

# -----------------------------
# Callback handler
# -----------------------------

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not await check_access(update, context):
        return

    uid = update.effective_user.id
    data = query.data or ""

    if data == "noop":
        return

    if data == "cancel_action":
        set_state(context, None)
        clear_temp(context)
        await query.edit_message_text("لغو شد.")
        await query.message.reply_text("منوی اصلی:", reply_markup=main_menu_kb())
        return

    if data == "add_product_confirm_yes":
        await finalize_add_product(update, context)
        return
    if data == "add_product_confirm_no":
        set_state(context, None)
        clear_temp(context)
        await query.edit_message_text("افزودن محصول لغو شد.")
        await query.message.reply_text("منوی اصلی:", reply_markup=main_menu_kb())
        return

    if data.startswith("buy_product:"):
        code = data.split(":", 1)[1]
        product = db_one("SELECT * FROM products WHERE code = ?", (code,))
        if not product or product["status"] != "active":
            await query.edit_message_text("این محصول قابل خرید نیست.")
            return
        set_state(context, S_BUY_CODE)
        get_temp(context)["buy_product_code"] = code
        await ask_buy_confirmation(query.message, context, product)
        return

    if data == "buy_confirm_yes":
        await create_pending_buy(update, context)
        return

    if data == "buy_confirm_no":
        set_state(context, None)
        clear_temp(context)
        await query.edit_message_text("خرید لغو شد.")
        await query.message.reply_text("منوی اصلی:", reply_markup=main_menu_kb())
        return

    if data.startswith("edit_product:"):
        await start_edit_product(update, context, data.split(":", 1)[1])
        return

    if data.startswith("edit_field:"):
        _, field, code = data.split(":", 2)
        temp = get_temp(context)
        temp["edit_product_code"] = code
        temp["edit_field"] = field
        set_state(context, S_EDIT_PRODUCT_VALUE)
        prompt = {"name": "نام جدید را وارد کن:", "price": "مبلغ جدید را وارد کن:", "link": "لینک جدید را وارد کن:"}[field]
        await query.edit_message_text(prompt)
        return

    if data.startswith("delete_product:"):
        code = data.split(":", 1)[1]
        await delete_product(update, context, code)
        return

    if data == "back_to_vitrine":
        await query.edit_message_text("بازگشت به ویترین.")
        await query.message.reply_text("ویترین فروش:", reply_markup=main_menu_kb())
        return

    # Admin
    if data == "admin_users":
        await admin_show_users(update, context)
        return
    if data == "admin_manage_user":
        await admin_ask_user_id(update, context)
        return
    if data == "admin_blacklist":
        await admin_blacklist_menu(update, context)
        return
    if data == "admin_logs":
        await admin_logs(update, context)
        return
    if data == "admin_toggle_bot":
        new_status = "off" if bot_is_on() else "on"
        set_bot_status(new_status)
        log_action(uid, "admin_toggle_bot", new_status)
        await query.edit_message_text(f"وضعیت ربات به {new_status} تغییر کرد.", reply_markup=admin_kb())
        return
    if data == "admin_back":
        await send_admin_home(update, context)
        return

    if data.startswith("admin_user_name:"):
        target = int(data.split(":", 1)[1])
        get_temp(context)["admin_target_user_id"] = target
        get_temp(context)["admin_target_field"] = "name"
        set_state(context, S_ADMIN_EDIT_USER_VALUE)
        await query.edit_message_text("نام جدید کملوتی را وارد کن:")
        return
    if data.startswith("admin_user_account:"):
        target = int(data.split(":", 1)[1])
        get_temp(context)["admin_target_user_id"] = target
        get_temp(context)["admin_target_field"] = "account"
        set_state(context, S_ADMIN_EDIT_USER_VALUE)
        await query.edit_message_text("شماره حساب جدید را وارد کن:")
        return
    if data.startswith("admin_user_username:"):
        target = int(data.split(":", 1)[1])
        get_temp(context)["admin_target_user_id"] = target
        get_temp(context)["admin_target_field"] = "username"
        set_state(context, S_ADMIN_EDIT_USER_VALUE)
        await query.edit_message_text("یوزرنیم جدید را بدون @ وارد کن:")
        return

    if data == "blacklist_add":
        set_state(context, S_ADMIN_BLACKLIST_ADD)
        await query.edit_message_text("آیدی عددی کاربر را برای افزودن به لیست سیاه وارد کن:")
        return
    if data == "blacklist_remove":
        set_state(context, S_ADMIN_BLACKLIST_REMOVE)
        await query.edit_message_text("آیدی عددی کاربر را برای خروج از لیست سیاه وارد کن:")
        return
    if data == "blacklist_list":
        await admin_blacklist_list(update, context)
        return

    await query.answer()

# -----------------------------
# Message handler
# -----------------------------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not await check_access(update, context):
        return

    uid = update.effective_user.id
    username = update.effective_user.username or ""
    text = normalize_text(update.message.text or update.message.caption)

    # Registration first
    if not get_user(uid):
        if await handle_registration(update, context, text):
            return
        return

    state = user_state(context)

    # If bot is off, only owner can use.
    if state:
        if state in {S_REG_NAME, S_REG_NID, S_REG_ACCOUNT}:
            if await handle_registration(update, context, text):
                return
        if state in {S_ADD_NAME, S_ADD_PRICE, S_ADD_LINK}:
            if await handle_add_product(update, context, text):
                return
        if state == S_BUY_CODE:
            product = db_one("SELECT * FROM products WHERE code = ?", (text,))
            if not product:
                await update.message.reply_text("محصولی با این کد پیدا نشد.")
                return
            if product["status"] != "active":
                await update.message.reply_text("این محصول قبلاً فروخته شده است.")
                return
            temp = get_temp(context)
            temp["buy_product_code"] = product["code"]
            await ask_buy_confirmation(update.message, context, product)
            return
        if state == S_BUY_RECEIPT:
            await verify_receipt(update, context, update.message)
            return
        if state == S_EDIT_PRODUCT_VALUE:
            temp = get_temp(context)
            field = temp.get("edit_field")
            code = temp.get("edit_product_code")
            await apply_product_edit(update, context, field, code, text)
            return
        if state == S_ADMIN_GET_USER_ID:
            await admin_open_user(update, context, text)
            set_state(context, None)
            return
        if state == S_ADMIN_EDIT_USER_VALUE:
            temp = get_temp(context)
            target = int(temp.get("admin_target_user_id", 0))
            field = temp.get("admin_target_field")
            row = get_user(target)
            if not row:
                await update.message.reply_text("کاربر پیدا نشد.", reply_markup=admin_kb())
                set_state(context, None)
                return
            if field == "name":
                db_exec("UPDATE users SET name = ?, updated_at = ? WHERE telegram_id = ?", (text, now_tehran(), target))
            elif field == "account":
                db_exec("UPDATE users SET bank_account = ?, updated_at = ? WHERE telegram_id = ?", (text, now_tehran(), target))
            elif field == "username":
                db_exec("UPDATE users SET username = ?, updated_at = ? WHERE telegram_id = ?", (text.lstrip('@'), now_tehran(), target))
            else:
                await update.message.reply_text("فیلد نامعتبر است.")
                set_state(context, None)
                return
            log_action(uid, "admin_edit_user", f"target={target} field={field} value={text}")
            set_state(context, None)
            clear_temp(context)
            await update.message.reply_text("ویرایش کاربر انجام شد ✅", reply_markup=admin_kb())
            return
        if state == S_ADMIN_BLACKLIST_ADD:
            if not is_digit_str(text):
                await update.message.reply_text("آیدی باید عدد باشد.")
                return
            target = int(text)
            db_exec(
                "INSERT INTO blacklist(telegram_id, reason, added_at) VALUES(?, ?, ?) ON CONFLICT(telegram_id) DO UPDATE SET reason=excluded.reason, added_at=excluded.added_at",
                (target, "admin action", now_tehran()),
            )
            log_action(uid, "blacklist_add", f"target={target}")
            set_state(context, None)
            await update.message.reply_text("به لیست سیاه اضافه شد.", reply_markup=admin_kb())
            return
        if state == S_ADMIN_BLACKLIST_REMOVE:
            if not is_digit_str(text):
                await update.message.reply_text("آیدی باید عدد باشد.")
                return
            target = int(text)
            db_exec("DELETE FROM blacklist WHERE telegram_id = ?", (target,))
            log_action(uid, "blacklist_remove", f"target={target}")
            set_state(context, None)
            await update.message.reply_text("از لیست سیاه حذف شد.", reply_markup=admin_kb())
            return

    # No active state -> main menu text
    if text == BTN_BUY:
        await start_buy_flow(update, context)
        return
    if text == BTN_ADD:
        await start_add_product(update, context)
        return
    if text == BTN_ASSETS:
        await send_my_assets(update, context)
        return
    if text == BTN_VITRINE:
        await send_vitrine(update, context)
        return
    if text == BTN_ADMIN and uid == OWNER_ID:
        await send_admin_home(update, context)
        return

    if uid == OWNER_ID and text.lower() == "/admin":
        await send_admin_home(update, context)
        return

    # friendly fallback
    await update.message.reply_text("از منوی اصلی یکی از گزینه‌ها را انتخاب کن.", reply_markup=main_menu_kb())

# -----------------------------
# Command fallbacks
# -----------------------------

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("از /start برای شروع استفاده کن.")

# -----------------------------
# Error handler
# -----------------------------

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error: %s", context.error)
    try:
        if isinstance(update, Update) and update.effective_user:
            log_action(update.effective_user.id, "error", str(context.error))
    except Exception:
        pass

# -----------------------------
# Main
# -----------------------------

def main() -> None:
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    logger.info("Bot started. OWNER_ID=%s BANK_BOT_USERNAME=%s", OWNER_ID, BANK_BOT_USERNAME)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
'''
Path('/mnt/data/bot.py').write_text(code, encoding='utf-8')
print('written', Path('/mnt/data/bot.py').stat().st_size)
