import os
import logging
import requests
import textwrap
from flask import Flask, request, jsonify

# --- Logging ---
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("tira-bot")

# --- Environment / configuration ---
# Accept either BOT_TOKEN or TELEGRAM_TOKEN (backwards-compatible)
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
if not BOT_TOKEN:
    log.error("Missing BOT_TOKEN / TELEGRAM_TOKEN environment variable.")
    raise RuntimeError("Missing BOT_TOKEN / TELEGRAM_TOKEN environment variable.")

# Optional environment-provided IDs. If not provided, group/admin are discovered dynamically.
GROUP_CHAT_ID_ENV = os.getenv("GROUP_CHAT_ID")  # e.g. -1234567890123
ADMIN_ID_ENV = os.getenv("ADMIN_ID")  # e.g. 123456789

group_chat_id = int(GROUP_CHAT_ID_ENV) if GROUP_CHAT_ID_ENV else None
admin_id = int(ADMIN_ID_ENV) if ADMIN_ID_ENV else None

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# --- Categories (Hebrew) including the new ירקניה ---
CATEGORIES = [
    "חומוס",
    "שווארמה",
    "מאפיה",
    "מכולת",
    "בשר",
    "דגים",
    "משתלה",
    "ירקניה",
]

# In-memory storage for current session
session_active = False
orders = []  # list of dicts: {user_id, username, text, category, message_id}

# Flask app
app = Flask(__name__)

# --- Helper: send message to Telegram via HTTP API (synchronous) ---
def send_message(chat_id, text, reply_to_message_id=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
    try:
        r = requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload, timeout=10)
        if not r.ok:
            log.warning("Telegram sendMessage failed: %s %s", r.status_code, r.text)
        return r.json()
    except Exception as e:
        log.exception("Failed to send message to Telegram: %s", e)
        return None

# --- Lightweight Hebrew keyword classifier (no external API) ---
def normalize_text(t: str) -> str:
    return t.replace("\n", " ").strip().lower()

def classify_text(text: str) -> str:
    t = normalize_text(text)
    # order matters a bit—more specific first
    if any(x in t for x in ["חומוס", "חומוסיה"]):
        return "חומוס"
    if any(x in t for x in ["שווארמה", "שוואר", "shawarma"]):
        return "שווארמה"
    if any(x in t for x in ["מאפיה", "מאפה", "לחם", "קרואסון", "בייגל"]):
        return "מאפיה"
    if any(x in t for x in ["מכולת", "סופר", "סופרמרקט", "חלב", "סוכר", "קמח", "חמאה"]):
        return "מכולת"
    if any(x in t for x in ["בשר", "קבב", "סטייק", "פרגית", "נתח"]):
        return "בשר"
    if any(x in t for x in ["דג", "סלמון", "טונה", "פילה", "דגים"]):
        return "דגים"
    if any(x in t for x in ["משתלה", "עציץ", "צמח", "שתיל"]):
        return "משתלה"
    if any(x in t for x in ["ירק", "ירקניה", "שוק ירקות", "ירקות", "פירות"]):
        return "ירקניה"
    # fallback
    return "מכולת"

# --- Helper: build summary text group-by-category ---
def build_summary_text():
    grouped = []
    for cat in CATEGORIES:
        items = [f"- {o['username']}: {o['text']}" for o in orders if o["category"] == cat]
        if items:
            grouped.append(f"{cat}:\n" + "\n".join(items))
    if not grouped:
        return "אין הזמנות כרגע."
    return "סיכום הזמנות:\n\n" + "\n\n".join(grouped)

# --- Webhook route for Telegram ---
@app.route("/webhook", methods=["POST"])
def webhook():
    global session_active, orders, group_chat_id, admin_id

    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"ok": False, "error": "no json"}), 400

    # We only handle standard 'message' updates
    message = data.get("message") or data.get("edited_message")
    if not message:
        return jsonify({"ok": True})

    chat = message.get("chat", {})
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()
    user = message.get("from", {})
    user_id = user.get("id")
    username = user.get("username") or user.get("first_name") or "משתמש"

    log.info("Received message from %s (%s) in chat %s: %s", username, user_id, chat_id, text[:120])

    # If /start command in the intended group: begin session
    if text.startswith("/start"):
        # If group not configured, accept this chat as the group
        if group_chat_id is None:
            group_chat_id = chat_id
            log.info("GROUP_CHAT_ID was not set; now set to %s", group_chat_id)
        if chat_id != group_chat_id:
            send_message(chat_id, "פקודה זו מותרת רק בקבוצה הראשית.")
            return jsonify({"ok": True})

        session_active = True
        orders = []
        # If admin not configured, set the command sender as admin
        if admin_id is None:
            admin_id = user_id
            send_message(admin_id, f"אתה הוגדרת כמנהל הזמנות (id={admin_id}).")
            log.info("ADMIN_ID was not set; now set to %s", admin_id)

        send_message(group_chat_id, "מישהו משהו מטירה? כתבו כאן מה אתם רוצים, ואז מנהל יכול לבקש /summary כדי לקבל סיכום.")
        return jsonify({"ok": True})

    # /summary - allowed only for admin (can be sent from private chat or group)
    if text.startswith("/summary"):
        if admin_id is None or user_id != admin_id:
            send_message(chat_id, "אינך מורשה לבקש סיכום.")
            return jsonify({"ok": True})
        summary = build_summary_text()
        # Telegram limit ~4096 - split into chunks of 3500 to be safe
        max_chunk = 3500
        for i in range(0, len(summary), max_chunk):
            send_message(admin_id, summary[i : i + max_chunk])
        return jsonify({"ok": True})

    # /reset - admin only
    if text.startswith("/reset"):
        if admin_id is None or user_id != admin_id:
            send_message(chat_id, "אינך מורשה לבצע איפוס.")
            return jsonify({"ok": True})
        orders = []
        session_active = False
        send_message(chat_id, "מאגר ההזמנות אופס.")
        return jsonify({"ok": True})

    # Only accept ordinary messages when a session is active and it's in the group
    if session_active and chat_id == group_chat_id:
        # ignore other bot commands
        if text.startswith("/"):
            return jsonify({"ok": True})

        category = classify_text(text)
        orders.append(
            {
                "user_id": user_id,
                "username": username,
                "text": text,
                "category": category,
                "message_id": message.get("message_id"),
            }
        )

        # reply with the category (as a reply to their message)
        send_message(chat_id, category, reply_to_message_id=message.get("message_id"))
        return jsonify({"ok": True})

    # otherwise, ignore (no error)
    return jsonify({"ok": True})

# simple health-check root
@app.route("/", methods=["GET"])
def index():
    status = {
        "ok": True,
        "group_chat_id": group_chat_id,
        "admin_id": admin_id,
        "session_active": session_active,
        "orders_count": len(orders),
    }
    return jsonify(status)

# End of file
