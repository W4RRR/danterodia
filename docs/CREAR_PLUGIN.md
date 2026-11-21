# Crear una fuente (plugin)

Una fuente es una clase que recibe una entidad (un dominio, un email, una persona…) y
devuelve una lista de hallazgos. No necesita saber nada de casos, base de datos, pivoting ni
informes: de eso se encarga el orquestador ([ARQUITECTURA.md](ARQUITECTURA.md)).

Las fuentes se descubren por *entry points* del grupo `danterodia.sources`. Puedes añadir
una de dos formas:

- **Dentro del repositorio**, en `src/danterodia/sources/`, si va a formar parte de la
  herramienta.
- **En un paquete aparte**, sin tocar este código, si es para ti o para tu organización.

El ejemplo de esta guía, `securitytxt`, se ha probado con tests sin conexión y contra un
dominio real.

## 1. La interfaz

```python
class Source(ABC):
    name: ClassVar[str]                          # identificador corto y estable
    description: ClassVar[str] = ""              # una línea, para `danterodia fuentes`
    input_types: ClassVar[frozenset[EntityType]] # tipos de entidad que acepta
    requires_api_key: ClassVar[str | None] = None  # campo de Settings con la clave
    sensitive: ClassVar[bool] = False            # datos penales o de categoría especial
    offline: ClassVar[bool] = False              # no hace peticiones de red
    timeout: ClassVar[float | None] = None       # segundos; None = DANTERODIA_SOURCE_TIMEOUT

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]: ...
```

`ctx` (`SourceContext`) te da todo lo que necesitas:

| Atributo | Para qué |
|---|---|
| `ctx.http` | Cliente HTTP común. **Úsalo siempre** en vez de crear el tuyo: aplica timeouts, reintentos, ritmo por dominio, caché y anti-SSRF. |
| `ctx.dns` | Resolvedor DNS asíncrono (`query(nombre, tipo)`, `reverse(ip)`). |
| `ctx.settings` | Configuración. Las claves se leen con `ctx.settings.api_key("campo")`. |
| `ctx.evidence` | Guardar evidencias: `ctx.evidence.save(ctx.case_id, url, contenido, tipo)`. |
| `ctx.index()` | Índice local (BOE, BORME…). Lanza `SourceSkipped` si no existe. |
| `ctx.log` | Logger que escribe en el log del caso. |
| `ctx.case_id`, `ctx.depth` | Caso actual y nivel de pivoting. |

## 2. Un ejemplo completo

`securitytxt` lee `https://<dominio>/.well-known/security.txt` (RFC 9116) y devuelve los
contactos de seguridad que publica el propio dominio.

```python
"""Fuente de ejemplo: contactos de seguridad publicados en ``/.well-known/security.txt``."""

from __future__ import annotations

from danterodia.models import Entity, EntityType, Finding
from danterodia.sources.base import Source, SourceContext


class SecurityTxt(Source):
    name = "securitytxt"
    description = "Contactos de seguridad del dominio (security.txt, RFC 9116)"
    input_types = frozenset({EntityType.DOMINIO})
    timeout = 30  # opcional: por defecto se usa DANTERODIA_SOURCE_TIMEOUT

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        url = f"https://{target.value}/.well-known/security.txt"
        r = await ctx.http.get(url)
        if r.status == 404 or not r.ok or "text/plain" not in (r.content_type or ""):
            return []  # no publicado: «sin_resultados», no es un error

        evidencia = ctx.evidence.save(ctx.case_id, r.url, r.content, r.content_type)
        campos: dict[str, list[str]] = {}
        for linea in r.text.splitlines():
            clave, sep, valor = linea.partition(":")
            if sep and not linea.startswith("#"):
                campos.setdefault(clave.strip().lower(), []).append(valor.strip())

        hallazgos = [
            self.finding(
                target,
                target,
                relation="publica_security_txt",
                title=f"security.txt con {len(campos.get('contact', []))} contacto(s)",
                confidence=0.95,
                source_url=r.url,
                evidence=[evidencia],
                data={"campos": campos},
            )
        ]
        for contacto in campos.get("contact", []):
            if contacto.startswith("mailto:"):
                email = contacto.removeprefix("mailto:")
                hallazgos.append(
                    self.finding(
                        target,
                        Entity(type=EntityType.EMAIL, value=email),
                        relation="contacto_seguridad",
                        title=f"Contacto de seguridad: {email}",
                        confidence=0.9,
                        source_url=r.url,
                        evidence=[evidencia],
                    )
                )
        return hallazgos
```

