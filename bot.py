#!/usr/bin/env python3
"""
Camelot (کملوت) Telegram Bot - Document & Property Registry Marketplace
Features:
- User registration (Camelot name, national code, bank account, telegram ID, username)
- Product marketplace (add, edit, delete, showcase)
- Purchase flow with bank transfer verification (12-char transaction code)
- Admin panel (owner only: 1275490079)
  - User management (list, edit name/account/username)
  - Blacklist management (block/unblock users)
  - Bot on/off switch (owner bypass)
  - Audit logs (all actions with Tehran time)
- Bank bot integration (@CamelotBank_bot) with 12-char transaction code verification
- In-memory database with audit logging
- Persian/English bilingual support
"""

import random
import string
import logging
import datetime
import json
import pytz
from copy import deepcopy
from typing import Dict, Any, Optional, List, Tuple

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters, ConversationHandler
)

# ============ CONFIG ============
BOT_TOKEN="***"
OWNER_ID = 1275490079
BANK_BOT_USERNAME = "CamelotBank_bot"
TEHRAN_TZ = pytz.timezone('Asia/Tehran')

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============ IN-MEMORY DATABASE ============
db: Dict[str, Any] = {
    'users': {},           # uid -> {kameloti_name, national_code, bank_account, telegram_id, username, registered_at}
    'products': {},        # code -> {seller_id, name, price, link, status, created_at, updated_at}
    'purchases': [],       # list of {product_code, buyer_id, txn_code, time, seller_id}
    'pendingBuys': {},     # uid -> {product_code, txn_code, step}
    'blacklist': set(),    # set of blocked uids
    'settings': {
        'bot_status': 'on',  # 'on' or 'off'
    },
    'audit_logs': [],      # list of {time, user_id, action, details, teheran_time}
}

# ============ UTILS ============
def tehran_now() -> str:
    """Current time in Tehran timezone as ISO string."""
    return datetime.datetime.now(TEHRAN_TZ).strftime("%Y-%m-%d %H:%M:%S")

def tehran_now_iso() -> str:
    """Current time in Tehran timezone as ISO format."""
    return datetime.datetime.now(TEHRAN_TZ).isoformat()

def make_code(length: int = 8) -> str:
    """Generate unique alphanumeric code (mixed case + digits)."""
    chars = string.ascii_letters + string.digits
    return ''.join(random.choices(chars, k=length))

def make_unique_code(length: int = 8, existing: Optional[set] = None) -> str:
    """Generate unique code not in existing set."""
    existing = existing or set(db['products'].keys())
    code = make_code(length)
    attempts = 0
    while code in existing and attempts < 100:
        code = make_code(length)
        attempts += 1
    return code

def fmt_price(amount: float) -> str:
    """Format price with Persian digit grouping."""
    return "{:,.0f}".format(amount)

def log_audit(user_id: int, action: str, details: Dict[str, Any] = None):
    """Log audit entry with Tehran timestamp."""
    entry = {
        'time': datetime.datetime.utcnow().isoformat(),
        'tehran_time': tehran_now(),
        'user_id': user_id,
        'action': action,
        'details': details or {}
    }
    db['audit_logs'].append(entry)
    # Keep last 10000 logs in memory
    if len(db['audit_logs']) > 10000:
        db['audit_logs'] = db['audit_logs'][-10000:]
    logger.info(f"AUDIT: {entry}")

def get_user_display(uid: int) -> str:
    """Get user display name (camelot name or telegram username)."""
    user = db['users'].get(uid)
    if user:
        return user.get('kameloti_name', user.get('username', f'User {uid}'))
    return f'User {uid}'

# ============ KEYBOARDS ============
def main_menu_kb() -> InlineKeyboardMarkup:
    """Main menu keyboard for regular users."""
    keyboard = [
        [InlineKeyboardButton("🛒 خرید محصول", callback_data="btn_buy"),
         InlineKeyboardButton("➕ افزودن محصول", callback_data="btn_add")],
        [InlineKeyboardButton("📦 لیست دارایی‌های من", callback_data="btn_assets"),
         InlineKeyboardButton("🏪 ویترین فروش", callback_data="btn_vitrine")],
    ]
    return InlineKeyboardMarkup(keyboard)

def admin_menu_kb() -> InlineKeyboardMarkup:
    """Admin panel keyboard."""
    bot_status = db['settings']['bot_status']
    status_text = "🟢 ربات روشن" if bot_status == 'on' else "🔴 ربات خاموش"
    keyboard = [
        [InlineKeyboardButton("👥 لیست کاربران", callback_data="admin_users"),
         InlineKeyboardButton("🛠 مدیریت کاربر", callback_data="admin_manage_user")],
        [InlineKeyboardButton("🚫 لیست سیاه", callback_data="admin_blacklist"),
         InlineKeyboardButton(status_text, callback_data="admin_toggle_bot")],
        [InlineKeyboardButton("📋 ثبت لاگ‌ها", callback_data="admin_logs"),
         InlineKeyboardButton("📊 آمار ربات", callback_data="admin_stats")],
        [InlineKeyboardButton("🏠 منوی اصلی", callback_data="admin_main_menu")],
    ]
    return InlineKeyboardMarkup(keyboard)

def confirm_kb(yes_data: str, no_data: str) -> InlineKeyboardMarkup:
    """Generic yes/no confirmation keyboard."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ آره", callback_data=yes_data),
        InlineKeyboardButton("❌ نه", callback_data=no_data)
    ]])

def product_actions_kb(code: str) -> InlineKeyboardMarkup:
    """Keyboard for product actions in showcase (edit/delete)."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ ویرایش", callback_data=f"edit_prod_{code}"),
         InlineKeyboardButton("🗑 حذف", callback_data=f"delete_prod_{code}")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="btn_vitrine")]
    ])

