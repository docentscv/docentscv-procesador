#!/usr/bin/env python3
"""DIAGNÓSTICO PUNTUAL (temporal): reintenta una fila de 'importaciones' que quedó en estado
'error' (la vuelve a poner 'pendiente', borrando el mensaje de error) y la procesa ya mismo con
el procesador normal. Pensado para el PDF de difícil cobertura que falló por el bug ya corregido
de parse_dificil.py (261120_pue_prov.pdf, «Faltan filas: leídas 115 de 116»).
Uso: python3 diagnosticar_dificil.py [nombre_original] [tipo]"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import procesar_pdfs as pp

nombre = sys.argv[1] if len(sys.argv) > 1 else '261120_pue_prov.pdf'
tipo = sys.argv[2] if len(sys.argv) > 2 else 'dificil'

antes = pp.psql(
    f"select id, estado, coalesce(mensaje,'') from importaciones where nombre_original = {pp.lit(nombre)} and tipo = {pp.lit(tipo)} order by id desc limit 1",
    tuples=True,
).strip()
print('ANTES ->', repr(antes))
if not antes:
    print('No encuentro ninguna fila en importaciones con ese nombre_original/tipo.'); sys.exit(1)
iid = antes.split('\t')[0]

pp.psql(f"update importaciones set estado = 'pendiente', mensaje = null where id = {int(iid)}")
print(f'Fila id={iid} reseteada a pendiente. Lanzando el procesador normal...')

pp.main()

despues = pp.psql(
    f"select id, estado, coalesce(mensaje,'') from importaciones where id = {int(iid)}",
    tuples=True,
).strip()
print('DESPUES ->', repr(despues))

chk = pp.psql("select fecha, count(*) from plazas_dificil group by fecha order by fecha desc limit 5", tuples=True)
print('--- plazas_dificil, últimas fechas ---')
print(chk)
