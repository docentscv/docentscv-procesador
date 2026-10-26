#!/usr/bin/env python3
"""DocentsCV · procesador automático de PDF de adjudicaciones continuas.

Lee las filas 'pendiente' de la tabla importaciones, descarga el PDF del almacén 'pdfs',
lo interpreta y actualiza la base de datos (posiciones, adjudicaciones, resumen, alcance y tasas).
Solo usa 'psql' y la librería estándar de Python (+ pdftotext, que usan los parsers).

Variables de entorno:
  DATABASE_URL          cadena de conexión de Supabase (Session pooler)
  SUPABASE_URL          https://xxxx.supabase.co
  SUPABASE_SERVICE_KEY  clave secreta (sb_secret_...)   -> para descargar el PDF
Opciones:  --local RUTA.pdf --tipo continua --cuerpo maestros|otros   (prueba sin Storage: carga ese PDF y ya)
"""
import os, re, sys, csv, subprocess, tempfile, urllib.request, urllib.parse, collections, argparse, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import parse_adjudicaciones as pm, parse_otros_cuerpos as po

SLUG = {"PROFESSORS D'ENSENYAMENT SECUNDARI": "secundaria", "PROFESSORS DE MÚSICA I ARTS ESCÈNIQUES": "musica",
        "CATEDRÀTICS DE MÚSICA I ARTS ESCÈNIQUES": "catedraticos_musica", "PROFESSORS D'ARTS PLÀSTIQUES I DISSENY": "artes_plasticas",
        "PROFESSORS D'ESCOLES D'IDIOMES": "idiomas", "M. DE TALLER D'ARTS PLÀSTIQUES I DISSENY": "taller_artes",
        "PROFESSORS ESPECIALISTES EN SECTORS SINGULARS DE FORMACIÓ PROFESSIONAL": "fp_especialistas"}
ESTADO = {'Adjudicat': 1, 'No adjudicat': 2, 'Ha participat': 3, 'No ha participat': 4, 'Desactivat': 5}
TR = str.maketrans('ÁÀÄÂÃÅÉÈËÊÍÌÏÎÓÒÖÔÕÚÙÜÛÑÇÝ', 'AAAAAAEEEEIIIIOOOOOUUUUNCY')
def norm(s): return re.sub(r'[^A-Z0-9]+', ' ', s.upper().translate(TR)).strip()      # = norm_nombre() de SQL
XB = [(0, 0), (1, 2), (3, 5), (6, 10), (11, 20), (21, 40), (41, 80), (81, 160), (161, 10**9)]
AB = [(0, 0), (1, 2), (3, 9), (10, 30), (31, 10**9)]
KTRANS = 4
def lab(v, tabla):
    for a, b in tabla:
        if a <= v <= b: return str(a) if a == b else (f'{a}-{b}' if b < 10**9 else f'{a}+')

class Fallo(Exception): pass

# ---------- psql ----------
def psql(sql, cwd=None, tuples=False):
    cmd = ['psql', os.environ['DATABASE_URL'], '-v', 'ON_ERROR_STOP=1', '-q'] + (['-At', '-F', '\t'] if tuples else [])
    p = subprocess.run(cmd, input=sql, capture_output=True, text=True, cwd=cwd)
    if p.returncode: ls = p.stderr.strip().splitlines(); e = next((l for l in ls if 'ERROR:' in l), ls[-1] if ls else 'error de base de datos'); raise Fallo(e.split('ERROR:')[-1].strip())
    return p.stdout
def lit(v): return "'" + str(v).replace("'", "''") + "'"

# ---------- lectura y comprobación del PDF ----------
def leer_pdf(ruta, grupo):
    if os.path.getsize(ruta) < 100_000: raise Fallo('El PDF está incompleto o cortado (pesa muy poco). Vuelve a descargarlo y a subirlo.')
    recs = (pm if grupo == 'maestros' else po).parse(ruta)
    if not recs: raise Fallo('No he encontrado ninguna fila en el PDF. ¿Es el listado correcto (maestros o secundaria)?')
    out = []
    for r in recs:
        if grupo == 'maestros': r = dict(r, cuerpo='maestros', bolsa='-', bolsa_n='Maestros (lista única)', sub=None)
        else:
            if r['cuerpo'] not in SLUG: raise Fallo('Cuerpo desconocido en el PDF: ' + r['cuerpo'])
            r = dict(r, cuerpo=SLUG[r['cuerpo']])
        out.append(r)
    mx = collections.defaultdict(lambda: [0, 0])
    for r in out: k = (r['cuerpo'], r['bolsa']); mx[k][0] += 1; mx[k][1] = max(mx[k][1], r['pos'])
    malas = [k for k, (n, m) in mx.items() if n != m]
    if malas: raise Fallo(f'El PDF parece incompleto: faltan filas en {len(malas)} lista(s), por ejemplo {malas[0][0]}/{malas[0][1]}.')
    return out

