#!/usr/bin/env python3
"""DocentsCV · lector del PDF «Llocs Ofertats / Puestos Ofertados» (AAMMDD_pue_prov.pdf / AAMMDD_pue_def.pdf).
Devuelve una lista de diccionarios, una por plaza ofertada. Se puede probar suelto:  python3 parse_ofertas.py archivo.pdf"""
import re, sys, subprocess

FAM = {'1': 'maestros', '2': 'secundaria', '3': 'fp_especialistas', '4': 'idiomas', '5': 'catedraticos_musica',
       '6': 'musica', '7': 'artes_plasticas', '8': 'taller_artes'}
CABECERAS = re.compile(r'(Llocs Ofertats|Avgda\.Campanar|ADJUDICACI[ÓO]|P[àa]g \d+ de|LOCALIDAD / LOCALITAT|CUERPO/COS:|ESPECIALIDAD/ESPECIALITAT:|PROVINCIA/PROVINCIA:)')
RE_TIPO = re.compile(r'(VACANTE|SUSTITUCI[ÓO]N\s+(?:IN)?DETERMINADA)')
RE_LING = re.compile(r'^(?:ING|FRA|VAL|CAS|ALE|ITA|CAT)[-A-Z0-9.]*$')

class ErrorPDF(Exception): pass

def texto(ruta):
    p = subprocess.run(['pdftotext', '-layout', ruta, '-'], capture_output=True, text=True)
    if p.returncode: raise ErrorPDF('No se pudo leer el PDF: ' + p.stderr.strip()[:200])
    return p.stdout

def num(s):
    return float(s.replace(',', '.'))

def parsear_registro(lineas, ctx):
    """lineas: líneas del registro (la primera empieza por el número de orden)."""
    t = ' '.join(re.sub(r'\s+', ' ', l.strip()) for l in lineas)
    m = re.match(r'^(\d+) (.*?) - (\d{8}) - (.*)$', t)
    if not m: raise ErrorPDF('No entiendo la fila: ' + t[:120])
    orden, localidad, centro_cod, resto = int(m.group(1)), m.group(2).strip(), m.group(3), m.group(4)
    ml = re.search(r'(?<!\d)(\d{6})(?!\d)', resto)
    if not ml: raise ErrorPDF(f'Fila {orden}: no encuentro el código de plaza (6 cifras).')
    plaza = ml.group(1); antes, despues = resto[:ml.start()].strip(), resto[ml.end():].strip()
    mt = RE_TIPO.search(despues)
    if not mt: raise ErrorPDF(f'Fila {orden}: no encuentro si es vacante o sustitución.')
    tipo_txt = re.sub(r'\s+', ' ', mt.group(1)).replace('Ó', 'O')
    medio, cola = despues[:mt.start()].strip(), despues[mt.end():].strip()
    # ITI/COMP: último NO/SI suelto antes del tipo
    mi = None
    for mm in re.finditer(r'(?<![A-Za-zÀ-ÿ])(NO|SI)(?![A-Za-zÀ-ÿ])', medio): mi = mm
    if not mi: raise ErrorPDF(f'Fila {orden}: no encuentro la columna ITI/COMP (NO/SI).')
    entre, obs = medio[:mi.start()].strip(), (medio[mi.end():].strip() + ' ' + cola).strip()
    horas, ling, nombre_extra = None, '', []
    for tok in entre.split():
        if horas is None and re.fullmatch(r'\d{1,2}(?:,\d{1,3})?', tok): horas = num(tok)
        elif not ling and RE_LING.match(tok): ling = tok
        else: nombre_extra.append(tok)
    centro = (antes + ' ' + ' '.join(nombre_extra)).strip()
    es_vac = tipo_txt.startswith('VACANTE')
    return {'orden': orden, 'cuerpo_txt': ctx['cuerpo_txt'], 'especialidad_codigo': ctx['esp'], 'especialidad': ctx['esp_nom'],
            'provincia': ctx['prov'], 'localidad': localidad, 'centro_codigo': centro_cod, 'centro': centro,
            'codigo_plaza': plaza, 'horas': horas, 'req_ling': ling, 'iti': mi.group(1) == 'SI', 'observ': re.sub(r'\s+', ' ', obs),
            'tipo': 'vacante' if es_vac else 'sustitucion', 'detalle': '' if es_vac else tipo_txt.replace('SUSTITUCION ', 'SUSTITUCIÓN ')}

def leer_ofertas(ruta):
    txt = texto(ruta)
    m = re.search(r'INTERINO D[ÍI]A (\d{2})/(\d{2})/(\d{4})', txt)
    if not m: raise ErrorPDF('No encuentro la fecha de la adjudicación dentro del PDF («… DÍA 27/10/2026»). ¿Es el PDF de plazas ofertadas?')
    fecha = f'{m.group(3)}-{m.group(2)}-{m.group(1)}'
    ctx = {'cuerpo_txt': '', 'esp': '', 'esp_nom': '', 'prov': ''}
    regs, actual, esperado = [], None, 1
    def cerrar():
        nonlocal actual, esperado
        if actual:
            r = parsear_registro(actual, ctx_reg); regs.append(r); actual = None; esperado += 1
    ctx_reg = ctx
    for l in txt.splitlines():
        if not l.strip(): continue
        if CABECERAS.search(l):
            cerrar()
            mc = re.search(r'CUERPO/COS:\s*(.+)$', l)
            if mc: ctx['cuerpo_txt'] = mc.group(1).strip()
            me = re.search(r'ESPECIALITAT:\s*(\S+)\s*-\s*(.+)$', l)
            if me: ctx['esp'], ctx['esp_nom'] = me.group(1), me.group(2).strip()
            mp = re.search(r'PROVINCIA/PROVINCIA:\s*(.+)$', l)
            if mp: ctx['prov'] = mp.group(1).strip()
            continue
        ms = re.match(r'^\s*(\d+)\s{2,}\S', l)
        if ms and int(ms.group(1)) == esperado + (1 if actual else 0):
            cerrar(); actual = [l]; ctx_reg = dict(ctx); continue
        if actual: actual.append(l)
    cerrar()
    if not regs: raise ErrorPDF('No he encontrado ninguna plaza en el PDF.')
    for r in regs:
        r['cuerpo'] = FAM.get(r['especialidad_codigo'][:1], '')
        if not r['cuerpo']: raise ErrorPDF('Especialidad con código desconocido: ' + r['especialidad_codigo'])
    ordenes = [r['orden'] for r in regs]
    if ordenes != list(range(1, len(regs) + 1)): raise ErrorPDF('Faltan filas: la numeración no es continua.')
    return fecha, regs

if __name__ == '__main__':
    f, rs = leer_ofertas(sys.argv[1]); print(f, len(rs), 'plazas')
    for r in rs[:3] + rs[-3:]: print(r)
