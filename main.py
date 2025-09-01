import sqlite3
import logging
import re
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from telegram.constants import ParseMode

# -----------------------------------------------------------------------------
# Bot Configuration and Initialization
# -----------------------------------------------------------------------------

# Set up logging for better error tracking and debugging.
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Essential Bot Credentials and Database Path.
BOT_TOKEN = "8439068545:AAFe_SlJuLJp7-ue4rZQljN6WVl_GFPT_l4"
DB_PATH = "bot_data.db"

# User state management dictionary to track multi-step conversations.
user_states = {}

# Admin IDs list for access control.
# Note: The admin management is also handled via the database settings.
ADMIN_IDS = {7509255483}
ADMIN_GROUP_ID = -4947085075

# Constants for database settings keys.
SETTING_SUPPORT = "support_user"
SETTING_SHAM_CODE = "sham_code"
SETTING_SHAM_ADDR = "sham_address"
SETTING_GROUP_TOPUP = "group_topup"
SETTING_GROUP_ORDERS = "group_orders"
SETTING_ADMINS = "admins"
SETTING_GROUP_SUBS = "group_subscriptions"
SETTING_GROUP_EXPIRE = "group_subscription_expire"

# -----------------------------------------------------------------------------
# Database Helper Functions
# These functions provide a clean interface for all database operations.
# -----------------------------------------------------------------------------

def db():
    """Connect to the SQLite database and set the row factory to dict-like objects."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initializes the database by creating all necessary tables."""
    conn = db()
    cur = conn.cursor()

    # Users table to store user information, balance, and admin status.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance REAL DEFAULT 0.0,
            is_admin BOOLEAN DEFAULT 0
        );
    """)

    # Categories table for product organization (main and sub-categories).
    cur.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            parent_id INTEGER NULL,
            FOREIGN KEY(parent_id) REFERENCES categories(id) ON DELETE CASCADE
        );
    """)

    # Products table to store product details, price, and stock.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            stock INTEGER DEFAULT 0,
            min_qty INTEGER NULL,
            max_qty INTEGER NULL,
            product_type TEXT DEFAULT 'regular',
            FOREIGN KEY(category_id) REFERENCES categories(id) ON DELETE CASCADE
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    

    # Top-ups table to log all top-up requests.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS topups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            op_number TEXT NOT NULL,
            amount REAL NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        );
    """)
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)

    # Orders table to log all product purchase orders.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            price REAL NOT NULL,
            contact TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(user_id),
            FOREIGN KEY(product_id) REFERENCES products(id)
        );
    """)

    # Settings table for storing key-value pairs of bot settings.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    
    # Subscriptions table for managing time-based services.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(user_id),
            FOREIGN KEY(product_id) REFERENCES products(id)
        );
    """)

    # Commits all changes and closes the connection.
    conn.commit()
    conn.close()

    # Update the in-memory admin list from the database.
    update_admins_list()

def add_admin(user_id: int):
    """Adds a user ID to the administrators list in the database."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()

def remove_admin(user_id: int):
    """Removes a user ID from the administrators list."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def update_admins_list():
    """Fetches the admin IDs from the database and updates the global set."""
    global ADMIN_IDS
    admins_str = get_setting(SETTING_ADMINS)
    if admins_str:
        try:
            ADMIN_IDS.update(int(uid.strip()) for uid in admins_str.split(',') if uid.strip())
        except ValueError:
            logger.error("Invalid ADMINS setting format. Please use comma-separated integers.")

def is_admin(user_id: int) -> bool:
    """Checks if a user is an administrator."""
    return user_id in ADMIN_IDS

def ensure_user(user):
    """Ensures a user exists in the database. Adds them if they don't."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT * FROM users WHERE user_id=?", (user.id,))
    existing_user = cur.fetchone()

    if existing_user is None:
        cur.execute("INSERT INTO users(user_id, username) VALUES(?,?)",
                    (user.id, user.username))
        conn.commit()
    elif existing_user['username'] != user.username:
        cur.execute("UPDATE users SET username=? WHERE user_id=?",
                    (user.username, user.id))
        conn.commit()

    conn.close()

def get_user(user_id: int) -> sqlite3.Row | None:
    """Retrieves a single user's information by their ID."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row

def get_balance(user_id):
    """Retrieves the current balance of a user."""
    user = get_user(user_id)
    return user['balance'] if user else 0.0

def change_balance(user_id: int, amount: float) -> float:
    """Adds or subtracts an amount from a user's balance."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("UPDATE users SET balance = balance + ? WHERE user_id=?",
                (amount, user_id))
    conn.commit()
    conn.close()
    u = get_user(user_id)
    return u['balance'] if u else 0.0

def get_setting(key: str) -> str | None:
    """Retrieves a specific setting's value from the database."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = cur.fetchone()
    conn.close()
    return row['value'] if row else None

def set_setting(key: str, value: str):
    """Sets or updates a specific setting's value in the database."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?,?)",
                (key, value))
    conn.commit()
    conn.close()
    if key == SETTING_ADMINS:
        update_admins_list()

def money(amount):
    """Formats an amount into a currency string. Converts integers to strings without decimal points."""
    if amount == int(amount):
        return f"{int(amount)} ل.س "
    else:
        return f"{amount} $"

def get_categories(parent_id: int | None = None) -> list[sqlite3.Row]:
    """Retrieves a list of categories based on their parent ID."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    if parent_id is None:
        cur.execute("SELECT * FROM categories WHERE parent_id IS NULL ORDER BY id ASC")
    else:
        cur.execute("SELECT * FROM categories WHERE parent_id=? ORDER BY id ASC", (parent_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

def get_sub_categories(parent_id: int) -> list[sqlite3.Row]:
    """Retrieves all sub-categories for a given parent ID."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM categories WHERE parent_id=? ORDER BY id ASC", (parent_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

def get_category(cat_id: int) -> sqlite3.Row | None:
    """Retrieves a single category by its ID."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM categories WHERE id=?", (cat_id,))
    row = cur.fetchone()
    conn.close()
    return row

def get_products_by_cat(cat_id: int) -> list[sqlite3.Row]:
    """Retrieves all products belonging to a specific category."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM products WHERE category_id=?", (cat_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

def get_product(prod_id: int) -> sqlite3.Row | None:
    """Retrieves a single product by its ID."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM products WHERE id=?", (prod_id,))
    row = cur.fetchone()
    conn.close()
    return row

def decrement_product_stock(prod_id: int, quantity: int = 1) -> bool:
    """Decrements the stock of a product by a given quantity."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT stock FROM products WHERE id=?", (prod_id,))
    stock_row = cur.fetchone()
    if stock_row is None:
        conn.close()
        return False
    stock = stock_row[0]
    if stock >= quantity:
        cur.execute("UPDATE products SET stock = stock - ? WHERE id=?", (quantity, prod_id,))
        conn.commit()
        conn.close()
        return True
    conn.close()
    return False

# -----------------------------------------------------------------------------
# Inline Keyboard Markup Definitions
# -----------------------------------------------------------------------------

# Main menu keyboard.
MAIN_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("🛍️ شراء منتج", callback_data="BUY"), InlineKeyboardButton("💳 شحن شام كاش", callback_data="TOPUP_MENU")],
    [InlineKeyboardButton("🆘 التواصل مع الدعم", callback_data="SUPPORT_CONTACT"), InlineKeyboardButton("👤 معلومات الحساب", callback_data="ACCOUNT")],
    [InlineKeyboardButton("🗞️ الأخبار", callback_data="NEWS")],
])

def admin_menu_kb() -> InlineKeyboardMarkup:
    """Admin main menu keyboard with 2 buttons per row."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 إدارة القوائم", callback_data="ADM_CATS"), InlineKeyboardButton("📦 إدارة المنتجات", callback_data="ADM_PRODS")],
        [InlineKeyboardButton("👥 إدارة المستخدمين", callback_data="ADM_USERS"), InlineKeyboardButton("⚙️ الإعدادات", callback_data="ADM_SETTINGS")],
        [InlineKeyboardButton("📢 بث رسالة", callback_data="ADM_BROADCAST") , InlineKeyboardButton("📝 تعديل الأخبار", callback_data="EDIT_NEWS")],
    ])

def cats_menu_kb() -> InlineKeyboardMarkup:
    """Admin categories menu keyboard."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 إدارة القوائم الرئيسية 🔑", callback_data="ADM_MAIN_CATS"), InlineKeyboardButton("🗝 إدارة القوائم الفرعية 🗝", callback_data="ADM_SUB_CATS")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="ADM_BACK")],
    ])

def main_cats_kb() -> InlineKeyboardMarkup:
    """Admin main categories management keyboard."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إضافة قائمة رئيسية", callback_data="CAT_ADD_MAIN")],
        [InlineKeyboardButton("🔃 تعديل قائمة رئيسية", callback_data="CAT_EDIT_MAIN")],
        [InlineKeyboardButton("❌ حذف قائمة رئيسية", callback_data="CAT_DEL_MAIN")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="ADM_BACK_CATS")],
    ])

def sub_cats_kb() -> InlineKeyboardMarkup:
    """Admin sub-categories management keyboard."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إضافة قائمة فرعية", callback_data="CAT_ADD_SUB")],
        [InlineKeyboardButton("🔃 تعديل قائمة فرعية", callback_data="CAT_EDIT_SUB")],
        [InlineKeyboardButton("❌ حذف قائمة فرعية", callback_data="CAT_DEL_SUB")],
        [InlineKeyboardButton("🔄 تغيير القائمة الأم", callback_data="CAT_MOVE_SUB")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="ADM_BACK_CATS")],
    ])

