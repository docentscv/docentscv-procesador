#!/usr/bin/env python3
"""DocentsCV · avisos PERSONALES por Telegram (solo usuarios con acceso activo).

Cada vez que lo ejecuta el procesador (cada pocos minutos):
  1. Lee los mensajes nuevos que han escrito las personas al bot (@docentscv_robot):
       /start CODIGO  → vincula su chat con su cuenta de DocentsCV
       /parar, /reanudar, /ayuda
  2. Si hay plazas ofertadas recientes, avisa a cada persona de las de SUS especialidades (una sola vez por lista).
  3. Una vez por semana (miércoles) le dice cuántas personas tiene delante en cada lista.

Necesita los secretos DATABASE_URL y TELEGRAM_BOT_TOKEN. Si falta alguno, no hace nada y no falla.
"""
import os, re, sys, json, datetime, subprocess, urllib.request, urllib.error, urllib.parse
from zoneinfo import ZoneInfo

WEB = os.environ.get('WEB_URL', 'https://docentscv.github.io/')
TZ = ZoneInfo('Europe/Madrid')
UUID = re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')
MAX_ENVIOS = 400          # por ejecución (Telegram permite unos 30 mensajes por segundo; sobra)


def lit(v): return "'" + str(v).replace("'", "''") + "'"

def psql(sql, tuples=True):
    r = subprocess.run(['psql', os.environ['DATABASE_URL'], '-v', 'ON_ERROR_STOP=1', '-At', '-c', sql], capture_output=True, text=True, timeout=120)
    if r.returncode != 0: raise RuntimeError('psql: ' + r.stderr.strip()[:300])
    return r.stdout

def filas(sql):
    t = psql(f"select coalesce(jsonb_agg(t), '[]'::jsonb) from ({sql}) t").strip()
    return json.loads(t or '[]')

def tg(metodo, **params):
    rq = urllib.request.Request(f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN'].strip()}/{metodo}",
                                data=urllib.parse.urlencode(params).encode())
    with urllib.request.urlopen(rq, timeout=30) as r: return json.loads(r.read())

def fmt(f): return '/'.join(reversed(str(f).split('-')))


# ───────────── textos ─────────────
BIENVENIDA = ("✅ ¡Listo! Ya recibirás en este chat tus avisos personales de DocentsCV.\n\n"
              "Qué te enviaré:\n• Cuando salgan plazas ofertadas de tus especialidades.\n• Cada miércoles, cuántas personas tienes delante en tu lista.\n\n"
              "Para decirte la plaza más próxima a tu casa usamos el domicilio que indicaste en Mi apartado de la web.\n\n"
              "Guardamos tu identificador de chat de Telegram solo para enviarte estos avisos. "
              "Para dejar de recibirlos: /parar. Para borrar tus datos de Telegram: desvincula desde Mi apartado en la web.")
AYUDA = ("Comandos:\n/parar – dejar de recibir avisos\n/reanudar – volver a recibirlos\n\n"
         f"Todo lo demás está en {WEB}")

def texto_ofertas(fecha, r):
    n = r['n']
    t = [f"📢 Plazas ofertadas · adjudicación del {fmt(fecha)}", "",
         f"Hay {n} plaza{'s' if n != 1 else ''} de tus especialidades."]
    if r.get('cercana_centro'):
        k = r.get('cercana_km')
        km = '' if k is None else (', en tu misma zona' if k < 3 else f', a unos {int(k)} km')
        t.append(f"📍 La plaza más próxima a tu casa ({r['casa_localidad']}) es {r['cercana_centro']} ({r['cercana_localidad']}){km}.")
    elif not r.get('tiene_casa'):
        if r.get('ejemplo_centro'): t.append(f"Por ejemplo: {r['ejemplo_centro']} ({r['ejemplo_localidad']}).")
        t.append("Indica tu localidad en Mi apartado y te diré cuál te queda más cerca.")
    if r.get('favoritos'):
        t.append(f"⭐ Hay plaza en centros favoritos tuyos: {r['favoritos']}.")
    t += ["", "⏰ Tienes hasta las 02:00 h (a.m.) del día de la adjudicación para presentar o modificar tu solicitud.", "", f"Verlas todas: {WEB}"]
    return '\n'.join(t)

def texto_delante(filas_u, fecha):
    t = ["📊 Tu posición esta semana", ""]
    for r in filas_u:
        d = r['delante']
        t.append(f"• {r['lista']}: tienes {d} persona{'s' if d != 1 else ''} delante de ti (tu puesto: {r['posicion']}).")
    t += ["", f"Según la última adjudicación continua ({fmt(fecha)}). No cuento a quien ya tiene plaza o está desactivado.", f"Detalle en {WEB}"]
    return '\n'.join(t)


