"""
Telegram Bot — Full Featured
Requirements: pip install python-telegram-bot httpx
Deploy: Push telegram_bot.py + requirements.txt + Procfile to GitHub, then Railway
"""

import asyncio
import html
import json
import logging
import os
import sqlite3

import httpx
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# ======================================================================
# CONFIG — edit these
# ======================================================================
BOT_TOKEN      = os.environ.get("BOT_TOKEN", "8495656887:AAHaeHqi77k3ASgGJ4G2_-GfuxJbzO7Cejw")
BOT_USERNAME   = "liesworlds2bot"
OWNER_ID       = 8790645158

SUBSCRIPTION_CONTACT = "@liesworlds"
DEVELOPER_CONTACT    = "@liesworlds"

CREDITS_PER_REFERRAL = 2
CREDITS_PER_USE      = 1
CREDITS_ON_SIGNUP    = 2

# 3 APIs — all GET with ?number=VALUE
# Replace the URLs with your real endpoints.
API_CONFIGS = [
    {
        "emoji": "🪄", "name": "Casting Magic",
        "url": "https://bomber-production-d127.up.railway.app//bomber",
        "method": "GET", "param_style": "query", "param_name": "number",
    },
    {
        "emoji": "🍳", "name": "Cooking Pixels",
        "url": "https://bomber-production-d127.up.railway.app//bomber",
        "method": "GET", "param_style": "query", "param_name": "number",
    },
    {
        "emoji": "🎉", "name": "Adding Sparkle",
        "url": "https://bomber-production-d127.up.railway.app//bomber",
        "method": "GET", "param_style": "query", "param_name": "number",
    },
]

# Dashboard video shown in My Profile.
# Send your video to the bot once in private chat, check console for file_id,
# paste it here. Admin can also update it via /setvideo <file_id>.
PROFILE_VIDEO = os.environ.get("PROFILE_VIDEO", "BAACAgUAAxkBAAFRdYpqeX8TQHmU4taNopyqEvCFP1S-lQACxR4AAreX0FehTWtCPlqNbT0E")

# Force-join channels — empty here, add via /addchannel in admin panel
FORCE_CHANNELS = []
# ======================================================================


# -----------------------------------------------------------------------
# Database
# -----------------------------------------------------------------------
DB_PATH = "bot.db"

