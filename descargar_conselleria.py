#!/usr/bin/env python3
"""DocentsCV · Busca en la web de la Conselleria los PDF nuevos, los guarda en el almacén 'pdfs' y los
deja como 'pendiente' en importaciones (igual que cuando los subes tú a mano). Luego procesar_pdfs.py los carga.

Seguridad:
  · Sin AUTO_DESCARGA=1 solo SIMULA (escribe lo que haría, no descarga ni guarda nada).
  · Solo PDF de los últimos DIAS_ATRAS días y que no estén ya registrados (mismo nombre y tipo).
  · En las ejecuciones programadas solo actúa de lunes a viernes, de 07:00 a 22:00 (hora de Madrid), como mucho cada ~25 minutos.
  · Se identifica con un nombre propio (no se hace pasar por un navegador)."""
import os, re, sys, time, subprocess, datetime, urllib.request, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import buscar_pdfs as bp

DIAS_ATRAS = 8
# (dirección, tipo de PDF que sale en esa página) ; en cada página solo nos interesan los nombres indicados
PAGINAS = [
    ('https://ceice.gva.es/es/web/rrhh-educacion/convocatoria-y-peticion-telematica',  {'pue': 'ofertas'}),
    ('https://ceice.gva.es/es/web/rrhh-educacion/resolucion',                           {'lis': 'continua', 'pue': 'ofertas'}),
    ('https://ceice.gva.es/es/web/rrhh-educacion/convocatoria-y-peticion-telematica6', {'pue': 'dificil'}),
    ('https://ceice.gva.es/es/web/rrhh-educacion/resolucion1',                          {'pue': 'dificil', 'par': 'dificil_adj'}),
]
NOM = re.compile(r'^(\d{2})(\d{2})(\d{2})_(lis_mae|lis_sec|pue_prov|pue_def|par)\.pdf$', re.I)


def toca_ahora(ahora=None):
    """Ejecuciones programadas: solo de lunes a viernes, de 07:00 a 21:59 (hora de Madrid). GitHub retrasa a veces
    las ejecuciones programadas, por eso la frecuencia se controla con la hora de la última búsqueda (ver main)."""
    if os.environ.get('GITHUB_EVENT_NAME', 'schedule') == 'workflow_dispatch': return True
    try:
        import zoneinfo; n = (ahora or datetime.datetime.now(zoneinfo.ZoneInfo('Europe/Madrid')))
    except Exception: n = ahora or datetime.datetime.utcnow() + datetime.timedelta(hours=1)
    return n.weekday() < 5 and 7 <= n.hour <= 21


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
            if tipo == 'dificil' and m.group(4).lower() == 'pue_prov' and any(x[0].lower() == n.lower().replace('pue_prov', 'pue_def') for x in out): continue   # si ya está el definitivo, el provisional sobra
            if not tipo or (n, tipo) in vistos: continue
            vistos.add((n, tipo))
            cuerpo = ('maestros' if n.lower().endswith('_lis_mae.pdf') else 'otros') if tipo == 'continua' else 'todos'
            out.append((n, u, tipo, cuerpo))
    out = [x for x in out if not (x[2] == 'dificil' and x[0].lower().endswith('pue_prov.pdf') and any(y[0].lower() == x[0].lower().replace('pue_prov', 'pue_def') for y in out))]
    out.sort(key=lambda x: x[0].lower().endswith('pue_def.pdf'))   # primero provisionales, luego definitivos
    return out


def ahora_madrid():
    try:
        import zoneinfo; return datetime.datetime.now(zoneinfo.ZoneInfo('Europe/Madrid'))
    except Exception: return datetime.datetime.utcnow() + datetime.timedelta(hours=1)


def _guardar(n, u, tipo, cuerpo, psql, lit, limpiar):
    """Descarga un PDF ya detectado como nuevo y lo deja 'pendiente'. True si lo ha guardado."""
    try:
        rq = urllib.request.Request(u, headers={'User-Agent': bp.UA})
        with urllib.request.urlopen(rq, timeout=120) as r: datos = r.read()
        if not datos.startswith(b'%PDF'): print('    no parece un PDF, lo salto'); return False
        ruta = 'pendientes/' + datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S') + '_' + re.sub(r'[^\w.\-]', '_', n)
        key = limpiar('SUPABASE_SERVICE_KEY')
        su = limpiar('SUPABASE_URL').rstrip('/') + '/storage/v1/object/pdfs/' + urllib.parse.quote(ruta)
        up = urllib.request.Request(su, data=datos, method='POST', headers={'apikey': key, 'Authorization': 'Bearer ' + key, 'Content-Type': 'application/pdf'})
        urllib.request.urlopen(up, timeout=120).read()
        psql(f"insert into importaciones (archivo, nombre_original, tipo, cuerpo) values ({lit(ruta)}, {lit(n)}, {lit(tipo)}, {lit(cuerpo)})")
        print('    guardado como pendiente')
        return True
    except Exception as e: print('    ERROR:', repr(e)[:300]); return False


