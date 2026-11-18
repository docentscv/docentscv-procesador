#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DocentsCV · robot de acreditación de idiomas (de momento solo INGLÉS).

Recorre en la web de la Conselleria «Listados definitivos» de Acreditación en lenguas
extranjeras (https://ceice.gva.es/es/web/rrhh-educacion/listados-definitivos), las 3
direcciones territoriales (València, Castellón, Alicante) y todos los cursos. Por cada
PDF nuevo que encuentra, extrae quién tiene acreditado el inglés (B2/C1/C2) y lo guarda
en `acreditaciones_idioma`, para que la web le muestre esa observación a cada docente.

Solo usa 'psql' + pdfplumber (puro Python) + la librería estándar.

Seguridad:
  · Sin AUTO_CARGA=1 solo SIMULA: busca, descarga y analiza los PDF, pero no escribe
    nada en la base de datos (ni siquiera crea las tablas).
  · Identifica cada petición con un nombre propio (no se hace pasar por un navegador).
  · Cada PDF ya procesado (por su URL) se guarda en `fuentes_idioma` y no se vuelve a
    bajar ni analizar en ejecuciones siguientes.
  · Nunca adivina: un nombre que parece mal extraído del PDF (dos apellidos pegados) o
    que aparece dos veces en el mismo listado con niveles distintos (homónimo) se excluye
    y se deja anotado en `idiomas_revisar` para repasarlo a mano.
  · Solo apunta la acreditación a personas que YA existen en la base (de alguna lista de
    bolsa); no crea personas nuevas solo por tener el idioma acreditado.

Uso:
  python3 cargar_idiomas.py                    # recorrer toda la web (real solo si AUTO_CARGA=1)
  python3 cargar_idiomas.py --local X.pdf --provincia València [--fecha AAAA-MM-DD]   # probar con un PDF local
"""
import os, re, sys, time, argparse, datetime, subprocess, urllib.request, urllib.parse
from html.parser import HTMLParser
from collections import Counter

RAIZ = 'https://ceice.gva.es/es/web/rrhh-educacion/listados-definitivos'
UA = 'Mozilla/5.0 (compatible; DocentsCV/1.0; +https://docentscv.github.io/)'
PROVINCIAS = {"València", "Castellón", "Alicante"}
IDIOMA = 'Anglès'
IDIOMA_RE = re.compile(r'angl|ingl', re.I)
NIVEL_RE = re.compile(r'^(B2|C1|C2)$')
TOL = 2.5             # tolerancia (puntos) para agrupar palabras en la misma fila de una tabla
LARGO_SOSPECHOSO = 15  # una "palabra" de apellido más larga que esto probablemente son dos pegadas
PAUSA = 0.4            # segundos entre peticiones, para no saturar la web de la Conselleria
TR = str.maketrans('ÁÀÄÂÃÅÉÈËÊÍÌÏÎÓÒÖÔÕÚÙÜÛÑÇÝ', 'AAAAAAEEEEIIIIOOOOOUUUUNCY')


def norm(s):
    """= norm_nombre() de la base de datos: mayúsculas, sin acentos, solo A-Z0-9 y espacios."""
    return re.sub(r'[^A-Z0-9]+', ' ', s.upper().translate(TR)).strip()


class Fallo(Exception):
    pass


# ---------------------------------------------------------------- psql (igual que procesar_pdfs.py) ----
def limpiar(nombre):
    v = os.environ.get(nombre, '').strip().strip('"\'').strip()
    return re.sub(r'^' + nombre + r'\s*[=:]?\s*', '', v).strip()


def psql(sql, tuples=False):
    url = limpiar('DATABASE_URL')
    if not url.startswith('postgres'):
        raise Fallo(f'DATABASE_URL no es válida: tiene {len(url)} caracteres y empieza por «{url[:12]}». Debe empezar por postgresql://')
    cmd = ['psql', url, '-v', 'ON_ERROR_STOP=1', '-q'] + (['-At', '-F', '\t'] if tuples else [])
    p = subprocess.run(cmd, input=sql, capture_output=True, text=True)
    if p.returncode:
        ls = p.stderr.strip().splitlines()
        e = next((l for l in ls if 'ERROR:' in l), ls[-1] if ls else 'error de base de datos')
        raise Fallo(e.split('ERROR:')[-1].strip())
    return p.stdout


def lit(v):
    return "'" + str(v).replace("'", "''") + "'"


ESQUEMA = """
create table if not exists acreditaciones_idioma (
  persona_id integer not null references personas(id),
  idioma text not null,
  nivel text not null,
  fecha date not null,
  direccion_territorial text,
  primary key (persona_id, idioma)
);
alter table acreditaciones_idioma enable row level security;
revoke all on acreditaciones_idioma from anon, authenticated;

create table if not exists fuentes_idioma (
  url text primary key,
  provincia text,
  curso text,
  nombre_archivo text,
  fecha date,
  estado text,
  personas integer,
  motivo text,
  procesado_en timestamptz default now()
);
alter table fuentes_idioma enable row level security;
revoke all on fuentes_idioma from anon, authenticated;

create table if not exists idiomas_revisar (
  id bigint generated always as identity primary key,
  fuente_url text,
  nombre text,
  nivel text,
  motivo text,
  creado timestamptz default now()
);
alter table idiomas_revisar enable row level security;
revoke all on idiomas_revisar from anon, authenticated;

drop function if exists mi_acreditacion_idiomas();
create function mi_acreditacion_idiomas()
returns table(idioma text, nivel text, fecha date, direccion_territorial text)
language sql stable security definer set search_path = public as $$
  select a.idioma, a.nivel, a.fecha, a.direccion_territorial
  from identidades d join acreditaciones_idioma a on a.persona_id = d.persona_id
  where d.user_id = auth.uid();
$$;
revoke execute on function mi_acreditacion_idiomas() from public, anon;
grant execute on function mi_acreditacion_idiomas() to authenticated;
"""


def asegurar_esquema():
    psql(ESQUEMA)


# ---------------------------------------------------------------- descubrir carpetas y PDF en la web ----
class DescubridorCarpetas(HTMLParser):
    """Encuentra, en la página raíz de 'Listados definitivos', cada enlace a una carpeta de curso
    (href con /folder/<id> y, dentro del propio <a>, un texto 'AAAA-AAAA') y lo asocia a la
    dirección territorial (València/Castellón/Alicante) bajo la que aparece en el árbol."""
    def __init__(self):
        super().__init__()
        self.provincia_actual = None
        self.en_a = False
        self.href_actual = None
        self.texto_actual = ""
        self.carpetas = []  # [(provincia, curso, url)]

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            d = dict(attrs)
            href = d.get("href", "")
            if "/folder/" in href:
                self.en_a = True
                self.href_actual = href
                self.texto_actual = ""

    def handle_endtag(self, tag):
        if tag == "a" and self.en_a:
            self.en_a = False
            m = re.search(r'(\d{4}-\d{4})', self.texto_actual)
            if m and self.provincia_actual:
                self.carpetas.append((self.provincia_actual, m.group(1), self.href_actual))

    def handle_data(self, data):
        txt = data.strip()
        if txt in PROVINCIAS:
            self.provincia_actual = txt
        if self.en_a:
            self.texto_actual += data


def _get(url):
    rq = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'text/html'})
    with urllib.request.urlopen(rq, timeout=40) as r:
        return r.read().decode('utf-8', 'replace')


def listar_carpetas():
    d = DescubridorCarpetas()
    d.feed(_get(RAIZ))
    return d.carpetas


PDF_RE = re.compile(r'<a\s+href="([^"]+?\.pdf[^"]*)"\s+title="([^"]+?\.pdf)"', re.I)


def listar_pdfs(url_carpeta):
    texto = _get(url_carpeta)
    vistos, out = set(), []
    for href, nombre in PDF_RE.findall(texto):
        url = urllib.parse.urljoin(url_carpeta, href)
        if url not in vistos:
            vistos.add(url)
            out.append((nombre, url))
    return out


NOMBRE_FECHA_RE = re.compile(r'^(\d{4})[_-](\d{2})[_-](\d{2})\.pdf$', re.I)


def fecha_de_nombre(nombre):
    m = NOMBRE_FECHA_RE.match(nombre)
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def descargar_pdf(url, destino):
    rq = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(rq, timeout=120) as r:
        datos = r.read()
    if not datos.startswith(b'%PDF'):
        raise Fallo('la respuesta no parece un PDF')
    with open(destino, 'wb') as f:
        f.write(datos)
    return len(datos)


# ---------------------------------------------------------------- extracción del PDF (genérica) ----
def filas_de_pagina(words):
    """Agrupa las palabras de una página por fila (misma altura, con tolerancia), y dentro de
    cada fila las deja ordenadas de izquierda a derecha (el orden de extracción de pdfplumber
    puede no coincidir exactamente con el orden visual cuando dos columnas tienen una altura
    ligeramente distinta dentro de la misma fila visual)."""
    filas, cur, cur_top = [], [], None
    for w in sorted(words, key=lambda w: (round(w['top']), w['x0'])):
        if cur and abs(w['top'] - cur_top) > TOL:
            filas.append(cur)
            cur = []
        cur.append(w)
        if len(cur) == 1:
            cur_top = w['top']
    if cur:
        filas.append(cur)
    return [sorted(f, key=lambda w: w['x0']) for f in filas]


def extraer_ingles(ruta):
    """-> (filas[(nombre, nivel)], páginas totales, páginas con texto extraíble).
    No asume columnas fijas: en cada fila busca un nivel (B2/C1/C2) junto a una palabra de
    idioma (anglès/inglés); todo lo que hay en la fila ANTES de esa palabra es el nombre.
    Así ignora automáticamente las filas de otros idiomas y las del Anexo de excluidos
    (que no tienen columna de nivel)."""
    import pdfplumber
    out, paginas_con_texto = [], 0
    with pdfplumber.open(ruta) as pdf:
        total = len(pdf.pages)
        for page in pdf.pages:
            words = page.extract_words()
            if words:
                paginas_con_texto += 1
            for fila in filas_de_pagina(words):
                textos = [w['text'] for w in fila]
                idx_idioma = [i for i, t in enumerate(textos) if IDIOMA_RE.search(t)]
                idx_nivel = [i for i, t in enumerate(textos) if NIVEL_RE.match(t)]
                if not idx_idioma or not idx_nivel:
                    continue
                nombre_tokens = textos[:min(idx_idioma)]
                if not nombre_tokens:
                    continue
                out.append((' '.join(nombre_tokens), textos[idx_nivel[0]]))
    return out, total, paginas_con_texto


def clasificar(filas):
    """Separa en: cargables, excluidos por homónimo (mismo nombre normalizado repetido en
    el propio PDF con niveles distintos: no se puede saber cuál es cuál) y sospechosos
    (parecen tener dos apellidos pegados sin espacio: fallo típico de extracción del PDF
    de origen, no de nuestro analizador)."""
    buenas, sospechosas = [], []
    for nombre, nivel in filas:
        primera = nombre.split(' ', 1)[0]
        (sospechosas if len(primera) > LARGO_SOSPECHOSO else buenas).append((nombre, nivel))
    cnt = Counter(norm(n) for n, _ in buenas)
    ambiguos = {nn for nn, c in cnt.items() if c > 1}
    cargables = [(n, nv) for n, nv in buenas if norm(n) not in ambiguos]
    homonimos = [(n, nv) for n, nv in buenas if norm(n) in ambiguos]
    return cargables, homonimos, sospechosas


# ---------------------------------------------------------------- carga en la base de datos ----
def cargar_en_bd(cargables, fecha, provincia, fuente_url, revisar=()):
    if cargables:
        valores = ",\n".join(f"({lit(norm(n))},{lit(nv)})" for n, nv in cargables)
        psql(f"""
begin;
create temp table t_acred (nn text, nivel text) on commit drop;
insert into t_acred (nn, nivel) values
{valores};
insert into acreditaciones_idioma (persona_id, idioma, nivel, fecha, direccion_territorial)
  select p.id, {lit(IDIOMA)}, t.nivel, {lit(fecha.isoformat())}, {lit(provincia)}
  from t_acred t join personas p on p.nombre_norm = t.nn
  on conflict (persona_id, idioma) do update
    set nivel = excluded.nivel, fecha = excluded.fecha, direccion_territorial = excluded.direccion_territorial
    where excluded.fecha >= acreditaciones_idioma.fecha;
commit;
""")
    for nombre, nivel, motivo in revisar:
        psql(f"insert into idiomas_revisar (fuente_url, nombre, nivel, motivo) values ({lit(fuente_url)}, {lit(nombre)}, {lit(nivel)}, {lit(motivo)})")


# ---------------------------------------------------------------- flujo principal ----
def procesar_pdf(ruta, provincia, fecha, fuente_url, real):
    filas, total_pag, pag_con_texto = extraer_ingles(ruta)
    if pag_con_texto == 0:
        raise Fallo('el PDF no tiene texto extraíble (¿escaneado?); necesita revisión manual')
    cargables, homonimos, sospechosas = clasificar(filas)
    if not filas:
        raise Fallo(f'no se ha encontrado ninguna fila de inglés (B2/C1/C2) en {total_pag} página(s); revisar a mano')
    revisar = [(n, nv, 'homónimo: mismo nombre y distinto nivel en este listado') for n, nv in homonimos]
    revisar += [(n, nv, 'nombre sospechoso (posibles dos apellidos pegados en el PDF de origen)') for n, nv in sospechosas]
    if real:
        cargar_en_bd(cargables, fecha, provincia, fuente_url, revisar)
    return len(cargables), len(homonimos), len(sospechosas)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--local', help='Probar con un PDF ya descargado, sin tocar la web ni fuentes_idioma')
    ap.add_argument('--provincia', default='València')
    ap.add_argument('--fecha', help='AAAA-MM-DD; si no se da, se intenta sacar del nombre del archivo (AAAA_MM_DD.pdf)')
    a = ap.parse_args()
    real = os.environ.get('AUTO_CARGA', '').strip() == '1'
    print('Acreditación de idiomas ·', 'REAL' if real else 'SIMULACIÓN (no escribe nada en la base de datos)')

    if a.local:
        fecha = datetime.date.fromisoformat(a.fecha) if a.fecha else fecha_de_nombre(os.path.basename(a.local))
        if not fecha:
            raise SystemExit('No se pudo deducir la fecha del nombre del archivo; indícala con --fecha AAAA-MM-DD')
        if real:
            asegurar_esquema()
        n, h, s = procesar_pdf(a.local, a.provincia, fecha, 'local:' + a.local, real)
        print(f'{n} cargables, {h} homónimos excluidos, {s} sospechosos excluidos (de {n + h + s} filas de inglés)')
        return

    if real:
        asegurar_esquema()
    try:
        ya = set(psql("select url from fuentes_idioma", tuples=True).splitlines())
    except Exception as e:
        print('  no se pudo leer fuentes_idioma (¿primera vez?):', repr(e)[:150]); ya = set()

    print('Buscando carpetas en', RAIZ)
    carpetas = listar_carpetas()
    print(f'{len(carpetas)} carpeta(s) de curso encontradas')
    nuevos, con_error, personas_nuevas = 0, 0, 0
    with_tmp = __import__('tempfile').TemporaryDirectory()
    tmp = with_tmp.name
    for provincia, curso, url_carpeta in carpetas:
        time.sleep(PAUSA)
        try:
            pdfs = listar_pdfs(url_carpeta)
        except Exception as e:
            print(f'  [{provincia} {curso}] no se pudo leer la carpeta:', repr(e)[:150]); continue
        for nombre, url_pdf in pdfs:
            if url_pdf in ya:
                continue
            fecha = fecha_de_nombre(nombre)
            if not fecha:
                print(f'  [{provincia} {curso}] {nombre}: nombre de archivo inesperado, lo salto'); continue
            time.sleep(PAUSA)
            ruta = os.path.join(tmp, re.sub(r'[^\w.\-]', '_', nombre))
            try:
                peso = descargar_pdf(url_pdf, ruta)
                n, h, s = procesar_pdf(ruta, provincia, fecha, url_pdf, real)
                print(f'  [{provincia} {curso}] {nombre} ({peso // 1024} KB): {n} cargables, {h} homónimos, {s} sospechosos')
                nuevos += 1; personas_nuevas += n
                if real:
                    psql(f"insert into fuentes_idioma (url, provincia, curso, nombre_archivo, fecha, estado, personas) "
                         f"values ({lit(url_pdf)}, {lit(provincia)}, {lit(curso)}, {lit(nombre)}, {lit(fecha.isoformat())}, 'ok', {n}) "
                         f"on conflict (url) do update set estado = excluded.estado, personas = excluded.personas, procesado_en = now()")
            except Exception as e:
                con_error += 1
                motivo = str(e)[:300] if isinstance(e, Fallo) else ('error inesperado: ' + repr(e)[:250])
                print(f'  [{provincia} {curso}] {nombre}: ERROR ·', motivo)
                if real:
                    psql(f"insert into fuentes_idioma (url, provincia, curso, nombre_archivo, fecha, estado, motivo) "
                         f"values ({lit(url_pdf)}, {lit(provincia)}, {lit(curso)}, {lit(nombre)}, {lit(fecha.isoformat() if fecha else None) if fecha else 'null'}, 'error', {lit(motivo)}) "
                         f"on conflict (url) do update set estado = excluded.estado, motivo = excluded.motivo, procesado_en = now()")
    with_tmp.cleanup()
    print(f'\nPDF nuevos procesados: {nuevos} (personas cargadas: {personas_nuevas}) · con error: {con_error}')
    if not real:
        print('(simulación: no se ha escrito nada en la base de datos; lánzalo a mano con "Descargar y registrar de verdad" para cargarlo.)')
    if real and (nuevos or con_error):
        try:
            import avisos
            resumen = f'{personas_nuevas} persona(s) con inglés acreditado cargadas desde {nuevos} PDF' + (f' · {con_error} con error, revisar' if con_error else '')
            avisos.avisar_admin(psql, lit, [('Acreditación de idiomas (inglés)', con_error == 0, resumen, None)],
                                 titulo='🌍 Acreditación de idiomas actualizada' if con_error == 0 else '⚠️ Acreditación de idiomas: hubo errores')
        except Exception as e:
            print('  no se pudo avisar a los administradores:', repr(e)[:150])


if __name__ == '__main__':
    main()
