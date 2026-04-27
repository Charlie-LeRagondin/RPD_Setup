"""
API — publication d'un setup depuis la Mini App
Hébergé sur Vercel (fonction serverless).
"""

from http.server import BaseHTTPRequestHandler
import json
import os
import re
import base64
from datetime import datetime
import requests as req

try:
    from zoneinfo import ZoneInfo
    _PARIS = ZoneInfo('Europe/Paris')
except Exception:
    _PARIS = None

BOT_TOKEN        = os.environ.get('BOT_TOKEN')
GROUP_ID         = os.environ.get('GROUP_ID')
PUBLISH_TOPIC_ID = int(os.environ.get('PUBLISH_TOPIC_ID', 0)) or None
COUNTER_FILE     = '/tmp/counter.txt'
STATE_FILE       = '/tmp/setups_state.json'

# ── State ─────────────────────────────────────────────────────────────────────

def _load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def _save_state(state):
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f)
    except Exception:
        pass

# ── Compteur setup ────────────────────────────────────────────────────────────

def _last_from_telegram():
    try:
        r = req.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
            params={'allowed_updates': '["channel_post"]', 'limit': 100, 'offset': -100},
            timeout=5,
        )
        pattern = re.compile(r'#SETUP_(\d+)')
        for upd in reversed(r.json().get('result', [])):
            post = upd.get('channel_post') or {}
            text = post.get('text', '') or post.get('caption', '')
            m = pattern.search(text)
            if m:
                return int(m.group(1))
    except Exception:
        pass
    return None

def get_next_number():
    last = None
    try:
        with open(COUNTER_FILE) as f:
            last = int(f.read().strip())
    except Exception:
        last = _last_from_telegram()
    next_n = (last or 0) + 1
    try:
        with open(COUNTER_FILE, 'w') as f:
            f.write(str(next_n))
    except Exception:
        pass
    return next_n

# ── Calcul du R ───────────────────────────────────────────────────────────────

def _entry_price(d):
    if d.get('entree_market'):
        p = d.get('prix_entree_manuel')
        return float(p) if p else None
    peh, peb = d.get('peh'), d.get('peb')
    if peh and peb:
        try:
            return (float(peh) + float(peb)) / 2
        except Exception:
            pass
    return float(peh) if peh else None

def _calc_r(tp_val, entry, sl, direction):
    try:
        tp    = float(tp_val)
        one_r = abs(entry - float(sl))
        if one_r == 0:
            return ''
        r = (tp - entry) / one_r if direction == 'LONG' else (entry - tp) / one_r
        if r <= 0:
            return ' (R invalide)'
        return f' (+{r:.1f}R)'
    except Exception:
        return ''

# ── Keyboard ──────────────────────────────────────────────────────────────────

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
    # MARKET : pas de ligne entrée

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

# ── Formateur de message ──────────────────────────────────────────────────────

def format_message(d, setup_num):
    style_labels = {'SCALP': 'SCALP 🚀', 'INTRA': 'INTRA ⚡', 'SWING': 'SWING 🌊'}
    dir_badge    = '🟢' if d['direction'] == 'LONG' else '🔴'

    now      = datetime.now(_PARIS) if _PARIS else datetime.utcnow()
    date_str = now.strftime('%d/%m à %H:%M')

    username = d.get('username') or 'inconnu'
    if username not in ('inconnu',) and not username.startswith('@'):
        username = f'@{username}'

    lines = [
        f"🛠 #SETUP_{setup_num} | {style_labels.get(d['style'], d['style'])}",
        f"🧑‍💼 {username} | 📅 {date_str}",
        f"{dir_badge} ${d['actif']} {dir_badge} ({d['direction']})",
        "",
    ]

    if d.get('entree_market'):
        lines.append(f"➡️ Entrée : MARKET à {d.get('prix_entree_manuel', '')}")
    else:
        lines.append(f"➡️ PEH : {d['peh']}")
        if d.get('peb'):
            lines.append(f"➡️ PEB : {d['peb']}")
    lines.append("")

    entry = _entry_price(d)
    for i, tp_val in enumerate(d.get('tps', []), 1):
        r_str = _calc_r(tp_val, entry, d['sl'], d['direction']) if entry is not None else ''
        lines.append(f"🎯 TP{i} : {tp_val}{r_str}")
    lines.append("")

    lines.append(f"❌ SL : {d['sl']}")

    be_map = {
        'MANUEL':          'Manuel',
        'AUTO_TP1':        'TP1',
        'AUTO_TP2':        'TP2',
        'PRIX_SPECIFIQUE': str(d.get('be_prix', '')),
    }
    lines.append(f"🛡 BE : {be_map.get(d.get('breakeven', 'MANUEL'), 'Manuel')}")

    if d.get('comment'):
        lines.append("")
        lines.append(f"📝 {d['comment']}")

    return "\n".join(lines)


