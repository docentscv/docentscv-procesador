#!/usr/bin/env python3
"""DocentsCV · PRUEBA de avisos: manda una notificación y un email de prueba SOLO a los administradores,
para comprobar que todo funciona (claves, Brevo, dispositivos). No avisa a ningún usuario."""
import os, sys, json, urllib.error
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import procesar_pdfs as pp
import avisos

def main():
    psql, lit = pp.psql, pp.lit
    print('=== COMPROBACIÓN DE AVISOS ===')
    adm = [l.split('\t') for l in psql("select au.email, (select count(*) from push_suscripciones s where s.user_id = a.user_id) from admins a join auth.users au on au.id = a.user_id", tuples=True).splitlines() if l.strip()]
    print('administradores:', len(adm))
    for e, n in adm: print('  -', e, '·', n, 'dispositivo(s) con notificaciones')
    vap = psql("select count(*) from config where clave in ('vapid_publica', 'vapid_privada')", tuples=True).strip()
    print('claves de notificaciones (VAPID):', 'OK' if vap == '2' else 'FALTAN (se crean solas en la próxima ejecución normal)')
    tot = psql("select count(*) from push_suscripciones", tuples=True).strip(); em = psql("select count(*) from perfiles where avisos", tuples=True).strip()
    print(f'usuarios con avisos: {tot} dispositivos con notificaciones · {em} personas con avisos por email activados')
    # 1) notificación
    if not any(int(n) > 0 for _, n in adm): print('PUSH: ningún administrador tiene notificaciones activadas. Actívalas en Mi apartado → Ajustes → «Activar notificaciones» en tu móvil y repite la prueba.')
    else:
        n = avisos.avisar_admin(psql, lit, [('Prueba de avisos', True, 'Si lees esto, las notificaciones funcionan')], titulo='🔔 Prueba de avisos de DocentsCV', icono='•')
        print('PUSH:', 'enviada a', n, 'dispositivo(s). Debería llegarte en unos segundos.' if n else 'no se pudo enviar (mira el mensaje de error de arriba).')
    # 2) email (Brevo)
    clave = os.environ.get('BREVO_API_KEY', '').strip().strip('"\'')
    if not clave: print('EMAIL: falta el secreto BREVO_API_KEY en GitHub (Settings → Secrets and variables → Actions).'); return
    remit = os.environ.get('BREVO_SENDER', '').strip() or 'docentscv@gmail.com'
    for e, _ in adm:
        asunto, ch, ct, baja = avisos.construir('2000-01-01', '00000000-0000-0000-0000-000000000000', [{'especialidad': 'PRUEBA · aviso de prueba', 'n': 1, 'nv': 1, 'centros': ['Localidad · Centro de prueba']}])
        asunto = '🧪 PRUEBA de avisos de DocentsCV'
        try: avisos.enviar(clave, remit, e, asunto, ch, ct, baja); print('EMAIL: enviado a', e, '(revisa también la carpeta de spam).')
        except urllib.error.HTTPError as x: print('EMAIL: error de Brevo', x.code, x.read().decode()[:300], '· remitente usado:', remit)
        except Exception as x: print('EMAIL: error', repr(x)[:200])

if __name__ == '__main__': main()
