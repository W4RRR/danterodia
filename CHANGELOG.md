# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/). Cada entrada
explica **qué** cambió y **por qué**, comparado con el original de Quantika14.

## [Sin publicar] — 2.0.0.dev0

### Fase 2 — Nueva arquitectura

#### Añadido
- **Paquete instalable `osintsuite`** (`pyproject.toml`, PEP 621, `uv.lock`) con el comando `osint`.
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
- **Exportación**: JSON (con JSON Schema: `osint esquema`), CSV (protegido contra inyección de
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
