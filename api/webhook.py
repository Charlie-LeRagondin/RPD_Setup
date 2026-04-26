"""
Webhook Telegram — commande /setup et /start
Hébergé sur Vercel (fonction serverless, limite 10 s d'exécution).
"""

from http.server import BaseHTTPRequestHandler
import json
import os
import time
import requests as req

BOT_TOKEN  = os.environ.get('BOT_TOKEN') or os.environ.get('TELEGRAM_BOT_TOKEN')
GROUP_ID   = int(os.environ.get('GROUP_ID', 0))
TOPIC_ID   = int(os.environ.get('TOPIC_ID', 0))
WEBAPP_URL = os.environ.get('WEBAPP_URL', '')

TG = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ── Helpers Telegram ──────────────────────────────────────────────────────────

def _send(chat_id, text, parse_mode='HTML', reply_markup=None, thread_id=None):
    payload = {'chat_id': chat_id, 'text': text, 'parse_mode': parse_mode}
    if reply_markup:
        payload['reply_markup'] = reply_markup
    if thread_id:
        payload['message_thread_id'] = thread_id
    r = req.post(f"{TG}/sendMessage", json=payload, timeout=5)
    return r.json().get('result', {})

def _delete(chat_id, message_id):
    try:
        req.post(f"{TG}/deleteMessage",
                 json={'chat_id': chat_id, 'message_id': message_id}, timeout=5)
    except Exception:
        pass

def _answer_cb(callback_query_id, text=None, show_alert=False):
    payload = {'callback_query_id': callback_query_id}
    if text:
        payload['text'] = text
        payload['show_alert'] = show_alert
    req.post(f"{TG}/answerCallbackQuery", json=payload, timeout=5)

def _webapp_kb():
    return {'inline_keyboard': [[
        {'text': '🚀 Ouvrir le Configurateur', 'web_app': {'url': WEBAPP_URL}}
    ]]}

def _url_kb(url, label='🔥 Ouvrir le Configurateur'):
    return {'inline_keyboard': [[
        {'text': label, 'url': url}
    ]]}

def _mention(user: dict) -> str:
    uid   = user.get('id', '')
    first = user.get('first_name', '')
    name  = first or user.get('username', '') or 'trader'
    return f'<a href="tg://user?id={uid}">{name}</a>'

def _bot_username() -> str:
    try:
        return req.get(f"{TG}/getMe", timeout=5).json()['result']['username']
    except Exception:
        return 'bot'

# ── Handlers ──────────────────────────────────────────────────────────────────

def handle_start(msg: dict):
    chat_id = msg['chat']['id']
    text    = msg.get('text', '')

    # from_setup_{group_msg_id}
    if 'from_setup' in text:
        parts = text.split('from_setup_', 1)
        if len(parts) == 2:
            try:
                group_msg_id = int(parts[1].split()[0])
                _delete(GROUP_ID, group_msg_id)
            except (ValueError, IndexError):
                pass
        _send(chat_id,
              '✅ Configurateur prêt — clique pour ouvrir :',
              reply_markup=_webapp_kb())
    else:
        _send(chat_id,
              '👋 Bonjour ! Tape /setup dans le topic setup pour publier un setup de trading.',
              reply_markup=_webapp_kb())


def handle_setup(msg: dict):
    user      = msg.get('from', {})
    chat      = msg.get('chat', {})
    chat_id   = chat['id']
    chat_type = chat.get('type', '')
    thread_id = msg.get('message_thread_id')
    msg_id    = msg.get('message_id')
    m         = _mention(user)

    # ── 1. Conversation privée ────────────────────────────────────
    if chat_type == 'private':
        _send(chat_id,
              '🎯 Remplis le formulaire pour publier ton setup :',
              reply_markup=_webapp_kb())
        return

    # ── 2. Mauvais groupe → silence total ─────────────────────────
    if chat_id != GROUP_ID:
        return

    # Supprimer la commande /setup originale
    _delete(chat_id, msg_id)

    # ── 3. Bon groupe, mauvais topic ──────────────────────────────
    if (thread_id or 0) != TOPIC_ID:
        result = _send(
            chat_id,
            f"❌ {m}, la commande /setup n'est disponible que dans le topic dédié aux setups.",
            thread_id=thread_id,
        )
        err_id = result.get('message_id')
        if err_id:
            time.sleep(5)
            _delete(chat_id, err_id)
        return

    # ── 4. Bon groupe, bon topic ──────────────────────────────────
    bot_username = _bot_username()
    deep_link    = f"https://t.me/{bot_username}?start=from_setup_PLACEHOLDER"

    # Envoyer d'abord sans msg_id pour obtenir le message_id du bot
    result = _send(
        chat_id,
        f"🚀 {m}, accès validé. Clique ci-dessous :",
        reply_markup=_url_kb(deep_link),
        thread_id=TOPIC_ID,
    )
    bot_msg_id = result.get('message_id')

    if bot_msg_id:
        # Mettre à jour le bouton avec le vrai message_id pour suppression au clic
        real_link = f"https://t.me/{bot_username}?start=from_setup_{bot_msg_id}"
        req.post(f"{TG}/editMessageReplyMarkup", json={
            'chat_id':    chat_id,
            'message_id': bot_msg_id,
            'reply_markup': _url_kb(real_link),
        }, timeout=5)
        # Auto-delete dans le budget Vercel (~5s)
        time.sleep(5)
        _delete(chat_id, bot_msg_id)


# ── Handler HTTP Vercel ───────────────────────────────────────────────────────

class handler(BaseHTTPRequestHandler):

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            update = json.loads(self.rfile.read(length))

            msg = update.get('message') or update.get('channel_post')
            if msg:
                text = msg.get('text', '')
                if text.startswith('/start'):
                    handle_start(msg)
                elif text.startswith('/setup'):
                    handle_setup(msg)

        except Exception:
            pass

        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *a):
        pass
