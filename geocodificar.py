#!/usr/bin/env python3
"""DocentsCV · Busca las coordenadas de cada localidad que aparece en la tabla centros y las guarda en localidades_geo.
Se ejecuta a mano (Actions > «Calcular coordenadas de las localidades»). Se puede repetir: solo busca las que faltan.
Usa Nominatim (OpenStreetMap) respetando su límite de 1 consulta por segundo y con un nombre propio de aplicación."""
import os, re, sys, json, time, urllib.request, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import procesar_pdfs as pp

UA = 'DocentsCV/1.0 (https://docentscv.github.io/; docentscv@gmail.com)'
CAJA = (-1.6, 40.9, 0.8, 37.7)        # oeste, norte, este, sur: Comunitat Valenciana con margen
PROV = {'Alicante': 'Alicante', 'València': 'Valencia', 'Castelló': 'Castellón'}
ART = {"L'": "L'", 'LA': 'La ', 'EL': 'El ', 'LES': 'Les ', 'ELS': 'Els ', 'LAS': 'Las ', 'LOS': 'Los '}


def limpio(n):
    """«NUCIA (LA)» -> «La Nucia»; «ALFÀS DEL PI (L')» -> «L'Alfàs del Pi»; «BONANZA (RAIGUERO DE)» -> «Bonanza»."""
    m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", n.strip())
    if m and m.group(2).upper() in ART: n = ART[m.group(2).upper()] + m.group(1)
    elif m: n = m.group(1)
    return n.strip().title().replace("D'", "d'").replace(" De ", " de ").replace(" Del ", " del ").replace(" La ", " la ").replace(" El ", " el ")


def variantes(loc, prov):
    base = limpio(loc); p = PROV.get(prov, prov); v = [f'{base}, {p}, España', f'{base}, Comunitat Valenciana, España']
    for parte in re.split(r'\s+-\s+|-', loc):                 # «ADOR - PALMA DE GANDIA», «ALGODA-MATOLA»…
        if parte.strip() and limpio(parte) != base: v.append(f'{limpio(parte)}, {p}, España')
    return v


def buscar(q):
    u = 'https://nominatim.openstreetmap.org/search?' + urllib.parse.urlencode({
        'q': q, 'format': 'jsonv2', 'limit': 1, 'countrycodes': 'es', 'viewbox': '%s,%s,%s,%s' % CAJA, 'bounded': 1})
    rs = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': UA}), timeout=30).read().decode('utf8'))
    time.sleep(1.1)                                           # política de uso de Nominatim: máximo 1 por segundo
    if not rs: return None
    lat, lon = float(rs[0]['lat']), float(rs[0]['lon'])
    return (lat, lon) if CAJA[3] <= lat <= CAJA[1] and CAJA[0] <= lon <= CAJA[2] else None


def main():
    filas = [l.split('\t') for l in pp.psql("""select distinct c.localidad, c.provincia from centros c
        where c.localidad is not null and c.localidad <> '' and not exists
          (select 1 from localidades_geo g where g.localidad = c.localidad and g.provincia = c.provincia and g.lat is not null)
        order by 2, 1""", tuples=True).splitlines() if l.strip()]
    print(len(filas), 'localidades por buscar'); falta = []
    for loc, prov in filas:
        pos = None
        for q in variantes(loc, prov):
            try: pos = buscar(q)
            except Exception as e: print('  error en', q, repr(e)[:100]); time.sleep(5)
            if pos: break
        if pos:
            pp.psql(f"insert into localidades_geo(localidad, provincia, lat, lon, fuente) values ({pp.lit(loc)}, {pp.lit(prov)}, {pos[0]}, {pos[1]}, 'nominatim') "
                    f"on conflict (localidad, provincia) do update set lat = excluded.lat, lon = excluded.lon, fuente = excluded.fuente")
        else: falta.append((loc, prov)); print('  SIN RESULTADO:', loc, prov)
    print(f'encontradas {len(filas) - len(falta)} · sin resultado {len(falta)}')


if __name__ == '__main__': main()
