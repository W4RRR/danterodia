# Auditoría de osint-suite-tools (Dante's Gates Minimal Version)

- **Proyecto original**: <https://github.com/Quantika14/osint-suite-tools> — Quantika14 / Jorge Coronado (@JorgeWebsec).
- **Copia auditada**: `osint-suite-tools-master.zip` (ficheros fechados el 28/04/2023), commit `baseline` de este repositorio.
- **Fecha de la auditoría**: 20/11/2026.
- **Método**:
  1. Lectura completa de los 268 ficheros (código, bots, datasets y README).
  2. Análisis estático, sin ejecutar el código original.
  3. Comprobación en vivo de cada fuente externa con un `GET` anónimo y genérico (sin datos personales) el 20/11/2026.
  4. Consulta en PyPI de la versión y la fecha de cada dependencia.

> **Sobre el estado «funciona hoy»**: no he ejecutado los scripts originales. Necesitan MongoDB y `pdfgrep`, hacen peticiones reales y varios intentan iniciar sesión en servicios de terceros. El estado de cada uno sale de cruzar tres cosas: el código, la respuesta real de cada endpoint y las APIs actuales de las librerías. Donde no he podido confirmarlo lo indico con **(no verificado)**.

Leyenda de estado: ✅ funciona · ⚠️ funciona a medias o con condiciones · ❌ roto · ☠️ no debería existir (riesgo legal o ético).

---

## 1. Resumen ejecutivo

| Aspecto | Diagnóstico |
|---|---|
| Funcionalidad real | De las ~25 capacidades que anuncia el README, **solo 4 o 5 funcionan hoy**: frecuencia INE (local), lista local de políticos, búsqueda en Wikipedia (parcial), comprobación de perfiles por nick (con muchos falsos positivos) y extracción de artículos con newspaper. `BuscadorNoticiasFalsas.py` y la opción de lista de `BuscadorEmails.py` se caen al arrancar. El resto falla porque la fuente desapareció, el scraping está bloqueado o hay errores en el código. |
| Fuentes clave | **LibreBORME API v1 ha desaparecido** (HTTP 410; la v2 de librebor.me es de pago y exige autenticación). **El scraping de Google, Dogpile, ABC y Páginas Amarillas está bloqueado.** **Twitter vía twint está muerto.** **emailrep.io ya no admite peticiones sin clave.** |
| Python | Hay código **Python 2** (bots BOE, TESTRA, insecam, PDF crawler, `modules/bing.py`) que no compila en Python 3. Los `__pycache__` son de CPython 3.6 (fin de soporte en 2021). |
| Arquitectura | Scripts interactivos con `input()`, estado global en módulos y salida con `print`. MongoDB es obligatorio (se conecta al importar) y `pdfgrep` se invoca por shell. No hay ningún test. |
| Seguridad | **Inyección de comandos** (`os.system` con datos de usuario o de Internet), **TLS desactivado** en 4 bots, 31 peticiones **sin timeout**, credenciales en el código (Mongo `123456@@`, hash de control de Tor) y regex de Mongo construida con la entrada del usuario. |
| Legal y ético | Hay funciones que **no son OSINT**: intentos de inicio de sesión con contraseña `123456` en Facebook, Netflix y WordPress para enumerar cuentas; un **generador de PHP para suplantar remitentes de email**; un scraper de **cámaras IP expuestas** (insecam); un scraper de **edictos de multas de la DGT** con DNI, nombre y matrícula. Además se extraen **categorías especiales de datos** (religión, ideología) sin justificación. |
| Licencia | Las cabeceras dicen BSD-2-Clause y el `LICENSE` es GPL-3.0. Hay código de terceros incluido (Search-Engines-Scraper, html-reports, TorScrapper, pdfcrawler, TrabajoFinGrado) **sin sus licencias**. |
| Promesas sin código | El README anuncia la **«API REST de Dante's Gates»**, **Páginas Amarillas**, **Pastebin**, **tweets por teléfono**, **localización** y **operador**, pero **ninguna de ellas existe en el código**. |

---

## 2. Inventario

### 2.1 Puntos de entrada (raíz)

