"""
Bot Telegram — commande /setup
Tourne en polling sur Render (service Background Worker).
"""

import asyncio
import logging
import os

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)
from telegram.error import Forbidden
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(
    format='%(asctime)s — %(name)s — %(levelname)s — %(message)s',
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Config (variables d'environnement Render) ─────────────────────────────────

BOT_TOKEN  = os.environ.get('BOT_TOKEN') or os.environ.get('TELEGRAM_BOT_TOKEN')
GROUP_ID   = int(os.environ.get('GROUP_ID', 0))
TOPIC_ID   = int(os.environ.get('TOPIC_ID', 0))
WEBAPP_URL = os.environ.get('WEBAPP_URL', '')

# ── Helpers ───────────────────────────────────────────────────────────────────

def mention(user) -> str:
    """Mention HTML cliquable : @username ou lien via tg://user si pas d'username."""
    if user.username:
        return f'@{user.username}'
    return f'<a href="tg://user?id={user.id}">{user.first_name}</a>'

def webapp_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton('🚀 Ouvrir le setup', web_app=WebAppInfo(url=WEBAPP_URL))
    ]])

async def _delete_later(bot, chat_id: int, message_id: int, delay: int):
    """Supprime un message après `delay` secondes."""
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass

# ── Handlers ──────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start [from_setup]
    Si deep link from_setup → envoyer directement la Mini App.
    """
    args = context.args or []
    if args and args[0] == 'from_setup':
        await update.message.reply_text(
            '✅ Bot activé. Retourne dans le topic SETUP et retape /setup, '
            'ou utilise le bouton ci-dessous directement.',
            reply_markup=webapp_keyboard(),
        )
    else:
        await update.message.reply_text(
            '👋 Bonjour ! Tape /setup pour publier un setup de trading.',
            reply_markup=webapp_keyboard(),
        )


async def setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /setup — comportement selon le contexte :
      • Privé           → bouton Web App directement
      • Mauvais groupe  → ignorer
      • Bon groupe, mauvais topic → erreur 10 s
      • Bon groupe, bon topic     → notif 15 s + MP
    """
    msg  = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    # ── 1. Conversation privée ────────────────────────────────────
    if chat.type == 'private':
        await msg.reply_text(
            '🎯 Remplis le formulaire pour publier ton setup :',
            reply_markup=webapp_keyboard(),
        )
        return

    # ── 2. Mauvais groupe → silence ───────────────────────────────
    if chat.id != GROUP_ID:
        return

    thread_id = getattr(msg, 'message_thread_id', None) or 0

    # ── 3. Bon groupe, mauvais topic ──────────────────────────────
    if thread_id != TOPIC_ID:
        sent = await msg.reply_text(
            f'❌ {mention(user)}, la commande /setup n\'est disponible que '
            f'dans le topic dédié aux setups.',
            parse_mode='HTML',
        )
        context.application.create_task(
            _delete_later(context.bot, chat.id, sent.message_id, 10)
        )
        return

    # ── 4. Bon groupe, bon topic ──────────────────────────────────
    notif = await msg.reply_text(
        f'🎯 {mention(user)}, je t\'envoie le formulaire en privé ⬇️',
        parse_mode='HTML',
    )
    context.application.create_task(
        _delete_later(context.bot, chat.id, notif.message_id, 15)
    )

    try:
        await context.bot.send_message(
            chat_id=user.id,
            text='🎯 Remplis le formulaire pour publier ton setup :',
            reply_markup=webapp_keyboard(),
        )

    except Forbidden:
        # L'utilisateur n'a jamais démarré le bot
        bot_info   = await context.bot.get_me()
        deep_link  = f'https://t.me/{bot_info.username}?start=from_setup'
        fallback   = await context.bot.send_message(
            chat_id=chat.id,
            message_thread_id=TOPIC_ID,
            text=(
                f'👋 {mention(user)}, démarre le bot une première fois en cliquant '
                f'ci-dessous, puis retape /setup ici.'
            ),
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton('▶️ Démarrer le bot d\'abord', url=deep_link)
            ]]),
        )
        context.application.create_task(
            _delete_later(context.bot, chat.id, fallback.message_id, 30)
        )

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('setup', setup))
    logger.info('Bot démarré en polling…')
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
