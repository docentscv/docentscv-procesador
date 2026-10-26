#!/usr/bin/env python3
"""Extrae las adjudicaciones de inicio de curso de 'Altres Cossos / Otros Cuerpos' (Secundaria y resto).
En estos listados cada especialidad (bolsa) tiene su propia numeración. No guarda nombres de personas."""
import re, subprocess
PROV = {'03': 'Alicante', '12': 'Castelló', '46': 'València'}
SPEC = re.compile(r'^([0-9A-Z]{3})\s+(\S.*)$')
START = re.compile(r'^[ \t]*(\d+)(?:/(\d+))?[ \t]{2,}(?=[A-ZÀ-ÚÑ])', re.M)
REC = re.compile(r'(\d{6})\s+(.+?)\((\d{8})\)(.+?)\n\s*([0-9A-Z]{3}) / (.+?)\n(.*?)(VACANT|SUBSTITUCI[ÓO][^\n]*?)\s{2,}', re.S)
STATUS = re.compile(r'(Desactivat|No adjudicat|Adjudicat|No ha participat|Ha participat)\s*$', re.M)

def nombre_de(texto):
    l = texto.split('\n', 1)[0]
    return re.sub(r'\s+', ' ', re.split(r'Petici[óo]n:', l)[0]).strip()

def parse(pdf):
    txt = subprocess.run(['pdftotext', '-layout', pdf, '-'], capture_output=True, text=True).stdout
    out, cuerpo = [], None
    for page in txt.split('\f'):
        lines = page.split('\n')
        first = next((i for i, l in enumerate(lines) if START.match(l)), None)
        if first is None: continue
        hdr = [l.strip() for l in lines[:first] if l.strip()][4:]
        idx = next((i for i, l in enumerate(hdr) if SPEC.match(l)), None)
        if idx is None: continue
        if idx > 0: cuerpo = hdr[idx - 1]
        bolsa, bolsa_n = SPEC.match(hdr[idx]).groups()
        body = '\n'.join(lines[first:])
        parts = START.split(body)           # [pre, pos, sub, resto, pos, sub, resto...]
        for i in range(1, len(parts), 3):
            pos, sub, rest = int(parts[i]), parts[i + 1], parts[i + 2]
            s = STATUS.findall(rest)
            r = {'nombre': nombre_de(rest), 'cuerpo': cuerpo, 'bolsa': bolsa, 'bolsa_n': bolsa_n.strip(), 'pos': pos, 'sub': int(sub) if sub else None, 'st': s[-1] if s else None}
            m = REC.search(rest)
            if m:
                h = re.search(r'(\d+(?:,\d+)?) horas|Jornada completa', rest)
                r.update(code=m[1], loc=m[2].strip(), cen=m[3], nom=m[4].strip(), esp=m[5], espn=m[6].strip(),
                         tipo=m[8].strip(), h=(h[1].replace(',', '.') if h[1] else '') if h else '', prov=PROV.get(m[3][:2], '?'))
            out.append(r)
    return out