| Script | Qué hace (según el código) | Depende de | Estado hoy |
|---|---|---|---|
| `BuscadorPersonas.py` | Menú interactivo. **Opción 1** (nombre, 2 apellidos y DNI): frecuencia INE, búsqueda en Wikipedia y en la ficha de Wikipedia (nacimiento, muerte, partido, ocupación, **religión, hijos, horóscopo**), indultos en Mongo, LibreBORME, YouTube, hemeroteca de ABC, posts de Facebook, lista local de políticos investigados, Google (nombre y DNI), Dogpile, descarga de PDFs vía Google e informe HTML. **Opción 2**: lee `targets.txt` y solo consulta Wikipedia; el resto está comentado. | wikipedia, wikipedia-api, requests, bs4, wget, tldextract, newspaper3k, facebook-scraper, pymongo, html-reports, `search_engines` | ⚠️ Solo funcionan INE, la lista local de políticos y, en parte, Wikipedia. LibreBORME ❌ (410), YouTube ❌ (selectores de 2019 y `TypeError` al concatenar un `Tag`), ABC ❌ (404), Facebook ❌, Google ❌ (redirige a la página de consentimiento o al captcha `/sorry`), Dogpile ❌ (202 vacío por un reto JS), indultos ⚠️ (solo si antes se llenó Mongo con un bot que también está roto). Si MongoDB no está levantado, cada inserción espera unos 30 s y lanza una excepción. |
| `BuscadorNick.py` | Posts de Facebook con el nick, prueba de `nick@<11 proveedores>` con `validate_email`, comprobación en 94 URLs de perfiles y Google con agrupación de resultados. Genera `report.html`. | requests, bs4, validate_email, html-reports, `search_engines`, tldextract, newspaper3k, pymongo | ⚠️ El chequeo de perfiles «funciona», pero sin timeout, en secuencia y con muchos sitios muertos (Google+, Fotolog, CashMe, Tracky, Stream.me, Blip.fm, Soup.io, Twicsy…). Dos entradas `Pastebin` se pisan en el dict y la URL de Strava no incluye el nick. `validate_email` se llama con argumentos de **otra versión** de la librería, así que falla siempre con «No verification possible». Google ❌. |
| `BuscadorEmails.py` | «EO-Ripper»: mismo alias en unos 300 dominios, emailrep.io, Twitter, **login en Facebook, WordPress y Netflix con contraseña `123456`**, scraping de HIBP y **generador de `evilmail.php` para spoofing**. | mechanize, requests, bs4, validate_email | ❌ / ☠️ `C.colores.Alert`, `C.count_leaked` y `C.dir_listEmails` **no existen** en `config.py`, así que la opción 1 se cae y emailrep termina siempre en el `except`, que muestra un anuncio de la app de pago. emailrep exige clave. La página `/account/` de HIBP ya no devuelve resultados en HTML. Twitter/X necesita JavaScript. Las pruebas de login son **intentos de acceso no autorizado** (ver §6). |
| `BuscadorTelefonos.py` | **Login en Facebook con el teléfono y `123456`**, 4 dorks de Google (locatefamily, Facebook, LinkedIn, Instagram) con `+34` fijo, Bing y Dogpile. | mechanize, requests, bs4, `search_engines` | ❌ / ☠️ Google y Dogpile bloqueados. Bing devuelve HTML (200) pero los selectores son frágiles **(no verificado)**. El README promete operador, localización y tweets: **no existen**. |
| `BuscadorNoticiasFalsas.py` | Pide una URL, saca título y `og:description`, extrae emails, DNI e IBAN, busca el título en Google y DDG, descarga cada resultado, compara con `difflib` contra el **HTML completo**, hace dorks `site:` en newtral, maldita y rtve, y busca en Twitter con twint. | requests, bs4, `search_engines`, tldextract, twint | ❌ **Se cae nada más empezar**: llama a `parser.parser_EN_DATE(url)`, que no existe en `modules/parsers.py`, fuera de cualquier `try` (`AttributeError`). Aunque se arreglara eso: DDG funcionaría, Google ❌ y twint ❌. `generateLOG` escribe en `report/`, que no existe (`FileNotFoundError` tragado). `search_DDG_DORKS` usa la variable `target`, que no está definida (`NameError` tragado). La condición `status_code >= 200 or status_code < 300` **siempre es verdadera**. Comparar el HTML con scripts y menús da un ratio sin significado. |

### 2.2 `modules/`

| Módulo | Función | Estado / problemas |
|---|---|---|
| `config.py` | Colores ANSI, listas globales de resultados, rutas, listas negras, palabras clave de fact-checking y banner animado (con `sleep`). | Estado global mutable compartido entre módulos. Faltan atributos que otros módulos usan. El banner tarda ~4 s en cada arranque. |
| `data.py` | Variables globales para el informe (INE, Wikipedia, empresas). | Estado global. `WIki_horoscopo` (errata) en `parsers.py` impide que el horóscopo llegue al informe. |
| `er.py` | Quita acentos y etiquetas HTML con una regex. | ✅ Simple pero incompleto (`unicodedata` lo hace mejor). La regex no quita `<script>`/`<style>`. |
| `parsers.py` | Regex de email, teléfono, IBAN y DNI; palabras clave; ficha de Wikipedia → Mongo. | La regex de DNI `^…$` sin `MULTILINE` **nunca encuentra nada** dentro de un texto. La de IBAN da muchos falsos positivos. El email solo se guarda si mide menos de 20 caracteres. Guarda **religión** y otros datos de categoría especial. El cálculo del horóscopo falla con `IndexError` si no encuentra el mes. |
| `findData.py` | `pdfgrep` por shell sobre `data/`; lista de políticos. | ☠️ **Inyección de comandos**: `os.system("pdfgrep '" + empresa + "' …")`, donde `empresa` viene de **LibreBORME (Internet)** o del usuario (las llamadas están comentadas, pero el código sigue ahí). Solo funciona en Linux. |
| `mongo.py` | Inserciones y búsquedas en MongoDB `Dante`. | ❌ `Collection.insert()` **se eliminó en PyMongo 4** (`AttributeError`). `companies_insertMongoDB` usa `company`, que no existe. `find_companies` usa `rep`, que no existe. El `$regex` usa la entrada del usuario sin escapar (regex inyectada o ReDoS). Comprueba duplicados en la colección `DG` pero inserta en otra. |
| `newspaper.py` | Descarga y parsea un artículo (newspaper3k) y lo guarda en la Mongo `Test`. | ⚠️ newspaper3k está abandonado (2018). Usa una base distinta (`Test`) de la del resto (`Dante`). Su nombre choca con el paquete `newspaper`. |
| `ine_nombreApellidos.py` | Frecuencia de nombre y apellidos en los CSV del INE. | ✅ Funciona. Errores menores: guarda un `set` (`{row[1]}`), el segundo apellido muestra `row[2]` pero guarda `row[1]`, lee 2 MB de CSV en cada llamada y no normaliza acentos. |
| `spainpress.py` | Hemeroteca de ABC. | ❌ La URL devuelve 404. |
| `facebook.py` | `facebook_scraper.get_posts(nombre)`. | ❌ La librería está abandonada (2022) y Facebook exige login y cookies. Además le pasa un nombre de persona como si fuera una página. |
| `twitter.py` | twint. | ❌ twint está muerto (última versión de 2020, repositorio archivado) desde los cambios de X en 2023. |
| `intelx_.py` + `key.py` | IntelX: pastes, leaks y darknet. | ⚠️ Está desactivado (comentado). `intelxapi` no está en PyPI. La clave va en un `.py` dentro del repositorio. Descarga el primer leak a `file.txt` en el directorio actual. |
| `graph.py` | Grafo con networkx y matplotlib → PNG. | No se usa (comentado). Grafo estrella sin pesos ni tipos. |
| `report.py` | Informe HTML con `html_reports`. | ⚠️ El Markdown se pasa a HTML **sin escapar**: el contenido scrapeado acaba en el informe (XSS almacenado). Usa una imagen `images/…png` que no existe. |
| `html_reports/` | Copia del paquete `html-reports` de PyPI. | Duplicado: el código importa el paquete de PyPI, no esta copia. Bug: `self.script` en lugar de `scripts`. Plantilla Jinja2 sin `autoescape`. |
| `control.py` | Detecta el SO y «cuenta ficheros» con `os.system("ls -A \| wc -l")`. | ❌ `os.system` devuelve el código de salida, no la cuenta. `"win" in "darwin"` es verdadero, así que macOS se detecta como Windows. |
| `dic.py` | Lista de unos 300 dominios de correo y palabras clave. | Lista antigua (geocities, lycos, altavista…). |
| `bing.py` | Scraper de Bing. | ❌ **Python 2** (`print "…"`, `urllib2`): `SyntaxError`. No se usa. |
| `scammerInfo.py` | Busca en scammer.info. | ❌ **Lanza una petición al importarse** (`search_scammerInfo("658")` a nivel de módulo). No se usa. |
| `wordcloud.py` | Ejemplo copiado de la librería wordcloud («alice.txt»). | ❌ No funciona (faltan los ficheros) y **tapa al paquete `wordcloud`**. |

