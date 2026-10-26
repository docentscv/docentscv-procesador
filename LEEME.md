# Procesador automático de PDF · DocentsCV

Cada 15 minutos GitHub ejecuta `procesar_pdfs.py`: busca en la tabla `importaciones` los PDF en estado `pendiente`
(los que subes desde el panel de Administración), los lee, actualiza la base de datos y los marca como `ok` o `error`
(con el motivo en español). Si no hay ninguno pendiente, termina en segundos.

Solo procesa adjudicaciones continuas (nombre `AAMMDD_lis_mae.pdf` / `AAMMDD_lis_sec.pdf`, como las publica la Conselleria).
Las listas de inicio de curso se siguen cargando a mano (una vez al año).

Secretos necesarios en GitHub (Settings → Secrets and variables → Actions):
- DATABASE_URL: cadena de conexión «Session pooler» de Supabase (con tu contraseña de base de datos)
- SUPABASE_URL: https://gwoamxgvnhietzhixjlf.supabase.co
- SUPABASE_SERVICE_KEY: clave secreta (sb_secret_...) de Supabase. NUNCA en la web ni en el código.
