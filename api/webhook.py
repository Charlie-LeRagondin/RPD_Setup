"""
Webhook Telegram — /setup, /start + callbacks inline keyboard (Prompt 2)
Hébergé sur Vercel (fonction serverless, limite 10 s d'exécution).
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

def _send(chat_id, text, parse_mode='HTML', reply_markup=None, thread_id=None, reply_to=None):
    payload = {'chat_id': chat_id, 'text': text, 'parse_mode': parse_mode}
    if reply_markup:
        payload['reply_markup'] = reply_markup
    if thread_id:
        payload['message_thread_id'] = thread_id
    if reply_to:
        payload['reply_to_message_id'] = reply_to
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

def _edit_message_text(chat_id, message_id, text, has_photo, keyboard=None):
    """Édite le texte/caption d'un message, et optionnellement son clavier."""
    method  = 'editMessageCaption' if has_photo else 'editMessageText'
    key     = 'caption'            if has_photo else 'text'
    payload = {'chat_id': chat_id, 'message_id': message_id,
               key: text, 'parse_mode': 'HTML'}
    if keyboard is not None:
        payload['reply_markup'] = keyboard
    try:
        req.post(f"{TG}/{method}", json=payload, timeout=5)
    except Exception:
        pass

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

def _parse_r_float(r_str: str) -> float:
    m = re.search(r'\+([\d.]+)R', r_str)
    return float(m.group(1)) if m else 0.0

def _calc_be_r(setup: dict) -> float:
    """Retourne le R de sortie selon le be_type configuré."""
    be_type = setup.get('be_type', 'MANUEL')
    ep      = setup.get('entry_price')
    sl      = setup.get('sl_value')
    dir_    = setup.get('direction', 'LONG')
    tps     = setup.get('tps', [])
    if not ep or not sl:
        return 0.0
    if be_type == 'MANUEL':
        return 0.0
    elif be_type == 'AUTO_TP1' and tps:
        return _parse_r_float(_calc_r(tps[0], ep, sl, dir_))
    elif be_type == 'AUTO_TP2' and len(tps) >= 2:
        return _parse_r_float(_calc_r(tps[1], ep, sl, dir_))
    elif be_type == 'PRIX_SPECIFIQUE':
        bv = setup.get('be_value')
        if bv:
            return _parse_r_float(_calc_r(bv, ep, sl, dir_))
    return 0.0

# ── Keyboard builder ──────────────────────────────────────────────────────────

def _build_keyboard(setup_id, setup) -> dict:
    rows = []
    sid  = str(setup_id)
    be_triggered = setup.get('be_triggered_by_tp')

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
                if hit:
                    # encode :be si ce TP a déclenché le BE auto
                    be_sfx = ':be' if (be_triggered == n) else ''
                    row.append({
                        'text':          f'⚠️ TP{n} CANCEL',
                        'callback_data': f'tp_cancel:{sid}:{n}{be_sfx}',
                    })
                else:
                    row.append({
                        'text':          f'🎯 TP{n}',
                        'callback_data': f'tp_hit:{sid}:{n}',
                    })
        rows.append(row)
        i += 2

    # Ligne BE
    if setup.get('be_passed'):
        rows.append([
            {'text': '⚠️ CANCEL BE',   'callback_data': f'be_cancel:{sid}'},
            {'text': '🛡 Clôture BE', 'callback_data': f'close_be:{sid}'},
        ])
    else:
        rows.append([{'text': '🛡 Passage BE', 'callback_data': f'be_pass:{sid}'}])

    rows.append([
        {'text': '❌ SL',       'callback_data': f'sl_hit:{sid}'},
        {'text': '🛠 Clôturer', 'callback_data': f'close:{sid}'},
    ])
    return {'inline_keyboard': rows}

# ── Parse setup depuis le message Telegram ────────────────────────────────────

