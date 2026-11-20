# Plan de modernización

Basado en [AUDITORIA.md](AUDITORIA.md). Pendiente de aprobación antes de empezar la Fase 2.

## 1. Principios

1. **Fuentes oficiales y datos abiertos primero.** El scraping solo se usa donde no hay alternativa, siempre identificable (User-Agent propio), con límite de ritmo y desactivable.
2. **Nada activo contra terceros**: ni inicios de sesión, ni formularios de reseteo, ni sondeo SMTP, ni pruebas de credenciales.
3. **Fallar sin romper**: si una fuente falla, se registra el error en el caso y se sigue con las demás.
4. **Trazabilidad**: cada dato lleva su fuente, la URL, la fecha UTC, un nivel de confianza y su evidencia con hash.
5. **Privacidad por diseño**: cada caso exige una finalidad declarada, las categorías especiales están desactivadas, los casos caducan y se pueden borrar.
6. **Código simple y testeable**: sin estado global, todo inyectable y los tests no dependen de Internet.

## 2. Decisiones técnicas

| Tema | Decisión | Por qué |
|---|---|---|
| Python | `requires-python >= 3.11`; desarrollo en 3.12 (ya instalado: `py -3.12`); CI en 3.11, 3.12 y 3.13 | Lo que pediste. `networkx` 3.7 exige 3.12, así que fijo `networkx>=3.4,<4` para seguir soportando 3.11. |
| Gestor de dependencias | **uv** (`uv.lock`), con export a `requirements.txt` | Rápido, fija versiones y gestiona Python en Windows. Hay que instalarlo: `winget install astral-sh.uv`. |
| HTTP | `httpx` async + `hishel` (caché en disco con TTL) + reintentos propios con backoff + semáforo por dominio | Un solo cliente con timeouts por defecto, HTTP/2, proxy y SOCKS para Tor. |
| CLI | Typer + Rich | Subcomandos, ayuda automática y tablas. |
| Configuración | `pydantic-settings` (`.env`, variables `OSINT_*`, `osint.toml`) | Las claves nunca van en el código. Cada fuente se activa o desactiva por configuración. |
| Modelos | Pydantic v2 | Validación y esquema JSON del export gratis. |
| Almacenamiento | SQLModel sobre SQLite (más FTS5 para los índices BOE/BORME/PLACSP); MongoDB como *extra* opcional `[mongo]` | Cero instalación en Windows. Mongo deja de ser obligatorio. |
| PDF | `pypdf` (texto) y `pdfplumber` (tablas) | Sustituyen a `pdfgrep` y `qpdf`; sin shell. |
| HTML | `selectolax` o `beautifulsoup4` + `lxml` | — |
| Prensa | `newspaper4k` | Sustituye a newspaper3k. |
| Similitud | `rapidfuzz` + TF-IDF de `scikit-learn` (extra opcional) | Explicable. |
| Grafo | `networkx` → GEXF, GraphML, CSV para Maltego; vis-network **embebido** en el HTML | Informe autocontenido, sin CDN. |
| Informes | Jinja2 con `autoescape=True` | Cierra el XSS (S4). |
| Calidad | ruff, mypy (`strict` con excepciones razonadas), pytest + `respx`, pytest-cov, pre-commit, pip-audit | — |

## 3. Arquitectura

```
src/osintsuite/
├── cli.py                 # osint persona|nick|email|telefono|dominio|ip|empresa|noticia|caso …
├── config.py              # Settings (pydantic-settings)
├── models.py              # Entity, EntityType, Finding, Evidence, Case, SourceRun
├── http.py                # HttpClient: timeouts, reintentos, rate limit por dominio, caché, proxy/Tor, bloqueo SSRF
├── logging.py             # logging estructurado (consola Rich + JSONL por caso)
├── storage/               # repositorio abstracto + SQLite (SQLModel) + Mongo opcional
├── sources/               # plugins: base.py (interfaz + registro), boe.py, borme.py, placsp.py, …
├── investigations/        # orquestador: depth/pivoting, deduplicación, concurrencia
├── correlation/           # grafo, merge de entidades, puntuación de confianza (con ajuste por frecuencia INE)
├── reports/               # html (Jinja2), json (+ JSON Schema), csv, md, gexf/graphml, maltego
├── evidence/              # SHA-256, UTC, URL, copia en bruto bajo casos/<id>/evidencias/
├── data/                  # datasets pequeños empaquetados (INE, prensa, Civio unificado)
└── legacy/                # envoltorios de BuscadorPersonas.py, etc.
```

