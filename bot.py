"""
Telegram Bot — Force-Join + Referral Credits + Credit-Gated Generator
=======================================================================

Flow:
  /start -> if user hasn't joined required channels, show channel buttons
            + a "Verify" button.
  Verify -> checks membership in each channel. If all joined, unlocks the
            main menu (Refer & Earn / My Profile / Subscription /
            Developer / USE) and, if this user was referred by someone,
            credits the referrer +2 and notifies the owner.
  USE    -> only works if user has credit. Asks for a 10-digit code,
            validates it, runs 4 APIs in parallel with a live progress
            bar + STOP button, then sends the combined result. Each
            successful USE costs 1 credit.

Requirements:
    pip install python-telegram-bot httpx --break-system-packages

Deploy on Railway:
    1. Push these 3 files to a GitHub repo: telegram_bot.py, requirements.txt, Procfile
    2. On Railway: New Project -> Deploy from GitHub repo
    3. Add environment variable BOT_TOKEN (or just hardcode it below)
    4. Railway auto-detects Procfile and runs the worker
    5. SQLite file (bot.db) persists as long as you don't wipe the volume —
       for production-grade persistence, attach a Railway volume, or swap
       to Postgres later.
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

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# ======================================================================
# CONFIG — edit these
# ======================================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8495656887:AAHaeHqi77k3ASgGJ4G2_-GfuxJbzO7Cejw")
BOT_USERNAME = "liesworlds2bot"          # without @, used to build referral links
OWNER_ID = 8790645158                       # your numeric Telegram user ID (gets new-user notifications + admin access)

# Force-join channels are now managed entirely through the admin panel —
# use /addchannel (or the 📢 Channels button in /admin) instead of editing
# code here. This list is only used to seed the database the very first
# time the bot runs; leave it empty and add channels via the bot itself.
FORCE_CHANNELS = []

SUBSCRIPTION_CONTACT = "@liesworlds"
DEVELOPER_CONTACT = "@liesworlds"

CREDITS_PER_REFERRAL = 2
CREDITS_PER_USE = 1
CREDITS_ON_SIGNUP = 2   # free credits given the first time a user verifies

# 4 (or however many you want) APIs — each can have a totally different
# shape. For each one, set:
#   method       "GET" or "POST"
#   param_style  "query"  -> code sent as ?code=VALUE
#                "json"   -> code sent as JSON body {"code": "VALUE"}
#                "path"   -> code appended to the URL, e.g. .../generate/VALUE
#                "none"   -> no code sent at all, just calls the URL as-is
#   param_name   the field/param name your API expects (default "code")
#   headers      optional dict, e.g. {"Authorization": "Bearer XXX"} if your
#                API needs a key
#
# Add, remove, or edit entries freely — the bot adapts automatically,
# and the progress screen + results message scale to however many you list.
API_CONFIGS = [
    {
        "emoji": "🪄", "name": "Casting Magic",
        "url": "https://eren-h785.onrender.com/bomb",
        "method": "GET", "param_style": "path",
    },
    {
        "emoji": "🍳", "name": "Cooking Pixels",
        "url": "https://immortalbomberpart-2.onrender.com/bomb",
        "method": "GET", "param_style": "query", "param_name": "app",
    },
    {
        "emoji": "🛸", "name": "Beaming from Space",
        "url": "https://bomber-production-d127.up.railway.app//bomber",
        "method": "GET", "param_style": "query", "param_name": "app",
    },
    {
        "emoji": "🎉", "name": "Adding Sparkle",
        "url": "https://newbomb-production.up.railway.app//bomb",
        "method": "GET", "param_style": "query", "param_name": "app",
    },
]

# Video shown at the top of the "My Profile" dashboard (premium feel).
# Easiest: send your video to your bot once in a private chat, check the
# console log for its file_id, then paste it here. A direct .mp4 URL also
# works, but a file_id loads faster since Telegram doesn't re-download it.
PROFILE_VIDEO = "BAACAgUAAxkBAAFRdYpqeX8TQHmU4taNopyqEvCFP1S-lQACxR4AAreX0FehTWtCPlqNbT0E"
# ======================================================================


# ---------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------
DB_PATH = "bot.db"


def db_init() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            credits INTEGER DEFAULT 0,
            verified INTEGER DEFAULT 0,
            referred_by INTEGER,
            referral_credited INTEGER DEFAULT 0,
            premium INTEGER DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY,
            added_by INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS force_channels (
            chat_id TEXT PRIMARY KEY,
            name TEXT,
            url TEXT
        )
        """
    )
    # Lightweight migration in case an older bot.db already exists without `premium`
    cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "premium" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN premium INTEGER DEFAULT 0")

    # Seed force_channels from the hardcoded FORCE_CHANNELS list once, so
    # channels already configured in code aren't lost when this table is new.
    existing_count = conn.execute("SELECT COUNT(*) FROM force_channels").fetchone()[0]
    if existing_count == 0:
        for ch in FORCE_CHANNELS:
            conn.execute(
                "INSERT OR IGNORE INTO force_channels (chat_id, name, url) VALUES (?, ?, ?)",
                (str(ch["chat_id"]), ch["name"], ch["url"]),
            )

    conn.commit()
    conn.close()


