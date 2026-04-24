from http.server import BaseHTTPRequestHandler
import json
import os
import base64
import requests as req

BOT_TOKEN  = os.environ.get('BOT_TOKEN')
CHANNEL_ID = os.environ.get('CHANNEL_ID')

def format_message(d):
    dir_emoji = "▲" if d['direction'] == 'LONG' else "▼"
    dir_badge = "🟢" if d['direction'] == 'LONG' else "🔴"
    style_map = {'SCALP': 'SCALP 🚀', 'INTRA': 'INTRA ⚡', 'SWING': 'SWING 🌊'}

    lines = [
        f"📊 <b>NOUVEAU SETUP — {d['actif']}</b>",
        f"{dir_badge} <b>{dir_emoji} {d['direction']}</b>  |  {style_map.get(d['style'], d['style'])}",
        "",
    ]

    # Entry
    if d.get('entree_market'):
        lines.append("💰 <b>Entrée :</b> MARKET")
    else:
        entry = f"💰 <b>Zone d'entrée</b>\n   PEH : <code>{d['peh']}</code>"
        if d.get('peb'):
            entry += f"\n   PEB : <code>{d['peb']}</code>"
        lines.append(entry)
    lines.append("")

    # TPs
    tp_lines = ["🎯 <b>Objectifs</b>"]
    for i in range(1, 6):
        val = d.get(f'tp{i}')
        if val:
            tp_lines.append(f"   TP{i} : <code>{val}</code>")
    lines += tp_lines
    lines.append("")

    # SL
    lines.append(f"❌ <b>Stop Loss</b>\n   SL : <code>{d['sl']}</code>")
    lines.append("")

    # Breakeven
    be_labels = {
        'MANUEL':           'Manuel',
        'AUTO_TP1':         'Auto à TP1 🎯',
        'AUTO_TP2':         'Auto à TP2 🎯',
        'PRIX_SPECIFIQUE':  f"Prix spécifique : {d.get('be_prix', '')}",
    }
    lines.append(f"⚙️ <b>Breakeven :</b> {be_labels.get(d.get('breakeven','MANUEL'), 'Manuel')}")

    # Comment
    if d.get('comment'):
        lines.append("")
        lines.append(f"📝 <i>{d['comment']}</i>")

    return "\n".join(lines)


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
            length = int(self.headers.get('Content-Length', 0))
            data   = json.loads(self.rfile.read(length))

            caption = format_message(data)
            tg      = f"https://api.telegram.org/bot{BOT_TOKEN}"

            if data.get('photo_b64'):
                photo_bytes = base64.b64decode(data['photo_b64'])
                r = req.post(
                    f"{tg}/sendPhoto",
                    data={'chat_id': CHANNEL_ID, 'caption': caption, 'parse_mode': 'HTML'},
                    files={'photo': ('chart.jpg', photo_bytes, 'image/jpeg')},
                    timeout=15,
                )
            else:
                r = req.post(
                    f"{tg}/sendMessage",
                    json={'chat_id': CHANNEL_ID, 'text': caption, 'parse_mode': 'HTML'},
                    timeout=15,
                )

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
