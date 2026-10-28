#!/usr/bin/env python3
"""DocentsCV · MODO PRUDENTE: mira las páginas de la Conselleria y APUNTA qué PDF encuentra.
No descarga ni carga nada en la base de datos. Sirve para comprobar que GitHub puede leer la web
y que reconocemos los nombres de archivo (lis_mae, lis_sec, pue_prov, pue_def)."""
import re, sys, urllib.request, urllib.parse, html

PAGINAS = [   # (qué es, dirección)
    ('Plazas ofertadas de adjudicación continua (salen lunes y miércoles)', 'https://ceice.gva.es/es/web/rrhh-educacion/convocatoria-y-peticion-telematica'),
    ('Resolución de la adjudicación continua (salen martes y jueves)',      'https://ceice.gva.es/es/web/rrhh-educacion/resolucion'),
    ('Plazas de difícil cobertura (siempre jueves)',                        'https://ceice.gva.es/es/web/rrhh-educacion/convocatoria-y-peticion-telematica6'),
    ('Resolución de difícil cobertura (salen los viernes)',                 'https://ceice.gva.es/es/web/rrhh-educacion/resolucion1'),
]
UA = 'Mozilla/5.0 (compatible; DocentsCV/1.0; +https://docentscv.github.io/)'
# AAMMDD_lis_mae.pdf · AAMMDD_lis_sec.pdf · AAMMDD_pue_prov.pdf · AAMMDD_pue_def.pdf
NOMBRE = re.compile(r'(\d{6})_(lis_mae|lis_sec|pue_prov|pue_def)\.pdf$', re.I)


def leer(url):
    rq = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': '*/*'})
    with urllib.request.urlopen(rq, timeout=40) as r:
        return r.status, r.read().decode('utf-8', 'replace')


def enlaces_pdf(texto, base):
    """Todos los enlaces que parecen PDF (href o texto), ya como URL completa y sin repetir."""
    out, vistos = [], set()
    for h in re.findall(r'href\s*=\s*["\']([^"\']+)["\']', texto, flags=re.I):
        h = html.unescape(h).strip()
        if '.pdf' not in h.lower():
            continue
        u = urllib.parse.urljoin(base, h)
        if u not in vistos:
            vistos.add(u); out.append(u)
    return out


def nombre_de(url):
    segs = [urllib.parse.unquote(x) for x in urllib.parse.urlparse(url).path.split('/') if x]
    pdfs = [x for x in segs if x.lower().endswith('.pdf')]      # en la web de la Conselleria el nombre puede no ser el último trozo
    return pdfs[-1] if pdfs else (segs[-1] if segs else '')


def main():
    try:
        st, rb = leer('https://ceice.gva.es/robots.txt')
        print(f'--- robots.txt (HTTP {st}) ---\n{rb[:1500]}\n')
    except Exception as e:
        print('robots.txt: no se pudo leer ->', e)
    total = 0
    for tit, p in PAGINAS:
        print('==', tit, '\n  ', p)
        try:
            st, t = leer(p)
        except Exception as e:
            print('   NO SE PUDO LEER:', e); continue
        ls = enlaces_pdf(t, p)
        print(f'   HTTP {st} · {len(t)} caracteres · {len(ls)} enlaces a PDF')
        for u in ls:
            n = nombre_de(u); m = NOMBRE.search(n)
            print(('   [RECONOCIDO] ' if m else '   [otro]       ') + n + '  <-  ' + u)
            total += 1 if m else 0
    print(f'\nPDF reconocidos en total: {total}')


if __name__ == '__main__':
    main()
