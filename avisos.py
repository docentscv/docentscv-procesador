"""Avisos por email (Brevo) cuando se cargan las plazas ofertadas de un día.
Se envía como mucho MAXIMO emails por ejecución (el plan gratuito de Brevo permite 300 al día);
quien no llegue recibirá el aviso en la siguiente ejecución (cada 15 min)."""
import os, json, html, base64, urllib.request, urllib.error, datetime

WEB = 'https://docentscv.github.io/'
MAXIMO = 250

def fmt(f): return '/'.join(reversed(f.split('-')))

def construir(fecha, token, filas):
    tot = sum(r['n'] for r in filas)
    asunto = f'{tot} plaza{"s" if tot != 1 else ""} ofertada{"s" if tot != 1 else ""} en tus bolsas · adjudicación del {fmt(fecha)}'
    baja = f'{WEB}#baja={token}'
    h, t = [], []
    for r in filas:
        cen = r['centros'] or []
        lista = ''.join(f'<li>{html.escape(c)}</li>' for c in cen[:8]) + (f'<li>… y {len(cen) - 8} centros más</li>' if len(cen) > 8 else '')
        h.append(f'<p style="margin:16px 0 4px"><b>{html.escape(r["especialidad"])}</b> — {r["n"]} plaza{"s" if r["n"] != 1 else ""} ({r["nv"]} vacante{"s" if r["nv"] != 1 else ""})</p><ul style="margin:0 0 0 18px;padding:0">{lista}</ul>')
        t.append(f'{r["especialidad"]}: {r["n"]} plaza{"s" if r["n"] != 1 else ""} ({r["nv"]} vacante{"s" if r["nv"] != 1 else ""})\n' + '\n'.join('  - ' + c for c in cen[:8]) + (f'\n  … y {len(cen) - 8} centros más' if len(cen) > 8 else ''))
    cuerpo_h = (f'<div style="font-family:Arial,sans-serif;font-size:15px;color:#1a2433;max-width:560px">'
                f'<h2 style="margin:0 0 8px">Plazas ofertadas el {fmt(fecha)}</h2>'
                f'<p style="margin:0">La Conselleria ha publicado las plazas que se adjudicarán ese día. Estas son las de tus bolsas:</p>{"".join(h)}'
                f'<p style="margin:20px 0 6px"><a href="{WEB}" style="background:#0F7B8A;color:#fff;padding:10px 16px;border-radius:8px;text-decoration:none;display:inline-block">Ver mis oportunidades</a></p>'
                f'<p style="font-size:12px;color:#667;margin-top:24px">Recibes este aviso porque lo tienes activado en Mi apartado de DocentsCV. <a href="{baja}">Dejar de recibir avisos</a>.</p></div>')
    cuerpo_t = (f'Plazas ofertadas el {fmt(fecha)}\n\nLa Conselleria ha publicado las plazas que se adjudicarán ese día. Estas son las de tus bolsas:\n\n' + '\n\n'.join(t) +
                f'\n\nVer mis oportunidades: {WEB}\n\nPara dejar de recibir avisos: {baja}\n')
    return asunto, cuerpo_h, cuerpo_t, baja

def enviar(api_key, remitente, destino, asunto, cuerpo_h, cuerpo_t, baja):
    datos = {'sender': {'name': 'DocentsCV', 'email': remitente}, 'to': [{'email': destino}], 'subject': asunto,
             'htmlContent': cuerpo_h, 'textContent': cuerpo_t, 'headers': {'List-Unsubscribe': f'<{baja}>'}}
    rq = urllib.request.Request('https://api.brevo.com/v3/smtp/email', data=json.dumps(datos).encode(),
                                headers={'api-key': api_key, 'content-type': 'application/json', 'accept': 'application/json'})
    with urllib.request.urlopen(rq, timeout=30) as r: return r.status

def avisar_ofertas(psql, lit, fecha, enviar_fn=enviar):
    clave = os.environ.get('BREVO_API_KEY', '').strip().strip('"\'')
    if not clave: print('avisos: falta el secreto BREVO_API_KEY; no se envía nada'); return 0
    if fecha < (datetime.date.today() - datetime.timedelta(days=1)).isoformat(): print('avisos: oferta antigua, no se avisa'); return 0
    remitente = os.environ.get('BREVO_SENDER', '').strip() or 'docentscv@gmail.com'
    txt = psql(f"select coalesce(jsonb_agg(t), '[]'::jsonb) from avisos_ofertas_pendientes({lit(fecha)}) t", tuples=True).strip()
    por = {}
    for r in json.loads(txt or '[]'): por.setdefault(r['user_id'], []).append(r)
    enviados = 0
    for uid, filas in list(por.items())[:MAXIMO]:
        asunto, ch, ct, baja = construir(fecha, filas[0]['token'], filas)
        try: enviar_fn(clave, remitente, filas[0]['email'], asunto, ch, ct, baja)
        except urllib.error.HTTPError as e:
            print('avisos: error de Brevo', e.code, e.read().decode()[:200]); break
        except Exception as e: print('avisos: error', repr(e)[:200]); break
        psql(f"update perfiles set avisos_fecha = {lit(fecha)} where id = {lit(uid)}"); enviados += 1
    print(f'avisos: {enviados} enviado(s), {max(len(por) - enviados, 0)} pendiente(s)')
    return enviados


