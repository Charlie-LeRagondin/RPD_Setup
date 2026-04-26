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
PUBLISH_PUBLISH_TOPIC_ID = int(os.environ.get('PUBLISH_PUBLISH_TOPIC_ID', 0)) or None
COUNTER_FILE     = '/tmp/counter.txt'

# ── Compteur setup ────────────────────────────────────────────────────────────

def _last_from_telegram():
    """Cherche #SETUP_N dans les derniers posts du canal via getUpdates."""
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

# ── Formateur de message ──────────────────────────────────────────────────────

def format_message(d, setup_num):
    style_labels = {'SCALP': 'SCALP 🚀', 'INTRA': 'INTRA ⚡', 'SWING': 'SWING 🌊'}
    dir_badge    = '🟢' if d['direction'] == 'LONG' else '🔴'

    now = datetime.now(_PARIS) if _PARIS else datetime.utcnow()
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

    # Entrée
    if d.get('entree_market'):
        lines.append(f"➡️ Entrée : MARKET à {d.get('prix_entree_manuel', '')}")
    else:
        lines.append(f"➡️ PEH : {d['peh']}")
        if d.get('peb'):
            lines.append(f"➡️ PEB : {d['peb']}")
    lines.append("")

    # TPs avec R
    entry = _entry_price(d)
    for i, tp_val in enumerate(d.get('tps', []), 1):
        r_str = _calc_r(tp_val, entry, d['sl'], d['direction']) if entry is not None else ''
        lines.append(f"🎯 TP{i} : {tp_val}{r_str}")
    lines.append("")

    # SL + BE
    lines.append(f"❌ SL : {d['sl']}")

    be_map = {
        'MANUEL':          'Manuel',
        'AUTO_TP1':        'TP1',
        'AUTO_TP2':        'TP2',
        'PRIX_SPECIFIQUE': str(d.get('be_prix', '')),
    }
    lines.append(f"🛡 BE : {be_map.get(d.get('breakeven', 'MANUEL'), 'Manuel')}")

    # Commentaire
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
            length  = int(self.headers.get('Content-Length', 0))
            data    = json.loads(self.rfile.read(length))

            setup_num = get_next_number()
            caption   = format_message(data, setup_num)
            tg        = f"https://api.telegram.org/bot{BOT_TOKEN}"

            if data.get('photo_b64'):
                photo_bytes = base64.b64decode(data['photo_b64'])
                extra = {'message_thread_id': PUBLISH_TOPIC_ID} if PUBLISH_TOPIC_ID else {}
                r = req.post(
                    f"{tg}/sendPhoto",
                    data={'chat_id': GROUP_ID, 'caption': caption, 'parse_mode': 'HTML', **extra},
                    files={'photo': ('chart.jpg', photo_bytes, 'image/jpeg')},
                    timeout=15,
                )
            else:
                payload = {'chat_id': GROUP_ID, 'text': caption, 'parse_mode': 'HTML'}
                if PUBLISH_TOPIC_ID:
                    payload['message_thread_id'] = PUBLISH_TOPIC_ID
                r = req.post(f"{tg}/sendMessage", json=payload, timeout=15)

            result = r.json()
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