### 2.3 `search_engines/`

Es una copia de **Search-Engines-Scraper v0.5** (Tasos M. Adamopoulos, licencia MIT; **el fichero de licencia no se incluye**). Scrapea HTML de 10 buscadores con requests y BeautifulSoup.

| Motor | Estado el 20/11/2026 |
|---|---|
| Google | ❌ 302 a consentimiento o al captcha `/sorry`. |
| Bing | ⚠️ 200 con HTML, pero los selectores son de 2020 **(no verificado)**. |
| Yahoo / AOL / Ask | ⚠️ Sin verificar; históricamente se rompen a menudo. |
| DuckDuckGo (`/html/`) | ⚠️ Funciona (200) pero limita el ritmo enseguida. Mejor usar la librería `ddgs`. |
| Startpage | ⚠️ Sin verificar; usa formularios con tokens. |
| Dogpile | ❌ 202 vacío (reto JavaScript). |
| Mojeek | ✅ 200 con resultados en HTML simple. |
| Torch (.onion) | ❌ **Dirección onion v2**: Tor dejó de admitirlas en octubre de 2021. |

Otros detalles: el User-Agent por defecto apunta al repositorio de Quantika14; `libs/windows_cmd_encoding.py` solo sirve para la consola antigua de Windows; tiene timeout de 3 s pero no reintentos.

### 2.4 `bots/`

| Bot | Qué hace | Estado |
|---|---|---|
| `BOE/BOE-indultos.py` | Lee `boes.txt` (10 582 IDs) y descarga el XML de cada BOE a Mongo `DG_BOE`. | ⚠️ El endpoint `diario_boe/xml.php` sigue vivo (200). Falla con PyMongo 4 (`insert`). Comprueba duplicados en `DG` e inserta en `DG_BOE`. **Se ejecuta al importarse.** Sin timeout ni pausa entre peticiones. |
| `BOE/botE_BOE_A.py`, `botE_BOE_B.py` | Fuerza bruta de IDs `BOE-A/B-<año>-<n>` hasta 320 000 o 400 000 por año. Descarga cada PDF y lo convierte a texto. | ❌ **Python 2**. TLS desactivado. `qpdf` por `os.popen`. Hash MD5. Millones de peticiones contra el BOE: abuso del servicio cuando ya existe la API de datos abiertos. |
| `BOE/botE_BOE_A (copia).py` | Borrador a medias del anterior. | ❌ Duplicado roto: concatena `str + int` y `main()` no hace nada. |
| `BOE/civio/*.csv` (20 ficheros) | Dataset del **Indultómetro de Civio**: `id, pardon_date, minister, crime, gender`, 1996-2018. | ✅ He comprobado que los 20 CSV son **particiones sin solapamiento** (10 582 IDs únicos = líneas de `boes.txt`), así que pueden unirse en uno solo. No incluye el nombre del indultado; ese dato está en el BOE. |
| `LinkedIn/scrape_linkedin.py` | Dork `site:linkedin.com inurl:/in/ <empresa>` en Google para sacar nombres de empleados. README de un repositorio de «pentest_scripts». | ❌ / ☠️ Pasa una «CSE key» a `google.com/search`, que no la usa, y usa una regex de 2017. Recopila empleados en masa (ToS de LinkedIn y RGPD). |
| `Instagram/README` | Enlace a 4K Stogram, un programa comercial para descargar fotos. | ☠️ No es código. Fomenta la descarga masiva de contenido de terceros. |
| `PDF_crawler_webs/pdfget.py` | Crawler que descarga PDFs (huydx/pdfcrawler). | ❌ **Python 2** (`urllib2`, `Queue`, `BeautifulSoup` 3). `++l` no incrementa nada (en Python es un doble signo «+»), así que el bucle no tiene límite. Lleva una captura de 140 KB. |
| `Scraper-news/` | TFG de terceros (g4lk/TrabajoFinGrado): recorre 26 portadas de periódicos con hilos y guarda los artículos en Mongo. | ⚠️ Python 3, pero las clases CSS son de 2019. **TLS desactivado globalmente** (`ssl._create_default_https_context`). Contraseña de Mongo en el código (`123456@@`). Importa spaCy y scikit-learn sin usarlos. Sin límite de ritmo. `noticia.json` (3,6 MB) y `similaridades_noticias.json` contienen **el texto completo de artículos de prensa de 2019** (derechos de autor). |
| `TESTRA/bot_testra.py` + `PDFS/` | Scrapea el **Tablón Edictal de Sanciones de Tráfico (DGT)** con dryscrape y extrae **nombre, DNI, matrícula y localidad** de los sancionados a Mongo. | ❌ / ☠️ Python 2 (y el fichero está **corrupto**: faltan los caracteres `[`). dryscrape está abandonado. El servicio no respondió en 40 s. **Datos personales de sancionados** (ver §6). |
| `Tor_Spider/` | Crawler de .onion (ConanKapoor/TorScrapper). | ❌ / ☠️ **Inyección de comandos**: `os.system("gnome-terminal -e 'python3 main.py " + URL + "'")` y `os.system("rm -r " + dominio)`. Hash de la contraseña de control de Tor en el código. Parchea `socket.socket` y `getaddrinfo` de todo el proceso. 50 hilos. Lista de onions **v2 (muertos)**. Solo Linux con GNOME. Hay ficheros `~` de editor. |
| `camarasIP_webs/insecam.py` | Recorre insecam.org por países y guarda en Mongo la ubicación (lat/lon, ciudad, CP) y el enlace a **cámaras IP privadas sin contraseña**. | ❌ / ☠️ Python 2. Acceso a imágenes de cámaras de particulares sin autorización (ver §6). |

