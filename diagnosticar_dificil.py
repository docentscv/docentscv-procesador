#!/usr/bin/env python3
"""DIAGNÓSTICO PUNTUAL (temporal): localiza la línea exacta del PDF de 'difícil cobertura' que
FILA_LAX cuenta como fila pero que FILA no consigue capturar, para encontrar el caso no cubierto
por el aparcador de 'nombre de centro que sigue en la línea siguiente'.
Uso: python3 diagnosticar_dificil.py [nombre_original]   (por defecto 261120_pue_prov.pdf)
Se borra en cuanto se arregla el bug real; no forma parte del procesador normal."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import procesar_pdfs as pp
import parse_dificil as pd

nombre = sys.argv[1] if len(sys.argv) > 1 else '261120_pue_prov.pdf'
fila = pp.psql(
    f"select archivo, estado, coalesce(mensaje,'') from importaciones where nombre_original = {pp.lit(nombre)} and tipo = 'dificil' order by id desc limit 1",
    tuples=True,
).strip()
print('IMPORTACIONES ->', repr(fila))
if not fila:
    print('No encuentro ninguna fila en importaciones con ese nombre_original.'); sys.exit(1)
archivo, estado, mensaje = fila.split('\t')
print('archivo:', archivo, '| estado:', estado, '| mensaje:', mensaje)

with tempfile.TemporaryDirectory() as d:
    ruta = os.path.join(d, 'f.pdf')
    pp.descargar(archivo, ruta)
    print('Descargado, tamaño:', os.path.getsize(ruta), 'bytes')
    txt = pd.texto(ruta)
    lines = txt.splitlines()
    print('Total líneas de texto:', len(lines))

    n_lax = sum(1 for l in lines if pd.FILA_LAX.search(l))
    n_fila = sum(1 for l in lines if pd.FILA.match(l))
    print(f'Líneas que cumplen FILA_LAX: {n_lax} | líneas que cumplen FILA (match completo): {n_fila}')

    print('\n--- Líneas que SOLO cumplen FILA_LAX (no FILA) ---')
    for i, l in enumerate(lines):
        if pd.FILA_LAX.search(l) and not pd.FILA.match(l):
            print(f'\n[LINEA {i}] sangria={pd.sangria(l)}')
            print('  ANTES   :', repr(lines[i - 1]) if i > 0 else None)
            print('  LINEA   :', repr(l))
            print('  DESPUES :', repr(lines[i + 1]) if i + 1 < len(lines) else None)

    print('\n--- Intento normal de leer_dificil() para comparar con el recuento real ---')
    try:
        fecha, regs = pd.leer_dificil(ruta)
        print('OK, fecha:', fecha, 'filas:', len(regs))
    except pd.ErrorPDF as e:
        print('ErrorPDF (el mismo que en producción, esperado):', e)