# ── Handler HTTP ──────────────────────────────────────────────────────────────

class handler(BaseHTTPRequestHandler):

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self):
        try:
            length    = int(self.headers.get('Content-Length', 0))
            data      = json.loads(self.rfile.read(length))
            setup_num = get_next_number()
            caption   = format_message(data, setup_num)
            tg        = f"https://api.telegram.org/bot{BOT_TOKEN}"

            # Determine entry type
            entry_type = 'MARKET'
            if not data.get('entree_market'):
                if data.get('peh') and data.get('peb'):
                    entry_type = 'PEH_PEB'
                elif data.get('peh'):
                    entry_type = 'PEH'

            tps         = data.get('tps', [])
            setup_entry = {
                'creator_id':         data.get('user_id'),
                'creator_username':   data.get('username'),
                'creator_first_name': data.get('first_name'),
                'chat_id':            int(GROUP_ID) if GROUP_ID else None,
                'message_id':         None,
                'direction':          data['direction'],
                'entry_type':         entry_type,
                'entry_price':        _entry_price(data),
                'peh_value':          data.get('peh'),
                'peb_value':          data.get('peb'),
                'tps':                tps,
                'sl_value':           data.get('sl'),
                'be_type':            data.get('breakeven', 'MANUEL'),
                'be_value':           data.get('be_prix'),
                'peh_hit':            False,
                'peb_hit':            False,
                'tp_hit':             [False] * len(tps),
                'be_passed':          False,
                'sl_hit':             False,
                'closed':             False,
                'created_at':         (datetime.now(_PARIS) if _PARIS else datetime.utcnow()).isoformat(),
            }

            keyboard = _build_keyboard(setup_num, setup_entry)

            if data.get('photo_b64'):
                photo_bytes = base64.b64decode(data['photo_b64'])
                extra = {'message_thread_id': str(PUBLISH_TOPIC_ID)} if PUBLISH_TOPIC_ID else {}
                r = req.post(
                    f"{tg}/sendPhoto",
                    data={
                        'chat_id':      GROUP_ID,
                        'caption':      caption,
                        'parse_mode':   'HTML',
                        'reply_markup': json.dumps(keyboard),
                        **extra,
                    },
                    files={'photo': ('chart.jpg', photo_bytes, 'image/jpeg')},
                    timeout=15,
                )
            else:
                payload = {
                    'chat_id':      GROUP_ID,
                    'text':         caption,
                    'parse_mode':   'HTML',
                    'reply_markup': keyboard,
                }
                if PUBLISH_TOPIC_ID:
                    payload['message_thread_id'] = PUBLISH_TOPIC_ID
                r = req.post(f"{tg}/sendMessage", json=payload, timeout=15)

            result = r.json()
            msg_id = (result.get('result') or {}).get('message_id')
            setup_entry['message_id'] = msg_id

            state = _load_state()
            state[str(setup_num)] = setup_entry
            _save_state(state)

            status = 200 if result.get('ok') else 502
            self.send_response(status)
            self.send_header('Content-type', 'application/json')
            self._cors()
            self.end_headers()
            self.wfile.write(json.dumps({'ok': result.get('ok'), 'tg': result}).encode())

        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self._cors()
            self.end_headers()
            self.wfile.write(json.dumps({'ok': False, 'error': str(e)}).encode())

    def log_message(self, *a):
        pass
