#!/usr/bin/env python3
"""DocentsCV · lector del PDF «Llocs de difícil cobertura convocats en el procediment» (p. ej. 261023_pue_def.pdf).
Devuelve (fecha, lista de plazas). Prueba suelta:  python3 parse_dificil.py archivo.pdf"""
import re, sys, subprocess, unicodedata

FAM = {'1': 'maestros', '2': 'secundaria', '3': 'fp_especialistas', '4': 'idiomas', '5': 'catedraticos_musica',
       '6': 'musica', '7': 'artes_plasticas', '8': 'taller_artes'}
PROV = {'ALICANTE': 'Alicante', 'ALACANT': 'Alicante', 'CASTELLON': 'Castelló', 'CASTELLÓ': 'Castelló', 'CASTELLÓN': 'Castelló',
        'VALENCIA': 'València', 'VALÈNCIA': 'València'}
FILA = re.compile(r'^(.+?) - (\d{8}) - (.+?)\s{2,}(\d{6})\s+(\d{1,2}(?:,\d{1,3})?)\s+(NO|SI)\s+(Vacante|Sust\. Det\.|Sust\. Ind\.)(.*)$')
FILA_LAX = re.compile(r'\d{6}\s+\d{1,2}(?:,\d{1,3})?\s+(?:NO|SI)\s+(?:Vacante|Sust)')
RE_LING = re.compile(r'^(?:ING|FRA|VAL|CAS|ALE|ITA|CAT)[-A-Z0-9.]*$')
CAB = re.compile(r'(Avgda\.Campanar|LLOCS DE DIF|PUESTOS DE DIF|^\s*PROCEDIMIENTO|LOCALITAT/LOCALIDAD|P[àa]g \d+ of \d+|CUERPO/COS:|ESPECIALIDAD/ESPECIALITAT:|PROV[ÍI]NCIA/PROVINCIA:)')

class ErrorPDF(Exception): pass

def texto(ruta):
    p = subprocess.run(['pdftotext', '-layout', ruta, '-'], capture_output=True, text=True)
    if p.returncode: raise ErrorPDF('No se pudo leer el PDF: ' + p.stderr.strip()[:200])
    return unicodedata.normalize('NFC', p.stdout)

def sangria(l): return len(l) - len(l.lstrip())

def leer_dificil(ruta):
    txt = texto(ruta)
    if not re.search(r'DIF[IÍ]CIL COBERTURA', txt, re.I):
        raise ErrorPDF('Este PDF no parece el de plazas de difícil cobertura (no aparece «DIFÍCIL COBERTURA» en el título).')
    m = re.search(r'PROCEDIMIENTO\s+(\d{2})/(\d{2})/(\d{4})', txt)
    if not m: raise ErrorPDF('No encuentro la fecha del PDF junto al título.')
    fecha = f'{m.group(3)}-{m.group(2)}-{m.group(1)}'
    ctx = {'esp': '', 'esp_nom': '', 'prov': ''}
    regs, pend_obs, ultima, huecos = [], [], None, 0
    for l in txt.splitlines():
        if not l.strip(): continue
        if CAB.search(l):
            me = re.search(r'ESPECIALITAT:\s*(\S+)\s+(.+)$', l)
            if me: ctx['esp'], ctx['esp_nom'] = me.group(1).strip(), me.group(2).strip()
            mp = re.search(r'PROV[ÍI]NCIA/PROVINCIA:\s*(.+)$', l)
            if mp: ctx['prov'] = PROV.get(mp.group(1).strip().upper(), mp.group(1).strip())
            ultima = None if (me or mp or 'CUERPO' in l) else ultima
            continue
        mf = FILA.match(l)
        if mf:
            if not ctx['esp'] or not ctx['prov']: raise ErrorPDF('Fila sin especialidad o provincia: ' + l.strip()[:100])
            loc, cen_cod, cen, plaza, horas, iti, tipo, resto = mf.groups()
            cols = [c.strip() for c in re.split(r'\s{2,}', resto.strip()) if c.strip()]
            ling = ''
            if cols and RE_LING.match(cols[0]): ling = cols.pop(0)
            obs = ' '.join(pend_obs + cols); pend_obs = []
            r = {'especialidad_codigo': ctx['esp'], 'especialidad': ctx['esp_nom'], 'provincia': ctx['prov'], 'localidad': loc.strip(),
                 'centro_codigo': cen_cod, 'centro': cen.strip(), 'codigo_plaza': plaza, 'horas': float(horas.replace(',', '.')),
                 'iti': iti == 'SI', 'req_ling': ling, 'observ': re.sub(r'\s+', ' ', obs).strip(),
                 'tipo': 'vacante' if tipo == 'Vacante' else 'sustitucion',
                 'detalle': '' if tipo == 'Vacante' else ('determinada' if 'Det' in tipo else 'indeterminada'), '_col': mf.start(4)}
            regs.append(r); ultima = r; continue
        s = sangria(l)
        if ultima is not None and s < ultima['_col'] - 5:      # sigue el nombre del centro en la línea siguiente
            ultima['centro'] = (ultima['centro'] + ' ' + l.strip()).strip(); continue
        if s >= 60:                                            # observación centrada verticalmente: pertenece a la fila siguiente
            pend_obs.append(l.strip()); continue
        huecos += 1
    if pend_obs: raise ErrorPDF('Queda una observación sin fila: ' + ' '.join(pend_obs)[:100])
    esperadas = sum(1 for l in txt.splitlines() if FILA_LAX.search(l))
    if not regs: raise ErrorPDF('No he encontrado ninguna plaza en el PDF.')
    if len(regs) != esperadas: raise ErrorPDF(f'Faltan filas: leídas {len(regs)} de {esperadas} que tiene el PDF.')
    for r in regs:
        r.pop('_col'); r['cuerpo'] = FAM.get(r['especialidad_codigo'][:1], '')
        if not r['cuerpo']: raise ErrorPDF('Especialidad con código desconocido: ' + r['especialidad_codigo'])
    if len({(r['especialidad_codigo'], r['codigo_plaza']) for r in regs}) != len(regs): raise ErrorPDF('Hay plazas repetidas en el PDF.')
    return fecha, regs

if __name__ == '__main__':
    f, rs = leer_dificil(sys.argv[1]); print(f, len(rs), 'plazas')
    import collections; print(collections.Counter(r['cuerpo'] for r in rs), collections.Counter(r['tipo'] for r in rs))
    for r in rs: 
        if r['observ'] or r['req_ling'] or len(r['centro']) > 38: print(r['codigo_plaza'], '|', r['centro'], '|', r['req_ling'], '|', r['observ'])