def preparar(recs, d):
    """Escribe los CSV temporales; devuelve nº de filas."""
    por = collections.defaultdict(list)
    for r in recs: por[(r['cuerpo'], r['bolsa'])].append(r)
    cen, w = {}, lambda n: csv.writer(open(os.path.join(d, n), 'w', newline='', encoding='utf-8'))
    wr, wa, wres, walc = w('raw.csv'), w('adj.csv'), w('res.csv'), w('alc.csv')
    for k, rs in por.items():
        rs.sort(key=lambda r: r['pos']); na = 0; alc = {}; cnt = collections.Counter(r['st'] for r in rs)
        wres.writerow([k[0], k[1]] + [cnt.get(e, 0) for e in ESTADO])
        for r in rs:
            wr.writerow([r['cuerpo'], r['bolsa'], r['bolsa_n'], r['pos'], '' if r['sub'] is None else r['sub'], norm(r['nombre']), r['nombre'], ESTADO[r['st']]])
            if r['st'] == 'Adjudicat':
                cen[r['cen']] = (r['nom'], r['loc'], r['prov'])
                t, det = ('vacante', '') if r['tipo'] == 'VACANT' else ('sustitucion', r['tipo'])
                wa.writerow([r['cuerpo'], r['bolsa'], r['pos'], r['code'], r['cen'], r['esp'], t, det, '' if r['h'] in ('', 'completa') else r['h']])
                a = alc.setdefault(r['esp'], [0, 0, 0]); a[0] += 1; a[1] = max(a[1], na); a[2] = max(a[2], r['pos'])
            if r['st'] == 'No adjudicat': na += 1
        for esp, (n, xm, pm_) in alc.items(): walc.writerow([k[0], k[1], esp, n, xm, pm_])
    wc = w('cen.csv')
    for c, (n, l, p) in cen.items(): wc.writerow([c, n, l, p])
    return len(recs)

LOAD = r"""
begin;
do $$ begin
  if exists (select 1 from instantaneas where fecha = {fecha} and tipo = 'continua' and grupo = {grupo}) then
    raise exception 'Esa lista (%) ya estaba cargada', {fecha}; end if; end $$;
create temp table t_raw (cuerpo text, bolsa text, bolsa_n text, pos int, sub int, nn text, nombre text, estado smallint) on commit drop;
create temp table t_adj (cuerpo text, bolsa text, pos int, plaza text, centro text, esp text, tipo text, detalle text, horas smallint) on commit drop;
create temp table t_res (cuerpo text, bolsa text, a int, b int, c int, d int, e int) on commit drop;
create temp table t_alc (cuerpo text, bolsa text, esp text, n int, x_max int, p_max int) on commit drop;
create temp table t_cen (codigo text, nombre text, localidad text, provincia text) on commit drop;
\copy t_raw from 'raw.csv' csv
\copy t_adj from 'adj.csv' csv
\copy t_res from 'res.csv' csv
\copy t_alc from 'alc.csv' csv
\copy t_cen from 'cen.csv' csv
insert into instantaneas (id, fecha, tipo, grupo, fuente, curso_id)
  select coalesce(max(id), 0) + 1, {fecha}, 'continua', {grupo}, {fuente}, null from instantaneas;
insert into listas (id, cuerpo, bolsa, nombre)
  select (select coalesce(max(id), 0) from listas) + row_number() over (order by cuerpo, bolsa), cuerpo, bolsa, nombre
  from (select distinct on (cuerpo, bolsa) cuerpo, bolsa, bolsa_n as nombre from t_raw) x
  where not exists (select 1 from listas l where l.cuerpo = x.cuerpo and l.bolsa = x.bolsa);
insert into personas (id, nombre_norm, nombre)
  select (select coalesce(max(id), 0) from personas) + row_number() over (order by nn), nn, nombre
  from (select distinct on (nn) nn, nombre from t_raw order by nn, nombre) x
  where not exists (select 1 from personas p where p.nombre_norm = x.nn);
insert into centros (codigo, nombre, localidad, provincia)
  select codigo, nombre, localidad, provincia from t_cen on conflict (codigo) do nothing;
create temp table n_inst on commit drop as select id from instantaneas where fecha = {fecha} and tipo = 'continua' and grupo = {grupo};
insert into posiciones (instantanea_id, lista_id, posicion, sub, persona_id, estado)
  select (select id from n_inst), l.id, r.pos, r.sub, p.id, r.estado
  from t_raw r join listas l on l.cuerpo = r.cuerpo and l.bolsa = r.bolsa join personas p on p.nombre_norm = r.nn;
insert into adjudicaciones_continuas (instantanea_id, lista_id, posicion, codigo_plaza, centro_codigo, especialidad_codigo, tipo, detalle, horas)
  select (select id from n_inst), l.id, a.pos, a.plaza, a.centro, a.esp, a.tipo, nullif(a.detalle, ''), a.horas
  from t_adj a join listas l on l.cuerpo = a.cuerpo and l.bolsa = a.bolsa;
insert into resumen_lista select (select id from n_inst), l.id, r.a, r.b, r.c, r.d, r.e from t_res r join listas l on l.cuerpo = r.cuerpo and l.bolsa = r.bolsa;
insert into alcance select (select id from n_inst), l.id, a.esp, a.n, a.x_max, a.p_max from t_alc a join listas l on l.cuerpo = a.cuerpo and l.bolsa = a.bolsa;
-- poda: los 'Desactivat' solo se guardan en la última aparición de cada lista
delete from posiciones where estado = 5 and instantanea_id <> (select id from n_inst)
  and lista_id in (select l.id from listas l join (select distinct cuerpo, bolsa from t_raw) x on x.cuerpo = l.cuerpo and x.bolsa = l.bolsa);
commit;
"""
def cargar(fecha, grupo, fuente, d):
    sql = LOAD.format(fecha=lit(fecha), grupo=lit(grupo), fuente=lit(fuente))
    psql(sql, cwd=d)

