#!/usr/bin/env python3
"""Extrae adjudicaciones de inicio de curso (Maestros) de los PDF de la Conselleria.
Uso: python3 parse_adjudicaciones.py listado.pdf salida.csv [especialidad]
No guarda nombres de personas."""
import re, sys, csv, subprocess, collections
PROV = {'03': 'Alicante', '12': 'Castelló', '46': 'València'}
HDR = re.compile(r'\f?\s*ADJUDICACI[ÓO] DE PERSONAL DOCENT[^\n]*\n\s*\d\d/\d\d/\d{4}\s*\n\s*ADJUDICACI[ÓO]N DE PERSONAL DOCENTE[^\n]*\n\s*Mestres / Maestros\s*\n\s*MESTRES\s*\n')
REC = re.compile(r'(\d{6})\s+(.+?)\((\d{8})\)(.+?)\n\s*([0-9A-Z]{3}) / (.+?)\n(.*?)(VACANT|SUBSTITUCI[ÓO][^\n]*?)\s{2,}', re.S)

def nombre_de(texto):
    l = texto.split('\n', 1)[0]
    return re.sub(r'\s+', ' ', re.split(r'Petici[óo]n:', l)[0]).strip()

def parse(pdf):
    txt = subprocess.run(['pdftotext', '-layout', pdf, '-'], capture_output=True, text=True).stdout
    txt = HDR.sub('\n', txt)
    parts = re.split(r'^(\d+)\s{2,}(?=[A-ZÀ-ÚÑ])', txt, flags=re.M)
    out = []
    for i in range(1, len(parts), 2):
        pos, body = int(parts[i]), parts[i + 1]
        s = re.findall(r'(Desactivat|No adjudicat|Adjudicat|No ha participat|Ha participat)\s*$', body, re.M)
        r = {'pos': pos, 'nombre': nombre_de(body), 'st': s[-1] if s else None}
        m = REC.search(body)
        if m:
            h = re.search(r'(\d+) horas|Jornada completa', body)
            r.update(code=m[1], loc=m[2].strip(), cen=m[3], nom=m[4].strip(), esp=m[5], espn=m[6].strip(), tipo=m[8].strip(),
                     h=(h[1] or 'completa') if h else '', prov=PROV.get(m[3][:2], '?'))
        out.append(r)
    return out

if __name__ == '__main__':
    pdf, dest = sys.argv[1], sys.argv[2]
    esp = sys.argv[3] if len(sys.argv) > 3 else '128'
    recs = parse(pdf)
    print(len(recs), collections.Counter(r['st'] for r in recs))
    sel = sorted((r for r in recs if r['st'] == 'Adjudicat' and r.get('esp') == esp), key=lambda r: r['pos'])
    with open(dest, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['posicion', 'codigo_plaza', 'provincia', 'localidad', 'codigo_centro', 'centro', 'especialidad', 'tipo', 'horas'])
        for r in sel:
            w.writerow([r['pos'], r['code'], r['prov'], r['loc'], r['cen'], r['nom'], r['esp'], r['tipo'], r['h']])
    print(len(sel), 'filas de la especialidad', esp)
