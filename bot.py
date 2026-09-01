# -*- coding: utf-8 -*-
"""Camelot Telegram Marketplace Bot - v2: Stores & Roles"""

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
from typing import Optional, List, Dict, Any, Tuple

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
BTN_ADD_STORE = "🏪 افزودن فروشگاه"
BTN_MY_STORES = "🏬 فروشگاه‌های من"
BTN_MY_PURCHASES = "🧾 خریدهای من"

# Registration
S_REG_NAME = "reg_name"
S_REG_NID = "reg_nid"
S_REG_ACCOUNT = "reg_account"
S_REG_CHOOSE_STORE = "reg_choose_store"  # new: list stores to join
S_REG_CHOOSE_ROLE = "reg_choose_role"    # new: manager/employee

# Add product
S_ADD_NAME = "add_name"
S_ADD_PRICE = "add_price"
S_ADD_LINK = "add_link"
S_ADD_CHOOSE_STORE = "add_choose_store"  # new: pick which store to add to

# Buy
S_BUY_CODE = "buy_code"
S_BUY_RECEIPT = "buy_receipt"

# Edit product
S_EDIT_PRODUCT_FIELD = "edit_product_field"
S_EDIT_PRODUCT_VALUE = "edit_product_value"

# Admin: user mgmt
S_ADMIN_GET_USER_ID = "admin_get_user_id"
S_ADMIN_EDIT_USER_FIELD = "admin_edit_user_field"
S_ADMIN_EDIT_USER_VALUE = "admin_edit_user_value"
S_ADMIN_BLACKLIST_ADD = "admin_blacklist_add"
S_ADMIN_BLACKLIST_REMOVE = "admin_blacklist_remove"

# Admin: store mgmt
S_ADMIN_CREATE_STORE_NAME = "admin_create_store_name"
S_ADMIN_TRANSFER_STORE = "admin_transfer_store"          # 1) which store
S_ADMIN_TRANSFER_TARGET = "admin_transfer_target"        # 2) target user id
S_ADMIN_TRANSFER_ROLE = "admin_transfer_role"            # 3) confirm role for new manager
S_ADMIN_DELETE_STORE = "admin_delete_store"              # confirm deletion
S_ADMIN_DELETE_STORE_OLD_MANAGER = "admin_delete_store_old_manager"  # what to do w/ old manager
S_ADMIN_DELETE_STORE_EMPLOYEES = "admin_delete_store_employees"      # what to do w/ employees

# User-side: add store
S_USER_CREATE_STORE_NAME = "user_create_store_name"
S_USER_JOIN_STORE = "user_join_store"
S_USER_JOIN_ROLE = "user_join_role"

# Manager-side: manage employees
S_MGR_REMOVE_EMPLOYEE = "mgr_remove_employee"

# Backup/Restore
S_ADMIN_BACKUP_IMPORT_FILE = "admin_backup_import_file"
S_ADMIN_BACKUP_CONFIRM = "admin_backup_confirm"
S_RESTORE_ACCOUNT_FILE = "restore_account_file"
S_RESTORE_ACCOUNT_CONFIRM = "restore_account_confirm"

ROLE_MANAGER = "manager"
ROLE_EMPLOYEE = "employee"

# -----------------------------
# SQLite helpers
# -----------------------------

_db_lock = threading.RLock()
_db = sqlite3.connect(DB_PATH, check_same_thread=False)
_db.row_factory = sqlite3.Row
_db.execute("PRAGMA foreign_keys = ON;")


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
    global _init_now_tehran
    _init_now_tehran = datetime.now(TEHRAN).strftime("%Y-%m-%d %H:%M:%S")
    with _db_lock:
        _db.execute("PRAGMA journal_mode=WAL;")
        _db.execute("PRAGMA foreign_keys = ON;")
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
                store_id INTEGER,
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
                store_id INTEGER,
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
                store_id INTEGER,
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

        # New: stores & members
        _db.execute(
            """
            CREATE TABLE IF NOT EXISTS stores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                owner_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        _db.execute(
            """
            CREATE TABLE IF NOT EXISTS store_members (
                store_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('manager','employee')),
                joined_at TEXT NOT NULL,
                PRIMARY KEY (store_id, user_id)
            )
            """
        )

        # Indices
        _db.execute("CREATE INDEX IF NOT EXISTS idx_products_store ON products(store_id)")
        _db.execute("CREATE INDEX IF NOT EXISTS idx_members_user ON store_members(user_id)")
        _db.execute("CREATE INDEX IF NOT EXISTS idx_members_store ON store_members(store_id)")

        _db.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('bot_status', 'on')")
        _db.commit()

    migrate_legacy_data()


def migrate_legacy_data() -> None:
    """One-time migration: every existing product with no store_id gets a default store 'فروشگاه پیش‌فرض'
    owned by its seller. Ensures no NULL store_id remains in active paths."""
    global _init_now_tehran
    now = _init_now_tehran
    with _db_lock:
        # Create default store for each unique seller_id that has products but no store_id
        rows = _db.execute(
            """
            SELECT DISTINCT seller_id FROM products WHERE store_id IS NULL
            """
        ).fetchall()
        for r in rows:
            sid = r["seller_id"]
            # Create a default store for this user (named after them)
            user = _db.execute("SELECT name FROM users WHERE telegram_id = ?", (sid,)).fetchone()
            store_name = f"فروشگاه {user['name'] if user else sid}"
            cur = _db.execute(
                "INSERT OR IGNORE INTO stores(name, owner_id, created_at, updated_at) VALUES(?, ?, ?, ?)",
                (store_name, sid, now, now),
            )
            store_id = cur.lastrowid
            if not store_id:
                row2 = _db.execute("SELECT id FROM stores WHERE name = ?", (store_name,)).fetchone()
                store_id = row2["id"] if row2 else None
            if store_id:
                _db.execute(
                    "INSERT OR IGNORE INTO store_members(store_id, user_id, role, joined_at) VALUES(?, ?, ?, ?)",
                    (store_id, sid, ROLE_MANAGER, now),
                )
                _db.execute("UPDATE products SET store_id = ? WHERE seller_id = ? AND store_id IS NULL", (store_id, sid))

        # Migrate pending_buys/purchases to have store_id if missing
        _db.execute(
            """
            UPDATE pending_buys SET store_id = (SELECT store_id FROM products WHERE products.code = pending_buys.product_code)
            WHERE store_id IS NULL
            """
        )
        _db.execute(
            """
            UPDATE purchases SET store_id = (SELECT store_id FROM products WHERE products.code = purchases.product_code)
            WHERE store_id IS NULL
            """
        )
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

# -----------------------------
# Store & membership helpers
# -----------------------------

def get_store(store_id: int) -> Optional[sqlite3.Row]:
    return db_one("SELECT * FROM stores WHERE id = ?", (store_id,))

def get_user_stores(uid: int) -> List[sqlite3.Row]:
    return db_all(
        """
        SELECT s.*, m.role FROM stores s
        JOIN store_members m ON m.store_id = s.id
        WHERE m.user_id = ?
        ORDER BY s.created_at ASC
        """,
        (uid,),
    )

def get_user_role_in_store(uid: int, store_id: int) -> Optional[str]:
    row = db_one(
        "SELECT role FROM store_members WHERE store_id = ? AND user_id = ?",
        (store_id, uid),
    )
    return row["role"] if row else None

def user_is_store_manager(uid: int, store_id: int) -> bool:
    return get_user_role_in_store(uid, store_id) == ROLE_MANAGER

def get_store_members(store_id: int) -> List[sqlite3.Row]:
    return db_all(
        """
        SELECT u.telegram_id, u.name, u.username, u.bank_account, m.role, m.joined_at
        FROM store_members m
        JOIN users u ON u.telegram_id = m.user_id
        WHERE m.store_id = ?
        ORDER BY (m.role = 'manager') DESC, m.joined_at ASC
        """,
        (store_id,),
    )

def get_store_manager(store_id: int) -> Optional[sqlite3.Row]:
    """Returns the manager user row, or None if no manager exists."""
    return db_one(
        """
        SELECT u.* FROM users u
        JOIN store_members m ON m.user_id = u.telegram_id
        WHERE m.store_id = ? AND m.role = 'manager'
        """,
        (store_id,),
    )

def get_store_payment_manager(store_id: int) -> Optional[sqlite3.Row]:
    """Returns the manager whose bank_account should be shown to buyers.
    Priority: a non-owner manager (the actual shop operator) over the bot owner.
    Returns None if the store has no manager at all."""
    # First, try non-owner manager
    row = db_one(
        """
        SELECT u.* FROM users u
        JOIN store_members m ON m.user_id = u.telegram_id
        WHERE m.store_id = ? AND m.role = 'manager' AND m.user_id != ?
        ORDER BY m.joined_at ASC
        LIMIT 1
        """,
        (store_id, OWNER_ID),
    )
    if row:
        return row
    # Fallback: any manager (could be the bot owner)
    return get_store_manager(store_id)

def get_store_employees(store_id: int) -> List[sqlite3.Row]:
    return db_all(
        """
        SELECT u.* FROM users u
        JOIN store_members m ON m.user_id = u.telegram_id
        WHERE m.store_id = ? AND m.role = 'employee'
        """,
        (store_id,),
    )

def store_has_non_owner_manager(store_id: int) -> bool:
    """True if the store has any manager other than the bot owner."""
    rows = db_all(
        "SELECT user_id FROM store_members WHERE store_id = ? AND role = 'manager' AND user_id != ?",
        (store_id, OWNER_ID),
    )
    return len(rows) > 0

def get_store_active_account(store_id: int) -> str:
    """Account that should receive payments for this store.
    Priority: a non-owner manager's account (the actual shop operator).
    Falls back to the bot owner's account if no other manager exists."""
    mgr = get_store_payment_manager(store_id)
    if not mgr:
        return ""
    return (mgr["bank_account"] or "").strip()

def list_all_stores() -> List[sqlite3.Row]:
    return db_all("SELECT * FROM stores ORDER BY created_at DESC")

def get_user_managed_stores(uid: int) -> List[sqlite3.Row]:
    """Stores where user is the manager."""
    return db_all(
        """
        SELECT s.* FROM stores s
        JOIN store_members m ON m.store_id = s.id
        WHERE m.user_id = ? AND m.role = 'manager'
        ORDER BY s.created_at ASC
        """,
        (uid,),
    )

