"""
Telegram Bot — Full Featured
Requirements: pip install python-telegram-bot httpx
"""

import asyncio
import html
import json
import logging
import os
import sqlite3
from datetime import datetime

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
# CONFIG
# ======================================================================
BOT_TOKEN      = os.environ.get("BOT_TOKEN", "8495656887:AAErNENGMYE-MU4j2jpouTuP32Slxi87ug8")
BOT_USERNAME   = "liesworlds2bot"
OWNER_ID       = 8790645158

SUBSCRIPTION_CONTACT = "@liesworlds"
DEVELOPER_CONTACT    = "@liesworlds"

CREDITS_PER_REFERRAL = 2
CREDITS_PER_USE      = 1
CREDITS_ON_SIGNUP    = 2

# Add your real API URLs below. param_name = exact query param your API expects.
API_CONFIGS = [
    {
        "emoji": "🪄", "name": "Casting Magic",
        "url": "https://wtf-production-8350.up.railway.app/bomb",
        "method": "GET", "param_style": "query", "param_name": "phone",
    },
    {
        "emoji": "🎉", "name": "Adding Sparkle",
        "url": "https://newbomb-production.up.railway.app//bomb",
        "method": "GET", "param_style": "query", "param_name": "phone",
    },
]

# Dashboard/start video file_id — update via /setvideo command
PROFILE_VIDEO  = os.environ.get("PROFILE_VIDEO", "BAACAgUAAxkBAAFRdYpqeX8TQHmU4taNopyqEvCFP1S-lQACxR4AAreX0FehTWtCPlqNbT0E")
FORCE_CHANNELS = []
# ======================================================================


# -----------------------------------------------------------------------
# Database
# -----------------------------------------------------------------------
# DB path — Railway pe Volume mount karo /data pe, tab data redeploy pe safe rahega.
# Railway > Service > Volumes > Add Volume > Mount Path: /data
DB_PATH = os.environ.get("DB_PATH", "/data/bot.db")

# Create /data directory if it doesn't exist (local dev fallback)
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