def prods_menu_kb() -> InlineKeyboardMarkup:
    """Admin products management keyboard."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إضافة منتج", callback_data="PROD_ADD")],
        [InlineKeyboardButton("🔃 تعديل اسم منتج", callback_data="PROD_EDIT_NAME_LIST")],
        [InlineKeyboardButton("💲 تعديل سعر منتج", callback_data="PROD_EDIT_PRICE_LIST")],
        [InlineKeyboardButton("🔄 نقل منتج لقائمة أخرى", callback_data="PROD_MOVE_LIST")],
        [InlineKeyboardButton("🗑 حذف منتج", callback_data="PROD_DEL_LIST")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="ADM_BACK")],
    ])

def users_menu_kb() -> InlineKeyboardMarkup:
    """Admin user management keyboard."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕شحن رصيد", callback_data="USR_CREDIT")],
        [InlineKeyboardButton("➖سحب رصيد", callback_data="USR_DEBIT")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="ADM_BACK")],
    ])

def settings_menu_kb() -> InlineKeyboardMarkup:
    """Admin settings keyboard."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🆘 يوزر الدعم", callback_data="SET_SUPPORT"), InlineKeyboardButton("📮 كود شام كاش", callback_data="SET_SHAM_CODE")],
        [InlineKeyboardButton("📍 عنوان شام كاش", callback_data="SET_SHAM_ADDR"), InlineKeyboardButton("🆔 آيدي مجموعة الشحن", callback_data="SET_GROUP_TOPUP")],
        [InlineKeyboardButton("🆔 آيدي مجموعة الطلبات", callback_data="SET_GROUP_ORDERS"), InlineKeyboardButton("🆔 آيديات الأدمن 🆔", callback_data="SET_ADMINS")],
        [InlineKeyboardButton("⬅️ رجوع", callback_data="ADM_BACK")],
    ])

# -----------------------------------------------------------------------------
# Text Helper Functions
# -----------------------------------------------------------------------------


def start_text(u_row: sqlite3.Row) -> str:
    """Generates the welcome message for the bot's /start command."""
    return ("🖐🏻أهلًا بك في متجرنا🖐🏻\n" + "\n👇🏻اختر من القائمة بالأسفل👇🏻" + "\n✍🏻الصانع : علي حاج مرعي✍🏻")