### 2.5 `data/` (≈ 75 MB)

| Dataset | Contenido | Observaciones |
|---|---|---|
| `spanish-names-master/` | Frecuencias de nombres y apellidos del INE (CSV). | ✅ Útil. Datos agregados, no personales. Hay que comprobar la licencia del repositorio de origen y actualizar desde el INE. |
| `Corruptos/list-spain-11-5-2019.txt` | 310 políticos de la página «Anexo: Políticos de España implicados en casos judiciales» (Wikipedia, 2019). | ⚠️ Datos sobre **infracciones penales** (art. 10 RGPD), con 7 años de antigüedad y sin fecha de corte ni licencia CC BY-SA en el fichero. Imputado ≠ condenado: mostrarlo sin contexto puede ser difamatorio. |
| `Adjudicaciones/adjudicaciones.csv` (22,7 MB, 78 106 líneas) | Volcado Mongo de adjudicaciones (Andalucía, 2008…) con NIF de empresas. | ⚠️ Origen no documentado. Sustituible por PLACSP. |
| `Adjudicaciones/Cádiz/contratos_menores.json` (12 MB) | 17 969 contratos menores de la Diputación de Cádiz (formato de datos abiertos). | ✅ Datos públicos, aunque antiguos. |
| `Adjudicaciones/Madrid/*.rar` (23,5 MB) | RAR de 2019. **No lo he abierto** (contenido desconocido; necesita un descompresor externo). | ⚠️ Mejor sustituirlo por el portal de datos abiertos de la Comunidad de Madrid o por PLACSP. |
| `Adjudicaciones/Málaga`, `Sevilla` (≈ 60 PDF) | Relaciones de contratos de 2014-2018. | ⚠️ Antiguos. Un PDF de Sevilla está duplicado (`septiembre-2016(1).pdf`). |
| `Testra/` (27 PDF) | Edictos de sanciones de tráfico de julio de 2018. | ☠️ **Datos personales de terceros** (nombre, DNI, matrícula). **Duplicados byte a byte** en `bots/TESTRA/PDFS/`. |
| `newspaper/spain-newspaper.txt` | 120 dominios de prensa española. | ✅ Útil como semilla. |

En total, **28 hashes SHA-256 de PDF aparecen repetidos** en el repositorio.

### 2.6 Ficheros sobrantes

- `__pycache__/` con `.pyc` de **CPython 3.6** en 7 carpetas (ya excluidos por `.gitignore`).
- `bots/BOE/botE_BOE_A (copia).py`, `bots/Tor_Spider/TorScrapper.py~`, `bots/Tor_Spider/onionlinks.txt~`.
- `modules/html_reports/` (copia de un paquete que ya está en `requiriments.txt`).
- `bots/TESTRA/PDFS/` = `data/Testra/`.
- `bots/TESTRA/onlink.png` y `bots/PDF_crawler_webs/readmeimg/sample.png` (capturas).

---

## 3. Dependencias (`requiriments.txt`)

El fichero tiene una **errata en el nombre** (`requiriments` en lugar de `requirements`), así que pip, Dependabot y los IDE no lo detectan. Además no fija versiones y **le faltan dependencias que el código usa**: `beautifulsoup4` (aparece como `bs4`, un paquete «alias»), `lxml`, `easydev`, `jinja2`, `markdown`, `pdfminer.six`, `stem`, `spacy`, `scikit-learn`, `tabula-py`, `dryscrape`, `intelxapi`, `wordcloud`, `Pillow`, `numpy`.

Datos de PyPI consultados el 20/11/2026:

| Paquete | Última versión (fecha) | Estado | Alternativa | ¿Se quita? |
|---|---|---|---|---|
| `pydns` | 2.3.6 (2012) | Muerto, Python 2. | `dnspython` 2.9 | **Sí** |
| `py3DNS` | 4.0.2 (2024) | Mantenimiento mínimo; duplica a `pydns`. | `dnspython` | **Sí** |
| `validate_email` | 1.3 (2015) | Muerto. **Choca con `py3-validate-email`**: los dos instalan el módulo `validate_email`. | `email-validator` (sintaxis) + `dnspython` (MX) | **Sí** |
| `py3-validate-email` | 1.0.5.post2 (2024) | Hace **sondeo SMTP (RCPT TO)**: técnica activa, el puerto 25 suele estar bloqueado por el ISP y puede meter tu IP en listas negras. | `email-validator` + MX/SPF/DMARC con `dnspython` | **Sí** |
| `twint` | 2.1.20 (2020) | Muerto; repositorio archivado. | Ninguna gratuita fiable. La API de X es de pago: plugin opcional. | **Sí** |
| `facebook-scraper` | 0.2.59 (2022) | Abandonado; Facebook exige login. | Ninguna legítima sin la Graph API. Solo dorks pasivos. | **Sí** |
| `newspaper3k` | 0.2.8 (2018) | Abandonado. | `newspaper4k` 0.9.6.1 (nov. 2026, activo) | **Sustituir** |
| `mechanize` | 0.4.10 (2024) | Vivo, pero aquí solo sirve para **rellenar formularios de login** de terceros. | No hace falta (`httpx`). | **Sí** |
| `wget` | 3.2 (2015) | Muerto. | `httpx` en streaming | **Sí** |
| `wikipedia` | 1.4.0 (2014) | Muerto. Scrapea la API sin User-Agent. | API de MediaWiki y Wikidata directamente con `httpx` | **Sí** |
| `wikipedia-api` | 0.16.0 (sep. 2026) | Vivo, pero desde la 0.6 el **primer parámetro es `user_agent`**: la llamada `Wikipedia('es')` del original ya no sirve como idioma **(no verificado)**. | API de MediaWiki directa | **Sí** (sobra) |
| `random-user-agent` | 1.0.1 (2018) | Muerto. Falsear el UA va contra la transparencia. | UA propio e identificable, configurable | **Sí** |
| `requests` / `requests[socks]` / `pysocks` | — | Vivos, pero síncronos. | `httpx[socks]` (async, HTTP/2, timeouts por defecto) | **Sustituir** |
| `bs4` | — | Paquete alias de `beautifulsoup4`. | `beautifulsoup4` + `lxml`, o `selectolax` | **Corregir nombre** |
| `pymongo` | 4.20 (nov. 2026) | Vivo, pero el código usa APIs eliminadas en la 4.0. | SQLite/SQLModel por defecto; `pymongo` como extra opcional | **Opcional** |
| `datefinder` | 1.0.0 (2026) | Vivo. No se usa (`parser_EN_DATE` no existe en `parsers.py`: `AttributeError`). | `dateparser` si hace falta | **Sí** |
| `networkx` | 3.7 (sep. 2026) | Vivo. **La 3.7 exige Python ≥ 3.12.** | Mantener | No |
| `matplotlib` | — | Solo para un PNG de un grafo que no se usa. | vis-network en el informe HTML | **Sí** |
| `tldextract` | 5.4.0 (2026) | Vivo. Descarga la lista PSL en la primera ejecución (hay que cachearla para los tests). | Mantener | No |
| `texttable`, `docopt` | — | No se usan. | `rich`, `typer` | **Sí** |
| `html-reports` | 0.2.0 (2021) | Abandonado; sin autoescape. | Jinja2 con `autoescape=True` | **Sí** |
| `intelxapi` (no listado) | No está en PyPI | Se instala desde GitHub. | Cliente `httpx` propio contra la API de IntelX (opcional, con clave) | Sustituir |

---

## 4. Fuentes y APIs externas (comprobadas el 20/11/2026)