def db_init():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
        credits INTEGER DEFAULT 0, verified INTEGER DEFAULT 0,
        referred_by INTEGER, referral_credited INTEGER DEFAULT 0,
        premium INTEGER DEFAULT 0)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY, added_by INTEGER)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS force_channels (chat_id TEXT PRIMARY KEY, name TEXT, url TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS api_stats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, code TEXT,
        api_name TEXT, timestamp TEXT,
        success INTEGER DEFAULT 1)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS gift_codes (
        code TEXT PRIMARY KEY,
        credits INTEGER DEFAULT 0,
        max_uses INTEGER DEFAULT 1,
        used_count INTEGER DEFAULT 0,
        created_by INTEGER,
        created_at TEXT,
        active INTEGER DEFAULT 1)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS gift_claims (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT, user_id INTEGER,
        claimed_at TEXT)""")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "premium" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN premium INTEGER DEFAULT 0")
    if FORCE_CHANNELS:
        for ch in FORCE_CHANNELS:
            conn.execute("INSERT OR IGNORE INTO force_channels(chat_id,name,url) VALUES(?,?,?)",
                        (str(ch["chat_id"]), ch["name"], ch["url"]))
    conn.commit()
    conn.close()

def _q(sql, p=()):
    c = sqlite3.connect(DB_PATH); r = c.execute(sql,p).fetchone(); c.close(); return r
def _qa(sql, p=()):
    c = sqlite3.connect(DB_PATH); r = c.execute(sql,p).fetchall(); c.close(); return r
def _ex(sql, p=()):
    c = sqlite3.connect(DB_PATH); cur = c.execute(sql,p); c.commit(); n = cur.rowcount; c.close(); return n

def db_get_user(uid): return _q("SELECT user_id,username,first_name,credits,verified,referred_by,referral_credited,premium FROM users WHERE user_id=?",(uid,))
def db_create_user(uid,uname,fname,ref=None): _ex("INSERT OR IGNORE INTO users(user_id,username,first_name,credits,verified,referred_by) VALUES(?,?,?,0,0,?)",(uid,uname,fname,ref))
def db_set_verified(uid): _ex("UPDATE users SET verified=1 WHERE user_id=?",(uid,))
def db_add_credits(uid,n): _ex("UPDATE users SET credits=credits+? WHERE user_id=?",(n,uid))
def db_set_credits(uid,n): return _ex("UPDATE users SET credits=? WHERE user_id=?",(n,uid))>0
def db_set_premium(uid,v): return _ex("UPDATE users SET premium=? WHERE user_id=?",(v,uid))>0
def db_mark_ref(uid): _ex("UPDATE users SET referral_credited=1 WHERE user_id=?",(uid,))
def db_count_refs(uid): return (_q("SELECT COUNT(*) FROM users WHERE referred_by=? AND referral_credited=1",(uid,)) or (0,))[0]

def db_get_setting(k,d=None):
    r = _q("SELECT value FROM settings WHERE key=?",(k,)); return r[0] if r else d
def db_set_setting(k,v): _ex("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(k,v))
def is_bot_enabled(): return db_get_setting("bot_enabled","1")=="1"
def get_video(): return db_get_setting("profile_video", PROFILE_VIDEO)

def db_is_admin(uid): return uid==OWNER_ID or bool(_q("SELECT 1 FROM admins WHERE user_id=?",(uid,)))
def db_add_admin(uid,by): _ex("INSERT OR IGNORE INTO admins(user_id,added_by) VALUES(?,?)",(uid,by))
def db_remove_admin(uid): return _ex("DELETE FROM admins WHERE user_id=?",(uid,))>0
def db_list_admins(): return [r[0] for r in _qa("SELECT user_id FROM admins")]

def db_add_channel(cid,name,url): _ex("INSERT INTO force_channels(chat_id,name,url) VALUES(?,?,?) ON CONFLICT(chat_id) DO UPDATE SET name=excluded.name,url=excluded.url",(str(cid),name,url))
def db_remove_channel(cid): return _ex("DELETE FROM force_channels WHERE chat_id=?",(str(cid),))>0
def db_list_channels(): return [{"chat_id":r[0],"name":r[1],"url":r[2]} for r in _qa("SELECT chat_id,name,url FROM force_channels")]

def db_log_api(uid, code, api_name, success=1):
    _ex("INSERT INTO api_stats(user_id,code,api_name,timestamp,success) VALUES(?,?,?,?,?)",
        (uid, code, api_name, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), success))

def db_get_stats():
    total_uses   = (_q("SELECT COUNT(*) FROM api_stats") or (0,))[0]
    unique_codes = (_q("SELECT COUNT(DISTINCT code) FROM api_stats") or (0,))[0]
    unique_users = (_q("SELECT COUNT(DISTINCT user_id) FROM api_stats") or (0,))[0]
    first_use    = _q("SELECT timestamp FROM api_stats ORDER BY id ASC LIMIT 1")
    last_use     = _q("SELECT timestamp FROM api_stats ORDER BY id DESC LIMIT 1")
    total_users  = (_q("SELECT COUNT(*) FROM users") or (0,))[0]
    verified     = (_q("SELECT COUNT(*) FROM users WHERE verified=1") or (0,))[0]
    return {
        "total_uses": total_uses, "unique_codes": unique_codes,
        "unique_users": unique_users, "total_users": total_users,
        "verified": verified,
        "first_use": first_use[0] if first_use else "N/A",
        "last_use":  last_use[0]  if last_use  else "N/A",
    }

def db_get_all_user_ids():
    """Returns all verified user IDs for broadcast."""
    return [r[0] for r in _qa("SELECT user_id FROM users WHERE verified=1")]

# Gift code helpers
def db_create_gift(code, credits, max_uses, created_by):
    _ex("INSERT OR REPLACE INTO gift_codes(code,credits,max_uses,used_count,created_by,created_at,active) VALUES(?,?,?,0,?,?,1)",
        (code.upper(), credits, max_uses, created_by, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))

def db_get_gift(code):
    return _q("SELECT code,credits,max_uses,used_count,created_by,created_at,active FROM gift_codes WHERE code=?",
               (code.upper(),))

def db_has_claimed(code, user_id):
    return bool(_q("SELECT 1 FROM gift_claims WHERE code=? AND user_id=?",(code.upper(), user_id)))

def db_claim_gift(code, user_id):
    _ex("UPDATE gift_codes SET used_count=used_count+1 WHERE code=?",(code.upper(),))
    _ex("INSERT INTO gift_claims(code,user_id,claimed_at) VALUES(?,?,?)",
        (code.upper(), user_id, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))

def db_deactivate_gift(code):
    return _ex("UPDATE gift_codes SET active=0 WHERE code=?",(code.upper(),)) > 0

def db_list_gifts():
    return _qa("SELECT code,credits,max_uses,used_count,active FROM gift_codes ORDER BY rowid DESC LIMIT 20")


# -----------------------------------------------------------------------
# API caller
# -----------------------------------------------------------------------
async def call_api(cfg: dict, code: str) -> dict:
    method = cfg.get("method","GET").upper()
    style  = cfg.get("param_style","query")
    pname  = cfg.get("param_name","phone")
    hdrs   = cfg.get("headers") or {}
    url    = cfg["url"]

    async with httpx.AsyncClient(timeout=60) as client:
        if style == "path":
            url = f"{url.rstrip('/')}/{code}"; params = None
        elif style == "query":
            params = {pname: code}
        else:
            params = None

        try:
            if method == "GET":
                resp = await client.get(url, params=params, headers=hdrs)
            else:
                if style == "json":
                    resp = await client.post(url, json={pname:code}, headers=hdrs)
                else:
                    resp = await client.post(url, params=params, headers=hdrs)

            log.info("API %s %s → %s", method, resp.url, resp.status_code)
            try:
                data = resp.json(); data["_status_code"] = resp.status_code; return data
            except Exception:
                return {"data": resp.text, "_status_code": resp.status_code}
        except httpx.ConnectError as e:
            return {"error": f"Connection failed: {e}"}
        except httpx.TimeoutException:
            return {"error": "Request timed out"}
        except Exception as e:
            return {"error": str(e)}

API_FUNCS = [lambda code, c=cfg: call_api(c, code) for cfg in API_CONFIGS]


# -----------------------------------------------------------------------
# Keyboards
# -----------------------------------------------------------------------
def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([
        [KeyboardButton("🚀 USE"),         KeyboardButton("✨ PREMIUM USE")],
        [KeyboardButton("🎁 Refer & Earn"), KeyboardButton("👤 My Profile")],
        [KeyboardButton("💎 Subscription"), KeyboardButton("👨‍💻 Developer")],
    ], resize_keyboard=True, is_persistent=True)

def join_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(f"📢 {c['name']}", url=c["url"])] for c in db_list_channels()]
    rows.append([InlineKeyboardButton("✅  Verify", callback_data="verify")])
    return InlineKeyboardMarkup(rows)

def stop_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🛑  STOP USE", callback_data="stop")]])

def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Credits",  callback_data="adm_credits"),
         InlineKeyboardButton("💎 Premium",  callback_data="adm_premium")],
        [InlineKeyboardButton("📢 Channels", callback_data="adm_channels"),
         InlineKeyboardButton("👑 Admins",   callback_data="adm_admins")],
        [InlineKeyboardButton("📊 Stats",    callback_data="adm_stats"),
         InlineKeyboardButton("⚙️ Status",   callback_data="adm_status")],
        [InlineKeyboardButton("🎬 Video",    callback_data="adm_video"),
         InlineKeyboardButton("ℹ️ User Info", callback_data="adm_userinfo")],
        [InlineKeyboardButton("🎁 Gift Codes", callback_data="adm_gifts"),
         InlineKeyboardButton("📣 Broadcast",  callback_data="adm_broadcast")],
    ])

def admin_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="adm_home")]])


# -----------------------------------------------------------------------
# Progress
# -----------------------------------------------------------------------
SPINNER = ["🕐","🕑","🕒","🕓","🕔","🕕","🕖","🕗","🕘","🕙","🕚","🕛"]

FAKE_LOGS = [
    "📡 Establishing secure connection...",
    "🔐 Authenticating request...",
    "📤 Sending payload to server...",
    "⚙️ Server is processing...",
    "📥 Fetching response...",
    "🔄 Parsing data stream...",
    "🧬 Decoding results...",
    "✨ Finalizing output...",
]

TIPS = [
    "🐢 Turbo mode... at snail speed 😅",
    "🍕 Order a pizza, this might take a sec...",
    "🎩 Pulling something cool out of the hat...",
    "🚀 Houston, we have liftoff...",
    "🍿 Grab popcorn, show's about to start...",
    "🦄 Unicorns are working overtime for you...",
    "🎲 Rolling the dice of destiny...",
    "😴 Don't fall asleep, almost there...",
]

def prog_bar(done, total):
    n = int((done/total)*12)
    return f"[{'█'*n}{'░'*(12-n)}] {int((done/total)*100)}%"

def build_progress(done_flags, spinner, tick, elapsed, round_num):
    total   = len(done_flags)
    done    = sum(done_flags)
    log_line = FAKE_LOGS[tick % len(FAKE_LOGS)]
    tip      = TIPS[(tick // 6) % len(TIPS)]
    lines = []
    for i, cfg in enumerate(API_CONFIGS):
        if done_flags[i]:
            lines.append(f"  ✅  {cfg['emoji']} {cfg['name']} — Done")
        else:
            lines.append(f"  {spinner}  {cfg['emoji']} {cfg['name']} — Running")
    return (
        f"📟 *SYSTEM LOG* — Round {round_num}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{spinner} `{log_line}`\n\n"
        f"{prog_bar(done, total)}\n\n"
        + "\n".join(lines) +
        f"\n\n━━━━━━━━━━━━━━━━━━━━\n"
        f"_{tip}_\n"
        f"⏱️ `{elapsed}s elapsed`"
    )


# -----------------------------------------------------------------------
# Membership check — supports both old & new Telegram API
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

def get_forward_chat(message):
    """Extract forwarded channel from message — handles both old & new Telegram API."""
    # New API (v21+): forward_origin
    fwd_origin = getattr(message, "forward_origin", None)
    if fwd_origin is not None:
        chat = getattr(fwd_origin, "chat", None)
        if chat and getattr(chat, "type", None) == "channel":
            return chat
    # Old API fallback
    fwd_chat = getattr(message, "forward_from_chat", None)
    if fwd_chat and getattr(fwd_chat, "type", None) == "channel":
        return fwd_chat
    return None


# -----------------------------------------------------------------------
# Send video helper
# -----------------------------------------------------------------------
async def send_video_msg(context, chat_id, caption, reply_markup=None, reply_to=None):
    """Send profile/start video. Falls back to text if video fails."""
    video = get_video()
    kwargs = dict(chat_id=chat_id, caption=caption, parse_mode=ParseMode.MARKDOWN)
    if reply_markup:
        kwargs["reply_markup"] = reply_markup
    if reply_to:
        kwargs["reply_to_message_id"] = reply_to

    if video and video != "NONE":
        try:
            await context.bot.send_video(video=video, **kwargs)
            return
        except Exception as e:
            log.warning("Video send failed: %s", e)
    # Fallback to text
    text_kwargs = dict(chat_id=chat_id, text=caption, parse_mode=ParseMode.MARKDOWN)
    if reply_markup:
        text_kwargs["reply_markup"] = reply_markup
    await context.bot.send_message(**text_kwargs)


# -----------------------------------------------------------------------
# /start
# -----------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    referred_by = None
    if context.args and context.args[0].startswith("ref_"):
        try:
            c = int(context.args[0].replace("ref_",""))
            if c != user.id: referred_by = c
        except ValueError: pass

    if not db_get_user(user.id):
        db_create_user(user.id, user.username or "", user.first_name or "", referred_by)

    row      = db_get_user(user.id)
    verified = row[4]
    channels = db_list_channels()

    if verified:
        caption = (
            f"👋 *Welcome back, {user.first_name}!*\n\n"
            "Everything is set — choose an option below 👇"
        )
        await send_video_msg(context, update.effective_chat.id, caption, main_keyboard())
        return

    if not channels:
        db_set_verified(user.id)
        db_add_credits(user.id, CREDITS_ON_SIGNUP)
        caption = (
            f"✅ *Welcome, {user.first_name}!*\n\n"
            f"🎁 You've received *{CREDITS_ON_SIGNUP} free credits* to get started!\n\n"
            "Choose an option below 👇"
        )
        await send_video_msg(context, update.effective_chat.id, caption, main_keyboard())
        return

    await update.message.reply_text(
        "🔐 *Access Restricted*\n\n"
        "Please join the channel(s) below, then tap *Verify* ✅",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=join_keyboard(),
    )


# -----------------------------------------------------------------------
# Verify
# -----------------------------------------------------------------------
async def on_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user  = update.effective_user

    if not await is_member_of_all(context, user.id):
        await query.answer("❌ You haven't joined all required channels yet!", show_alert=True)
        return

    row          = db_get_user(user.id)
    was_verified = row[4] if row else 0

    if not was_verified:
        db_set_verified(user.id)
        db_add_credits(user.id, CREDITS_ON_SIGNUP)
        referred_by      = row[5] if row else None
        already_credited = row[6] if row else 0
        if referred_by and not already_credited:
            db_add_credits(referred_by, CREDITS_PER_REFERRAL)
            db_mark_ref(user.id)
        try:
            await context.bot.send_message(OWNER_ID,
                f"🆕 *New user verified!*\n\n"
                f"👤 {user.first_name}\n🔗 @{user.username or 'N/A'}\n🆔 `{user.id}`",
                parse_mode=ParseMode.MARKDOWN)
        except Exception: pass

    await query.message.delete()
    caption = (
        f"✅ *Verified! Welcome, {user.first_name}* 🎉\n\n"
        f"🎁 *{CREDITS_ON_SIGNUP} free credits* added to your account!\n\n"
        "Choose an option below 👇"
    )
    await send_video_msg(context, update.effective_chat.id, caption, main_keyboard())


# -----------------------------------------------------------------------
# Menu buttons
# -----------------------------------------------------------------------
MENU_BUTTONS = {"🚀 USE","✨ PREMIUM USE","🎁 Refer & Earn","👤 My Profile","💎 Subscription","👨‍💻 Developer"}

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    user = update.effective_user

    # Admin: awaiting channel forward
    if context.user_data.get("awaiting_channel_forward"):
        fwd_chat = get_forward_chat(update.message)
        if fwd_chat:
            context.user_data["awaiting_channel_forward"] = False
            await _try_add_channel(update, context, fwd_chat.id)
        else:
            await update.message.reply_text(
                "⚠️ *Couldn't detect the channel.*\n\n"
                "Make sure you *forward a message directly from the channel* "
                "(not a link, not copy-paste — use the Forward button).",
                parse_mode=ParseMode.MARKDOWN,
            )
        return

    # Admin: awaiting video
    if context.user_data.get("awaiting_video") and update.message.video:
        context.user_data["awaiting_video"] = False
        file_id = update.message.video.file_id
        db_set_setting("profile_video", file_id)
        await update.message.reply_text("✅ *Dashboard video updated!*", parse_mode=ParseMode.MARKDOWN)
        return

    # Menu button cancels code flow
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
        row     = db_get_user(user.id)
        premium = row[7] if row else 0
        status_msg = await update.message.reply_text(
            build_progress([False]*len(API_CONFIGS), SPINNER[0], 0, 0, 1),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=stop_keyboard(),
        )
        task = asyncio.create_task(
            run_all_apis(text, update, context, status_msg, user.id, premium))
        context.user_data["running_task"] = task
        return

    # Menu routing
    if text == "🚀 USE":
        await handle_use(update, context)
    elif text == "✨ PREMIUM USE":
        await update.message.reply_text(
            "✨ *Premium Use — Coming Soon!*\n\n"
            "We're cooking something special 🔥 Stay tuned!",
            parse_mode=ParseMode.MARKDOWN)
    elif text == "🎁 Refer & Earn":
        await handle_refer(update, context)
    elif text == "👤 My Profile":
        await handle_profile(update, context)
    elif text == "💎 Subscription":
        await update.message.reply_text(
            f"💎 *Subscription*\n\nWant unlimited access?\n\n📩 Contact {SUBSCRIPTION_CONTACT}!",
            parse_mode=ParseMode.MARKDOWN)
    elif text == "👨‍💻 Developer":
        await update.message.reply_text(
            f"👨‍💻 *Developer*\n\nSupport, bugs, business 👉 {DEVELOPER_CONTACT} 🚀",
            parse_mode=ParseMode.MARKDOWN)


# -----------------------------------------------------------------------
# Feature handlers
# -----------------------------------------------------------------------
async def handle_use(update, context):
    user    = update.effective_user
    row     = db_get_user(user.id)
    credits = row[3] if row else 0
    premium = row[7] if row else 0
    if not premium and credits < CREDITS_PER_USE:
        await update.message.reply_text(
            "🚫 *Insufficient Credits*\n\n"
            "You don't have enough credits.\n\n"
            "🎁 Earn via *Refer & Earn*, or 💎 buy a *Subscription*!",
            parse_mode=ParseMode.MARKDOWN)
        return
    context.user_data["awaiting_code"] = True
    await update.message.reply_text(
        "🔑 *Send Me the Code*\n\nPlease send your *10-digit code* to continue 👇",
        parse_mode=ParseMode.MARKDOWN)


async def handle_refer(update, context):
    user = update.effective_user
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{user.id}"
    refs = db_count_refs(user.id)
    await update.message.reply_text(
        "🎁 *Refer & Earn*\n\n"
        f"Earn *{CREDITS_PER_REFERRAL} credits* for every friend who joins & verifies! 🚀\n\n"
        f"👥 Successful referrals: *{refs}*\n\n"
        "🔗 *Your referral link:*\n"
        f"`{link}`\n\n"
        "_Tap the link to copy, then share with friends!_",
        parse_mode=ParseMode.MARKDOWN)


async def handle_profile(update, context):
    user    = update.effective_user
    row     = db_get_user(user.id)
    if not row:
        await update.message.reply_text("❌ Profile not found. Try /start again.")
        return
    credits = row[3]
    premium = row[7]
    status  = "💎 *PREMIUM* ✨" if premium else "🆓 Free User"
    caption = (
        "👤 *Your Profile*\n\n"
        f"📛 Name: {user.first_name}\n"
        f"🔗 Username: @{user.username or 'N/A'}\n"
        f"🆔 User ID: `{user.id}`\n"
        f"💰 Credits: *{credits}*\n"
        f"⭐ Status: {status}\n"
    )
    await send_video_msg(context, update.effective_chat.id, caption,
                         reply_to=update.message.message_id)


# -----------------------------------------------------------------------
# STOP
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
                parse_mode=ParseMode.MARKDOWN)
        except Exception: pass
    else:
        await query.answer("Nothing running right now.", show_alert=True)


# -----------------------------------------------------------------------
# API runner — continuous loop
# -----------------------------------------------------------------------
def format_result(cfg, response) -> str:
    emoji = cfg['emoji']
    name  = html.escape(cfg['name'])
    if not isinstance(response, dict):
        return f"{emoji} <b>{name}</b>\n<pre>{html.escape(str(response))}</pre>"
    sc = response.pop("_status_code", None)
    sc_txt = f" <i>(HTTP {sc})</i>" if sc else ""
    pretty = json.dumps(response, indent=2, ensure_ascii=False)
    return f"{emoji} <b>{name}</b>{sc_txt}\n<pre>{html.escape(pretty)}</pre>"


async def run_all_apis(code, update, context, status_msg, user_id, premium=0):
    result_msg = None
    round_num  = 0
    TICK       = 0.5

    try:
        while True:
            round_num += 1
            tasks = [asyncio.ensure_future(fn(code)) for fn in API_FUNCS]
            tick  = 0

            while not all(t.done() for t in tasks):
                spinner    = SPINNER[tick % len(SPINNER)]
                done_flags = [t.done() for t in tasks]
                elapsed    = int(tick * TICK)
                try:
                    await status_msg.edit_text(
                        build_progress(done_flags, spinner, tick, elapsed, round_num),
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=stop_keyboard(),
                    )
                except Exception: pass
                await asyncio.sleep(TICK)
                tick += 1

            # Collect results
            results = []
            for i, t in enumerate(tasks):
                try:
                    r = t.result()
                    db_log_api(user_id, code, API_CONFIGS[i]["name"], 1)
                    results.append(r)
                except Exception as e:
                    db_log_api(user_id, code, API_CONFIGS[i]["name"], 0)
                    results.append({"error": str(e)})

            # Show 100% complete
            try:
                await status_msg.edit_text(
                    build_progress([True]*len(API_CONFIGS), "✅", tick, int(tick*TICK), round_num),
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception: pass

            formatted = [format_result(API_CONFIGS[i], results[i]) for i in range(len(results))]
            blocks    = f"🔄 <b>Round {round_num} — Results</b>\n\n" + "\n\n".join(formatted)

            db_add_credits(user_id, -CREDITS_PER_USE if not premium else 0)

            if result_msg is None:
                result_msg = await update.message.reply_text(
                    blocks, parse_mode=ParseMode.HTML, reply_markup=stop_keyboard())
            else:
                try:
                    await result_msg.edit_text(
                        blocks, parse_mode=ParseMode.HTML, reply_markup=stop_keyboard())
                except Exception: pass

            # Reset progress for next round
            try:
                await status_msg.edit_text(
                    build_progress([False]*len(API_CONFIGS), SPINNER[0], 0, 0, round_num+1),
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=stop_keyboard(),
                )
            except Exception: pass

            await asyncio.sleep(1.5)

    except asyncio.CancelledError:
        for t in (tasks if 'tasks' in dir() else []):
            if not t.done(): t.cancel()
        raise


# -----------------------------------------------------------------------
# Admin decorators
# -----------------------------------------------------------------------
def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not db_is_admin(update.effective_user.id):
            await update.message.reply_text("🚫 Admins only."); return
        return await func(update, context)
    return wrapper

def owner_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != OWNER_ID:
            await update.message.reply_text("🚫 Owner only."); return
        return await func(update, context)
    return wrapper


# -----------------------------------------------------------------------
# Admin commands
# -----------------------------------------------------------------------
@admin_only
async def admin_panel(update, context):
    await update.message.reply_text(
        "🛠️ *Admin Panel*\n\nChoose a category 👇",
        parse_mode=ParseMode.MARKDOWN, reply_markup=admin_keyboard())

@admin_only
async def admin_addcredit(update, context):
    try: tid,amt = int(context.args[0]),int(context.args[1])
    except: await update.message.reply_text("⚠️ `/addcredit <uid> <amount>`",parse_mode=ParseMode.MARKDOWN); return
    if not db_get_user(tid): await update.message.reply_text("❌ User not found."); return
    db_add_credits(tid,amt)
    await update.message.reply_text(f"✅ Added *{amt}* credits to `{tid}`.",parse_mode=ParseMode.MARKDOWN)

@admin_only
async def admin_removecredit(update, context):
    try: tid,amt = int(context.args[0]),int(context.args[1])
    except: await update.message.reply_text("⚠️ `/removecredit <uid> <amount>`",parse_mode=ParseMode.MARKDOWN); return
    if not db_get_user(tid): await update.message.reply_text("❌ User not found."); return
    db_add_credits(tid,-amt)
    await update.message.reply_text(f"✅ Removed *{amt}* credits from `{tid}`.",parse_mode=ParseMode.MARKDOWN)

@admin_only
async def admin_setcredit(update, context):
    try: tid,amt = int(context.args[0]),int(context.args[1])
    except: await update.message.reply_text("⚠️ `/setcredit <uid> <amount>`",parse_mode=ParseMode.MARKDOWN); return
    if not db_set_credits(tid,amt): await update.message.reply_text("❌ User not found."); return
    await update.message.reply_text(f"✅ Set `{tid}` credits to *{amt}*.",parse_mode=ParseMode.MARKDOWN)

@admin_only
async def admin_addpremium(update, context):
    try: tid = int(context.args[0])
    except: await update.message.reply_text("⚠️ `/addpremium <uid>`",parse_mode=ParseMode.MARKDOWN); return
    if not db_set_premium(tid,1): await update.message.reply_text("❌ User not found."); return
    await update.message.reply_text(f"💎 `{tid}` is now *PREMIUM*.",parse_mode=ParseMode.MARKDOWN)
    try: await context.bot.send_message(tid,"💎 *You're now PREMIUM!* Unlimited USE — no credits needed! 🚀",parse_mode=ParseMode.MARKDOWN)
    except: pass

@admin_only
async def admin_removepremium(update, context):
    try: tid = int(context.args[0])
    except: await update.message.reply_text("⚠️ `/removepremium <uid>`",parse_mode=ParseMode.MARKDOWN); return
    if not db_set_premium(tid,0): await update.message.reply_text("❌ User not found."); return
    await update.message.reply_text(f"✅ Premium removed from `{tid}`.",parse_mode=ParseMode.MARKDOWN)

@admin_only
async def admin_userinfo(update, context):
    try: tid = int(context.args[0])
    except: await update.message.reply_text("⚠️ `/userinfo <uid>`",parse_mode=ParseMode.MARKDOWN); return
    row = db_get_user(tid)
    if not row: await update.message.reply_text("❌ User not found."); return
    _,uname,fname,credits,verified,ref_by,_,premium = row
    await update.message.reply_text(
        f"📋 *User Info*\n\n"
        f"📛 {fname}\n🔗 @{uname or 'N/A'}\n🆔 `{tid}`\n"
        f"💰 Credits: *{credits}*\n"
        f"⭐ Premium: {'Yes 💎' if premium else 'No'}\n"
        f"✅ Verified: {'Yes' if verified else 'No'}\n"
        f"👥 Referred by: `{ref_by or 'N/A'}`",
        parse_mode=ParseMode.MARKDOWN)

@admin_only
async def admin_offbot(update, context):
    db_set_setting("bot_enabled","0")
    await update.message.reply_text("🔴 *Bot is now OFF.*",parse_mode=ParseMode.MARKDOWN)

@admin_only
async def admin_onbot(update, context):
    db_set_setting("bot_enabled","1")
    await update.message.reply_text("🟢 *Bot is now ON.*",parse_mode=ParseMode.MARKDOWN)

@admin_only
async def admin_listadmins(update, context):
    ids = db_list_admins()
    lines = [f"👑 `{OWNER_ID}` (owner)"] + [f"🛠️ `{i}`" for i in ids]
    await update.message.reply_text("📋 *Admins*\n\n"+"\n".join(lines),parse_mode=ParseMode.MARKDOWN)

@owner_only
async def admin_addadmin(update, context):
    try: tid = int(context.args[0])
    except: await update.message.reply_text("⚠️ `/addadmin <uid>`",parse_mode=ParseMode.MARKDOWN); return
    db_add_admin(tid, update.effective_user.id)
    await update.message.reply_text(f"✅ `{tid}` is now an admin.",parse_mode=ParseMode.MARKDOWN)
    try: await context.bot.send_message(tid,"🛠️ *You've been granted admin access!* Send /admin.",parse_mode=ParseMode.MARKDOWN)
    except: pass

@owner_only
async def admin_removeadmin(update, context):
    try: tid = int(context.args[0])
    except: await update.message.reply_text("⚠️ `/removeadmin <uid>`",parse_mode=ParseMode.MARKDOWN); return
    if tid==OWNER_ID: await update.message.reply_text("🚫 Owner can't be removed."); return
    if not db_remove_admin(tid): await update.message.reply_text("❌ Not an admin."); return
    await update.message.reply_text(f"✅ `{tid}` removed.",parse_mode=ParseMode.MARKDOWN)

@admin_only
async def admin_addchannel(update, context):
    if not context.args:
        context.user_data["awaiting_channel_forward"] = True
        await update.message.reply_text(
            "📢 *Add Force-Join Channel*\n\n"
            "For *public* channel: `/addchannel @username`\n\n"
            "For *private* channel:\n"
            "1️⃣ Make bot *admin* in that channel\n"
            "2️⃣ Open the channel\n"
            "3️⃣ Tap any message → Forward → Forward to this bot chat\n\n"
            "⚠️ Bot must be *admin* in channel first!",
            parse_mode=ParseMode.MARKDOWN)
        return
    raw = context.args[0]
    username = raw.replace("https://t.me/","").replace("http://t.me/","").lstrip("@").strip()
    if username.startswith("+") or "joinchat" in username:
        await update.message.reply_text("⚠️ Private channel — run `/addchannel` with no args and forward a post.",parse_mode=ParseMode.MARKDOWN); return
    await _try_add_channel(update, context, f"@{username}")

@admin_only
async def admin_removechannel(update, context):
    try: cid = context.args[0]
    except: await update.message.reply_text("⚠️ `/removechannel <chat_id>`",parse_mode=ParseMode.MARKDOWN); return
    if not db_remove_channel(cid): await update.message.reply_text("❌ Channel not found."); return
    await update.message.reply_text("✅ Channel removed.")

@admin_only
async def admin_listchannels(update, context):
    chs = db_list_channels()
    if not chs: await update.message.reply_text("📭 No channels. Use /addchannel."); return
    lines = [f"📢 *{c['name']}*\nID: `{c['chat_id']}`" for c in chs]
    await update.message.reply_text("📋 *Force-Join Channels*\n\n"+"\n\n".join(lines),parse_mode=ParseMode.MARKDOWN)

@admin_only
async def admin_setvideo(update, context):
    if update.message.reply_to_message and update.message.reply_to_message.video:
        file_id = update.message.reply_to_message.video.file_id
    elif context.args:
        file_id = context.args[0]
    else:
        context.user_data["awaiting_video"] = True
        await update.message.reply_text("🎬 Send the video file now 👇",parse_mode=ParseMode.MARKDOWN); return
    db_set_setting("profile_video", file_id)
    await update.message.reply_text("✅ *Video updated!*",parse_mode=ParseMode.MARKDOWN)
    try: await context.bot.send_video(update.effective_chat.id, file_id, caption="Preview 👆")
    except Exception as e: await update.message.reply_text(f"⚠️ Set but preview failed: {e}")

@admin_only
async def admin_clearvideo(update, context):
    db_set_setting("profile_video","NONE")
    await update.message.reply_text("✅ Video removed. Profile shows text only.")

@admin_only
async def admin_apitest(update, context):
    test_code = context.args[0] if context.args else "1234567890"
    msg = await update.message.reply_text(f"🧪 Testing with code `{test_code}`...",parse_mode=ParseMode.MARKDOWN)
    lines = []
    for cfg in API_CONFIGS:
        style = cfg.get("param_style","query")
        pname = cfg.get("param_name","phone")
        url   = cfg["url"]
        if style=="path": preview = f"{url.rstrip('/')}/{test_code}"
        elif style=="query": preview = f"{url}?{pname}={test_code}"
        else: preview = url
        result = await call_api(cfg, test_code)
        pretty = json.dumps(result, indent=2, ensure_ascii=False)
        lines.append(f"{cfg['emoji']} <b>{html.escape(cfg['name'])}</b>\n🔗 <code>{html.escape(preview)}</code>\n<pre>{html.escape(pretty[:400])}</pre>")
    await msg.edit_text("🧪 <b>API Test Results</b>\n\n"+"\n\n".join(lines),parse_mode=ParseMode.HTML)

@admin_only
async def admin_stats(update, context):
    s = db_get_stats()
    await update.message.reply_text(
        "📊 *Bot Statistics*\n\n"
        f"👥 Total users: *{s['total_users']}*\n"
        f"✅ Verified users: *{s['verified']}*\n\n"
        f"🚀 Total API uses: *{s['total_uses']}*\n"
        f"👤 Unique users who used API: *{s['unique_users']}*\n"
        f"🔢 Unique codes submitted: *{s['unique_codes']}*\n\n"
        f"🕐 First API use: `{s['first_use']}`\n"
        f"🕐 Last API use: `{s['last_use']}`",
        parse_mode=ParseMode.MARKDOWN)


@admin_only
# -----------------------------------------------------------------------
# Gift Code system
# -----------------------------------------------------------------------
@admin_only
async def admin_giftcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Create a gift code.
    Usage: /giftcode <CODE> <credits> [max_uses]
    Example: /giftcode WELCOME50 5 100
    If no code given, a random one is generated.
    """
    import random, string

    args = context.args
    if not args:
        # Generate random code
        code     = "GIFT-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        credits  = 2
        max_uses = 1
    elif len(args) == 1:
        code     = args[0]
        credits  = 2
        max_uses = 1
    elif len(args) == 2:
        code     = args[0]
        try: credits = int(args[1])
        except: await update.message.reply_text("⚠️ Credits must be a number."); return
        max_uses = 1
    else:
        code     = args[0]
        try: credits  = int(args[1]); max_uses = int(args[2])
        except: await update.message.reply_text("⚠️ Usage: `/giftcode <CODE> <credits> <max_uses>`", parse_mode=ParseMode.MARKDOWN); return

    db_create_gift(code, credits, max_uses, update.effective_user.id)
    await update.message.reply_text(
        f"🎁 *Gift Code Created!*\n\n"
        f"🔑 Code: `{code.upper()}`\n"
        f"💰 Credits: *{credits}*\n"
        f"👥 Max uses: *{max_uses}*\n\n"
        f"Users can redeem with:\n`/redeem {code.upper()}`",
        parse_mode=ParseMode.MARKDOWN,
    )