def db_get_setting(key: str, default: str = None) -> str:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row[0] if row else default


def db_set_setting(key: str, value: str) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def is_bot_enabled() -> bool:
    return db_get_setting("bot_enabled", "1") == "1"


def db_is_admin(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row is not None


def db_add_admin(user_id: int, added_by: int) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?, ?)",
        (user_id, added_by),
    )
    conn.commit()
    conn.close()


def db_remove_admin(user_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
    conn.commit()
    removed = cur.rowcount > 0
    conn.close()
    return removed


def db_list_admins() -> list:
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT user_id FROM admins").fetchall()
    conn.close()
    return [r[0] for r in rows]


def db_add_force_channel(chat_id, name: str, url: str) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO force_channels (chat_id, name, url) VALUES (?, ?, ?) "
        "ON CONFLICT(chat_id) DO UPDATE SET name = excluded.name, url = excluded.url",
        (str(chat_id), name, url),
    )
    conn.commit()
    conn.close()


def db_remove_force_channel(chat_id) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("DELETE FROM force_channels WHERE chat_id = ?", (str(chat_id),))
    conn.commit()
    removed = cur.rowcount > 0
    conn.close()
    return removed


def db_list_force_channels() -> list:
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT chat_id, name, url FROM force_channels").fetchall()
    conn.close()
    return [{"chat_id": r[0], "name": r[1], "url": r[2]} for r in rows]


def db_get_user(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT user_id, username, first_name, credits, verified, referred_by, referral_credited, premium "
        "FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    return row


def db_create_user(user_id: int, username: str, first_name: str, referred_by=None) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR IGNORE INTO users (user_id, username, first_name, credits, verified, referred_by) "
        "VALUES (?, ?, ?, 0, 0, ?)",
        (user_id, username, first_name, referred_by),
    )
    conn.commit()
    conn.close()


def db_set_verified(user_id: int) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE users SET verified = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def db_add_credits(user_id: int, amount: int) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE users SET credits = credits + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()


def db_mark_referral_credited(user_id: int) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE users SET referral_credited = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def db_count_referrals(user_id: int) -> int:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT COUNT(*) FROM users WHERE referred_by = ? AND referral_credited = 1",
        (user_id,),
    ).fetchone()
    conn.close()
    return row[0] if row else 0


def db_set_premium(user_id: int, value: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("UPDATE users SET premium = ? WHERE user_id = ?", (value, user_id))
    conn.commit()
    updated = cur.rowcount > 0
    conn.close()
    return updated


def db_set_credits(user_id: int, amount: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("UPDATE users SET credits = ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    updated = cur.rowcount > 0
    conn.close()
    return updated


db_init()


# ---------------------------------------------------------------------
# One generic caller that adapts to whatever each API_CONFIGS entry says
# (GET/POST, query/json/path param, custom headers). Returns the parsed
# JSON, or {"data": <raw text>} if the response isn't valid JSON.
# ---------------------------------------------------------------------
async def call_generic_api(cfg: dict, code: str) -> dict:
    method = cfg.get("method", "GET").upper()
    style = cfg.get("param_style", "query")
    param_name = cfg.get("param_name", "code")
    headers = cfg.get("headers") or {}
    url = cfg["url"]

    async with httpx.AsyncClient(timeout=60) as client:
        if style == "path":
            url = f"{url.rstrip('/')}/{code}"

        if method == "GET":
            params = {param_name: code} if style == "query" else None
            resp = await client.get(url, params=params, headers=headers)
        else:  # POST
            if style == "json":
                resp = await client.post(url, json={param_name: code}, headers=headers)
            elif style == "query":
                resp = await client.post(url, params={param_name: code}, headers=headers)
            else:
                resp = await client.post(url, headers=headers)

        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError:
            return {"data": resp.text}


API_FUNCS = [lambda code, cfg=cfg: call_generic_api(cfg, code) for cfg in API_CONFIGS]


# ---------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------
def join_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(f"📢 {c['name']}", url=c["url"])] for c in db_list_force_channels()]
    rows.append([InlineKeyboardButton("✅ Verify", callback_data="verify")])
    return InlineKeyboardMarkup(rows)


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🎁 Refer & Earn", callback_data="refer")],
            [InlineKeyboardButton("👤 My Profile", callback_data="profile")],
            [InlineKeyboardButton("💎 Subscription", callback_data="subscription")],
            [InlineKeyboardButton("👨‍💻 Developer", callback_data="developer")],
            [InlineKeyboardButton("🚀 USE", callback_data="use")],
            [InlineKeyboardButton("✨ PREMIUM USE", callback_data="premium_use")],
        ]
    )