# -----------------------------------------------------------------------------
# Command Handlers
# These functions handle specific Telegram bot commands (e.g., /start, /admin).
# -----------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /start command."""
    ensure_user(update.effective_user)
    u = get_user(update.effective_user.id)
    welcome_message = (
        f"🖐🏻أهلًا بك في متجرنا🖐🏻\n"+"\n✍🏻الصانع : علي حاج مرعي✍🏻\n" + "\n👇🏻اختر من القائمة بالأسفل👇🏻"
    )
    await update.message.reply_text(
        welcome_message,
        reply_markup=MAIN_MENU,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /admin command, providing access to the admin panel."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("غير مصرح لك بالوصول إلى لوحة التحكم هذه.")
        return
    await update.message.reply_text("أهلاً بك أيها المدير! اختر من القائمة:", reply_markup=admin_menu_kb())

async def show_account(update: Update, context: ContextTypes.DEFAULT_TYPE, as_new: bool = True):
    """Displays the user's account information."""
    ensure_user(update.effective_user)
    u = get_user(update.effective_user.id)
    if as_new:
        await update.effective_chat.send_message(account_text(u), parse_mode=ParseMode.HTML)
    else:
        q = update.callback_query
        await q.message.edit_text(account_text(u), parse_mode=ParseMode.HTML, reply_markup=MAIN_MENU)

# -----------------------------------------------------------------------------
# Callback Query Handlers (General User Flow)
# These functions handle button clicks from the main user menu.
# -----------------------------------------------------------------------------

async def on_main_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles main menu button callbacks."""
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "ACCOUNT":
        user_id = q.from_user.id
        user_data = get_user(user_id)

        if user_data:
            username = user_data['username'] if user_data['username'] else "لا يوجد"
            balance = user_data['balance']

            # تم تعديل النص بالكامل ليناسب تنسيق HTML بشكل صحيح
            message_text = (
                f"👤 <b>معلومات حسابك:</b>\n"
                f"• الآيدي: {user_id}\n"
                f"• اليوزر: @{username}\n"
                f"• الرصيد: {balance} ل.س"
            )

            # تأكد أن parse_mode هو HTML
            await context.bot.send_message(
                chat_id=q.message.chat_id,
                text=message_text,
                parse_mode="HTML"
            )
        else:
            await context.bot.send_message(
                chat_id=q.message.chat_id,
                text="لم يتم العثور على معلومات حسابك. يرجى التواصل مع الدعم."
            )

        await q.answer()
        return
        
    if data == "SUPPORT_CONTACT":
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT value FROM settings WHERE key=?", (SETTING_SUPPORT,))
        support_user = cur.fetchone()
        conn.close()

        support_text = "لا يوجد دعم متاح حالياً." # رسالة افتراضية
        if support_user and support_user[0]:
            support_text = f"<b>💬 التواصل مع الدعم</b>\n\nللتواصل مع الدعم الفني، يرجى مراسلة:\n\n@{support_user[0]}\n"

        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع", callback_data="BACK_TO_MAIN")]])
        await q.message.edit_text(support_text, reply_markup=kb, parse_mode='HTML')
        return

    if data == "TOPUP_MENU":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📮 كود شام كاش", callback_data="SHOW_SHAM_CODE"), InlineKeyboardButton("📍 عنوان شام كاش", callback_data="SHOW_SHAM_ADDR")],
            [InlineKeyboardButton("➕ شحن الحساب", callback_data="TOPUP_START")],
            [InlineKeyboardButton("⬅️ رجوع", callback_data="BACK_TO_MAIN")]
        ])
        await q.message.edit_text("اختر من خيارات الشحن:", reply_markup=kb)
        return

    if data == "BACK_TO_MAIN":
        welcome_message = (
            f"🖐🏻أهلًا بك في متجرنا🖐🏻\n"+"\n✍🏻الصانع : علي حاج مرعي✍🏻\n" + "\n👇🏻اختر من القائمة بالأسفل👇🏻"
        )
        await q.message.edit_text(welcome_message, reply_markup=MAIN_MENU, parse_mode=ParseMode.HTML)
        return

    if data == "BUY":
        cats = get_categories()
        if not cats:
            await q.message.edit_text("لا توجد قوائم بعد. الرجاء مراجعة الأدمن.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع", callback_data="BACK_TO_MAIN")]]))
            return

        # New code to group 2 buttons per row
        rows = []
        for i in range(0, len(cats), 2):
            row = [InlineKeyboardButton(f" {cats[i]['name']}", callback_data=f"BUY_CAT:{cats[i]['id']}")]
            if i + 1 < len(cats):
                row.append(InlineKeyboardButton(f" {cats[i+1]['name']}", callback_data=f"BUY_CAT:{cats[i+1]['id']}"))
            rows.append(row)

        rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="BACK_TO_MAIN")])
        await q.message.edit_text("👇🏻 اختر اي قائمة تريد 👇🏻:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data == "NEWS":
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT value FROM settings WHERE key=?", ('news_message',))
        news_message_from_db = cur.fetchone()
        conn.close()

        news_text = news_message_from_db[0] if news_message_from_db else "🗞️ قسم الأخبار\n\nلا توجد أخبار جديدة حالياً. تابعنا للمزيد!"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع", callback_data="BACK_TO_MAIN")]])
        await q.message.edit_text(news_text, reply_markup=kb, parse_mode='HTML')
        return

# -----------------------------------------------------------------------------
# Show Sham Cash Code/Address + Start Top-up Handlers
# -----------------------------------------------------------------------------
async def on_topup_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles top-up menu button callbacks."""
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "SHOW_SHAM_CODE":
        # The user's requested photo ID for the Sham Cash QR code.
        photo_id = "AgACAgQAAxkBAYkui2ixsUvmCDPQVMDpOvFzFISV2TEIAAKeyjEbDEyQUc4oaicsvccZAQADAgADcwADNgQ" 
        caption_text = f"👇🏻 عنوان شام كاش 👇🏻:\n \n 9cd65bde642da2496b407f8941dc01 \n إذا كنت تريد تحويل الدولار أو الليرة التركية فحول انا احولهم لحسابك ليصبحوا رصيد بالسوري على البوت لا تقلق😁"
        if photo_id:
            await q.message.chat.send_photo(photo=photo_id, caption=caption_text, parse_mode=ParseMode.HTML)
        else:
            await q.message.chat.send_message("لم يتم ضبط صورة كود شام كاش بعد. أخبر الأدمن.")
        return

    if data == "SHOW_SHAM_ADDR":
        addr = get_setting(SETTING_SHAM_ADDR)
        if addr:
            await q.message.chat.send_message(f"عنوان شام كاش:\n<code>{addr}</code>", parse_mode=ParseMode.HTML)
        else:
            await q.message.chat.send_message("لم يتم ضبط العنوان بعد. أخبر الأدمن.")
        return

    if data == "TOPUP_START":
        context.user_data.clear()
        context.user_data["flow"] = "topup"
        await q.message.chat.send_message("🔢 أرسل رقم العملية:")
        return

# -----------------------------------------------------------------------------
# Buy Flow Handlers
# -----------------------------------------------------------------------------
async def on_buy_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles product buying process callbacks."""
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "BUY_BACK":
        current_cat_id = context.user_data.get("current_cat_id")
        if current_cat_id is None: 
            welcome_message = (
                f"🖐🏻أهلًا بك في متجرنا🖐🏻\n"+"\n✍🏻الصانع : علي حاج مرعي✍🏻\n" + "\n👇🏻اختر من القائمة بالأسفل👇🏻"
            )
            await q.message.edit_text(welcome_message, reply_markup=MAIN_MENU, parse_mode=ParseMode.HTML)
            return

        parent_cat = get_category(current_cat_id)
        parent_id = parent_cat['parent_id'] if parent_cat else None

        if parent_id is None:
            cats = get_categories(parent_id=None)
            rows = []
            for i in range(0, len(cats), 2):
                row = [InlineKeyboardButton(f" {cats[i]['name']}", callback_data=f"BUY_CAT:{cats[i]['id']}")]
                if i + 1 < len(cats):
                    row.append(InlineKeyboardButton(f" {cats[i+1]['name']}", callback_data=f"BUY_CAT:{cats[i+1]['id']}"))
                rows.append(row)
            rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="BACK_TO_MAIN")])
            await q.message.edit_text("اختر اي قائمة تريد:", reply_markup=InlineKeyboardMarkup(rows))
            context.user_data["current_cat_id"] = None
            return
        else:
            sub_cats = get_categories(parent_id=parent_id)
            if sub_cats:
                rows = []
                for i in range(0, len(sub_cats), 2):
                    row = [InlineKeyboardButton(f" {sub_cats[i]['name']}", callback_data=f"BUY_CAT:{sub_cats[i]['id']}")]
                    if i + 1 < len(sub_cats):
                        row.append(InlineKeyboardButton(f" {sub_cats[i+1]['name']}", callback_data=f"BUY_CAT:{sub_cats[i+1]['id']}"))
                    rows.append(row)
                rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="BUY_BACK")])
                await q.message.edit_text("اختر اي قسم تريد:", reply_markup=InlineKeyboardMarkup(rows))
                context.user_data["current_cat_id"] = parent_id
            else:
                await q.message.edit_text("لا توجد قوائم فرعية في هذه القائمة.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع", callback_data="BUY_BACK")]]))
            return

    if data.startswith("BUY_CAT:"):
        cat_id = int(data.split(":", 1)[1])
        context.user_data["current_cat_id"] = cat_id

        sub_cats = get_categories(parent_id=cat_id)
        if sub_cats:
            rows = []
            for i in range(0, len(sub_cats), 2):
                row = [InlineKeyboardButton(f" {sub_cats[i]['name']}", callback_data=f"BUY_CAT:{sub_cats[i]['id']}")]
                if i + 1 < len(sub_cats):
                    row.append(InlineKeyboardButton(f" {sub_cats[i+1]['name']}", callback_data=f"BUY_CAT:{sub_cats[i+1]['id']}"))
                rows.append(row)
            rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="BUY_BACK")])
            await q.message.edit_text("👇🏻 اختر اي قسم تريد 👇🏻:", reply_markup=InlineKeyboardMarkup(rows))
            return

        prods = get_products_by_cat(cat_id)
        if not prods:
            await q.message.edit_text("❌ لا توجد منتجات في هذه القائمة حالياً ❌", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع", callback_data="BUY_BACK")]]))
            return

        rows = []
        for i in range(0, len(prods), 2):
            p1 = prods[i]
            button_text1 = f" {p1['name']} /-↔-/ {money(p1['price'])}" if p1['product_type'] == 'regular' else f" {p1['name']} /-↔-/ {money(p1['price'])} للواحدة"
            row = [InlineKeyboardButton(button_text1, callback_data=f"BUY_PROD:{p1['id']}")]

            if i + 1 < len(prods):
                p2 = prods[i+1]
                button_text2 = f" {p2['name']} /-↔-/ {money(p2['price'])}" if p2['product_type'] == 'regular' else f" {p2['name']} /-↔-/ {money(p2['price'])} للواحدة"
                row.append(InlineKeyboardButton(button_text2, callback_data=f"BUY_PROD:{p2['id']}"))
            rows.append(row)

        rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="BUY_BACK")])
        await q.message.edit_text("👇🏻 اختر منتجاً 👇🏻:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("BUY_PROD:"):
        prod_id = int(data.split(":", 1)[1])
        prow = get_product(prod_id)
        if not prow:
            await q.message.chat.send_message("❌ تعذر إيجاد المنتج ❌")
            return

        context.user_data.clear()
        context.user_data["buy_prod_id"] = prod_id
        context.user_data["current_cat_id"] = prow['category_id']

        if prow['product_type'] == 'regular':
            # قم بالتحقق أولا إذا كانت الكمية غير محدودة
            if prow['stock'] is None:
                # إذا كانت غير محدودة، لا تفعل شيئًا وانتقل
                pass
            # أما إذا كانت الكمية محددة، فتحقق إذا كانت 0 أو أقل
            elif prow['stock'] <= 0:
                await q.answer("❌ لا يوجد كمية كافية من هذا المنتج. يرجى التواصل مع الأدمن.", show_alert=True)
                return
                await q.message.chat.send_message("هذا المنتج غير متوفر حالياً. الرجاء المحاولة لاحقاً.")
                return
            context.user_data["flow"] = "buy_contact"
            await q.message.chat.send_message("🆔 أرسل الآيدي أو رقم الهاتف المطلوب ربطه بالطلب(إذا رقم هاتف بدون 963+ رجاءاً و إلا طلبك رح ينلغي) 🆔:")
        elif prow['product_type'] == 'quantity':
            context.user_data["flow"] = "buy_quantity"
            await q.message.chat.send_message(f"أدخل الكمية المطلوبة.\nالحد المسموح به هو: {prow['min_qty']} إلى {prow['max_qty']}.")
        return

    if data == "CHANGE_QTY":
        context.user_data["flow"] = "buy_quantity"
        prod_id = context.user_data.get("buy_prod_id")
        prow = get_product(prod_id)
        await q.message.chat.send_message(f"أدخل الكمية الجديدة.\nالحد المسموح به هو: {prow['min_qty']} إلى {prow['max_qty']}.")
        return

    if data == "BUY_CANCEL":
        msg_id = context.user_data.get("confirm_msg_id")
        if msg_id:
            try:
                await q.message.chat.delete_message(msg_id)
            except Exception:
                pass
        context.user_data.clear()
        await q.message.chat.send_message("✔ تم إلغاء الطلب ✔")
        return

    if data == "BUY_EDIT":
        await q.message.chat.send_message("🔄 أعد إرسال الآيدي/رقم الهاتف الجديد 🔄:")
        context.user_data["flow"] = "buy_contact"
        return

    if data == "BUY_CONFIRM":
        urow = get_user(q.from_user.id)
        prod_id = int(context.user_data.get("buy_prod_id", 0))
        contact = context.user_data.get("buy_contact")
        if not (prod_id and contact):
            await q.message.chat.send_message("⛔ الطلب غير مكتمل أعد المحاولة ⛔")
            context.user_data.clear()
            return
        prow = get_product(prod_id)
        if not prow:
            await q.message.chat.send_message("🔍 تعذر إيجاد المنتج 🚫")
            context.user_data.clear()
            return

        quantity = 1
        if prow['product_type'] == 'quantity':
            quantity = context.user_data.get("buy_quantity", 0)
            if not quantity:
                await q.message.chat.send_message("🔄لم يتم تحديد الكمية الرجاء المحاولة مرة أخرى⛔")
                return

        price = float(prow["price"]) * quantity

        # Check if enough balance
        bal = float(urow["balance"]) if urow else 0.0
        if bal < price:
            await q.message.chat.send_message("💔 رصيدك غير كافٍ لهذا الطلب 💔")
            context.user_data.clear()
            return

        # Check if enough stock (for regular products)
        if prow['product_type'] == 'regular' and prow['stock'] < quantity:
            await q.message.chat.send_message("📦 تعذر تنفيذ الطلب الكمية غير كافية 🚫")
            context.user_data.clear()
            return

        # Decrement stock and change balance
        decrement_product_stock(prod_id, quantity)
        new_bal = change_balance(q.from_user.id, -price)

        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO orders(user_id, product_id, price, contact, status, created_at) VALUES(?,?,?,?,?,?)",
            (q.from_user.id, prod_id, price, contact, "pending", datetime.utcnow().isoformat()),
        )
        oid = cur.lastrowid
        conn.commit()
        conn.close()

        await q.message.chat.send_message("⏳ تم تقديم طلبك. الرجاء الانتظار ريثما يتم التحقق منه.")

        gid = get_setting(SETTING_GROUP_ORDERS)
        if gid:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ قبول", callback_data=f"ORD_ACCEPT:{oid}"),
                InlineKeyboardButton("❌ رفض", callback_data=f"ORD_REJECT:{oid}"),
            ]])
            urow = get_user(q.from_user.id)
            text = (
                "🧾 تأكيد طلب شراء\n"
                f"• المنتج: <b>{prow['name']}</b>\n"
                f"• السعر: <b>{money(price)}</b>\n"
                f"• الكمية: <b>{quantity}</b>\n"
                f"• الآيدي/الهاتف: <code>{contact}</code>\n"
                f"• اليوزر: @{urow['username'] if urow['username'] else '—'}\n"
                f"• آيدي المستخدم: <code>{urow['user_id']}</code>\n")
            try:
                await context.bot.send_message(int(gid), text, parse_mode=ParseMode.HTML, reply_markup=kb)
            except Exception as e:
                logger.error(f"Failed to send order to group: {e}")
        else:
            await q.message.chat.send_message("⚠️ لم يتم ضبط آيدي مجموعة الطلبات. تواصل مع الأدمن.")

        context.user_data.clear()
        return

# -----------------------------------------------------------------------------
# Message Handler for User Input (Multi-state)
# This handles text input based on the user's current "flow" state.
# -----------------------------------------------------------------------------

async def on_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles all text messages from users."""
    text = (update.message.text or '').strip()
    user_id = update.effective_user.id
    current_flow = context.user_data.get("flow")

    if not current_flow:
        await update.message.reply_text("اختر إجراءً من الأزرار.", reply_markup=MAIN_MENU)
        return

    # Admin actions first
    if is_admin(user_id):
        if current_flow.startswith("adm_"):
            if current_flow == "adm_cat_add":
                cat_name = text
                if not cat_name:
                    await update.message.reply_text("🔄 الاسم لا يمكن أن يكون فارغاً، يرجى المحاولة مرة أخرى ❌")
                    return
                conn = sqlite3.connect(DB_PATH)
                cur = conn.cursor()
                cur.execute("INSERT INTO categories(name, parent_id) VALUES(?,?)", (cat_name, None))
                conn.commit()
                conn.close()
                del context.user_data["flow"]
                await update.message.reply_text(f"✅ تم إضافة الفئة '{cat_name}' بنجاح.")
                return

            elif current_flow == "adm_cat_add_sub_name":
                cat_name = text
                parent_id = context.user_data.get("parent_id")
                if not cat_name:
                    await update.message.reply_text("🔄 الاسم لا يمكن أن يكون فارغاً، يرجى المحاولة مرة أخرى ❌")
                    return
                conn = sqlite3.connect(DB_PATH)
                cur = conn.cursor()
                cur.execute("INSERT INTO categories(name, parent_id) VALUES(?,?)", (cat_name, parent_id))
                conn.commit()
                conn.close()
                del context.user_data["flow"]
                del context.user_data["parent_id"]
                await update.message.reply_text(f"✅ تم إضافة الفئة الفرعية '{cat_name}' بنجاح.")
                return

            elif current_flow == "adm_cat_rename":
                cid = context.user_data.get("cid")
                conn = sqlite3.connect(DB_PATH)
                cur = conn.cursor()
                cur.execute("UPDATE categories SET name=? WHERE id=?", (text, cid))
                conn.commit()
                conn.close()
                del context.user_data["flow"]
                del context.user_data["cid"]
                await update.message.reply_text("✅ تم تعديل اسم القائمة.")
                return

            elif current_flow == "adm_cat_move_sub_target":
                cid = context.user_data.get("cid")
                conn = sqlite3.connect(DB_PATH)
                cur = conn.cursor()
                cur.execute("UPDATE categories SET parent_id=? WHERE id=?", (text, cid))
                conn.commit()
                conn.close()
                del context.user_data["flow"]
                del context.user_data["cid"]
                await update.message.reply_text("✅ تم نقل القائمة الفرعية بنجاح.")
                return

            if current_flow == "adm_prod_add_name":
                context.user_data["name"] = update.message.text
                context.user_data["flow"] = "adm_prod_add_price"
                await update.message.reply_text("أرسل سعر المنتج الجديد:")
                return

            elif current_flow == "adm_prod_add_price":
                try:
                    price = float(update.message.text)
                    name = context.user_data.get("name")
                    cid = context.user_data.get("cid")
                    conn = sqlite3.connect(DB_PATH)
                    cur = conn.cursor()
                    cur.execute("INSERT INTO products (name, price, stock, category_id, product_type) VALUES (?, ?, ?, ?, ?)", (name, price, None, cid, 'regular'))
                    conn.commit()
                    conn.close()
                    await update.message.reply_text("✅ تم إضافة المنتج بنجاح (كمية غير محدودة).")
                except ValueError:
                    await update.message.reply_text("الرجاء إرسال السعر كقيمة رقمية صحيحة.")
                finally:
                    context.user_data.clear()
                return


            elif current_flow == "adm_prod_add_quantity_name":
                prod_name = text
                context.user_data["prod_name"] = prod_name
                context.user_data["flow"] = "adm_prod_add_quantity_price"
                await update.message.reply_text("أدخل سعر الوحدة الواحدة:")
                return

            elif current_flow == "adm_prod_add_quantity_price":
                try:
                    prod_price = float(text)
                except ValueError:
                    await update.message.reply_text("السعر يجب أن يكون رقماً، يرجى المحاولة مرة أخرى.")
                    return
                context.user_data["prod_price"] = prod_price
                context.user_data["flow"] = "adm_prod_add_quantity_range"
                await update.message.reply_text("أدخل نطاق الكمية المسموح به (مثلاً: 10-20):")
                return

            elif current_flow == "adm_prod_add_quantity_range":
                try:
                    min_qty, max_qty = map(int, text.split('-'))
                    if min_qty > max_qty or min_qty < 1:
                        raise ValueError
                except ValueError:
                    await update.message.reply_text("الرجاء إدخال نطاق صحيح مثل 10-20.")
                    return

                prod_name = context.user_data.get("prod_name")
                prod_price = context.user_data.get("prod_price")
                cid = context.user_data.get("cid")
                conn = sqlite3.connect(DB_PATH)
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO products(name, price, min_qty, max_qty, category_id, product_type) VALUES(?,?,?,?,?,?)",
                    (prod_name, prod_price, min_qty, max_qty, cid, 'quantity')
                )
                conn.commit()
                conn.close()
                del context.user_data["flow"]
                del context.user_data["prod_name"]
                del context.user_data["prod_price"]
                del context.user_data["cid"]
                await update.message.reply_text(f"✅ تم إضافة المنتج بكمية '{prod_name}' بنجاح.")
                return

            elif current_flow == "adm_prod_reprice":
                try:
                    prod_price = float(text)
                except ValueError:
                    await update.message.reply_text("السعر يجب أن يكون رقماً، يرجى المحاولة مرة أخرى.")
                    return
                pid = context.user_data.get("pid")
                conn = sqlite3.connect(DB_PATH)
                cur = conn.cursor()
                cur.execute("UPDATE products SET price=? WHERE id=?", (prod_price, pid))
                conn.commit()
                conn.close()
                del context.user_data["flow"]
                del context.user_data["pid"]
                await update.message.reply_text("✅ تم تعديل سعر المنتج.")
                return

            elif current_flow == "adm_usr_credit_id":
                context.user_data["credit_uid"] = text
                context.user_data["flow"] = "adm_usr_credit_amount"
                await update.message.reply_text("أدخل المبلغ المراد شحنه (رقماً):")
                return

            elif current_flow == "adm_usr_credit_amount":
                try:
                    amount = float(text)
                except ValueError:
                    await update.message.reply_text("المبلغ يجب أن يكون رقماً، يرجى المحاولة مرة أخرى.")
                    return
                credit_uid = int(context.user_data.get("credit_uid"))
                new_balance = change_balance(credit_uid, amount)
                await update.message.reply_text("✅ تم شحن رصيد المستخدم بنجاح.")
                try:
                    await context.bot.send_message(credit_uid, f"✅ تم شحن رصيد حسابك بقيمة {money(amount)}. رصيدك الحالي: {money(new_balance)}")
                except Exception as e:
                    logger.error(f"Failed to notify user {credit_uid}: {e}")
                del context.user_data["flow"]
                del context.user_data["credit_uid"]
                return

            elif current_flow == "adm_usr_debit_id":
                context.user_data["debit_uid"] = text
                context.user_data["flow"] = "adm_usr_debit_amount"
                await update.message.reply_text("أدخل المبلغ المراد سحبه (رقماً):")
                return

            elif current_flow == "adm_usr_debit_amount":
                try:
                    amount = float(text)
                except ValueError:
                    await update.message.reply_text("المبلغ يجب أن يكون رقماً، يرجى المحاولة مرة أخرى.")
                    return
                debit_uid = int(context.user_data.get("debit_uid"))
                new_balance = change_balance(debit_uid, -amount)
                await update.message.reply_text("✅ تم سحب الرصيد من المستخدم بنجاح.")
                try:
                    await context.bot.send_message(debit_uid, f"➖ تم سحب رصيد من حسابك بقيمة {money(amount)}. رصيدك الحالي: {money(new_balance)}")
                except Exception as e:
                    logger.error(f"Failed to notify user {debit_uid}: {e}")
                del context.user_data["flow"]
                del context.user_data["debit_uid"]
                return

            elif current_flow == "adm_set_support":
                set_setting(SETTING_SUPPORT, text)
                await update.message.reply_text("✅ تم حفظ يوزر الدعم.")
                del context.user_data["flow"]
                return

            elif current_flow == "adm_set_sham_code":
                set_setting(SETTING_SHAM_CODE, text)
                await update.message.reply_text("✅ تم حفظ كود شام كاش.")
                del context.user_data["flow"]
                return

            elif current_flow == "adm_set_sham_addr":
                set_setting(SETTING_SHAM_ADDR, text)
                await update.message.reply_text("✅ تم حفظ عنوان شام كاش.")
                del context.user_data["flow"]
                return

            elif current_flow == "adm_set_group_topup":
                set_setting(SETTING_GROUP_TOPUP, text)
                await update.message.reply_text("✅ تم حفظ آيدي مجموعة الشحن.")
                del context.user_data["flow"]
                return

            elif current_flow == "adm_set_group_orders":
                set_setting(SETTING_GROUP_ORDERS, text)
                await update.message.reply_text("✅ تم حفظ آيدي مجموعة الطلبات.")
                del context.user_data["flow"]
                return

            elif current_flow == "adm_set_admins":
                admin_ids = [int(i.strip()) for i in text.split(",")]
                set_setting(SETTING_ADMINS, ",".join(map(str, admin_ids)))
                await update.message.reply_text("✅ تم حفظ آيديات الأدمن.")
                del context.user_data["flow"]
                return

            elif current_flow == "adm_broadcast":
                message_text = text
                context.user_data["flow"] = None
                conn = db()
                cur = conn.cursor()
                cur.execute("SELECT user_id FROM users")
                users = cur.fetchall()
                conn.close()
                sent_count = 0
                blocked_count = 0
                for user in users:
                    try:
                        await context.bot.send_message(chat_id=user['user_id'], text=message_text)
                        sent_count += 1
                    except Exception:
                        blocked_count += 1
                await update.message.reply_text(
                    f"✅ تم إرسال الرسالة بنجاح.\n\n"
                    f"عدد الرسائل المرسلة: {sent_count}\n"
                    f"عدد المستخدمين المحظورين: {blocked_count}"
                )
                return

            elif current_flow == "adm_edit_news":
                new_news_message = update.message.text
                conn = sqlite3.connect(DB_PATH)
                cur = conn.cursor()
                cur.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", ('news_message', new_news_message))
                conn.commit()
                conn.close()
                context.user_data.clear()
                await update.message.reply_text("✅ تم تحديث رسالة الأخبار بنجاح.")
                return

    # Top-up process
    if current_flow == "topup":
        stage = context.user_data.get("stage")
        if stage is None:
            context.user_data["topup_op"] = text
            context.user_data["stage"] = "amount"
            await update.message.reply_text("💰 الآن أرسل المبلغ (رقماً مثل 1000 أو 10.5):")
            return
        elif stage == "amount":
            try:
                amount = float(text)
            except ValueError:
                await update.message.reply_text("الرجاء إرسال المبلغ كرقم صحيح أو عشري.")
                return
            op = context.user_data.get("topup_op")
            user = update.effective_user
            ensure_user(user)

            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO topups(user_id, op_number, amount, status, created_at) VALUES(?,?,?,?,?)",
                (user.id, op, amount, "pending", datetime.utcnow().isoformat()),
            )
            tid = cur.lastrowid
            conn.commit()
            conn.close()

            await update.message.reply_text("⏳ تم إرسال طلب الشحن. الرجاء الانتظار ريثما يتم التحقق منه.")
            gid = get_setting(SETTING_GROUP_TOPUP)
            if gid:
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ قبول", callback_data=f"TP_ACCEPT:{tid}"),
                    InlineKeyboardButton("❌ رفض", callback_data=f"TP_REJECT:{tid}"),
                ]])
                urow = get_user(user.id)
                message_text = (
                    "📩 طلب شحن جديد\n"
                    f"• اليوزر: @{urow['username'] if urow['username'] else '—'}\n"
                    f"• الآيدي: <code>{urow['user_id']}</code>\n"
                    f"• رقم العملية: <code>{op}</code>\n"
                    f"• المبلغ: <b>{money(amount)}</b>\n"
                )
                try:
                    await context.bot.send_message(int(gid), message_text, parse_mode=ParseMode.HTML, reply_markup=kb)
                except Exception as e:
                    logger.error(f"Failed to send topup to group: {e}")
            else:
                await update.message.reply_text("⚠️ لم يتم ضبط آيدي مجموعة الشحن. تواصل مع الأدمن.")
            context.user_data.clear()
            return

    # Buy process
    if current_flow == "buy_contact":
        contact = text
        context.user_data["buy_contact"] = contact
        prod_id = context.user_data.get("buy_prod_id")
        prow = get_product(prod_id)
        if not prow:
            await update.message.reply_text("تعذر إيجاد المنتج. الرجاء المحاولة مرة أخرى.")
            context.user_data.clear()
            return

        current_balance = get_balance(user_id)
        new_balance = current_balance - prow['price']
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ تأكيد الطلب", callback_data="BUY_CONFIRM") , InlineKeyboardButton("✏️ تعديل الآيدي/الهاتف", callback_data="BUY_EDIT")],
            [InlineKeyboardButton("❌ إلغاء الطلب", callback_data="BUY_CANCEL")]
        ])
        msg_text = (f"❓هل أنت متأكد من معلومات الطلب\n"
                    f"• المنتج: {prow['name']}\n"
                    f"• السعر: {money(prow['price'])}\n"
                    f"• الآيدي/الهاتف: {contact}\n"
                    f"• الرصيد قبل: {money(current_balance)}\n"
                    f"• الرصيد بعد: {money(new_balance)}\n")
        msg = await update.message.reply_text(msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
        context.user_data["confirm_msg_id"] = msg.message_id
        context.user_data["flow"] = None
        return

    if current_flow == "buy_quantity":
        try:
            quantity = int(text)
        except ValueError:
            await update.message.reply_text("الكمية يجب أن تكون رقماً صحيحاً، يرجى المحاولة مرة أخرى.")
            return

        prod_id = context.user_data.get("buy_prod_id")
        prow = get_product(prod_id)
        if not prow:
            await update.message.reply_text("تعذر إيجاد المنتج. الرجاء المحاولة مرة أخرى.")
            context.user_data.clear()
            return

        if flow == "adm_edit_support_message":
            new_message = update.message.text
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", ('support_message', new_message))
            conn.commit()
            conn.close()
            context.user_data.clear()
            await update.message.reply_text("✅ تم تحديث رسالة الدعم بنجاح.")
            return

        min_qty = prow['min_qty']
        max_qty = prow['max_qty']
        if not (min_qty <= quantity <= max_qty):
            await update.message.reply_text(f"الرجاء إدخال كمية صحيحة.\nالحد المسموح به هو: {min_qty} إلى {max_qty}.")
            return

        context.user_data["buy_quantity"] = quantity
        context.user_data["flow"] = "buy_contact_quantity"
        await update.message.reply_text("أرسل الآيدي أو رقم الهاتف المطلوب ربطه بالطلب:")
        return

    if flow == "adm_edit_news":
        new_news_message = update.message.text
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", ('news_message', new_news_message))
        conn.commit()
        conn.close()
        context.user_data.clear()
        await update.message.reply_text("✅ تم تحديث رسالة الأخبار بنجاح.")
        return

    if current_flow == "buy_contact_quantity":
        contact = text
        context.user_data["buy_contact"] = contact

        prod_id = context.user_data.get("buy_prod_id")
        quantity = context.user_data.get("buy_quantity")
        prow = get_product(prod_id)

        total_price = prow['price'] * quantity
        current_balance = get_balance(user_id)
        new_balance = current_balance - total_price

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ تأكيد الطلب", callback_data="BUY_CONFIRM") , InlineKeyboardButton("❌ إلغاء الطلب", callback_data="BUY_CANCEL")],
            [InlineKeyboardButton("✏️ تغيير الكمية/تغيير الرقم", callback_data="CHANGE_QTY")]
        ])

        msg_text = (f"❓هل أنت متأكد من معلومات الطلب\n"
                    f"• المنتج: {prow['name']}\n"
                    f"• الكمية: {quantity}\n"
                    f"• السعر الإجمالي: {money(total_price)}\n"
                    f"• الآيدي/الهاتف: {contact}\n"
                    f"• الرصيد قبل: {money(current_balance)}\n"
                    f"• الرصيد بعد: {money(new_balance)}\n")

        msg = await update.message.reply_text(msg_text, reply_markup=kb, parse_mode=ParseMode.HTML)
        context.user_data["confirm_msg_id"] = msg.message_id
        context.user_data["flow"] = None
        return