def edit_product_field_kb(code: str) -> InlineKeyboardMarkup:
    """Keyboard for choosing which field to edit."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 نام", callback_data=f"edit_field_name_{code}"),
         InlineKeyboardButton("💰 قیمت", callback_data=f"edit_field_price_{code}"),
         InlineKeyboardButton("🔗 لینک", callback_data=f"edit_field_link_{code}")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data=f"edit_prod_{code}")]
    ])

def blacklist_kb() -> InlineKeyboardMarkup:
    """Blacklist management keyboard."""
    keyboard = []
    for uid in sorted(db['blacklist']):
        user = db['users'].get(uid, {})
        name = user.get('kameloti_name', f'User {uid}')
        keyboard.append([InlineKeyboardButton(f"❌ {name} ({uid})", callback_data=f"unblacklist_{uid}")])
    keyboard.append([InlineKeyboardButton("➕ اضافه کردن به لیست سیاه", callback_data="blacklist_add")])
    keyboard.append([InlineKeyboardButton("🔙 بازگشت به پنل مدیریت", callback_data="admin_main_menu")])
    return InlineKeyboardMarkup(keyboard)

def user_management_kb(uid: int) -> InlineKeyboardMarkup:
    """User management keyboard for specific user."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 تغییر نام کملوتی", callback_data=f"edit_user_name_{uid}"),
         InlineKeyboardButton("🏦 تغییر شماره حساب", callback_data=f"edit_user_account_{uid}")],
        [InlineKeyboardButton("👤 تغییر یوزرنیم تلگرام", callback_data=f"edit_user_username_{uid}"),
         InlineKeyboardButton("🚫 اضافه به لیست سیاه", callback_data=f"blacklist_add_user_{uid}")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_users")]
    ])

def cancel_kb() -> InlineKeyboardMarkup:
    """Cancel button."""
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ لغو", callback_data="cancel_action")]])

# ============ MIDDLEWARE / ACCESS CHECK ============
async def check_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if user has access (not blacklisted, bot is on, or is owner)."""
uid = update.effective_user.id
    
    # Owner always has access
if uid == OWNER_ID:
        return True
    
    # Check blacklist
    if uid in db['blacklist']:
        msg = "🚫 شما در لیست سیاه هستید و نمی‌توانید از ربات استفاده کنید."
        if update.message:
            await update.message.reply_text(msg)
        elif update.callback_query:
            await update.callback_query.answer(msg, show_alert=True)
        log_audit(uid, 'access_denied_blacklist', {})
        return False
    
    # Check bot status
    if db['settings']['bot_status'] == 'off':
        msg = "🤖 ربات در حال حاضر خاموش است. لطفاً بعداً مراجعه کنید."
        if update.message:
            await update.message.reply_text(msg)
        elif update.callback_query:
            await update.callback_query.answer(msg, show_alert=True)
        log_audit(uid, 'access_denied_bot_off', {})
        return False
    
    return True

async def ensure_registered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Ensure user is registered, start registration if not."""
    uid = update.effective_user.id
    if uid in db['users']:
        return True
    
    # Start registration
    context.user_data['state'] = 'REG_NAME'
    context.user_data['temp'] = {}
    await update.message.reply_text(
        "سلام! به ربات ثبت اسناد و دارایی کملوت خوش آمدید ✅\n\n"
        "لطفاً نام کملوتی خود را وارد کنید:"
    )
    return False

# ============ COMMANDS ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    if not await check_access(update, context):
        return
    
    uid = update.effective_user.id
    user = update.effective_user
    
    # Register/update telegram info
    if uid in db['users']:
        db['users'][uid]['username'] = user.username or ''
        db['users'][uid]['telegram_id'] = uid
        context.user_data['state'] = 'IDLE'
        context.user_data['temp'] = {}
        log_audit(uid, 'start_command', {'returning_user': True})
        
        if uid == OWNER_ID:
            await update.message.reply_text(
                "👑 به پنل مدیریت ربات کملوت خوش آمدید!",
                reply_markup=admin_menu_kb()
            )
        else:
            await update.message.reply_text(
                "به ربات ثبت اسناد و دارایی کملوت خوش آمدید ✅",
                reply_markup=main_menu_kb()
            )
    else:
        context.user_data['state'] = 'REG_NAME'
        context.user_data['temp'] = {}
        log_audit(uid, 'start_registration', {'username': user.username or ''})
        await update.message.reply_text(
            "سلام! به ربات ثبت اسناد و دارایی کملوت خوش آمدید ✅\n\n"
            "لطفاً نام کملوتی خود را وارد کنید:"
        )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /cancel command - reset state."""
    if not await check_access(update, context):
        return
    
    uid = update.effective_user.id
    context.user_data['state'] = 'IDLE'
    context.user_data['temp'] = {}
    log_audit(uid, 'cancel_command', {})
    
    if uid == OWNER_ID:
        await update.message.reply_text("عملیات لغو شد.", reply_markup=admin_menu_kb())
    else:
        await update.message.reply_text("عملیات لغو شد.", reply_markup=main_menu_kb())

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin panel entry via /admin command."""
    if not await check_access(update, context):
        return
    
    uid = update.effective_user.id
    if uid != OWNER_ID:
        await update.message.reply_text("⛔️ شما دسترسی به پنل مدیریت ندارید.")
        log_audit(uid, 'admin_access_denied', {})
        return
    
    log_audit(uid, 'admin_panel_opened', {})
    await update.message.reply_text("👑 پنل مدیریت ربات کملوت", reply_markup=admin_menu_kb())