# ---------- notificaciones push ----------
def asegurar_vapid(psql, lit):
    """Crea (una sola vez) el par de claves de las notificaciones push y lo guarda en la tabla config."""
    ya = psql("select count(*) from config where clave in ('vapid_publica', 'vapid_privada')", tuples=True).strip()
    if ya == '2': return False
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization as ser
    k = ec.generate_private_key(ec.SECP256R1())
    pem = k.private_bytes(ser.Encoding.PEM, ser.PrivateFormat.PKCS8, ser.NoEncryption()).decode()
    pub = base64.urlsafe_b64encode(k.public_key().public_bytes(ser.Encoding.X962, ser.PublicFormat.UncompressedPoint)).rstrip(b'=').decode()
    psql(f"insert into config(clave, valor) values ('vapid_publica', {lit(pub)}), ('vapid_privada', {lit(pem)}) on conflict (clave) do nothing")
    print('push: claves VAPID creadas')
    return True

def enviar_push(clave_pem, sub, carga, remitente):
    from pywebpush import webpush, WebPushException
    from py_vapid import Vapid
    webpush(subscription_info=sub, data=json.dumps(carga), vapid_private_key=Vapid.from_pem(clave_pem.encode()),
            vapid_claims={'sub': 'mailto:' + remitente}, ttl=60 * 60 * 24)

def avisar_push(psql, lit, fecha, enviar_fn=None):
    if fecha < (datetime.date.today() - datetime.timedelta(days=1)).isoformat(): print('push: oferta antigua, no se avisa'); return 0
    remitente = os.environ.get('BREVO_SENDER', '').strip() or 'docentscv@gmail.com'
    if enviar_fn is None:
        pem = psql("select valor from config where clave = 'vapid_privada'", tuples=True)
        if not pem.strip(): print('push: aún no hay claves'); return 0
        enviar_fn = lambda sub, carga: enviar_push(pem, sub, carga, remitente)
    txt = psql(f"select coalesce(jsonb_agg(t), '[]'::jsonb) from push_ofertas_pendientes({lit(fecha)}) t", tuples=True).strip()
    filas = json.loads(txt or '[]'); usuarios, quitar = {}, []
    for r in filas: usuarios.setdefault(r['user_id'], []).append(r)
    ok = 0
    for uid, subs in usuarios.items():
        r0 = subs[0]
        carga = {'title': f'Plazas ofertadas el {fmt(fecha)}',
                 'body': f'{r0["n"]} plaza{"s" if r0["n"] != 1 else ""} en tus bolsas ({r0["nv"]} vacante{"s" if r0["nv"] != 1 else ""}). Toca para ver tu mejor oportunidad.',
                 'url': '/', 'tag': 'ofertas-' + fecha}
        enviado = False
        for s in subs:
            try:
                enviar_fn({'endpoint': s['endpoint'], 'keys': {'p256dh': s['p256dh'], 'auth': s['auth']}}, carga); enviado = True
            except Exception as e:
                cod = getattr(getattr(e, 'response', None), 'status_code', None)
                if cod in (404, 410): quitar.append(s['endpoint'])       # el dispositivo ya no existe
                else: print('push: error', cod, repr(e)[:120])
        if enviado:
            psql(f"update perfiles set push_fecha = {lit(fecha)} where id = {lit(uid)}"); ok += 1
    for ep in quitar: psql(f"delete from push_suscripciones where endpoint = {lit(ep)}")
    print(f'push: {ok} usuario(s) avisado(s), {len(quitar)} dispositivo(s) retirado(s)')
    return ok


def avisar_admin(psql, lit, resultados, enviar_fn=None):
    """Una notificación para los administradores con el resultado de las cargas de esta ejecución.
    resultados: lista de (nombre, True/False, detalle)."""
    if not resultados: return 0
    remitente = os.environ.get('BREVO_SENDER', '').strip() or 'docentscv@gmail.com'
    if enviar_fn is None:
        pem = psql("select valor from config where clave = 'vapid_privada'", tuples=True)
        if not pem.strip(): return 0
        enviar_fn = lambda sub, carga: enviar_push(pem, sub, carga, remitente)
    bien = [r for r in resultados if r[1]]; mal = [r for r in resultados if not r[1]]
    if mal: titulo = f'❌ Error al cargar {len(mal)} PDF' + ('s' if len(mal) != 1 else '')
    else: titulo = f'✅ {len(bien)} PDF cargado' + ('s' if len(bien) != 1 else '')
    lineas = [f'{"✅" if ok else "❌"} {n}' + (f' · {str(d).splitlines()[0][:60]}' if d else '') for n, ok, d in resultados]
    carga = {'title': titulo, 'body': '\n'.join(lineas)[:300], 'url': '/', 'tag': 'admin-carga'}
    filas = json.loads(psql("select coalesce(jsonb_agg(t), '[]'::jsonb) from (select s.endpoint, s.p256dh, s.auth from push_suscripciones s join admins a on a.user_id = s.user_id) t", tuples=True).strip() or '[]')
    ok, quitar = 0, []
    for s in filas:
        try: enviar_fn({'endpoint': s['endpoint'], 'keys': {'p256dh': s['p256dh'], 'auth': s['auth']}}, carga); ok += 1
        except Exception as e:
            cod = getattr(getattr(e, 'response', None), 'status_code', None)
            if cod in (404, 410): quitar.append(s['endpoint'])
            else: print('push admin: error', cod, repr(e)[:120])
    for ep in quitar: psql(f"delete from push_suscripciones where endpoint = {lit(ep)}")
    print(f'push admin: {ok} dispositivo(s) avisado(s)')
    return ok
