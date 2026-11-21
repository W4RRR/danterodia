# danterodia

Herramienta OSINT de línea de comandos con foco en **fuentes españolas**: BOE, BORME,
contratación pública, indultos, INE, prensa y verificadores de noticias, además de las
fuentes habituales para nicks, emails, teléfonos, dominios e IPs.

Es la versión modernizada de **Dante's Gates Minimal Version** (`osint-suite-tools`), de
Jorge Coronado / Quantika14: <https://github.com/Quantika14/osint-suite-tools>. Conserva la
idea y el enfoque español del original y lo reescribe como un paquete de Python 3.11+
instalable, con plugins, casos guardados, evidencias con hash e informes.

> ⚖️ **Úsala solo con una finalidad legítima.** Cada investigación te pide la finalidad y
> la guarda en el caso. Lee [docs/USO_RESPONSABLE.md](docs/USO_RESPONSABLE.md) antes de
> empezar.

## Índice

- [Qué hace](#qué-hace)
- [Instalación](#instalación)
- [Primeros pasos](#primeros-pasos)
- [Comandos](#comandos)
- [Fuentes](#fuentes)
- [Índice local de fuentes oficiales](#índice-local-de-fuentes-oficiales)
- [Configuración](#configuración)
- [Casos, evidencias e informes](#casos-evidencias-e-informes)
- [Si venías del original](#si-venías-del-original)
- [Desarrollo](#desarrollo)
- [Documentación](#documentación)
- [Créditos y licencia](#créditos-y-licencia)

## Qué hace

- **Investiga ocho tipos de objetivo**: persona, empresa, nick, email, teléfono, dominio, IP
  y noticia (URL).
- **27 fuentes en forma de plugins.** Las que necesitan clave de API o tratan datos
  sensibles se desactivan solas y explican por qué.
- **Pivoting** (`--depth`): investiga también lo que va descubriendo (el nick derivado de
  un email, el dominio de una web…), sin repetir entidades y con un límite total.
- **Casos guardados** en SQLite (o MongoDB, opcional), con finalidad obligatoria. Se pueden
  listar, retomar, comparar, borrar y purgar por antigüedad.
- **Trazabilidad**: cada dato lleva su fuente, URL, fecha UTC, un nivel de confianza
  explicado y, cuando procede, una evidencia guardada con su SHA-256.
- **Informes** en HTML autocontenido (con grafo interactivo y cronología), JSON con esquema,
  CSV, Markdown, GEXF, GraphML y CSV para Maltego.
- **Ritmo y seguridad de red**: timeouts, reintentos, límite por dominio, caché, TLS siempre
  verificado y bloqueo de peticiones a redes privadas (anti-SSRF).

Qué **no** hace, a propósito: no inicia sesión en cuentas ajenas, no prueba contraseñas, no
sondea servidores SMTP, no hace spoofing y no descarga en masa perfiles de redes sociales.
Esas funciones del original no se han portado ([USO_RESPONSABLE](docs/USO_RESPONSABLE.md)).

## Instalación

Necesitas **Python 3.11, 3.12 o 3.13**. Funciona en Windows, Linux y macOS.

### Con uv (recomendado)

[uv](https://docs.astral.sh/uv/) instala las versiones exactas de `uv.lock`:

```powershell
git clone <url-de-este-repositorio> danterodia
```

```powershell
cd danterodia
```

```powershell
uv sync
```

```powershell
uv run danterodia --help
```

Para usar MongoDB en lugar de SQLite: `uv sync --extra mongo`.

### Con pip

```powershell
python -m venv .venv
```

```powershell
.venv\Scripts\Activate.ps1
```

```powershell
pip install -r requirements.txt -e .
```

En Linux o macOS, activa el entorno con `source .venv/bin/activate`.

### Con Docker

```powershell
docker compose build
```

```powershell
docker compose run --rm danterodia dominio example.com -f "Auditoría de mi propio dominio" -r html -o /trabajo/informes
```

La base de datos, la caché y los casos se guardan en `./datos`, y los informes en
`./informes`. MongoDB es un perfil opcional: `docker compose --profile mongo up -d mongo`
(instrucciones en [docker-compose.yml](docker-compose.yml)).

## Primeros pasos

```powershell
danterodia fuentes
```

Lista las fuentes instaladas, qué tipo de objetivo acepta cada una y si está disponible.

```powershell
danterodia persona "Miguel de Cervantes" -f "Prueba de la herramienta" -r html,json
```

Salida (solo con las fuentes sin conexión, `-s ine,identificadores,politicos_judicial`):

```text
Investigando Miguel de Cervantes (persona), profundidad 0…
  politicos_judicial · persona:miguel de cervantes → omitida (0) — fuente sensible: actívala con DANTERODIA_INCLUDE_SENSITIVE=true
  identificadores · persona:miguel de cervantes → sin_resultados (0)
  ine · persona:miguel de cervantes → ok (1)
──────────────────────── Caso 20261121-110406-465fb4 ────────────────────────
Objetivo: Miguel de Cervantes (persona)
Finalidad: Prueba de la herramienta
Estado: terminado · hallazgos: 1
│ ine │ persona │ Miguel de Cervantes │ frecuencia_nombre │ 0.90 │ Nombre poco común: unas 38 personas en España │
Informe html: informes\caso-20261121-110406-465fb4.html
Informe json: informes\caso-20261121-110406-465fb4.json
```

Más ejemplos:

```powershell
danterodia nick usuario_ejemplo -f "Verificar la identidad de un proveedor" --depth 1
```

```powershell
danterodia email contacto@ejemplo.es -f "Due diligence de un socio" -r md
```

```powershell
danterodia empresa B12345674 -f "Alta de proveedor" -s identificadores,borme,contratacion
```

```powershell
danterodia noticia "https://www.ejemplo.es/noticia" -f "Verificar una noticia" -r html
```

Si no pasas `-f/--finalidad`, la herramienta te la pide. Si la entrada no es interactiva
(scripts, CI), es obligatorio pasarla.

### Opciones de las investigaciones

| Opción | Qué hace |
|---|---|
| `-f`, `--finalidad` | Finalidad legítima de la investigación. Obligatoria; se guarda en el caso. |
| `-d`, `--depth N` | Niveles de pivoting (0-3). Por defecto, 0. |
| `-s`, `--fuentes a,b` | Usar solo esas fuentes. Por defecto, todas las activas. |
| `-r`, `--report html,json…` | Formatos de informe: `html`, `json`, `csv`, `md`, `gexf`, `graphml`, `maltego`. |
| `-o`, `--salida CARPETA` | Carpeta de los informes. Por defecto, `informes/`. |
| `--sensibles` | Incluir las fuentes con datos penales o de categoría especial. |
| `--sin-cache` | No usar la caché HTTP en esta ejecución. |

`persona` admite además `--nombre`, `--apellido1` y `--apellido2` para nombres compuestos,
que mejoran la estimación del INE.

## Comandos

| Comando | Qué hace |
|---|---|
| `persona`, `empresa`, `nick`, `email`, `telefono`, `dominio`, `ip`, `noticia` | Investigar un objetivo. |
| `fuentes` | Fuentes instaladas y su disponibilidad. |
| `config` | Configuración efectiva (las claves salen como «configurada» o «vacía»). |
| `caso listar` · `ver` · `retomar` · `comparar` · `borrar` · `purgar` | Gestionar los casos guardados. |
| `indice boe` · `borme` · `placsp` · `indultos` · `locales` · `estado` | Construir y consultar el índice local. |
| `actualizar-wmn` | Descargar la última versión del dataset de WhatsMyName. |
| `limpiar-cache` | Vaciar la caché HTTP. |
| `esquema` | JSON Schema de los casos exportados. |
| `version` | Versión instalada. |

Cada comando tiene su ayuda: `danterodia caso retomar --help`.

## Fuentes

**Red**: hace peticiones a Internet. **Índice**: consulta el índice local, que hay que
construir antes (ver la sección siguiente). **Sensible**: datos penales o de categoría
especial; está desactivada salvo con `--sensibles` o `DANTERODIA_INCLUDE_SENSITIVE=true`.

La columna **En vivo** indica si se probó contra el servicio real el 20/11/2026. Todas
tienen además tests sin conexión. Las que necesitan clave solo se han probado con
respuestas simuladas, porque no había clave disponible.

| Fuente | Acepta | Tipo | Clave | En vivo | Qué aporta |
|---|---|---|---|---|---|
| `ine` | persona | sin conexión | — | ✅ | Frecuencia del nombre y apellidos en España y personas estimadas que lo comparten. |
| `identificadores` | persona, empresa | sin conexión | — | ✅ | Valida CIF, DNI y NIE (dígito de control). |
| `telefono` | teléfono | sin conexión | — | ✅ | Validez, país, tipo de línea, operador original y zona horaria. |
| `email` | email | sin conexión | — | ✅ | Sintaxis, proveedor, dominio desechable y nick derivado. |
| `politicos_judicial` | persona | sin conexión, **sensible** | — | ✅ | Lista de Wikipedia (2019) de políticos implicados en casos judiciales. |
| `boe` | persona, empresa | índice | — | ✅ | Disposiciones del BOE que la mencionan en el título. |
| `borme` | persona, empresa | índice | — | ✅ | Cargos societarios: nombramientos, ceses, reelecciones… |
| `contratacion` | persona, empresa | índice | — | ✅ | Adjudicaciones de PLACSP y de los datasets locales (por CIF o nombre). |
| `indultos` | persona | índice, **sensible** | — | ✅ | Indultos 1996-2018 (Civio + BOE). |
| `documentos_locales` | persona, empresa | índice | — | ✅ | Texto de los PDF de `data/`. |
| `wikidata` | persona, empresa | red | — | ✅ | Identidad, fechas, cargos, web y cuentas oficiales. |
| `prensa` | persona, empresa | red | — | ✅ | Noticias en medios españoles, deduplicadas. |
| `buscadores` | persona, empresa, nick, email, teléfono, dominio | red | opcional | ✅ (DuckDuckGo) | Dorks editables en DuckDuckGo; Brave, Google CSE y Mojeek si hay clave. |
| `whatsmyname` | nick | red | — | ✅ | Cuentas en unas 690 webs, sin falsos positivos por un 200 genérico. |
| `dns` | email, dominio | red | — | ✅ | MX, SPF, DMARC, DKIM y si el dominio es suplantable. |
| `gravatar` | email | red | — | ✅ | Perfil público de Gravatar. |
| `pgp` | email | red | — | ✅ | Claves publicadas en keys.openpgp.org. |
| `hibp` | email | red | `HIBP_API_KEY` | ❌ | Brechas en Have I Been Pwned (API de pago). |
| `emailrep` | email | red | `EMAILREP_API_KEY` | ❌ | Reputación del email (clave gratuita). |
| `rdap` | dominio, IP | red | — | ✅ | Registro del dominio o de la IP (sucesor de WHOIS). |
| `crtsh` | dominio | red | — | ✅ | Subdominios en Certificate Transparency. |
| `wayback` | dominio, URL | red | — | ✅ | Primera y última captura en la Wayback Machine. |
| `tecnologias` | dominio, URL | red | — | ✅ | Tecnologías web detectadas (explicando por qué) y enlaces sociales. |
| `ipinfo` | IP | red | opcional | ✅ | Geolocalización aproximada, ASN y nombre inverso. |
| `shodan` | IP | red | `SHODAN_API_KEY` | ❌ | Puertos y servicios expuestos. |
| `censys` | IP | red | `CENSYS_API_ID` + `_SECRET` | ❌ | Servicios expuestos. |
| `verificar_noticia` | noticia | red | opcional | ✅ | Corroboración en otros medios, verificadores españoles y puntuación 0-100 explicada. |

Las claves se configuran con el prefijo `DANTERODIA_` (por ejemplo,
`DANTERODIA_HIBP_API_KEY`). Para añadir tus propias fuentes, mira
[docs/CREAR_PLUGIN.md](docs/CREAR_PLUGIN.md).

## Índice local de fuentes oficiales

La API del BOE no permite buscar por nombre, así que `boe`, `borme`, `contratacion`,
`indultos` y `documentos_locales` buscan en un **índice local** (SQLite FTS5) que construyes
tú. Es incremental: no vuelve a descargar los días que ya tiene. Mientras esté vacío, esas
fuentes salen como «omitidas» y te dicen qué comando ejecutar.

```powershell
danterodia indice boe --desde 2026-01-01 --hasta 2026-03-31
```

```powershell
danterodia indice borme --desde 2026-11-01 --provincia SEVILLA
```

```powershell
danterodia indice placsp --paginas 3
```

```powershell
danterodia indice indultos --limite 500
```

```powershell
danterodia indice locales --datos data
```

```powershell
danterodia indice estado
```

| Comando | Coste aproximado |
|---|---|
| `boe` | Una petición por día. Máximo un año por ejecución (puedes encadenar varias). |
| `borme` | Unas 50 peticiones por día (una por provincia). Con `--provincia` es mucho menos. |
| `placsp` | Unos 7 MB y 20-30 s por página (≈280 anuncios). |
| `indultos` | Unas 10 600 peticiones en total. Se puede interrumpir y reanudar. |
| `locales` | Sin red. Importa los contratos de Cádiz y Andalucía y los PDF de `data/`. Los edictos de TESTRA (DNI y matrículas de terceros) solo con `--incluir-testra`. |

## Configuración

La configuración se lee, de más a menos prioridad, de: las opciones de la CLI, las
variables de entorno `DANTERODIA_*`, el fichero `.env` del directorio actual y los valores
por defecto. Copia [.env.example](.env.example) como `.env` y rellena solo lo que necesites.
**No subas nunca el `.env` a git** (ya está en `.gitignore`).

| Variable | Por defecto | Qué hace |
|---|---|---|
| `DANTERODIA_DATA_DIR` | Carpeta de datos del usuario¹ | Base de datos, caché, índice y casos. |
| `DANTERODIA_DATABASE_URL` | SQLite en `DATA_DIR/danterodia.db` | URL de SQLAlchemy, o `mongodb://…` con el extra `mongo`. |
| `DANTERODIA_LOG_LEVEL` | `WARNING` | Nivel de la consola. El log del caso siempre guarda todo. |
| `DANTERODIA_INCLUDE_SENSITIVE` | `false` | Activar las fuentes sensibles. |
| `DANTERODIA_ENABLED_SOURCES` | todas | Si se indica, solo estas fuentes (separadas por comas). |
| `DANTERODIA_DISABLED_SOURCES` | ninguna | Fuentes desactivadas (separadas por comas). |
| `DANTERODIA_CASE_RETENTION_DAYS` | `90` | Antigüedad a partir de la cual `caso purgar` borra. |
| `DANTERODIA_MAX_PIVOT_ENTITIES` | `50` | Máximo de entidades por caso al pivotar. |
| `DANTERODIA_SOURCE_TIMEOUT` | `120` | Segundos máximos por fuente. |
| `DANTERODIA_TIMEOUT` | `20` | Timeout de cada petición HTTP. |
| `DANTERODIA_MAX_RETRIES` | `3` | Reintentos con espera exponencial. |
| `DANTERODIA_PER_DOMAIN_INTERVAL` | `1.0` | Segundos mínimos entre dos peticiones al mismo dominio. |
| `DANTERODIA_CACHE_ENABLED` / `_CACHE_TTL` | `true` / `86400` | Caché HTTP en disco. |
| `DANTERODIA_PROXY` | — | Proxy HTTP o SOCKS para todo el tráfico. |
| `DANTERODIA_TOR_PROXY` | `socks5://127.0.0.1:9050` | Solo se usa para direcciones `.onion`. |
| `DANTERODIA_ALLOW_PRIVATE_NETWORKS` | `false` | Permitir peticiones a IPs privadas o locales. |

¹ En Windows, `%LOCALAPPDATA%\danterodia`; en Linux, `~/.local/share/danterodia`.

Claves de API (todas opcionales): `HIBP_API_KEY`, `EMAILREP_API_KEY`, `BRAVE_API_KEY`,
`GOOGLE_CSE_KEY` + `GOOGLE_CSE_CX`, `MOJEEK_API_KEY`, `FACTCHECK_API_KEY`,
`SHODAN_API_KEY`, `CENSYS_API_ID` + `CENSYS_API_SECRET` e `IPINFO_TOKEN`, todas con el
prefijo `DANTERODIA_`. `YOUTUBE_API_KEY`, `INTELX_API_KEY` y `LIBREBOR_API_KEY` están
reservadas, pero de momento ninguna fuente las usa.

Los dorks de `buscadores` son plantillas editables: copia
[src/danterodia/data/dorks.toml](src/danterodia/data/dorks.toml) a `DATA_DIR/dorks.toml` y
modifica las secciones que quieras.

## Casos, evidencias e informes

Cada investigación crea un **caso** con un ID como `20261121-110406-465fb4`. El caso guarda
el objetivo, la finalidad, las fuentes usadas, cada ejecución (con su estado: `ok`,
`sin_resultados`, `omitida`, `error` o `timeout`) y todos los hallazgos.

```powershell
danterodia caso listar
```

```powershell
danterodia caso ver 20261121-110406-465fb4 -r html
```

```powershell
danterodia caso retomar 20261121-110406-465fb4
```

```powershell
danterodia caso comparar 20261121-110406-465fb4 20261121-110423-c3d731
```

```powershell
danterodia caso borrar 20261121-110406-465fb4
```

```powershell
danterodia caso purgar
```

`retomar` repite solo lo que falló o no llegó a ejecutarse. `borrar` y `purgar` eliminan
también las evidencias y el log del caso, y no se pueden deshacer.

En `DATA_DIR/casos/<id>/` quedan:

- `evidencias/<sha256>.<ext>`: copia en bruto de lo descargado, con un `.json` al lado con
  la URL, la fecha UTC y el tipo de contenido. Recalculando el hash se comprueba que no ha
  cambiado.
- `caso.log.jsonl`: log estructurado con cada fuente, su duración y sus errores.

**Confianza.** Cada hallazgo tiene una confianza entre 0 y 1. Si varias fuentes
independientes encuentran lo mismo, se combina con `1 − Π(1 − c)`. Si un hallazgo coincide
solo por el nombre, la confianza baja según cuánta gente comparte ese nombre en España
(INE): con «María García López» vale mucho menos que con un nombre único. El valor original
y el motivo del ajuste quedan en el hallazgo.

**Formatos de informe** (`-r`):

| Formato | Para qué |
|---|---|
| `html` | Informe autocontenido (funciona sin conexión): resumen, grafo interactivo, cronología, hallazgos filtrables y anexo de evidencias. |
| `json` | Datos completos; el esquema se obtiene con `danterodia esquema`. |
| `csv` | Hoja de cálculo, protegida contra inyección de fórmulas. |
| `md` | Para notas (Obsidian, Logseq…). |
| `gexf`, `graphml` | Grafo para Gephi y similares. |
| `maltego` | CSV importable en Maltego. |

## Si venías del original

| Antes | Ahora |
|---|---|
| `python BuscadorPersonas.py` | `danterodia persona "Nombre Apellido"` |
| `python BuscadorNick.py` | `danterodia nick usuario` |
| `python BuscadorEmails.py` | `danterodia email correo@ejemplo.es` |
| `python BuscadorTelefonos.py` | `danterodia telefono +34600000000` |
| `python BuscadorNoticiasFalsas.py` | `danterodia noticia URL` |
| MongoDB obligatorio | SQLite sin instalar nada; MongoDB opcional. |
| `requiriments.txt` | `uv.lock` / `requirements.txt` |

Los `Buscador*.py` de la raíz siguen funcionando: preguntan el objetivo y la finalidad y
llaman a la nueva herramienta. El código original está en [legacy/](legacy/) solo como
referencia, y las diferencias, explicadas, en [CHANGELOG.md](CHANGELOG.md).

## Desarrollo

```powershell
uv sync --extra mongo
```

```powershell
uv run pytest
```

```powershell
uv run ruff check .
```

```powershell
uv run mypy
```

```powershell
uv run pre-commit install
```

Los tests no usan Internet: las peticiones se simulan con `respx` y el DNS con un
resolvedor falso. La CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) pasa ruff,
mypy estricto y los tests en Linux y Windows con Python 3.11, 3.12 y 3.13 (cobertura mínima
del 70 %; ahora está en el 95 %), además de tests contra MongoDB real, `pip-audit` y la
construcción de la imagen Docker.

## Documentación

- [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md): cómo está organizado el código y qué pasa
  en una investigación.
- [docs/CREAR_PLUGIN.md](docs/CREAR_PLUGIN.md): cómo escribir y probar una fuente nueva.
- [docs/USO_RESPONSABLE.md](docs/USO_RESPONSABLE.md): marco legal y buenas prácticas.
- [docs/AUDITORIA.md](docs/AUDITORIA.md): auditoría del código original.
- [docs/PLAN.md](docs/PLAN.md): plan de modernización y decisiones tomadas.
- [CHANGELOG.md](CHANGELOG.md): qué cambió y por qué.

## Créditos y licencia

- Idea y herramienta original: **Jorge Coronado (Jorge Websec) / QuantiKa14 Servicios
  Integrales S.L.**, Dante's Gates Minimal Version.
- Datos de terceros (INE, Civio, Wikipedia, WhatsMyName, disposable-email-domains) y
  librerías incluidas: ver [NOTICE](NOTICE).

Licencia **GPL-3.0-only** ([LICENSE](LICENSE)), la misma que declara el proyecto original.
Se conservan los avisos de copyright del autor; la discrepancia con las cabeceras BSD del
código original se explica en [docs/AUDITORIA.md §8](docs/AUDITORIA.md).