# ============ CALLBACK HANDLER ============
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all callback queries."""
    if not await check_access(update, context):
return
    
    query = update.callback_query
    uid = query.from_user.id
    data = query.data
    
    await query.answer()
    
    if 'temp' not in context.user_data:
        context.user_data['temp'] = {}
    
    # ============ MAIN MENU ACTIONS ============
    if data == "btn_add":
        context.user_data['state'] = 'ADD_NAME'
        context.user_data['temp'] = {}
        log_audit(uid, 'add_product_started', {})
        await query.edit_message_text("📝 نام محصول را وارد کنید:", reply_markup=cancel_kb())
    
    elif data == "btn_buy":
        context.user_data['state'] = 'BUY_CODE'
        context.user_data['temp'] = {}
        log_audit(uid, 'buy_product_started', {})
        await query.edit_message_text("🔍 کد یکتای محصول را وارد کنید:", reply_markup=cancel_kb())
    
    elif data == "btn_vitrine":
        await show_vitrine(query, uid)
    
    elif data == "btn_assets":
        await show_assets(query, uid)
    
    elif data == "admin_main_menu":
        if uid == OWNER_ID:
            context.user_data['state'] = 'IDLE'
            context.user_data['temp'] = {}
            await query.edit_message_text("👑 پنل مدیریت ربات کملوت", reply_markup=admin_menu_kb())
    
    # ============ ADMIN PANEL ============
    elif data == "admin_users" and uid == OWNER_ID:
        await show_admin_users(query)
    
    elif data == "admin_manage_user" and uid == OWNER_ID:
        context.user_data['state'] = 'ADMIN_GET_USER_ID'
        context.user_data['temp'] = {}
        await query.edit_message_text(
            "🆔 آیدی عددی تلگرامی کاربر را وارد کنید:",
            reply_markup=cancel_kb()
        )
    
    elif data == "admin_blacklist" and uid == OWNER_ID:
        await query.edit_message_text("🚫 مدیریت لیست سیاه", reply_markup=blacklist_kb())
    
    elif data == "admin_toggle_bot" and uid == OWNER_ID:
        await toggle_bot_status(query)
    
    elif data == "admin_logs" and uid == OWNER_ID:
        await show_audit_logs(query, page=0)
    
    elif data == "admin_stats" and uid == OWNER_ID:
        await show_bot_stats(query)
    
    elif data.startswith("admin_logs_page_") and uid == OWNER_ID:
        page = int(data.split("_")[-1])
        await show_audit_logs(query, page=page)
    
    # ============ BLACKLIST MANAGEMENT ============
    elif data == "blacklist_add" and uid == OWNER_ID:
        context.user_data['state'] = 'BLACKLIST_ADD_ID'
        context.user_data['temp'] = {}
        await query.edit_message_text(
            "🆔 آیدی عددی تلگرامی کاربر برای اضافه کردن به لیست سیاه را وارد کنید:",
            reply_markup=cancel_kb()
        )
    
    elif data.startswith("unblacklist_") and uid == OWNER_ID:
        target_uid = int(data.split("_")[1])
        db['blacklist'].discard(target_uid)
        log_audit(uid, 'blacklist_remove', {'target_user_id': target_uid})
        await query.edit_message_text("✅ کاربر از لیست سیاه حذف شد.", reply_markup=blacklist_kb())
    
    # ============ USER MANAGEMENT ============
    elif data.startswith("edit_user_name_") and uid == OWNER_ID:
        target_uid = int(data.split("_")[-1])
        context.user_data['state'] = 'EDIT_USER_NAME'
        context.user_data['temp'] = {'target_uid': target_uid}
        await query.edit_message_text(
            f"📝 نام کملوتی جدید برای کاربر {target_uid} را وارد کنید:",
            reply_markup=cancel_kb()
        )
    
    elif data.startswith("edit_user_account_") and uid == OWNER_ID:
        target_uid = int(data.split("_")[-1])
        context.user_data['state'] = 'EDIT_USER_ACCOUNT'
        context.user_data['temp'] = {'target_uid': target_uid}
        await query.edit_message_text(
            f"🏦 شماره حساب بانکی جدید برای کاربر {target_uid} را وارد کنید:",
            reply_markup=cancel_kb()
        )
    
    elif data.startswith("edit_user_username_") and uid == OWNER_ID:
        target_uid = int(data.split("_")[-1])
        context.user_data['state'] = 'EDIT_USER_USERNAME'
        context.user_data['temp'] = {'target_uid': target_uid}
await query.edit_message_text(
            f"👤 یوزرنیم تلگرامی جدید (بدون @) برای کاربر {target_uid} را وارد کنید:",
            reply_markup=cancel_kb()
        )
    
    elif data.startswith("blacklist_add_user_") and uid == OWNER_ID:
        target_uid = int(data.split("_")[-1])
        if target_uid == OWNER_ID:
            await query.answer("⛔️ نمی‌توانید مالک را به لیست سیاه اضافه کنید!", show_alert=True)
        else:
            db['blacklist'].add(target_uid)
            log_audit(uid, 'blacklist_add', {'target_user_id': target_uid})
            await query.edit_message_text("✅ کاربر به لیست سیاه اضافه شد.", reply_markup=blacklist_kb())
    
    # ============ PRODUCT MANAGEMENT ============
    elif data.startswith("edit_prod_"):
        code = data.split("_", 2)[-1]
        await show_product_edit_menu(query, code, uid)
    
    elif data.startswith("edit_field_name_"):
        code = data.split("_", 3)[-1]
        context.user_data['state'] = 'EDIT_PROD_NAME'
        context.user_data['temp'] = {'edit_code': code}
        await query.edit_message_text("📝 نام جدید محصول را وارد کنید:", reply_markup=cancel_kb())
    
    elif data.startswith("edit_field_price_"):
        code = data.split("_", 3)[-1]
        context.user_data['state'] = 'EDIT_PROD_PRICE'
        context.user_data['temp'] = {'edit_code': code}
        await query.edit_message_text("💰 قیمت جدید را وارد کنید (تومان):", reply_markup=cancel_kb())
    
    elif data.startswith("edit_field_link_"):
        code = data.split("_", 3)[-1]
        context.user_data['state'] = 'EDIT_PROD_LINK'
        context.user_data['temp'] = {'edit_code': code}
        await query.edit_message_text("🔗 لینک پست جدید را وارد کنید:", reply_markup=cancel_kb())
    
    elif data.startswith("delete_prod_"):
        code = data.split("_", 2)[-1]
        await confirm_delete_product(query, code, uid)
    
    elif data.startswith("confirm_delete_") and uid == OWNER_ID:
        code = data.split("_", 2)[-1]
        await delete_product(query, code, uid)
    
    # ============ ADD PRODUCT FLOW ============
    elif data == "add_yes":
        await confirm_add_product(query, uid)
    
    elif data == "add_no":
        context.user_data['state'] = 'IDLE'
        context.user_data['temp'] = {}
        await query.edit_message_text("❌ عملیات افزودن محصول لغو شد.", reply_markup=main_menu_kb())
    
    # ============ BUY PRODUCT FLOW ============
    elif data == "buy_yes":
        await confirm_buy_product(query, uid)
    
    elif data == "buy_no":
        context.user_data['state'] = 'IDLE'
        context.user_data['temp'] = {}
        await query.edit_message_text("❌ خرید لغو شد.", reply_markup=main_menu_kb())
    
    elif data == "cancel_action":
        context.user_data['state'] = 'IDLE'
        context.user_data['temp'] = {}
        if uid == OWNER_ID:
            await query.edit_message_text("❌ عملیات لغو شد.", reply_markup=admin_menu_kb())
        else:
            await query.edit_message_text("❌ عملیات لغو شد.", reply_markup=main_menu_kb())

# ============ SHOWCASE / VITRINE ============
async def show_vitrine(query, uid: int):
    """Display product showcase."""
    active_prods = [(code, p) for code, p in db['products'].items() if p['status'] == 'active']
    
    if not active_prods:
        await query.edit_message_text(
            "🏪 ویترین فروش خالی است.",
            reply_markup=main_menu_kb()
        )
        return
    
    text = "🏪 <b>ویترین فروش کملوت</b>\n\n"
    for code, p in active_prods:
        formatted_price = fmt_price(p['price'])
        seller_name = get_user_display(p['seller_id'])
        text += (
            f"🔹 <b>کد:</b> <code>{code}</code>\n"
            f"   <b>نام:</b> {p['name']}\n"
            f"   <b>قیمت:</b> {formatted_price} تومان\n"
            f"   <b>فروشنده:</b> {seller_name}\n"
            f"   <b>لینک:</b> <a href='{p['link']}'>مشاهده پست</a>\n\n"
        )
    
    # Add edit/delete buttons for own products
my_prods = [(code, p) for code, p in db['products'].items() if p['seller_id'] == uid and p['status'] == 'active']
    if my_prods and uid != OWNER_ID:
        text += "\n📝 <b>محصولات شما (برای ویرایش/حذف روی کد بزنید):</b>\n"
    
    await query.edit_message_text(
        text, parse_mode='HTML', disable_web_page_preview=True,
        reply_markup=main_menu_kb()
    )

async def show_assets(query, uid: int):
    """Show user's assets (purchased + own products)."""
    # Purchased products
    bought = [p for p in db['purchases'] if p['buyer_id'] == uid]
    # Own products
    my_prods = [(code, p) for code, p in db['products'].items() if p['seller_id'] == uid]
    
    text = "<b>📦 لیست دارایی‌های من</b>\n\n"
    text += "<b>🛒 محصولات خریداری شده:</b>\n"
    
    if bought:
        for b in bought:
            p = db['products'].get(b['product_code'])
            p_name = p['name'] if p else "محصول نامشخص"
            text += f"• {p_name} (کد: <code>{b['product_code']}</code>) - تاریخ: {b['time']}\n"
    else:
        text += "هیچ محصولی خریداری نکرده‌اید.\n"
    
    text += "\n<b>🏪 محصولات من در ویترین:</b>\n"
    if my_prods:
        for code, p in my_prods:
            status_text = "✅ فعال" if p['status'] == 'active' else "❌ فروخته شده"
            formatted_price = fmt_price(p['price'])
            text += f"• {p['name']} (کد: <code>{code}</code>) - {formatted_price} تومان [{status_text}]\n"
    else:
        text += "شما هیچ محصولی برای فروش نگذاشته‌اید."
    
    await query.edit_message_text(text, parse_mode='HTML', reply_markup=main_menu_kb())