# ───────────── envío con control de errores ─────────────
def enviar(chat_id, texto, enviar_fn=None):
    """True si se envió. Si la persona ha bloqueado el bot (403), se desactiva y no se reintenta."""
    try:
        (enviar_fn or (lambda c, t: tg('sendMessage', chat_id=c, text=t, disable_web_page_preview='true')))(chat_id, texto)
        return True
    except urllib.error.HTTPError as e:
        if e.code in (400, 403):
            psql(f"select telegram_parar({int(chat_id)}, false)")
            print(f'telegram: chat {chat_id} no acepta mensajes ({e.code}); se desactiva')
        else:
            print('telegram: error', e.code)
    except Exception as e:
        print('telegram: error', repr(e)[:150])
    return False


# ───────────── 1) mensajes que llegan al bot ─────────────
def procesar_mensajes(enviar_fn=None, leer_fn=None):
    off = (psql("select valor from config where clave = 'tg_offset'") or '').strip()
    pedir = leer_fn or (lambda o: tg('getUpdates', offset=o, timeout=0, allowed_updates=json.dumps(['message']))['result'])
    ups = pedir(int(off) if off.isdigit() else 0)
    if not ups: return 0
    for u in ups:
        m = u.get('message') or {}
        txt = (m.get('text') or '').strip(); chat = (m.get('chat') or {}).get('id')
        if not txt or not chat or (m.get('chat') or {}).get('type') != 'private': continue
        cmd, _, arg = txt.partition(' '); cmd = cmd.split('@')[0].lower(); arg = arg.strip()
        if cmd == '/start':
            if UUID.match(arg) and psql(f"select telegram_vincular({lit(arg)}::uuid, {int(chat)})").strip() == 't':
                enviar(chat, BIENVENIDA, enviar_fn)
            else:
                enviar(chat, "Para vincular tu cuenta, pulsa el botón «Recibir avisos en Telegram» dentro de Mi apartado en " + WEB, enviar_fn)
        elif cmd == '/parar':
            psql(f"select telegram_parar({int(chat)}, false)"); enviar(chat, 'Hecho: no recibirás más avisos. Cuando quieras volver: /reanudar', enviar_fn)
        elif cmd == '/reanudar':
            psql(f"select telegram_parar({int(chat)}, true)"); enviar(chat, 'Hecho: volverás a recibir tus avisos.', enviar_fn)
        else:
            enviar(chat, AYUDA, enviar_fn)
    psql(f"insert into config(clave, valor) values ('tg_offset', {lit(ups[-1]['update_id'] + 1)}) on conflict (clave) do update set valor = excluded.valor")
    return len(ups)


# ───────────── 2) plazas ofertadas ─────────────
def avisar_ofertas(enviar_fn=None, hoy=None):
    hoy = hoy or datetime.datetime.now(TZ).date()
    f = (psql("select max(fecha) from plazas_ofertadas") or '').strip()
    if not f: return 0
    if f < (hoy - datetime.timedelta(days=1)).isoformat(): print('telegram: ofertas antiguas, no se avisa'); return 0
    n = 0
    for r in filas(f"select * from telegram_pendientes_ofertas({lit(f)}::date)")[:MAX_ENVIOS]:
        if enviar(r['chat_id'], texto_ofertas(f, r), enviar_fn):
            psql(f"insert into telegram_enviados(user_id, tipo, clave) values ({lit(r['user_id'])}, 'ofertas', {lit(f)}) on conflict do nothing"); n += 1
    print(f'telegram: {n} aviso(s) de plazas enviados'); return n


# ───────────── 3) personas delante (miércoles) ─────────────
def avisar_delante(enviar_fn=None, ahora=None, forzar=False):
    ahora = ahora or datetime.datetime.now(TZ)
    if not forzar and not (ahora.weekday() == 2 and ahora.hour >= 9): return 0
    iso = ahora.isocalendar(); clave = f'{iso[0]}-W{iso[1]:02d}'
    por = {}
    for r in filas(f"select * from telegram_pendientes_delante({lit(clave)})"): por.setdefault(r['user_id'], []).append(r)
    n = 0
    for uid, fs in list(por.items())[:MAX_ENVIOS]:
        if enviar(fs[0]['chat_id'], texto_delante(fs, fs[0]['fecha']), enviar_fn):
            psql(f"insert into telegram_enviados(user_id, tipo, clave) values ({lit(uid)}, 'delante', {lit(clave)}) on conflict do nothing"); n += 1
    if n: print(f'telegram: {n} aviso(s) de posición enviados')
    return n


def main():
    if not os.environ.get('DATABASE_URL') or not os.environ.get('TELEGRAM_BOT_TOKEN'):
        print('telegram personal: faltan DATABASE_URL o TELEGRAM_BOT_TOKEN; no se hace nada'); return 0
    if psql("select to_regclass('public.telegram_vinculos')").strip() == '':
        print('telegram personal: aún no se ha ejecutado telegram_avisos.sql; no se hace nada'); return 0
    rc = 0
    for paso in (procesar_mensajes, avisar_ofertas, avisar_delante):
        try: paso()
        except Exception as e: print(f'telegram personal: error en {paso.__name__}: {repr(e)[:200]}'); rc = 1
    return rc

if __name__ == '__main__': sys.exit(main())