# ---------- tasas empíricas (solo listas numeradas por bolsa = 'otros') ----------
def recalcular_tasas(d):
    ins = [l.split('\t') for l in psql("select id, fecha from instantaneas where tipo = 'continua' and grupo = 'otros' order by fecha desc limit 7", tuples=True).splitlines()]
    ins.sort(key=lambda x: x[1])
    if len(ins) < 2: return 'sin datos suficientes'
    ids = [int(i[0]) for i in ins]; ult5 = ids[-(KTRANS + 1):]
    pos = collections.defaultdict(list)             # (inst, lista) -> [(posicion, persona, estado)]
    out = psql(f"select instantanea_id, lista_id, posicion, persona_id, estado from posiciones where instantanea_id in ({','.join(map(str, ult5))})", tuples=True)
    for l in out.splitlines():
        i, li, p, pe, e = map(int, l.split('\t')); pos[(i, li)].append((p, pe, e))
    adjn = {}
    for l in psql(f"select instantanea_id, lista_id, n_adjudicat from resumen_lista where instantanea_id in ({','.join(map(str, ids))})", tuples=True).splitlines():
        i, li, n = map(int, l.split('\t')); adjn[(i, li)] = n
    listas = sorted({k[1] for k in pos})
    muestras = []
    for a, b in zip(ult5[:-1], ult5[1:]):
        ia = ids.index(a); prev = ids[max(0, ia - 2):ia + 1]
        for li in listas:
            A = sorted(pos.get((a, li), [])); B = pos.get((b, li), [])
            if not A: continue
            dupA = {p for p, c in collections.Counter(x[1] for x in A).items() if c > 1}
            dupB = {p for p, c in collections.Counter(x[1] for x in B).items() if c > 1}
            adjB = {x[1] for x in B if x[2] == 1}
            act = sum(adjn.get((i, li), 0) for i in prev); na = 0
            for p, pe, e in A:
                if e in (2, 3, 4) and pe not in dupA and pe not in dupB: muestras.append((e, lab(act, AB), lab(na, XB), pe in adjB))
                if e == 2: na += 1
    filas = []
    for est in (2, 3, 4):
        S = [m for m in muestras if m[0] == est]; base = sum(m[3] for m in S) / len(S) if S else 0.0
        for a in AB:
            for x in XB:
                s = [m for m in S if m[1] == lab(a[0], AB) and m[2] == lab(x[0], XB)]
                filas.append((est, lab(a[0], AB), lab(x[0], XB), len(s), round((sum(m[3] for m in s) + base * 20) / (len(s) + 20), 5)))
    with open(os.path.join(d, 'tasas.csv'), 'w', newline='', encoding='utf-8') as f: csv.writer(f).writerows(filas)
    psql("begin;\ndelete from tasas_tanda;\n\\copy tasas_tanda from 'tasas.csv' csv\ncommit;\n", cwd=d)
    return f'{len(muestras)} muestras'