async def show_product_edit_menu(query, code: str, uid: int):
    """Show edit/delete options for a product."""
    prod = db['products'].get(code)
    if not prod:
        await query.edit_message_text("❌ محصول یافت نشد.", reply_markup=main_menu_kb())
        return
    
    # Check ownership or admin
    if prod['seller_id'] != uid and uid != OWNER_ID:
        await query.answer("⛔️ شما مجاز به ویرایش این محصول نیستید.", show_alert=True)
        return
    
    formatted_price = fmt_price(prod['price'])
    status_text = "✅ فعال" if prod['status'] == 'active' else "❌ فروخته شده"
    
    text = (
        f"📦 <b>مدیریت محصول</b>\n\n"
        f"🔹 <b>کد:</b> <code>{code}</code>\n"
        f"🔹 <b>نام:</b> {prod['name']}\n"
        f"🔹 <b>قیمت:</b> {formatted_price} تومان\n"
        f"🔹 <b>لینک:</b> <a href='{prod['link']}'>مشاهده</a>\n"
        f"🔹 <b>وضعیت:</b> {status_text}\n"
    )
    
    await query.edit_message_text(text, parse_mode='HTML', disable_web_page_preview=True, reply_markup=product_actions_kb(code))

async def confirm_delete_product(query, code: str, uid: int):
    """Confirm product deletion."""
    prod = db['products'].get(code)
    if not prod:
        await query.edit_message_text("❌ محصول یافت نشد.", reply_markup=main_menu_kb())
        return
    
    if prod['seller_id'] != uid and uid != OWNER_ID:
        await query.answer("⛔️ شما مجاز به حذف این محصول نیستید.", show_alert=True)
        return
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ بله، حذف شود", callback_data=f"confirm_delete_{code}"),
         InlineKeyboardButton("❌ انصراف", callback_data=f"edit_prod_{code}")]
    ])
    
    await query.edit_message_text(
        f"⚠️ آیا مطمئن هستید که می‌خواهید محصول «{prod['name']}» (کد: <code>{code}</code>) را حذف کنید؟",
        parse_mode='HTML', reply_markup=kb
    )

async def delete_product(query, code: str, uid: int):
    """Delete a product."""
    prod = db['products'].pop(code, None)
    if prod:
        log_audit(uid, 'product_deleted', {'product_code': code, 'product_name': prod['name']})
        await query.edit_message_text("✅ محصول با موفقیت حذف شد.", reply_markup=main_menu_kb())
    else:
        await query.edit_message_text("❌ محصول یافت نشد.", reply_markup=main_menu_kb())

# ============ ADMIN PANEL FUNCTIONS ============
async def show_admin_users(query):
    """Show list of all registered users."""
    users = db['users']
    if not users:
await query.edit_message_text("👥 هیچ کاربری ثبت‌نام نکرده است.", reply_markup=admin_menu_kb())
        return
    
    text = "<b>👥 لیست کاربران ثبت‌نام شده</b>\n\n"
    for uid, user in users.items():
        username = user.get('username', 'ندارد')
        kameloti = user.get('kameloti_name', 'ندارد')
        national = user.get('national_code', 'ندارد')
        account = user.get('bank_account', 'ندارد')
        reg_date = user.get('registered_at', 'نامشخص')
        blacklisted = " 🚫" if uid in db['blacklist'] else ""
        owner_mark = " 👑" if uid == OWNER_ID else ""
        
        text += (
            f"🔹 <b>آیدی:</b> <code>{uid}</code>{blacklisted}{owner_mark}\n"
            f"   <b>یوزرنیم:</b> @{username}\n"
            f"   <b>نام کملوتی:</b> {kameloti}\n"
            f"   <b>کد ملی:</b> {national}\n"
            f"   <b>شماره حساب:</b> {account}\n"
            f"   <b>تاریخ ثبت‌نام:</b> {reg_date}\n\n"
        )
    
    # Split if too long
    if len(text) > 4000:
        # Send in chunks
        chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
        for i, chunk in enumerate(chunks):
            if i == len(chunks) - 1:
                await query.edit_message_text(chunk, parse_mode='HTML', reply_markup=admin_menu_kb())
            else:
                await query.message.reply_text(chunk, parse_mode='HTML')
    else:
        await query.edit_message_text(text, parse_mode='HTML', reply_markup=admin_menu_kb())

async def toggle_bot_status(query):
    """Toggle bot on/off status."""
    current = db['settings']['bot_status']
    new_status = 'off' if current == 'on' else 'on'
    db['settings']['bot_status'] = new_status
    status_text = "🟢 روشن" if new_status == 'on' else "🔴 خاموش"
    log_audit(query.from_user.id, 'bot_toggle', {'new_status': new_status})
    await query.edit_message_text(f"✅ ربات {status_text} شد.", reply_markup=admin_menu_kb())

async def show_audit_logs(query, page: int = 0):
    """Show paginated audit logs."""
    logs = db['audit_logs']
    if not logs:
        await query.edit_message_text("📋 هیچ لاگی ثبت نشده است.", reply_markup=admin_menu_kb())
        return
    
    per_page = 10
    total_pages = (len(logs) + per_page - 1) // per_page
    page = max(0, min(page, total_pages - 1))
    
    start = -(page + 1) * per_page
    end = -page * per_page if page > 0 else None
    page_logs = logs[start:end] if end else logs[start:]
    
    text = f"<b>📋 ثبت لاگ‌ها (صفحه {page + 1}/{total_pages})</b>\n\n"
    for log in reversed(page_logs):  # newest first
        time = log.get('tehran_time', log.get('time', 'نامشخص'))
        uid = log['user_id']
        action = log['action']
        details = log.get('details', {})
        user_name = get_user_display(uid)
        text += f"🕐 <code>{time}</code> | 👤 {user_name} (<code>{uid}</code>) | 🎬 {action}\n"
        if details:
            text += f"   📝 {json.dumps(details, ensure_ascii=False)}\n"
        text += "\n"
    
    # Pagination keyboard
    kb_rows = []
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"admin_logs_page_{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("➡️ بعدی", callback_data=f"admin_logs_page_{page+1}"))
    if nav:
        kb_rows.append(nav)
    kb_rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="admin_main_menu")])
    
    await query.edit_message_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(kb_rows))

async def show_bot_stats(query):
    """Show bot statistics."""
    total_users = len(db['users'])
    total_products = len(db['products'])
    active_products = len([p for p in db['products'].values() if p['status'] == 'active'])
    sold_products = len([p for p in db['products'].values() if p['status'] == 'sold'])
    total_purchases = len(db['purchases'])
    blacklisted = len(db['blacklist'])
    bot_status = db['settings']['bot_status']
    total_logs = len(db['audit_logs'])
    
    text = (
f"<b>📊 آمار ربات کملوت</b>\n\n"
        f"👥 کاربران کل: {total_users}\n"
        f"📦 کل محصولات: {total_products}\n"
        f"   ✅ فعال: {active_products}\n"
        f"   ❌ فروخته شده: {sold_products}\n"
        f"🛒 کل خریدها: {total_purchases}\n"
        f"🚫 کاربران مسدود: {blacklisted}\n"
        f"🤖 وضعیت ربات: {'🟢 روشن' if bot_status == 'on' else '🔴 خاموش'}\n"
        f"📋 کل لاگ‌ها: {total_logs}\n"
    )
    await query.edit_message_text(text, parse_mode='HTML', reply_markup=admin_menu_kb())