def db_init():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT, first_name TEXT,
            credits INTEGER DEFAULT 0,
            verified INTEGER DEFAULT 0,
            referred_by INTEGER,
            referral_credited INTEGER DEFAULT 0,
            premium INTEGER DEFAULT 0
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY, added_by INTEGER
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS force_channels (
            chat_id TEXT PRIMARY KEY, name TEXT, url TEXT
        )""")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "premium" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN premium INTEGER DEFAULT 0")
    # Seed hardcoded channels once if table is empty
    if FORCE_CHANNELS and conn.execute("SELECT COUNT(*) FROM force_channels").fetchone()[0] == 0:
        for ch in FORCE_CHANNELS:
            conn.execute("INSERT OR IGNORE INTO force_channels (chat_id,name,url) VALUES (?,?,?)",
                         (str(ch["chat_id"]), ch["name"], ch["url"]))
    conn.commit()
    conn.close()

def _q(sql, params=()):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(sql, params).fetchone()
    conn.close()
    return row

def _qa(sql, params=()):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows

def _ex(sql, params=()):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(sql, params)
    conn.commit()
    affected = cur.rowcount
    conn.close()
    return affected

def db_get_user(uid): return _q("SELECT user_id,username,first_name,credits,verified,referred_by,referral_credited,premium FROM users WHERE user_id=?", (uid,))
def db_create_user(uid, username, first_name, referred_by=None): _ex("INSERT OR IGNORE INTO users (user_id,username,first_name,credits,verified,referred_by) VALUES (?,?,?,0,0,?)", (uid, username, first_name, referred_by))
def db_set_verified(uid): _ex("UPDATE users SET verified=1 WHERE user_id=?", (uid,))
def db_add_credits(uid, n): _ex("UPDATE users SET credits=credits+? WHERE user_id=?", (n, uid))
def db_set_credits(uid, n): return _ex("UPDATE users SET credits=? WHERE user_id=?", (n, uid)) > 0
def db_set_premium(uid, v): return _ex("UPDATE users SET premium=? WHERE user_id=?", (v, uid)) > 0
def db_mark_ref_credited(uid): _ex("UPDATE users SET referral_credited=1 WHERE user_id=?", (uid,))
def db_count_referrals(uid): return (_q("SELECT COUNT(*) FROM users WHERE referred_by=? AND referral_credited=1", (uid,)) or (0,))[0]

def db_get_setting(key, default=None):
    r = _q("SELECT value FROM settings WHERE key=?", (key,))
    return r[0] if r else default

def db_set_setting(key, val): _ex("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, val))
def is_bot_enabled(): return db_get_setting("bot_enabled","1") == "1"
def get_profile_video(): return db_get_setting("profile_video", PROFILE_VIDEO)

def db_is_admin(uid): return uid == OWNER_ID or bool(_q("SELECT 1 FROM admins WHERE user_id=?", (uid,)))
def db_add_admin(uid, by): _ex("INSERT OR IGNORE INTO admins(user_id,added_by) VALUES(?,?)", (uid, by))
def db_remove_admin(uid): return _ex("DELETE FROM admins WHERE user_id=?", (uid,)) > 0
def db_list_admins(): return [r[0] for r in _qa("SELECT user_id FROM admins")]

def db_add_channel(chat_id, name, url): _ex("INSERT INTO force_channels(chat_id,name,url) VALUES(?,?,?) ON CONFLICT(chat_id) DO UPDATE SET name=excluded.name,url=excluded.url", (str(chat_id), name, url))
def db_remove_channel(chat_id): return _ex("DELETE FROM force_channels WHERE chat_id=?", (str(chat_id),)) > 0
def db_list_channels(): return [{"chat_id":r[0],"name":r[1],"url":r[2]} for r in _qa("SELECT chat_id,name,url FROM force_channels")]

db_init()


# -----------------------------------------------------------------------
# API caller
# -----------------------------------------------------------------------
async def call_api(cfg: dict, code: str) -> dict:
    method = cfg.get("method","GET").upper()
    style  = cfg.get("param_style","query")
    pname  = cfg.get("param_name","number")
    hdrs   = cfg.get("headers") or {}
    url    = cfg["url"]

    async with httpx.AsyncClient(timeout=60) as client:
        if style == "path":
            url = f"{url.rstrip('/')}/{code}"
            params = None
        elif style == "query":
            params = {pname: code}
        else:
            params = None

        if method == "GET":
            resp = await client.get(url, params=params, headers=hdrs)
        else:
            if style == "json":
                resp = await client.post(url, json={pname: code}, headers=hdrs)
            else:
                resp = await client.post(url, params=params, headers=hdrs)

        resp.raise_for_status()
        try:
            return resp.json()
        except Exception:
            return {"data": resp.text}

API_FUNCS = [lambda code, c=cfg: call_api(c, code) for cfg in API_CONFIGS]


# -----------------------------------------------------------------------
# Keyboards
# -----------------------------------------------------------------------
def main_reply_keyboard() -> ReplyKeyboardMarkup:
    """Bottom keyboard — like the screenshot."""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🚀 USE"),        KeyboardButton("✨ PREMIUM USE")],
            [KeyboardButton("🎁 Refer & Earn"), KeyboardButton("👤 My Profile")],
            [KeyboardButton("💎 Subscription"), KeyboardButton("👨‍💻 Developer")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )

def join_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(f"📢 {c['name']}", url=c["url"])] for c in db_list_channels()]
    rows.append([InlineKeyboardButton("✅  Verify", callback_data="verify")])
    return InlineKeyboardMarkup(rows)

def stop_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🛑  STOP", callback_data="stop")]])

def back_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙  Back", callback_data="noop")]])

def admin_panel_keyboard(is_owner=False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("💰 Credits",    callback_data="adm_credits"),
         InlineKeyboardButton("💎 Premium",    callback_data="adm_premium")],
        [InlineKeyboardButton("📢 Channels",   callback_data="adm_channels"),
         InlineKeyboardButton("👑 Admins",     callback_data="adm_admins")],
        [InlineKeyboardButton("ℹ️ User Info",  callback_data="adm_userinfo"),
         InlineKeyboardButton("⚙️ Bot Status", callback_data="adm_status")],
        [InlineKeyboardButton("🎬 Dashboard Video", callback_data="adm_video")],
    ]
    return InlineKeyboardMarkup(rows)

def admin_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="adm_home")]])


# -----------------------------------------------------------------------
# Progress helpers
# -----------------------------------------------------------------------
SPINNER = ["🌑","🌒","🌓","🌔","🌕","🌖","🌗","🌘"]
TIPS = [
    "🐢 Turbo mode... loading at snail speed 😅",
    "🍕 Order a pizza, this might take a sec...",
    "🧠 AI is thinking really hard right now...",
    "🎩 Pulling something cool out of the hat...",
    "🐸 Even frogs wait for good things...",
    "🚀 Houston, we have liftoff...",
    "🍿 Grab popcorn, show's about to start...",
    "🦄 Unicorns are working overtime for you...",
    "🎲 Rolling the dice of destiny...",
    "😴 Don't fall asleep, almost there...",
]

def progress_bar(done, total):
    return "🟩"*done + "⬜️"*(total-done) + f"  {int(done/total*100)}%"

def build_progress(done_flags, spinner, tip, elapsed):
    total = len(done_flags)
    done  = sum(done_flags)
    lines = []
    for i, cfg in enumerate(API_CONFIGS):
        if done_flags[i]:
            lines.append(f"  ✅  {cfg['emoji']} {cfg['name']}")
        else:
            lines.append(f"  {spinner}  {cfg['emoji']} {cfg['name']}")
    return (
        f"{spinner}{spinner}{spinner} *Processing...* {spinner}{spinner}{spinner}\n\n"
        f"{progress_bar(done, total)}\n\n"
        + "\n".join(lines)
        + f"\n\n_{tip}_\n⏱️ {elapsed}s"
    )


# -----------------------------------------------------------------------
# Membership check
# -----------------------------------------------------------------------
async def is_member_of_all(context, user_id):
    for ch in db_list_channels():
        try:
            m = await context.bot.get_chat_member(ch["chat_id"], user_id)
            if m.status in ("left","kicked"):
                return False
        except Exception as e:
            log.warning("Membership check failed %s: %s", ch["chat_id"], e)
            return False
    return True


# -----------------------------------------------------------------------
# /start
# -----------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    referred_by = None
    if args and args[0].startswith("ref_"):
        try:
            c = int(args[0].replace("ref_",""))
            if c != user.id:
                referred_by = c
        except ValueError:
            pass

    if not db_get_user(user.id):
        db_create_user(user.id, user.username or "", user.first_name or "", referred_by)

    row = db_get_user(user.id)
    verified = row[4]

    if verified:
        await update.message.reply_text(
            f"👋 *Welcome back, {user.first_name}!*\n\nChoose an option below 👇",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_reply_keyboard(),
        )
        return

    channels = db_list_channels()
    if not channels:
        # No channels configured — auto-verify
        db_set_verified(user.id)
        db_add_credits(user.id, CREDITS_ON_SIGNUP)
        await update.message.reply_text(
            f"✅ *Welcome, {user.first_name}!*\n\n"
            f"🎁 You've received *{CREDITS_ON_SIGNUP} free credits* to get started!\n\n"
            "Choose an option below 👇",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_reply_keyboard(),
        )
        return

    await update.message.reply_text(
        "🔐 *Access Restricted*\n\n"
        "Please join the channel(s) below to unlock the bot, then tap *Verify* ✅",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=join_keyboard(),
    )


# -----------------------------------------------------------------------
# Verify
# -----------------------------------------------------------------------
async def on_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user

    if not await is_member_of_all(context, user.id):
        await query.answer("❌ You haven't joined all required channels yet!", show_alert=True)
        return

    row = db_get_user(user.id)
    was_verified = row[4] if row else 0

    if not was_verified:
        db_set_verified(user.id)
        db_add_credits(user.id, CREDITS_ON_SIGNUP)
        referred_by    = row[5] if row else None
        already_credited = row[6] if row else 0
        if referred_by and not already_credited:
            db_add_credits(referred_by, CREDITS_PER_REFERRAL)
            db_mark_ref_credited(user.id)
        try:
            await context.bot.send_message(
                OWNER_ID,
                f"🆕 *New user verified!*\n\n"
                f"👤 {user.first_name}\n"
                f"🔗 @{user.username or 'N/A'}\n"
                f"🆔 `{user.id}`",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass

    await query.edit_message_text(
        f"✅ *Verified successfully!*\n\n"
        f"Welcome, {user.first_name} 🎉\n"
        f"🎁 You've received *{CREDITS_ON_SIGNUP} free credits* to get started!\n\n"
        "Choose an option below 👇",
        parse_mode=ParseMode.MARKDOWN,
    )
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="🏠 *Main Menu*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_reply_keyboard(),
    )


# -----------------------------------------------------------------------
# Reply keyboard message handler
# -----------------------------------------------------------------------
MENU_BUTTONS = {"🚀 USE", "✨ PREMIUM USE", "🎁 Refer & Earn", "👤 My Profile", "💎 Subscription", "👨‍💻 Developer"}

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    user = update.effective_user

    # Admin forward-channel flow
    if context.user_data.get("awaiting_channel_forward"):
        fwd = getattr(update.message, "forward_from_chat", None)
        if fwd and fwd.type == "channel":
            context.user_data["awaiting_channel_forward"] = False
            await _try_add_channel(update, context, fwd.id)
        else:
            await update.message.reply_text("⚠️ Please forward a message directly from the channel.")
        return

    # If user presses a menu button while awaiting code — cancel code flow
    if context.user_data.get("awaiting_code") and text in MENU_BUTTONS:
        context.user_data["awaiting_code"] = False

    # 10-digit code flow
    if context.user_data.get("awaiting_code"):
        if not text.isdigit() or len(text) != 10:
            await update.message.reply_text(
                "❌ *Invalid Code*\n\nMust be exactly *10 digits*. Try again 🔁",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        context.user_data["awaiting_code"] = False
        row = db_get_user(user.id)
        premium = row[7] if row else 0
        status_msg = await update.message.reply_text(
            build_progress([False]*len(API_CONFIGS), SPINNER[0], TIPS[0], 0),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=stop_keyboard(),
        )
        task = asyncio.create_task(run_all_apis(text, update, context, status_msg, user.id, premium))
        context.user_data["running_task"] = task
        return

    # Main menu buttons
    if text == "🚀 USE":
        await handle_use(update, context)
    elif text == "✨ PREMIUM USE":
        await update.message.reply_text(
            "✨ *Premium Use — Coming Soon!*\n\n"
            "We're cooking something special for premium users 🔥\nStay tuned!",
            parse_mode=ParseMode.MARKDOWN,
        )
    elif text == "🎁 Refer & Earn":
        await handle_refer(update, context)
    elif text == "👤 My Profile":
        await handle_profile(update, context)
    elif text == "💎 Subscription":
        await update.message.reply_text(
            "💎 *Subscription*\n\n"
            "Want unlimited access and premium perks?\n\n"
            f"📩 Contact {SUBSCRIPTION_CONTACT} to buy a subscription!",
            parse_mode=ParseMode.MARKDOWN,
        )
    elif text == "👨‍💻 Developer":
        await update.message.reply_text(
            "👨‍💻 *Developer*\n\n"
            f"For support, bugs, or business inquiries 👉 {DEVELOPER_CONTACT} 🚀",
            parse_mode=ParseMode.MARKDOWN,
        )


# -----------------------------------------------------------------------
# Feature handlers
# -----------------------------------------------------------------------
async def handle_use(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    row = db_get_user(user.id)
    credits = row[3] if row else 0
    premium = row[7] if row else 0

    if not premium and credits < CREDITS_PER_USE:
        await update.message.reply_text(
            "🚫 *Insufficient Credits*\n\n"
            "You don't have enough credits to use this feature.\n\n"
            "🎁 Earn free credits via *Refer & Earn*, or 💎 grab a *Subscription*!",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    context.user_data["awaiting_code"] = True
    await update.message.reply_text(
        "🔑 *Send Me the Code* 🔑\n\nPlease send your *10-digit code* to continue 👇",
        parse_mode=ParseMode.MARKDOWN,
    )


async def handle_refer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{user.id}"
    refs = db_count_referrals(user.id)
    await update.message.reply_text(
        "🎁 *Refer & Earn*\n\n"
        f"Invite your friends and earn *{CREDITS_PER_REFERRAL} credits* for every "
        "friend who joins and verifies! 🚀\n\n"
        f"👥 Total successful referrals: *{refs}*\n\n"
        "🔗 *Your referral link:*\n"
        f"`{link}`\n\n"
        "_Tap the link above to copy it, then share it with your friends!_",
        parse_mode=ParseMode.MARKDOWN,
    )


async def handle_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    row  = db_get_user(user.id)
    credits = row[3] if row else 0
    premium = row[7] if row else 0
    status  = "💎 *PREMIUM* ✨" if premium else "🆓 Free User"

    caption = (
        "👤 *Your Profile*\n\n"
        f"📛 Name: {user.first_name}\n"
        f"🔗 Username: @{user.username or 'N/A'}\n"
        f"🆔 User ID: `{user.id}`\n"
        f"💰 Credits: *{credits}*\n"
        f"⭐ Status: {status}\n"
    )
    video = get_profile_video()
    if video and video != "NONE":
        try:
            await context.bot.send_video(
                chat_id=update.effective_chat.id,
                video=video,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        except Exception as e:
            log.warning("Profile video send failed: %s", e)
    # Fallback — plain text if video fails
    await update.message.reply_text(caption, parse_mode=ParseMode.MARKDOWN)


# -----------------------------------------------------------------------
# STOP button
# -----------------------------------------------------------------------
async def on_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Stopping... 🛑")
    task = context.user_data.get("running_task")
    if task and not task.done():
        task.cancel()
        try:
            await query.edit_message_text(
                "🛑 *Stopped.*\n\nProcess cancelled by you.",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass
    else:
        await query.answer("Nothing running right now.", show_alert=True)


# -----------------------------------------------------------------------
# API runner
# -----------------------------------------------------------------------
def format_result(cfg, response) -> str:
    if isinstance(response, Exception):
        return f"❌ <b>{html.escape(cfg['emoji'])} {html.escape(cfg['name'])}</b>\nFailed to fetch result."
    if isinstance(response, (dict, list)):
        pretty = json.dumps(response, indent=2, ensure_ascii=False)
    else:
        pretty = str(response)
    return f"{cfg['emoji']} <b>{html.escape(cfg['name'])}</b>\n<pre>{html.escape(pretty)}</pre>"


async def run_all_apis(code, update, context, status_msg, user_id, premium=0):
    """Runs APIs in a continuous loop until user hits STOP.
    Progress and results are edited in-place (same messages, quoted)."""
    result_msg = None   # will hold the result message (edited each round)
    round_num  = 0

    try:
        while True:
            round_num += 1
            tasks = [asyncio.ensure_future(fn(code)) for fn in API_FUNCS]
            tick  = 0
            TICK  = 0.5

            # -- progress loop --
            while not all(t.done() for t in tasks):
                spinner    = SPINNER[tick % len(SPINNER)]
                tip        = TIPS[(tick // 4) % len(TIPS)]
                done_flags = [t.done() for t in tasks]
                elapsed    = int(tick * TICK)
                try:
                    await status_msg.edit_text(
                        build_progress(done_flags, spinner, tip, elapsed),
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=stop_keyboard(),
                    )
                except Exception:
                    pass
                await asyncio.sleep(TICK)
                tick += 1

            # -- collect results --
            results = []
            for t in tasks:
                try:
                    results.append(t.result())
                except Exception as e:
                    results.append(e)

            # -- show 100% --
            try:
                await status_msg.edit_text(
                    build_progress([True]*len(API_CONFIGS), "✅", "🎉 Done! Loading results...", int(tick*TICK)),
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass

            if all(isinstance(r, Exception) for r in results):
                blocks = "⚠️ <b>All APIs failed this round.</b>"
            else:
                blocks = [format_result(API_CONFIGS[i], results[i]) for i in range(len(results))]
                blocks = (
                    f"🔄 <b>Round {round_num} — Results</b>\n\n"
                    + "\n\n".join(blocks)
                )

            db_add_credits(user_id, -CREDITS_PER_USE if not premium else 0)

            # Edit existing result message or create a new quoted one
            if result_msg is None:
                result_msg = await update.message.reply_text(
                    blocks,
                    parse_mode=ParseMode.HTML,
                    reply_markup=stop_keyboard(),
                )
            else:
                try:
                    await result_msg.edit_text(
                        blocks,
                        parse_mode=ParseMode.HTML,
                        reply_markup=stop_keyboard(),
                    )
                except Exception:
                    pass

            # Reset progress bar for next round
            try:
                await status_msg.edit_text(
                    build_progress([False]*len(API_CONFIGS), SPINNER[0], TIPS[0], 0),
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=stop_keyboard(),
                )
            except Exception:
                pass

            # Small gap between rounds
            await asyncio.sleep(1.5)

    except asyncio.CancelledError:
        for t in tasks if 'tasks' in dir() else []:
            if not t.done():
                t.cancel()
        try:
            await status_msg.edit_text(
                "🛑 *Stopped.*\n\nProcess cancelled by you.",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass
        raise


# -----------------------------------------------------------------------
# Admin decorators
# -----------------------------------------------------------------------
def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not db_is_admin(update.effective_user.id):
            await update.message.reply_text("🚫 Admins only.")
            return
        return await func(update, context)
    return wrapper

def owner_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != OWNER_ID:
            await update.message.reply_text("🚫 Owner only.")
            return
        return await func(update, context)
    return wrapper


# -----------------------------------------------------------------------
# Admin commands
# -----------------------------------------------------------------------
@admin_only
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    is_owner = update.effective_user.id == OWNER_ID
    await update.message.reply_text(
        "🛠️ *Admin Panel*\n\nChoose a category below 👇",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=admin_panel_keyboard(is_owner),
    )

@admin_only
async def admin_addcredit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        tid, amt = int(context.args[0]), int(context.args[1])
    except (IndexError, ValueError):
        await update.message.reply_text("⚠️ Usage: `/addcredit <user_id> <amount>`", parse_mode=ParseMode.MARKDOWN); return
    if not db_get_user(tid):
        await update.message.reply_text("❌ User not found."); return
    db_add_credits(tid, amt)
    await update.message.reply_text(f"✅ Added *{amt}* credits to `{tid}`.", parse_mode=ParseMode.MARKDOWN)

@admin_only
async def admin_removecredit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        tid, amt = int(context.args[0]), int(context.args[1])
    except (IndexError, ValueError):
        await update.message.reply_text("⚠️ Usage: `/removecredit <user_id> <amount>`", parse_mode=ParseMode.MARKDOWN); return
    if not db_get_user(tid):
        await update.message.reply_text("❌ User not found."); return
    db_add_credits(tid, -amt)
    await update.message.reply_text(f"✅ Removed *{amt}* credits from `{tid}`.", parse_mode=ParseMode.MARKDOWN)

@admin_only
async def admin_setcredit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        tid, amt = int(context.args[0]), int(context.args[1])
    except (IndexError, ValueError):
        await update.message.reply_text("⚠️ Usage: `/setcredit <user_id> <amount>`", parse_mode=ParseMode.MARKDOWN); return
    if not db_set_credits(tid, amt):
        await update.message.reply_text("❌ User not found."); return
    await update.message.reply_text(f"✅ Set `{tid}`'s credits to *{amt}*.", parse_mode=ParseMode.MARKDOWN)

@admin_only
async def admin_addpremium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        tid = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("⚠️ Usage: `/addpremium <user_id>`", parse_mode=ParseMode.MARKDOWN); return
    if not db_set_premium(tid, 1):
        await update.message.reply_text("❌ User not found."); return
    await update.message.reply_text(f"💎 `{tid}` is now *PREMIUM*.", parse_mode=ParseMode.MARKDOWN)
    try:
        await context.bot.send_message(tid, "💎 *You've been upgraded to PREMIUM!*\n\nUnlimited USE — no credits needed! 🚀", parse_mode=ParseMode.MARKDOWN)
    except Exception: pass

@admin_only
async def admin_removepremium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        tid = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("⚠️ Usage: `/removepremium <user_id>`", parse_mode=ParseMode.MARKDOWN); return
    if not db_set_premium(tid, 0):
        await update.message.reply_text("❌ User not found."); return
    await update.message.reply_text(f"✅ Premium removed from `{tid}`.", parse_mode=ParseMode.MARKDOWN)

@admin_only
async def admin_userinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        tid = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("⚠️ Usage: `/userinfo <user_id>`", parse_mode=ParseMode.MARKDOWN); return
    row = db_get_user(tid)
    if not row:
        await update.message.reply_text("❌ User not found."); return
    _, uname, fname, credits, verified, ref_by, _, premium = row
    await update.message.reply_text(
        f"📋 *User Info*\n\n"
        f"📛 {fname}\n🔗 @{uname or 'N/A'}\n🆔 `{tid}`\n"
        f"💰 Credits: *{credits}*\n"
        f"⭐ Premium: {'Yes 💎' if premium else 'No'}\n"
        f"✅ Verified: {'Yes' if verified else 'No'}\n"
        f"👥 Referred by: `{ref_by or 'N/A'}`",
        parse_mode=ParseMode.MARKDOWN,
    )

@admin_only
async def admin_offbot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db_set_setting("bot_enabled","0")
    await update.message.reply_text("🔴 *Bot is now OFF.*", parse_mode=ParseMode.MARKDOWN)

@admin_only
async def admin_onbot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db_set_setting("bot_enabled","1")
    await update.message.reply_text("🟢 *Bot is now ON.*", parse_mode=ParseMode.MARKDOWN)

@admin_only
async def admin_listadmins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ids = db_list_admins()
    lines = [f"👑 `{OWNER_ID}` (owner)"] + [f"🛠️ `{i}`" for i in ids]
    await update.message.reply_text("📋 *Admins*\n\n" + "\n".join(lines), parse_mode=ParseMode.MARKDOWN)

@owner_only
async def admin_addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        tid = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("⚠️ Usage: `/addadmin <user_id>`", parse_mode=ParseMode.MARKDOWN); return
    db_add_admin(tid, update.effective_user.id)
    await update.message.reply_text(f"✅ `{tid}` is now an admin.", parse_mode=ParseMode.MARKDOWN)
    try:
        await context.bot.send_message(tid, "🛠️ *You've been granted admin access!*\n\nSend /admin to manage the bot.", parse_mode=ParseMode.MARKDOWN)
    except Exception: pass

@owner_only
async def admin_removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        tid = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("⚠️ Usage: `/removeadmin <user_id>`", parse_mode=ParseMode.MARKDOWN); return
    if tid == OWNER_ID:
        await update.message.reply_text("🚫 Owner can't be removed."); return
    if not db_remove_admin(tid):
        await update.message.reply_text("❌ Not an admin."); return
    await update.message.reply_text(f"✅ `{tid}` removed from admins.", parse_mode=ParseMode.MARKDOWN)

@admin_only
async def admin_addchannel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        context.user_data["awaiting_channel_forward"] = True
        await update.message.reply_text(
            "📢 *Add Force-Join Channel*\n\n"
            "For *public* channel: `/addchannel @username`\n"
            "For *private* channel: forward any post from that channel here 👇\n\n"
            "⚠️ Bot must be *admin* in that channel first!",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    raw = context.args[0]
    username = raw.replace("https://t.me/","").replace("http://t.me/","").lstrip("@").strip()
    if username.startswith("+") or "joinchat" in username:
        await update.message.reply_text("⚠️ Private channel — run `/addchannel` with no args and forward a post instead.", parse_mode=ParseMode.MARKDOWN)
        return
    await _try_add_channel(update, context, f"@{username}")

@admin_only
async def admin_removechannel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        cid = context.args[0]
    except IndexError:
        await update.message.reply_text("⚠️ Usage: `/removechannel <chat_id>`", parse_mode=ParseMode.MARKDOWN); return
    if not db_remove_channel(cid):
        await update.message.reply_text("❌ Channel not found."); return
    await update.message.reply_text("✅ Channel removed.")

@admin_only
async def admin_listchannels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chs = db_list_channels()
    if not chs:
        await update.message.reply_text("📭 No channels configured. Use /addchannel."); return
    lines = [f"📢 *{c['name']}*\nID: `{c['chat_id']}`" for c in chs]
    await update.message.reply_text("📋 *Force-Join Channels*\n\n" + "\n\n".join(lines), parse_mode=ParseMode.MARKDOWN)

@admin_only
async def admin_setvideo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set dashboard profile video. Usage: /setvideo <file_id>  OR reply to a video."""
    # Check if replying to a video message
    if update.message.reply_to_message and update.message.reply_to_message.video:
        file_id = update.message.reply_to_message.video.file_id
    elif context.args:
        file_id = context.args[0]
    else:
        context.user_data["awaiting_video"] = True
        await update.message.reply_text(
            "🎬 *Set Dashboard Video*\n\n"
            "Send the video file right now 👇\n"
            "_(Or reply to any video with /setvideo)_",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    db_set_setting("profile_video", file_id)
    await update.message.reply_text("✅ *Dashboard video updated!*\n\nUsers will see the new video in My Profile.", parse_mode=ParseMode.MARKDOWN)
    try:
        await context.bot.send_video(update.effective_chat.id, file_id, caption="Preview of new dashboard video 👆")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Video set but preview failed: {e}")

@admin_only
async def admin_clearvideo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db_set_setting("profile_video", "NONE")
    await update.message.reply_text("✅ Dashboard video removed. Profile will show text only.")


async def _try_add_channel(update, context, chat_ref):
    try:
        chat = await context.bot.get_chat(chat_ref)
    except Exception as e:
        await update.message.reply_text(f"❌ Couldn't find channel.\n`{e}`", parse_mode=ParseMode.MARKDOWN); return
    try:
        bm = await context.bot.get_chat_member(chat.id, context.bot.id)
        if bm.status not in ("administrator","creator"):
            await update.message.reply_text(f"⚠️ Bot is not admin in *{html.escape(chat.title)}*. Make it admin first.", parse_mode=ParseMode.MARKDOWN); return
    except Exception as e:
        await update.message.reply_text(f"❌ Can't verify admin status.\n`{e}`", parse_mode=ParseMode.MARKDOWN); return

    url = f"https://t.me/{chat.username}" if chat.username else (chat.invite_link or "")
    if not url:
        try: url = await context.bot.export_chat_invite_link(chat.id)
        except Exception: url = ""
    db_add_channel(chat.id, chat.title, url)
    await update.message.reply_text(f"✅ *{html.escape(chat.title)}* added to required channels! 🎉", parse_mode=ParseMode.MARKDOWN)


# -----------------------------------------------------------------------
# Admin panel inline callbacks
# -----------------------------------------------------------------------
async def on_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not db_is_admin(query.from_user.id):
        await query.answer("🚫 Admins only.", show_alert=True); return
    await query.answer()
    data = query.data
    is_owner = query.from_user.id == OWNER_ID

    if data == "adm_home":
        await query.edit_message_text("🛠️ *Admin Panel*\n\nChoose a category below 👇", parse_mode=ParseMode.MARKDOWN, reply_markup=admin_panel_keyboard(is_owner))

    elif data == "adm_credits":
        await query.edit_message_text(
            "💰 *Credit Commands*\n\n"
            "`/addcredit <uid> <amount>`\n`/removecredit <uid> <amount>`\n`/setcredit <uid> <amount>`",
            parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back_keyboard())

    elif data == "adm_premium":
        await query.edit_message_text(
            "💎 *Premium Commands*\n\n`/addpremium <uid>`\n`/removepremium <uid>`",
            parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back_keyboard())

    elif data == "adm_channels":
        chs = db_list_channels()
        ch_text = "\n".join([f"📢 {c['name']} — `{c['chat_id']}`" for c in chs]) if chs else "_(none yet)_"
        await query.edit_message_text(
            f"📢 *Channel Commands*\n\n"
            f"`/addchannel @username` — public\n`/addchannel` — private (forward post)\n`/removechannel <chat_id>`\n\n"
            f"*Current:*\n{ch_text}",
            parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back_keyboard())

    elif data == "adm_admins":
        ids = db_list_admins()
        lines = [f"👑 `{OWNER_ID}` (owner)"] + [f"🛠️ `{i}`" for i in ids]
        text = "👑 *Admins*\n\n" + "\n".join(lines)
        if is_owner:
            text += "\n\n`/addadmin <uid>` · `/removeadmin <uid>`"
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back_keyboard())

    elif data == "adm_userinfo":
        await query.edit_message_text("ℹ️ *User Info*\n\n`/userinfo <user_id>`", parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back_keyboard())

    elif data in ("adm_status","adm_toggle"):
        if data == "adm_toggle":
            db_set_setting("bot_enabled","0" if is_bot_enabled() else "1")
        enabled = is_bot_enabled()
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔴 Turn OFF" if enabled else "🟢 Turn ON", callback_data="adm_toggle")],
            [InlineKeyboardButton("🔙 Back", callback_data="adm_home")],
        ])
        await query.edit_message_text(f"⚙️ *Bot Status:* {'🟢 ON' if enabled else '🔴 OFF'}", parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

    elif data == "adm_video":
        video = get_profile_video()
        vid_status = f"`{video[:40]}...`" if video and video != "NONE" else "_(not set)_"
        await query.edit_message_text(
            f"🎬 *Dashboard Video*\n\nCurrent: {vid_status}\n\n"
            "`/setvideo` — send a new video (or reply to video)\n"
            "`/clearvideo` — remove current video",
            parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back_keyboard())


# -----------------------------------------------------------------------
# Maintenance gate
# -----------------------------------------------------------------------
async def maintenance_gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # Handle awaiting_video here (admin video upload flow)
    if (user and db_is_admin(user.id)
            and context.user_data.get("awaiting_video")
            and update.message and update.message.video):
        context.user_data["awaiting_video"] = False
        file_id = update.message.video.file_id
        db_set_setting("profile_video", file_id)
        await update.message.reply_text("✅ *Dashboard video updated!*", parse_mode=ParseMode.MARKDOWN)
        raise ApplicationHandlerStop

    if user and db_is_admin(user.id):
        return  # admins bypass maintenance

    if not is_bot_enabled():
        if update.callback_query:
            await update.callback_query.answer("🔴 Bot is currently OFF. Check back later!", show_alert=True)
        elif update.message:
            await update.message.reply_text(
                "🔴 *Bot is currently OFF*\n\nMaintenance in progress — check back later! 🙏",
                parse_mode=ParseMode.MARKDOWN,
            )
        raise ApplicationHandlerStop


# -----------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Maintenance gate runs before everything
    app.add_handler(TypeHandler(Update, maintenance_gate), group=-1)

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("addcredit", admin_addcredit))
    app.add_handler(CommandHandler("removecredit", admin_removecredit))
    app.add_handler(CommandHandler("setcredit", admin_setcredit))
    app.add_handler(CommandHandler("addpremium", admin_addpremium))
    app.add_handler(CommandHandler("removepremium", admin_removepremium))
    app.add_handler(CommandHandler("userinfo", admin_userinfo))
    app.add_handler(CommandHandler("offbot", admin_offbot))
    app.add_handler(CommandHandler("onbot", admin_onbot))
    app.add_handler(CommandHandler("listadmins", admin_listadmins))
    app.add_handler(CommandHandler("addadmin", admin_addadmin))
    app.add_handler(CommandHandler("removeadmin", admin_removeadmin))
    app.add_handler(CommandHandler("addchannel", admin_addchannel))
    app.add_handler(CommandHandler("removechannel", admin_removechannel))
    app.add_handler(CommandHandler("listchannels", admin_listchannels))
    app.add_handler(CommandHandler("setvideo", admin_setvideo))
    app.add_handler(CommandHandler("clearvideo", admin_clearvideo))

    # Inline callbacks
    app.add_handler(CallbackQueryHandler(on_verify,       pattern="^verify$"))
    app.add_handler(CallbackQueryHandler(on_stop,         pattern="^stop$"))
    app.add_handler(CallbackQueryHandler(on_admin_callback, pattern="^adm_"))

    # All text/video messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    log.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