# ---------- Storage ----------
def descargar(archivo, destino):
    if os.environ.get('STORAGE_LOCAL_DIR'):            # solo para pruebas en local
        try: return shutil.copyfile(os.path.join(os.environ['STORAGE_LOCAL_DIR'], archivo), destino)
        except OSError as e: raise Fallo('Archivo no encontrado: ' + str(e))
    url = os.environ['SUPABASE_URL'].rstrip('/') + '/storage/v1/object/pdfs/' + urllib.parse.quote(archivo)
    key = os.environ['SUPABASE_SERVICE_KEY']
    req = urllib.request.Request(url, headers={'apikey': key, 'Authorization': 'Bearer ' + key})
    try:
        with urllib.request.urlopen(req, timeout=120) as r, open(destino, 'wb') as f: shutil.copyfileobj(r, f)
    except Exception as e: raise Fallo('No he podido descargar el PDF del almacén: ' + str(e))

def interpretar_nombre(nombre, cuerpo):
    m = re.match(r'^(\d{2})(\d{2})(\d{2})_lis_(mae|sec)\.pdf$', nombre.strip(), re.I)
    if not m: raise Fallo('El nombre del archivo debe ser como lo publica la Conselleria: AAMMDD_lis_mae.pdf o AAMMDD_lis_sec.pdf (por ejemplo 261022_lis_mae.pdf).')
    if (m.group(4).lower() == 'mae') != (cuerpo == 'maestros'): raise Fallo('El nombre del archivo no coincide con el cuerpo elegido al subirlo (maestros / otros).')
    return f'20{m.group(1)}-{m.group(2)}-{m.group(3)}'

def procesar(nombre, tipo, cuerpo, ruta_pdf, d):
    if tipo != 'continua': raise Fallo('Las listas de inicio de curso todavía se cargan a mano; este procesador solo hace adjudicaciones continuas.')
    fecha = interpretar_nombre(nombre, cuerpo)
    recs = leer_pdf(ruta_pdf, cuerpo)
    n = preparar(recs, d); cargar(fecha, cuerpo, nombre, d)
    return n

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--local'); ap.add_argument('--tipo', default='continua'); ap.add_argument('--cuerpo')
    ap.add_argument('--sin-tasas', action='store_true'); a = ap.parse_args()
    hecho_otros = False
    with tempfile.TemporaryDirectory() as tmp:
        if a.local:
            d = os.path.join(tmp, 'w'); os.makedirs(d)
            n = procesar(os.path.basename(a.local), a.tipo, a.cuerpo, a.local, d); print('cargado', a.local, n, 'filas'); hecho_otros = a.cuerpo == 'otros'
        else:
            pend = psql("select id, archivo, nombre_original, tipo, cuerpo from importaciones where estado = 'pendiente' order by creado, id", tuples=True).splitlines()
            print(len(pend), 'pendiente(s)')
            for l in pend:
                iid, archivo, nombre, tipo, cuerpo = l.split('\t')
                psql(f"update importaciones set estado = 'procesando' where id = {int(iid)} and estado = 'pendiente'")
                d = os.path.join(tmp, 'w' + iid); os.makedirs(d); pdf = os.path.join(d, 'in.pdf')
                try:
                    interpretar_nombre(nombre, cuerpo) if tipo == 'continua' else None
                    descargar(archivo, pdf); n = procesar(nombre, tipo, cuerpo, pdf, d)
                    psql(f"update importaciones set estado = 'ok', filas = {n}, mensaje = null, procesado = now() where id = {int(iid)}")
                    print('OK', nombre, n); hecho_otros |= cuerpo == 'otros'
                except Fallo as e:
                    psql(f"update importaciones set estado = 'error', mensaje = {lit(str(e)[:500])}, procesado = now() where id = {int(iid)}"); print('ERROR', nombre, e)
                except Exception as e:
                    psql(f"update importaciones set estado = 'error', mensaje = {lit('Error inesperado: ' + str(e)[:400])}, procesado = now() where id = {int(iid)}"); print('ERROR', nombre, repr(e))
        if hecho_otros and not a.sin_tasas:
            d = os.path.join(tmp, 'tasas'); os.makedirs(d); print('tasas:', recalcular_tasas(d))
if __name__ == '__main__': main()