# Qué toca vigilar cada día (0 = lunes ... 4 = viernes):
#   lunes/miércoles: salen las plazas de continua · martes: adjudicación continua (del día anterior lectivo)
#   jueves: adjudicación continua (del miércoles) + salen las plazas de difícil cobertura · viernes: adjudicación de difícil cobertura
TIPOS_POR_DIA = {0: ['ofertas'], 1: ['continua'], 2: ['ofertas'], 3: ['continua', 'dificil'], 4: ['dificil_adj']}


def vigilancia(psql, lit, limpiar, pausa=60, hasta_hora=14):
    """Pensado para lanzarlo a las 8:00: mira la web de la Conselleria sin parar (cada `pausa` segundos,
    sin el límite habitual de 'como mucho cada 25 minutos') hasta que salga lo que toca hoy, o hasta las
    `hasta_hora` (GitHub no deja que un job dure más de 6 horas, así que de 8:00 no puede pasar de 14:00).
    En cuanto aparece cada PDF esperado lo descarga, lo deja pendiente y lo procesa al momento (no espera
    a que salgan los demás), para publicarlo lo antes posible. Si a esa hora sigue faltando algo, avisa."""
    n = ahora_madrid()
    tipos = TIPOS_POR_DIA.get(n.weekday(), [])
    if not tipos: print('Vigilancia: hoy no toca nada (fin de semana).'); return 0
    real = os.environ.get('AUTO_DESCARGA', '').strip() == '1'
    if not real: print('Vigilancia: AUTO_DESCARGA no está activada; en modo vigilancia no tiene sentido simular, me paro aquí.'); return 0
    print(f'Vigilancia: hoy ({n.strftime("%A")}) toca {", ".join(tipos)}. Compruebo cada {pausa}s hasta que salga, o hasta las {hasta_hora}:00.')
    visto = {t: set() for t in tipos}   # tipo -> cuerpos ya encontrados hoy ('continua' necesita 'maestros' y 'otros'; el resto, con uno vale)
    intentos = encontrados_total = 0
    while True:
        intentos += 1
        # Primero se mira de verdad qué hay (en la web Y en la base de datos): 'visto' empieza vacío en
        # cada llamada, así que si no se comprobara esto ANTES de decidir qué falta, una vigilancia
        # lanzada ya tarde (p. ej. a mano, o si algo retrasa el aviso externo) daría una falsa alarma de
        # "falta todo" aunque ya estuviera cargado desde hace horas por la vía normal.
        encontrados_ronda = 0
        try: cands = candidatos()
        except Exception as e: print('  no se pudo mirar la web:', repr(e)[:150]); cands = []
        for nom, u, tipo, cuerpo in cands:
            completo = ({'maestros', 'otros'} <= visto[tipo]) if tipo == 'continua' else bool(visto[tipo])
            if tipo not in tipos or completo: continue
            ya = psql(f"select count(*) from importaciones where nombre_original = {lit(nom)} and tipo = {lit(tipo)}", tuples=True).strip()
            if ya != '0': visto[tipo].add(cuerpo); continue
            print(f'  ¡ENCONTRADO! {nom} ({tipo}, {cuerpo}) tras {intentos} comprobación(es) <- {u}')
            if _guardar(nom, u, tipo, cuerpo, psql, lit, limpiar):
                visto[tipo].add(cuerpo); encontrados_ronda += 1; encontrados_total += 1
        if encontrados_ronda:
            print('  proceso lo encontrado para publicarlo ya...')
            try: subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'procesar_pdfs.py')], check=False)
            except Exception as e: print('  no se pudo procesar al momento (se procesará en el siguiente paso):', repr(e)[:200])
        faltan = [t for t in tipos if not ({'maestros', 'otros'} <= visto[t] if t == 'continua' else visto[t])]
        if not faltan: print(f'Vigilancia: ya ha salido todo lo de hoy (tras {intentos} comprobación(es)).'); return encontrados_total
        n = ahora_madrid()
        if n.hour >= hasta_hora:
            print(f'Vigilancia: a las {hasta_hora}:00 todavía falta {", ".join(faltan)}; lo dejo por hoy y aviso.')
            try:
                import avisos
                avisos.avisar_admin(psql, lit, [(f'Vigilancia de hoy sin terminar: sigue faltando {", ".join(faltan)}', False, 'Revisa la web de la Conselleria a mano, o relanza la vigilancia desde Actions')], titulo='⚠️ No ha salido todo lo esperado hoy')
            except Exception as e: print('  no se pudo avisar:', repr(e)[:120])
            return encontrados_total
        time.sleep(pausa)