### Interfaz de plugin

```python
class Source(Protocol):
    name: ClassVar[str]                    # "boe"
    input_types: ClassVar[set[EntityType]] # {PERSONA, EMPRESA}
    requires_api_key: ClassVar[str | None] # nombre del campo en Settings, o None
    sensitive: ClassVar[bool] = False      # datos penales o especiales → desactivada por defecto
    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]: ...
```

- El registro se hace con *entry points* (`osintsuite.sources`), para que cualquiera pueda añadir plugins desde fuera del paquete.
- `SourceContext` lleva el `HttpClient`, los `Settings`, el logger del caso y el gestor de evidencias.
- Si falta la clave: la fuente queda `SKIPPED (sin clave)`. Si lanza una excepción: `FAILED` con traza en el log. Nunca se aborta el caso.

### Flujo de una investigación

`CLI → Case(id, objetivo, finalidad, fuentes) → Orchestrator → [Source.run en paralelo] → Finding[] → dedup/merge → nuevas entidades → (si depth > 0 y no vistas) cola → Storage → Correlation → Reports`

## 4. Qué pasa con cada pieza original

> **Las eliminaciones necesitan tu OK** (ver §6). Mientras tanto no se borra nada.

| Pieza | Propuesta |
|---|---|
| 5 `Buscador*.py` | Se reescriben en `sources/` e `investigations/`. Los originales quedan como envoltorios finos que llaman a `osint …`. |
| Login con `123456` (Emails/Teléfonos), `evilmail.php` | **Eliminar** sin sustituto. |
| `modules/*` útiles (INE, parsers, er, prensa, lista de políticos) | Migrar y corregir. |
| `modules/` muertos (`bing.py`, `scammerInfo.py`, `wordcloud.py`, `twitter.py`, `facebook.py`, `html_reports/`, `control.py`, `key.py`) | **Eliminar.** |
| `search_engines/` | **Eliminar.** Sustituido por los plugins `ddg` (ddgs), `mojeek`, `brave`, `google_cse` y `mojeek_api`, opcionales y con clave. |
| `bots/BOE/` | Sustituidos por los plugins `boe` y `borme` (API de datos abiertos + índice local incremental). **Eliminar** los scripts Python 2 y la copia. `boes.txt` se elimina: es redundante con la columna `id` del CSV de Civio. |
| `bots/BOE/civio/*.csv` | **Unificar** en `data/civio_indultos_1996-2018.csv` (10 582 filas, sin pérdida). |
| `bots/TESTRA/`, `data/Testra/` | **Eliminar** (datos personales de sancionados). |
| `bots/camarasIP_webs/`, `bots/LinkedIn/`, `bots/Instagram/` | **Eliminar.** |
| `bots/PDF_crawler_webs/` | **Eliminar.** Se sustituye por la descarga controlada de PDFs como evidencia, con límite de tamaño y de tipo. |
| `bots/Scraper-news/` + JSON | **Eliminar** el código y los JSON (derechos de autor). La lista de 26 medios pasa a `spain-newspaper.txt`. |
| `bots/Tor_Spider/` | **Eliminar.** Si da tiempo, nuevo plugin `ahmia` y modo Tor aislado (cliente separado, solo `.onion`). |
| `data/Adjudicaciones/*` | **Sacar del repositorio** (unos 60 MB). Opción A: borrarlos y usar PLACSP. Opción B: moverlos a un *release* o a una carpeta `datos-legacy/` ignorada por git, con un script de importación al índice local. |
| `data/Corruptos/` | Mantener como fuente `sensitive=True` (desactivada por defecto), con fecha de corte, atribución CC BY-SA y la advertencia «imputado ≠ condenado». |
| `data/spanish-names-master/`, `data/newspaper/` | Mantener, dentro del paquete. |

## 5. Orden de trabajo (un commit por bloque)