Qué conviene fijarse:

- **`self.finding(target, entidad, …)`** crea el hallazgo con `source` y `related_to` ya
  rellenos. El primer argumento es lo investigado y el segundo lo descubierto. Si es la
  misma entidad (un dato *sobre* el objetivo), pasa `target` dos veces.
- **Un 404 no es un error.** `ctx.http.get` no lanza excepción por códigos 4xx o 5xx:
  devuelve la respuesta y tú decides. Devolver `[]` deja la fuente como `sin_resultados`.
- **Entidades nuevas = pivoting.** El email del contacto es una entidad de tipo `email`. Si
  el usuario pide `--depth 1`, las fuentes de email lo investigarán en el nivel siguiente
  (si la confianza es de al menos 0,5).
- **Evidencia.** Guarda la respuesta en bruto cuando sea la prueba del hallazgo. El fichero
  se nombra por su SHA-256.

## 3. Registrarla

### Dentro del repositorio

1. Pon la clase en un módulo de `src/danterodia/sources/` (o en uno existente, si encaja).
2. Añade el *entry point* en `pyproject.toml`:

   ```toml
   [project.entry-points."danterodia.sources"]
   securitytxt = "danterodia.sources.securitytxt:SecurityTxt"
   ```

3. Reinstala el paquete para que se registre el *entry point* (no basta con guardar el
   fichero):

   ```powershell
   uv sync
   ```

4. Comprueba que aparece:

   ```powershell
   uv run danterodia fuentes
   ```

### En un paquete aparte

```text
danterodia-securitytxt/
├── pyproject.toml
├── src/danterodia_securitytxt/__init__.py
└── tests/test_securitytxt.py
```

```toml
[project]
name = "danterodia-securitytxt"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["danterodia"]

[project.entry-points."danterodia.sources"]
securitytxt = "danterodia_securitytxt:SecurityTxt"

[build-system]
requires = ["hatchling>=1.26"]
build-backend = "hatchling.build"
```

Instálalo **en el mismo entorno** que danterodia. Como danterodia todavía no está publicado
en PyPI, instala primero el núcleo desde tu copia local y después el plugin:

```powershell
pip install -e ruta\a\danterodia -e ruta\a\danterodia-securitytxt
```

```powershell
danterodia dominio ejemplo.es -s securitytxt -f "Auditoría de mi propio dominio"
```

Si el *entry point* no se puede cargar (error de importación, nombre duplicado…),
`danterodia` lo avisa en el log y sigue con el resto de fuentes.

## 4. Probarla sin Internet