def _parse_message(msg: dict) -> dict:
    text      = msg.get('text') or msg.get('caption', '')
    has_photo = bool(msg.get('photo'))

    # setup_id + style
    m        = re.search(r'#SETUP_(\d+) \| (.+)', text)
    setup_id = m.group(1) if m else None
    style_str = m.group(2).strip() if m else ''

    # username (avant " | ") + date (après "📅 ")
    m        = re.search(r'💼 (.+?) \|', text)
    username = m.group(1).strip() if m else 'inconnu'
    m        = re.search(r'📅 (.+)', text)
    date_str = m.group(1).strip() if m else ''

    # direction + actif
    direction = 'LONG' if '(LONG)' in text else 'SHORT'
    m         = re.search(r'\$(\S+)', text)
    actif     = m.group(1) if m else ''

    # entry
    mkt = re.search(r'Entr[eé]e : MARKET à ([\d.]+)', text)
    if mkt:
        entry_type         = 'MARKET'
        entry_market_price = mkt.group(1)
        entry_price        = float(entry_market_price)
        peh_val = peb_val  = None
    else:
        entry_market_price = None
        pm_peh = re.search(r'PEH : ([\d.]+)', text)
        pm_peb = re.search(r'PEB : ([\d.]+)', text)
        peh_val = pm_peh.group(1) if pm_peh else None
        peb_val = pm_peb.group(1) if pm_peb else None
        if peh_val and peb_val:
            entry_type  = 'PEH_PEB'
            entry_price = (float(peh_val) + float(peb_val)) / 2
        elif peh_val:
            entry_type  = 'PEH'
            entry_price = float(peh_val)
        else:
            entry_type  = 'MARKET'
            entry_price = None

    # SL
    m        = re.search(r'SL : ([\d.]+)', text)
    sl_value = m.group(1) if m else None

    # TPs
    tps = re.findall(r'TP\d+ : ([\d.]+)', text)

    # BE config
    m       = re.search(r'🛡 BE : (.+)', text)
    be_raw  = (m.group(1).strip() if m else 'Manuel').replace(' ✅', '').strip()
    if be_raw == 'Manuel':
        be_type = 'MANUEL';          be_value = None
    elif be_raw == 'TP1':
        be_type = 'AUTO_TP1';        be_value = None
    elif be_raw == 'TP2':
        be_type = 'AUTO_TP2';        be_value = None
    else:
        be_type = 'PRIX_SPECIFIQUE'; be_value = be_raw

    # Commentaire
    m       = re.search(r'📝 (.+)', text)
    comment = m.group(1).strip() if m else None

    # État depuis le clavier
    keyboard   = (msg.get('reply_markup') or {}).get('inline_keyboard', [])
    peh_hit    = False
    peb_hit    = False
    tp_states  = {}
    be_passed  = False
    be_triggered_by_tp = None

    for row in keyboard:
        for btn in row:
            cbd = btn.get('callback_data', '')
            if cbd.startswith('peh_cancel'):
                peh_hit = True
            elif cbd.startswith('peb_cancel'):
                peb_hit = True
            elif cbd.startswith(('tp_cancel', 'tp_hit')):
                p = cbd.split(':')
                if len(p) >= 3:
                    n            = int(p[2])
                    tp_states[n] = (p[0] == 'tp_cancel')
                    if p[0] == 'tp_cancel' and len(p) == 4 and p[3] == 'be':
                        be_triggered_by_tp = n
            elif cbd.startswith(('be_cancel', 'close_be')):
                be_passed = True

    tp_hit = [tp_states.get(i + 1, False) for i in range(len(tps))]
    closed = bool(re.search(r'CLÔTURE', text)) and not bool(keyboard)

    return {
        'setup_id':          setup_id,
        'creator_id':        None,
        'style_str':         style_str,
        'username':          username,
        'date_str':          date_str,
        'direction':         direction,
        'actif':             actif,
        'entry_type':        entry_type,
        'entry_price':       entry_price,
        'entry_market_price':entry_market_price,
        'peh_val':           peh_val,
        'peb_val':           peb_val,
        'sl_value':          sl_value,
        'tps':               tps,
        'be_type':           be_type,
        'be_value':          be_value,
        'comment':           comment,
        'peh_hit':           peh_hit,
        'peb_hit':           peb_hit,
        'tp_hit':            tp_hit,
        'be_passed':         be_passed,
        'be_triggered_by_tp':be_triggered_by_tp,
        'closed':            closed,
        'chat_id':           msg.get('chat', {}).get('id'),
        'message_id':        msg.get('message_id'),
        'has_photo':         has_photo,
    }

# ── Rendu HTML du message ─────────────────────────────────────────────────────

