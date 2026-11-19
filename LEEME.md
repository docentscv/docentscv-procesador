# Procesador automático de PDF · DocentsCV

Cada 15 minutos (en la práctica, GitHub lo retrasa y suele ser más bien cada hora — ver «Vigilancia» abajo)
`descargar_conselleria.py` recorre las páginas de la Conselleria y descarga los PDF nuevos de plazas
ofertadas, adjudicación continua y difícil cobertura; `procesar_pdfs.py` lee los que queden `pendiente` en
la tabla `importaciones` (también los que subas tú a mano desde Administración), actualiza la base de
datos y los marca como `ok` o `error` (con el motivo en español), y avisa (email, push, Telegram/Instagram
vía el robot de `docentscv-robot-social`). Si no hay ninguno pendiente, termina en segundos.

Las listas de inicio de curso se siguen cargando a mano (una vez al año).

Secretos necesarios en GitHub (Settings → Secrets and variables → Actions):
- DATABASE_URL: cadena de conexión «Session pooler» de Supabase (con tu contraseña de base de datos)
- SUPABASE_URL: https://gwoamxgvnhietzhixjlf.supabase.co
- SUPABASE_SERVICE_KEY: clave secreta (sb_secret_...) de Supabase. NUNCA en la web ni en el código.

## Vigilancia (ser los primeros en publicarlo)

GitHub no garantiza la hora de sus ejecuciones programadas: el cron de cada 15 minutos en la práctica
va saltando cada 55-80 minutos (es una limitación conocida de GitHub Actions, no un fallo nuestro). Para
los momentos en que de verdad importa ser rápidos, hay un segundo workflow, **«Vigilancia»**
(`vigilancia.yml`), que en cuanto se lanza comprueba la web de la Conselleria sin parar (cada minuto) hasta
que sale lo que toca ese día, lo carga y avisa al momento — sin esperar a que acabe de mirar lo demás.

Qué vigila cada día (`TIPOS_POR_DIA` en `descargar_conselleria.py`):
- Lunes y miércoles: salen las plazas de continua (`ofertas`)
- Martes: adjudicación continua del día anterior (`continua`; espera a que salgan maestros Y secundaria/otros)
- Jueves: adjudicación continua del miércoles (`continua`) y salen las plazas de difícil cobertura (`dificil`)
- Viernes: adjudicación de difícil cobertura (`dificil_adj`)

Si a las 14:00 todavía falta algo, lo deja (GitHub no permite que un job dure más de 6 horas) y avisa por
push a los administradores para revisarlo a mano o relanzar la Vigilancia desde Actions.

**Hace falta que algo la lance a las 8:00 en punto** (el cron de GitHub no es fiable para esto, como se
explica arriba). La forma más sencilla es un servicio externo gratuito como [cron-job.org](https://www.cron-job.org/)
que llame una vez al día, de lunes a viernes a las 8:00, a:
```
POST https://api.github.com/repos/docentscv/docentscv-procesador/actions/workflows/vigilancia.yml/dispatches
Authorization: Bearer <tu token de GitHub, con permiso Actions: Read and write solo en este repositorio>
Accept: application/vnd.github+json
Content-Type: application/json

{"ref":"main"}
```
También se puede lanzar a mano cualquier día desde la pestaña Actions → «Vigilancia» → Run workflow.

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

Los listados más antiguos (se ha visto al menos hasta 2019) usan además otra maqueta de tabla: una
columna por idioma (Ang/Fra/Ale/Ita) en vez de una fila por idioma con la palabra "Anglès" al lado. El
robot también reconoce este formato (busca la columna "Ang" por su cabecera). En escaneados muy
antiguos y de poca calidad, el OCR a veces confunde la "B" con una "e" o un "6", lo que puede dejar el
nivel igual de parecido a B2 que a C2 (un empate real): esas filas también van a `idiomas_revisar`, nunca
se adivinan. Cuantos más años antiguos y escaneados se procesen, más es esperable encontrar en esa tabla
de revisión manual — no es un fallo del robot, es la calidad del documento de origen.

Para probar con un PDF suelto sin tocar la web ni la base de datos:
`python3 cargar_idiomas.py --local archivo.pdf --provincia València --fecha 2026-09-30`

### Revisar de golpe todos los PDF históricos

Pestaña Actions → «Informe de idiomas (revisión completa de todos los PDF)» → Run workflow. Vuelve a
descargar y analizar TODOS los PDF de las 3 direcciones territoriales y todos los cursos (también los
que ya estaban cargados antes), sin tocar la base de datos pase lo que pase: es solo para comprobar,
con la versión actual del robot, cuáles se leen bien y cuáles no. Al acabar deja descargable (en la
propia página del workflow, abajo del todo, en «Artifacts») un `informe_idiomas.csv` con una fila por
PDF: provincia, curso, archivo, estado (`ok` / `error`), cuántos nombres se cargaron, cuántos
homónimos/sospechosos/ambiguos se excluyeron, y si dio error, el motivo exacto. Puede tardar bastante
(recorre todo el histórico), así que no pasa nada si tarda una hora o más.