| Fuente | Uso original | Estado | Sustituto recomendado |
|---|---|---|---|
| **LibreBORME** `libreborme.net/borme/api/v1/persona/…` | Cargos societarios | ❌ 301 → `librebor.me` → **410 Gone**: «our v1 API was deprecated… update your authentication». | **BORME vía la API de datos abiertos del BOE** (`/datosabiertos/api/borme/sumario/AAAAMMDD` responde 200 con `Accept: application/json`) + parseo de los PDF de la Sección A (actos inscritos) con un índice local. `librebor.me` v2 como plugin **opcional con clave**. |
| **BOE** `diario_boe/xml.php?id=` | Texto de indultos | ✅ 200 | Mantener para documentos sueltos. Para sumarios diarios, la **API de datos abiertos** (`/datosabiertos/api/boe/sumario/…`, `/legislacion-consolidada`), que responde 200 **si se envía `Accept: application/json` o XML**; sin esa cabecera da 400. **La API no tiene búsqueda por nombre**: hay que construir un índice local (SQLite FTS5) recorriendo sumarios de un rango de fechas, o usar el buscador web del BOE como alternativa. |
| **API REST de Dante's Gates** | Anunciada en el README | **No hay código.** Es un producto de pago. | No se implementa: queda retirada (en el README ponemos un enlace al producto del autor). |
| **IntelX** | Pastes, leaks, darknet (desactivado) | Requiere clave. **(no verificado)** | Plugin opcional con clave, **solo metadatos** (nunca descargar el contenido del leak). |
| **Páginas Amarillas** | Anunciada en el README | **No hay código.** La web devuelve un reto de Incapsula (anti-bot). | Retirada. Alternativa: la consulta pública de empresas del BORME o dorks. |
| **Pastebin** | Anunciada como búsqueda | Solo existe la URL `pastebin.com/u/<nick>`. Pastebin no tiene búsqueda pública. | WhatsMyName incluye Pastebin. Para pastes, IntelX opcional. |
| **Google** (scraping) | Personas, nick, teléfono, PDF, noticias | ❌ 302 a consentimiento o `/sorry` | **Google Programmable Search JSON API** (clave + cx; 100 consultas/día gratis) como plugin opcional. |
| **Bing** (scraping) | Teléfono | ⚠️ HTML 200 | La **Bing Web Search API se retiró en agosto de 2025**. Lo dejo como scraper experimental desactivado por defecto o directamente fuera. **Brave Search API** (clave; el endpoint responde 422 sin ella) como alternativa oficial. |
| **Dogpile** | Personas, teléfono | ❌ 202 vacío | Retirada. |
| **DuckDuckGo** (`/html/`) | Noticias, nick | ✅ 200 | Librería **`ddgs`** 9.16 (mantenida), con límite de ritmo. |
| **Mojeek** | (no usado) | ✅ 200 HTML | Mantener (scraper sencillo) y su **API oficial** opcional con clave. |
| **YouTube** (scraping) | Personas | ❌ Selectores de 2019 | **YouTube Data API v3** opcional con clave. |
| **ABC hemeroteca** | Prensa | ❌ 404 | Búsqueda de prensa genérica: `ddgs` con `site:` sobre los 120 dominios + extracción con `newspaper4k`. |
| **Facebook / Twitter (X) / Instagram / LinkedIn** | Posts, cuentas, empleados | ❌ (login obligatorio o API de pago) | **Retiradas.** Solo dorks pasivos en buscadores y comprobación de perfil público vía WhatsMyName. |
| **emailrep.io** | Reputación de email | ❌ 429: «the unauthenticated API is currently disabled. please use an API key» | Plugin opcional con clave gratuita. |
| **Have I Been Pwned** | Brechas (scraping HTML) | ⚠️ `/api/v3/breaches` (catálogo) es público ✅. `/breachedaccount` **requiere clave de pago**. | Plugin con clave. Sin clave, solo el dominio (catálogo de brechas). Nunca contraseñas. |
| **Netflix / WordPress / Facebook** (login) | ¿Existe la cuenta? | ☠️ | **Retiradas sin sustituto.** |
| **DGT TESTRA** | Sancionados | Sin respuesta en 40 s | ☠️ **Retirada.** |
| **insecam.org** | Cámaras | (no comprobado a propósito) | ☠️ **Retirada.** |
| **Torch (.onion v2)** | Buscador Tor | ❌ Onion v2 inexistente | Ahmia (clearnet/onion v3) como plugin opcional del modo Tor. |
| **Civio Indultómetro** | Indultos (CSV local) | ✅ La web responde 200 | Mantener el CSV unificado y documentar cómo actualizarlo. |
| **INE** | Frecuencia de nombres (CSV local) | ✅ La web responde 200 | Mantener el CSV. Añadir un script para actualizarlo desde los ficheros del INE. |
| **PLACSP** (contratación) | No se usaba (solo datos locales antiguos) | ✅ Feed Atom de sindicación (200; unos 2 MB por página, lento: 20 s) | Descarga incremental de los **ZIP mensuales de datos abiertos** a un índice local + feed Atom para lo reciente. |
| **Wikidata / MediaWiki API** | — | ✅ 200 | Sustituye a `wikipedia` y `wikipedia-api`. |
| **RDAP** (`rdap.org`) | — | ✅ 200 | Nuevo: dominios. |
| **crt.sh** | — | ⚠️ 502 intermitente; responde 200 al reintentar | Nuevo: subdominios (con reintentos). Alternativa: CertSpotter. |
| **Wayback CDX** | — | ✅ 200 | Nuevo. |
| **ip-api.com** | — | ✅ Por HTTP (por HTTPS es de pago: 403) | Nuevo: geolocalización y ASN de IP. Mejor `ipinfo.io` (200, HTTPS, gratis con límite) o RDAP de IP. |
| **keys.openpgp.org** | — | ✅ (404 = no hay clave) | Nuevo: PGP por email. |
| **Gravatar** | — | ✅ (404 = no existe) | Nuevo, por hash del email. |
| **Google Fact Check Tools** | — | ✅ (403 sin clave) | Nuevo: plugin opcional con clave. |
| **WhatsMyName** (`wmn-data.json`) | — | ✅ **691 sitios**, con campos `e_code/e_string/m_code/m_string/cat/protection` | Nuevo: sustituye a la lista de 94 webs. |

---

## 5. Problemas de calidad

1. **Errores tragados**: 49 bloques `except:` / `except Exception`, la mayoría con `pass` o con un mensaje genérico. Así se ocultan bugs reales: `NameError: target`, `AttributeError: colores.Alert`, `AttributeError: Collection.insert`. En `BuscadorEmails.py` el `except` de emailrep **muestra publicidad de la app de pago** en lugar del error.
2. **Estado global**: `modules/config.py` y `modules/data.py` hacen de «base de datos» en memoria compartida entre módulos; `rep = Report()` está a nivel de módulo. No se pueden lanzar dos investigaciones en el mismo proceso ni testear nada aislado.
3. **`print` en lugar de logging**: no hay niveles ni fichero, y los mensajes mezclan inglés y español. Los resultados solo existen en la consola.
4. **Interactividad obligatoria**: todo funciona con `input()`, así que no se puede automatizar ni testear.
5. **Solo Linux**: `os.system("pdfgrep …")`, `ls -A | wc -l`, `gnome-terminal`, `rm -r`, rutas relativas al directorio actual (`data/…`, `report/…`) y `os.chdir` en mitad de la ejecución. `control.py` confunde macOS con Windows.
6. **Dependencias de infraestructura**: MongoDB obligatorio (el `MongoClient()` se crea al importar; además usa dos bases distintas, `Dante` y `Test`). `pdfgrep`, `qpdf` y Tor con ControlPort son externos y no están documentados para Windows.
7. **Código Python 2** mezclado con Python 3 (6 ficheros no compilan).
8. **Código duplicado**: tres copias de `search_google_` (Personas, Nick y NoticiasFalsas, casi idénticas); tres funciones `search_*` casi iguales en NoticiasFalsas; `remove_tags` definido 4 veces; `insert_mongo` en cada bot; `html_reports` copiado; PDFs duplicados.
9. **Código que se ejecuta al importar**: `scammerInfo.py`, `BOE-indultos.py` y `wordcloud.py`.
10. **Bugs de lógica**: `status_code >= 200 or < 300` (siempre verdadero); regex de DNI anclada; `++l`; claves duplicadas en un dict; `menu()` recursivo que devuelve `None`; `str(response)[11]` para leer el código HTTP.
11. **Sin tests, sin linter, sin empaquetado** (no hay `setup.py` ni `pyproject.toml`) y sin versiones fijadas.
12. **Rendimiento**: 94 peticiones en serie sin timeout; un CSV de 2 MB leído en cada búsqueda; los bots del BOE harían millones de peticiones por fuerza bruta.
13. **Falsos positivos**: la detección de perfiles por «código 200 y URL igual» da positivo en sitios que devuelven 200 a cualquier usuario. No hay noción de confianza.

