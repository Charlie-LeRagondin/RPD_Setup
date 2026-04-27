"""
Webhook Telegram — /setup, /start + callbacks inline keyboard
Hébergé sur Vercel (fonction serverless, limite 10 s d'exécution).

État du setup lu directement depuis le message Telegram (texte + clavier),
sans dépendance à /tmp (non partagé entre instances Vercel).
"""

from http.server import BaseHTTPRequestHandler
import json
import os
import re
import time
import requests as req

BOT_TOKEN        = os.environ.get('BOT_TOKEN') or os.environ.get('TELEGRAM_BOT_TOKEN')
GROUP_ID         = int(os.environ.get('GROUP_ID', 0))
TOPIC_ID         = int(os.environ.get('TOPIC_ID', 0))
PUBLISH_TOPIC_ID = int(os.environ.get('PUBLISH_TOPIC_ID', 0)) or None
WEBAPP_URL       = os.environ.get('WEBAPP_URL', '')

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
        payload['text']       = text
        payload['show_alert'] = show_alert
    req.post(f"{TG}/answerCallbackQuery", json=payload, timeout=5)

def _edit_keyboard(chat_id, message_id, keyboard):
    req.post(f"{TG}/editMessageReplyMarkup", json={
        'chat_id':      chat_id,
        'message_id':   message_id,
        'reply_markup': keyboard,
    }, timeout=5)

def _webapp_kb():
    return {'inline_keyboard': [[
        {'text': '🚀 Ouvrir le Configurateur', 'web_app': {'url': WEBAPP_URL}}
    ]]}

def _url_kb(url, label='🔥 Ouvrir le Configurateur'):
    return {'inline_keyboard': [[{'text': label, 'url': url}]]}

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

# ── Calcul R ──────────────────────────────────────────────────────────────────

def _calc_r(tp_val, entry, sl, direction):
    try:
        tp    = float(tp_val)
        one_r = abs(float(entry) - float(sl))
        if one_r == 0:
            return ''
        r = (tp - float(entry)) / one_r if direction == 'LONG' else (float(entry) - tp) / one_r
        if r <= 0:
            return ' (R invalide)'
        return f' (+{r:.1f}R)'
    except Exception:
        return ''

# ── Keyboard builder ──────────────────────────────────────────────────────────

def _build_keyboard(setup_id, setup):
    rows = []
    sid  = str(setup_id)

    entry_type = setup['entry_type']
    if entry_type == 'PEH':
        hit = setup['peh_hit']
        rows.append([{
            'text':          '⚠️ PEH CANCEL' if hit else '➡️ PEH IN',
            'callback_data': f'peh_cancel:{sid}' if hit else f'peh_in:{sid}',
        }])
    elif entry_type == 'PEH_PEB':
        rows.append([
            {
                'text':          '⚠️ PEH CANCEL' if setup['peh_hit'] else '➡️ PEH IN',
                'callback_data': f'peh_cancel:{sid}' if setup['peh_hit'] else f'peh_in:{sid}',
            },
            {
                'text':          '⚠️ PEB CANCEL' if setup['peb_hit'] else '➡️ PEB IN',
                'callback_data': f'peb_cancel:{sid}' if setup['peb_hit'] else f'peb_in:{sid}',
            },
        ])

    tps    = setup['tps']
    tp_hit = setup['tp_hit']
    i = 0
    while i < len(tps):
        row = []
        for j in range(2):
            idx = i + j
            if idx < len(tps):
                n   = idx + 1
                hit = tp_hit[idx]
                row.append({
                    'text':          f'⚠️ TP{n} CANCEL' if hit else f'🎯 TP{n}',
                    'callback_data': f'tp_cancel:{sid}:{n}' if hit else f'tp_hit:{sid}:{n}',
                })
        rows.append(row)
        i += 2

    rows.append([{'text': '🛡 Passage BE', 'callback_data': f'be_pass:{sid}'}])
    rows.append([
        {'text': '❌ SL',       'callback_data': f'sl_hit:{sid}'},
        {'text': '🛠 Clôturer', 'callback_data': f'close:{sid}'},
    ])
    return {'inline_keyboard': rows}

# ── Parse setup depuis le message Telegram ────────────────────────────────────

