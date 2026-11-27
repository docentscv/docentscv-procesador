#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import procesar_pdfs as pp

def mostrar(titulo, sql):
    print(f'\n=== {titulo} ===')
    print(pp.psql(sql))

for fn in ('telegram_mi_estado', 'telegram_desvincular'):
    mostrar(f'Definición de {fn}', f"select pg_get_functiondef(oid) from pg_proc where proname = '{fn}'")
mostrar('Constraints de telegram_vinculos', "select conname, pg_get_constraintdef(oid) from pg_constraint where conrelid = 'telegram_vinculos'::regclass")
mostrar('Grants de anon/authenticated sobre TODAS las funciones telegram_*', "select routine_name, grantee, privilege_type from information_schema.routine_privileges where routine_name like 'telegram%' order by 1,2")
mostrar('Políticas RLS en otras tablas relevantes (perfiles, domicilios)', "select tablename, policyname, cmd, roles from pg_policies where tablename in ('perfiles','domicilios') order by 1,2")
