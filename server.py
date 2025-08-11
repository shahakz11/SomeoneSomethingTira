import os
import logging
import requests
from flask import Flask, request, jsonify

# --- Logging ---
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("tira-bot")

# --- Environment / config ---
BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
if not BOT_TOKEN:
    log.error("Missing BOT_TOKEN / TELEGRAM_TOKEN environment variable.")
    raise RuntimeError("Missing BOT_TOKEN / TELEGRAM_TOKEN environment variable.")

GROUP_CHAT_ID_ENV = os.getenv("GROUP_CHAT_ID")  # e.g. -1001234567890
ADMIN_ID_ENV = os.getenv("ADMIN_ID")            # e.g. 123456789

group_chat_id = int(GROUP_CHAT_ID_ENV) if GROUP_CHAT_ID_ENV else None
admin_id = int(ADMIN_ID_ENV) if ADMIN_ID_ENV else None

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# --- Categories (Hebrew) including ירקניה ---
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

# --- In-memory session storage ---
session_active = False
orders = []  # {user_id, username, text, category, message_id}

app = Flask(__name__)

# --- Helper to send message via Telegram HTTP API ---
def send_message(chat_id, text, reply_to_message_id=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
    try:
        r = requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload, timeout=10)
        if not r.ok:
            log.warning("Telegram sendMessage failed: %s %s", r.status_code, r.text)
        return r.json() if r.ok else None
    except Exception as e:
        log.exception("Failed to send message to Telegram: %s", e)
        return None

# --- Simple Hebrew keyword classifier (line-level) ---
def normalize_text(t: str) -> str:
    return t.replace("\r", " ").replace("\n", " ").strip().lower()

def classify_text(text: str) -> str:
    t = normalize_text(text)
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
    if any(x in t for x in ["ירק", "ירקניה", "שוק ירקות", "ירקות", "מלפפון", "מלפפונים", "חסה", "גזר"]):
        return "ירקניה"
    return "מכולת"

# --- Build grouped summary text ---
def build_summary_text():
    grouped = []
    for cat in CATEGORIES:
        items = [f"- {o['username']}: {o['text']}" for o in orders if o["category"] == cat]
        if items:
            grouped.append(f"{cat}:\n" + "\n".join(items))
    if not grouped:
        return "אין הזמנות כרגע."
    return "סיכום הזמנות:\n\n" + "\n\n".join(grouped)

# --- Webhook endpoint ---
@app.route("/webhook", methods=["POST"])
def webhook():
    global session_active, orders, group_chat_id, admin_id

    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"ok": False, "error": "no json"}), 400

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

    # /start initializes session in the group
    if text.startswith("/start"):
        if group_chat_id is None:
            group_chat_id = chat_id
            log.info("GROUP_CHAT_ID set to %s", group_chat_id)
        if chat_id != group_chat_id:
            send_message(chat_id, "פקודה זו מותרת רק בקבוצה הראשית.")
            return jsonify({"ok": True})

        session_active = True
        orders = []
        if admin_id is None:
            admin_id = user_id
            send_message(admin_id, f"אתה הוגדרת כמנהל הזמנות (id={admin_id}).")
            log.info("ADMIN_ID set to %s", admin_id)

        send_message(group_chat_id, "מישהו משהו מטירה?")
        return jsonify({"ok": True})

    # /summary - admin only
    if text.startswith("/summary"):
        if admin_id is None or user_id != admin_id:
            send_message(chat_id, "אינך מורשה לבקש סיכום.")
            return jsonify({"ok": True})

        summary = build_summary_text()
        max_chunk = 3500

        # Try to send privately first (if admin_id set)
        sent_privately = True
        if admin_id:
            for i in range(0, len(summary), max_chunk):
                resp = send_message(admin_id, summary[i : i + max_chunk])
                if not resp:
                    sent_privately = False
                    break
        else:
            sent_privately = False

        if not sent_privately:
            # Fallback: post the summary in the chat where /summary was requested
            for i in range(0, len(summary), max_chunk):
                send_message(chat_id, summary[i : i + max_chunk])
            send_message(chat_id, "הערה: לא הצלחתי לשלוח הודעה פרטית — שולח את הסיכום כאן בקבוצה במקום.")
        else:
            send_message(chat_id, "הסיכום נשלח אליך בפרטי.")

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

    # Regular group messages while session active: handle multiline -> multiple orders
    if session_active and chat_id == group_chat_id:
        if not text or text.startswith("/"):
            return jsonify({"ok": True})

        # Split by lines and classify each line separately
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            return jsonify({"ok": True})

        categories_for_lines = []
        for line in lines:
            cat = classify_text(line)
            orders.append({
                "user_id": user_id,
                "username": username,
                "text": line,
                "category": cat,
                "message_id": message.get("message_id"),
            })
            categories_for_lines.append(cat)

        # Reply with the category per line (one category per line, Hebrew)
        reply_text = "\n".join(categories_for_lines) if len(categories_for_lines) > 1 else categories_for_lines[0]
        send_message(chat_id, reply_text, reply_to_message_id=message.get("message_id"))
        return jsonify({"ok": True})

    return jsonify({"ok": True})

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