def _parse_message(msg: dict) -> dict:
    """
    Reconstitue l'état complet du setup depuis le texte/caption + clavier
    du message. Aucune dépendance à /tmp.
    """
    text = msg.get('text') or msg.get('caption', '')

    # setup_id
    m        = re.search(r'#SETUP_(\d+)', text)
    setup_id = m.group(1) if m else None

    # creator_id (encodé en spoiler <tg-spoiler>cid:{id}</tg-spoiler>)
    m          = re.search(r'cid:(\d+)', text)
    creator_id = int(m.group(1)) if m else None

    # direction
    direction = 'LONG' if '(LONG)' in text else 'SHORT'

    # entry_type + entry_price
    mkt = re.search(r'MARKET à ([\d.]+)', text)
    if mkt:
        entry_price = float(mkt.group(1))
        entry_type  = 'MARKET'
    else:
        peh = re.search(r'PEH : ([\d.]+)', text)
        peb = re.search(r'PEB : ([\d.]+)', text)
        if peh and peb:
            entry_price = (float(peh.group(1)) + float(peb.group(1))) / 2
            entry_type  = 'PEH_PEB'
        elif peh:
            entry_price = float(peh.group(1))
            entry_type  = 'PEH'
        else:
            entry_price = None
            entry_type  = 'MARKET'

    # sl_value
    m        = re.search(r'SL : ([\d.]+)', text)
    sl_value = m.group(1) if m else None

    # tps (dans l'ordre du message)
    tps = re.findall(r'TP\d+ : ([\d.]+)', text)

    # État actuel depuis le clavier — callback_data est la source de vérité
    keyboard  = (msg.get('reply_markup') or {}).get('inline_keyboard', [])
    peh_hit   = False
    peb_hit   = False
    tp_states = {}

    for row in keyboard:
        for btn in row:
            cbd = btn.get('callback_data', '')
            if cbd.startswith('peh_cancel'):
                peh_hit = True
            elif cbd.startswith('peb_cancel'):
                peb_hit = True
            elif cbd.startswith('tp_cancel') or cbd.startswith('tp_hit'):
                parts = cbd.split(':')
                if len(parts) == 3:
                    n            = int(parts[2])
                    tp_states[n] = (parts[0] == 'tp_cancel')

    tp_hit = [tp_states.get(i + 1, False) for i in range(len(tps))]

    return {
        'setup_id':    setup_id,
        'creator_id':  creator_id,
        'direction':   direction,
        'entry_type':  entry_type,
        'entry_price': entry_price,
        'sl_value':    sl_value,
        'tps':         tps,
        'peh_hit':     peh_hit,
        'peb_hit':     peb_hit,
        'tp_hit':      tp_hit,
        'chat_id':     msg.get('chat', {}).get('id'),
        'message_id':  msg.get('message_id'),
    }

# ── Permissions ───────────────────────────────────────────────────────────────

def _is_authorized(user_id, setup):
    creator_id = setup.get('creator_id')
    if creator_id and user_id == creator_id:
        return True
    try:
        r      = req.post(f"{TG}/getChatMember",
                          json={'chat_id': GROUP_ID, 'user_id': user_id}, timeout=5)
        status = r.json().get('result', {}).get('status', '')
        return status in ('creator', 'administrator')
    except Exception:
        return False

# ── Callback handler ──────────────────────────────────────────────────────────