---

## 6. Problemas de seguridad

| # | Gravedad | Hallazgo | Dónde |
|---|---|---|---|
| S1 | **Crítica** | **Inyección de comandos** al concatenar en shell: nombre de empresa (de Internet o del usuario) → `pdfgrep`; URL → `gnome-terminal -e`; dominio → `rm -r`; nombre de fichero → `qpdf`. | `modules/findData.py:31,37,43`; `bots/Tor_Spider/TorScrapper.py:10`, `main.py:17`; `bots/BOE/botE_BOE_*.py:48` |
| S2 | **Alta** | **TLS desactivado**: `ssl._create_unverified_context()` y, en Scraper-news, desactivado **para todo el proceso**. | `bots/BOE/botE_BOE_A.py:91`, `botE_BOE_B.py:86`, `bots/TESTRA/bot_testra.py:87`, `bots/Scraper-news/periodicos.py:72-73` |
| S3 | **Alta** | **Credenciales en el código**: contraseña de Mongo `123456@@`, hash de la contraseña de control de Tor y clave de IntelX en `key.py` (vacía, pero el patrón invita a commitearla). | `bots/Scraper-news/config.py`, `bots/Tor_Spider/crawl_bot.py:12`, `modules/key.py` |
| S4 | **Alta** | **XSS almacenado en los informes**: texto scrapeado → Markdown → HTML sin escapar, con una plantilla Jinja2 sin `autoescape`. Abrir el informe ejecuta el JavaScript que haya puesto una web de terceros. | `modules/report.py`, `modules/html_reports/`, `BuscadorNick.py` |
| S5 | Media | **31 peticiones sin timeout** (`requests.get`, `urlopen`, `br.open`), que pueden colgarse para siempre. | Todo el proyecto |
| S6 | Media | **Regex inyectada en MongoDB** (`$regex: target`): ReDoS y resultados manipulables. | `modules/mongo.py:find_in_BOE`, `find_companies` |
| S7 | Media | **SSRF y descarga de ficheros no confiables**: se descargan y parsean URLs que vienen de resultados de búsqueda (PDFs con `wget`, cualquier HTML), sin limitar esquema, host (IPs privadas, `localhost`), tamaño ni tipo. Un PDF malicioso queda en `data/<objetivo>/`. | `BuscadorPersonas.py:search_google_and_downloadPDF`, `search_dogpile_`, `BuscadorNoticiasFalsas.py` |
| S8 | Media | **Monkey-patching global de `socket`** para enviar todo por Tor, con riesgo de fugas o de mezclar tráfico Tor y clearnet sin querer. | `bots/Tor_Spider/crawl_bot.py` |
| S9 | Baja | **Hash débil**: MD5 para identificar PDFs. | `bots/BOE/*.py` |
| S10 | Baja | **User-Agent falso** de Firefox 3 (2008) y rotación de UA para eludir bloqueos. | `BuscadorEmails.py`, `BuscadorTelefonos.py`, `scammerInfo.py` |
| S11 | Baja | **Rutas a partir de entrada del usuario**: `data/{target}` y `report/report-{title[:10]}` (path traversal con `..`). | `BuscadorPersonas.py`, `BuscadorNoticiasFalsas.py` |
| S12 | Info | **Deserialización insegura**: no he encontrado `pickle`, `yaml.load` ni `eval`. ✅ | — |

---

## 7. Riesgos legales y éticos

### 7.1 Funciones que hay que retirar (no son OSINT)

| Función | Por qué |
|---|---|
| Login con contraseña `123456` en Facebook, Netflix y WordPress (email y teléfono) | Es un **intento de acceso a un sistema ajeno con credenciales que no son tuyas**, no una consulta a una fuente abierta. Puede encajar en el **art. 197 bis del Código Penal** (acceso sin autorización a sistemas), viola las condiciones de uso, provoca **bloqueos y alertas de seguridad en la cuenta de la víctima** (que así se entera) y deja tu IP registrada. |
| Generador `evilmail.php` (spoofing de remitente) | Es una herramienta para **phishing y suplantación**. No tiene ninguna utilidad OSINT. |
| `bots/camarasIP_webs/insecam.py` | Recopila y geolocaliza **imágenes de cámaras privadas** expuestas por error: intimidad (art. 18 CE, art. 197 CP), datos de geolocalización de particulares. |
| `bots/TESTRA/` + `data/Testra/` | Los edictos de la DGT se publican **solo para notificar**, y la LOPDGDD (DA 7ª) limita los datos que pueden aparecer y su indexación. Extraer y almacenar en masa **nombre, DNI, matrícula y localidad** de sancionados no tiene base legal para un tercero. Además el repositorio **redistribuye esos PDFs** (dos veces). |
| `bots/LinkedIn/` | Recopilación masiva de empleados (ToS de LinkedIn; caso hiQ v. LinkedIn; RGPD: tratamiento masivo sin informar). |
| `bots/Instagram/` | Fomenta la descarga masiva de contenido ajeno. |
| Mensaje «If you want the password ... You know what to do» | Invita a buscar contraseñas filtradas. |