def stop_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🛑 STOP", callback_data="stop")]])


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Menu", callback_data="menu")]])


def progress_bar(done: int, total: int) -> str:
    filled = "🟩" * done
    empty = "⬜️" * (total - done)
    pct = int((done / total) * 100)
    return f"{filled}{empty}  {pct}%"


SPINNER_FRAMES = ["◐", "◓", "◑", "◒"]

FUN_TIPS = [
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


def build_progress_text(done_flags: list, spinner: str, tip: str, elapsed: int) -> str:
    done_count = sum(done_flags)
    total = len(done_flags)
    checklist_lines = []
    for i, mod in enumerate(API_CONFIGS):
        if done_flags[i]:
            checklist_lines.append(f"✅ {mod['emoji']} {mod['name']}")
        else:
            checklist_lines.append(f"◽️ {mod['emoji']} {mod['name']}")
    checklist = "\n".join(checklist_lines)

    return (
        f"{spinner} *Generating your content...*\n\n"
        f"{progress_bar(done_count, total)}\n\n"
        f"{checklist}\n\n"
        f"_{tip}_\n"
        f"⏱️ {elapsed}s"
    )


# ---------------------------------------------------------------------
# Membership check
# ---------------------------------------------------------------------
async def is_member_of_all(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    for ch in db_list_force_channels():
        try:
            member = await context.bot.get_chat_member(ch["chat_id"], user_id)
            if member.status in ("left", "kicked"):
                return False
        except Exception as e:
            log.warning("Membership check failed for %s: %s", ch["chat_id"], e)
            return False
    return True


# ---------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    args = context.args

    referred_by = None
    if args and args[0].startswith("ref_"):
        try:
            candidate = int(args[0].replace("ref_", ""))
            if candidate != user.id:
                referred_by = candidate
        except ValueError:
            pass

    existing = db_get_user(user.id)
    if not existing:
        db_create_user(user.id, user.username or "", user.first_name or "", referred_by)

    row = db_get_user(user.id)
    verified = row[4] if row else 0

    if verified:
        await update.message.reply_text(
            f"👋 *Welcome back, {user.first_name}!*\n\nChoose an option below 👇",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu_keyboard(),
        )
        return

    await update.message.reply_text(
        "🔐 *Access Restricted*\n\n"
        "Please join the channel(s) below to unlock the bot, then tap *Verify* ✅",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=join_keyboard(),
    )


async def on_verify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

        # First-time signup bonus
        db_add_credits(user.id, CREDITS_ON_SIGNUP)

        # Handle referral credit + owner notification, only once per user
        referred_by = row[5] if row else None
        already_credited = row[6] if row else 0
        if referred_by and not already_credited:
            db_add_credits(referred_by, CREDITS_PER_REFERRAL)
            db_mark_referral_credited(user.id)

        try:
            await context.bot.send_message(
                OWNER_ID,
                f"🆕 *New user started the bot!*\n\n"
                f"👤 Name: {user.first_name}\n"
                f"🔗 Username: @{user.username if user.username else 'N/A'}\n"
                f"🆔 ID: `{user.id}`",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            log.warning("Could not notify owner: %s", e)

    bonus_line = (
        f"🎁 You've received *{CREDITS_ON_SIGNUP} free credits* to get started!\n\n"
        if not was_verified
        else ""
    )
    await query.edit_message_text(
        f"✅ *Verified successfully!*\n\n"
        f"Welcome, {user.first_name} 🎉\n"
        f"{bonus_line}"
        "Choose an option below 👇",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_keyboard(),
    )


async def on_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    text = f"👋 *Welcome back, {user.first_name}!*\n\nChoose an option below 👇"

    # If we're coming back from a video message (e.g. Profile), edit_message_text
    # will fail since it's not a text message — delete and send fresh instead.
    try:
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_keyboard())
    except Exception:
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu_keyboard(),
        )


async def on_refer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{user.id}"
    referrals = db_count_referrals(user.id)

    await query.edit_message_text(
        "🎁 *Refer & Earn*\n\n"
        f"Invite your friends and earn *{CREDITS_PER_REFERRAL} credits* for every "
        "friend who joins and verifies! 🚀\n\n"
        f"👥 Total successful referrals: *{referrals}*\n\n"
        "🔗 *Your referral link:*\n"
        f"`{link}`\n\n"
        "_Tap the link above to copy it, then share it with your friends!_",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=back_keyboard(),
    )


async def on_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    row = db_get_user(user.id)
    credits = row[3] if row else 0
    premium = row[7] if row else 0
    status_line = "💎 *PREMIUM* ✨" if premium else "🆓 Free User"

    caption = (
        "👤 *Your Profile*\n\n"
        f"📛 Name: {user.first_name}\n"
        f"🔗 Username: @{user.username if user.username else 'N/A'}\n"
        f"🆔 User ID: `{user.id}`\n"
        f"💰 Credits: *{credits}*\n"
        f"⭐ Status: {status_line}\n"
    )

    # A text message can't be turned into a video message via edit, so we
    # delete the old menu message and send a fresh video+caption instead.
    try:
        await query.message.delete()
    except Exception:
        pass

    await context.bot.send_video(
        chat_id=update.effective_chat.id,
        video=PROFILE_VIDEO,
        caption=caption,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=back_keyboard(),
    )


async def on_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "💎 *Subscription*\n\n"
        "Want unlimited access and premium perks?\n\n"
        f"📩 Contact {SUBSCRIPTION_CONTACT} to buy a subscription!",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=back_keyboard(),
    )


