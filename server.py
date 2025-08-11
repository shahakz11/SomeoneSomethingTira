import os
import logging
from flask import Flask, request, jsonify
import openai
from telegram import Bot

# --- Configuration from environment variables ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")            # Telegram bot token (required)
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")  # Optional - for better classification
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-3.5-turbo")
GROUP_CHAT_ID = os.environ.get("GROUP_CHAT_ID")    # Group chat id (required for /start)
ADMIN_ID = os.environ.get("ADMIN_ID")              # Your Telegram user id for /summary (recommended)

# --- Categories (Hebrew) ---
CATEGORIES = [
    "חומוס",
    "שווארמה",
    "מאפיה",
    "מכולת",
    "בשר",
    "דגים",
    "משתלה",
]

# --- Basic runtime objects ---
logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

if not BOT_TOKEN:
    logging.error("BOT_TOKEN not set in environment variables. The bot cannot run without it.")

bot = Bot(token=BOT_TOKEN) if BOT_TOKEN else None

if OPENAI_API_KEY:
    openai.api_key = OPENAI_API_KEY

# In-memory storage for the current "order session"
orders = []  # list of dicts: {user_id, username, text, category, message_id}
session_active = False


# --- Classification helpers ---
def classify_with_openai(text: str):
    """Try to classify using OpenAI. Returns one of CATEGORIES or None."""
    try:
        system_prompt = (
            "אתה מסווג טקסט בעברית לקטגוריות מדויקות. "
            "יש לבחור בדיוק אחת מהקטגוריות הבאות (תשובה בעברית, בדיוק כפי שמופיעות): "
            + ", ".join(CATEGORIES)
            + ".\n" 
            "החזר אך ורק את שם הקטגוריה בעברית, ללא הסברים נוספים.\n"
            "אם לא ברור, בחר 'מכולת' כברירת מחדל."
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ]
        resp = openai.ChatCompletion.create(
            model=OPENAI_MODEL,
            messages=messages,
            temperature=0,
            max_tokens=12,
        )
        raw = resp["choices"][0]["message"]["content"].strip()
        # Try to find a category name in the model reply
        for c in CATEGORIES:
            if c in raw:
                return c
        # If the model returned exactly one of the categories
        if raw in CATEGORIES:
            return raw
    except Exception as e:
        logging.exception("OpenAI classification failed")
    return None


def classify_fallback(text: str):
    """Lightweight keyword-based fallback classifier (Hebrew)."""
    t = text.lower()
    if any(w in t for w in ["חומוס", "חומוסיה"]):
        return "חומוס"
    if any(w in t for w in ["שווארמה", "shawarma", "שווא"]):
        return "שווארמה"
    if any(w in t for w in ["מאפה", "מאפיה", "לחם", "קרואסון", "בייגל"]):
        return "מאפיה"
    if any(w in t for w in ["מכולת", "סופר", "סופרמרקט", "חלב", "לחם", "סוכר"]):
        return "מכולת"
    if any(w in t for w in ["בשר", "קבב", "סטייק", "פרגית"]):
        return "בשר"
    if any(w in t for w in ["דג", "סלמון", "טונה", "פילה"]):
        return "דגים"
    if any(w in t for w in ["עציץ", "צמח", "שתיל", "משתלה"]):
        return "משתלה"
    return "מכולת"


def classify_text(text: str):
    """Return one of CATEGORIES. Use OpenAI if available, otherwise fallback."""
    if OPENAI_API_KEY:
        result = classify_with_openai(text)
        if result:
            return result
    return classify_fallback(text)


# --- Telegram helpers ---

def send_msg(chat_id, text, reply_to_message_id=None):
    if not bot:
        logging.warning("No bot configured - cannot send message")
        return
    try:
        if reply_to_message_id:
            bot.send_message(chat_id=chat_id, text=text, reply_to_message_id=reply_to_message_id)
        else:
            bot.send_message(chat_id=chat_id, text=text)
    except Exception:
        logging.exception("Failed to send Telegram message")


# --- Flask routes ---
@app.route("/", methods=["GET"])
def index():
    return "מוכן — SomeoneSomethingTira"


@app.route("/webhook", methods=["POST"])
def webhook():
    """Receive Telegram updates (webhook)."""
    global session_active
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"ok": False, "error": "no json"}), 400

    # Telegram sends updates with 'message' (or other fields). We handle 'message'.
    message = data.get("message") or data.get("edited_message")
    if not message:
        return jsonify({"ok": True})

    chat = message.get("chat", {})
    chat_id = chat.get("id")
    text = message.get("text", "") or ""
    user = message.get("from", {})
    user_id = user.get("id")
    username = user.get("username") or user.get("first_name") or "משתמש"

    # --- /start in the GROUP starts a session ---
    if text.startswith("/start") and str(chat_id) == str(GROUP_CHAT_ID):
        session_active = True
        orders.clear()
        send_msg(chat_id, "מישהו משהו מטירה? כתבו כאן מה אתם רוצים, ואז מנהל יכול לבקש /summary כדי לקבל סיכום.")
        return jsonify({"ok": True})

    # --- /summary (admin only) ---
    if text.startswith("/summary"):
        if not ADMIN_ID or str(user_id) != str(ADMIN_ID):
            send_msg(chat_id, "אינך מורשה לבקש סיכום.")
            return jsonify({"ok": True})

        if not orders:
            send_msg(ADMIN_ID, "אין הזמנות כרגע.")
            return jsonify({"ok": True})

        # Group orders by category
        parts = []
        for cat in CATEGORIES:
            lines = [f"- {o['username']}: {o['text']}" for o in orders if o['category'] == cat]
            if lines:
                parts.append(f"{cat}:\n" + "\n".join(lines))

        summary = "סיכום הזמנות:\n\n" + "\n\n".join(parts)

        # Telegram has a message size limit; split if necessary
        MAX = 3900
        for i in range(0, len(summary), MAX):
            send_msg(ADMIN_ID, summary[i:i+MAX])
        return jsonify({"ok": True})

    # --- /reset (admin only) ---
    if text.startswith("/reset") and ADMIN_ID and str(user_id) == str(ADMIN_ID):
        orders.clear()
        send_msg(chat_id, "מאגר ההזמנות אופס.")
        return jsonify({"ok": True})

    # --- Regular group messages while a session is active ---
    if session_active and str(chat_id) == str(GROUP_CHAT_ID):
        # ignore commands
        if text.strip().startswith("/"):
            return jsonify({"ok": True})

        category = classify_text(text)
        orders.append({
            "user_id": user_id,
            "username": username,
            "text": text,
            "category": category,
            "message_id": message.get("message_id"),
        })

        # Reply to the user's message with the category (Hebrew only)
        send_msg(chat_id, category, reply_to_message_id=message.get("message_id"))
        return jsonify({"ok": True})

    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