# ============ REGISTRATION FLOW ============
async def handle_registration(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Handle user registration flow."""
    uid = update.effective_user.id
    state = context.user_data.get('state')
    temp = context.user_data.setdefault('temp', {})
    
    if state == 'REG_NAME':
        temp['reg_kameloti'] = text
        context.user_data['state'] = 'REG_NATIONAL'
        await update.message.reply_text("📋 کد ملی خود را وارد کنید:")
    
    elif state == 'REG_NATIONAL':
        temp['reg_national'] = text
        context.user_data['state'] = 'REG_BANK'
        await update.message.reply_text("🏦 شماره حساب بانکی خود را وارد کنید:")
    
    elif state == 'REG_BANK':
        user = update.effective_user
        db['users'][uid] = {
            'kameloti_name': temp['reg_kameloti'],
            'national_code': temp['reg_national'],
            'bank_account': text,
            'telegram_id': uid,
            'username': user.username or '',
            'registered_at': tehran_now()
        }
        context.user_data['state'] = 'IDLE'
        context.user_data['temp'] = {}
        log_audit(uid, 'user_registered', {
            'kameloti_name': temp['reg_kameloti'],
            'national_code': temp['reg_national'],
            'bank_account': text
        })
        
        if uid == OWNER_ID:
            await update.message.reply_text(
                "✅ ثبت‌نام شما به عنوان مالک ربات انجام شد!",
                reply_markup=admin_menu_kb()
            )
        else:
            await update.message.reply_text(
                "✅ ثبت‌نام شما با موفقیت انجام شد!",
                reply_markup=main_menu_kb()
            )

# ============ ADD PRODUCT FLOW ============
async def handle_add_product(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Handle add product flow."""
    uid = update.effective_user.id
    state = context.user_data.get('state')
    temp = context.user_data.setdefault('temp', {})
    
    if state == 'ADD_NAME':
        temp['add_name'] = text
        context.user_data['state'] = 'ADD_PRICE'
        await update.message.reply_text("💰 مبلغ محصول را وارد کنید (تومان):")
    
    elif state == 'ADD_PRICE':
        try:
            price = float(text.replace(',', '').replace('،', ''))
            temp['add_price'] = price
            context.user_data['state'] = 'ADD_LINK'
            await update.message.reply_text("🔗 لینک پست محصول را ارسال کنید:")
        except ValueError:
            await update.message.reply_text("❌ لطفاً یک عدد معتبر وارد کنید:")
    
    elif state == 'ADD_LINK':
        temp['add_link'] = text
        context.user_data['state'] = 'ADD_CONFIRM'
        
        formatted_price = fmt_price(temp['add_price'])
        msg = (
            f"✅ <b>تایید افزودن محصول</b>\n\n"
            f"📝 <b>نام:</b> {temp['add_name']}\n"
            f"💰 <b>قیمت:</b> {formatted_price} تومان\n"
            f"🔗 <b>لینک:</b> <a href='{temp['add_link']}'>مشاهده</a>\n\n"
            f"آیا مطمئن هستید که می‌خواهید این محصول را به ویترین اضافه کنید؟"
        )
        await update.message.reply_text(msg, parse_mode='HTML', disable_web_page_preview=True, reply_markup=confirm_kb("add_yes", "add_no"))

async def confirm_add_product(query, uid: int):
    """Confirm and create product."""
    temp = context.user_data.get('temp', {})
    if not temp.get('add_name') or 'add_price' not in temp or not temp.get('add_link'):
await query.edit_message_text("❌ اطلاعات ناقص است. لطفاً دوباره تلاش کنید.", reply_markup=main_menu_kb())
        return
    
    # Generate unique 8-char code
    code = make_unique_code(8)
    
    db['products'][code] = {
        'seller_id': uid,
        'name': temp['add_name'],
        'price': temp['add_price'],
        'link': temp['add_link'],
        'status': 'active',
        'created_at': tehran_now(),
        'updated_at': tehran_now()
    }
    
    log_audit(uid, 'product_added', {
        'product_code': code,
        'name': temp['add_name'],
        'price': temp['add_price']
    })
    
    context.user_data['state'] = 'IDLE'
    context.user_data['temp'] = {}
    
    await query.edit_message_text(
        f"✅ محصول با موفقیت اضافه شد!\n"
        f"🔑 <b>کد یکتا:</b> <code>{code}</code>",
        parse_mode='HTML', reply_markup=main_menu_kb()
    )

# ============ EDIT PRODUCT FLOW ============
async def handle_edit_product(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Handle product editing flow."""
    uid = update.effective_user.id
    state = context.user_data.get('state')
    temp = context.user_data.setdefault('temp', {})
    code = temp.get('edit_code')
    
    if not code or code not in db['products']:
        context.user_data['state'] = 'IDLE'
        context.user_data['temp'] = {}
        await update.message.reply_text("❌ محصول یافت نشد یا منقضی شده.", reply_markup=main_menu_kb())
        return
    
    prod = db['products'][code]
    if prod['seller_id'] != uid and uid != OWNER_ID:
        await update.message.reply_text("⛔️ شما مجاز به ویرایش این محصول نیستید.", reply_markup=main_menu_kb())
        return
    
    if state == 'EDIT_PROD_NAME':
        old_name = prod['name']
        prod['name'] = text
        prod['updated_at'] = tehran_now()
        log_audit(uid, 'product_edited', {'product_code': code, 'field': 'name', 'old': old_name, 'new': text})
        context.user_data['state'] = 'IDLE'
        context.user_data['temp'] = {}
        await update.message.reply_text(f"✅ نام محصول به «{text}» تغییر یافت.", reply_markup=main_menu_kb())
    
    elif state == 'EDIT_PROD_PRICE':
        try:
            price = float(text.replace(',', '').replace('،', ''))
            old_price = prod['price']
            prod['price'] = price
            prod['updated_at'] = tehran_now()
            log_audit(uid, 'product_edited', {'product_code': code, 'field': 'price', 'old': old_price, 'new': price})
            context.user_data['state'] = 'IDLE'
            context.user_data['temp'] = {}
            await update.message.reply_text(f"✅ قیمت محصول به {fmt_price(price)} تومان تغییر یافت.", reply_markup=main_menu_kb())
        except ValueError:
            await update.message.reply_text("❌ لطفاً یک عدد معتبر وارد کنید:")
    
    elif state == 'EDIT_PROD_LINK':
        old_link = prod['link']
        prod['link'] = text
        prod['updated_at'] = tehran_now()
        log_audit(uid, 'product_edited', {'product_code': code, 'field': 'link', 'old': old_link, 'new': text})
        context.user_data['state'] = 'IDLE'
        context.user_data['temp'] = {}
        await update.message.reply_text("✅ لینک محصول به‌روزرسانی شد.", reply_markup=main_menu_kb())

# ============ BUY PRODUCT FLOW ============
async def handle_buy_product(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Handle buy product code entry."""
    uid = update.effective_user.id
    code = text.strip()
    prod = db['products'].get(code)
    
    if not prod or prod['status'] != 'active':
        await update.message.reply_text("❌ محصولی با این کد یافت نشد یا فروخته شده است. دوباره وارد کنید:")
        return
    
    if prod['seller_id'] == uid:
        await update.message.reply_text("⛔️ شما نمی‌توانید محصول خودتان را بخرید!")
        return
    
    # Store product for confirmation
    context.user_data['temp']['buy_prod'] = {**prod, 'code': code}
    context.user_data['state'] = 'BUY_CONFIRM'
    
    formatted_price = fmt_price(prod['price'])
seller_name = get_user_display(prod['seller_id'])
    
    msg = (
        f"🛒 <b>تایید خرید</b>\n\n"
        f"📦 <b>محصول:</b> {prod['name']}\n"
        f"💰 <b>قیمت:</b> {formatted_price} تومان\n"
        f"👤 <b>فروشنده:</b> {seller_name}\n"
        f"🔗 <b>لینک:</b> <a href='{prod['link']}'>مشاهده پست</a>\n\n"
        f"آیا این محصول را می‌خواهید بخرید؟"
    )
    await update.message.reply_text(msg, parse_mode='HTML', disable_web_page_preview=True, reply_markup=confirm_kb("buy_yes", "buy_no"))

async def confirm_buy_product(query, uid: int):
    """Confirm purchase and generate transaction code."""
    temp = context.user_data.get('temp', {})
    prod = temp.get('buy_prod')
    
    if not prod:
        await query.edit_message_text("❌ خطا: اطلاعات محصول یافت نشد.", reply_markup=main_menu_kb())
        return
    
    seller = db['users'].get(prod['seller_id'])
    bank_acc = seller['bank_account'] if seller else "ثبت نشده"
    txn_code = make_code(12)
    
    # Store pending purchase
    db['pendingBuys'][uid] = {
        'product_code': prod['code'],
        'txn_code': txn_code,
        'seller_id': prod['seller_id'],
        'price': prod['price']
    }
    
    context.user_data['state'] = 'BUY_RECEIPT'
    
    formatted_price = fmt_price(prod['price'])
    text = (
        f"💳 <b>مراحل پرداخت</b>\n\n"
        f"1️⃣ به ربات بانک بروید: @{BANK_BOT_USERNAME}\n"
        f"2️⃣ مبلغ <b>{formatted_price} تومان</b> را به شماره حساب زیر واریز کنید:\n"
        f"🏦 <code>{bank_acc}</code>\n\n"
        f"⚠️ <b>نکته بسیار مهم:</b>\n"
        f"در بخش <b>توضیحات/شرح تراکنش</b> حتماً کد زیر را وارد کنید:\n"
        f"<code>{txn_code}</code>\n\n"
        f"❌ <b>بدون این کد، پرداخت شناسایی نمی‌شود و پول شما برگردانده نمی‌شود!</b>\n\n"
        f"✅ پس از واریز، <b>فاکتور/رسید را مستقیماً از ربات بانک به این ربات فوروارد کنید</b> "
        f"(به طوری که مشخص باشد پیام از ربات بانک فوروارد شده است)."
    )
    
    log_audit(uid, 'purchase_initiated', {
        'product_code': prod['code'],
        'txn_code': txn_code,
        'price': prod['price'],
        'seller_id': prod['seller_id']
    })
    
    await query.edit_message_text(text, parse_mode='HTML')

async def handle_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle forwarded bank receipt verification."""
    uid = update.effective_user.id
    msg = update.message
    
    # Check if forwarded from bank bot
    if not msg.forward_origin or not hasattr(msg.forward_origin, 'sender_user'):
        await msg.reply_text("❌ فاکتور ارسالی نامعتبر است! پیام باید مستقیماً از ربات بانک (@CamelotBank_bot) فوروارد شده باشد.")
        return
    
    forward_sender = msg.forward_origin.sender_user
    if forward_sender.username != BANK_BOT_USERNAME:
        await msg.reply_text(f"❌ پیام باید از ربات بانک (@{BANK_BOT_USERNAME}) فوروارد شده باشد!")
        return
    
    # Get pending purchase
    pending = db['pendingBuys'].get(uid)
    if not pending:
        await msg.reply_text("❌ هیچ خرید معلقی یافت نشد.", reply_markup=main_menu_kb())
        return
    
    # Check for 12-char transaction code in message text
    text = msg.text or msg.caption or ""
    txn_code = pending['txn_code']
    if txn_code not in text:
        await msg.reply_text(f"❌ کد ۱۲ رقمی (<code>{txn_code}</code>) در فاکتور یافت نشد.", parse_mode='HTML')
        return
    
    # Verify seller's bank account in receipt
    prod = db['products'].get(pending['product_code'])
    if not prod:
        await msg.reply_text("❌ محصول یافت نشد.", reply_markup=main_menu_kb())
        return
    
    seller = db['users'].get(prod['seller_id'])
    if seller and seller['bank_account'] not in text:
        await msg.reply_text("❌ شماره حساب فروشنده در فاکتور یافت نشد. فاکتور نامعتبر است.")
        return
    
    # All checks passed - complete purchase
    prod['status'] = 'sold'
    purchase_record = {
        'product_code': pending['product_code'],
        'buyer_id': uid,
        'seller_id': prod['seller_id'],
        'txn_code': txn_code,
'time': tehran_now(),
        'price': prod['price']
    }
    db['purchases'].append(purchase_record)
    del db['pendingBuys'][uid]
    
    context.user_data['state'] = 'IDLE'
    context.user_data['temp'] = {}
    
    log_audit(uid, 'purchase_completed', {
        'product_code': prod['code'],
        'txn_code': txn_code,
        'seller_id': prod['seller_id'],
        'price': prod['price']
    })
    
    # Notify buyer
    await msg.reply_text(
        "✅ پرداخت تایید شد و محصول به دارایی‌های شما اضافه شد.",
        reply_markup=main_menu_kb()
    )
    
    # Notify seller
    buyer = db['users'].get(uid)
    buyer_name = buyer['kameloti_name'] if buyer else f"User {uid}"
    buyer_account = buyer['bank_account'] if buyer else "ثبت نشده"
    
    try:
        await context.bot.send_message(
            chat_id=prod['seller_id'],
            text=(
                f"🔔 <b>محصول شما فروخته شد!</b>\n\n"
                f"📦 <b>محصول:</b> {prod['name']}\n"
                f"🔑 <b>کد:</b> <code>{prod['code']}</code>\n"
                f"💰 <b>قیمت:</b> {fmt_price(prod['price'])} تومان\n"
                f"👤 <b>خریدار:</b> {buyer_name}\n"
                f"🏦 <b>شماره حساب خریدار:</b> {buyer_account}\n"
                f"🕐 <b>تاریخ و ساعت (تهران):</b> {tehran_now()}\n\n"
                f"⚖️ در صورت عدم واریز وجه به حساب شما، می‌توانید شکایت خود را در دادگاه عدالت کملوت ثبت کنید."
            ),
            parse_mode='HTML'
        )
    except Exception as e:
        logger.warning(f"Failed to notify seller {prod['seller_id']}: {e}")

# ============ ADMIN USER MANAGEMENT FLOW ============
async def handle_admin_manage_user(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Handle admin user management flows."""
    uid = update.effective_user.id
    state = context.user_data.get('state')
    temp = context.user_data.setdefault('temp', {})
    
    if state == 'ADMIN_GET_USER_ID':
        try:
            target_uid = int(text.strip())
        except ValueError:
            await update.message.reply_text("❌ آیدی عددی معتبر وارد کنید:")
            return
        
        if target_uid not in db['users']:
            await update.message.reply_text("❌ کاربری با این آیدی یافت نشد.", reply_markup=admin_menu_kb())
            return
        
        context.user_data['state'] = 'IDLE'
        context.user_data['temp'] = {}
        await update.message.reply_text(
            f"👤 مدیریت کاربر <code>{target_uid}</code>\n\n"
            f"چه تغییری می‌خواهید انجام دهید؟",
            parse_mode='HTML', reply_markup=user_management_kb(target_uid)
        )
    
    elif state == 'EDIT_USER_NAME':
        target_uid = temp.get('target_uid')
        if target_uid in db['users']:
            old_name = db['users'][target_uid].get('kameloti_name', '')
            db['users'][target_uid]['kameloti_name'] = text
            log_audit(uid, 'admin_edit_user_name', {
                'target_user_id': target_uid, 'old': old_name, 'new': text
            })
            await update.message.reply_text(f"✅ نام کملوتی کاربر {target_uid} به «{text}» تغییر یافت.", reply_markup=admin_menu_kb())
        else:
            await update.message.reply_text("❌ کاربر یافت نشد.", reply_markup=admin_menu_kb())
        context.user_data['state'] = 'IDLE'
        context.user_data['temp'] = {}
    
    elif state == 'EDIT_USER_ACCOUNT':
        target_uid = temp.get('target_uid')
        if target_uid in db['users']:
            old_acc = db['users'][target_uid].get('bank_account', '')
            db['users'][target_uid]['bank_account'] = text
            log_audit(uid, 'admin_edit_user_account', {
                'target_user_id': target_uid, 'old': old_acc, 'new': text
            })
            await update.message.reply_text(f"✅ شماره حساب کاربر {target_uid} به‌روزرسانی شد.", reply_markup=admin_menu_kb())
        else:
            await update.message.reply_text("❌ کاربر یافت نشد.", reply_markup=admin_menu_kb())
        context.user_data['state'] = 'IDLE'
context.user_data['temp'] = {}
    
    elif state == 'EDIT_USER_USERNAME':
        target_uid = temp.get('target_uid')
        if target_uid in db['users']:
            old_user = db['users'][target_uid].get('username', '')
            db['users'][target_uid]['username'] = text.lstrip('@')
            log_audit(uid, 'admin_edit_user_username', {
                'target_user_id': target_uid, 'old': old_user, 'new': text
            })
            await update.message.reply_text(f"✅ یوزرنیم تلگرام کاربر {target_uid} به @{text} تغییر یافت.", reply_markup=admin_menu_kb())
        else:
            await update.message.reply_text("❌ کاربر یافت نشد.", reply_markup=admin_menu_kb())
        context.user_data['state'] = 'IDLE'
        context.user_data['temp'] = {}

# ============ BLACKLIST FLOW ============
async def handle_blacklist_add(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    """Handle adding user to blacklist."""
    uid = update.effective_user.id
    try:
        target_uid = int(text.strip())
    except ValueError:
        await update.message.reply_text("❌ آیدی عددی معتبر وارد کنید:")
        return
    
    if target_uid == OWNER_ID:
        await update.message.reply_text("⛔️ نمی‌توانید مالک را به لیست سیاه اضافه کنید!", reply_markup=admin_menu_kb())
    else:
        db['blacklist'].add(target_uid)
        log_audit(uid, 'blacklist_add', {'target_user_id': target_uid})
        await update.message.reply_text(f"✅ کاربر {target_uid} به لیست سیاه اضافه شد.", reply_markup=admin_menu_kb())
    
    context.user_data['state'] = 'IDLE'
    context.user_data['temp'] = {}

# ============ MAIN TEXT HANDLER ============
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main text message handler - routes to appropriate flow."""
    if not await check_access(update, context):
        return
    
    uid = update.effective_user.id
    state = context.user_data.get('state', 'IDLE')
    text = (update.message.text or update.message.caption or "").strip()
    
    if 'temp' not in context.user_data:
        context.user_data['temp'] = {}
    
    # Handle /cancel-like text
    if text in ['/cancel', 'لغو', 'cancel']:
        context.user_data['state'] = 'IDLE'
        context.user_data['temp'] = {}
        await update.message.reply_text("❌ عملیات لغو شد.", reply_markup=main_menu_kb() if uid != OWNER_ID else admin_menu_kb())
        return
    
    # ============ REGISTRATION ============
    if state.startswith('REG_'):
        await handle_registration(update, context, text)
        return
    
    # ============ ADD PRODUCT ============
    if state.startswith('ADD_') or state == 'ADD_CONFIRM':
        await handle_add_product(update, context, text)
        return
    
    # ============ EDIT PRODUCT ============
    if state.startswith('EDIT_PROD_'):
        await handle_edit_product(update, context, text)
        return
    
    # ============ BUY PRODUCT ============
    if state == 'BUY_CODE':
        await handle_buy_product(update, context, text)
        return
    
    if state == 'BUY_RECEIPT':
        await handle_receipt(update, context)
        return
    
    # ============ ADMIN FLOWS ============
    if uid == OWNER_ID:
        if state == 'ADMIN_GET_USER_ID':
            await handle_admin_manage_user(update, context, text)
            return
        if state in ('EDIT_USER_NAME', 'EDIT_USER_ACCOUNT', 'EDIT_USER_USERNAME'):
            await handle_admin_manage_user(update, context, text)
            return
        if state == 'BLACKLIST_ADD_ID':
            await handle_blacklist_add(update, context, text)
            return
    
    # Default: unknown state or idle
    if state != 'IDLE':
        logger.warning(f"Unknown state {state} for user {uid}")
        context.user_data['state'] = 'IDLE'
        context.user_data['temp'] = {}
    
    # Show main menu for idle users
    if uid == OWNER_ID:
        await update.message.reply_text("👑 پنل مدیریت", reply_markup=admin_menu_kb())
    else:
await update.message.reply_text("🏠 منوی اصلی", reply_markup=main_menu_kb())

# ============ ERROR HANDLER ============
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Log errors."""
    logger.error(f"Exception while handling update: {context.error}", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "❌ خطای داخلی رخ داد. لطفاً دوباره تلاش کنید یا با پشتیبانی تماس بگیرید."
            )
        except Exception:
            pass

# ============ MAIN ============
def main():
    """Initialize and run the bot."""
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("admin", admin_cmd))
    
    # Callback handler
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    # Message handler (all non-command messages)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_text))
    
    # Error handler
    app.add_error_handler(error_handler)
    
    logger.info("Bot is starting...")
    print("🤖 Camelot Bot is running...")
    app.run_polling()

if __name__ == '__main__':
    main()