async def on_developer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "👨‍💻 *Developer*\n\n"
        f"For support, bugs, or business inquiries, reach out to {DEVELOPER_CONTACT} 🚀",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=back_keyboard(),
    )


async def on_use(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    row = db_get_user(user.id)
    credits = row[3] if row else 0
    premium = row[7] if row else 0

    if not premium and credits < CREDITS_PER_USE:
        await query.edit_message_text(
            "🚫 *Insufficient Credits*\n\n"
            "You don't have enough credits to use this feature.\n\n"
            "🎁 Earn free credits via *Refer & Earn*, or 💎 grab a *Subscription*!",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_keyboard(),
        )
        return

    context.user_data["awaiting_code"] = True
    await query.edit_message_text(
        "🔑 *Send Me the Code* 🔑\n\nPlease send your *10-digit code* to continue 👇",
        parse_mode=ParseMode.MARKDOWN,
    )


async def on_premium_use(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer(
        "✨ Premium Use is coming soon — stay tuned! 🚀",
        show_alert=True,
    )


async def on_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("Stopping...")

    task = context.user_data.get("running_task")
    if task and not task.done():
        task.cancel()
        await query.edit_message_text(
            "🛑 *Stopped.*\n\nProcess cancelled successfully.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_keyboard(),
        )
    else:
        await query.edit_message_text("✅ Nothing running right now.", reply_markup=back_keyboard())


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Handle admin forwarding a channel post to register it as a force-join channel
    if context.user_data.get("awaiting_channel_forward"):
        fwd_chat = getattr(update.message, "forward_from_chat", None)
        if fwd_chat and fwd_chat.type == "channel":
            context.user_data["awaiting_channel_forward"] = False
            await _try_add_channel(update, context, chat_ref=fwd_chat.id)
            return
        else:
            await update.message.reply_text(
                "⚠️ That doesn't look like a forwarded channel post. Please forward a message "
                "directly from the channel, or run /addchannel again to cancel."
            )
            return

    if not context.user_data.get("awaiting_code"):
        return

    text = (update.message.text or "").strip()

    if not text.isdigit() or len(text) != 10:
        await update.message.reply_text(
            "❌ *Invalid Code*\n\nThe code must be exactly *10 digits*. Please try again 🔁",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    context.user_data["awaiting_code"] = False
    user = update.effective_user
    row = db_get_user(user.id)
    premium = row[7] if row else 0
    code = text

    status_msg = await update.message.reply_text(
        build_progress_text([False, False, False, False], SPINNER_FRAMES[0], FUN_TIPS[0], 0),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=stop_keyboard(),
    )

    task = asyncio.create_task(run_all_apis(code, update, context, status_msg, user.id, premium))
    context.user_data["running_task"] = task


def format_api_result(mod: dict, response) -> str:
    """Show one API's raw JSON response, pretty-printed inside a code
    block so it's still exact JSON but readable instead of a messy
    one-liner."""
    if isinstance(response, Exception):
        return f"❌ <b>{mod['emoji']} {html.escape(mod['name'])}</b>\nFailed to fetch a result."

    if isinstance(response, (dict, list)):
        pretty = json.dumps(response, indent=2, ensure_ascii=False)
    else:
        pretty = str(response)

    return (
        f"{mod['emoji']} <b>{html.escape(mod['name'])}</b>\n"
        f"<pre>{html.escape(pretty)}</pre>"
    )


async def run_all_apis(code, update, context, status_msg, user_id, premium=0) -> None:
    total = len(API_FUNCS)
    tasks = [asyncio.ensure_future(fn(code)) for fn in API_FUNCS]
    tick = 0
    TICK_SECONDS = 0.6

    try:
        while True:
            done_flags = [t.done() for t in tasks]

            if all(done_flags):
                break

            spinner = SPINNER_FRAMES[tick % len(SPINNER_FRAMES)]
            tip = FUN_TIPS[(tick // 5) % len(FUN_TIPS)]  # rotate tip every ~3s
            elapsed = int(tick * TICK_SECONDS)
            try:
                await status_msg.edit_text(
                    build_progress_text(done_flags, spinner, tip, elapsed),
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=stop_keyboard(),
                )
            except Exception:
                pass  # ignore "message not modified" and similar transient errors

            await asyncio.sleep(TICK_SECONDS)
            tick += 1

        # Final tick to show 100% + all checkmarks before sending results
        elapsed = int(tick * TICK_SECONDS)
        try:
            await status_msg.edit_text(
                build_progress_text([True, True, True, True], "✅", "🎉 All done!", elapsed),
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass

        # Collect each API's result, preserving order and catching failures
        # individually so one bad API doesn't wipe out the other 3.
        results = []
        for t in tasks:
            try:
                results.append(t.result())
            except Exception as e:
                log.error("API call failed: %s", e)
                results.append(e)

        if all(isinstance(r, Exception) for r in results):
            await status_msg.edit_text(
                "⚠️ *Something went wrong.* No results were returned.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_keyboard(),
            )
            return

        blocks = [format_api_result(API_CONFIGS[i], results[i]) for i in range(len(results))]
        final_text = "🎉 <b>Here are your results!</b>\n\n" + "\n\n".join(blocks)

        db_add_credits(user_id, -CREDITS_PER_USE if not premium else 0)

        await update.effective_chat.send_message(final_text, parse_mode=ParseMode.HTML)
        await update.effective_chat.send_message(
            "🎉 *All done!* Tap below to go back to the menu.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_keyboard(),
        )

    except asyncio.CancelledError:
        for t in tasks:
            if not t.done():
                t.cancel()
        log.info("Task cancelled by user")
        raise


# ---------------------------------------------------------------------
# Admin panel (owner-only)
# ---------------------------------------------------------------------
def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not db_is_admin(update.effective_user.id):
            await update.message.reply_text("🚫 This command is for admins only.")
            return
        return await func(update, context)
    return wrapper


def owner_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != OWNER_ID:
            await update.message.reply_text("🚫 This command is for the bot owner only.")
            return
        return await func(update, context)
    return wrapper


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💰 Credits", callback_data="adm_credits"),
             InlineKeyboardButton("💎 Premium", callback_data="adm_premium")],
            [InlineKeyboardButton("📢 Channels", callback_data="adm_channels"),
             InlineKeyboardButton("👑 Admins", callback_data="adm_admins")],
            [InlineKeyboardButton("ℹ️ User Info", callback_data="adm_userinfo"),
             InlineKeyboardButton("⚙️ Bot Status", callback_data="adm_status")],
        ]
    )


def admin_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="adm_home")]])


