import os
from flask import Flask, request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import openai 

# Get environment variables
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

if not TELEGRAM_TOKEN or not OPENAI_API_KEY:
    raise ValueError("Missing TELEGRAM_TOKEN or OPENAI_API_KEY in environment variables.")

# OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

# Flask app for Render
app = Flask(__name__)

# Allowed categories
CATEGORIES = ['חומוס', 'שווארמה', 'מאפיה', 'מכולת', 'בשר', 'דגים', 'משתלה', 'ירקניה']

# Command handler for /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Send the fixed message
    await update.message.reply_text("מישהו משהו מטירה?")

    # Ask AI to return one category
    prompt = f"אנא החזר רק קטגוריה אחת מתוך הרשימה: {', '.join(CATEGORIES)}."
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "אתה עוזר שבוחר קטגוריות רק מתוך הרשימה הנתונה."},
            {"role": "user", "content": prompt}
        ],
        max_tokens=20
    )

    ai_category = response.choices[0].message.content.strip()

    # Ensure it's a valid category
    if ai_category not in CATEGORIES:
        ai_category = "מכולת"  # ברירת מחדל

    await update.message.reply_text(ai_category)

# Telegram bot setup
application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
application.add_handler(CommandHandler("start", start))

# Flask route for health check
@app.route('/')
def home():
    return "Bot is running!"

# Webhook endpoint
@app.route('/webhook', methods=['POST'])
def webhook():
    application.update_queue.put_nowait(Update.de_json(request.get_json(force=True), application.bot))
    return "OK", 200

if __name__ == '__main__':
    import asyncio
    async def run():
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
    asyncio.run(run())
