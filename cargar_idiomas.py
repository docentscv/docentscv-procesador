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
  · Algunos PDF (sobre todo los más recientes) no tienen la tabla como texto real, sino
    dibujada como líneas vectoriales; en ese caso se lee por OCR (Tesseract, a 300dpi). El
    nombre se usa igual (si el OCR se equivoca en una letra, esa persona simplemente no
    coincide con nadie y no se carga; no se carga mal). El nivel (B2/C1/C2), en cambio,
    solo se acepta si se lee exacto o si hay una única corrección evidente a un carácter
    de distancia; si el OCR del nivel es ambiguo, se manda igualmente a `idiomas_revisar`
    en vez de adivinar.
  · Solo apunta la acreditación a personas que YA existen en la base (de alguna lista de
    bolsa); no crea personas nuevas solo por tener el idioma acreditado.

Uso:
  python3 cargar_idiomas.py                    # recorrer toda la web (real solo si AUTO_CARGA=1)
  python3 cargar_idiomas.py --local X.pdf --provincia València [--fecha AAAA-MM-DD]   # probar con un PDF local
"""
import os, re, sys, csv, time, argparse, datetime, subprocess, urllib.request, urllib.parse
from html.parser import HTMLParser
from collections import Counter

RAIZ = 'https://ceice.gva.es/es/web/rrhh-educacion/listados-definitivos'
UA = 'Mozilla/5.0 (compatible; DocentsCV/1.0; +https://docentscv.github.io/)'
PROVINCIAS = {"València", "Castellón", "Alicante"}
IDIOMA = 'Anglès'
IDIOMA_RE = re.compile(r'angl|ingl', re.I)
NIVEL_RE = re.compile(r'^(B2|C1|C2)$')
NIVELES = ('B2', 'C1', 'C2')
TOL = 2.5             # tolerancia (puntos) para agrupar palabras en la misma fila de una tabla
TOL_OCR = 9            # igual, pero para filas leídas por OCR (las cajas de palabra son menos precisas)
OCR_DPI = 300          # resolución para el OCR; por debajo de esto empeora mucho el reconocimiento
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
def filas_de_pagina(words, tol=TOL):
    """Agrupa las palabras de una página por fila (misma altura, con tolerancia), y dentro de
    cada fila las deja ordenadas de izquierda a derecha (el orden de extracción de pdfplumber,
    o las cajas del OCR, pueden no coincidir exactamente con el orden visual cuando dos
    columnas tienen una altura ligeramente distinta dentro de la misma fila visual). Compara
    cada palabra con la media de la fila que se está formando (no solo con la primera), para
    no perder la fila por el ruido de posición típico del OCR."""
    filas, cur, suma_top, n = [], [], 0.0, 0
    for w in sorted(words, key=lambda w: (round(w['top']), w['x0'])):
        if cur and abs(w['top'] - suma_top / n) > tol:
            filas.append(cur)
            cur, suma_top, n = [], 0.0, 0
        cur.append(w)
        suma_top += w['top']
        n += 1
    if cur:
        filas.append(cur)
    return [sorted(f, key=lambda w: w['x0']) for f in filas]


def _distancia(a, b):
    """Distancia de Levenshtein entre dos cadenas cortas (sin dependencias externas)."""
    if a == b:
        return 0
    fila = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        nueva = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            nueva[j] = min(fila[j] + 1, nueva[j - 1] + 1, fila[j - 1] + (ca != cb))
        fila = nueva
    return fila[-1]


def candidatos_nivel(crudo):
    """Candidatos de nivel (B2/C1/C2) a los que se parece un texto corto leído por OCR, a
    lo sumo a una letra de distancia (p.ej. 'Ct'->C1, 'c1'->C1, 'e2'->B2: la OCR a veces
    confunde la primera letra). Quita antes el ruido típico de las líneas de una tabla
    (corchetes, barras) pegado al texto. Lista vacía: no se parece a ningún nivel, no es
    una celda de nivel (p.ej. una palabra suelta cualquiera) y hay que ignorarla sin más.
    Un único candidato: se puede corregir con seguridad. Más de uno (p.ej. 'Cc', igual de
    parecido a C1 y a C2): empate genuino, no se adivina, se manda a revisar a mano."""
    t = re.sub(r'[^A-Z0-9]', '', crudo.upper())
    if len(t) != 2:
        return []
    if t in NIVELES:
        return [t]
    return [niv for niv in NIVELES if _distancia(t, niv) <= 1]


def _palabras_ocr(page):
    """OCR de una página sin texto extraíble (escaneada, o con la tabla dibujada como
    curvas vectoriales en vez de texto real -visto en algún PDF de la Conselleria-).
    Devuelve las palabras en el mismo formato que extract_words() (text/x0/top), para
    poder reutilizar filas_de_pagina(). Prueba primero el modelo de español; si no está
    instalado (p.ej. en pruebas locales sin red), usa el de inglés como alternativa: los
    niveles B2/C1/C2 se leen igual de bien con cualquiera de los dos."""
    import pytesseract
    imagen = page.to_image(resolution=OCR_DPI).original
    try:
        datos = pytesseract.image_to_data(imagen, lang='spa', output_type=pytesseract.Output.DICT)
    except Exception:
        datos = pytesseract.image_to_data(imagen, lang='eng', output_type=pytesseract.Output.DICT)
    return [
        {'text': datos['text'][i].strip(), 'x0': float(datos['left'][i]),
         'x1': float(datos['left'][i] + datos['width'][i]), 'top': float(datos['top'][i])}
        for i in range(len(datos['text'])) if datos['text'][i].strip()
    ]


def fila_valida(textos, ocr):
    """Si la fila tiene pinta de ser una fila de 'inglés' (nombre + Idioma + Nivel), devuelve
    (nombre, nivel, duda): duda es None si el nivel se reconoce con seguridad, o un texto
    explicando la duda si viene de OCR y el nivel no se puede asegurar. Si la fila no tiene
    columna de idioma reconocible, devuelve None (no es una fila de inglés: cabecera, pie,
    otro idioma, o el Anexo de excluidos). Para texto extraído directamente del PDF (ocr=False)
    exige un nivel exacto, como siempre; el margen para corregir ruido de reconocimiento solo
    se aplica a filas que vienen de OCR."""
    idx_idioma = [i for i, t in enumerate(textos) if IDIOMA_RE.search(t)]
    if not idx_idioma:
        return None
    primero = min(idx_idioma)
    nombre_tokens = textos[:primero]
    if not nombre_tokens:
        return None
    resto = textos[primero + 1:]
    exactos = [t for t in resto if NIVEL_RE.match(t)]
    if exactos:
        return ' '.join(nombre_tokens), exactos[0], None
    if not ocr:
        return None
    for crudo in resto:
        candidatos = candidatos_nivel(crudo)
        if not candidatos:
            continue
        if len(candidatos) == 1:
            return ' '.join(nombre_tokens), candidatos[0], None
        return ' '.join(nombre_tokens), crudo, f'nivel no reconocido con confianza por OCR (leído: "{crudo}")'
    return None


# -- formato "ancho": algunos listados antiguos no ponen una fila por idioma con la palabra
# "Anglès"/"Inglés" al lado, sino una columna por idioma (Ang/Fra/Ale/Ita) y el nivel metido
# en la columna que toque; aquí no hay ninguna palabra de idioma en la fila de cada persona,
# así que fila_valida() nunca encuentra nada y hay que localizar la columna por su posición.
REL_JUR_RE = re.compile(r'^(FI|FC|FP)$')
_PATRONES_CABECERA_ANCHA = {
    'ang': re.compile(r'^(ang|angl|ingl)', re.I),
    'fra': re.compile(r'^(fra|franc)', re.I),
    'ale': re.compile(r'^(ale|alem)', re.I),
    'ita': re.compile(r'^(ita|ital)', re.I),
}


def _col_idioma_ancho(filas):
    """Busca, entre las filas de una página, la fila de cabecera del formato "ancho" (las
    columnas Ang/Fra/Ale/Ita juntas en la misma fila). Quita primero el ruido típico del OCR
    con tablas (corchetes y barras de las líneas de la rejilla pegadas a la palabra, p.ej.
    "[Ang]"). Exige encontrar "Ang" y al menos otras dos de las tres restantes (en un PDF
    escaneado de mala calidad alguna puede salir ilegible, como "Ita" partida en dos) para
    no confundir una fila cualquiera con la cabecera. Si la encuentra, devuelve ((x0, x1)
    de la columna de inglés, la fila de cabecera en sí); si no, (None, None)."""
    for fila in filas:
        hallados = {}
        for w in fila:
            limpio = w['text'].strip('[]{}|_').strip()
            if not limpio or len(limpio) > 8:
                continue
            for clave, patron in _PATRONES_CABECERA_ANCHA.items():
                if clave not in hallados and patron.match(limpio):
                    hallados[clave] = (w['x0'], w['x1'])
        if 'ang' in hallados and len(hallados) >= 3:
            return hallados['ang'], fila
    return None, None


def filas_formato_ancho(filas, fila_cabecera, col_ang, ocr):
    """Para el formato "ancho": el nombre es todo lo que hay antes del código de relación
    jurídica (FI/FC/FP) o de la letra suelta de la columna "Cos" (M/P); el nivel es
    cualquier B2/C1/C2 (o, en OCR, un valor parecido que se pueda corregir con seguridad)
    cuyo x0 caiga dentro de la columna de inglés detectada en la cabecera."""
    x0_ang, x1_ang = col_ang
    margen = 15 if ocr else 6
    buenas, ambiguas = [], []
    for fila in filas:
        if fila is fila_cabecera:
            continue
        fila = sorted(fila, key=lambda w: w['x0'])
        nombre_tokens = []
        for w in fila:
            if REL_JUR_RE.match(w['text']) or (len(w['text']) == 1 and w['text'].isalpha()):
                break
            nombre_tokens.append(w['text'])
        if not nombre_tokens:
            continue
        for w in fila:
            if not (x0_ang - margen <= w['x0'] <= x1_ang + margen):
                continue
            limpio = w['text'].strip('[]{}|_').strip()
            if NIVEL_RE.match(limpio):
                buenas.append((' '.join(nombre_tokens), limpio))
                break
            if not ocr:
                continue
            candidatos = candidatos_nivel(limpio)
            if len(candidatos) == 1:
                buenas.append((' '.join(nombre_tokens), candidatos[0]))
                break
            if len(candidatos) > 1:
                ambiguas.append((' '.join(nombre_tokens), limpio,
                                  f'nivel no reconocido con confianza por OCR (leído: "{limpio}")'))
                break
    return buenas, ambiguas


def extraer_ingles(ruta):
    """-> (filas[(nombre, nivel)], ambiguas[(nombre, nivel_crudo, motivo)], páginas totales,
    páginas con texto, páginas leídas por OCR).
    Reconoce dos formatos de tabla: el habitual (una fila por idioma, con la palabra
    "Anglès"/"Inglés" al lado del nivel: ver fila_valida) y uno "ancho" usado en algunos
    listados antiguos (una columna por idioma: Ang/Fra/Ale/Ita, con el nivel metido en la
    columna que toque: ver filas_formato_ancho). No asume columnas fijas en el primer
    formato: todo lo que hay en la fila ANTES de la palabra de idioma es el nombre, así que
    ignora automáticamente las filas de otros idiomas y las del Anexo de excluidos. Si una
    página no tiene texto extraíble, se lee por OCR en vez de dejarla en blanco (ver
    _palabras_ocr); las filas de OCR con el nivel ambiguo van a `ambiguas`, nunca se
    adivinan, en ninguno de los dos formatos."""
    import pdfplumber
    paginas = []  # [(ocr, filas_de_la_pagina)]
    paginas_con_texto = paginas_ocr = 0
    with pdfplumber.open(ruta) as pdf:
        total = len(pdf.pages)
        for page in pdf.pages:
            words = page.extract_words()
            ocr = False
            if not words:
                try:
                    words = _palabras_ocr(page)
                    ocr = bool(words)
                except Exception as e:
                    print('  (aviso: no se pudo hacer OCR de una página:', repr(e)[:150], ')')
                    words = []
            if not words:
                continue
            paginas_con_texto += 1
            if ocr:
                paginas_ocr += 1
            paginas.append((ocr, filas_de_pagina(words, TOL_OCR if ocr else TOL)))

    col_ang = fila_cabecera = None
    for _, filas in paginas:
        col_ang, fila_cabecera = _col_idioma_ancho(filas)
        if col_ang:
            break

    filas_out, ambiguas = [], []
    for ocr, filas in paginas:
        for fila in filas:
            if fila is fila_cabecera:
                continue
            r = fila_valida([w['text'] for w in fila], ocr)
            if not r:
                continue
            nombre, nivel, duda = r
            if duda:
                ambiguas.append((nombre, nivel, duda))
            else:
                filas_out.append((nombre, nivel))
        if col_ang:
            extra, extra_amb = filas_formato_ancho(filas, fila_cabecera, col_ang, ocr)
            filas_out += extra
            ambiguas += extra_amb
    return filas_out, ambiguas, total, paginas_con_texto, paginas_ocr


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
    filas, ambiguas, total_pag, pag_con_texto, pag_ocr = extraer_ingles(ruta)
    if pag_con_texto == 0:
        raise Fallo('el PDF no tiene texto extraíble ni se ha podido leer por OCR; necesita revisión manual')
    cargables, homonimos, sospechosas = clasificar(filas)
    if not filas and not ambiguas:
        raise Fallo(f'no se ha encontrado ninguna fila de inglés (B2/C1/C2) en {total_pag} página(s); revisar a mano')
    revisar = [(n, nv, 'homónimo: mismo nombre y distinto nivel en este listado') for n, nv in homonimos]
    revisar += [(n, nv, 'nombre sospechoso (posibles dos apellidos pegados en el PDF de origen)') for n, nv in sospechosas]
    revisar += ambiguas
    if real:
        cargar_en_bd(cargables, fecha, provincia, fuente_url, revisar)
    return len(cargables), len(homonimos), len(sospechosas), len(ambiguas), pag_ocr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--local', help='Probar con un PDF ya descargado, sin tocar la web ni fuentes_idioma')
    ap.add_argument('--provincia', default='València')
    ap.add_argument('--fecha', help='AAAA-MM-DD; si no se da, se intenta sacar del nombre del archivo (AAAA_MM_DD.pdf)')
    ap.add_argument('--todos', action='store_true',
                     help='Volver a analizar TODOS los PDF encontrados en la web (también los ya procesados antes), '
                          'sin escribir nada en la base de datos, y guardar un informe en informe_idiomas.csv')
    ap.add_argument('--estado', action='store_true',
                     help='Solo mira lo que ya hay en la base de datos (cifras, sin nombres): cuántas acreditaciones '
                          'hay cargadas y cuánta gente sigue pendiente de revisión manual en idiomas_revisar, '
                          'con el motivo agrupado (OCR ambiguo, homónimo, nombre sospechoso)')
    a = ap.parse_args()

    if a.estado:
        print('--- acreditaciones_idioma (cargadas de verdad) ---')
        print(psql("select idioma, nivel, count(*) as personas from acreditaciones_idioma "
                    "group by idioma, nivel order by idioma, nivel;"))
        print('--- idiomas_revisar (pendiente de decidir a mano; no cuenta como acreditado) ---')
        print(psql("""