@admin_only
async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🛠️ *Admin Panel*\n\nChoose a category below 👇",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=admin_panel_keyboard(),
    )


async def on_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = query.from_user.id

    if not db_is_admin(user_id):
        await query.answer("🚫 Admins only.", show_alert=True)
        return

    await query.answer()
    data = query.data
    is_owner = user_id == OWNER_ID

    if data == "adm_home":
        await query.edit_message_text(
            "🛠️ *Admin Panel*\n\nChoose a category below 👇",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_panel_keyboard(),
        )

    elif data == "adm_credits":
        await query.edit_message_text(
            "💰 *Credit Commands*\n\n"
            "`/addcredit <user_id> <amount>` — add credits\n"
            "`/removecredit <user_id> <amount>` — remove credits\n"
            "`/setcredit <user_id> <amount>` — set exact value",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_back_keyboard(),
        )

    elif data == "adm_premium":
        await query.edit_message_text(
            "💎 *Premium Commands*\n\n"
            "`/addpremium <user_id>` — grant unlimited USE\n"
            "`/removepremium <user_id>` — revoke premium",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_back_keyboard(),
        )

    elif data == "adm_channels":
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("📋 List Channels", callback_data="adm_ch_list")],
                [InlineKeyboardButton("🔙 Back", callback_data="adm_home")],
            ]
        )
        await query.edit_message_text(
            "📢 *Channel Commands*\n\n"
            "`/addchannel @username` — add a public channel\n"
            "`/addchannel` (no args) — then forward a post to add a private channel\n"
            "`/removechannel <chat_id>` — remove a channel\n\n"
            "⚠️ Bot must already be an admin in that channel first.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb,
        )

    elif data == "adm_ch_list":
        channels = db_list_force_channels()
        if not channels:
            text = "📭 No force-join channels configured yet."
        else:
            lines = [f"📢 *{c['name']}*\nID: `{c['chat_id']}`" for c in channels]
            text = "📋 *Required Channels*\n\n" + "\n\n".join(lines)
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back_keyboard())

    elif data == "adm_admins":
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("📋 List Admins", callback_data="adm_ad_list")],
                [InlineKeyboardButton("🔙 Back", callback_data="adm_home")],
            ]
        )
        text = "👑 *Admin Commands*"
        if is_owner:
            text += "\n\n`/addadmin <user_id>` — grant admin access\n`/removeadmin <user_id>` — revoke access"
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

    elif data == "adm_ad_list":
        admin_ids = db_list_admins()
        lines = [f"👑 `{OWNER_ID}` (owner)"] + [f"🛠️ `{aid}`" for aid in admin_ids]
        await query.edit_message_text(
            "📋 *Current Admins*\n\n" + "\n".join(lines),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_back_keyboard(),
        )

    elif data == "adm_userinfo":
        await query.edit_message_text(
            "ℹ️ *User Info*\n\n`/userinfo <user_id>` — view a user's full profile",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_back_keyboard(),
        )

    elif data in ("adm_status", "adm_toggle"):
        if data == "adm_toggle":
            db_set_setting("bot_enabled", "0" if is_bot_enabled() else "1")
        enabled = is_bot_enabled()
        status_text = "🟢 ON" if enabled else "🔴 OFF"
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🔴 Turn OFF" if enabled else "🟢 Turn ON", callback_data="adm_toggle")],
                [InlineKeyboardButton("🔙 Back", callback_data="adm_home")],
            ]
        )
        await query.edit_message_text(
            f"⚙️ *Bot Status:* {status_text}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb,
        )


