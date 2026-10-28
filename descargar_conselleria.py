#!/usr/bin/env python3
"""DocentsCV · Busca en la web de la Conselleria los PDF nuevos, los guarda en el almacén 'pdfs' y los
deja como 'pendiente' en importaciones (igual que cuando los subes tú a mano). Luego procesar_pdfs.py los carga.

Seguridad:
  · Sin AUTO_DESCARGA=1 solo SIMULA (escribe lo que haría, no descarga ni guarda nada).
  · Solo PDF de los últimos DIAS_ATRAS días y que no estén ya registrados (mismo nombre y tipo).
  · En las ejecuciones programadas solo actúa 1 vez por hora, de lunes a viernes, de 07:00 a 21:00 (hora de Madrid).
  · Se identifica con un nombre propio (no se hace pasar por un navegador)."""
import os, re, sys, datetime, urllib.request, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import buscar_pdfs as bp

DIAS_ATRAS = 8
# (dirección, tipo de PDF que sale en esa página) ; en cada página solo nos interesan los nombres indicados
PAGINAS = [
    ('https://ceice.gva.es/es/web/rrhh-educacion/convocatoria-y-peticion-telematica',  {'pue': 'ofertas'}),
    ('https://ceice.gva.es/es/web/rrhh-educacion/resolucion',                           {'lis': 'continua', 'pue': 'ofertas'}),
    ('https://ceice.gva.es/es/web/rrhh-educacion/convocatoria-y-peticion-telematica6', {'pue': 'dificil'}),
    ('https://ceice.gva.es/es/web/rrhh-educacion/resolucion1',                          {'pue': 'dificil'}),
]
NOM = re.compile(r'^(\d{2})(\d{2})(\d{2})_(lis_mae|lis_sec|pue_prov|pue_def)\.pdf$', re.I)


def toca_ahora(ahora=None):
    """Para las ejecuciones programadas: una vez por hora, entre semana y en horario de trabajo."""
    if os.environ.get('GITHUB_EVENT_NAME', 'schedule') == 'workflow_dispatch': return True
    try:
        import zoneinfo; n = (ahora or datetime.datetime.now(zoneinfo.ZoneInfo('Europe/Madrid')))
    except Exception: n = ahora or datetime.datetime.utcnow() + datetime.timedelta(hours=1)
    return n.weekday() < 5 and 7 <= n.hour <= 21 and n.minute < 15


def candidatos(hoy=None):
    """[(nombre, url, tipo, cuerpo)] de los PDF recientes que reconocemos."""
    hoy = hoy or datetime.date.today(); lim = hoy - datetime.timedelta(days=DIAS_ATRAS); out, vistos = [], set()
    for pag, reglas in PAGINAS:
        try: st, t = bp.leer(pag)
        except Exception as e: print('  no se pudo leer', pag, '->', e); continue
        for u in bp.enlaces_pdf(t, pag):
            n = bp.nombre_de(u); m = NOM.match(n)
            if not m: continue
            try: f = datetime.date(2000 + int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError: continue
            if f < lim or f > hoy + datetime.timedelta(days=2): continue
            clase = m.group(4).lower().split('_')[0]          # 'lis' o 'pue'
            tipo = reglas.get(clase)
            if tipo == 'dificil' and m.group(4).lower() == 'pue_prov': continue    # el provisional de difícil cobertura lleva la fecha del día anterior dentro del PDF; solo cargamos el definitivo
            if not tipo or (n, tipo) in vistos: continue
            vistos.add((n, tipo))
            cuerpo = ('maestros' if n.lower().endswith('_lis_mae.pdf') else 'otros') if tipo == 'continua' else 'todos'
            out.append((n, u, tipo, cuerpo))
    return out


def main(psql, lit, limpiar):
    if not toca_ahora(): print('Descarga automática: ahora no toca (se hace 1 vez por hora, de lunes a viernes).'); return 0
    real = os.environ.get('AUTO_DESCARGA', '').strip() == '1'
    print('Descarga automática desde la Conselleria ·', 'REAL' if real else 'SIMULACIÓN (no guarda nada)')
    nuevos = 0
    for n, u, tipo, cuerpo in candidatos():
        ya = psql(f"select count(*) from importaciones where nombre_original = {lit(n)} and tipo = {lit(tipo)}", tuples=True).strip()
        if ya != '0': print('  ya registrado:', n, f'({tipo})'); continue
        print('  NUEVO:', n, f'({tipo}, {cuerpo})', '<-', u)
        if not real: continue
        try:
            rq = urllib.request.Request(u, headers={'User-Agent': bp.UA})
            with urllib.request.urlopen(rq, timeout=120) as r: datos = r.read()
            if not datos.startswith(b'%PDF'): print('    no parece un PDF, lo salto'); continue
            ruta = 'pendientes/' + datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S') + '_' + re.sub(r'[^\w.\-]', '_', n)
            key = limpiar('SUPABASE_SERVICE_KEY')
            su = limpiar('SUPABASE_URL').rstrip('/') + '/storage/v1/object/pdfs/' + urllib.parse.quote(ruta)
            up = urllib.request.Request(su, data=datos, method='POST', headers={'apikey': key, 'Authorization': 'Bearer ' + key, 'Content-Type': 'application/pdf'})
            urllib.request.urlopen(up, timeout=120).read()
            psql(f"insert into importaciones (archivo, nombre_original, tipo, cuerpo) values ({lit(ruta)}, {lit(n)}, {lit(tipo)}, {lit(cuerpo)})")
            nuevos += 1; print('    guardado como pendiente')
        except Exception as e: print('    ERROR:', repr(e)[:300])
    print('PDF nuevos registrados:', nuevos); return nuevos


if __name__ == '__main__':
    import procesar_pdfs as pp
    main(pp.psql, pp.lit, pp.limpiar)