async def notify_membership_change(context: ContextTypes.DEFAULT_TYPE, store_id: int, new_member_uid: int, role: str) -> None:
    """Send notification to the bot owner and current store manager(s) about new membership."""
    store = get_store(store_id)
    if not store:
        return
    new_member = get_user(new_member_uid)
    if not new_member:
        return
    role_label = "مدیر" if role == ROLE_MANAGER else "کارمند"
    text = (
        f"🔔 **تغییر عضویت در فروشگاه**\n\n"
        f"🏪 فروشگاه: {store['name']}\n"
        f"👤 کاربر جدید: {new_member['name']} (id: {new_member_uid})\n"
        f"🎖 سطح دسترسی: {role_label}\n"
        f"🕐 زمان: {now_tehran()}"
    )
    # Notify the bot owner (if not the new member)
    if not is_owner(new_member_uid):
        try:
            await context.bot.send_message(chat_id=OWNER_ID, text=text, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Failed to notify owner about membership: {e}")
    # Notify current manager(s) of the store (if not the new member and not the owner)
    managers = db_all(
        "SELECT user_id FROM store_members WHERE store_id = ? AND role = 'manager'",
        (store_id,),
    )
    for mgr in managers:
        mgr_uid = int(mgr["user_id"])
        if mgr_uid == new_member_uid or mgr_uid == OWNER_ID:
            continue
        try:
            await context.bot.send_message(chat_id=mgr_uid, text=text, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Failed to notify manager {mgr_uid}: {e}")

# -----------------------------
# Keyboards
# -----------------------------

def main_menu_kb(uid: int) -> ReplyKeyboardMarkup:
    rows = [
        [BTN_BUY, BTN_ADD],
        [BTN_ASSETS, BTN_VITRINE],
        [BTN_MY_STORES],  # visible to all users
    ]
    if is_owner(uid):
        # Only the bot owner sees "Add Store" button + admin panel
        rows.append([BTN_ADD_STORE])
        rows.append([BTN_ADMIN])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو عملیات", callback_data="cancel_action")]])

def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 لیست کاربران", callback_data="admin_users")],
        [InlineKeyboardButton("🧑‍💼 مدیریت کاربر", callback_data="admin_manage_user")],
        [InlineKeyboardButton("🏪 مدیریت فروشگاه‌ها", callback_data="admin_stores")],
        [InlineKeyboardButton("⛔ لیست سیاه", callback_data="admin_blacklist")],
        [InlineKeyboardButton("📄 ثبت لاگ ها", callback_data="admin_logs")],
        [InlineKeyboardButton("🔌 خاموش/روشن", callback_data="admin_toggle_bot")],
        [InlineKeyboardButton("💾 پشتیبان‌گیری و بازیابی", callback_data="admin_backup")],
        [InlineKeyboardButton("❌ بستن پنل", callback_data="cancel_action")],
    ])

def admin_stores_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ ساخت فروشگاه", callback_data="admin_store_create")],
        [InlineKeyboardButton("📋 لیست فروشگاه‌ها", callback_data="admin_store_list")],
        [InlineKeyboardButton("🔄 انتقال مدیریت فروشگاه", callback_data="admin_store_transfer")],
        [InlineKeyboardButton("🗑 حذف فروشگاه", callback_data="admin_store_delete")],
        [InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_back")],
    ])

def confirm_kb(yes_data: str, no_data: str = "cancel_action") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ آره", callback_data=yes_data),
        InlineKeyboardButton("❌ نه", callback_data=no_data),
    ]])

def product_actions_kb(code: str, seller_id: int, store_id: int, viewer_id: int) -> InlineKeyboardMarkup:
    rows = []
    # Permission to edit: owner, OR any member of the store the product belongs to
    can_edit = False
    if is_owner(viewer_id):
        can_edit = True
    elif store_id and get_user_role_in_store(viewer_id, int(store_id)) is not None:
        can_edit = True
    elif viewer_id == seller_id:
        can_edit = True
    if can_edit:
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

def store_role_kb(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👑 مدیر", callback_data=f"{prefix}:manager")],
        [InlineKeyboardButton("🧑‍🔧 کارمند", callback_data=f"{prefix}:employee")],
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

# -----------------------------
# Backup & Restore
# -----------------------------

def export_full_backup() -> str:
    tables = ['users', 'products', 'purchases', 'pending_buys', 'blacklist', 'settings', 'logs', 'stores', 'store_members']
    data = {}
    with _db_lock:
        for table in tables:
            cursor = _db.execute(f"SELECT * FROM {table}")
            rows = cursor.fetchall()
            data[table] = [dict(row) for row in rows]
    return json.dumps(data, indent=2, ensure_ascii=False)

def import_full_backup(json_data: str) -> tuple:
    try:
        data = json.loads(json_data)
    except json.JSONDecodeError as e:
        return False, f"فایل JSON معتبر نیست: {e}"

    expected_tables = {'users', 'products', 'purchases', 'pending_buys', 'blacklist', 'settings', 'logs', 'stores', 'store_members'}
    if not expected_tables.issubset(data.keys()):
        return False, "فایل پشتیبان کامل نیست. جداول مورد نیاز وجود ندارند."

    with _db_lock:
        try:
            # Order matters due to FK
            for table in expected_tables:
                _db.execute(f"DELETE FROM {table}")
            for table, rows in data.items():
                if not rows:
                    continue
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

# -----------------------------
# Access control
# -----------------------------

async def check_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    uid = update.effective_user.id if update.effective_user else None
    if uid is None:
        return False

    if user_is_blacklisted(uid):
        msg = "دسترسی های شما، توسط مدیریت مسدود شده است!"
        if update.message:
            await update.message.reply_text(msg)
        elif update.callback_query:
            await update.callback_query.answer(msg, show_alert=True)
        return False

    if not bot_is_on() and not is_owner(uid):
        msg = "این اداره، توسط هیئت مدیره، بسته شده است!"
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
    if is_owner(uid):
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 بازیابی اطلاعات", callback_data="restore_account")],
            [InlineKeyboardButton("📝 ثبت‌نام جدید", callback_data="register_new")]
        ])
        await update.message.reply_text(
            "🏦 **به اداره ثبت‌اسناد کملوت خوش آمدید!**\n\n"
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
# Cancel handler
# -----------------------------

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    user = get_user(uid)
    set_state(context, None)
    clear_temp(context)
    if user:
        await update.message.reply_text(
            "❌ عملیات لغو شد. به منوی اصلی بازگشتید.",
            reply_markup=main_menu_kb(uid)
        )
    else:
        await update.message.reply_text("❌ عملیات لغو شد. برای شروع مجدد /start بزنید.")
    return ConversationHandler.END

# -----------------------------
# Start / Admin command
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
        await update.message.reply_text("صفحه اصلی", reply_markup=main_menu_kb(uid))
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

async def prompt_store_choice_for_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """After account is created, show list of stores to join. Only owner sees 'create new' option."""
    uid = update.effective_user.id
    stores = list_all_stores()
    rows = []
    for s in stores[:20]:  # cap to avoid huge keyboards
        rows.append([InlineKeyboardButton(f"🏪 {s['name']} (#{s['id']})", callback_data=f"reg_pick_store:{s['id']}")])
    if is_owner(uid):
        # Only the bot owner can create new stores during registration
        rows.append([InlineKeyboardButton("➕ ساخت فروشگاه جدید", callback_data="reg_new_store")])
    rows.append([InlineKeyboardButton("❌ لغو", callback_data="cancel_action")])
    msg = "🏪 **انتخاب فروشگاه**\n\n"
    if stores:
        msg += "یک فروشگاه موجود را انتخاب کنید تا به آن بپیوندید."
        if is_owner(uid):
            msg += "\nیا فروشگاه جدیدی بسازید."
    else:
        msg += "فروشگاهی ثبت نشده است. "
        if is_owner(uid):
            msg += "می‌توانید یک فروشگاه جدید بسازید."
        else:
            msg += "لطفاً منتظر بمانید تا مالک ربات فروشگاهی ایجاد کند."
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(rows), parse_mode='Markdown')