@admin_only
async def admin_addcredit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        target_id = int(context.args[0])
        amount = int(context.args[1])
    except (IndexError, ValueError):
        await update.message.reply_text("⚠️ Usage: `/addcredit <user_id> <amount>`", parse_mode=ParseMode.MARKDOWN)
        return

    if not db_get_user(target_id):
        await update.message.reply_text("❌ No user found with that ID.")
        return

    db_add_credits(target_id, amount)
    await update.message.reply_text(f"✅ Added *{amount}* credits to `{target_id}`.", parse_mode=ParseMode.MARKDOWN)


@admin_only
async def admin_removecredit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        target_id = int(context.args[0])
        amount = int(context.args[1])
    except (IndexError, ValueError):
        await update.message.reply_text("⚠️ Usage: `/removecredit <user_id> <amount>`", parse_mode=ParseMode.MARKDOWN)
        return

    if not db_get_user(target_id):
        await update.message.reply_text("❌ No user found with that ID.")
        return

    db_add_credits(target_id, -amount)
    await update.message.reply_text(f"✅ Removed *{amount}* credits from `{target_id}`.", parse_mode=ParseMode.MARKDOWN)


@admin_only
async def admin_setcredit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        target_id = int(context.args[0])
        amount = int(context.args[1])
    except (IndexError, ValueError):
        await update.message.reply_text("⚠️ Usage: `/setcredit <user_id> <amount>`", parse_mode=ParseMode.MARKDOWN)
        return

    if not db_set_credits(target_id, amount):
        await update.message.reply_text("❌ No user found with that ID.")
        return

    await update.message.reply_text(f"✅ Set `{target_id}`'s credits to *{amount}*.", parse_mode=ParseMode.MARKDOWN)


