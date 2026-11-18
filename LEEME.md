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

## Acreditación de idiomas (inglés)

Cada lunes (y también a mano, pestaña Actions → «Acreditación de idiomas (inglés)» → Run workflow)
`cargar_idiomas.py` recorre en la web de la Conselleria los «Listados definitivos» de Acreditación en
lenguas extranjeras de las 3 direcciones territoriales (València, Castellón, Alicante) y todos los
cursos. Descarga cada PDF nuevo, saca de la tabla «Anexo I» quién tiene el inglés acreditado (B2/C1/C2)
y lo guarda en `acreditaciones_idioma`, para que la web le muestre esa observación a cada docente. Solo
apunta la acreditación a personas que ya existan en alguna lista de bolsa (no crea personas nuevas).

Cada PDF ya tratado se apunta en `fuentes_idioma` (por su URL) y no se vuelve a bajar. Si un nombre sale
dos veces en el mismo PDF con niveles distintos (homónimo) o parece tener dos apellidos pegados sin
espacio (fallo de extracción del propio PDF), no se adivina: se deja anotado en `idiomas_revisar` para
repasarlo a mano.

Algunos PDF (sobre todo los más recientes) no tienen la tabla como texto real, sino dibujada como líneas;
en ese caso se lee por OCR (Tesseract, en español) y se carga igual, sin paso manual. El nombre leído por
OCR se usa tal cual (si se equivoca en una letra, esa persona simplemente no coincide con nadie y no se
carga: no se carga mal). El nivel (B2/C1/C2) solo se acepta si se lee exacto o con una corrección evidente
de una sola letra; si queda ambiguo, también va a `idiomas_revisar` en vez de adivinarlo.

Para probar con un PDF suelto sin tocar la web ni la base de datos:
`python3 cargar_idiomas.py --local archivo.pdf --provincia València --fecha 2026-09-30`