# -----------------------------------------------------------------------------
# Group Action Buttons (Admin Only) Handlers
# These handle actions taken by admins in the top-up/order groups.
# -----------------------------------------------------------------------------

async def on_group_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles admin actions on top-up and order requests."""
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        await q.message.reply_text("هذا الإجراء للأدمن فقط.")
        return

    data = q.data
    actor_id = q.from_user.id

    if data.startswith("TP_ACCEPT:") or data.startswith("TP_REJECT:"):
        tid = int(data.split(":", 1)[1])
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM topups WHERE id=?", (tid,))
        row = cur.fetchone()
        if not row:
            await q.message.reply_text("لم يتم العثور على هذا الطلب.")
            conn.close()
            return
        if row["status"] != "pending":
            await q.message.edit_text("تمت معالجته مسبقًا.")
            conn.close()
            return

        if data.startswith("TP_ACCEPT"):
            new_bal = change_balance(row["user_id"], float(row["amount"]))
            cur.execute("UPDATE topups SET status='approved' WHERE id=?", (tid,))
            conn.commit()
            conn.close()
            new_text = q.message.text + "\n\n✅ **تم قبول الشحن**"
            try:
                await q.message.edit_text(new_text, parse_mode=ParseMode.MARKDOWN, reply_markup=None)
            except Exception:
                pass
            try:
                await context.bot.send_message(row["user_id"],f"✅ تم شحن حسابك بمبلغ {money(row['amount'])}. رصيدك الحالي: {money(new_bal)}")
            except Exception:
                pass
            return
        else:
            cur.execute("UPDATE topups SET status='rejected' WHERE id=?", (tid,))
            conn.commit()
            conn.close()
            new_text = q.message.text + "\n\n❌ **تم رفض الشحن**"
            try:
                await q.message.edit_text(new_text, parse_mode=ParseMode.MARKDOWN, reply_markup=None)
            except Exception:
                pass
            try:
                await context.bot.send_message(row["user_id"], "❌ تم رفض طلب الشحن.")
            except Exception:
                pass
            return

    if data.startswith("ORD_ACCEPT:") or data.startswith("ORD_REJECT:"):
        oid = int(data.split(":", 1)[1])
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM orders WHERE id=?", (oid,))
        row = cur.fetchone()
        if not row:
            await q.message.reply_text("لم يتم العثور على الطلب.")
            conn.close()
            return
        if row["status"] != "pending":
            await q.message.edit_text("تمت معالجته مسبقًا.")
            conn.close()
            return

        if data.startswith("ORD_ACCEPT"):
            cur.execute("UPDATE orders SET status='approved' WHERE id=?", (oid,))
            conn.commit()
            conn.close()
            new_text = q.message.text + "\n\n✅ **تم قبول الطلب**"
            try:
                await q.message.edit_text(new_text, parse_mode=ParseMode.MARKDOWN, reply_markup=None)
            except Exception:
                pass
            try:
                await context.bot.send_message(row["user_id"], "✅ تم تنفيذ طلبك.")
            except Exception:
                pass
            return
        else:
            change_balance(row["user_id"], float(row["price"]))
            cur.execute("UPDATE orders SET status='rejected' WHERE id=?", (oid,))
            conn.commit()
            conn.close()
            new_text = q.message.text + "\n\n❌ **تم رفض الطلب**"
            try:
                await q.message.edit_text(new_text, parse_mode=ParseMode.MARKDOWN, reply_markup=None)
            except Exception:
                pass
            try:
                await context.bot.send_message(row["user_id"], "❌ تم رفض طلبك وتم إرجاع الرصيد.")
            except Exception:
                pass
            return

# -----------------------------------------------------------------------------
# Admin Menu Callback Handlers
# These functions handle all button clicks within the admin panel.
# -----------------------------------------------------------------------------

async def on_admin_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles admin menu button callbacks."""
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        await q.message.reply_text("لوحة الأدمن: الوصول مرفوض.")
        return
    data = q.data

    if data == "ADM_BACK":
        await q.message.edit_text("أهلاً بك أيها المدير! اختر من القائمة:", reply_markup=admin_menu_kb())
        return

    if data == "ADM_CATS":
        await q.message.edit_text("إدارة القوائم:", reply_markup=cats_menu_kb())
        return

    if data == "ADM_BACK_CATS":
        await q.message.edit_text("إدارة القوائم:", reply_markup=cats_menu_kb())
        return

    if data == "ADM_MAIN_CATS":
        await q.message.edit_text("إدارة القوائم الرئيسية:", reply_markup=main_cats_kb())
        return

    if data == "ADM_SUB_CATS":
        await q.message.edit_text("إدارة القوائم الفرعية:", reply_markup=sub_cats_kb())
        return

    if data == "CAT_ADD_MAIN":
        context.user_data.clear()
        context.user_data["flow"] = "adm_cat_add"
        await q.message.reply_text("أرسل اسم القائمة الرئيسية الجديدة:")
        return

    if data == "CAT_EDIT_MAIN":
        cats = get_categories(parent_id=None)
        if not cats:
            await q.message.reply_text("لا توجد قوائم رئيسية لتعديلها.")
            return
        rows = []
        for i in range(0, len(cats), 2):
            row = [InlineKeyboardButton(f"✏️ {cats[i]['name']}", callback_data=f"CAT_EDIT:{cats[i]['id']}")]
            if i + 1 < len(cats):
                row.append(InlineKeyboardButton(f"✏️ {cats[i+1]['name']}", callback_data=f"CAT_EDIT:{cats[i+1]['id']}"))
            rows.append(row)
        rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="ADM_BACK_CATS")])
        await q.message.reply_text("اختر قائمة لتعديل اسمها:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data == "EDIT_SUPPORT_MESSAGE":
        context.user_data["flow"] = "adm_edit_support_message"
        await q.message.edit_text("أرسل رسالة الدعم الجديدة الآن.")
        await q.answer()
        return

    if data == "CAT_DEL_MAIN":
        cats = get_categories(parent_id=None)
        if not cats:
            await q.message.reply_text("لا توجد قوائم رئيسية لحذفها.")
            return
        rows = []
        for i in range(0, len(cats), 2):
            row = [InlineKeyboardButton(f"🗑️ {cats[i]['name']}", callback_data=f"CAT_DEL:{cats[i]['id']}")]
            if i + 1 < len(cats):
                row.append(InlineKeyboardButton(f"🗑️ {cats[i+1]['name']}", callback_data=f"CAT_DEL:{cats[i+1]['id']}"))
            rows.append(row)
        rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="ADM_BACK_CATS")])
        await q.message.reply_text("اختر قائمة لحذفها:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data == "EDIT_SUPPORT_MESSAGE":
        context.user_data["flow"] = "adm_edit_support_message"
        await q.message.edit_text("أرسل رسالة الدعم الجديدة الآن. يمكنك استخدام تنسيق HTML (مثل <b> لخط عريض).")
        await q.answer()
        return

    if data == "CAT_ADD_SUB":
        context.user_data.clear()
        cats = get_categories(parent_id=None)
        if not cats:
            await q.message.reply_text("لا توجد قوائم رئيسية لإضافة قائمة فرعية إليها.")
            return
        rows = []
        for i in range(0, len(cats), 2):
            row = [InlineKeyboardButton(f"{cats[i]['name']}", callback_data=f"CAT_LIST_ADD_SUB:{cats[i]['id']}")]
            if i + 1 < len(cats):
                row.append(InlineKeyboardButton(f"{cats[i+1]['name']}", callback_data=f"CAT_LIST_ADD_SUB:{cats[i+1]['id']}"))
            rows.append(row)
        rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="ADM_BACK_CATS")])
        await q.message.reply_text("اختر قائمة رئيسية لإضافة قائمة فرعية إليها:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data == "CAT_EDIT_SUB":
        sub_cats = get_categories_with_parent()
        if not sub_cats:
            await q.message.reply_text("لا توجد قوائم فرعية لتعديلها.")
            return
        rows = []
        for i in range(0, len(sub_cats), 2):
            row = [InlineKeyboardButton(f"{sub_cats[i]['name']}", callback_data=f"CAT_EDIT:{sub_cats[i]['id']}")]
            if i + 1 < len(sub_cats):
                row.append(InlineKeyboardButton(f"{sub_cats[i+1]['name']}", callback_data=f"CAT_EDIT:{sub_cats[i+1]['id']}"))
            rows.append(row)
        rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="ADM_BACK_CATS")])
        await q.message.reply_text("اختر قائمة فرعية لتعديل اسمها:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data == "CAT_DEL_SUB":
        sub_cats = get_categories_with_parent()
        if not sub_cats:
            await q.message.reply_text("لا توجد قوائم فرعية لحذفها.")
            return
        rows = []
        for i in range(0, len(sub_cats), 2):
            row = [InlineKeyboardButton(f"{sub_cats[i]['name']}", callback_data=f"CAT_DEL:{sub_cats[i]['id']}")]
            if i + 1 < len(sub_cats):
                row.append(InlineKeyboardButton(f"{sub_cats[i+1]['name']}", callback_data=f"CAT_DEL:{sub_cats[i+1]['id']}"))
            rows.append(row)
        rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="ADM_BACK_CATS")])
        await q.message.reply_text("اختر قائمة فرعية لحذفها:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data == "CAT_MOVE_SUB":
        sub_cats = get_categories_with_parent()
        if not sub_cats:
            await q.message.reply_text("لا توجد قوائم فرعية لنقلها.")
            return
        rows = []
        for i in range(0, len(sub_cats), 2):
            row = [InlineKeyboardButton(f"{sub_cats[i]['name']}", callback_data=f"CAT_MOVE:{sub_cats[i]['id']}")]
            if i + 1 < len(sub_cats):
                row.append(InlineKeyboardButton(f"{sub_cats[i+1]['name']}", callback_data=f"CAT_MOVE:{sub_cats[i+1]['id']}"))
            rows.append(row)
        rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="ADM_BACK_CATS")])
        await q.message.reply_text("اختر قائمة فرعية لنقلها:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("CAT_MOVE:"):
        cid = int(data.split(":", 1)[1])
        context.user_data["flow"] = "adm_cat_move_sub_target"
        context.user_data["cid"] = cid
        cats = get_categories(parent_id=None)
        rows = []
        for i in range(0, len(cats), 2):
            row = [InlineKeyboardButton(f"{cats[i]['name']}", callback_data=f"TARGET_CAT:{cats[i]['id']}")]
            if i + 1 < len(cats):
                row.append(InlineKeyboardButton(f"{cats[i+1]['name']}", callback_data=f"TARGET_CAT:{cats[i+1]['id']}"))
            rows.append(row)
        rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="ADM_BACK_CATS")])
        await q.message.reply_text("اختر القائمة الرئيسية الجديدة:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("TARGET_CAT:"):
        target_id = int(data.split(":", 1)[1])
        cid = context.user_data.get("cid")
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("UPDATE categories SET parent_id=? WHERE id=?", (target_id, cid))
        conn.commit()
        conn.close()
        await q.message.reply_text("✅ تم نقل القائمة الفرعية بنجاح.")
        del context.user_data["flow"]
        del context.user_data["cid"]
        return

    if data == "EDIT_NEWS":
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع", callback_data="ADM_BACK")]])
        context.user_data["flow"] = "adm_edit_news"
        await q.message.edit_text("أرسل رسالة الأخبار الجديدة الآن.", reply_markup=kb)
        await q.answer()
        return

    if data.startswith("CAT_LIST_ADD_SUB:"):
        parent_id = int(data.split(":", 1)[1])
        context.user_data["flow"] = "adm_cat_add_sub_name"
        context.user_data["parent_id"] = parent_id
        await q.message.reply_text("أرسل اسم القائمة الفرعية الجديدة:")
        return

    if data.startswith("CAT_EDIT:"):
        cid = int(data.split(":", 1)[1])
        context.user_data.clear()
        context.user_data["flow"] = "adm_cat_rename"
        context.user_data["cid"] = cid
        await q.message.reply_text("أرسل الاسم الجديد للقائمة:")
        return

    if data.startswith("CAT_DEL:"):
        cid = int(data.split(":", 1)[1])
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("DELETE FROM categories WHERE id=?", (cid,))
        conn.commit()
        conn.close()
        await q.message.edit_text("✅ تم حذف القائمة بنجاح.", reply_markup=None)
        return

    # Product Management
    if data == "ADM_PRODS":
        await q.message.edit_text("إدارة المنتجات:", reply_markup=prods_menu_kb())
        return

    if data == "PROD_ADD":
        context.user_data.clear()
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("منتج عادي", callback_data="ADD_PROD_REGULAR"), InlineKeyboardButton("منتج بكمية", callback_data="ADD_PROD_QUANTITY")],
            [InlineKeyboardButton("⬅️ رجوع", callback_data="ADM_BACK")]
        ])
        await q.message.edit_text("ما نوع المنتج الذي تريد إضافته؟", reply_markup=kb)
        return

    if data == "ADD_PROD_REGULAR":
        cats = get_categories(parent_id=None)
        if not cats:
            await q.message.edit_text("لا توجد قوائم رئيسية بعد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع", callback_data="ADM_BACK")]]))
            return
        rows = []
        for i in range(0, len(cats), 2):
            row = [InlineKeyboardButton(f"{cats[i]['name']}", callback_data=f"PROD_ADD_REGULAR_CAT:{cats[i]['id']}")]
            if i + 1 < len(cats):
                row.append(InlineKeyboardButton(f"{cats[i+1]['name']}", callback_data=f"PROD_ADD_REGULAR_CAT:{cats[i+1]['id']}"))
            rows.append(row)
        rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="PROD_ADD")])
        await q.message.edit_text("اختر قائمة لإضافة المنتج العادي إليها:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data == "ADD_PROD_QUANTITY":
        cats = get_categories(parent_id=None)
        if not cats:
            await q.message.edit_text("لا توجد قوائم رئيسية بعد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع", callback_data="ADM_BACK")]]))
            return
        rows = []
        for i in range(0, len(cats), 2):
            row = [InlineKeyboardButton(f"{cats[i]['name']}", callback_data=f"PROD_ADD_QUANTITY_CAT:{cats[i]['id']}")]
            if i + 1 < len(cats):
                row.append(InlineKeyboardButton(f"{cats[i+1]['name']}", callback_data=f"PROD_ADD_QUANTITY_CAT:{cats[i+1]['id']}"))
            rows.append(row)
        rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="PROD_ADD")])
        await q.message.edit_text("اختر قائمة لإضافة المنتج بكمية إليها:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("PROD_ADD_REGULAR_CAT:"):
        cat_id = int(data.split(":", 1)[1])
        sub_cats = get_sub_categories(cat_id)
        if sub_cats:
            rows = []
            for i in range(0, len(sub_cats), 2):
                row = [InlineKeyboardButton(f"{sub_cats[i]['name']}", callback_data=f"PROD_ADD_REGULAR_CAT:{sub_cats[i]['id']}")]
                if i + 1 < len(sub_cats):
                    row.append(InlineKeyboardButton(f"{sub_cats[i+1]['name']}", callback_data=f"PROD_ADD_REGULAR_CAT:{sub_cats[i+1]['id']}"))
                rows.append(row)
            rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="ADD_PROD_REGULAR")])
            await q.message.edit_text("اختر قائمة فرعية لإضافة المنتج إليها:", reply_markup=InlineKeyboardMarkup(rows))
        else:
            context.user_data["cid"] = cat_id
            context.user_data["flow"] = "adm_prod_add_name"
            await q.message.reply_text("أرسل اسم المنتج العادي الجديد:")
        return

    if data.startswith("PROD_ADD_QUANTITY_CAT:"):
        cat_id = int(data.split(":", 1)[1])
        sub_cats = get_sub_categories(cat_id)
        if sub_cats:
            rows = []
            for i in range(0, len(sub_cats), 2):
                row = [InlineKeyboardButton(f"{sub_cats[i]['name']}", callback_data=f"PROD_ADD_QUANTITY_CAT:{sub_cats[i]['id']}")]
                if i + 1 < len(sub_cats):
                    row.append(InlineKeyboardButton(f"{sub_cats[i+1]['name']}", callback_data=f"PROD_ADD_QUANTITY_CAT:{sub_cats[i+1]['id']}"))
                rows.append(row)
            rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="ADD_PROD_QUANTITY")])
            await q.message.edit_text("اختر قائمة فرعية لإضافة المنتج إليها:", reply_markup=InlineKeyboardMarkup(rows))
        else:
            context.user_data["cid"] = cat_id
            context.user_data["flow"] = "adm_prod_add_quantity_name"
            await q.message.reply_text("أرسل اسم المنتج بكمية الجديد:")
        return

    if data == "PROD_EDIT_NAME_LIST":
        await show_admin_categories_for_edit(update, context, "edit_prod_name")
        return

    if data.startswith("EDIT_PROD_NAME_CAT:"):
        cid = int(data.split(":", 1)[1])
        sub_cats = get_sub_categories(cid)
        if sub_cats:
            rows = []
            for i in range(0, len(sub_cats), 2):
                row = [InlineKeyboardButton(f"{sub_cats[i]['name']}", callback_data=f"EDIT_PROD_NAME_CAT:{sub_cats[i]['id']}")]
                if i + 1 < len(sub_cats):
                    row.append(InlineKeyboardButton(f"{sub_cats[i+1]['name']}", callback_data=f"EDIT_PROD_NAME_CAT:{sub_cats[i+1]['id']}"))
                rows.append(row)
            rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="PROD_EDIT_NAME_LIST")])
            await q.message.edit_text("اختر قائمة فرعية لعرض المنتجات:", reply_markup=InlineKeyboardMarkup(rows))
        else:
            prods = get_products_by_cat(cid)
            if not prods:
                await q.message.edit_text("لا توجد منتجات في هذه القائمة.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع", callback_data="PROD_EDIT_NAME_LIST")]]))
                return
            rows = []
            for i in range(0, len(prods), 2):
                row = [InlineKeyboardButton(f"{prods[i]['name']}", callback_data=f"PROD_EDIT_NAME:{prods[i]['id']}")]
                if i + 1 < len(prods):
                    row.append(InlineKeyboardButton(f"{prods[i+1]['name']}", callback_data=f"PROD_EDIT_NAME:{prods[i+1]['id']}"))
                rows.append(row)
            rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="PROD_EDIT_NAME_LIST")])
            await q.message.edit_text("اختر منتجاً لتعديل اسمه:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("PROD_EDIT_NAME:"):
        pid = int(data.split(":", 1)[1])
        context.user_data.clear()
        context.user_data["flow"] = "adm_prod_rename"
        context.user_data["pid"] = pid
        await q.message.reply_text("أرسل الاسم الجديد للمنتج:")
        return

    if data == "PROD_EDIT_PRICE_LIST":
        await show_admin_categories_for_edit(update, context, "edit_prod_price")
        return

    if data.startswith("EDIT_PROD_PRICE_CAT:"):
        cid = int(data.split(":", 1)[1])
        sub_cats = get_sub_categories(cid)
        if sub_cats:
            rows = []
            for i in range(0, len(sub_cats), 2):
                row = [InlineKeyboardButton(f"{sub_cats[i]['name']}", callback_data=f"EDIT_PROD_PRICE_CAT:{sub_cats[i]['id']}")]
                if i + 1 < len(sub_cats):
                    row.append(InlineKeyboardButton(f"{sub_cats[i+1]['name']}", callback_data=f"EDIT_PROD_PRICE_CAT:{sub_cats[i+1]['id']}"))
                rows.append(row)
            rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="PROD_EDIT_PRICE_LIST")])
            await q.message.edit_text("اختر قائمة فرعية لعرض المنتجات:", reply_markup=InlineKeyboardMarkup(rows))
        else:
            prods = get_products_by_cat(cid)
            if not prods:
                await q.message.edit_text("لا توجد منتجات في هذه القائمة.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع", callback_data="PROD_EDIT_PRICE_LIST")]]))
                return
            rows = []
            for i in range(0, len(prods), 2):
                row = [InlineKeyboardButton(f"{prods[i]['name']}", callback_data=f"PROD_REPRICE:{prods[i]['id']}")]
                if i + 1 < len(prods):
                    row.append(InlineKeyboardButton(f"{prods[i+1]['name']}", callback_data=f"PROD_REPRICE:{prods[i+1]['id']}"))
                rows.append(row)
            rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="PROD_EDIT_PRICE_LIST")])
            await q.message.edit_text("اختر منتجاً لتعديل سعره:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("PROD_REPRICE:"):
        pid = int(data.split(":", 1)[1])
        context.user_data.clear()
        context.user_data["flow"] = "adm_prod_reprice"
        context.user_data["pid"] = pid
        await q.message.reply_text("أرسل السعر الجديد للمنتج (رقماً):")
        return

    if data == "PROD_DEL_LIST":
        await show_admin_categories_for_edit(update, context, "del_prod")
        return

    if data.startswith("DEL_PROD_CAT:"):
        cid = int(data.split(":", 1)[1])
        sub_cats = get_sub_categories(cid)
        if sub_cats:
            rows = []
            for i in range(0, len(sub_cats), 2):
                row = [InlineKeyboardButton(f"{sub_cats[i]['name']}", callback_data=f"DEL_PROD_CAT:{sub_cats[i]['id']}")]
                if i + 1 < len(sub_cats):
                    row.append(InlineKeyboardButton(f"{sub_cats[i+1]['name']}", callback_data=f"DEL_PROD_CAT:{sub_cats[i+1]['id']}"))
                rows.append(row)
            rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="PROD_DEL_LIST")])
            await q.message.edit_text("اختر قائمة فرعية لعرض المنتجات:", reply_markup=InlineKeyboardMarkup(rows))
        else:
            prods = get_products_by_cat(cid)
            if not prods:
                await q.message.edit_text("لا توجد منتجات في هذه القائمة.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع", callback_data="PROD_DEL_LIST")]]))
                return
            rows = []
            for i in range(0, len(prods), 2):
                row = [InlineKeyboardButton(f"{prods[i]['name']}", callback_data=f"PROD_DEL:{prods[i]['id']}")]
                if i + 1 < len(prods):
                    row.append(InlineKeyboardButton(f"{prods[i+1]['name']}", callback_data=f"PROD_DEL:{prods[i+1]['id']}"))
                rows.append(row)
            rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="PROD_DEL_LIST")])
            await q.message.edit_text("اختر منتجاً لحذفه:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("PROD_DEL:"):
        pid = int(data.split(":", 1)[1])
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("DELETE FROM products WHERE id=?", (pid,))
        conn.commit()
        conn.close()
        await q.message.reply_text("✅ تم حذف المنتج بنجاح.")
        return

    if data == "PROD_MOVE_LIST":
        await show_admin_categories_for_edit(update, context, "move_prod")
        return

    if data.startswith("MOVE_PROD_CAT:"):
        cid = int(data.split(":", 1)[1])
        sub_cats = get_sub_categories(cid)
        if sub_cats:
            rows = []
            for i in range(0, len(sub_cats), 2):
                row = [InlineKeyboardButton(f"{sub_cats[i]['name']}", callback_data=f"MOVE_PROD_CAT:{sub_cats[i]['id']}")]
                if i + 1 < len(sub_cats):
                    row.append(InlineKeyboardButton(f"{sub_cats[i+1]['name']}", callback_data=f"MOVE_PROD_CAT:{sub_cats[i+1]['id']}"))
                rows.append(row)
            rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="PROD_MOVE_LIST")])
            await q.message.edit_text("اختر قائمة فرعية لعرض المنتجات:", reply_markup=InlineKeyboardMarkup(rows))
        else:
            prods = get_products_by_cat(cid)
            if not prods:
                await q.message.edit_text("لا توجد منتجات في هذه القائمة.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع", callback_data="PROD_MOVE_LIST")]]))
                return
            rows = []
            for i in range(0, len(prods), 2):
                row = [InlineKeyboardButton(f"{prods[i]['name']}", callback_data=f"PROD_MOVE:{prods[i]['id']}")]
                if i + 1 < len(prods):
                    row.append(InlineKeyboardButton(f"{prods[i+1]['name']}", callback_data=f"PROD_MOVE:{prods[i+1]['id']}"))
                rows.append(row)
            rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="PROD_MOVE_LIST")])
            await q.message.edit_text("اختر منتجاً لنقله:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("PROD_MOVE:"):
        pid = int(data.split(":", 1)[1])
        context.user_data["pid"] = pid
        await show_admin_categories_for_edit(update, context, "move_prod_target")
        return

    if data.startswith("PROD_MOVE_TARGET:"):
        pid = context.user_data.get("pid")
        target_cid = int(data.split(":", 1)[1])
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("UPDATE products SET category_id=? WHERE id=?", (target_cid, pid))
        conn.commit()
        conn.close()
        await q.message.edit_text("✅ تم نقل المنتج بنجاح.")
        del context.user_data["pid"]
        return

    if data == "ADM_USERS":
        await q.message.edit_text("إدارة المستخدمين:", reply_markup=users_menu_kb())
        return

    if data == "USR_CREDIT":
        context.user_data.clear()
        context.user_data["flow"] = "adm_usr_credit_id"
        await q.message.reply_text("أرسل آيدي المستخدم المراد شحنه:")
        return

    if data == "USR_DEBIT":
        context.user_data.clear()
        context.user_data["flow"] = "adm_usr_debit_id"
        await q.message.reply_text("أرسل آيدي المستخدم المراد سحب الرصيد منه:")
        return

    if data == "ADM_SETTINGS":
        await q.message.edit_text("إعدادات البوت:", reply_markup=settings_menu_kb())
        return

    if data == "SET_SUPPORT":
        context.user_data.clear()
        context.user_data["flow"] = "adm_set_support"
        await q.message.reply_text("أرسل يوزر الدعم (بدون @):")
        return

    if data == "SET_SHAM_CODE":
        context.user_data.clear()
        context.user_data["flow"] = "adm_set_sham_code"
        await q.message.reply_text("أرسل صورة كود شام كاش:")
        return

    if data == "SET_SHAM_ADDR":
        context.user_data.clear()
        context.user_data["flow"] = "adm_set_sham_addr"
        await q.message.reply_text("أرسل عنوان شام كاش:")
        return

    if data == "SET_GROUP_TOPUP":
        context.user_data.clear()
        context.user_data["flow"] = "adm_set_group_topup"
        await q.message.reply_text("أرسل آيدي مجموعة الشحن:")
        return

    if data == "SET_GROUP_ORDERS":
        context.user_data.clear()
        context.user_data["flow"] = "adm_set_group_orders"
        await q.message.reply_text("أرسل آيدي مجموعة الطلبات:")
        return

    if data == "SET_ADMINS":
        context.user_data.clear()
        context.user_data["flow"] = "adm_set_admins"
        await q.message.reply_text("أرسل آيديات الأدمن مفصولة بفاصلة (,):")
        return

    if data == "ADM_BROADCAST":
        context.user_data.clear()
        context.user_data["flow"] = "adm_broadcast"
        await q.message.reply_text("أرسل الرسالة التي تريد إرسالها لجميع المستخدمين.")
        return

async def show_admin_categories_for_edit(update: Update, context: ContextTypes.DEFAULT_TYPE, next_action: str):
    """A helper function to display categories for admin editing purposes."""
    q = update.callback_query
    cats = get_categories()
    if not cats:
        await q.message.edit_text("لا توجد قوائم رئيسية بعد.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ رجوع", callback_data="ADM_PRODS")]]))
        return

    rows = []
    for i in range(0, len(cats), 2):
        button_text = f"{cats[i]['name']}"
        callback_data = f"{next_action}_cat:{cats[i]['id']}"
        row = [InlineKeyboardButton(button_text, callback_data=callback_data)]
        if i + 1 < len(cats):
            button_text2 = f"{cats[i+1]['name']}"
            callback_data2 = f"{next_action}_cat:{cats[i+1]['id']}"
            row.append(InlineKeyboardButton(button_text2, callback_data=callback_data2))
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ رجوع", callback_data="ADM_PRODS")])
    await q.message.edit_text("اختر قائمة:", reply_markup=InlineKeyboardMarkup(rows))