### 7.2 RGPD / LOPDGDD

- **Base jurídica (art. 6 RGPD)**: que un dato sea público **no significa que se pueda tratar libremente**. Quien investiga es responsable del tratamiento y necesita una base legal, normalmente el **interés legítimo (art. 6.1.f)** con una ponderación documentada. La herramienta original ni pregunta la finalidad ni la deja registrada. → En la nueva versión **cada caso exige una finalidad declarada** y se guarda.
- **Categorías especiales (art. 9)**: `parsers.py` extrae **religión** y **partido político** (ideología) de Wikipedia, además del número de hijos y el horóscopo, que no aportan nada. → Se eliminan religión y horóscopo. La afiliación política solo se recoge si la persona tiene un cargo público y el dato está en Wikidata como hecho público (art. 9.2.e: datos hechos manifiestamente públicos), y va marcada como sensible.
- **Datos penales (art. 10 RGPD, art. 10 LOPDGDD)**: la lista de «corruptos» y los indultos. → Se mantienen como fuentes con fecha de corte, enlace a la fuente oficial, la advertencia «imputado ≠ condenado» y la opción de desactivarlas.
- **Minimización y conservación (art. 5.1.c y 5.1.e)**: el original guarda HTML completo y textos en Mongo para siempre. → Casos con caducidad configurable, comando de borrado y almacenamiento solo de lo necesario más la evidencia.
- **DNI**: buscar el DNI de alguien en Google y descargar PDFs con su DNI es justo el tratamiento que más protección exige. → El DNI solo se usa para **validar** (letra de control) y, si el usuario lo activa explícitamente, para dorks. Se anonimiza en logs e informes.
- **Derechos de autor**: `noticia.json` redistribuye miles de artículos completos de prensa de 2019. → Se elimina. Solo se guardan URL, titular, fecha y un fragmento corto como evidencia.

### 7.3 Condiciones de uso de las plataformas

Hacer scraping de Google, Facebook, Instagram, LinkedIn o X va contra sus condiciones de uso. En la UE no es un delito en sí mismo, pero supone un riesgo contractual y de bloqueo de IP, y cuando hay datos personales el riesgo pasa a ser también de RGPD. → Se priorizan **APIs oficiales y datos abiertos**. El scraping que quede (DDG, Mojeek, WhatsMyName) será identificable (UA propio), respetará el ritmo y `robots.txt` donde aplique y se podrá desactivar.

---

## 8. Licencia

**Problema**: los `.py` principales llevan una cabecera **BSD-2-Clause** con la cláusula final del FreeBSD Project («Copyright (c) 2020, QuantiKa14 Servicios Integrales S.L & Jorge Coronado Diaz»). En cambio, `LICENSE` es la **GNU GPL v3** y el README dice «licencia GNU». Además hay **código de terceros** incluido sin su licencia:

| Componente | Origen | Licencia del origen (a confirmar) |
|---|---|---|
| `search_engines/` | tasos-py/Search-Engines-Scraper | MIT (falta el aviso) |
| `modules/html_reports/` | paquete `html-reports` | MIT (falta el aviso) |
| `bots/Tor_Spider/` | ConanKapoor/TorScrapper | Sin confirmar |
| `bots/PDF_crawler_webs/` | huydx/pdfcrawler | Sin confirmar |
| `bots/Scraper-news/` | g4lk/TrabajoFinGrado | Sin confirmar |
| `bots/LinkedIn/` | repositorio «pentest_scripts» | Sin confirmar |
| `data/spanish-names-master/` | Repositorio de datos del INE | Sin confirmar |
| `data/Corruptos/` | Wikipedia | CC BY-SA 4.0 (falta la atribución) |

**Propuesta, respetando al autor**:

1. **Distribuir el conjunto bajo GPL-3.0**, que es lo que declara el `LICENSE` del autor. La BSD-2-Clause es **compatible** con GPL-3.0: el código BSD puede formar parte de una obra GPL siempre que se conserven sus avisos. Así se respetan las dos declaraciones a la vez.
2. Identificador SPDX **`GPL-3.0-only`**, no `-or-later`: el autor no concedió «o posterior» y no podemos añadir permisos que él no dio.
3. En los ficheros que **deriven** de su código, **mantener su aviso de copyright BSD** y añadir debajo `SPDX-License-Identifier: GPL-3.0-only` y «Modificado por … en 2026». Los ficheros nuevos llevan solo la cabecera SPDX.
4. Crear `NOTICE` / `AUTHORS` con los créditos: Jorge Coronado / Quantika14 como autor original, los autores de los componentes de terceros que se conserven y la atribución CC BY-SA de los datos de Wikipedia.
5. Lo que se elimine (TorScrapper, pdfcrawler, TFG…) deja de ser un problema. Lo que se conserve tendrá su licencia en `third_party/LICENSES/`.
6. **Recomendado**: abrir un *issue* en el repositorio original para que el autor aclare la discrepancia, y enlazarlo desde el `CHANGELOG`.

> Nota: esto es una propuesta técnica razonada, no asesoramiento jurídico.

---

## 9. Qué se puede reaprovechar

- La **idea y el enfoque español**: BOE, BORME, indultos, contratación, INE, prensa española y fact-checkers.
- `data/spanish-names-master/` (INE), `data/newspaper/spain-newspaper.txt`, el CSV de Civio (unificado) y los contratos de Cádiz.
- Las palabras clave de fact-checking y corrupción (`config.FC_*`) como heurísticas explicables.
- La lógica de clasificar los resultados en prensa, redes sociales y otros.
- El parseo de la ficha de Wikipedia, sustituido por Wikidata.