async def handle_registration(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    uid = update.effective_user.id
    state = user_state(context)
    temp = get_temp(context)

    if state == S_REG_NAME:
        temp["name"] = text
        set_state(context, S_REG_NID)
        await update.message.reply_text("کدملی خود را وارد کنید:", reply_markup=ReplyKeyboardRemove())
        return True

    if state == S_REG_NID:
        if not (text.isdigit() and len(text) == 6):
            await update.message.reply_text("خطا: کدملی باید دقیقاً ۶ رقم و فقط عدد باشد. دوباره وارد کنید:", reply_markup=ReplyKeyboardRemove())
            return True
        temp["national_id"] = text
        set_state(context, S_REG_ACCOUNT)
        await update.message.reply_text("شماره حساب بانکی خود را وارد کنید:", reply_markup=ReplyKeyboardRemove())
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

        # After account is created, prompt store selection
        await update.message.reply_text(
            "ثبت نام شما با موفقیت انجام شد ✅\n\nحالا فروشگاه خود را انتخاب کنید:",
            reply_markup=ReplyKeyboardRemove(),
        )
        await prompt_store_choice_for_registration(update, context)
        return True

    return False

# -----------------------------
# Add product (now store-aware)
# -----------------------------

async def handle_add_product(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    uid = update.effective_user.id
    state = user_state(context)
    temp = get_temp(context)

    if state == S_ADD_CHOOSE_STORE:
        # Should not normally get plain text here; selection is via callback
        await update.message.reply_text("لطفاً فروشگاه را از دکمه‌ها انتخاب کنید.", reply_markup=cancel_kb())
        return True

    if state == S_ADD_NAME:
        temp["name"] = text
        set_state(context, S_ADD_PRICE)
        await update.message.reply_text("مبلغ محصول را وارد کنید:", reply_markup=cancel_kb())
        return True

    if state == S_ADD_PRICE:
        if not text.isdigit():
            await update.message.reply_text("لطفا مبلغ را به صورت عدد و ترجیحا اعداد انگلیسی و نه فارسی، وارد کنید:", reply_markup=cancel_kb())
            return True
        temp["price"] = int(text)
        set_state(context, S_ADD_LINK)
        await update.message.reply_text("لینک پست تلگرامی محصول که در فروشگاه خود قرار داده اید را بفرستید:", reply_markup=cancel_kb())
        return True

    if state == S_ADD_LINK:
        temp["link"] = text
        seller_name = get_user_display(uid)
        store = get_store(int(temp.get("add_store_id", 0))) if temp.get("add_store_id") else None
        store_name = store["name"] if store else "نامشخص"
        preview = (
            f"{seller_name}، آیا مطمئنی که میخواهید محصول {temp.get('name')} رو با مبلغ "
            f"{fmt_money(temp.get('price'))} به فروشگاه «{store_name}» اضافه کنی؟\n"
            f"لینک پست: {temp.get('link')}"
        )
        await update.message.reply_text(preview, reply_markup=confirm_kb("add_confirm_yes", "cancel_action"))
        return True

    return False


async def add_product_callback_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """When user picks a store from inline list before adding product."""
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    data = query.data or ""

    if not data.startswith("add_pick_store:"):
        return

    store_id = int(data.split(":")[1])
    if get_user_role_in_store(uid, store_id) is None:
        await query.edit_message_text("❌ شما عضو این فروشگاه نیستید.")
        return

    get_temp(context)["add_store_id"] = store_id
    set_state(context, S_ADD_NAME)
    await query.edit_message_text("نام محصول را وارد کن:", reply_markup=cancel_kb())

# -----------------------------
# Verify receipt (now store-aware: always uses store manager account)
# -----------------------------

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

    import re
    if tx_code not in text_content:
        log_action(uid, "receipt_failed_code", "12-char code not in receipt.")
        await update.message.reply_text("فاکتور نامعتبر!!", reply_markup=cancel_kb())
        return

    # Use store manager's account (not the original seller's)
    store_id = pending["store_id"] if pending["store_id"] else None
    if store_id is None:
        # Fallback: derive from product
        prod = db_one("SELECT store_id FROM products WHERE code = ?", (pending["product_code"],))
        store_id = prod["store_id"] if prod else None

    if store_id is None:
        await update.message.reply_text("❌ خطا: فروشگاه این محصول یافت نشد. با پشتیبانی تماس بگیرید.")
        return

    seller_account = get_store_active_account(int(store_id))
    if seller_account and seller_account not in text_content:
        log_action(uid, "receipt_failed_account", f"store_id={store_id}, account={seller_account}")
        await update.message.reply_text("فاکتور نامعتبر!!", reply_markup=cancel_kb())
        return

    expected_price = int(pending["price"])
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
    seller = get_user(int(pending["seller_id"]))  # the employee who listed it
    manager = get_store_payment_manager(int(store_id))  # the manager who gets paid (priority: non-owner)
    buyer = get_user(uid)
    purchased_at = now_tehran()

    db_exec(
        """
        INSERT INTO purchases(buyer_id, seller_id, store_id, product_code, product_name, price, seller_account, transaction_code, receipt_text, purchased_at)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (uid, int(pending["seller_id"]), int(store_id), pending["product_code"], product["name"], int(pending["price"]), seller_account, tx_code, text_content, purchased_at)
    )
    db_exec(
        "UPDATE products SET status = 'sold', sold_to = ?, sold_at = ?, updated_at = ? WHERE code = ?",
        (uid, purchased_at, purchased_at, product["code"])
    )
    db_exec("DELETE FROM pending_buys WHERE buyer_id = ?", (uid,))

    await update.message.reply_text("خرید با موفقیت انجام شد! سند این محصول به نام شما ثبت و تایید گردید.", reply_markup=main_menu_kb(uid))
    log_action(uid, "purchase_success", f"code={product['code']}, store_id={store_id}")

    buyer_name = buyer["name"] if buyer else str(uid)
    buyer_acc = buyer["bank_account"] if buyer else "ثبت نشده"
    manager_name = manager["name"] if manager else "نامشخص"

    # Notify the employee who listed the product
    if seller and int(seller["telegram_id"]) != uid:
        try:
            await context.bot.send_message(
                chat_id=int(seller["telegram_id"]),
                text=(
                    f"📦 محصول {product['name']} که شما در فروشگاه «{get_store(int(store_id))['name']}» ثبت کرده‌اید، "
                    f"توسط {buyer_name} (حساب {buyer_acc}) خریداری شد.\n"
                    f"پول به حساب مدیر فروشگاه ({manager_name}) واریز شد.\n"
                    f"تاریخ: {purchased_at}"
                ),
            )
        except Exception as e:
            logger.error(f"Failed to notify seller: {e}")

    # Notify the manager (whose account got paid)
    if manager and int(manager["telegram_id"]) != uid:
        try:
            await context.bot.send_message(
                chat_id=int(manager["telegram_id"]),
                text=(
                    f"💰 خرید جدید در فروشگاه شما «{get_store(int(store_id))['name']}»\n\n"
                    f"محصول: {product['name']} (کد {product['code']})\n"
                    f"خریدار: {buyer_name} (حساب {buyer_acc})\n"
                    f"مبلغ: {fmt_money(int(pending['price']))} تومان → واریز به حساب شما ({seller_account})\n"
                    f"ثبت‌کننده محصول: {seller['name'] if seller else 'نامشخص'}\n"
                    f"تاریخ: {purchased_at}"
                ),
            )
        except Exception as e:
            logger.error(f"Failed to notify manager: {e}")

    set_state(context, None)
    clear_temp(context)

# -----------------------------
# Backup/Restore Handlers (unchanged, included for completeness)
# -----------------------------

async def admin_backup_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        "• **گرفتن پشتیبان:** یک فایل JSON کامل از تمام اطلاعات تهیه می‌شود.\n"
        "• **بازیابی:** با ارسال فایل پشتیبان، اطلاعات قبلی بازگردانده می‌شود.\n\n"
        "⚠️ **هشدار:** بازیابی تمام اطلاعات فعلی را بازنویسی می‌کند!",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )

async def admin_backup_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
                    "📌 این فایل شامل تمام اطلاعات است.",
            parse_mode='Markdown'
        )
        log_action(uid, "admin_backup_export", "Backup exported")
        await query.edit_message_text(
            "✅ **پشتیبان با موفقیت تهیه و ارسال شد.**",
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
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    if not is_owner(uid):
        await query.edit_message_text("⛔ دسترسی ندارید.")
        return ConversationHandler.END

    await query.edit_message_text(
        "📤 **بازیابی از پشتیبان**\n\n"
        "⚠️ این عملیات تمام اطلاعات فعلی را بازنویسی می‌کند.\n\n"
        "لطفاً فایل پشتیبان (JSON) را ارسال کنید.\n"
        "(برای لغو /cancel بزنید)",
        parse_mode='Markdown'
    )
    return S_ADMIN_BACKUP_IMPORT_FILE

async def admin_backup_import_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    if not is_owner(uid):
        await update.message.reply_text("⛔ دسترسی ندارید.")
        return ConversationHandler.END

    document = update.message.document
    if not document:
        await update.message.reply_text("❌ لطفاً یک فایل ارسال کنید.")
        return S_ADMIN_BACKUP_IMPORT_FILE

    if not document.file_name.endswith('.json'):
        await update.message.reply_text("❌ فقط فایل‌های JSON معتبر هستند.")
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
            "آیا از بازنویسی کامل اطلاعات مطمئن هستید؟",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        return S_ADMIN_BACKUP_CONFIRM
    except Exception as e:
        logger.error(f"Error receiving backup file: {e}")
        await update.message.reply_text(f"❌ خطا در دریافت فایل: {str(e)}")
        context.user_data.pop('backup_json_data', None)
        return ConversationHandler.END

async def admin_backup_import_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
                "✅ **بازیابی با موفقیت انجام شد!**",
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

async def restore_account_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    if not is_owner(uid):
        await query.edit_message_text("⛔ دسترسی ندارید.")
        return ConversationHandler.END

    await query.edit_message_text(
        "📤 **بازیابی اطلاعات از فایل بکاپ**\n\n"
        "لطفاً فایل بکاپ (JSON) را ارسال کنید.\n"
        "(برای لغو /cancel بزنید)",
        parse_mode='Markdown'
    )
    return S_RESTORE_ACCOUNT_FILE

async def restore_account_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    if not is_owner(uid):
        await update.message.reply_text("⛔ دسترسی ندارید.")
        return ConversationHandler.END

    document = update.message.document
    if not document:
        await update.message.reply_text("❌ لطفاً یک فایل ارسال کنید.")
        return S_RESTORE_ACCOUNT_FILE

    if not document.file_name.endswith('.json'):
        await update.message.reply_text("❌ فقط فایل‌های JSON معتبر هستند.")
        return S_RESTORE_ACCOUNT_FILE

    await update.message.reply_text("📥 در حال دریافت و بررسی فایل...", parse_mode='Markdown')
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
            "⚠️ **تأیید نهایی بازیابی**",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        return S_RESTORE_ACCOUNT_CONFIRM
    except Exception as e:
        logger.error(f"Error receiving restore file: {e}")
        await update.message.reply_text(f"❌ خطا در دریافت فایل: {str(e)}")
        context.user_data.pop('backup_json_data', None)
        return ConversationHandler.END

async def restore_account_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    await query.edit_message_text("🔄 در حال بازیابی...", parse_mode='Markdown')
    try:
        success, message = import_full_backup(json_data)
        if success:
            log_action(uid, "restore_account", "Restore successful via start")
            await query.edit_message_text(
                "✅ **بازیابی با موفقیت انجام شد!**\n"
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
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    if not is_owner(uid):
        await query.edit_message_text("⛔ دسترسی ندارید.")
        return
    await query.edit_message_text(
        "🔄 ربات در حال ری‌استارت است... لطفاً چند ثانیه صبر کنید و سپس دوباره /start بزنید.",
        parse_mode='Markdown'
    )

# -----------------------------
# Callback router
# -----------------------------

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not await check_access(update, context):
        return

    uid = update.effective_user.id
    data = query.data or ""

    # ---- Cancel ----
    if data == "cancel_action":
        log_action(uid, "cancelled_action", f"State was {user_state(context)}")
        set_state(context, None)
        clear_temp(context)
        await query.message.delete()
        await context.bot.send_message(chat_id=uid, text="عملیات لغو شد. به منوی اصلی بازگشتید.", reply_markup=main_menu_kb(uid))
        return

    # ---- User browse & join stores (from "My Stores" menu) ----
    if data == "user_browse_stores":
        all_stores = list_all_stores()
        my_stores = get_user_stores(uid)
        my_ids = {s["id"] for s in my_stores}
        available = [s for s in all_stores if s["id"] not in my_ids]
        if not available:
            await query.edit_message_text(
                "🏬 هیچ فروشگاه دیگری برای پیوستن وجود ندارد.\n(شما عضو همه فروشگاه‌ها هستید یا فروشگاهی ثبت نشده)",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ بازگشت", callback_data="my_stores_back")]]),
            )
            return
        rows = []
        for s in available[:20]:
            mgr = get_store_manager(s["id"])
            mgr_name = mgr["name"] if mgr else "—"
            rows.append([InlineKeyboardButton(
                f"#{s['id']} | {s['name']} (مدیر: {mgr_name})",
                callback_data=f"user_pick_store:{s['id']}"
            )])
        rows.append([InlineKeyboardButton("⬅️ بازگشت", callback_data="my_stores_back")])
        await query.edit_message_text(
            "🏬 **انتخاب فروشگاه**\n\nیکی از فروشگاه‌های زیر را برای پیوستن انتخاب کنید:",
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode='Markdown',
        )
        return

    if data == "my_stores_back":
        # Re-render "My Stores" list
        stores = get_user_stores(uid)
        lines = []
        if stores:
            for s in stores:
                mgr = get_store_manager(s["id"])
                members = get_store_members(s["id"])
                role_label = "👑 مدیر" if s["role"] == ROLE_MANAGER else "🧑‍🔧 کارمند"
                mgr_name = mgr["name"] if mgr else "—"
                lines.append(f"#{s['id']} | {s['name']} | {role_label} | مدیر فعلی: {mgr_name} | {len(members)} عضو")
            text_out = "🏬 **فروشگاه‌های من**\n\n" + "\n".join(lines)
        else:
            text_out = "🏬 **فروشگاه‌های من**\n\nشما هنوز عضو هیچ فروشگاهی نیستید."

        kb_rows = [
            [InlineKeyboardButton("➕ دریافت فروشگاه‌های دیگر", callback_data="user_browse_stores")],
        ]
        managed = get_user_managed_stores(uid)
        if managed:
            if len(managed) == 1:
                kb_rows.append([InlineKeyboardButton(f"👥 مدیریت کارکنان «{managed[0]['name']}»", callback_data=f"mgr_emp_store:{managed[0]['id']}")])
            else:
                kb_rows.append([InlineKeyboardButton("👥 مدیریت کارکنان", callback_data="mgr_employees")])
        kb_rows.append([InlineKeyboardButton("❌ بستن", callback_data="cancel_action")])
        await query.edit_message_text(text_out, reply_markup=InlineKeyboardMarkup(kb_rows), parse_mode='Markdown')
        return

    if data.startswith("user_pick_store:"):
        store_id = int(data.split(":")[1])
        store = get_store(store_id)
        if not store:
            await query.edit_message_text("❌ فروشگاه یافت نشد.")
            return
        # Already a member?
        if get_user_role_in_store(uid, store_id) is not None:
            await query.edit_message_text("ℹ️ شما عضو این فروشگاه هستید.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ بازگشت", callback_data="my_stores_back")]]))
            return
        get_temp(context)["reg_store_id"] = store_id
        # Manager option is only shown if the store has no non-owner manager yet
        can_be_manager = not store_has_non_owner_manager(store_id)
        role_rows = []
        if can_be_manager:
            role_rows.append([InlineKeyboardButton("👑 مدیر", callback_data="user_join_role:manager")])
        role_rows.append([InlineKeyboardButton("🧑‍🔧 کارمند", callback_data="user_join_role:employee")])
        role_rows.append([InlineKeyboardButton("⬅️ بازگشت", callback_data="my_stores_back")])
        msg = f"🏪 فروشگاه: **{store['name']}**\n\nنقش خود را انتخاب کنید:"
        if not can_be_manager:
            msg += "\n\nℹ️ این فروشگاه در حال حاضر مدیر دارد. شما فقط می‌توانید به عنوان کارمند بپیوندید."
        await query.edit_message_text(
            msg,
            reply_markup=InlineKeyboardMarkup(role_rows),
            parse_mode='Markdown',
        )
        return

    if data.startswith("user_join_role:"):
        role = data.split(":")[1]
        if role not in (ROLE_MANAGER, ROLE_EMPLOYEE):
            return
        store_id = int(get_temp(context).get("reg_store_id", 0))
        store = get_store(store_id)
        if not store:
            await query.edit_message_text("❌ فروشگاه یافت نشد.")
            return
        if get_user_role_in_store(uid, store_id) is not None:
            await query.edit_message_text("ℹ️ شما عضو این فروشگاه هستید.")
            return
        # Guard: only allow "manager" if no non-owner manager exists
        if role == ROLE_MANAGER and store_has_non_owner_manager(store_id):
            await query.edit_message_text(
                "⛔ این فروشگاه در حال حاضر مدیر دارد. شما فقط می‌توانید به عنوان کارمند بپیوندید.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🧑‍🔧 عضویت به عنوان کارمند", callback_data="user_join_role:employee")], [InlineKeyboardButton("⬅️ بازگشت", callback_data="my_stores_back")]]),
            )
            return
        # If user picked "manager" and store already has a non-owner manager, demote old manager
        if role == ROLE_MANAGER:
            old = get_store_manager(store_id)
            if old and int(old["telegram_id"]) != uid and int(old["telegram_id"]) != OWNER_ID:
                db_exec(
                    "UPDATE store_members SET role = ? WHERE store_id = ? AND user_id = ?",
                    (ROLE_EMPLOYEE, store_id, int(old["telegram_id"])),
                )
                try:
                    await context.bot.send_message(
                        chat_id=int(old["telegram_id"]),
                        text=f"ℹ️ شما از مدیریت فروشگاه «{store['name']}» به نقش کارمند تغییر یافتید."
                    )
                except Exception:
                    pass
        db_exec(
            "INSERT OR REPLACE INTO store_members(store_id, user_id, role, joined_at) VALUES(?, ?, ?, ?)",
            (store_id, uid, role, now_tehran()),
        )
        db_exec("UPDATE stores SET owner_id = ?, updated_at = ? WHERE id = ?", (uid, now_tehran(), store_id))
        log_action(uid, "joined_store_via_browse", f"store_id={store_id}, role={role}")
        # Notify owner + current manager(s) about the new membership
        await notify_membership_change(context, store_id, uid, role)
        set_state(context, None)
        clear_temp(context)
        role_label = "مدیر" if role == ROLE_MANAGER else "کارمند"
        await query.edit_message_text(
            f"✅ شما با موفقیت به فروشگاه «{store['name']}» به عنوان **{role_label}** پیوستید.",
            parse_mode='Markdown',
        )
        await context.bot.send_message(chat_id=uid, text="به منوی اصلی بازگشتید.", reply_markup=main_menu_kb(uid))
        return

    # ---- Registration: store choice ----
    if data == "register_new":
        # Fresh registration flow already started via /start. Just send name prompt.
        context.user_data.clear()
        set_state(context, S_REG_NAME)
        clear_temp(context)
        await query.edit_message_text("📝 لطفاً نام کملوتی خود را وارد کنید:\n(برای لغو /cancel بزنید)", parse_mode='Markdown')
        return

    if data == "reg_new_store":
        set_state(context, S_USER_CREATE_STORE_NAME)
        await query.edit_message_text("🏪 نام فروشگاه جدید را وارد کنید:", reply_markup=cancel_kb())
        return

    if data.startswith("reg_pick_store:"):
        store_id = int(data.split(":")[1])
        store = get_store(store_id)
        if not store:
            await query.edit_message_text("❌ فروشگاه یافت نشد.")
            return
        get_temp(context)["reg_store_id"] = store_id
        # Manager option is only shown if the store has no non-owner manager yet
        can_be_manager = not store_has_non_owner_manager(store_id)
        role_rows = []
        if can_be_manager:
            role_rows.append([InlineKeyboardButton("👑 مدیر", callback_data=f"reg_pick_role:manager")])
        role_rows.append([InlineKeyboardButton("🧑‍🔧 کارمند", callback_data=f"reg_pick_role:employee")])
        role_rows.append([InlineKeyboardButton("❌ لغو", callback_data="cancel_action")])
        msg = f"🏪 فروشگاه: **{store['name']}**\n\nنقش خود را انتخاب کنید:"
        if not can_be_manager:
            msg += "\n\nℹ️ این فروشگاه در حال حاضر مدیر دارد. شما فقط می‌توانید به عنوان کارمند بپیوندید."
        await query.edit_message_text(
            msg,
            reply_markup=InlineKeyboardMarkup(role_rows),
            parse_mode='Markdown',
        )
        return

    if data.startswith("reg_pick_role:"):
        role = data.split(":")[1]
        if role not in (ROLE_MANAGER, ROLE_EMPLOYEE):
            return
        store_id = int(get_temp(context).get("reg_store_id", 0))
        store = get_store(store_id)
        if not store:
            await query.edit_message_text("❌ فروشگاه یافت نشد.")
            return
        # Guard: only allow "manager" if no non-owner manager exists
        if role == ROLE_MANAGER and store_has_non_owner_manager(store_id):
            await query.edit_message_text(
                "⛔ این فروشگاه در حال حاضر مدیر دارد. شما فقط می‌توانید به عنوان کارمند بپیوندید.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🧑‍🔧 عضویت به عنوان کارمند", callback_data="reg_pick_role:employee")], [InlineKeyboardButton("⬅️ بازگشت", callback_data="cancel_action")]]),
            )
            return
        # If user chose "manager" for an existing store, demote the old non-owner manager to employee
        if role == ROLE_MANAGER:
            old = get_store_manager(store_id)
            if old and int(old["telegram_id"]) != uid and int(old["telegram_id"]) != OWNER_ID:
                # Demote old manager
                db_exec(
                    "UPDATE store_members SET role = ? WHERE store_id = ? AND user_id = ?",
                    (ROLE_EMPLOYEE, store_id, int(old["telegram_id"])),
                )
                try:
                    await context.bot.send_message(
                        chat_id=int(old["telegram_id"]),
                        text=f"ℹ️ شما از مدیریت فروشگاه «{store['name']}» به نقش کارمند تغییر یافتید."
                    )
                except Exception:
                    pass
        # Add the new member
        db_exec(
            "INSERT OR REPLACE INTO store_members(store_id, user_id, role, joined_at) VALUES(?, ?, ?, ?)",
            (store_id, uid, role, now_tehran()),
        )
        # Update stores.owner_id for consistency
        db_exec("UPDATE stores SET owner_id = ?, updated_at = ? WHERE id = ?", (uid, now_tehran(), store_id))
        log_action(uid, "joined_store", f"store_id={store_id}, role={role}")
        # Notify owner + current manager(s) about the new membership
        await notify_membership_change(context, store_id, uid, role)
        set_state(context, None)
        clear_temp(context)
        role_label = "مدیر" if role == ROLE_MANAGER else "کارمند"
        await query.edit_message_text(
            f"✅ شما با موفقیت به فروشگاه «{store['name']}» به عنوان **{role_label}** پیوستید.",
            parse_mode='Markdown',
        )
        await context.bot.send_message(chat_id=uid, text="به منوی اصلی بازگشتید.", reply_markup=main_menu_kb(uid))
        return

    # ---- Add product: pick store ----
    if data == "add_pick_store_btn":
        stores = get_user_stores(uid)
        if not stores:
            await query.edit_message_text("❌ شما عضو هیچ فروشگاهی نیستید. ابتدا از دکمه «🏪 افزودن فروشگاه» استفاده کنید.")
            return
        rows = [[InlineKeyboardButton(f"🏪 {s['name']} ({'مدیر' if s['role']=='manager' else 'کارمند'})", callback_data=f"add_pick_store:{s['id']}")] for s in stores]
        rows.append([InlineKeyboardButton("❌ لغو", callback_data="cancel_action")])
        await query.edit_message_text("محصول را به کدام فروشگاه اضافه کنم؟", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("add_pick_store:"):
        await add_product_callback_choice(update, context)
        return

    # ---- Add confirm ----
    if data == "add_confirm_yes":
        temp = get_temp(context)
        store_id = int(temp.get("add_store_id", 0))
        if not store_id or not get_store(store_id):
            await query.edit_message_text("❌ خطا: فروشگاه یافت نشد.")
            return
        code = unique_product_code()
        db_exec(
            "INSERT INTO products(code, seller_id, store_id, name, price, link, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (code, uid, store_id, temp["name"], int(temp["price"]), temp["link"], now_tehran(), now_tehran())
        )
        log_action(uid, "product_added", f"code={code}, store_id={store_id}")
        set_state(context, None)
        await query.edit_message_text(f"محصول با موفقیت اضافه شد.\n\nکد یکتا: {code}\nنام: {temp['name']}")
        await context.bot.send_message(chat_id=uid, text="به صفحه اصلی بازگشتید.", reply_markup=main_menu_kb(uid))
        return

    # ---- Buy confirm ----
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

        store_id = product["store_id"]
        if not store_id:
            await query.edit_message_text("❌ خطا: فروشگاه این محصول یافت نشد.")
            return
        store = get_store(store_id)
        if not store:
            await query.edit_message_text("❌ فروشگاه این محصول حذف شده است.")
            return

        manager = get_store_payment_manager(store_id)
        if not manager:
            await query.edit_message_text("❌ این فروشگاه در حال حاضر مدیر ندارد و امکان خرید وجود ندارد.")
            return

        seller_account = (manager["bank_account"] or "").strip()
        if not seller_account:
            await query.edit_message_text("❌ مدیر فروشگاه هنوز شماره حساب خود را ثبت نکرده است. فعلاً امکان خرید نیست.")
            return

        tx_code = unique_transaction_code()
        db_exec("DELETE FROM pending_buys WHERE buyer_id = ?", (uid,))
        db_exec(
            "INSERT INTO pending_buys(buyer_id, product_code, seller_id, store_id, seller_account, price, transaction_code, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (uid, product_code, int(product["seller_id"]), store_id, seller_account, int(product["price"]), tx_code, now_tehran())
        )

        temp["transaction_code"] = tx_code
        set_state(context, S_BUY_RECEIPT)
        log_action(uid, "buy_initiated", f"code={product_code}, store_id={store_id}, tx={tx_code}")

        msg = (
            f"✅ مرحله بعد:\n\n"
            f"۱. به  بانک (@{BANK_BOT_USERNAME}) بروید.\n"
            f"۲. مبلغ {fmt_money(int(product['price']))} تومان را به شماره حساب زیر واریز کنید:\n"
            f"<code>{seller_account}</code>\n"
            f"(این حساب متعلق به مدیر فروشگاه «{store['name']}» — آقا/خانم {manager['name']} — است)\n\n"
            f"⚠️ <b>حتماً حتماً</b> در بخش توضیحات انتقال وجه، کد ۱۲ کاراکتری زیر را وارد کنید:\n"
            f"<code>{tx_code}</code>\n\n"
            f"اگر این کد را در بخش توضیحات وارد نکنید، پول شما گم می‌شود و قابل پیگیری نخواهد بود.\n\n"
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
                "خطا در ارسال دستورالعمل خرید. لطفاً دوباره تلاش کنید.",
                reply_markup=confirm_kb("buy_confirm_yes", "cancel_action")
            )
        return

    # ---- Delete / edit product ----
    if data.startswith("delete_product:"):
        code = data.split(":")[1]
        product = db_one("SELECT * FROM products WHERE code = ?", (code,))
        if not product:
            await query.edit_message_text("❌ محصول یافت نشد.")
            return
        # Permission: owner, OR any member of the store
        store_id = int(product["store_id"]) if product["store_id"] else 0
        can_delete = (
            is_owner(uid)
            or int(product["seller_id"]) == uid
            or (store_id and get_user_role_in_store(uid, store_id) is not None)
        )
        if not can_delete:
            await query.edit_message_text("⛔ دسترسی ندارید.")
            return
        db_exec("DELETE FROM products WHERE code = ?", (code,))
        log_action(uid, "product_deleted", f"code={code}")
        await query.edit_message_text("محصول با موفقیت حذف شد.")
        return

    if data.startswith("edit_product:"):
        code = data.split(":")[1]
        product = db_one("SELECT * FROM products WHERE code = ?", (code,))
        if not product:
            await query.edit_message_text("❌ محصول یافت نشد.")
            return
        store_id = int(product["store_id"]) if product["store_id"] else 0
        can_edit = (
            is_owner(uid)
            or int(product["seller_id"]) == uid
            or (store_id and get_user_role_in_store(uid, store_id) is not None)
        )
        if not can_edit:
            await query.edit_message_text("⛔ دسترسی ندارید.")
            return
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

    # ---- Admin callbacks ----
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
        await query.edit_message_text("پنل مدیریت:", reply_markup=admin_kb())
        return

    if data == "admin_backup":
        await admin_backup_menu(update, context)
        return

    if data == "admin_backup_export":
        await admin_backup_export(update, context)
        return

    if data == "restart_bot":
        await restart_bot_callback(update, context)
        return

    # ---- Admin: stores ----
    if data == "admin_stores":
        await query.edit_message_text("🏪 **مدیریت فروشگاه‌ها**\n\nیکی از گزینه‌ها را انتخاب کنید:", reply_markup=admin_stores_kb(), parse_mode='Markdown')
        return

    if data == "adm_xfer_confirm":
        # Final transfer: demote old non-owner manager(s) to employee, set new user as manager
        temp = get_temp(context)
        store_id = int(temp.get("xfer_store_id", 0))
        target_uid = int(temp.get("xfer_target_uid", 0))
        store = get_store(store_id) if store_id else None
        target_user = get_user(target_uid) if target_uid else None
        if not store or not target_user:
            await query.edit_message_text("❌ فروشگاه یا کاربر یافت نشد.", reply_markup=admin_stores_kb())
            return
        # Capture old non-owner managers for notification
        old_managers = db_all(
            "SELECT user_id FROM store_members WHERE store_id = ? AND role = 'manager' AND user_id != ?",
            (store_id, OWNER_ID),
        )
        # Demote all current non-owner managers to employees (keep owner as manager)
        db_exec(
            "UPDATE store_members SET role = 'employee' WHERE store_id = ? AND role = 'manager' AND user_id != ?",
            (store_id, OWNER_ID),
        )
        # Promote target to manager (insert or replace)
        db_exec(
            "INSERT OR REPLACE INTO store_members(store_id, user_id, role, joined_at) VALUES(?, ?, ?, ?)",
            (store_id, target_uid, ROLE_MANAGER, now_tehran()),
        )
        # Update owner_id
        db_exec("UPDATE stores SET owner_id = ?, updated_at = ? WHERE id = ?", (target_uid, now_tehran(), store_id))
        # Cancel pending_buys
        db_exec("DELETE FROM pending_buys WHERE store_id = ?", (store_id,))
        log_action(uid, "admin_transfer_store", f"store_id={store_id}, target={target_uid}, name={store['name']}")
        # Notify owner + new manager
        await notify_membership_change(context, store_id, target_uid, ROLE_MANAGER)
        # Notify old non-owner managers about demotion
        for om in old_managers:
            om_uid = int(om["user_id"])
            if om_uid == target_uid:
                continue
            try:
                await context.bot.send_message(
                    chat_id=om_uid,
                    text=f"ℹ️ شما از مدیریت فروشگاه «{store['name']}» به کارمند تنزل یافتید (انتقال توسط ادمین).",
                )
            except Exception:
                pass
        set_state(context, None)
        await query.edit_message_text(
            f"✅ فروشگاه **{store['name']}** به {target_user['name']} منتقل شد.",
            reply_markup=admin_stores_kb(),
            parse_mode='Markdown',
        )
        return

    if data == "admin_store_create":
        set_state(context, S_ADMIN_CREATE_STORE_NAME)
        await query.edit_message_text("🏪 نام فروشگاه جدید را وارد کنید:", reply_markup=cancel_kb())
        return

    if data == "admin_store_list":
        stores = list_all_stores()
        if not stores:
            await query.edit_message_text("هیچ فروشگاهی ثبت نشده.", reply_markup=admin_stores_kb())
            return
        text_lines = []
        for s in stores:
            mgr = get_store_manager(s["id"])
            mgr_name = mgr["name"] if mgr else "—"
            members = get_store_members(s["id"])
            text_lines.append(f"#{s['id']} | {s['name']} | مدیر: {mgr_name} | {len(members)} عضو")
        text = "\n".join(text_lines)
        for chunk in safe_send_chunks(text):
            await query.message.reply_text(chunk)
        await query.message.reply_text("برای بازگشت:", reply_markup=admin_stores_kb())
        return

    if data == "admin_store_transfer":
        stores = list_all_stores()
        if not stores:
            await query.edit_message_text("هیچ فروشگاهی برای انتقال نیست.", reply_markup=admin_stores_kb())
            return
        rows = [[InlineKeyboardButton(f"#{s['id']} - {s['name']}", callback_data=f"adm_xfer_pick:{s['id']}")] for s in stores]
        rows.append([InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_stores")])
        await query.edit_message_text("کدام فروشگاه را می‌خواهید منتقل کنید؟", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("adm_xfer_pick:"):
        store_id = int(data.split(":")[1])
        if not get_store(store_id):
            await query.edit_message_text("❌ فروشگاه یافت نشد.")
            return
        get_temp(context)["xfer_store_id"] = store_id
        set_state(context, S_ADMIN_TRANSFER_TARGET)
        await query.edit_message_text(
            f"🏪 فروشگاه: **{get_store(store_id)['name']}**\n\n"
            "آیدی عددی تلگرامی مدیر جدید را وارد کنید:",
            reply_markup=cancel_kb(),
            parse_mode='Markdown',
        )
        return

    if data == "admin_store_delete":
        stores = list_all_stores()
        if not stores:
            await query.edit_message_text("هیچ فروشگاهی برای حذف نیست.", reply_markup=admin_stores_kb())
            return
        rows = [[InlineKeyboardButton(f"#{s['id']} - {s['name']}", callback_data=f"adm_del_pick:{s['id']}")] for s in stores]
        rows.append([InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_stores")])
        await query.edit_message_text("کدام فروشگاه حذف شود؟", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("adm_del_pick:"):
        store_id = int(data.split(":")[1])
        store = get_store(store_id)
        if not store:
            await query.edit_message_text("❌ فروشگاه یافت نشد.")
            return
        get_temp(context)["del_store_id"] = store_id
        # Ask about old manager
        mgr = get_store_manager(store_id)
        emps = get_store_employees(store_id)
        if mgr or emps:
            # Ask about manager first
            set_state(context, S_ADMIN_DELETE_STORE_OLD_MANAGER)
            rows = []
            if mgr:
                rows.append([InlineKeyboardButton(f"👑 نگه داشتن مدیر فعلی ({mgr['name']})", callback_data="del_mgr_keep")])
                rows.append([InlineKeyboardButton("📉 تنزل مدیر به کارمند", callback_data="del_mgr_demote")])
                rows.append([InlineKeyboardButton("❌ حذف مدیر از فروشگاه", callback_data="del_mgr_remove")])
            rows.append([InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_stores")])
            await query.edit_message_text(
                f"🗑 حذف فروشگاه **{store['name']}**\n\n"
                "تکلیف مدیر فعلی چه باشد؟",
                reply_markup=InlineKeyboardMarkup(rows),
                parse_mode='Markdown',
            )
        else:
            # No members, just confirm
            await query.edit_message_text(
                f"🗑 فروشگاه **{store['name']}** هیچ عضوی ندارد. تأیید حذف؟",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ بله، حذف شود", callback_data="del_confirm")],
                    [InlineKeyboardButton("❌ لغو", callback_data="admin_stores")],
                ]),
                parse_mode='Markdown',
            )
            set_state(context, S_ADMIN_DELETE_STORE)
        return

    # ---- Admin: user edit fields ----
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

    # ---- Manager: remove employee ----
    if data == "mgr_employees":
        stores = get_user_managed_stores(uid)
        if not stores:
            await query.edit_message_text("❌ شما مدیر هیچ فروشگاهی نیستید.")
            return
        if len(stores) == 1:
            await show_store_employees(update, context, stores[0]["id"], edit=True)
        else:
            rows = [[InlineKeyboardButton(f"🏪 {s['name']}", callback_data=f"mgr_emp_store:{s['id']}")] for s in stores]
            await query.edit_message_text("کدام فروشگاه؟", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("mgr_emp_store:"):
        sid = int(data.split(":")[1])
        await show_store_employees(update, context, sid, edit=True)
        return

    if data.startswith("mgr_remove_emp:"):
        sid = int(data.split(":")[1])
        await show_store_employees(update, context, sid, edit=True)
        return

# -----------------------------
# Manager helpers
# -----------------------------

async def show_store_employees(update: Update, context: ContextTypes.DEFAULT_TYPE, store_id: int, edit: bool = False) -> None:
    """Show employees of a store with optional remove buttons."""
    uid = update.effective_user.id
    store = get_store(store_id)
    if not store:
        if edit:
            await update.callback_query.edit_message_text("❌ فروشگاه یافت نشد.")
        return
    if not is_owner(uid) and not user_is_store_manager(uid, store_id):
        if edit:
            await update.callback_query.edit_message_text("⛔ فقط مدیر فروشگاه یا ادمین می‌تواند.")
        return
    members = get_store_members(store_id)
    if not members:
        if edit:
            await update.callback_query.edit_message_text("هیچ عضوی نیست.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ بازگشت", callback_data="cancel_action")]]))
        return
    rows = []
    for m in members:
        role_icon = "👑" if m["role"] == ROLE_MANAGER else "🧑‍🔧"
        # Manager cannot remove themselves
        if int(m["telegram_id"]) == uid:
            rows.append([InlineKeyboardButton(f"{role_icon} {m['name']} (شما)", callback_data="noop")])
        else:
            label = f"❌ حذف {m['name']}" if m["role"] == ROLE_EMPLOYEE else f"📉 تنزل {m['name']} به کارمند"
            rows.append([InlineKeyboardButton(label, callback_data=f"mgr_act:{store_id}:{m['telegram_id']}")])
    rows.append([InlineKeyboardButton("⬅️ بازگشت", callback_data="cancel_action")])
    text = f"👥 اعضای فروشگاه **{store['name']}**:\n"
    for m in members:
        text += f"  {('👑' if m['role']==ROLE_MANAGER else '🧑‍🔧')} {m['name']} (id: {m['telegram_id']})\n"
    if edit:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows), parse_mode='Markdown')
    else:
        await update.callback_query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(rows), parse_mode='Markdown')

# -----------------------------
# Manager inline action handler (separate because logic is complex)
# -----------------------------

async def handle_manager_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for callback data like mgr_act:<store_id>:<user_id>."""
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    data = query.data or ""
    if not data.startswith("mgr_act:"):
        return
    _, sid_s, target_s = data.split(":")
    sid = int(sid_s)
    target = int(target_s)

    if not is_owner(uid) and not user_is_store_manager(uid, sid):
        await query.edit_message_text("⛔ دسترسی ندارید.")
        return

    target_role = get_user_role_in_store(target, sid)
    if target_role is None:
        await query.edit_message_text("❌ کاربر عضو این فروشگاه نیست.")
        return

    store = get_store(sid)
    if not store:
        await query.edit_message_text("❌ فروشگاه یافت نشد.")
        return

    if target_role == ROLE_EMPLOYEE:
        # Remove employee
        db_exec("DELETE FROM store_members WHERE store_id = ? AND user_id = ?", (sid, target))
        log_action(uid, "manager_remove_employee", f"store_id={sid}, target={target}")
        try:
            await context.bot.send_message(
                chat_id=target,
                text=f"ℹ️ شما از فروشگاه «{store['name']}» توسط مدیر حذف شدید."
            )
        except Exception:
            pass
        await query.edit_message_text("✅ کارمند حذف شد.")
        return
    elif target_role == ROLE_MANAGER:
        # Demote manager to employee (cannot fully remove, because store must have at least one manager conceptually,
        # but the admin flow handles store deletion). Other managers (if any) are unaffected.
        # We allow demotion but warn.
        db_exec("UPDATE store_members SET role = ? WHERE store_id = ? AND user_id = ?", (ROLE_EMPLOYEE, sid, target))
        log_action(uid, "manager_demote_manager", f"store_id={sid}, target={target}")
        try:
            await context.bot.send_message(
                chat_id=target,
                text=f"ℹ️ شما از مدیر به کارمند در فروشگاه «{store['name']}» تنزل یافتید."
            )
        except Exception:
            pass
        await query.edit_message_text("✅ مدیر به کارمند تنزل یافت.")
        return

# -----------------------------
# Admin store management inline handlers
# -----------------------------

async def handle_admin_store_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline buttons in admin-store flow: del_mgr_*, del_emp_*, del_confirm."""
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    if not is_owner(uid):
        await query.edit_message_text("⛔ دسترسی ندارید.")
        return
    data = query.data or ""
    temp = get_temp(context)
    sid = int(temp.get("del_store_id", 0))
    store = get_store(sid) if sid else None
    if not store:
        await query.edit_message_text("❌ فروشگاه یافت نشد.")
        return

    if data == "del_mgr_keep":
        # Manager stays as employee (he keeps membership, but no longer owner)
        # We just demote the existing manager to employee and proceed
        mgr = get_store_manager(sid)
        if mgr:
            db_exec("UPDATE store_members SET role = ? WHERE store_id = ? AND user_id = ?", (ROLE_EMPLOYEE, sid, int(mgr["telegram_id"])))
        temp["del_old_manager_action"] = "keep"
        await ask_about_employees_for_delete(update, context, sid)
        return

    if data == "del_mgr_demote":
        mgr = get_store_manager(sid)
        if mgr:
            db_exec("UPDATE store_members SET role = ? WHERE store_id = ? AND user_id = ?", (ROLE_EMPLOYEE, sid, int(mgr["telegram_id"])))
        temp["del_old_manager_action"] = "demote"
        await ask_about_employees_for_delete(update, context, sid)
        return

    if data == "del_mgr_remove":
        mgr = get_store_manager(sid)
        if mgr:
            db_exec("DELETE FROM store_members WHERE store_id = ? AND user_id = ?", (sid, int(mgr["telegram_id"])))
            try:
                await context.bot.send_message(
                    chat_id=int(mgr["telegram_id"]),
                    text=f"ℹ️ شما از فروشگاه «{store['name']}» توسط ادمین حذف شدید (فروشگاه در حال حذف است)."
                )
            except Exception:
                pass
        temp["del_old_manager_action"] = "remove"
        await ask_about_employees_for_delete(update, context, sid)
        return

    if data == "del_emp_remove_all":
        # Remove all employees
        emps = get_store_employees(sid)
        for e in emps:
            db_exec("DELETE FROM store_members WHERE store_id = ? AND user_id = ?", (sid, int(e["telegram_id"])))
            try:
                await context.bot.send_message(
                    chat_id=int(e["telegram_id"]),
                    text=f"ℹ️ شما از فروشگاه «{store['name']}» توسط ادمین حذف شدید (فروشگاه در حال حذف است)."
                )
            except Exception:
                pass
        temp["del_employees_action"] = "remove_all"
        await query.edit_message_text(
            f"🗑 تأیید نهایی حذف فروشگاه **{store['name']}**؟\n"
            "تمام محصولات فعال، pending_buys و عضویت‌ها حذف خواهند شد.\n"
            "خریدهای تکمیل‌شده (purchases) بدون تغییر می‌مانند.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ بله، حذف کن", callback_data="del_confirm")],
                [InlineKeyboardButton("❌ لغو", callback_data="admin_stores")],
            ]),
            parse_mode='Markdown',
        )
        set_state(context, S_ADMIN_DELETE_STORE)
        return

    if data == "del_emp_keep_all":
        temp["del_employees_action"] = "keep_all"
        await query.edit_message_text(
            f"🗑 تأیید نهایی حذف فروشگاه **{store['name']}**؟\n"
            "کارمندان از فروشگاه حذف می‌شوند ولی حساب کاربری‌شان باقی می‌ماند.\n"
            "خریدهای تکمیل‌شده (purchases) بدون تغییر می‌مانند.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ بله، حذف کن", callback_data="del_confirm")],
                [InlineKeyboardButton("❌ لغو", callback_data="admin_stores")],
            ]),
            parse_mode='Markdown',
        )
        set_state(context, S_ADMIN_DELETE_STORE)
        return

    if data == "del_emp_one_by_one":
        emps = get_store_employees(sid)
        if not emps:
            await query.edit_message_text("کارمندی برای تصمیم‌گیری نیست.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_stores")]]))
            return
        # Show employees with keep/remove buttons
        rows = [[InlineKeyboardButton(f"❌ حذف {e['name']} (id:{e['telegram_id']})", callback_data=f"del_emp_one:{e['telegram_id']}")] for e in emps]
        rows.append([InlineKeyboardButton("✅ همه را نگه دار", callback_data="del_emp_keep_all")])
        rows.append([InlineKeyboardButton("🗑 همه را حذف کن", callback_data="del_emp_remove_all")])
        rows.append([InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_stores")])
        await query.edit_message_text("برای هر کارمند تصمیم بگیرید:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("del_emp_one:"):
        target = int(data.split(":")[1])
        db_exec("DELETE FROM store_members WHERE store_id = ? AND user_id = ?", (sid, target))
        try:
            await context.bot.send_message(
                chat_id=target,
                text=f"ℹ️ شما از فروشگاه «{store['name']}» توسط ادمین حذف شدید."
            )
        except Exception:
            pass
        # Recompute list
        emps = get_store_employees(sid)
        if not emps:
            await query.edit_message_text(
                f"🗑 همه کارمندان تصمیم‌گیری شدند. تأیید حذف فروشگاه **{store['name']}**؟",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ بله، حذف کن", callback_data="del_confirm")],
                    [InlineKeyboardButton("❌ لغو", callback_data="admin_stores")],
                ]),
                parse_mode='Markdown',
            )
            set_state(context, S_ADMIN_DELETE_STORE)
            return
        rows = [[InlineKeyboardButton(f"❌ حذف {e['name']} (id:{e['telegram_id']})", callback_data=f"del_emp_one:{e['telegram_id']}")] for e in emps]
        rows.append([InlineKeyboardButton("✅ باقی‌مانده‌ها نگه داشته شوند", callback_data="del_emp_keep_remaining")])
        rows.append([InlineKeyboardButton("🗑 باقی‌مانده‌ها هم حذف شوند", callback_data="del_emp_remove_all")])
        await query.edit_message_text("ادامه:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data == "del_emp_keep_remaining":
        await query.edit_message_text(
            f"🗑 تأیید نهایی حذف فروشگاه **{store['name']}**؟\n"
            "خریدهای تکمیل‌شده (purchases) بدون تغییر می‌مانند.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ بله، حذف کن", callback_data="del_confirm")],
                [InlineKeyboardButton("❌ لغو", callback_data="admin_stores")],
            ]),
            parse_mode='Markdown',
        )
        set_state(context, S_ADMIN_DELETE_STORE)
        return

    if data == "del_confirm":
        # Final deletion
        # Cancel pending_buys
        db_exec("DELETE FROM pending_buys WHERE store_id = ?", (sid,))
        # Delete active products
        db_exec("DELETE FROM products WHERE store_id = ? AND status = 'active'", (sid,))
        # Mark sold products as deleted-archive? We'll keep them but with null store_id? Simpler: just remove store_id from purchases, keep purchase records.
        db_exec("UPDATE purchases SET store_id = NULL WHERE store_id = ?", (sid,))
        # Delete store (cascade would also remove store_members but we don't have ON DELETE CASCADE; clean manually)
        db_exec("DELETE FROM store_members WHERE store_id = ?", (sid,))
        db_exec("DELETE FROM stores WHERE id = ?", (sid,))
        log_action(uid, "admin_delete_store", f"store_id={sid}, name={store['name']}")
        set_state(context, None)
        await query.edit_message_text(
            f"✅ فروشگاه **{store['name']}** با موفقیت حذف شد.",
            reply_markup=admin_stores_kb(),
            parse_mode='Markdown',
        )
        return


async def ask_about_employees_for_delete(update: Update, context: ContextTypes.DEFAULT_TYPE, store_id: int) -> None:
    emps = get_store_employees(store_id)
    store = get_store(store_id)
    if not emps:
        # No employees, go to final confirm
        await update.callback_query.edit_message_text(
            f"🗑 تأیید نهایی حذف فروشگاه **{store['name']}**؟\n"
            "خریدهای تکمیل‌شده (purchases) بدون تغییر می‌مانند.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ بله، حذف کن", callback_data="del_confirm")],
                [InlineKeyboardButton("❌ لغو", callback_data="admin_stores")],
            ]),
            parse_mode='Markdown',
        )
        set_state(context, S_ADMIN_DELETE_STORE)
        return
    await update.callback_query.edit_message_text(
        f"👥 فروشگاه **{store['name']}** {len(emps)} کارمند دارد.\n"
        "تکلیف کارمندان چه باشد؟",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑 حذف همه کارمندان", callback_data="del_emp_remove_all")],
            [InlineKeyboardButton("✅ نگه داشتن همه", callback_data="del_emp_keep_all")],
            [InlineKeyboardButton("👤 تصمیم تک‌تک", callback_data="del_emp_one_by_one")],
            [InlineKeyboardButton("⬅️ بازگشت", callback_data="admin_stores")],
        ]),
        parse_mode='Markdown',
    )

# -----------------------------
# Main message router
# -----------------------------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    if not await check_access(update, context):
        return

    uid = update.effective_user.id
    text = normalize_text(update.message.text or update.message.caption)
    state = user_state(context)

    # Pass-through to ConversationHandler for backup/restore if user is in that flow
    if state in (S_ADMIN_BACKUP_IMPORT_FILE, S_RESTORE_ACCOUNT_FILE):
        if text in ["لغو", "بازگشت"]:
            set_state(context, None)
            clear_temp(context)
            await update.message.reply_text("❌ عملیات لغو شد.", reply_markup=main_menu_kb(uid))
            return
        # If a document comes, ConversationHandler will catch it; otherwise ignore
        if not update.message.document:
            await update.message.reply_text("لطفاً فایل JSON را ارسال کنید.")
        return

    if text in ["لغو", "بازگشت"]:
        log_action(uid, "cancelled_action_text", f"State was {state}")
        set_state(context, None)
        clear_temp(context)
        await update.message.reply_text("عملیات لغو شد. به منوی اصلی بازگشتید.", reply_markup=main_menu_kb(uid))
        return

    # If user is not registered
    if not get_user(uid):
        if await handle_registration(update, context, text):
            return
        return

    # Active states
    if state:
        if state in {S_REG_NAME, S_REG_NID, S_REG_ACCOUNT}:
            if await handle_registration(update, context, text):
                return

        if state in {S_ADD_NAME, S_ADD_PRICE, S_ADD_LINK, S_ADD_CHOOSE_STORE}:
            if await handle_add_product(update, context, text):
                return

        if state == S_USER_CREATE_STORE_NAME:
            # Create new store, user becomes its manager
            name = text
            existing = db_one("SELECT id FROM stores WHERE name = ?", (name,))
            if existing:
                await update.message.reply_text("❌ نام فروشگاه تکراری است. نام دیگری وارد کنید:", reply_markup=cancel_kb())
                return
            now = now_tehran()
            cur = db_exec(
                "INSERT INTO stores(name, owner_id, created_at, updated_at) VALUES(?, ?, ?, ?)",
                (name, uid, now, now),
            )
            new_id = cur.lastrowid if hasattr(cur, "lastrowid") else None
            if not new_id:
                row = db_one("SELECT id FROM stores WHERE name = ?", (name,))
                new_id = row["id"] if row else None
            if not new_id:
                await update.message.reply_text("❌ خطا در ساخت فروشگاه. دوباره تلاش کنید.")
                return
            db_exec(
                "INSERT INTO store_members(store_id, user_id, role, joined_at) VALUES(?, ?, ?, ?)",
                (new_id, uid, ROLE_MANAGER, now),
            )
            log_action(uid, "created_store", f"store_id={new_id}, name={name}")
            # Notify owner about the new store creation (and that user is now its manager)
            await notify_membership_change(context, new_id, uid, ROLE_MANAGER)
            set_state(context, None)
            await update.message.reply_text(
                f"✅ فروشگاه «{name}» ساخته شد و شما مدیر آن هستید.",
                reply_markup=main_menu_kb(uid),
            )
            return

        if state == S_BUY_CODE:
            product = db_one("SELECT * FROM products WHERE code = ?", (text,))
            if not product:
                await update.message.reply_text("محصولی با این کد پیدا نشد.", reply_markup=cancel_kb())
                return
            if product["status"] != "active":
                await update.message.reply_text("این محصول قبلاً فروخته شده یا درحال حاضر در دسترس نیست.", reply_markup=cancel_kb())
                return
            if not product["store_id"]:
                await update.message.reply_text("❌ خطا: فروشگاه این محصول نامشخص است.", reply_markup=cancel_kb())
                return
            store = get_store(int(product["store_id"]))
            if not store:
                await update.message.reply_text("❌ فروشگاه این محصول حذف شده است.", reply_markup=cancel_kb())
                return
            get_temp(context)["buy_product_code"] = product["code"]
            msg = (
                f"محصول {product['name']} از فروشگاه «{store['name']}»\n"
                f"قیمت: {fmt_money(int(product['price']))}\n"
                f"لینک: {product['link']}\n\n"
                "می‌خواهی بخری؟"
            )
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

        # Admin: get user id
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

        # Admin: create store
        if state == S_ADMIN_CREATE_STORE_NAME:
            name = text
            existing = db_one("SELECT id FROM stores WHERE name = ?", (name,))
            if existing:
                await update.message.reply_text("❌ نام فروشگاه تکراری است.", reply_markup=cancel_kb())
                return
            now = now_tehran()
            cur = db_exec(
                "INSERT INTO stores(name, owner_id, created_at, updated_at) VALUES(?, ?, ?, ?)",
                (name, uid, now, now),
            )
            new_id = cur.lastrowid if hasattr(cur, "lastrowid") else None
            if not new_id:
                row = db_one("SELECT id FROM stores WHERE name = ?", (name,))
                new_id = row["id"] if row else None
            if not new_id:
                await update.message.reply_text("❌ خطا در ساخت فروشگاه.")
                return
            db_exec(
                "INSERT INTO store_members(store_id, user_id, role, joined_at) VALUES(?, ?, ?, ?)",
                (new_id, uid, ROLE_MANAGER, now),
            )
            log_action(uid, "admin_create_store", f"store_id={new_id}, name={name}")
            # Notify owner about the new store (admin is owner, so this is a no-op for owner, but safe to call)
            await notify_membership_change(context, new_id, uid, ROLE_MANAGER)
            set_state(context, None)
            await update.message.reply_text(
                f"✅ فروشگاه «{name}» ساخته شد. شما مدیر آن هستید.",
                reply_markup=main_menu_kb(uid),
            )
            return

        # Admin: transfer target user id
        if state == S_ADMIN_TRANSFER_TARGET:
            target = int(text)
            target_user = get_user(target)
            if not target_user:
                await update.message.reply_text("❌ کاربر یافت نشد. دوباره آیدی عددی وارد کنید:", reply_markup=cancel_kb())
                return
            store_id = int(get_temp(context).get("xfer_store_id", 0))
            if not store_id or not get_store(store_id):
                await update.message.reply_text("❌ فروشگاه یافت نشد.", reply_markup=cancel_kb())
                return
            get_temp(context)["xfer_target_uid"] = target
            await update.message.reply_text(
                f"📤 انتقال فروشگاه **{get_store(store_id)['name']}**\n"
                f"به: {target_user['name']} (id: {target})\n\n"
                f"تأیید نهایی؟ تمام محصولات فعال منتقل می‌شوند و شماره حساب پرداخت به حساب مدیر جدید تغییر می‌کند.\n"
                f"خریدهای تکمیل‌شده (purchases) بدون تغییر می‌مانند.\n"
                f"خریدهای در جریان (pending) لغو می‌شوند.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ تأیید و انتقال", callback_data="adm_xfer_confirm")],
                    [InlineKeyboardButton("❌ لغو", callback_data="admin_stores")],
                ]),
                parse_mode='Markdown',
            )
            set_state(context, S_ADMIN_TRANSFER_ROLE)
            return

    # Main menu buttons
    if text == BTN_ADD:
        stores = get_user_stores(uid)
        if not stores:
            await update.message.reply_text("❌ شما عضو هیچ فروشگاهی نیستید. ابتدا از دکمه «🏪 افزودن فروشگاه» استفاده کنید.", reply_markup=main_menu_kb(uid))
            return
        if len(stores) == 1:
            get_temp(context)["add_store_id"] = stores[0]["id"]
            set_state(context, S_ADD_NAME)
            clear_temp(context)
            get_temp(context)["add_store_id"] = stores[0]["id"]
            await update.message.reply_text("نام محصول را وارد کن:", reply_markup=cancel_kb())
        else:
            # Show inline list of stores
            rows = [[InlineKeyboardButton(f"🏪 {s['name']} ({'مدیر' if s['role']=='manager' else 'کارمند'})", callback_data=f"add_pick_store:{s['id']}")] for s in stores]
            rows.append([InlineKeyboardButton("❌ لغو", callback_data="cancel_action")])
            await update.message.reply_text("محصول را به کدام فروشگاه اضافه کنم؟", reply_markup=InlineKeyboardMarkup(rows))
        return

    if text == BTN_BUY:
        set_state(context, S_BUY_CODE)
        clear_temp(context)
        await update.message.reply_text("کدیکتای محصول رو وارد کنید:", reply_markup=cancel_kb())
        return

    if text == BTN_VITRINE:
        # Show active products of stores the user belongs to
        stores = get_user_stores(uid)
        if not stores:
            await update.message.reply_text("شما عضو هیچ فروشگاهی نیستید.", reply_markup=main_menu_kb(uid))
            return
        any_product = False
        for s in stores:
            rows = db_all("SELECT * FROM products WHERE status = 'active' AND store_id = ? ORDER BY created_at DESC", (s["id"],))
            if not rows:
                continue
            any_product = True
            for r in rows:
                seller = get_user(r["seller_id"])
                seller_name = seller["name"] if seller else str(r["seller_id"])
                msg = (
                    f"🏪 فروشگاه: {s['name']}\n"
                    f"کد: {r['code']}\nنام: {r['name']}\nقیمت: {fmt_money(int(r['price']))} تومان\n"
                    f"لینک: {r['link']}\nثبت‌کننده: {seller_name}"
                )
                await update.message.reply_text(msg, reply_markup=product_actions_kb(r["code"], r["seller_id"], r["store_id"], uid))
        if not any_product:
            await update.message.reply_text("ویترین فروشگاه‌های شما خالی است.", reply_markup=main_menu_kb(uid))
        return

    if text == BTN_ASSETS:
        rows = db_all("SELECT p.* FROM purchases pu JOIN products p ON p.code = pu.product_code WHERE pu.buyer_id = ? ORDER BY pu.purchased_at DESC", (uid,))
        if not rows:
            await update.message.reply_text("سند محصولی برای شما ثبت نشده است!", reply_markup=main_menu_kb(uid))
            return
        for r in rows:
            store = get_store(r["store_id"]) if r["store_id"] else None
            store_name = store["name"] if store else "—"
            msg = f"کد: {r['code']}\nنام: {r['name']}\nقیمت: {fmt_money(int(r['price']))}\nلینک: {r['link']}\nفروشگاه: {store_name}"
            await update.message.reply_text(msg)
        return

    if text == BTN_ADD_STORE:
        if not is_owner(uid):
            await update.message.reply_text("⛔ این گزینه فقط برای مالک ربات فعال است.", reply_markup=main_menu_kb(uid))
            return
        # Show options: create new, or join existing
        await update.message.reply_text(
            "🏪 **مدیریت فروشگاه**\n\nیکی از گزینه‌ها را انتخاب کنید:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ ساخت فروشگاه جدید", callback_data="reg_new_store")],
                [InlineKeyboardButton("🔗 پیوستن به فروشگاه موجود", callback_data="add_pick_store_btn")],
                [InlineKeyboardButton("❌ لغو", callback_data="cancel_action")],
            ]),
            parse_mode='Markdown',
        )
        return

    if text == BTN_MY_STORES:
        stores = get_user_stores(uid)
        lines = []
        if stores:
            for s in stores:
                mgr = get_store_manager(s["id"])
                members = get_store_members(s["id"])
                role_label = "👑 مدیر" if s["role"] == ROLE_MANAGER else "🧑‍🔧 کارمند"
                mgr_name = mgr["name"] if mgr else "—"
                lines.append(f"#{s['id']} | {s['name']} | {role_label} | مدیر فعلی: {mgr_name} | {len(members)} عضو")
            text_out = "🏬 **فروشگاه‌های من**\n\n" + "\n".join(lines)
        else:
            text_out = "🏬 **فروشگاه‌های من**\n\nشما هنوز عضو هیچ فروشگاهی نیستید. می‌توانید از دکمه «دریافت فروشگاه‌های دیگر» یک فروشگاه را انتخاب کنید."

        kb_rows = [
            [InlineKeyboardButton("➕ دریافت فروشگاه‌های دیگر", callback_data="user_browse_stores")],
        ]
        # If user manages any store, show "manage employees" button
        managed = get_user_managed_stores(uid)
        if managed:
            if len(managed) == 1:
                kb_rows.append([InlineKeyboardButton(f"👥 مدیریت کارکنان «{managed[0]['name']}»", callback_data=f"mgr_emp_store:{managed[0]['id']}")])
            else:
                kb_rows.append([InlineKeyboardButton(f"👥 مدیریت کارکنان", callback_data="mgr_employees")])
        kb_rows.append([InlineKeyboardButton("❌ بستن", callback_data="cancel_action")])
        await update.message.reply_text(text_out, reply_markup=InlineKeyboardMarkup(kb_rows), parse_mode='Markdown')
        return

    if text == BTN_ADMIN and is_owner(uid):
        await admin_cmd(update, context)
        return

    await update.message.reply_text("لطفاً یک گزینه انتخاب کنید:", reply_markup=main_menu_kb(uid))

# -----------------------------
# Conversation Handlers
# -----------------------------

admin_backup_import_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(admin_backup_import_start, pattern="^admin_backup_import$")],
    states={
        S_ADMIN_BACKUP_IMPORT_FILE: [MessageHandler(filters.Document.ALL, admin_backup_import_file)],
        S_ADMIN_BACKUP_CONFIRM: [CallbackQueryHandler(admin_backup_import_confirm, pattern="^admin_backup_import_confirm$")],
    },
    fallbacks=[CommandHandler("start", start), CommandHandler("cancel", cancel)],
)

restore_account_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(restore_account_start, pattern="^restore_account$")],
    states={
        S_RESTORE_ACCOUNT_FILE: [MessageHandler(filters.Document.ALL, restore_account_file)],
        S_RESTORE_ACCOUNT_CONFIRM: [CallbackQueryHandler(restore_account_confirm, pattern="^restore_account_confirm$")],
    },
    fallbacks=[CommandHandler("start", start), CommandHandler("cancel", cancel)],
)

# Catch-all callback handler that runs the inline actions NOT covered above
async def handle_inline_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Single source of truth for inline button presses (admin store actions and manager actions)."""
    query = update.callback_query
    data = query.data or ""
    if data.startswith("mgr_act:"):
        await handle_manager_action(update, context)
    elif data.startswith("del_") or data == "del_confirm":
        await handle_admin_store_action(update, context)

# -----------------------------
# Main
# -----------------------------

def main() -> None:
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("cancel", cancel))

    app.add_handler(admin_backup_import_conv)
    app.add_handler(restore_account_conv)

    # Order matters: ConversationHandlers first, then main callback, then inline-action callback
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(CallbackQueryHandler(handle_inline_callbacks))

    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))

    logger.info("ربات v2 (Stores & Roles) راه‌اندازی شد.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