def get_categories_with_parent():
    """Retrieves all categories that have a parent (i.e., sub-categories)."""
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM categories WHERE parent_id IS NOT NULL ORDER BY id ASC")
    rows = cur.fetchall()
    conn.close()
    return rows

# -----------------------------------------------------------------------------
# Main Application Entry Point
# -----------------------------------------------------------------------------

def main():
    """The main function that sets up and runs the bot."""
    # Initialize the database and ensure all tables exist.
    init_db()

    # Build the Telegram application instance.
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Add command handlers.
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))

    # Add callback query handlers.
    app.add_handler(CallbackQueryHandler(on_main_buttons, pattern=r"^(BUY|TOPUP_MENU|SUPPORT|ACCOUNT|NEWS|CHECK_SUB|BACK_TO_MAIN|SUPPORT_CONTACT)$"))
    app.add_handler(CallbackQueryHandler(on_topup_buttons, pattern=r"^(SHOW_SHAM_CODE|SHOW_SHAM_ADDR|TOPUP_START)$"))
    app.add_handler(CallbackQueryHandler(on_buy_flow, pattern=r"^BUY_|CHANGE_QTY"))
    app.add_handler(CallbackQueryHandler(on_admin_buttons, pattern=r"^(ADM_|CAT_|PROD_|USR_|SET_|ADD_PROD_|TARGET_CAT|EDIT_PROD_|DEL_PROD_|MOVE_PROD_|EDIT_NEWS).*$"))
    app.add_handler(CallbackQueryHandler(on_group_actions, pattern=r"^(TP_|ORD_).*$"))

    # Add a message handler for text messages that are not commands.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_user_message))

    # Run the bot until the user presses Ctrl-C.
    app.run_polling(drop_pending_updates=True)

    # Start the bot's polling loop.
    app.run_polling()


if __name__ == '__main__':
    main()