def _render_message(setup: dict, closure: dict = None) -> str:
    dir_badge = '🟢' if setup['direction'] == 'LONG' else '🔴'

    lines = [
        f"🛠 #SETUP_{setup['setup_id']} | {setup['style_str']}",
        f"🧑‍💼 {setup['username']} | 📅 {setup['date_str']}",
        f"{dir_badge} ${setup['actif']} {dir_badge} ({setup['direction']})",
        "",
    ]

    # Entrée
    et = setup['entry_type']
    if et == 'MARKET':
        lines.append(f"➡️ Entrée : MARKET à {setup.get('entry_market_price', '')}")
    elif et == 'PEH':
        pv = setup.get('peh_val', '')
        lines.append(f"➡️ <s>PEH : {pv}</s> ✅" if setup['peh_hit'] else f"➡️ PEH : {pv}")
    elif et == 'PEH_PEB':
        pv = setup.get('peh_val', '')
        bv = setup.get('peb_val', '')
        lines.append(f"➡️ <s>PEH : {pv}</s> ✅" if setup['peh_hit'] else f"➡️ PEH : {pv}")
        lines.append(f"➡️ <s>PEB : {bv}</s> ✅" if setup['peb_hit'] else f"➡️ PEB : {bv}")
    lines.append("")

    # TPs
    ep = setup.get('entry_price')
    sl = setup.get('sl_value')
    for i, tp_val in enumerate(setup['tps']):
        n     = i + 1
        r_str = _calc_r(tp_val, ep, sl, setup['direction']) if (ep and sl) else ''
        hit   = setup['tp_hit'][i] if i < len(setup['tp_hit']) else False
        lines.append(f"🎯 TP{n} : {tp_val}{r_str}{' ✅' if hit else ''}")
    lines.append("")

    # SL
    sl_val = setup.get('sl_value', '')
    lines.append(f"❌ <s>SL : {sl_val}</s>" if setup.get('be_passed') else f"❌ SL : {sl_val}")

    # BE config
    be_map     = {'MANUEL': 'Manuel', 'AUTO_TP1': 'TP1', 'AUTO_TP2': 'TP2'}
    be_display = be_map.get(setup.get('be_type', 'MANUEL')) or setup.get('be_value') or 'Manuel'
    be_sfx     = ' ✅' if setup.get('be_passed') else ''
    lines.append(f"🛡 BE : {be_display}{be_sfx}")

    # Commentaire
    if setup.get('comment'):
        lines.append("")
        lines.append(f"📝 {setup['comment']}")

    # Bloc de clôture
    if closure:
        lines.append("")
        c_type = closure.get('type', '')
        if c_type == 'SL':
            lines.append("❌ CLÔTURE : SL (-1.0R)")
        elif c_type == 'BE':
            r_val = closure.get('r_val', 0.0)
            lines.append(f"🛡 CLÔTURE : BE ({r_val:+.1f}R)")
        elif c_type == 'MANUELLE':
            lines.append("🛠 CLÔTURE : MANUELLE")
        if closure.get('max_tp_line'):
            lines.append(f"🏆 Max atteint : {closure['max_tp_line']}")

    return "\n".join(lines)

# ── Max TP atteint ────────────────────────────────────────────────────────────

