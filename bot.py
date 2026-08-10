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
BOT_USERNAME = "your_bot_username"          # without @, used to build referral links
OWNER_ID = 8790645158                       # your numeric Telegram user ID (gets new-user notifications + admin access)

# Channels users must join before using the bot.
# chat_id: the channel's numeric ID (e.g. -1001234567890) or "@channelusername".
# For a PUBLIC channel, "@username" works directly.
# For a PRIVATE channel (invite-link only, like channel 2 below), get_chat_member
# needs the numeric chat_id, not the invite link. To find it:
#   1. Add the bot as an ADMIN in that private channel
#   2. Forward any message from that channel to @userinfobot (or @JsonDumpBot)
#      — it'll show you the channel's numeric ID (starts with -100)
#   3. Paste that number below in place of "REPLACE_WITH_NUMERIC_CHAT_ID"
FORCE_CHANNELS = [
    {"name": "CHANNEL 1", "url": "https://t.me/liesworlds2", "chat_id": "@liesworlds2"},
    {"name": "CHANNEL 2", "url": "https://t.me/+r2ruzqH4m441YjE1", "chat_id": "REPLACE_WITH_NUMERIC_CHAT_ID"},
]

SUBSCRIPTION_CONTACT = "@liesworlds"
DEVELOPER_CONTACT = "@liesworlds"

CREDITS_PER_REFERRAL = 2
CREDITS_PER_USE = 1
CREDITS_ON_SIGNUP = 2   # free credits given the first time a user verifies

# 4 APIs — replace URL with your own. Remove the Authorization header
# entirely (see the # EDIT HERE comments below) if your API needs no key.
API_1_URL = "https://api-one.example.com/generate"
API_2_URL = "https://api-two.example.com/generate"
API_3_URL = "https://api-three.example.com/generate"
API_4_URL = "https://api-four.example.com/generate"

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
    # Lightweight migration in case an older bot.db already exists without `premium`
    cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "premium" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN premium INTEGER DEFAULT 0")
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
# API calls — each returns the raw JSON response as a dict, e.g.
# {"status": "ok", "data": "..."}. Adjust the payload below to match
# what your real APIs expect.
# ---------------------------------------------------------------------
async def call_api_1(code: str) -> dict:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            API_1_URL,
            # headers={"Authorization": f"Bearer {API_1_KEY}"},  # EDIT HERE: remove if no key needed
            json={"code": code},
        )
        resp.raise_for_status()
        return resp.json()


async def call_api_2(code: str) -> dict:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(API_2_URL, json={"code": code})
        resp.raise_for_status()
        return resp.json()


async def call_api_3(code: str) -> dict:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(API_3_URL, json={"code": code})
        resp.raise_for_status()
        return resp.json()


async def call_api_4(code: str) -> dict:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(API_4_URL, json={"code": code})
        resp.raise_for_status()
        return resp.json()


API_FUNCS = [call_api_1, call_api_2, call_api_3, call_api_4]


# ---------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------
def join_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(f"📢 {c['name']}", url=c["url"])] for c in FORCE_CHANNELS]
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

MODULES = [
    {"emoji": "🪄", "name": "Casting Magic"},
    {"emoji": "🍳", "name": "Cooking Pixels"},
    {"emoji": "🛸", "name": "Beaming from Space"},
    {"emoji": "🎉", "name": "Adding Sparkle"},
]

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
    for i, mod in enumerate(MODULES):
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
    for ch in FORCE_CHANNELS:
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
    """Turn one API's raw JSON response into a clean, readable block.
    Handles the {"status": ..., "data": ...} shape, but falls back
    gracefully if the real API sends different or extra fields."""
    if isinstance(response, Exception):
        return f"❌ <b>{mod['emoji']} {html.escape(mod['name'])}</b>\nFailed to fetch a result."

    if not isinstance(response, dict):
        # Not a dict (plain string/number/list) — just show it as-is
        return f"{mod['emoji']} <b>{html.escape(mod['name'])}</b>\n{html.escape(str(response))}"

    status = response.get("status")
    data = response.get("data")

    lines = [f"{mod['emoji']} <b>{html.escape(mod['name'])}</b>"]

    if status is not None:
        status_icon = "✅" if str(status).lower() in ("ok", "success", "true", "1") else "⚠️"
        lines.append(f"{status_icon} Status: {html.escape(str(status))}")

    if data is not None:
        lines.append(f"📄 Result: {html.escape(str(data))}")

    # Any other fields the API sent — show them too, generically
    for key, value in response.items():
        if key in ("status", "data"):
            continue
        lines.append(f"• {html.escape(str(key).capitalize())}: {html.escape(str(value))}")

    return "\n".join(lines)


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

        blocks = [format_api_result(MODULES[i], results[i]) for i in range(len(results))]
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
        if update.effective_user.id != OWNER_ID:
            await update.message.reply_text("🚫 This command is for the bot owner only.")
            return
        return await func(update, context)
    return wrapper


@admin_only
async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🛠️ *Admin Panel*\n\n"
        "`/addcredit <user_id> <amount>` — add credits to a user\n"
        "`/removecredit <user_id> <amount>` — remove credits from a user\n"
        "`/setcredit <user_id> <amount>` — set a user's credits to an exact value\n"
        "`/addpremium <user_id>` — grant premium (unlimited USE, no credit cost)\n"
        "`/removepremium <user_id>` — revoke premium\n"
        "`/userinfo <user_id>` — view a user's full profile\n"
        "`/offbot` — turn the bot OFF for all users (except you)\n"
        "`/onbot` — turn the bot back ON\n",
        parse_mode=ParseMode.MARKDOWN,
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


# ---------------------------------------------------------------------
# Maintenance-mode gate — runs before every other handler
# ---------------------------------------------------------------------
async def maintenance_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user and user.id == OWNER_ID:
        return  # owner always has access, even while bot is "off"

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