@admin_only
async def admin_deletegift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Usage: `/deletegift <CODE>`", parse_mode=ParseMode.MARKDOWN); return
    code = context.args[0]
    if db_deactivate_gift(code):
        await update.message.reply_text(f"✅ Gift code `{code.upper()}` deactivated.", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text("❌ Code not found.")

@admin_only
async def admin_listgifts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gifts = db_list_gifts()
    if not gifts:
        await update.message.reply_text("📭 No gift codes yet. Use /giftcode to create one.")
        return
    lines = []
    for g in gifts:
        code, credits, max_uses, used, active = g
        status = "✅" if active else "❌"
        lines.append(f"{status} `{code}` — {credits} cr | {used}/{max_uses} used")
    await update.message.reply_text(
        "🎁 *Gift Codes* (last 20)\n\n" + "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
    )

# User redeem command — works for everyone
async def cmd_redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    row  = db_get_user(user.id)
    if not row or not row[4]:
        await update.message.reply_text(
            "❌ *Please verify first!*\n\nSend /start to join and verify.",
            parse_mode=ParseMode.MARKDOWN,
        ); return

    if not context.args:
        await update.message.reply_text(
            "🎁 *Redeem a Gift Code*\n\nUsage: `/redeem YOUR_CODE`",
            parse_mode=ParseMode.MARKDOWN,
        ); return

    code  = context.args[0].upper()
    gift  = db_get_gift(code)

    if not gift:
        await update.message.reply_text("❌ *Invalid code.* Double-check and try again.", parse_mode=ParseMode.MARKDOWN); return

    _code, credits, max_uses, used_count, _by, _at, active = gift

    if not active:
        await update.message.reply_text("❌ *This code has been deactivated.*", parse_mode=ParseMode.MARKDOWN); return

    if used_count >= max_uses:
        await update.message.reply_text("❌ *This code has already reached its usage limit.*", parse_mode=ParseMode.MARKDOWN); return

    if db_has_claimed(code, user.id):
        await update.message.reply_text("❌ *You've already redeemed this code.*", parse_mode=ParseMode.MARKDOWN); return

    # All checks passed — claim it
    db_claim_gift(code, user.id)
    db_add_credits(user.id, credits)
    new_credits = (db_get_user(user.id) or (0,0,0,0))[3]

    await update.message.reply_text(
        f"🎉 *Code Redeemed Successfully!*\n\n"
        f"🎁 Code: `{code}`\n"
        f"💰 Credits added: *+{credits}*\n"
        f"💳 Your total credits: *{new_credits}*\n\n"
        f"Enjoy! 🚀",
        parse_mode=ParseMode.MARKDOWN,
    )

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Broadcast a message to all verified users.
    Usage:
      - Reply to any message with /broadcast  → forwards that message
      - /broadcast Hello everyone!            → sends plain text
    """
    # Get broadcast content
    if update.message.reply_to_message:
        reply_msg = update.message.reply_to_message
        use_forward = True
    elif context.args:
        broadcast_text = " ".join(context.args)
        use_forward = False
    else:
        await update.message.reply_text(
            "📣 *Broadcast Usage:*\n\n"
            "1️⃣ Reply to any message with `/broadcast`\n"
            "2️⃣ Or: `/broadcast Your message here`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    user_ids = db_get_all_user_ids()
    total    = len(user_ids)

    if total == 0:
        await update.message.reply_text("📭 No verified users to broadcast to.")
        return

    status = await update.message.reply_text(
        f"📣 *Broadcasting to {total} users...*\n\n"
        f"[{'░'*10}] 0%",
        parse_mode=ParseMode.MARKDOWN,
    )

    sent = 0
    failed = 0

    for i, uid in enumerate(user_ids):
        try:
            if use_forward:
                await reply_msg.forward(uid)
            else:
                await context.bot.send_message(
                    uid,
                    f"📢 *Message from Admin*\n\n{broadcast_text}",
                    parse_mode=ParseMode.MARKDOWN,
                )
            sent += 1
        except Exception:
            failed += 1

        # Update progress every 10 users
        if (i + 1) % 10 == 0 or (i + 1) == total:
            pct   = int(((i + 1) / total) * 100)
            filled = int(pct / 10)
            bar   = "█" * filled + "░" * (10 - filled)
            try:
                await status.edit_text(
                    f"📣 *Broadcasting...*\n\n"
                    f"[{bar}] {pct}%\n\n"
                    f"✅ Sent: {sent} | ❌ Failed: {failed}",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass
        await asyncio.sleep(0.05)  # avoid flood limits

    await status.edit_text(
        f"✅ *Broadcast Complete!*\n\n"
        f"📤 Total: *{total}*\n"
        f"✅ Sent: *{sent}*\n"
        f"❌ Failed: *{failed}*",
        parse_mode=ParseMode.MARKDOWN,
    )


async def _try_add_channel(update, context, chat_ref):
    try:
        chat = await context.bot.get_chat(chat_ref)
    except Exception as e:
        await update.message.reply_text(f"❌ Couldn't find channel.\n`{e}`",parse_mode=ParseMode.MARKDOWN); return
    try:
        bm = await context.bot.get_chat_member(chat.id, context.bot.id)
        if bm.status not in ("administrator","creator"):
            await update.message.reply_text(
                f"⚠️ Bot is *not admin* in *{html.escape(chat.title)}*.\n\nMake it admin first, then try again.",
                parse_mode=ParseMode.MARKDOWN); return
    except Exception as e:
        await update.message.reply_text(f"❌ Can't verify admin status.\n`{e}`",parse_mode=ParseMode.MARKDOWN); return
    url = f"https://t.me/{chat.username}" if chat.username else (chat.invite_link or "")
    if not url:
        try: url = await context.bot.export_chat_invite_link(chat.id)
        except Exception: url = ""
    db_add_channel(chat.id, chat.title, url)
    await update.message.reply_text(f"✅ *{html.escape(chat.title)}* added! 🎉",parse_mode=ParseMode.MARKDOWN)


# -----------------------------------------------------------------------
# Admin inline callbacks
# -----------------------------------------------------------------------
async def on_admin_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not db_is_admin(query.from_user.id):
        await query.answer("🚫 Admins only.", show_alert=True); return
    await query.answer()
    data     = query.data
    is_owner = query.from_user.id == OWNER_ID

    if data == "adm_home":
        await query.edit_message_text("🛠️ *Admin Panel*\n\nChoose a category 👇",
                                      parse_mode=ParseMode.MARKDOWN, reply_markup=admin_keyboard())
    elif data == "adm_credits":
        await query.edit_message_text(
            "💰 *Credit Commands*\n\n"
            "`/addcredit <uid> <amount>`\n`/removecredit <uid> <amount>`\n`/setcredit <uid> <amount>`",
            parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back())
    elif data == "adm_premium":
        await query.edit_message_text(
            "💎 *Premium Commands*\n\n`/addpremium <uid>`\n`/removepremium <uid>`",
            parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back())
    elif data == "adm_channels":
        chs = db_list_channels()
        ch_text = "\n".join([f"📢 {c['name']} — `{c['chat_id']}`" for c in chs]) if chs else "_(none yet)_"
        await query.edit_message_text(
            f"📢 *Channels*\n\n"
            f"`/addchannel @user` — public\n"
            f"`/addchannel` — private (forward post)\n"
            f"`/removechannel <id>` — remove\n\n"
            f"*Current:*\n{ch_text}",
            parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back())
    elif data == "adm_admins":
        ids   = db_list_admins()
        lines = [f"👑 `{OWNER_ID}` (owner)"] + [f"🛠️ `{i}`" for i in ids]
        text  = "👑 *Admins*\n\n" + "\n".join(lines)
        if is_owner: text += "\n\n`/addadmin <uid>` · `/removeadmin <uid>`"
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back())
    elif data == "adm_stats":
        s = db_get_stats()
        await query.edit_message_text(
            "📊 *Bot Statistics*\n\n"
            f"👥 Total users: *{s['total_users']}*\n"
            f"✅ Verified: *{s['verified']}*\n\n"
            f"🚀 Total API uses: *{s['total_uses']}*\n"
            f"👤 Unique users: *{s['unique_users']}*\n"
            f"🔢 Unique codes: *{s['unique_codes']}*\n\n"
            f"🕐 First use: `{s['first_use']}`\n"
            f"🕐 Last use: `{s['last_use']}`",
            parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back())
    elif data in ("adm_status","adm_toggle"):
        if data=="adm_toggle":
            db_set_setting("bot_enabled","0" if is_bot_enabled() else "1")
        enabled = is_bot_enabled()
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔴 Turn OFF" if enabled else "🟢 Turn ON", callback_data="adm_toggle")],
            [InlineKeyboardButton("🔙 Back", callback_data="adm_home")],
        ])
        await query.edit_message_text(
            f"⚙️ *Bot Status:* {'🟢 ON' if enabled else '🔴 OFF'}",
            parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    elif data == "adm_video":
        vid = get_video()
        vs  = f"`{vid[:50]}...`" if vid and vid!="NONE" else "_(not set)_"
        await query.edit_message_text(
            f"🎬 *Dashboard/Start Video*\n\nCurrent: {vs}\n\n"
            "`/setvideo` — send or reply to a video\n"
            "`/clearvideo` — remove video",
            parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back())
    elif data == "adm_userinfo":
        await query.edit_message_text(
            "ℹ️ *User Info*\n\n`/userinfo <user_id>`",
            parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back())

    elif data == "adm_gifts":
        gifts = db_list_gifts()
        if not gifts:
            gift_text = "_(no codes yet)_"
        else:
            lines = []
            for g in gifts[:8]:
                code, credits, max_uses, used, active = g
                st = "✅" if active else "❌"
                lines.append(f"{st} `{g[0]}` — {credits}cr | {used}/{max_uses}")
            gift_text = "\n".join(lines)
        await query.edit_message_text(
            f"🎁 *Gift Codes*\n\n"
            f"`/giftcode <CODE> <credits> <max_uses>` — create\n"
            f"`/giftcode WELCOME 5 100` — example (5cr, 100 uses)\n"
            f"`/deletegift <CODE>` — deactivate\n"
            f"`/listgifts` — full list\n\n"
            f"*Recent codes:*\n{gift_text}",
            parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back())

    elif data == "adm_broadcast":
        await query.edit_message_text(
            "📣 *Broadcast*\n\n"
            "Reply to any message with `/broadcast` — forwards it to all verified users.\n\n"
            "Or: `/broadcast Your text here`",
            parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back())


# -----------------------------------------------------------------------
# Maintenance gate
# -----------------------------------------------------------------------
async def maintenance_gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user and db_is_admin(user.id): return
    if not is_bot_enabled():
        if update.callback_query:
            await update.callback_query.answer("🔴 Bot is currently OFF!", show_alert=True)
        elif update.message:
            await update.message.reply_text(
                "🔴 *Bot is currently OFF*\n\nMaintenance in progress — check back later! 🙏",
                parse_mode=ParseMode.MARKDOWN)
        raise ApplicationHandlerStop


# -----------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(TypeHandler(Update, maintenance_gate), group=-1)

    app.add_handler(CommandHandler("start",         start))
    app.add_handler(CommandHandler("admin",         admin_panel))
    app.add_handler(CommandHandler("addcredit",     admin_addcredit))
    app.add_handler(CommandHandler("removecredit",  admin_removecredit))
    app.add_handler(CommandHandler("setcredit",     admin_setcredit))
    app.add_handler(CommandHandler("addpremium",    admin_addpremium))
    app.add_handler(CommandHandler("removepremium", admin_removepremium))
    app.add_handler(CommandHandler("userinfo",      admin_userinfo))
    app.add_handler(CommandHandler("offbot",        admin_offbot))
    app.add_handler(CommandHandler("onbot",         admin_onbot))
    app.add_handler(CommandHandler("listadmins",    admin_listadmins))
    app.add_handler(CommandHandler("addadmin",      admin_addadmin))
    app.add_handler(CommandHandler("removeadmin",   admin_removeadmin))
    app.add_handler(CommandHandler("addchannel",    admin_addchannel))
    app.add_handler(CommandHandler("removechannel", admin_removechannel))
    app.add_handler(CommandHandler("listchannels",  admin_listchannels))
    app.add_handler(CommandHandler("setvideo",      admin_setvideo))
    app.add_handler(CommandHandler("clearvideo",    admin_clearvideo))
    app.add_handler(CommandHandler("apitest",       admin_apitest))
    app.add_handler(CommandHandler("stats",         admin_stats))
    app.add_handler(CommandHandler("broadcast",     admin_broadcast))
    app.add_handler(CommandHandler("giftcode",      admin_giftcode))
    app.add_handler(CommandHandler("deletegift",    admin_deletegift))
    app.add_handler(CommandHandler("listgifts",     admin_listgifts))
    app.add_handler(CommandHandler("redeem",        cmd_redeem))

    app.add_handler(CallbackQueryHandler(on_verify,    pattern="^verify$"))
    app.add_handler(CallbackQueryHandler(on_stop,      pattern="^stop$"))
    app.add_handler(CallbackQueryHandler(on_admin_cb,  pattern="^adm_"))

    app.add_handler(MessageHandler(
        (filters.TEXT | filters.VIDEO) & ~filters.COMMAND, on_message))

    log.info("Bot starting...")
    app.run_polling()

if __name__ == "__main__":
    main()