Los tests no deben salir a Internet. Simula las respuestas con
[respx](https://lundberg.github.io/respx/) y sustituye el DNS por un resolvedor falso: el
anti-SSRF necesita que el dominio resuelva a una IP pública.

```python
import logging
from pathlib import Path

import httpx
import respx

from danterodia.config import Settings
from danterodia.evidence import EvidenceStore
from danterodia.http import HttpClient
from danterodia.models import Entity, EntityType
from danterodia.sources.base import SourceContext
from danterodia_securitytxt import SecurityTxt

DOMINIO = Entity(type=EntityType.DOMINIO, value="ejemplo.es")


async def ip_publica(host: str) -> list[str]:
    return ["93.184.215.14"]  # sin DNS real; el anti-SSRF exige una IP pública


async def contexto(tmp_path: Path) -> tuple[HttpClient, SourceContext]:
    settings = Settings(data_dir=tmp_path, cache_enabled=False, per_domain_interval=0)
    http = await HttpClient(settings, resolver=ip_publica).__aenter__()
    ctx = SourceContext(
        http=http,
        settings=settings,
        evidence=EvidenceStore(settings.cases_dir),
        case_id="caso-test",
        log=logging.getLogger("test"),
    )
    return http, ctx


@respx.mock
async def test_extrae_contactos(tmp_path: Path) -> None:
    respx.get("https://ejemplo.es/.well-known/security.txt").mock(
        return_value=httpx.Response(
            200,
            text="# comentario\nContact: mailto:seguridad@ejemplo.es\nExpires: 2027-01-01T00:00:00Z\n",
            headers={"content-type": "text/plain; charset=utf-8"},
        )
    )
    http, ctx = await contexto(tmp_path)
    try:
        hallazgos = await SecurityTxt().run(DOMINIO, ctx)
    finally:
        await http.aclose()

    emails = [h.entity.value for h in hallazgos if h.entity.type == EntityType.EMAIL]
    assert emails == ["seguridad@ejemplo.es"]
    assert hallazgos[0].evidence[0].sha256  # la evidencia queda guardada con su hash


@respx.mock
async def test_sin_security_txt(tmp_path: Path) -> None:
    respx.get("https://ejemplo.es/.well-known/security.txt").mock(
        return_value=httpx.Response(404)
    )
    http, ctx = await contexto(tmp_path)
    try:
        assert await SecurityTxt().run(DOMINIO, ctx) == []
    finally:
        await http.aclose()
```

Estos tests son asíncronos: necesitas `pytest-asyncio` con `asyncio_mode = "auto"`.

**Dentro del repositorio es más corto**: `tests/conftest.py` ya aísla el entorno, falsea el
DNS y ofrece la *fixture* `ctx` lista para usar, así que basta con
`async def test_x(ctx: SourceContext) -> None:`. Mira `tests/test_sources_red.py`. Si
grabas respuestas reales, recórtalas, quita los datos personales y guárdalas en
`tests/fixtures/`.

## 5. Casos habituales

### Necesita clave de API

```python
class MiServicio(Source):
    name = "miservicio"
    input_types = frozenset({EntityType.IP})
    requires_api_key = "miservicio_api_key"

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        clave = ctx.settings.api_key("miservicio_api_key")
        r = await ctx.http.get(
            f"https://api.miservicio.example/ip/{target.value}",
            headers={"Authorization": f"Bearer {clave}"},
            cache_ttl=0,  # opcional: no cachear respuestas con datos de pago
        )
        ...
```

`requires_api_key` es el nombre de un campo de `Settings`. Si la fuente va dentro del
repositorio, añade el campo en `config.py` como `SecretStr | None = None` y la línea
correspondiente en `.env.example`. Mientras la clave falte, la fuente sale como «omitida»
con el motivo `falta la clave DANTERODIA_MISERVICIO_API_KEY`, sin llegar a ejecutarse.

Desde un paquete externo no puedes añadir campos a `Settings`. Lee tu clave de una variable
de entorno propia y, si falta, lanza `SourceSkipped("falta la clave MI_VARIABLE")`.

### No se puede ejecutar ahora

```python
from danterodia.sources.base import SourceSkipped

raise SourceSkipped("el servicio exige registro previo: ver README")
```

Queda como «omitida», con el motivo visible para el usuario, en lugar de como error.

### Usa el índice local

```python
store = ctx.index()  # lanza SourceSkipped si no hay índice
```

Mira `sources/oficiales.py` para ver cómo se consulta y cómo se avisa de que la tabla está
vacía.

### Trata datos sensibles

Si la fuente devuelve datos penales o de categoría especial (salud, ideología, religión,
orientación sexual… arts. 9 y 10 RGPD), márcala con `sensitive = True`. Así queda
desactivada salvo que el usuario pase `--sensibles`. Marca también las entidades con
`Entity(..., sensitive=True)`: nunca se pivota sobre ellas.

### Coincidencia solo por nombre

Si el hallazgo se basa únicamente en que coincide el nombre de una persona, añade
`data={"coincidencia": "nombre"}`. La correlación rebajará la confianza según lo común que
sea ese nombre en España (INE) y lo dejará explicado en el hallazgo.

## 6. Lista de comprobación

- [ ] `name` corto, en minúsculas, sin espacios y que no choque con ninguna fuente existente.
- [ ] Todas las peticiones pasan por `ctx.http` (nada de `requests` ni de clientes propios).
- [ ] Respeta las condiciones de uso del servicio. Prefiere APIs oficiales y datos
      abiertos al scraping.
- [ ] Nada activo contra terceros: ni inicios de sesión, ni reseteos de contraseña, ni
      sondeo SMTP, ni pruebas de credenciales ([USO_RESPONSABLE.md](USO_RESPONSABLE.md)).
- [ ] Recoge solo lo necesario (minimización) y marca como sensible lo que lo sea.
- [ ] Cada hallazgo tiene un `title` que se entiende solo, una `confidence` razonada y
      `source_url`.
- [ ] Guarda evidencia cuando la respuesta es la prueba del hallazgo.
- [ ] Los fallos esperables (404, «no encontrado») devuelven `[]`; lo que impide ejecutar
      lanza `SourceSkipped`.
- [ ] Tests sin Internet, con el caso positivo y el caso «no hay nada».
- [ ] Dentro del repositorio: `uv run ruff check .`, `uv run mypy` y `uv run pytest` en
      verde, y la fuente añadida a la tabla del README.
