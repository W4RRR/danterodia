# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/). Cada entrada
explica **qué** cambió y **por qué**, comparado con el original de Quantika14.

## [Sin publicar] — 2.0.0.dev0

### Fase 4 — Calidad

#### Añadido
- **MongoDB opcional** (`pip install danterodia[mongo]`, `DANTERODIA_DATABASE_URL=mongodb://…`).
  Cumple el mismo contrato que SQLite, probado con `mongomock` y, en CI, contra un MongoDB 7
  real. *Por qué*: el original obligaba a instalar MongoDB; ahora es una opción para quien
  quiera compartir casos entre equipos.
- **GitHub Actions** (`.github/workflows/ci.yml`):
  - lint y formato (ruff) y tipos (mypy estricto);
  - tests en **Linux y Windows** con Python **3.11, 3.12 y 3.13**, con cobertura mínima del 70 %;
  - tests contra MongoDB real;
  - `pip-audit`;
  - construcción y arranque de la imagen Docker.
- **Docker**: `Dockerfile` multi-stage (dependencias exactas de `uv.lock`, sin herramientas
  de desarrollo, usuario sin privilegios, volumen `/datos`) y `docker-compose.yml` con
  MongoDB como **perfil opcional** (solo accesible desde la red interna y desde 127.0.0.1).
- `requirements.txt` exportado de `uv.lock` para quien prefiera `pip`.
- Umbral de cobertura (`fail_under = 70`); la cobertura actual es del 95 %.

#### Cambiado
- Los hooks de `pre-commit` que tocan espacios y finales de fichero excluyen los datos
  empaquetados y las fixtures, para que sigan siendo idénticos byte a byte a su origen.

#### Verificado
- `pip-audit`: **sin vulnerabilidades conocidas** en dependencias de producción ni de
  desarrollo (20/11/2026).
- Tests con Python 3.12 y 3.13 en Windows: 147 correctos y 1 omitido (el de MongoDB real,
  que necesita `TEST_MONGO_URL`).

### Fase 3 — Capacidades

Todas las fuentes se han probado **en vivo** el 20/11/2026 con objetivos no personales
(example.com, 8.8.8.8, la organización «github», Miguel de Cervantes, empresas del BORME) y
tienen tests sin conexión con respuestas reales recortadas o sintéticas.

#### Añadido: fuentes (29, activables por configuración)
- **Índice local** (`danterodia indice boe|borme|placsp|indultos|locales|estado`), en SQLite FTS5.
  *Por qué*: la API del BOE no busca por nombre. El original hacía fuerza bruta de IDs
  (millones de peticiones) y lo guardaba en MongoDB; ahora se usan los sumarios oficiales de
  forma incremental, sin repetir días ya descargados.
  - `boe`: títulos de todas las disposiciones (API de datos abiertos).
  - `borme`: **cargos societarios** (nombramientos, ceses, reelecciones…) desde el XML
    estructurado de la sección A. Sustituye a LibreBORME (API v1 retirada, HTTP 410).
  - `contratacion`: adjudicaciones de **PLACSP** (feed CODICE) y de los datasets locales de
    Cádiz y Andalucía. Busca por CIF (exacto) o por nombre.
  - `indultos` (sensible): Civio + título del Real Decreto en el BOE, que incluye el nombre.
  - `documentos_locales`: los PDF de `data/` con **pypdf** (sustituye a `pdfgrep`). Los
    edictos de TESTRA solo se importan con `--incluir-testra` y se marcan como sensibles.
- **Personas y empresas**: `ine`, `wikidata` (identidad, fechas, cargos, cuentas y web
  oficiales; sin religión, y la ideología solo con `--sensibles`), `identificadores`
  (CIF/DNI/NIE con dígito de control), `politicos_judicial` (sensible), `prensa`
  (DuckDuckGo News + **newspaper4k**, deduplicada).
- **Nick**: `whatsmyname` (≈690 webs, actualizable con `danterodia actualizar-wmn`). Exige
  **código HTTP y texto** esperados, así que no da por bueno un 200 genérico, que era el falso
  positivo del original.
- **Email**: `email` (sintaxis, desechable, proveedor, alias para pivotar), `dns` (MX, SPF,
  DMARC, DKIM, ¿suplantable?), `gravatar`, `pgp`, `hibp` y `emailrep` (con clave).
- **Teléfono**: `telefono` (phonenumbers: validez, tipo, operador original, zona horaria) y
  dorks con todas las variantes de formato.
- **Dominio/IP**: `rdap` (sucesor de WHOIS), `dns`, `crtsh` (subdominios por Certificate
  Transparency), `wayback`, `tecnologias` (huellas explicables), `ipinfo` + PTR, y
  `shodan`/`censys` con clave.
- **Buscadores**: `buscadores` con dorks en plantillas editables (`dorks.toml`), DuckDuckGo
  (`ddgs`) y, si hay clave, Brave, Google CSE y la API de Mojeek. Filtra anuncios y
  resultados que no contienen el objetivo.
- **Noticias**: `verificar_noticia` con corroboración en otros medios (TF-IDF + rapidfuzz),
  verificadores españoles (Maldita, Newtral, EFE Verifica, RTVE Verifica), Google Fact Check
  (con clave) y una **puntuación de 0 a 100 explicada señal a señal**.

#### Añadido: correlación e informes
- **Ajuste de confianza por frecuencia del nombre** (INE): una coincidencia solo por nombre
  se multiplica por `1/(1+log10(N))`. Queda registrado el valor original y el motivo.