def _max_tp_str(setup: dict):
    tp_hit = setup.get('tp_hit', [])
    tps    = setup.get('tps', [])
    ep     = setup.get('entry_price')
    sl     = setup.get('sl_value')
    last   = None
    for i, hit in enumerate(tp_hit):
        if hit:
            last = i
    if last is None:
        return None
    n     = last + 1
    tp_v  = tps[last] if last < len(tps) else None
    r_str = _calc_r(tp_v, ep, sl, setup['direction']) if (tp_v and ep and sl) else ''
    return f"TP{n}{r_str}"

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

    setup = _parse_message(msg)
    if not setup.get('setup_id'):
        _answer_cb(query_id, "❌ Setup introuvable")
        return

    if setup.get('closed'):
        _answer_cb(query_id, "❌ Setup déjà clôturé")
        return

    if not _is_authorized(user_id, setup):
        _answer_cb(query_id, "❌ Réservé au créateur du setup et aux admins")
        return

    setup_id   = setup['setup_id']
    chat_id    = setup['chat_id']
    message_id = setup['message_id']
    has_photo  = setup['has_photo']
    NO_KB      = {'inline_keyboard': []}

    def _commit(notif, keyboard=None):
        """Édite le message + envoie la notif en reply."""
        kb = _build_keyboard(setup_id, setup) if keyboard is None else keyboard
        _edit_message_text(chat_id, message_id, _render_message(setup), has_photo, keyboard=kb)
        _send(chat_id, notif, thread_id=PUBLISH_TOPIC_ID, reply_to=message_id)
        _answer_cb(query_id)

    def _commit_closure(closure, notif):
        txt = _render_message(setup, closure=closure)
        _edit_message_text(chat_id, message_id, txt, has_photo, keyboard=NO_KB)
        _send(chat_id, notif, thread_id=PUBLISH_TOPIC_ID, reply_to=message_id)
        _answer_cb(query_id)

    # ── PEH ──────────────────────────────────────────────────────────────────
    if action == 'peh_in':
        setup['peh_hit'] = True
        _commit(f"✅ #SETUP_{setup_id} | PEH IN ✅")

    elif action == 'peh_cancel':
        setup['peh_hit'] = False
        _commit(f"⚠️ #SETUP_{setup_id} | CORRECTION : PEH IN annulé")

    # ── PEB ──────────────────────────────────────────────────────────────────
    elif action == 'peb_in':
        setup['peb_hit'] = True
        _commit(f"✅ #SETUP_{setup_id} | PEB IN ✅")

    elif action == 'peb_cancel':
        setup['peb_hit'] = False
        _commit(f"⚠️ #SETUP_{setup_id} | CORRECTION : PEB IN annulé")

    # ── TP ────────────────────────────────────────────────────────────────────
    elif action in ('tp_hit', 'tp_cancel'):
        n      = int(parts[2]) if len(parts) > 2 else 1
        idx    = n - 1
        be_sfx = parts[3] if len(parts) > 3 else None

        if idx < 0 or idx >= len(setup['tp_hit']):
            _answer_cb(query_id, "❌ TP invalide")
            return

        is_hit               = (action == 'tp_hit')
        setup['tp_hit'][idx] = is_hit

        # R pour la notif
        ep    = setup.get('entry_price')
        sl    = setup.get('sl_value')
        tp_v  = setup['tps'][idx] if idx < len(setup['tps']) else None
        r_str = _calc_r(tp_v, ep, sl, setup['direction']) if (ep and sl and tp_v) else ''

        # BE auto
        be_type    = setup.get('be_type', 'MANUEL')
        auto_tp_n  = 1 if be_type == 'AUTO_TP1' else (2 if be_type == 'AUTO_TP2' else None)
        be_auto_on = be_auto_off = False

        if is_hit and auto_tp_n == n and not setup.get('be_passed'):
            setup['be_passed']          = True
            setup['be_triggered_by_tp'] = n
            be_auto_on = True
        elif not is_hit and (be_sfx == 'be' or setup.get('be_triggered_by_tp') == n):
            setup['be_passed']          = False
            setup['be_triggered_by_tp'] = None
            be_auto_off = True

        if is_hit:
            notif = (f"✅ #SETUP_{setup_id} | TP{n}{r_str} ✅ + BE AUTO 🛡"
                     if be_auto_on else
                     f"✅ #SETUP_{setup_id} | TP{n}{r_str} ✅")
        else:
            notif = (f"⚠️ #SETUP_{setup_id} | CORRECTION : TP{n} + BE AUTO annulés"
                     if be_auto_off else
                     f"⚠️ #SETUP_{setup_id} | CORRECTION : TP{n} annulé")

        _commit(notif)

    # ── Passage BE manuel ─────────────────────────────────────────────────────
    elif action == 'be_pass':
        setup['be_passed'] = True
        _commit(f"🛡 #SETUP_{setup_id} | PASSAGE BE")

    elif action == 'be_cancel':
        setup['be_passed']          = False
        setup['be_triggered_by_tp'] = None
        _commit(f"⚠️ #SETUP_{setup_id} | CORRECTION : Passage BE annulé")

    # ── SL (logique selon état BE) ────────────────────────────────────────────
    elif action == 'sl_hit':
        max_tp = _max_tp_str(setup)
        if setup.get('be_passed'):
            r_val = _calc_be_r(setup)
            _commit_closure(
                {'type': 'BE', 'r_val': r_val, 'max_tp_line': max_tp},
                f"🛡 #SETUP_{setup_id} | CLÔTURE : BE ({r_val:+.1f}R)"
            )
        else:
            _commit_closure(
                {'type': 'SL', 'max_tp_line': max_tp},
                f"❌ #SETUP_{setup_id} | CLÔTURE : SL (-1.0R)"
            )

    # ── Clôture manuelle ──────────────────────────────────────────────────────
    elif action == 'close':
        max_tp = _max_tp_str(setup)
        _commit_closure(
            {'type': 'MANUELLE', 'max_tp_line': max_tp},
            f"🛠 #SETUP_{setup_id} | CLÔTURE : MANUELLE"
        )

    # ── Clôture BE ────────────────────────────────────────────────────────────
    elif action == 'close_be':
        max_tp = _max_tp_str(setup)
        r_val  = _calc_be_r(setup)
        _commit_closure(
            {'type': 'BE', 'r_val': r_val, 'max_tp_line': max_tp},
            f"🛡 #SETUP_{setup_id} | CLÔTURE : BE ({r_val:+.1f}R)"
        )

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
