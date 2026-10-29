#!/usr/bin/env python3
"""DocentsCV · Carga la dirección de cada centro (centros_direcciones.csv) y calcula sus coordenadas.
Se ejecuta a mano (Actions > «Calcular coordenadas»). Se puede repetir: solo busca los centros que faltan.
Usa Nominatim (OpenStreetMap) respetando su límite de 1 consulta por segundo y con un nombre propio de aplicación.
Orden de precisión: 1) dirección completa  2) código postal + localidad (si la dirección no aparece)  3) la localidad (lo hace la web)."""
import os, re, sys, csv, math, time, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import procesar_pdfs as pp
import geocodificar as g

AQUI = os.path.dirname(os.path.abspath(__file__))
MAX = int(os.environ.get('MAX_CENTROS', '2600'))          # por ejecución (≈ 1 h); si faltan, se vuelve a lanzar
RADIO_KM = 15                                              # un resultado más lejos de su localidad se descarta (suele ser un homónimo)
TIPOS = r'(calle|c/|avenida|avda\.?|avinguda|plaza|plaça|paseo|passeig|camino|cami|carrer|carretera|ctra\.?|urbanizaci[oó]n|partida|pol[ií]gono|ronda|travesia|traves[ií]a|glorieta|camí)'


def km(a, b):
    R = 6371; t = math.radians; dl = t(b[0] - a[0]); dn = t(b[1] - a[1])
    h = math.sin(dl / 2) ** 2 + math.cos(t(a[0])) * math.cos(t(b[0])) * math.sin(dn / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def calle_limpia(d):
    """«Calle PADRE RECADERO DE LOS RÍOS, 14-16 (Edif. B)» -> «Padre Recadero De Los Ríos 14», con y sin el tipo de vía."""
    d = re.sub(r'\([^)]*\)', ' ', d or '')
    d = re.sub(r'\bs/n\b\.?', '', d, flags=re.I)
    m = re.match(r'^(.*?)[,\s]+(\d+)\s*(?:[-/]\s*[0-9A-Za-z]+)?\s*(?:,.*)?$', d.strip())
    nombre, num = (m.group(1), m.group(2)) if m else (d.strip(), '')
    nombre = re.sub(r'\s+', ' ', nombre).strip(' ,.')
    con = (nombre.title() + (' ' + num if num else '')).strip()
    sin = re.sub(r'^' + TIPOS + r'\s+', '', nombre, flags=re.I)
    sin = (sin.title() + (' ' + num if num else '')).strip()
    return con, sin


def consultas(dirc, cp, loc):
    l = g.limpio(loc); con, sin = calle_limpia(dirc); base = f'{cp} {l}, España'.strip()
    q = []
    if con: q.append((f'{con}, {base}', 'direccion'))
    if sin and sin != con: q.append((f'{sin}, {base}', 'direccion'))
    q.append((f'{cp} {l}, España' if cp else f'{l}, España', 'cp'))
    return q


def main():
    filas = list(csv.DictReader(open(os.path.join(AQUI, 'centros_direcciones.csv'), encoding='utf8')))
    print(len(filas), 'centros en el archivo')
    # 1) cargar direcciones en los centros que ya existen en la base de datos
    for i in range(0, len(filas), 400):
        vals = ','.join(f"({pp.lit(f['codigo'])},{pp.lit(f['direccion'])},{pp.lit(f['cp'])})" for f in filas[i:i + 400])
        pp.psql(f"""update centros c set direccion = v.d, cp = v.p,
                      lat = case when c.direccion is distinct from v.d or c.cp is distinct from v.p then null else c.lat end,
                      lon = case when c.direccion is distinct from v.d or c.cp is distinct from v.p then null else c.lon end,
                      geo_fuente = case when c.direccion is distinct from v.d or c.cp is distinct from v.p then null else c.geo_fuente end
                    from (values {vals}) v(codigo, d, p) where c.codigo = v.codigo""")
    loc_csv = {f['codigo']: f['localidad'] for f in filas}
    # 2) coordenadas de las localidades (respaldo y comprobación de que el resultado no es un homónimo)
    g.main()
    ref = {}
    for l in pp.psql("select localidad, provincia, lat, lon from localidades_geo where lat is not null", tuples=True).splitlines():
        a = l.split('\t')
        if len(a) == 4: ref[(a[0], a[1])] = (float(a[2]), float(a[3]))
    # 3) centros pendientes
    pend = [l.split('\t') for l in pp.psql("""select codigo, coalesce(direccion,''), coalesce(cp,''), localidad, provincia from centros
        where direccion is not null and geo_fuente is null order by codigo limit %d""" % MAX, tuples=True).splitlines() if l.strip()]
    print(len(pend), 'centros por localizar en esta ejecución')
    n = {'direccion': 0, 'cp': 0, 'sin': 0}
    for cod, dirc, cp, loc, prov in pend:
        centro = ref.get((loc, prov)); res = None; fuente = 'sin'
        for q, f in consultas(dirc, cp, loc_csv.get(cod) or loc):
            try: pos = g.buscar(q)
            except Exception as e: print('  error en', q, repr(e)[:100]); time.sleep(5); continue
            if pos and (centro is None or km(pos, centro) <= RADIO_KM):
                res, fuente = pos, f; break
        n[fuente] += 1
        if res: pp.psql(f"update centros set lat = {res[0]}, lon = {res[1]}, geo_fuente = {pp.lit(fuente)} where codigo = {pp.lit(cod)}")
        else: pp.psql(f"update centros set geo_fuente = 'sin' where codigo = {pp.lit(cod)}")
    print('con dirección exacta:', n['direccion'], '· solo código postal:', n['cp'], '· sin resultado (se usará la localidad):', n['sin'])


if __name__ == '__main__': main()