select
  case
    when motivo like 'nivel no reconocido%' then 'ocr: nivel ambiguo (p.ej. B2 vs C2)'
    when motivo like 'homónimo%'            then 'homónimo (mismo nombre, dos niveles en el mismo listado)'
    when motivo like 'nombre sospechoso%'   then 'nombre sospechoso (posible fallo de extracción del PDF)'
    else 'otro'
  end as motivo_agrupado,
  count(*) as filas,
  count(distinct nombre) as personas_distintas,
  count(distinct fuente_url) as pdf_distintos,
  min(creado)::date as mas_antiguo,
  max(creado)::date as mas_reciente
from idiomas_revisar
group by 1
order by filas desc;
"""))
        return
    real = os.environ.get('AUTO_CARGA', '').strip() == '1'
    print('Acreditación de idiomas ·', 'REAL' if real else 'SIMULACIÓN (no escribe nada en la base de datos)')

    if a.local:
        fecha = datetime.date.fromisoformat(a.fecha) if a.fecha else fecha_de_nombre(os.path.basename(a.local))
        if not fecha:
            raise SystemExit('No se pudo deducir la fecha del nombre del archivo; indícala con --fecha AAAA-MM-DD')
        if real:
            asegurar_esquema()
        n, h, s, a_, o = procesar_pdf(a.local, a.provincia, fecha, 'local:' + a.local, real)
        print(f'{n} cargables, {h} homónimos excluidos, {s} sospechosos excluidos, {a_} ambiguos de OCR excluidos '
              f'(de {n + h + s + a_} filas de inglés; {o} página(s) leídas por OCR)')
        return

    if a.todos:
        print('Modo informe: se vuelven a analizar TODOS los PDF encontrados en la web (también los ya '
              'procesados antes). No se escribe nada en la base de datos pase lo que pase.')
        print('Buscando carpetas en', RAIZ)
        carpetas = listar_carpetas()
        print(f'{len(carpetas)} carpeta(s) de curso encontradas')
        campos = ['provincia', 'curso', 'archivo', 'fecha', 'url', 'estado', 'cargables', 'homonimos', 'sospechosos', 'ambiguos', 'paginas_ocr', 'motivo']
        filas_csv, ok, con_error = [], 0, 0
        with_tmp = __import__('tempfile').TemporaryDirectory()
        tmp = with_tmp.name
        for provincia, curso, url_carpeta in carpetas:
            time.sleep(PAUSA)
            try:
                pdfs = listar_pdfs(url_carpeta)
            except Exception as e:
                print(f'  [{provincia} {curso}] no se pudo leer la carpeta:', repr(e)[:150]); continue
            for nombre, url_pdf in pdfs:
                fila = dict.fromkeys(campos, '')
                fila.update(provincia=provincia, curso=curso, archivo=nombre, url=url_pdf,
                            cargables=0, homonimos=0, sospechosos=0, ambiguos=0, paginas_ocr=0)
                fecha = fecha_de_nombre(nombre)
                if not fecha:
                    fila.update(estado='error', motivo='nombre de archivo inesperado, no se pudo deducir la fecha')
                    print(f'  [{provincia} {curso}] {nombre}: nombre de archivo inesperado, lo salto')
                    con_error += 1; filas_csv.append(fila); continue
                fila['fecha'] = fecha.isoformat()
                time.sleep(PAUSA)
                ruta = os.path.join(tmp, re.sub(r'[^\w.\-]', '_', nombre))
                try:
                    peso = descargar_pdf(url_pdf, ruta)
                    n, h, s, a_, o = procesar_pdf(ruta, provincia, fecha, url_pdf, False)
                    fila.update(estado='ok', cargables=n, homonimos=h, sospechosos=s, ambiguos=a_, paginas_ocr=o)
                    ocr_txt = f', {o} pág. por OCR' if o else ''
                    print(f'  [{provincia} {curso}] {nombre} ({peso // 1024} KB): {n} cargables, {h} homónimos, '
                          f'{s} sospechosos, {a_} ambiguos de OCR{ocr_txt}')
                    ok += 1
                except Exception as e:
                    motivo = str(e)[:300] if isinstance(e, Fallo) else ('error inesperado: ' + repr(e)[:250])
                    fila.update(estado='error', motivo=motivo)
                    con_error += 1
                    print(f'  [{provincia} {curso}] {nombre}: ERROR ·', motivo)
                finally:
                    if os.path.exists(ruta):
                        os.remove(ruta)
                filas_csv.append(fila)
        with_tmp.cleanup()
        with open('informe_idiomas.csv', 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=campos)
            w.writeheader()
            w.writerows(filas_csv)
        print(f'\nInforme guardado en informe_idiomas.csv: {len(filas_csv)} PDF analizados · {ok} correctos · {con_error} con error')
        errores = [f for f in filas_csv if f['estado'] == 'error']
        if errores:
            print(f'\nPDF a revisar ({len(errores)}) — no se pudo sacar nada en claro, hay que ver cómo bajarlos:')
            for f in errores:
                print(f"  - [{f['provincia']} {f['curso']}] {f['archivo']}: {f['motivo']}")
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
                n, h, s, a_, o = procesar_pdf(ruta, provincia, fecha, url_pdf, real)
                ocr_txt = f', {o} pág. por OCR' if o else ''
                print(f'  [{provincia} {curso}] {nombre} ({peso // 1024} KB): {n} cargables, {h} homónimos, '
                      f'{s} sospechosos, {a_} ambiguos de OCR{ocr_txt}')
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