def handle_callback(cb: dict):
    query_id = cb['id']
    user     = cb.get('from', {})
    user_id  = user.get('id')
    data     = cb.get('data', '')
    msg      = cb.get('message')

    if not msg:
        _answer_cb(query_id)
        return

    parts  = data.split(':')
    action = parts[0]

    # Reconstituer le setup depuis le message — pas de /tmp
    setup = _parse_message(msg)

    if not setup.get('setup_id'):
        _answer_cb(query_id, "❌ Setup introuvable")
        return

    setup_id = setup['setup_id']

    if not _is_authorized(user_id, setup):
        _answer_cb(query_id, "❌ Réservé au créateur du setup et aux admins")
        return

    chat_id    = setup['chat_id']
    message_id = setup['message_id']

    # ── Stubs (Prompt 2) ──────────────────────────────────────────────────────
    if action in ('be_pass', 'sl_hit', 'close'):
        _answer_cb(query_id, "🚧 Bientôt disponible (prompt 2)")
        return

    # ── PEH ───────────────────────────────────────────────────────────────────
    if action == 'peh_in':
        setup['peh_hit'] = True
        _edit_keyboard(chat_id, message_id, _build_keyboard(setup_id, setup))
        _send(chat_id, f"✅ #SETUP_{setup_id} | PEH IN ✅", thread_id=PUBLISH_TOPIC_ID)
        _answer_cb(query_id)

    elif action == 'peh_cancel':
        setup['peh_hit'] = False
        _edit_keyboard(chat_id, message_id, _build_keyboard(setup_id, setup))
        _send(chat_id, f"⚠️ #SETUP_{setup_id} | CORRECTION : PEH IN annulé", thread_id=PUBLISH_TOPIC_ID)
        _answer_cb(query_id)

    # ── PEB ───────────────────────────────────────────────────────────────────
    elif action == 'peb_in':
        setup['peb_hit'] = True
        _edit_keyboard(chat_id, message_id, _build_keyboard(setup_id, setup))
        _send(chat_id, f"✅ #SETUP_{setup_id} | PEB IN ✅", thread_id=PUBLISH_TOPIC_ID)
        _answer_cb(query_id)

    elif action == 'peb_cancel':
        setup['peb_hit'] = False
        _edit_keyboard(chat_id, message_id, _build_keyboard(setup_id, setup))
        _send(chat_id, f"⚠️ #SETUP_{setup_id} | CORRECTION : PEB IN annulé", thread_id=PUBLISH_TOPIC_ID)
        _answer_cb(query_id)

    # ── TP ────────────────────────────────────────────────────────────────────
    elif action in ('tp_hit', 'tp_cancel'):
        n   = int(parts[2]) if len(parts) > 2 else 1
        idx = n - 1
        if idx < 0 or idx >= len(setup['tp_hit']):
            _answer_cb(query_id, "❌ TP invalide")
            return

        is_hit               = (action == 'tp_hit')
        setup['tp_hit'][idx] = is_hit
        _edit_keyboard(chat_id, message_id, _build_keyboard(setup_id, setup))

        if is_hit:
            ep    = setup.get('entry_price')
            sl    = setup.get('sl_value')
            tp_v  = setup['tps'][idx] if idx < len(setup['tps']) else None
            r_str = _calc_r(tp_v, ep, sl, setup['direction']) if (ep and sl and tp_v) else ''
            _send(chat_id, f"✅ #SETUP_{setup_id} | TP{n}{r_str} ✅", thread_id=PUBLISH_TOPIC_ID)
        else:
            _send(chat_id, f"⚠️ #SETUP_{setup_id} | CORRECTION : TP{n} annulé", thread_id=PUBLISH_TOPIC_ID)

        _answer_cb(query_id)

    else:
        _answer_cb(query_id)

# ── Handlers /start et /setup ─────────────────────────────────────────────────

def handle_start(msg: dict):
    chat_id = msg['chat']['id']
    text    = msg.get('text', '')

    if 'from_setup' in text:
        parts = text.split('from_setup_', 1)
        if len(parts) == 2:
            try:
                group_msg_id = int(parts[1].split()[0])
                _delete(GROUP_ID, group_msg_id)
            except (ValueError, IndexError):
                pass
        _send(chat_id, '✅ Configurateur prêt — clique pour ouvrir :', reply_markup=_webapp_kb())
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

    if chat_type == 'private':
        _send(chat_id, '🎯 Remplis le formulaire pour publier ton setup :', reply_markup=_webapp_kb())
        return

    if chat_id != GROUP_ID:
        return

    _delete(chat_id, msg_id)

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

    bot_username = _bot_username()
    deep_link    = f"https://t.me/{bot_username}?start=from_setup_PLACEHOLDER"
    result       = _send(
        chat_id,
        f"🚀 {m}, accès validé. Clique ci-dessous :",
        reply_markup=_url_kb(deep_link),
        thread_id=TOPIC_ID,
    )
    bot_msg_id = result.get('message_id')
    if bot_msg_id:
        real_link = f"https://t.me/{bot_username}?start=from_setup_{bot_msg_id}"
        req.post(f"{TG}/editMessageReplyMarkup", json={
            'chat_id':      chat_id,
            'message_id':   bot_msg_id,
            'reply_markup': _url_kb(real_link),
        }, timeout=5)
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

            cb = update.get('callback_query')
            if cb:
                handle_callback(cb)

        except Exception:
            pass

        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *a):
        pass