| # | Fase | Bloque | Verificación |
|---|---|---|---|
| 1 | 2 | Limpieza aprobada (eliminar, unificar Civio, `.gitattributes`) + `LICENSE`/`NOTICE`/SPDX | `git diff --stat` |
| 2 | 2 | Esqueleto: `pyproject.toml`, uv, ruff, mypy, pytest, pre-commit | `uv run pytest`, `ruff check` |
| 3 | 2 | `config`, `models`, `logging`, `evidence` | Tests unitarios |
| 4 | 2 | `http.py` (caché, reintentos, rate limit, bloqueo SSRF) | Tests con `respx` |
| 5 | 2 | `storage` SQLite + casos (crear, retomar, comparar, borrar) | Tests con SQLite en memoria |
| 6 | 2 | Interfaz de plugins + orquestador + pivoting `--depth` | Tests con fuentes falsas |
| 7 | 2 | CLI mínima + envoltorios legacy | `osint --help` |
| 8 | 3 | Fuentes offline: INE, DNI/NIE/CIF, `phonenumbers`, email (sintaxis), lista de políticos, Civio | Tests sin red |
| 9 | 3 | Wikipedia/Wikidata, BOE, BORME (índice), PLACSP | Respuestas grabadas + **una prueba manual en vivo** |
| 10 | 3 | Nick: WhatsMyName async con detección de falsos positivos | Ídem |
| 11 | 3 | Email: MX/SPF/DMARC/DKIM, desechables, Gravatar, PGP, HIBP (clave), emailrep (clave) | Ídem |
| 12 | 3 | Dominio/IP: RDAP, DNS, crt.sh, Wayback, ipinfo/RDAP-IP, tecnologías; Shodan/Censys (clave) | Ídem |
| 13 | 3 | Buscadores (ddgs, Mojeek, Brave, Google CSE) + dorks como plantillas | Ídem |
| 14 | 3 | Prensa (`newspaper4k`) y verificación de noticias con puntuación explicada | Ídem |
| 15 | 3 | Correlación + informes HTML/JSON/CSV/MD/GEXF/Maltego | Snapshot tests |
| 16 | 4 | Cobertura ≥ 70 %, mypy, pip-audit, GitHub Actions (Linux + Windows), Dockerfile, compose | CI en verde |
| 17 | 5 | README, ARQUITECTURA, CREAR_PLUGIN, USO_RESPONSABLE, CHANGELOG | Revisión |
| 18 | Opc. | FastAPI + UI; Ahmia/Tor | — |

Al acabar cada fase te paso un resumen: qué se hizo, qué falta, qué falló y cómo probarlo. Las fuentes que no pueda verificar en vivo (caídas o sin clave) quedarán marcadas así en la tabla del README.

## 6. Decisiones tomadas (20/11/2026)

- **No se borra nada salvo las herramientas obsoletas** (indicación del usuario). Se han retirado:
  - Los scripts en Python 2: bots BOE A/B y su copia, PDF crawler, insecam, TESTRA y `modules/bing.py`.
  - Lo que depende de servicios muertos: `twitter.py` (twint), `facebook.py`, `bots/LinkedIn`, `scammerInfo.py` y la demo `wordcloud.py`.
  - Lo ya sustituido: `search_engines/`, `html_reports/` y `control.py`.
  - Las copias duplicadas: ficheros `~` y `bots/TESTRA/PDFS`, idéntica byte a byte a `data/Testra`.
- **Todos los datasets se conservan** en `data/`, incluidos `Testra` y `Adjudicaciones`. El resto del código original pasa a `legacy/` como referencia. Todo lo retirado se puede recuperar con el tag `baseline`.
- **Las funciones de §7.1 que no son obsoletas no se borran del código original**, pero **no se portan** a la nueva herramienta.
- **Licencia**: GPL-3.0-only, conservando los avisos del autor (`NOTICE`).
- **uv**: se instala **dentro del entorno virtual del proyecto** (`.venv`), sin tocar el sistema.

## 7. Decisiones que estaban pendientes (resueltas en §6)

1. **Eliminaciones** (§4): ¿apruebas eliminar las funciones y bots marcados ☠️ (login con `123456`, spoofing, insecam, TESTRA con sus PDFs, LinkedIn, Instagram) y el código muerto (Python 2, `search_engines/`, `html_reports/`, Tor_Spider, Scraper-news con sus JSON)?
2. **Datos de contratación antiguos** (`data/Adjudicaciones`, unos 60 MB): ¿opción A (borrar y usar PLACSP) u opción B (sacarlos del repositorio con un script de importación)?
3. **Licencia**: ¿de acuerdo con GPL-3.0-only conservando los avisos BSD del autor?
4. **uv**: ¿lo instalo con `winget install astral-sh.uv`?