def vigilar_continua(psql, lit, ahora=None):
    """Los martes y jueves (días de adjudicación continua), a partir de las 15:00, si todavía no están cargadas las dos listas
    de ese día (maestros y secundaria/otros) avisa UNA vez al día a los administradores por notificación push."""
    n = ahora or ahora_madrid()
    if n.weekday() not in (1, 3) or n.hour < 15: return False
    pref = n.strftime('%y%m%d') + '_lis'
    hechas = psql(f"select count(distinct cuerpo) from importaciones where tipo = 'continua' and estado <> 'error' and nombre_original ilike {lit(pref + '%')}", tuples=True).strip()
    if hechas == '2': return False
    ya = psql("select valor from config where clave = 'aviso_falta_continua'", tuples=True).strip()
    if ya == n.date().isoformat(): return False
    psql(f"insert into config(clave, valor) values ('aviso_falta_continua', {lit(n.date().isoformat())}) on conflict (clave) do update set valor = excluded.valor")
    print('VIGILANTE: hoy hay adjudicación continua y aún no están cargadas las dos listas (' + str(hechas) + ' de 2). Aviso a los administradores.')
    try:
        import avisos
        avisos.avisar_admin(psql, lit, [(f'Adjudicación continua de hoy: {hechas} de 2 listas cargadas', False, 'Mira Administración o súbelas a mano')], titulo='⚠️ Falta la adjudicación continua de hoy')
    except Exception as e: print('  no se pudo avisar:', repr(e)[:120])
    return True


def main(psql, lit, limpiar):
    if not toca_ahora(): print('Descarga automática: ahora no toca (solo de lunes a viernes, de 07:00 a 22:00).'); return 0
    try: vigilar_continua(psql, lit)
    except Exception as e: print('  vigilante:', repr(e)[:120])
    manual = os.environ.get('GITHUB_EVENT_NAME', '') == 'workflow_dispatch'
    try:                                                    # como mucho una búsqueda cada ~25 minutos (salvo si la lanzas tú a mano)
        seg = psql("select coalesce(extract(epoch from now() - valor::timestamptz)::int, 99999) from config where clave = 'ultima_busqueda_conselleria'", tuples=True).strip()
        if seg and int(seg) < 25 * 60 and not manual: print(f'Descarga automática: última búsqueda hace {int(seg) // 60} min; espero.'); return 0
    except Exception: pass
    real = os.environ.get('AUTO_DESCARGA', '').strip() == '1'
    print('Descarga automática desde la Conselleria ·', 'REAL' if real else 'SIMULACIÓN (no guarda nada)')
    if not real: print('  ATENCIÓN: AUTO_DESCARGA no está activada (variable de Actions con valor 1); solo se simula.')
    try: psql("insert into config(clave, valor) values ('ultima_busqueda_conselleria', now()::text) on conflict (clave) do update set valor = excluded.valor")
    except Exception as e: print('  no se pudo apuntar la hora de la búsqueda:', repr(e)[:120])
    nuevos = 0
    for n, u, tipo, cuerpo in candidatos():
        ya = psql(f"select count(*) from importaciones where nombre_original = {lit(n)} and tipo = {lit(tipo)}", tuples=True).strip()
        if ya != '0': print('  ya registrado:', n, f'({tipo})'); continue
        print('  NUEVO:', n, f'({tipo}, {cuerpo})', '<-', u)
        if not real: continue
        if _guardar(n, u, tipo, cuerpo, psql, lit, limpiar): nuevos += 1
    print('PDF nuevos registrados:', nuevos); return nuevos


if __name__ == '__main__':
    import procesar_pdfs as pp
    main(pp.psql, pp.lit, pp.limpiar)