- **Informe HTML autocontenido**: resumen, grafo interactivo (vis-network embebido, sin CDN),
  cronología, hallazgos filtrables, señales de verificación y anexo de evidencias con SHA-256.
  Usa autoescape, así que el contenido de terceros no puede ejecutar código en el informe.

#### Cambiado
- `HttpClient` admite reintentos y timeout por petición. WhatsMyName pasa de 4 min a 1 min
  con los mismos resultados.
- Un único contexto TLS compartido (sigue verificando certificados): los tests pasan de
  201 s a 21 s.
- Las fuentes pueden marcarse como **omitidas** con un motivo (p. ej. «índice vacío:
  ejecuta …») en lugar de fallar.
- `newspaper3k` → `newspaper4k`; `pydns`/`py3DNS` → `dnspython`; `validate_email` /
  `py3-validate-email` → `email-validator` (sin sondeo SMTP); `wikipedia`/`wikipedia-api` →
  API de Wikidata; scraping de buscadores → `ddgs` + APIs oficiales.

#### Retirado (sin sustituto, por estar muertos)
- Scraping de Google, Bing (su API se retiró en agosto de 2025), Dogpile, Yahoo, Startpage,
  Torch (onion v2) y Mojeek HTML; hemeroteca de ABC; Páginas Amarillas (anti-bots).

### Fase 2 — Nueva arquitectura

#### Añadido
- **Paquete instalable `danterodia`** (`pyproject.toml`, PEP 621, `uv.lock`) con el comando `osint`.
  *Por qué*: el original eran scripts sueltos con `input()`, sin versiones fijadas
  (`requiriments.txt`, con errata) y sin forma de automatizarlos ni testearlos.
- **Modelo de datos unificado** (`models.py`): `Entity`, `Finding`, `Evidence`, `SourceRun`, `Case`.
  *Por qué*: en el original cada script imprimía a su manera y guardaba en listas globales
  (`config.emailsData_list`…); no se podía cruzar nada ni exportarlo.
- **Casos** con ID, objetivo, **finalidad obligatoria**, fuentes, ejecuciones y hallazgos, en
  SQLite. Se pueden listar, ver, retomar, comparar, borrar y purgar por antigüedad.
  *Por qué*: el RGPD exige finalidad determinada (art. 5.1.b) y no conservar más de lo necesario
  (art. 5.1.e). Además MongoDB era obligatorio y ahora no hace falta instalar nada.
- **Cliente HTTP común** (`http.py`): timeouts, reintentos con backoff exponencial, límite por
  dominio, caché en disco, TLS siempre verificado, límite de tamaño, **anti-SSRF** (también en
  redirecciones) y cliente Tor separado solo para `.onion`.
  *Por qué*: el original tenía 31 peticiones sin timeout, TLS desactivado en 4 bots y descargaba
  cualquier URL de los resultados de búsqueda (ver AUDITORIA §6).
- **Sistema de plugins** (`sources/base.py`) por *entry points*. Una fuente que falla o tarda se
  registra como `error`/`timeout` y la investigación continúa; las que necesitan clave o son
  sensibles se omiten solas explicando el motivo.
  *Por qué*: en el original un fallo se tragaba con `except: pass` o tumbaba el script.
- **Pivoting** (`--depth N`) sin repetir entidades, con límite total y confianza mínima.
- **Correlación**: deduplicación y confianza combinada «o ruidoso» (`1 − Π(1 − c)`), explicada.
- **Exportación**: JSON (con JSON Schema: `danterodia esquema`), CSV (protegido contra inyección de
  fórmulas), Markdown, GEXF, GraphML y CSV para Maltego.
- **Evidencias** con SHA-256, fecha UTC y URL (`casos/<id>/evidencias/`).
  *Por qué*: el original usaba MD5 y no conservaba prueba de dónde salía cada dato.
- **Logging** estructurado: consola con Rich y un `caso.log.jsonl` por caso.
- **Fuente `ine`** (sin conexión): frecuencia del nombre y estimación de cuántas personas lo
  comparten. **Corrige un bug del original**: convertía «Ñ» en «N» y leía la fila equivocada
  (MUNOZ: 150 personas, frente a MUÑOZ: más de 100 000).
- **Envoltorios** `Buscador*.py` en la raíz que llaman a la nueva CLI (compatibilidad).
- Calidad: ruff (incluye reglas de seguridad *bandit*), mypy estricto, pytest + respx (sin
  internet), pre-commit. 76 tests, 97 % de cobertura.

#### Cambiado
- Librerías obsoletas sustituidas por equivalentes mantenidas: `requests`/`mechanize`/`wget` →
  `httpx`; `pymongo` obligatorio → SQLite (SQLModel); `html-reports` → Jinja2 con autoescape
  (Fase 3); `docopt`/`texttable` → Typer + Rich; `matplotlib` para el grafo → exportación GEXF.
- El código original pasa a `legacy/` como referencia.
- Windows: la salida y la entrada por tubería se fuerzan a UTF-8. *Por qué*: con cp1252 los
  caracteres como «·» provocaban `UnicodeEncodeError`, y «García» se leía como «GarcÃ­a».

#### Retirado (recuperable con el tag `baseline`)
- Herramientas obsoletas: bots BOE A/B, PDF crawler, insecam y TESTRA (Python 2); twint, Facebook,
  LinkedIn y scammer.info (servicios muertos); `search_engines/`, `html_reports/` y
  `control.py` (sustituidos); copias duplicadas (`~`, `(copia)`, PDFs de TESTRA repetidos).
- **No se portan** a la nueva herramienta las pruebas de login con contraseña `123456` ni el
  generador de spoofing de email (AUDITORIA §7.1); siguen solo en `legacy/`.