@admin_only
async def admin_addpremium(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        target_id = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("⚠️ Usage: `/addpremium <user_id>`", parse_mode=ParseMode.MARKDOWN)
        return

    if not db_set_premium(target_id, 1):
        await update.message.reply_text("❌ No user found with that ID.")
        return

    await update.message.reply_text(f"💎 `{target_id}` is now *PREMIUM*.", parse_mode=ParseMode.MARKDOWN)
    try:
        await context.bot.send_message(
            target_id,
            "💎 *Congratulations!*\n\nYou've been upgraded to *PREMIUM* — unlimited USE, no credits needed! 🚀",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        log.warning("Could not notify user of premium upgrade: %s", e)


@admin_only
async def admin_removepremium(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        target_id = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("⚠️ Usage: `/removepremium <user_id>`", parse_mode=ParseMode.MARKDOWN)
        return

    if not db_set_premium(target_id, 0):
        await update.message.reply_text("❌ No user found with that ID.")
        return

    await update.message.reply_text(f"✅ `{target_id}`'s premium has been removed.", parse_mode=ParseMode.MARKDOWN)


@admin_only
async def admin_userinfo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        target_id = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("⚠️ Usage: `/userinfo <user_id>`", parse_mode=ParseMode.MARKDOWN)
        return

    row = db_get_user(target_id)
    if not row:
        await update.message.reply_text("❌ No user found with that ID.")
        return

    _, username, first_name, credits, verified, referred_by, _, premium = row
    await update.message.reply_text(
        "📋 *User Info*\n\n"
        f"📛 Name: {first_name}\n"
        f"🔗 Username: @{username if username else 'N/A'}\n"
        f"🆔 ID: `{target_id}`\n"
        f"💰 Credits: *{credits}*\n"
        f"⭐ Premium: {'Yes 💎' if premium else 'No'}\n"
        f"✅ Verified: {'Yes' if verified else 'No'}\n"
        f"👥 Referred by: `{referred_by}`" if referred_by else "👥 Referred by: N/A",
        parse_mode=ParseMode.MARKDOWN,
    )


@admin_only
async def admin_offbot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db_set_setting("bot_enabled", "0")
    await update.message.reply_text(
        "🔴 *Bot is now OFF.*\n\nAll users will see a maintenance message until you run /onbot.",
        parse_mode=ParseMode.MARKDOWN,
    )


@admin_only
async def admin_onbot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db_set_setting("bot_enabled", "1")
    await update.message.reply_text(
        "🟢 *Bot is now ON.*\n\nUsers can use the bot again.",
        parse_mode=ParseMode.MARKDOWN,
    )


@owner_only
async def admin_addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        target_id = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("⚠️ Usage: `/addadmin <user_id>`", parse_mode=ParseMode.MARKDOWN)
        return

    db_add_admin(target_id, update.effective_user.id)
    await update.message.reply_text(
        f"✅ `{target_id}` is now an *admin* and can use the admin panel.",
        parse_mode=ParseMode.MARKDOWN,
    )
    try:
        await context.bot.send_message(
            target_id,
            "🛠️ *You've been granted admin access!*\n\nSend /admin to see what you can do.",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        log.warning("Could not notify new admin: %s", e)


@owner_only
async def admin_removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        target_id = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("⚠️ Usage: `/removeadmin <user_id>`", parse_mode=ParseMode.MARKDOWN)
        return

    if target_id == OWNER_ID:
        await update.message.reply_text("🚫 The owner can't be removed as admin.")
        return

    if not db_remove_admin(target_id):
        await update.message.reply_text("❌ That user isn't an admin.")
        return

    await update.message.reply_text(f"✅ `{target_id}` is no longer an admin.", parse_mode=ParseMode.MARKDOWN)


@admin_only
async def admin_listadmins(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admin_ids = db_list_admins()
    lines = [f"👑 `{OWNER_ID}` (owner)"] + [f"🛠️ `{aid}`" for aid in admin_ids]
    await update.message.reply_text(
        "📋 *Current Admins*\n\n" + "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
    )


@admin_only
async def admin_addchannel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        context.user_data["awaiting_channel_forward"] = True
        await update.message.reply_text(
            "📢 *Add a Force-Join Channel*\n\n"
            "For a *public* channel, send:\n"
            "`/addchannel @channelusername`\n"
            "or `/addchannel https://t.me/channelusername`\n\n"
            "For a *private* channel (invite-link only), just *forward any post* "
            "from that channel to me right now instead 👇\n\n"
            "⚠️ In both cases, make sure the bot is already an *admin* in that channel first!",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    raw = context.args[0]
    username = raw.replace("https://t.me/", "").replace("http://t.me/", "").lstrip("@").strip()

    if username.startswith("+") or "joinchat" in username:
        await update.message.reply_text(
            "⚠️ That's a private invite link — I can't resolve it directly.\n\n"
            "Please run `/addchannel` with no arguments and *forward a post* from that channel instead.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await _try_add_channel(update, context, chat_ref=f"@{username}")


async def _try_add_channel(update, context, chat_ref) -> None:
    try:
        chat = await context.bot.get_chat(chat_ref)
    except Exception as e:
        await update.message.reply_text(f"❌ Couldn't find that channel. Make sure the link is correct.\n\n`{e}`", parse_mode=ParseMode.MARKDOWN)
        return

    try:
        bot_member = await context.bot.get_chat_member(chat.id, context.bot.id)
        if bot_member.status not in ("administrator", "creator"):
            await update.message.reply_text(
                f"⚠️ *{chat.title}* found, but I'm not an admin there yet.\n\n"
                "Please make the bot an admin in that channel, then try again.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
    except Exception as e:
        await update.message.reply_text(
            f"❌ Couldn't verify bot admin status in *{chat.title}*.\n\n`{e}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    url = f"https://t.me/{chat.username}" if chat.username else (chat.invite_link or "")
    if not url:
        try:
            url = await context.bot.export_chat_invite_link(chat.id)
        except Exception:
            url = ""

    db_add_force_channel(chat.id, chat.title, url)
    await update.message.reply_text(
        f"✅ *{chat.title}* added to the required channels list! 🎉",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=back_keyboard(),
    )


@admin_only
async def admin_removechannel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        chat_id = context.args[0]
    except IndexError:
        await update.message.reply_text("⚠️ Usage: `/removechannel <chat_id>` — see /listchannels for IDs", parse_mode=ParseMode.MARKDOWN)
        return

    if not db_remove_force_channel(chat_id):
        await update.message.reply_text("❌ No channel found with that ID.")
        return

    await update.message.reply_text("✅ Channel removed from the required list.")


@admin_only
async def admin_listchannels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    channels = db_list_force_channels()
    if not channels:
        await update.message.reply_text("📭 No force-join channels configured yet. Use /addchannel to add one.")
        return

    lines = [f"📢 *{c['name']}*\n   ID: `{c['chat_id']}`\n   {c['url']}" for c in channels]
    await update.message.reply_text(
        "📋 *Required Channels*\n\n" + "\n\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True,
    )


# ---------------------------------------------------------------------
# Maintenance-mode gate — runs before every other handler
# ---------------------------------------------------------------------
async def maintenance_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user and db_is_admin(user.id):
        return  # admins always have access, even while bot is "off"

    if not is_bot_enabled():
        if update.callback_query:
            await update.callback_query.answer(
                "🔴 Bot is currently OFF. Please check back later!", show_alert=True
            )
        elif update.message:
            await update.message.reply_text(
                "🔴 *Bot is currently OFF*\n\nWe're doing some maintenance — please check back later! 🙏",
                parse_mode=ParseMode.MARKDOWN,
            )
        raise ApplicationHandlerStop


def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()

    # Runs before every other handler; blocks all non-owner interaction while bot is off
    app.add_handler(TypeHandler(Update, maintenance_gate), group=-1)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_help))
    app.add_handler(CommandHandler("addcredit", admin_addcredit))
    app.add_handler(CommandHandler("removecredit", admin_removecredit))
    app.add_handler(CommandHandler("setcredit", admin_setcredit))
    app.add_handler(CommandHandler("addpremium", admin_addpremium))
    app.add_handler(CommandHandler("removepremium", admin_removepremium))
    app.add_handler(CommandHandler("userinfo", admin_userinfo))
    app.add_handler(CommandHandler("offbot", admin_offbot))
    app.add_handler(CommandHandler("onbot", admin_onbot))
    app.add_handler(CommandHandler("addadmin", admin_addadmin))
    app.add_handler(CommandHandler("removeadmin", admin_removeadmin))
    app.add_handler(CommandHandler("listadmins", admin_listadmins))
    app.add_handler(CommandHandler("addchannel", admin_addchannel))
    app.add_handler(CommandHandler("removechannel", admin_removechannel))
    app.add_handler(CommandHandler("listchannels", admin_listchannels))
    app.add_handler(CallbackQueryHandler(on_admin_callback, pattern="^adm_"))
    app.add_handler(CallbackQueryHandler(on_verify, pattern="^verify$"))
    app.add_handler(CallbackQueryHandler(on_menu, pattern="^menu$"))
    app.add_handler(CallbackQueryHandler(on_refer, pattern="^refer$"))
    app.add_handler(CallbackQueryHandler(on_profile, pattern="^profile$"))
    app.add_handler(CallbackQueryHandler(on_subscription, pattern="^subscription$"))
    app.add_handler(CallbackQueryHandler(on_developer, pattern="^developer$"))
    app.add_handler(CallbackQueryHandler(on_use, pattern="^use$"))
    app.add_handler(CallbackQueryHandler(on_premium_use, pattern="^premium_use$"))
    app.add_handler(CallbackQueryHandler(on_stop, pattern="^stop$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    log.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
