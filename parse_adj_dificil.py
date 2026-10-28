#!/usr/bin/env python3
"""Lector del PDF «Participantes y puesto asignado provisionalmente en el procedimiento de adjudicación de
puestos de difícil cobertura» (AAMMDD_par.pdf). Tiene muchas páginas: por cada plaza se repite la lista de
participantes de la especialidad, y la persona que se lleva la plaza va marcada con «-->».
Solo guardamos a quien obtiene plaza (una fila por plaza adjudicada)."""
import re, subprocess

class ErrorPDF(Exception): pass

FAM = {'1': 'maestros', '2': 'secundaria', '3': 'fp_especialistas', '4': 'idiomas', '5': 'catedraticos_musica',
       '6': 'musica', '7': 'artes_plasticas', '8': 'taller_artes'}
RE_PUESTO = re.compile(r'PUESTO :\s+(\d+)\s+(\d+)\s+(\S.*?)\s*$')
RE_ESP = re.compile(r'^\s{3,10}(\d[0-9A-Z]{2,3})\s+([A-ZÁÉÍÓÚÜÑ][^\d]*?)\s*$')
RE_FECHA = re.compile(r'\d\d/\d\d/\d{4} \d\d:\d\d:\d\d')


def texto(ruta):
    p = subprocess.run(['pdftotext', '-layout', ruta, '-'], capture_output=True, text=True)
    if p.returncode: raise ErrorPDF('No he podido leer el PDF: ' + p.stderr.strip()[:200])
    return p.stdout


def leer_adj_dificil(ruta):
    """Devuelve (fecha 'AAAA-MM-DD', [dict por plaza adjudicada])."""
    ls = texto(ruta).split('\n')
    if 'PARTICIPANTS I LLOC ASSIGNAT' not in '\n'.join(ls[:40]).upper() and 'PUESTO ASIGNADO' not in '\n'.join(ls[:40]).upper():
        raise ErrorPDF('Este PDF no es el de participantes y puesto asignado de difícil cobertura (AAMMDD_par.pdf).')
    fecha = None
    for l in ls[:60]:
        m = re.search(r'^\s{20,}(\d\d)/(\d\d)/(\d{4})\s*$', l)
        if m: fecha = f'{m.group(3)}-{m.group(2)}-{m.group(1)}'; break
    if not fecha: raise ErrorPDF('No encuentro la fecha del PDF.')
    cur = esp = None; puestos = {}; out = []; i = 0
    while i < len(ls):
        l = ls[i]
        m = RE_PUESTO.search(l)
        if m:
            tr = re.split(r'\s{2,}', m.group(3).strip())        # nombre del centro y, si cabe, la localidad; a veces el nombre se parte en dos líneas
            cur = (m.group(1), m.group(2), tr[0], tr[-1] if len(tr) > 1 else ''); puestos.setdefault(m.group(1), cur)
        elif 'PUESTO' not in l and 'Página' not in l:
            m = RE_ESP.match(l)
            if m: esp = (m.group(1), m.group(2).strip())
        if '-->' in l:
            if not cur or not esp: raise ErrorPDF('Fila marcada sin plaza o especialidad (línea %d).' % (i + 1))
            # una persona puede ocupar 2 o 3 líneas cuando el nombre es largo: juntamos hasta ver la fecha y el código final
            tramo = [l.split('-->', 1)[1]]; j = i + 1
            while not (RE_FECHA.search(' '.join(tramo)) and re.search(r'\b\d{6}\s*$', tramo[-1].rstrip())) and j < min(i + 4, len(ls)):
                if ls[j].strip(): tramo.append(ls[j])
                j += 1
            junto = ' '.join(tramo); fm = RE_FECHA.search(junto)
            if not fm: raise ErrorPDF('Fila sin fecha de registro (línea %d).' % (i + 1))
            partes = []
            for t in tramo:
                f2 = RE_FECHA.search(t); parte = t[:f2.start()] if f2 else t
                if f2 is None and re.search(r'\b\d{6}\s*$', t): parte = ''
                partes += [x for x in re.split(r'\s{2,}', parte.strip()) if x]
            resto = junto[fm.end():].split()
            if not resto or not re.fullmatch(r'\d{6}', resto[-1]): raise ErrorPDF('Fila sin puesto adjudicado (línea %d).' % (i + 1))
            if resto[-1] != cur[0]: raise ErrorPDF(f'El puesto adjudicado {resto[-1]} no coincide con el de la cabecera {cur[0]} (línea {i + 1}).')
            if len(partes) == 3: ap1, ap2, nom = partes
            elif len(partes) == 2: ap1, ap2, nom = partes[0], '', partes[1]
            else: raise ErrorPDF('No entiendo el nombre de la persona en la línea %d: %r' % (i + 1, partes))
            grupo = resto[-2]; nums = [x for x in resto[:-2] if x.isdigit() and not x.startswith('00') and not re.fullmatch(r'\d{6,}', x)]
            pet = nums[-1] if nums else ''
            if not (grupo.isdigit() and len(grupo) == 1 and pet.isdigit()): raise ErrorPDF('Columnas inesperadas en la línea %d.' % (i + 1))
            fam = FAM.get(esp[0][:1], '')
            if not fam: raise ErrorPDF('Especialidad con código desconocido: ' + esp[0])
            out.append({'cuerpo': fam, 'especialidad_codigo': esp[0], 'codigo_plaza': cur[0], 'centro_codigo': cur[1], 'centro': cur[2], 'zona': cur[3],
                        'nombre': (ap1 + (' ' + ap2 if ap2 else '') + ', ' + nom).upper(), 'peticion': int(pet), 'grupo': int(grupo)})
            i = j - 1 if j > i + 1 else i
        i += 1
    cods = [r['codigo_plaza'] for r in out]
    if len(set(cods)) != len(cods): raise ErrorPDF('Hay plazas adjudicadas repetidas en el PDF.')
    if not out: raise ErrorPDF('No he encontrado ninguna plaza adjudicada en el PDF.')
    vacias = [p for p in puestos if p not in set(cods)]
    return fecha, out, vacias


if __name__ == '__main__':
    import sys, collections
    f, rs, vac = leer_adj_dificil(sys.argv[1]); print(f, len(rs), 'adjudicadas;', len(vac), 'plazas sin adjudicar')
    print(collections.Counter(r['grupo'] for r in rs), collections.Counter(r['cuerpo'] for r in rs))
    for r in rs[:5] + rs[-3:]: print(r)
