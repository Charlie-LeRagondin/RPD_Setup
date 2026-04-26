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

def _send_then_delete(chat_id, text, delay=5, parse_mode='HTML',
                      reply_markup=None, thread_id=None):
    result = _send(chat_id, text, parse_mode=parse_mode,
                   reply_markup=reply_markup, thread_id=thread_id)
    msg_id = result.get('message_id')
    if msg_id:
        time.sleep(delay)
        _delete(chat_id, msg_id)

def _answer_cb(callback_query_id, text=None, show_alert=False):
    payload = {'callback_query_id': callback_query_id}
    if text:
        payload['text'] = text
        payload['show_alert'] = show_alert
    req.post(f"{TG}/answerCallbackQuery", json=payload, timeout=5)

def _webapp_kb():
    return {'inline_keyboard': [[
        {'text': '🚀 Ouvrir le setup', 'web_app': {'url': WEBAPP_URL}}
    ]]}

def _launcher_kb(user_id):
    """Bouton web_app — ouvre la Mini App directement au clic."""
    return {'inline_keyboard': [[
        {'text': '🚀 Lancer le configurateur', 'web_app': {'url': WEBAPP_URL}}
    ]]}

def _deeplink_kb(bot_username):
    return {'inline_keyboard': [[
        {'text': "▶️ Démarrer le bot d'abord",
         'url': f"https://t.me/{bot_username}?start=from_setup"}
    ]]}

def _mention(user: dict) -> str:
    if user.get('username'):
        return f"@{user['username']}"
    first = user.get('first_name', 'utilisateur')
    uid   = user.get('id', '')
    return f'<a href="tg://user?id={uid}">{first}</a>'

def _bot_username() -> str:
    try:
        return req.get(f"{TG}/getMe", timeout=5).json()['result']['username']
    except Exception:
        return 'bot'

# ── Handlers ──────────────────────────────────────────────────────────────────

def handle_start(msg: dict):
    chat_id = msg['chat']['id']
    text    = msg.get('text', '')
    if 'from_setup' in text:
        _send(chat_id,
              '✅ Bot activé. Retourne dans le topic SETUP et retape /setup, '
              'ou utilise le bouton ci-dessous directement.',
              reply_markup=_webapp_kb())
    else:
        _send(chat_id,
              '👋 Bonjour ! Tape /setup pour publier un setup de trading.',
              reply_markup=_webapp_kb())


def handle_setup(msg: dict):
    user      = msg.get('from', {})
    chat      = msg.get('chat', {})
    chat_id   = chat['id']
    chat_type = chat.get('type', '')
    thread_id = msg.get('message_thread_id')
    msg_id    = msg.get('message_id')
    user_id   = user.get('id')
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

    # Supprimer la commande /setup originale dans tous les cas groupe
    _delete(chat_id, msg_id)

    # ── 3. Bon groupe, mauvais topic ──────────────────────────────
    if (thread_id or 0) != TOPIC_ID:
        _send_then_delete(
            chat_id,
            f"❌ {m}, la commande /setup n'est disponible que dans le topic dédié aux setups.",
            delay=5,
        )
        return

    # ── 4. Bon groupe, bon topic ──────────────────────────────────
    # Le bouton ouvre la Mini App directement, le message disparaît après 8s
    _send_then_delete(
        chat_id,
        f"🎯 {m} :",
        delay=8,
        reply_markup=_launcher_kb(user_id),
        thread_id=TOPIC_ID,
    )




# ── Handler HTTP Vercel ───────────────────────────────────────────────────────

class handler(BaseHTTPRequestHandler):

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            update = json.loads(self.rfile.read(length))

            # Commandes texte
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
