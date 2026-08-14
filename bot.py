"""
Telegram Bot — Full Featured
pip install python-telegram-bot httpx
"""

import asyncio
import html
import json
import logging
import os
import random
import sqlite3
import string
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
    ChatJoinRequestHandler,
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
# BLOCKQUOTE PATCH
# Every outgoing bot message gets wrapped in a Telegram <blockquote>.
# Legacy-Markdown formatting already used throughout this file
# (*bold*, `code`, _italic_) is converted to HTML so it keeps working
# once parse_mode is switched to HTML.
#
# NOTE: dynamic content (first_name, username, etc.) flows straight into
# these captions/messages. If that text happens to contain *, `, or _ in
# a way that forms an unbalanced/nested tag, Telegram's HTML parser will
# reject the whole message ("can't parse entities"). To guarantee a
# message is always delivered, every patched send/edit below first tries
# the formatted HTML version and, only if Telegram rejects it, retries
# once with plain (unformatted, safely escaped) text.
# ======================================================================
import re as _re

def _md_to_html(text: str) -> str:
    if text is None:
        return text
    escaped = html.escape(str(text), quote=False)
    escaped = _re.sub(r"```(.+?)```", r"<pre>\1</pre>", escaped, flags=_re.DOTALL)
    escaped = _re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = _re.sub(r"\*([^*\n]+)\*", r"<b>\1</b>", escaped)
    escaped = _re.sub(r"(?<![\w<])_([^_\n]+)_(?![\w>])", r"<i>\1</i>", escaped)
    return escaped

def _to_blockquote(text: str) -> str:
    if not text:
        return text
    return f"<blockquote>{_md_to_html(text)}</blockquote>"

def _plain_blockquote(text: str) -> str:
    """Fallback with zero inline formatting — just escaped text in a
    blockquote. Can't fail to parse since it contains no other tags."""
    if not text:
        return text
    return f"<blockquote>{html.escape(str(text), quote=False)}</blockquote>"

def _patch_blockquote():
    """Monkey-patch only the low-level Bot methods. Message.reply_text and
    CallbackQuery.edit_message_text both delegate to these internally, so
    patching them too (as an earlier version of this patch did) caused the
    text to be blockquote-wrapped TWICE. Patching only the Bot-level
    methods avoids that while still covering every call site."""
    from telegram import Bot
    from telegram.error import BadRequest

    _orig_send_message = Bot.send_message
    async def _send_message(self, chat_id, text=None, *args, **kwargs):
        kwargs.pop("parse_mode", None)
        kwargs["parse_mode"] = ParseMode.HTML
        try:
            return await _orig_send_message(self, chat_id, _to_blockquote(text), *args, **kwargs)
        except BadRequest as e:
            if "parse entities" in str(e).lower() or "can't parse" in str(e).lower():
                log.warning("send_message HTML rejected, retrying plain: %s", e)
                return await _orig_send_message(self, chat_id, _plain_blockquote(text), *args, **kwargs)
            raise
    Bot.send_message = _send_message

    _orig_edit_message_text = Bot.edit_message_text
    async def _edit_message_text(self, text=None, *args, **kwargs):
        kwargs.pop("parse_mode", None)
        kwargs["parse_mode"] = ParseMode.HTML
        try:
            return await _orig_edit_message_text(self, _to_blockquote(text), *args, **kwargs)
        except BadRequest as e:
            if "parse entities" in str(e).lower() or "can't parse" in str(e).lower():
                log.warning("edit_message_text HTML rejected, retrying plain: %s", e)
                return await _orig_edit_message_text(self, _plain_blockquote(text), *args, **kwargs)
            raise
    Bot.edit_message_text = _edit_message_text

    _orig_send_video = Bot.send_video
    async def _send_video(self, *args, **kwargs):
        if kwargs.get("caption"):
            raw_caption = kwargs["caption"]
            kwargs.pop("parse_mode", None)
            kwargs["parse_mode"] = ParseMode.HTML
            kwargs["caption"] = _to_blockquote(raw_caption)
            try:
                return await _orig_send_video(self, *args, **kwargs)
            except BadRequest as e:
                if "parse entities" in str(e).lower() or "can't parse" in str(e).lower():
                    log.warning("send_video HTML caption rejected, retrying plain: %s", e)
                    kwargs["caption"] = _plain_blockquote(raw_caption)
                    return await _orig_send_video(self, *args, **kwargs)
                raise
        return await _orig_send_video(self, *args, **kwargs)
    Bot.send_video = _send_video

    log.info("Blockquote patch applied — all bot messages now render as blockquotes.")

_patch_blockquote()

# ======================================================================
# CONFIG
# ======================================================================
BOT_TOKEN   = os.environ.get("BOT_TOKEN", "8495656887:AAGRiOoRIdfEsfwMND2WypnnMWXlstjpN6Y")
BOT_USERNAME = "liesworlds2bot"
OWNER_ID     = 8137776838
ADMIN_ID     = 8790645158  # Second admin added

SUBSCRIPTION_CONTACT = "@liesworlds"
DEVELOPER_CONTACT    = "@liesworlds"

CREDITS_PER_REFERRAL = 2
CREDITS_PER_USE      = 1
CREDITS_ON_SIGNUP    = 2

# Add your real API URLs here
API_CONFIGS = [
    {
        "emoji": "🪄", "name": "Casting Magic",
        "url": "https://wtf-production-8350.up.railway.app/bomb",
        "method": "GET", 
        "param_style": "query", 
        "param_name": "number",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://t.me/",
            "Origin": "https://t.me"
        }
    },
    {
        "emoji": "🎉", "name": "Adding Sparkle",
        "url": "https://newbomb-production.up.railway.app//bomb",
        "method": "GET", 
        "param_style": "query", 
        "param_name": "phone",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://t.me/",
            "Origin": "https://t.me"
        }
    },
]

PROFILE_VIDEO = os.environ.get(
    "PROFILE_VIDEO",
    "BAACAgUAAxkBAAFRdYpqeX8TQHmU4taNopyqEvCFP1S-lQACxR4AAreX0FehTWtCPlqNbT0E",
)
FORCE_CHANNELS = []  # managed via /addchannel
# ======================================================================


# -----------------------------------------------------------------------
# Database — init runs immediately at module load
# -----------------------------------------------------------------------
def _resolve_db_path() -> str:
    for path in [os.environ.get("DB_PATH"), "/data/bot.db"]:
        if not path:
            continue
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            test = path + ".tmp"
            open(test, "w").close()
            os.remove(test)
            log.info("Using DB: %s", path)
            return path
        except Exception:
            continue
    log.warning("Using local bot.db")
    return "bot.db"


