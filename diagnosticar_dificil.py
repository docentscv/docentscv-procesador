#!/usr/bin/env python3
"""DIAGNÓSTICO PUNTUAL (temporal): inspecciona el esquema ya existente de telegram_* en la base
real (tabla telegram_vinculos, funciones telegram_vincular/telegram_parar/telegram_pendientes_*),
para poder añadir desde la web el botón «Recibir avisos en Telegram» sin adivinar la forma de las
tablas/funciones que ya están en producción."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import procesar_pdfs as pp

def mostrar(titulo, sql):
    print(f'\n=== {titulo} ===')
    print(pp.psql(sql))

mostrar('Tablas telegram_*', "select table_name from information_schema.tables where table_name like 'telegram%' order by 1")
mostrar('Columnas de telegram_vinculos', "select column_name, data_type, is_nullable, column_default from information_schema.columns where table_name = 'telegram_vinculos' order by ordinal_position")
mostrar('Funciones telegram_*', "select routine_name from information_schema.routines where routine_name like 'telegram%' order by 1")
for fn in ('telegram_vincular', 'telegram_parar', 'telegram_pendientes_ofertas', 'telegram_pendientes_delante'):
    try:
        mostrar(f'Definición de {fn}', f"select pg_get_functiondef(oid) from pg_proc where proname = '{fn}'")
    except Exception as e:
        print(fn, 'error:', e)
mostrar('RLS activo en telegram_vinculos', "select relrowsecurity, relforcerowsecurity from pg_class where relname = 'telegram_vinculos'")
mostrar('Políticas RLS de telegram_vinculos', "select policyname, cmd, roles, qual, with_check from pg_policies where tablename = 'telegram_vinculos'")
mostrar('Grants de rol anon/authenticated sobre funciones telegram_*', "select routine_name, grantee, privilege_type from information_schema.routine_privileges where routine_name like 'telegram%' and grantee in ('anon','authenticated') order by 1,2")
mostrar('Unas filas de ejemplo (sin datos sensibles, solo columnas no-nombre)', "select chat_id, activo, creado from telegram_vinculos limit 5")
mostrar('¿Existe ya alguna función para generar código?', "select routine_name from information_schema.routines where routine_name ilike '%codigo%telegram%' or routine_name ilike '%telegram%codigo%'")