DB_PATH = _resolve_db_path()


def db_init():
    c = sqlite3.connect(DB_PATH)
    c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT, first_name TEXT,
            credits INTEGER DEFAULT 0,
            verified INTEGER DEFAULT 0,
            referred_by INTEGER,
            referral_credited INTEGER DEFAULT 0,
            premium INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT
        );
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY, added_by INTEGER
        );
        CREATE TABLE IF NOT EXISTS force_channels (
            chat_id TEXT PRIMARY KEY,
            name TEXT, url TEXT,
            require_request INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS join_requests (
            user_id INTEGER,
            chat_id TEXT,
            PRIMARY KEY (user_id, chat_id)
        );
        CREATE TABLE IF NOT EXISTS api_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, code TEXT,
            api_name TEXT, timestamp TEXT,
            success INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS gift_codes (
            code TEXT PRIMARY KEY,
            credits INTEGER DEFAULT 0,
            max_uses INTEGER DEFAULT 1,
            used_count INTEGER DEFAULT 0,
            created_by INTEGER,
            created_at TEXT,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS gift_claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT, user_id INTEGER,
            claimed_at TEXT
        );
    """)
    # migrations
    cols = [r[1] for r in c.execute("PRAGMA table_info(users)").fetchall()]
    if "premium" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN premium INTEGER DEFAULT 0")
    fc_cols = [r[1] for r in c.execute("PRAGMA table_info(force_channels)").fetchall()]
    if "require_request" not in fc_cols:
        c.execute("ALTER TABLE force_channels ADD COLUMN require_request INTEGER DEFAULT 0")
    if FORCE_CHANNELS:
        for ch in FORCE_CHANNELS:
            c.execute(
                "INSERT OR IGNORE INTO force_channels(chat_id,name,url) VALUES(?,?,?)",
                (str(ch["chat_id"]), ch["name"], ch["url"]),
            )
    c.commit()
    c.close()
    log.info("DB initialised at %s", DB_PATH)


# Run immediately so tables exist before any handler fires
db_init()


# -----------------------------------------------------------------------
# DB helpers
# -----------------------------------------------------------------------
def _q(sql, p=()):
    c = sqlite3.connect(DB_PATH)
    r = c.execute(sql, p).fetchone()
    c.close()
    return r

def _qa(sql, p=()):
    c = sqlite3.connect(DB_PATH)
    r = c.execute(sql, p).fetchall()
    c.close()
    return r

def _ex(sql, p=()):
    c = sqlite3.connect(DB_PATH)
    cur = c.execute(sql, p)
    c.commit()
    n = cur.rowcount
    c.close()
    return n

def db_get_user(uid):
    return _q(
        "SELECT user_id,username,first_name,credits,verified,"
        "referred_by,referral_credited,premium FROM users WHERE user_id=?",
        (uid,),
    )

def db_create_user(uid, uname, fname, ref=None):
    _ex(
        "INSERT OR IGNORE INTO users(user_id,username,first_name,credits,verified,referred_by)"
        " VALUES(?,?,?,0,0,?)",
        (uid, uname, fname, ref),
    )

def db_set_verified(uid):    _ex("UPDATE users SET verified=1 WHERE user_id=?", (uid,))
def db_add_credits(uid, n):  _ex("UPDATE users SET credits=credits+? WHERE user_id=?", (n, uid))
def db_set_credits(uid, n):  return _ex("UPDATE users SET credits=? WHERE user_id=?", (n, uid)) > 0
def db_set_premium(uid, v):  return _ex("UPDATE users SET premium=? WHERE user_id=?", (v, uid)) > 0
def db_mark_ref(uid):        _ex("UPDATE users SET referral_credited=1 WHERE user_id=?", (uid,))
def db_count_refs(uid):      return (_q("SELECT COUNT(*) FROM users WHERE referred_by=? AND referral_credited=1", (uid,)) or (0,))[0]
def db_get_all_user_ids():   return [r[0] for r in _qa("SELECT user_id FROM users WHERE verified=1")]

def db_get_setting(k, d=None):
    r = _q("SELECT value FROM settings WHERE key=?", (k,))
    return r[0] if r else d

def db_set_setting(k, v):
    _ex(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (k, v),
    )

def is_bot_enabled(): return db_get_setting("bot_enabled", "1") == "1"
def get_video():      return db_get_setting("profile_video", PROFILE_VIDEO)

def db_is_admin(uid): 
    return uid == OWNER_ID or uid == ADMIN_ID or bool(_q("SELECT 1 FROM admins WHERE user_id=?", (uid,)))

def db_add_admin(uid, by): _ex("INSERT OR IGNORE INTO admins(user_id,added_by) VALUES(?,?)", (uid, by))
def db_remove_admin(uid):  return _ex("DELETE FROM admins WHERE user_id=?", (uid,)) > 0
def db_list_admins():      return [r[0] for r in _qa("SELECT user_id FROM admins")]

def db_add_channel(cid, name, url, require_request=False):
    _ex(
        "INSERT INTO force_channels(chat_id,name,url,require_request) VALUES(?,?,?,?)"
        " ON CONFLICT(chat_id) DO UPDATE SET name=excluded.name,url=excluded.url,require_request=excluded.require_request",
        (str(cid), name, url, 1 if require_request else 0),
    )

def db_remove_channel(cid): return _ex("DELETE FROM force_channels WHERE chat_id=?", (str(cid),)) > 0
def db_list_channels():
    return [
        {"chat_id": r[0], "name": r[1], "url": r[2], "require_request": bool(r[3])}
        for r in _qa("SELECT chat_id,name,url,require_request FROM force_channels")
    ]

def db_save_join_request(user_id, chat_id):
    _ex("INSERT OR IGNORE INTO join_requests(user_id,chat_id) VALUES(?,?)", (user_id, str(chat_id)))

def db_has_join_request(user_id, chat_id):
    return bool(_q("SELECT 1 FROM join_requests WHERE user_id=? AND chat_id=?", (user_id, str(chat_id))))

def db_log_api(uid, code, api_name, success=1):
    _ex(
        "INSERT INTO api_stats(user_id,code,api_name,timestamp,success) VALUES(?,?,?,?,?)",
        (uid, code, api_name, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), success),
    )

def db_get_stats():
    return {
        "total_uses":   (_q("SELECT COUNT(*) FROM api_stats") or (0,))[0],
        "unique_codes": (_q("SELECT COUNT(DISTINCT code) FROM api_stats") or (0,))[0],
        "unique_users": (_q("SELECT COUNT(DISTINCT user_id) FROM api_stats") or (0,))[0],
        "total_users":  (_q("SELECT COUNT(*) FROM users") or (0,))[0],
        "verified":     (_q("SELECT COUNT(*) FROM users WHERE verified=1") or (0,))[0],
        "first_use":    (lambda r: r[0] if r else "N/A")(_q("SELECT timestamp FROM api_stats ORDER BY id ASC LIMIT 1")),
        "last_use":     (lambda r: r[0] if r else "N/A")(_q("SELECT timestamp FROM api_stats ORDER BY id DESC LIMIT 1")),
    }

# Gift codes
def db_create_gift(code, credits, max_uses, created_by):
    _ex(
        "INSERT OR REPLACE INTO gift_codes(code,credits,max_uses,used_count,created_by,created_at,active)"
        " VALUES(?,?,?,0,?,?,1)",
        (code.upper(), credits, max_uses, created_by, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")),
    )

def db_get_gift(code):       return _q("SELECT code,credits,max_uses,used_count,created_by,created_at,active FROM gift_codes WHERE code=?", (code.upper(),))
def db_has_claimed(code, uid): return bool(_q("SELECT 1 FROM gift_claims WHERE code=? AND user_id=?", (code.upper(), uid)))
def db_claim_gift(code, uid):
    _ex("UPDATE gift_codes SET used_count=used_count+1 WHERE code=?", (code.upper(),))
    _ex("INSERT INTO gift_claims(code,user_id,claimed_at) VALUES(?,?,?)", (code.upper(), uid, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))
def db_deactivate_gift(code): return _ex("UPDATE gift_codes SET active=0 WHERE code=?", (code.upper(),)) > 0
def db_list_gifts():          return _qa("SELECT code,credits,max_uses,used_count,active FROM gift_codes ORDER BY rowid DESC LIMIT 20")


# -----------------------------------------------------------------------
# API caller
# -----------------------------------------------------------------------
async def call_api(cfg: dict, code: str) -> dict:
    method = cfg.get("method", "GET").upper()
    style  = cfg.get("param_style", "query")
    pname  = cfg.get("param_name", "phone")
    hdrs   = cfg.get("headers") or {}
    url    = cfg["url"]

    async with httpx.AsyncClient(
        timeout=60,
        follow_redirects=True
    ) as client:
        params = None
        if style == "path":
            url = f"{url.rstrip('/')}/{code}"
        elif style == "query":
            params = {pname: code}

        try:
            if method == "GET":
                resp = await client.get(url, params=params, headers=hdrs)
            else:
                if style == "json":
                    resp = await client.post(url, json={pname: code}, headers=hdrs)
                else:
                    resp = await client.post(url, params=params, headers=hdrs)

            log.info("API %s %s → %s", method, resp.url, resp.status_code)
            
            # Check if response is successful
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    data["_status_code"] = resp.status_code
                    return data
                except Exception:
                    return {"data": resp.text, "_status_code": resp.status_code}
            else:
                return {
                    "error": f"HTTP {resp.status_code}",
                    "_status_code": resp.status_code,
                    "response": resp.text[:500]
                }
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
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🚀 USE"),          KeyboardButton("✨ PREMIUM USE")],
            [KeyboardButton("🎁 Refer & Earn"),  KeyboardButton("👤 My Profile")],
            [KeyboardButton("💎 Subscription"),  KeyboardButton("👨‍💻 Developer")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )

def join_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for c in db_list_channels():
        label = f"📨 {c['name']} (Request)" if c["require_request"] else f"📢 {c['name']}"
        rows.append([InlineKeyboardButton(label, url=c["url"])])
    rows.append([InlineKeyboardButton("✅  Verify", callback_data="verify")])
    return InlineKeyboardMarkup(rows)

def stop_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🛑  STOP USE", callback_data="stop")]])

def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Credits",   callback_data="adm_credits"),
         InlineKeyboardButton("💎 Premium",   callback_data="adm_premium")],
        [InlineKeyboardButton("📢 Channels",  callback_data="adm_channels"),
         InlineKeyboardButton("👑 Admins",    callback_data="adm_admins")],
        [InlineKeyboardButton("📊 Stats",     callback_data="adm_stats"),
         InlineKeyboardButton("⚙️ Status",    callback_data="adm_status")],
        [InlineKeyboardButton("🎬 Video",     callback_data="adm_video"),
         InlineKeyboardButton("ℹ️ User Info", callback_data="adm_userinfo")],
        [InlineKeyboardButton("🎁 Gift Codes", callback_data="adm_gifts"),
         InlineKeyboardButton("📣 Broadcast", callback_data="adm_broadcast")],
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
    "🍕 Order a pizza, might take a sec...",
    "🎩 Pulling something cool out of the hat...",
    "🚀 Houston, we have liftoff...",
    "🍿 Grab popcorn, show's starting...",
    "🦄 Unicorns working overtime for you...",
    "🎲 Rolling the dice of destiny...",
    "😴 Don't sleep — almost there...",
]

def prog_bar(done, total):
    n = int((done / total) * 12)
    return f"[{'█'*n}{'░'*(12-n)}] {int((done/total)*100)}%"

def build_progress(done_flags, spinner, tick, elapsed, round_num):
    done  = sum(done_flags)
    total = len(done_flags)
    lines = []
    for i, cfg in enumerate(API_CONFIGS):
        icon = "✅" if done_flags[i] else spinner
        lines.append(f"  {icon}  {cfg['emoji']} {cfg['name']} — {'Done' if done_flags[i] else 'Running'}")
    return (
        f"📟 *SYSTEM LOG* — Round {round_num}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{spinner} `{FAKE_LOGS[tick % len(FAKE_LOGS)]}`\n\n"
        f"{prog_bar(done, total)}\n\n"
        + "\n".join(lines) +
        f"\n\n━━━━━━━━━━━━━━━━━━━━\n"
        f"_{TIPS[(tick // 6) % len(TIPS)]}_\n"
        f"⏱️ `{elapsed}s elapsed`"
    )


# -----------------------------------------------------------------------
# Membership / join-request check
# -----------------------------------------------------------------------
async def is_member_of_all(context, user_id: int) -> bool:
    for ch in db_list_channels():
        cid = ch["chat_id"]
        if ch["require_request"]:
            # For request-based channels: check if user sent a join request
            if db_has_join_request(user_id, cid):
                continue
            # Also accept if they got approved and are now a member
            try:
                m = await context.bot.get_chat_member(cid, user_id)
                if m.status in ("member", "administrator", "creator"):
                    continue
            except Exception:
                pass
            return False
        else:
            # Normal channel: must be member
            try:
                m = await context.bot.get_chat_member(cid, user_id)
                if m.status in ("left", "kicked"):
                    return False
            except Exception as e:
                log.warning("Membership check failed %s: %s", cid, e)
                return False
    return True


def get_forward_chat(message):
    """Handle both old & new Telegram forward API."""
    fwd_origin = getattr(message, "forward_origin", None)
    if fwd_origin:
        chat = getattr(fwd_origin, "chat", None)
        if chat and getattr(chat, "type", None) == "channel":
            return chat
    fwd_chat = getattr(message, "forward_from_chat", None)
    if fwd_chat and getattr(fwd_chat, "type", None) == "channel":
        return fwd_chat
    return None


# -----------------------------------------------------------------------
# Chat join request handler — auto-saves when user sends request
# -----------------------------------------------------------------------
async def on_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    req    = update.chat_join_request
    if not req:
        return
    uid    = req.from_user.id
    cid    = req.chat.id
    db_save_join_request(uid, cid)
    log.info("Join request saved: user %s → chat %s", uid, cid)


# -----------------------------------------------------------------------
# Video helper
# -----------------------------------------------------------------------
async def send_video_msg(context, chat_id, caption, reply_markup=None, reply_to=None):
    video  = get_video()
    kwargs = dict(chat_id=chat_id, caption=caption, parse_mode=ParseMode.MARKDOWN)
    if reply_markup: kwargs["reply_markup"]        = reply_markup
    if reply_to:     kwargs["reply_to_message_id"] = reply_to

    if video and video != "NONE":
        try:
            await context.bot.send_video(video=video, **kwargs)
            return
        except Exception as e:
            log.warning("Video send failed: %s", e)

    text_kw = dict(chat_id=chat_id, text=caption, parse_mode=ParseMode.MARKDOWN)
    if reply_markup: text_kw["reply_markup"] = reply_markup
    await context.bot.send_message(**text_kw)


# -----------------------------------------------------------------------
# /start
# -----------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    referred_by = None
    if context.args and context.args[0].startswith("ref_"):
        try:
            c = int(context.args[0].replace("ref_", ""))
            if c != user.id:
                referred_by = c
        except ValueError:
            pass

    if not db_get_user(user.id):
        db_create_user(user.id, user.username or "", user.first_name or "", referred_by)

    row      = db_get_user(user.id)
    verified = row[4]
    channels = db_list_channels()

    if verified:
        await send_video_msg(
            context, update.effective_chat.id,
            f"👋 *Welcome back, {user.first_name}!*\n\nChoose an option below 👇",
            main_keyboard(),
        )
        return

    if not channels:
        db_set_verified(user.id)
        db_add_credits(user.id, CREDITS_ON_SIGNUP)
        await send_video_msg(
            context, update.effective_chat.id,
            f"✅ *Welcome, {user.first_name}!*\n\n"
            f"🎁 You received *{CREDITS_ON_SIGNUP} free credits*!\n\n"
            "Choose an option below 👇",
            main_keyboard(),
        )
        return

    await update.message.reply_text(
        "🔐 *Access Restricted*\n\n"
        "Please join/request the channel(s) below, then tap *Verify* ✅\n\n"
        "📨 *Request channels:* tap the button → send join request → come back & verify\n"
        "📢 *Normal channels:* just join → verify",
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
        await query.answer(
            "❌ Not verified yet!\n\n"
            "For normal channels: join first.\n"
            "For request channels: send a join request first.",
            show_alert=True,
        )
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
            await context.bot.send_message(
                OWNER_ID,
                f"🆕 *New user verified!*\n\n"
                f"👤 {user.first_name}\n🔗 @{user.username or 'N/A'}\n🆔 `{user.id}`",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass

    try:
        await query.message.delete()
    except Exception:
        pass

    await send_video_msg(
        context, update.effective_chat.id,
        f"✅ *Verified! Welcome, {user.first_name}* 🎉\n\n"
        f"🎁 *{CREDITS_ON_SIGNUP} free credits* added!\n\n"
        "Choose an option below 👇",
        main_keyboard(),
    )


# -----------------------------------------------------------------------
# Message router
# -----------------------------------------------------------------------
MENU_BUTTONS = {
    "🚀 USE", "✨ PREMIUM USE", "🎁 Refer & Earn",
    "👤 My Profile", "💎 Subscription", "👨‍💻 Developer",
}

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return

    user = update.effective_user
    msg  = update.message
    text = (msg.text or "").strip()

    # Admin: awaiting channel forward
    if context.user_data.get("awaiting_channel_forward"):
        fwd = get_forward_chat(msg)
        if fwd:
            context.user_data["awaiting_channel_forward"] = False
            await _try_add_channel(update, context, fwd.id,
                                   require_request=context.user_data.pop("channel_require_request", False))
        else:
            await msg.reply_text(
                "⚠️ Couldn't detect the channel.\n\n"
                "Please *forward a message directly* from the channel "
                "(tap & hold a message → Forward → select this chat).",
                parse_mode=ParseMode.MARKDOWN,
            )
        return

    # Admin: awaiting video
    if context.user_data.get("awaiting_video") and msg.video:
        context.user_data["awaiting_video"] = False
        db_set_setting("profile_video", msg.video.file_id)
        await msg.reply_text("✅ *Dashboard video updated!*", parse_mode=ParseMode.MARKDOWN)
        return

    # Menu button cancels code flow
    if context.user_data.get("awaiting_code") and text in MENU_BUTTONS:
        context.user_data["awaiting_code"] = False

    # 10-digit code
    if context.user_data.get("awaiting_code"):
        if not text.isdigit() or len(text) != 10:
            await msg.reply_text(
                "❌ *Invalid Code*\n\nMust be exactly *10 digits*. Try again 🔁",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        context.user_data["awaiting_code"] = False
        row     = db_get_user(user.id)
        premium = row[7] if row else 0
        status_msg = await msg.reply_text(
            build_progress([False] * len(API_CONFIGS), SPINNER[0], 0, 0, 1),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=stop_keyboard(),
        )
        task = asyncio.create_task(
            run_all_apis(text, update, context, status_msg, user.id, premium)
        )
        context.user_data["running_task"] = task
        return

    # Menu routing
    if text == "🚀 USE":
        await handle_use(update, context)
    elif text == "✨ PREMIUM USE":
        await msg.reply_text(
            "✨ *Premium Use — Coming Soon!*\n\nSomething special is cooking 🔥 Stay tuned!",
            parse_mode=ParseMode.MARKDOWN,
        )
    elif text == "🎁 Refer & Earn":
        await handle_refer(update, context)
    elif text == "👤 My Profile":
        await handle_profile(update, context)
    elif text == "💎 Subscription":
        await msg.reply_text(
            f"💎 *Subscription*\n\nWant unlimited access?\n\n📩 Contact {SUBSCRIPTION_CONTACT}!",
            parse_mode=ParseMode.MARKDOWN,
        )
    elif text == "👨‍💻 Developer":
        await msg.reply_text(
            f"👨‍💻 *Developer*\n\nSupport / bugs / business 👉 {DEVELOPER_CONTACT} 🚀",
            parse_mode=ParseMode.MARKDOWN,
        )


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
            "Not enough credits to use this feature.\n\n"
            "🎁 Earn via *Refer & Earn* or 💎 buy a *Subscription*!",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    context.user_data["awaiting_code"] = True
    await update.message.reply_text(
        "🔑 *Send Me the Code*\n\nPlease send your *10-digit code* to continue 👇",
        parse_mode=ParseMode.MARKDOWN,
    )


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
        parse_mode=ParseMode.MARKDOWN,
    )


async def handle_profile(update, context):
    user = update.effective_user
    row  = db_get_user(user.id)
    if not row:
        await update.message.reply_text("❌ Profile not found. Please /start again.")
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
    await send_video_msg(
        context, update.effective_chat.id, caption,
        reply_to=update.message.message_id,
    )


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
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass
    else:
        await query.answer("Nothing running right now.", show_alert=True)


# -----------------------------------------------------------------------
# API runner — continuous loop
# -----------------------------------------------------------------------
def format_result(cfg, response) -> str:
    emoji = cfg["emoji"]
    name  = html.escape(cfg["name"])
    if not isinstance(response, dict):
        return f"{emoji} <b>{name}</b>\n<pre>{html.escape(str(response))}</pre>"
    sc     = response.pop("_status_code", None)
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
                except Exception:
                    pass
                await asyncio.sleep(TICK)
                tick += 1

            results = []
            for i, t in enumerate(tasks):
                try:
                    r = t.result()
                    db_log_api(user_id, code, API_CONFIGS[i]["name"], 1)
                    results.append(r)
                except Exception as e:
                    db_log_api(user_id, code, API_CONFIGS[i]["name"], 0)
                    results.append({"error": str(e)})

            try:
                await status_msg.edit_text(
                    build_progress([True] * len(API_CONFIGS), "✅", tick, int(tick * TICK), round_num),
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass

            formatted = [format_result(API_CONFIGS[i], results[i]) for i in range(len(results))]
            blocks    = f"🔄 <b>Round {round_num} — Results</b>\n\n" + "\n\n".join(formatted)
            db_add_credits(user_id, -CREDITS_PER_USE if not premium else 0)

            if result_msg is None:
                result_msg = await update.message.reply_text(
                    blocks, parse_mode=ParseMode.HTML, reply_markup=stop_keyboard()
                )
            else:
                try:
                    await result_msg.edit_text(
                        blocks, parse_mode=ParseMode.HTML, reply_markup=stop_keyboard()
                    )
                except Exception:
                    pass

            try:
                await status_msg.edit_text(
                    build_progress([False] * len(API_CONFIGS), SPINNER[0], 0, 0, round_num + 1),
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=stop_keyboard(),
                )
            except Exception:
                pass

            await asyncio.sleep(1.5)

    except asyncio.CancelledError:
        for t in tasks if "tasks" in dir() else []:
            if not t.done():
                t.cancel()
        raise


# -----------------------------------------------------------------------
# Decorators
# -----------------------------------------------------------------------
def admin_only(func):
    async def wrapper(update, context):
        if not db_is_admin(update.effective_user.id):
            await update.message.reply_text("🚫 Admins only.")
            return
        return await func(update, context)
    return wrapper

def owner_only(func):
    async def wrapper(update, context):
        if update.effective_user.id != OWNER_ID:
            await update.message.reply_text("🚫 Owner only.")
            return
        return await func(update, context)
    return wrapper


# -----------------------------------------------------------------------
# Admin commands
# -----------------------------------------------------------------------
@admin_only
async def admin_panel(update, context):
    await update.message.reply_text(
        "🛠️ *Admin Panel*\n\nChoose a category 👇",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=admin_keyboard(),
    )

@admin_only
async def admin_addcredit(update, context):
    try: tid, amt = int(context.args[0]), int(context.args[1])
    except: await update.message.reply_text("⚠️ `/addcredit <uid> <amount>`", parse_mode=ParseMode.MARKDOWN); return
    if not db_get_user(tid): await update.message.reply_text("❌ User not found."); return
    db_add_credits(tid, amt)
    await update.message.reply_text(f"✅ Added *{amt}* credits to `{tid}`.", parse_mode=ParseMode.MARKDOWN)

@admin_only
async def admin_removecredit(update, context):
    try: tid, amt = int(context.args[0]), int(context.args[1])
    except: await update.message.reply_text("⚠️ `/removecredit <uid> <amount>`", parse_mode=ParseMode.MARKDOWN); return
    if not db_get_user(tid): await update.message.reply_text("❌ User not found."); return
    db_add_credits(tid, -amt)
    await update.message.reply_text(f"✅ Removed *{amt}* credits from `{tid}`.", parse_mode=ParseMode.MARKDOWN)

@admin_only
async def admin_setcredit(update, context):
    try: tid, amt = int(context.args[0]), int(context.args[1])
    except: await update.message.reply_text("⚠️ `/setcredit <uid> <amount>`", parse_mode=ParseMode.MARKDOWN); return
    if not db_set_credits(tid, amt): await update.message.reply_text("❌ User not found."); return
    await update.message.reply_text(f"✅ Set `{tid}` credits to *{amt}*.", parse_mode=ParseMode.MARKDOWN)

@admin_only
async def admin_addpremium(update, context):
    try: tid = int(context.args[0])
    except: await update.message.reply_text("⚠️ `/addpremium <uid>`", parse_mode=ParseMode.MARKDOWN); return
    if not db_set_premium(tid, 1): await update.message.reply_text("❌ User not found."); return
    await update.message.reply_text(f"💎 `{tid}` is now *PREMIUM*.", parse_mode=ParseMode.MARKDOWN)
    try: await context.bot.send_message(tid, "💎 *You're now PREMIUM!* Unlimited USE — no credits needed! 🚀", parse_mode=ParseMode.MARKDOWN)
    except: pass

@admin_only
async def admin_removepremium(update, context):
    try: tid = int(context.args[0])
    except: await update.message.reply_text("⚠️ `/removepremium <uid>`", parse_mode=ParseMode.MARKDOWN); return
    if not db_set_premium(tid, 0): await update.message.reply_text("❌ User not found."); return
    await update.message.reply_text(f"✅ Premium removed from `{tid}`.", parse_mode=ParseMode.MARKDOWN)

@admin_only
async def admin_userinfo(update, context):
    try: tid = int(context.args[0])
    except: await update.message.reply_text("⚠️ `/userinfo <uid>`", parse_mode=ParseMode.MARKDOWN); return
    row = db_get_user(tid)
    if not row: await update.message.reply_text("❌ User not found."); return
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
async def admin_offbot(update, context):
    db_set_setting("bot_enabled", "0")
    await update.message.reply_text("🔴 *Bot is now OFF.*", parse_mode=ParseMode.MARKDOWN)

@admin_only
async def admin_onbot(update, context):
    db_set_setting("bot_enabled", "1")
    await update.message.reply_text("🟢 *Bot is now ON.*", parse_mode=ParseMode.MARKDOWN)

@admin_only
async def admin_listadmins(update, context):
    ids   = db_list_admins()
    lines = [f"👑 `{OWNER_ID}` (owner)", f"🛠️ `{ADMIN_ID}` (admin)"] + [f"🛠️ `{i}`" for i in ids]
    await update.message.reply_text("📋 *Admins*\n\n" + "\n".join(lines), parse_mode=ParseMode.MARKDOWN)

@owner_only
async def admin_addadmin(update, context):
    try: tid = int(context.args[0])
    except: await update.message.reply_text("⚠️ `/addadmin <uid>`", parse_mode=ParseMode.MARKDOWN); return
    db_add_admin(tid, update.effective_user.id)
    await update.message.reply_text(f"✅ `{tid}` is now an admin.", parse_mode=ParseMode.MARKDOWN)
    try: await context.bot.send_message(tid, "🛠️ *You've been granted admin access!* Send /admin.", parse_mode=ParseMode.MARKDOWN)
    except: pass

@owner_only
async def admin_removeadmin(update, context):
    try: tid = int(context.args[0])
    except: await update.message.reply_text("⚠️ `/removeadmin <uid>`", parse_mode=ParseMode.MARKDOWN); return
    if tid == OWNER_ID or tid == ADMIN_ID: 
        await update.message.reply_text("🚫 Can't remove owner or hardcoded admin.")
        return
    if not db_remove_admin(tid): await update.message.reply_text("❌ Not an admin."); return
    await update.message.reply_text(f"✅ `{tid}` removed.", parse_mode=ParseMode.MARKDOWN)

@admin_only
async def admin_addchannel(update, context):
    """
    /addchannel @username          → normal join channel
    /addchannel @username request  → join-request channel
    /addchannel request            → private join-request (forward a post)
    /addchannel                    → private normal (forward a post)
    """
    args = context.args
    require_request = "request" in [a.lower() for a in args]
    usernames = [a for a in args if not a.lower() == "request"]

    if not usernames:
        # Private channel — ask for forward
        context.user_data["awaiting_channel_forward"] = True
        context.user_data["channel_require_request"]  = require_request
        mode = "join-request" if require_request else "normal"
        await update.message.reply_text(
            f"📢 *Add Private Channel* ({mode} mode)\n\n"
            "1️⃣ Make bot *admin* in that channel\n"
            "2️⃣ Open the channel\n"
            "3️⃣ Tap any message → *Forward* → select this chat\n\n"
            "⚠️ Bot must be admin in channel first!",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    raw      = usernames[0]
    username = raw.replace("https://t.me/", "").replace("http://t.me/", "").lstrip("@").strip()
    if username.startswith("+") or "joinchat" in username:
        context.user_data["awaiting_channel_forward"] = True
        context.user_data["channel_require_request"]  = require_request
        await update.message.reply_text(
            "⚠️ That's a private link — forward a post from the channel instead 👇",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await _try_add_channel(update, context, f"@{username}", require_request=require_request)

@admin_only
async def admin_removechannel(update, context):
    try: cid = context.args[0]
    except: await update.message.reply_text("⚠️ `/removechannel <chat_id>`", parse_mode=ParseMode.MARKDOWN); return
    if not db_remove_channel(cid): await update.message.reply_text("❌ Channel not found."); return
    await update.message.reply_text("✅ Channel removed.")

@admin_only
async def admin_listchannels(update, context):
    chs = db_list_channels()
    if not chs: await update.message.reply_text("📭 No channels. Use /addchannel."); return
    lines = [f"{'📨' if c['require_request'] else '📢'} *{c['name']}*\nID: `{c['chat_id']}`" for c in chs]
    await update.message.reply_text("📋 *Force-Join Channels*\n\n" + "\n\n".join(lines), parse_mode=ParseMode.MARKDOWN)

@admin_only
async def admin_setvideo(update, context):
    if update.message.reply_to_message and update.message.reply_to_message.video:
        file_id = update.message.reply_to_message.video.file_id
    elif context.args:
        file_id = context.args[0]
    else:
        context.user_data["awaiting_video"] = True
        await update.message.reply_text("🎬 Send the video now 👇"); return
    db_set_setting("profile_video", file_id)
    await update.message.reply_text("✅ *Video updated!*", parse_mode=ParseMode.MARKDOWN)
    try: await context.bot.send_video(update.effective_chat.id, file_id, caption="Preview 👆")
    except Exception as e: await update.message.reply_text(f"⚠️ Set but preview failed: {e}")

@admin_only
async def admin_clearvideo(update, context):
    db_set_setting("profile_video", "NONE")
    await update.message.reply_text("✅ Video removed.")

@admin_only
async def admin_apitest(update, context):
    test_code = context.args[0] if context.args else "1234567890"
    msg = await update.message.reply_text(f"🧪 Testing with `{test_code}`...", parse_mode=ParseMode.MARKDOWN)
    lines = []
    for cfg in API_CONFIGS:
        style = cfg.get("param_style", "query")
        pname = cfg.get("param_name", "phone")
        url   = cfg["url"]
        if style == "path":   preview = f"{url.rstrip('/')}/{test_code}"
        elif style == "query": preview = f"{url}?{pname}={test_code}"
        else:                  preview = url
        result = await call_api(cfg, test_code)
        pretty = json.dumps(result, indent=2, ensure_ascii=False)
        lines.append(
            f"{cfg['emoji']} <b>{html.escape(cfg['name'])}</b>\n"
            f"🔗 <code>{html.escape(preview)}</code>\n"
            f"<pre>{html.escape(pretty[:400])}</pre>"
        )
    await msg.edit_text("🧪 <b>API Test Results</b>\n\n" + "\n\n".join(lines), parse_mode=ParseMode.HTML)

@admin_only
async def admin_stats(update, context):
    s = db_get_stats()
    await update.message.reply_text(
        "📊 *Bot Statistics*\n\n"
        f"👥 Total users: *{s['total_users']}*\n"
        f"✅ Verified: *{s['verified']}*\n\n"
        f"🚀 Total API uses: *{s['total_uses']}*\n"
        f"👤 Unique users: *{s['unique_users']}*\n"
        f"🔢 Unique codes: *{s['unique_codes']}*\n\n"
        f"🕐 First use: `{s['first_use']}`\n"
        f"🕐 Last use: `{s['last_use']}`",
        parse_mode=ParseMode.MARKDOWN,
    )

@admin_only
async def admin_giftcode(update, context):
    args = context.args or []
    rq   = "request" in [a.lower() for a in args]
    nums = [a for a in args if not a.lower() == "request"]
    if not nums:
        code     = "GIFT-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        credits  = 2
        max_uses = 1
    elif len(nums) == 1:
        code = nums[0]; credits = 2; max_uses = 1
    elif len(nums) == 2:
        code = nums[0]
        try: credits = int(nums[1]); max_uses = 1
        except: await update.message.reply_text("⚠️ Credits must be a number."); return
    else:
        code = nums[0]
        try: credits = int(nums[1]); max_uses = int(nums[2])
        except: await update.message.reply_text("⚠️ `/giftcode <CODE> <credits> <max_uses>`", parse_mode=ParseMode.MARKDOWN); return
    db_create_gift(code, credits, max_uses, update.effective_user.id)
    await update.message.reply_text(
        f"🎁 *Gift Code Created!*\n\n"
        f"🔑 Code: `{code.upper()}`\n"
        f"💰 Credits: *{credits}*\n"
        f"👥 Max uses: *{max_uses}*\n\n"
        f"Users redeem with: `/redeem {code.upper()}`",
        parse_mode=ParseMode.MARKDOWN,
    )

@admin_only
async def admin_deletegift(update, context):
    if not context.args: await update.message.reply_text("⚠️ `/deletegift <CODE>`", parse_mode=ParseMode.MARKDOWN); return
    code = context.args[0]
    if db_deactivate_gift(code): await update.message.reply_text(f"✅ `{code.upper()}` deactivated.", parse_mode=ParseMode.MARKDOWN)
    else: await update.message.reply_text("❌ Code not found.")

@admin_only
async def admin_listgifts(update, context):
    gifts = db_list_gifts()
    if not gifts: await update.message.reply_text("📭 No gift codes. Use /giftcode."); return
    lines = [f"{'✅' if g[4] else '❌'} `{g[0]}` — {g[1]}cr | {g[3]}/{g[2]} used" for g in gifts]
    await update.message.reply_text("🎁 *Gift Codes*\n\n" + "\n".join(lines), parse_mode=ParseMode.MARKDOWN)

@admin_only
async def admin_broadcast(update, context):
    if update.message.reply_to_message:
        reply_msg   = update.message.reply_to_message
        use_forward = True
    elif context.args:
        broadcast_text = " ".join(context.args)
        use_forward    = False
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
        await update.message.reply_text("📭 No verified users."); return

    status = await update.message.reply_text(
        f"📣 Broadcasting to *{total}* users...\n\n[{'░'*10}] 0%",
        parse_mode=ParseMode.MARKDOWN,
    )
    sent = failed = 0
    for i, uid in enumerate(user_ids):
        try:
            if use_forward: await reply_msg.forward(uid)
            else: await context.bot.send_message(uid, f"📢 *Message from Admin*\n\n{broadcast_text}", parse_mode=ParseMode.MARKDOWN)
            sent += 1
        except Exception: failed += 1
        if (i + 1) % 10 == 0 or (i + 1) == total:
            pct = int(((i + 1) / total) * 100)
            bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
            try:
                await status.edit_text(
                    f"📣 *Broadcasting...*\n\n[{bar}] {pct}%\n\n✅ {sent} | ❌ {failed}",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception: pass
        await asyncio.sleep(0.05)

    await status.edit_text(
        f"✅ *Broadcast Complete!*\n\n📤 Total: *{total}*\n✅ Sent: *{sent}*\n❌ Failed: *{failed}*",
        parse_mode=ParseMode.MARKDOWN,
    )

async def cmd_redeem(update, context):
    user = update.effective_user
    row  = db_get_user(user.id)
    if not row or not row[4]:
        await update.message.reply_text("❌ Please /start and verify first.", parse_mode=ParseMode.MARKDOWN); return
    if not context.args:
        await update.message.reply_text("🎁 Usage: `/redeem YOUR_CODE`", parse_mode=ParseMode.MARKDOWN); return
    code = context.args[0].upper()
    gift = db_get_gift(code)
    if not gift: await update.message.reply_text("❌ *Invalid code.*", parse_mode=ParseMode.MARKDOWN); return
    _, credits, max_uses, used_count, _, _, active = gift
    if not active: await update.message.reply_text("❌ *This code has been deactivated.*", parse_mode=ParseMode.MARKDOWN); return
    if used_count >= max_uses: await update.message.reply_text("❌ *This code has reached its usage limit.*", parse_mode=ParseMode.MARKDOWN); return
    if db_has_claimed(code, user.id): await update.message.reply_text("❌ *You already redeemed this code.*", parse_mode=ParseMode.MARKDOWN); return
    db_claim_gift(code, user.id)
    db_add_credits(user.id, credits)
    new_credits = (db_get_user(user.id) or (0, 0, 0, 0))[3]
    await update.message.reply_text(
        f"🎉 *Code Redeemed!*\n\n"
        f"🎁 Code: `{code}`\n"
        f"💰 Credits added: *+{credits}*\n"
        f"💳 Total credits: *{new_credits}*\n\nEnjoy! 🚀",
        parse_mode=ParseMode.MARKDOWN,
    )


async def _try_add_channel(update, context, chat_ref, require_request=False):
    try:
        chat = await context.bot.get_chat(chat_ref)
    except Exception as e:
        await update.message.reply_text(f"❌ Couldn't find channel.\n`{e}`", parse_mode=ParseMode.MARKDOWN); return
    try:
        bm = await context.bot.get_chat_member(chat.id, context.bot.id)
        if bm.status not in ("administrator", "creator"):
            await update.message.reply_text(
                f"⚠️ Bot is *not admin* in *{html.escape(chat.title)}*. Make it admin first.",
                parse_mode=ParseMode.MARKDOWN,
            ); return
    except Exception as e:
        await update.message.reply_text(f"❌ Can't verify admin status.\n`{e}`", parse_mode=ParseMode.MARKDOWN); return

    url = f"https://t.me/{chat.username}" if chat.username else (chat.invite_link or "")
    if not url:
        try: url = await context.bot.export_chat_invite_link(chat.id)
        except Exception: url = ""

    db_add_channel(chat.id, chat.title, url, require_request=require_request)
    mode = "📨 join-request" if require_request else "📢 normal"
    await update.message.reply_text(
        f"✅ *{html.escape(chat.title)}* added! ({mode} mode) 🎉",
        parse_mode=ParseMode.MARKDOWN,
    )


# -----------------------------------------------------------------------
# Admin panel inline callbacks
# -----------------------------------------------------------------------
async def on_admin_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not db_is_admin(query.from_user.id):
        await query.answer("🚫 Admins only.", show_alert=True); return
    await query.answer()
    data     = query.data
    is_owner = query.from_user.id == OWNER_ID

    if data == "adm_home":
        await query.edit_message_text("🛠️ *Admin Panel*\n\nChoose a category 👇", parse_mode=ParseMode.MARKDOWN, reply_markup=admin_keyboard())

    elif data == "adm_credits":
        await query.edit_message_text(
            "💰 *Credit Commands*\n\n`/addcredit <uid> <amount>`\n`/removecredit <uid> <amount>`\n`/setcredit <uid> <amount>`",
            parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back())

    elif data == "adm_premium":
        await query.edit_message_text(
            "💎 *Premium Commands*\n\n`/addpremium <uid>`\n`/removepremium <uid>`",
            parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back())

    elif data == "adm_channels":
        chs     = db_list_channels()
        ch_text = "\n".join([
            f"{'📨' if c['require_request'] else '📢'} {c['name']} — `{c['chat_id']}`" for c in chs
        ]) if chs else "_(none yet)_"
        await query.edit_message_text(
            f"📢 *Channels*\n\n"
            f"`/addchannel @user` — normal join\n"
            f"`/addchannel @user request` — join-request channel\n"
            f"`/addchannel` — private normal (forward post)\n"
            f"`/addchannel request` — private join-request (forward post)\n"
            f"`/removechannel <id>`\n\n"
            f"*Current:*\n{ch_text}",
            parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back())

    elif data == "adm_admins":
        ids   = db_list_admins()
        lines = [f"👑 `{OWNER_ID}` (owner)", f"🛠️ `{ADMIN_ID}` (admin)"] + [f"🛠️ `{i}`" for i in ids]
        text  = "👑 *Admins*\n\n" + "\n".join(lines)
        if is_owner: text += "\n\n`/addadmin <uid>` · `/removeadmin <uid>`"
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back())

    elif data == "adm_stats":
        s = db_get_stats()
        await query.edit_message_text(
            "📊 *Statistics*\n\n"
            f"👥 Total: *{s['total_users']}* | ✅ Verified: *{s['verified']}*\n\n"
            f"🚀 API uses: *{s['total_uses']}*\n"
            f"👤 Unique users: *{s['unique_users']}*\n"
            f"🔢 Unique codes: *{s['unique_codes']}*\n\n"
            f"🕐 First: `{s['first_use']}`\n"
            f"🕐 Last: `{s['last_use']}`",
            parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back())

    elif data in ("adm_status", "adm_toggle"):
        if data == "adm_toggle":
            db_set_setting("bot_enabled", "0" if is_bot_enabled() else "1")
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
        vs  = f"`{vid[:50]}...`" if vid and vid != "NONE" else "_(not set)_"
        await query.edit_message_text(
            f"🎬 *Dashboard Video*\n\nCurrent: {vs}\n\n`/setvideo` — upload video\n`/clearvideo` — remove",
            parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back())

    elif data == "adm_userinfo":
        await query.edit_message_text("ℹ️ *User Info*\n\n`/userinfo <user_id>`", parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back())

    elif data == "adm_gifts":
        gifts = db_list_gifts()
        gt = "\n".join([f"{'✅' if g[4] else '❌'} `{g[0]}` — {g[1]}cr | {g[3]}/{g[2]}" for g in gifts[:8]]) if gifts else "_(none yet)_"
        await query.edit_message_text(
            f"🎁 *Gift Codes*\n\n"
            f"`/giftcode <CODE> <credits> <uses>`\n"
            f"`/giftcode WELCOME 5 100`\n"
            f"`/deletegift <CODE>`\n`/listgifts`\n\n*Recent:*\n{gt}",
            parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back())

    elif data == "adm_broadcast":
        await query.edit_message_text(
            "📣 *Broadcast*\n\n"
            "Reply to any message with `/broadcast` — forwards to all verified users.\n\n"
            "Or: `/broadcast Your text here`",
            parse_mode=ParseMode.MARKDOWN, reply_markup=admin_back())


# -----------------------------------------------------------------------
# Maintenance gate
# -----------------------------------------------------------------------
async def maintenance_gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    if db_is_admin(user.id):
        return
    if not is_bot_enabled():
        if update.callback_query:
            await update.callback_query.answer("🔴 Bot is currently OFF!", show_alert=True)
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
    db_init()  # double-safety: ensure tables exist
    log.info("DB path: %s", DB_PATH)

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(TypeHandler(Update, maintenance_gate), group=-1)

    app.add_handler(ChatJoinRequestHandler(on_join_request))

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
    app.add_handler(CommandHandler("giftcode",      admin_giftcode))
    app.add_handler(CommandHandler("deletegift",    admin_deletegift))
    app.add_handler(CommandHandler("listgifts",     admin_listgifts))
    app.add_handler(CommandHandler("broadcast",     admin_broadcast))
    app.add_handler(CommandHandler("redeem",        cmd_redeem))

    app.add_handler(CallbackQueryHandler(on_verify,   pattern="^verify$"))
    app.add_handler(CallbackQueryHandler(on_stop,     pattern="^stop$"))
    app.add_handler(CallbackQueryHandler(on_admin_cb, pattern="^adm_"))

    app.add_handler(MessageHandler(
        (filters.TEXT | filters.VIDEO) & ~filters.COMMAND,
        on_message,
    ))

    log.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
