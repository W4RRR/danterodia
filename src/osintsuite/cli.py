"""Interfaz de línea de comandos ``osint``.

Ejemplos::

    osint persona "Nombre Apellido1 Apellido2" -f "Due diligence de proveedor"
    osint nick usuario --depth 1 -r json,md
    osint caso listar
    osint fuentes
"""

import asyncio
import contextlib
import sys
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from osintsuite import __version__
from osintsuite.config import Settings
from osintsuite.http import DiskCache, HttpClient
from osintsuite.indices import IndexStore
from osintsuite.investigations import Investigation, InvestigationOptions
from osintsuite.investigations.orchestrator import delete_case_files, purge_expired
from osintsuite.logs import setup_logging
from osintsuite.models import Case, Entity, EntityType, SourceRun, SourceStatus
from osintsuite.reports import EXPORTERS, export, json_schema
from osintsuite.sources import SourceRegistry
from osintsuite.storage import Repository, diff_cases, open_repository


def _forzar_utf8() -> None:
    """En Windows, si la salida va a un fichero o tubería se usa cp1252 y caracteres como
    «·» o «→» provocan UnicodeEncodeError. Se fuerza UTF-8 (con sustitución si falla)."""
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if encoding != "utf8" and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    # La entrada por tubería (p. ej. desde PowerShell 7 o Git Bash) llega en UTF-8.
    stdin = sys.stdin
    if (
        stdin is not None
        and not stdin.isatty()
        and (getattr(stdin, "encoding", None) or "").lower().replace("-", "") != "utf8"
        and hasattr(stdin, "reconfigure")
    ):
        stdin.reconfigure(encoding="utf-8", errors="replace")


_forzar_utf8()

console = Console()
err = Console(stderr=True)

app = typer.Typer(
    name="osint",
    help="Herramienta OSINT modular con foco en fuentes españolas. Basada en Dante's Gates "
    "Minimal Version (Quantika14). Úsala solo con una finalidad legítima: ver "
    "docs/USO_RESPONSABLE.md.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
caso_app = typer.Typer(help="Gestionar casos guardados.", no_args_is_help=True)
app.add_typer(caso_app, name="caso")

ESTILO_ESTADO = {
    SourceStatus.OK: "green",
    SourceStatus.EMPTY: "dim",
    SourceStatus.SKIPPED: "yellow",
    SourceStatus.FAILED: "red",
    SourceStatus.TIMEOUT: "red",
}


# ------------------------------------------------------------------ utilidades


@dataclass
class Runtime:
    settings: Settings
    repo: Repository
    registry: SourceRegistry
    indices: IndexStore


@contextlib.contextmanager
def runtime(**overrides: object) -> Iterator[Runtime]:
    settings = Settings(**{k: v for k, v in overrides.items() if v is not None})  # type: ignore[arg-type]
    setup_logging(settings.log_level)
    settings.ensure_dirs()
    repo = open_repository(settings.db_url)
    indices = IndexStore(settings.data_dir / "indices.sqlite")
    try:
        yield Runtime(settings, repo, SourceRegistry.discover(), indices)
    finally:
        indices.close()
        repo.close()


def _formatos(valor: str | None) -> list[str]:
    if not valor:
        return []
    fmts = [f.strip().lower() for f in valor.split(",") if f.strip()]
    malos = [f for f in fmts if f not in EXPORTERS]
    if malos:
        raise typer.BadParameter(
            f"Formato no válido: {', '.join(malos)}. Opciones: {', '.join(EXPORTERS)}"
        )
    return fmts


def _progreso(run: SourceRun) -> None:
    estilo = ESTILO_ESTADO.get(run.status, "white")
    detalle = f" — {run.error}" if run.error else ""
    nivel = f"[nivel {run.depth}] " if run.depth else ""
    console.print(
        f"  {nivel}[bold]{run.source}[/] · {run.target} → [{estilo}]{run.status.value}[/]"
        f" ({run.findings}){detalle}",
        highlight=False,
    )


def mostrar_caso(case: Case) -> None:
    console.rule(f"Caso {case.id}")
    console.print(f"Objetivo: [bold]{case.target.value}[/] ({case.target.type.value})")
    console.print(f"Finalidad: {case.purpose}", highlight=False)
    console.print(f"Estado: {case.status.value} · hallazgos: {len(case.findings)}")
    if not case.runs:
        console.print(
            f"[yellow]Ninguna fuente instalada acepta el tipo «{case.target.type.value}» "
            "(revisa `osint fuentes`).[/]"
        )
    if not case.findings:
        console.print("[dim]Sin hallazgos.[/]")
        return
    t = Table(show_lines=False)
    for col in ("Fuente", "Tipo", "Valor", "Relación", "Conf.", "Título"):
        t.add_column(col)
    for f in sorted(case.findings, key=lambda x: x.confidence, reverse=True):
        t.add_row(
            f.source,
            f.entity.type.value,
            f.entity.value,
            f.relation,
            f"{f.confidence:.2f}",
            f.title,
        )
    console.print(t)


def _exportar(case: Case, formatos: list[str], salida: Path) -> None:
    for fmt in formatos:
        path = export(case, fmt, salida)
        console.print(f"Informe {fmt}: [link=file:///{path.resolve().as_posix()}]{path}[/link]")


def _pedir_finalidad(finalidad: str | None) -> str:
    if finalidad:
        return finalidad
    if not sys.stdin.isatty():
        raise typer.BadParameter("Indica la finalidad con --finalidad (obligatoria).")
    return str(
        typer.prompt("Finalidad de la investigación (queda registrada en el caso; art. 5.1.b RGPD)")
    )


async def _investigar(
    rt: Runtime, entity: Entity, finalidad: str, opts: InvestigationOptions
) -> Case:
    async with HttpClient(rt.settings) as http:
        inv = Investigation(rt.settings, rt.repo, rt.registry, http, indices=rt.indices)
        return await inv.run(entity, finalidad, opts)


def investigar(
    tipo: EntityType,
    valor: str,
    finalidad: str | None,
    depth: int,
    fuentes: str | None,
    report: str | None,
    salida: Path,
    sensibles: bool,
    sin_cache: bool,
    attributes: dict[str, object] | None = None,
) -> Case:
    formatos = _formatos(report)
    finalidad = _pedir_finalidad(finalidad)
    lista = [s.strip() for s in fuentes.split(",") if s.strip()] if fuentes else None
    with runtime(
        include_sensitive=sensibles or None, cache_enabled=False if sin_cache else None
    ) as rt:
        entity = Entity(type=tipo, value=valor, attributes=attributes or {})
        console.print(f"Investigando [bold]{entity.value}[/] ({tipo.value}), profundidad {depth}…")
        try:
            case = asyncio.run(
                _investigar(
                    rt,
                    entity,
                    finalidad,
                    InvestigationOptions(depth=depth, sources=lista, on_progress=_progreso),
                )
            )
        except KeyError as exc:
            raise typer.BadParameter(str(exc.args[0])) from exc
        mostrar_caso(case)
        _exportar(case, formatos, salida)
        return case


# --------------------------------------------------- opciones comunes (Annotated)

Finalidad = Annotated[
    str | None,
    typer.Option(
        "--finalidad", "-f", help="Finalidad legítima de la investigación (se guarda en el caso)."
    ),
]
Depth = Annotated[
    int,
    typer.Option("--depth", "-d", min=0, max=3, help="Niveles de pivoting sobre lo descubierto."),
]
Fuentes = Annotated[
    str | None,
    typer.Option(
        "--fuentes",
        "-s",
        help="Lista de fuentes separadas por comas (por defecto, todas las activas).",
    ),
]
Report = Annotated[
    str | None,
    typer.Option(
        "--report", "-r", help=f"Formatos de informe separados por comas: {', '.join(EXPORTERS)}."
    ),
]
Salida = Annotated[Path, typer.Option("--salida", "-o", help="Carpeta para los informes.")]
Sensibles = Annotated[
    bool,
    typer.Option("--sensibles", help="Incluir fuentes con datos penales o de categoría especial."),
]
SinCache = Annotated[bool, typer.Option("--sin-cache", help="No usar la caché HTTP.")]

SALIDA_DEF = Path("informes")


def _comando(tipo: EntityType, ayuda: str, nombre: str) -> None:
    def cmd(
        valor: Annotated[str, typer.Argument(help=ayuda)],
        finalidad: Finalidad = None,
        depth: Depth = 0,
        fuentes: Fuentes = None,
        report: Report = None,
        salida: Salida = SALIDA_DEF,
        sensibles: Sensibles = False,
        sin_cache: SinCache = False,
    ) -> None:
        investigar(tipo, valor, finalidad, depth, fuentes, report, salida, sensibles, sin_cache)

    cmd.__doc__ = f"Investigar {ayuda.lower()}."
    app.command(nombre)(cmd)


@app.command("persona")
def persona(
    nombre: Annotated[str, typer.Argument(help='Nombre y apellidos, p. ej. "Ana García López".')],
    finalidad: Finalidad = None,
    depth: Depth = 0,
    fuentes: Fuentes = None,
    report: Report = None,
    salida: Salida = SALIDA_DEF,
    sensibles: Sensibles = False,
    sin_cache: SinCache = False,
    nombre_pila: Annotated[
        str | None, typer.Option("--nombre", help="Nombre de pila (si es compuesto).")
    ] = None,
    apellido1: Annotated[str | None, typer.Option(help="Primer apellido.")] = None,
    apellido2: Annotated[str | None, typer.Option(help="Segundo apellido.")] = None,
) -> None:
    """Investigar a una persona por nombre y apellidos."""
    attrs: dict[str, object] = {}
    if nombre_pila:
        attrs = {"nombre": nombre_pila, "apellido1": apellido1, "apellido2": apellido2}
    investigar(
        EntityType.PERSONA,
        nombre,
        finalidad,
        depth,
        fuentes,
        report,
        salida,
        sensibles,
        sin_cache,
        attrs,
    )


_comando(EntityType.USERNAME, "Nombre de usuario (nick)", "nick")
_comando(EntityType.EMAIL, "Dirección de email", "email")
_comando(EntityType.TELEFONO, "Número de teléfono (con prefijo, p. ej. +34…)", "telefono")
_comando(EntityType.DOMINIO, "Dominio, p. ej. ejemplo.es", "dominio")
_comando(EntityType.IP, "Dirección IP", "ip")
_comando(EntityType.EMPRESA, "Empresa (razón social o CIF)", "empresa")
_comando(EntityType.URL, "URL de una noticia para verificar", "noticia")


# --------------------------------------------------------------- informativos


@app.command("fuentes")
def fuentes_cmd(sensibles: Sensibles = False) -> None:
    """Listar las fuentes instaladas y si están disponibles."""
    with runtime(include_sensitive=sensibles or None) as rt:
        t = Table(title="Fuentes")
        for col in ("Nombre", "Entrada", "Clave", "Estado", "Descripción"):
            t.add_column(col)
        for s in rt.registry.all():
            ok, motivo = s.availability(rt.settings)
            if s.name in rt.settings.disabled_sources:
                ok, motivo = False, "desactivada en la configuración"
            t.add_row(
                s.name,
                ", ".join(sorted(x.value for x in s.input_types)),
                s.requires_api_key or "—",
                f"[green]{motivo}[/]" if ok else f"[yellow]{motivo}[/]",
                s.description,
            )
        console.print(t)


@app.command("config")
def config_cmd() -> None:
    """Mostrar la configuración efectiva (sin revelar claves)."""
    with runtime() as rt:
        t = Table(title="Configuración")
        t.add_column("Opción")
        t.add_column("Valor")
        for k, v in rt.settings.public_dump().items():
            t.add_row(k, str(v))
        console.print(t)


@app.command("esquema")
def esquema_cmd() -> None:
    """Imprimir el JSON Schema de los casos exportados."""
    typer.echo(json_schema())


@app.command("version")
def version_cmd() -> None:
    """Mostrar la versión."""
    typer.echo(f"osintsuite {__version__}")


# ----------------------------------------------------------------- índices

indice_app = typer.Typer(
    help="Construir el índice local de fuentes oficiales (BOE, BORME, contratación…).",
    no_args_is_help=True,
)
app.add_typer(indice_app, name="indice")

Desde = Annotated[
    str | None, typer.Option("--desde", help="Fecha inicial AAAA-MM-DD (por defecto, hace 7 días).")
]
Hasta = Annotated[
    str | None, typer.Option("--hasta", help="Fecha final AAAA-MM-DD (por defecto, hoy).")
]


def _rango(desde: str | None, hasta: str | None) -> tuple[date, date]:
    try:
        h = date.fromisoformat(hasta) if hasta else datetime.now(UTC).date()
        d = date.fromisoformat(desde) if desde else h - timedelta(days=7)
    except ValueError as exc:
        raise typer.BadParameter(f"Fecha no válida: {exc}") from exc
    if d > h:
        raise typer.BadParameter("--desde es posterior a --hasta")
    if (h - d).days > 366:
        raise typer.BadParameter("Rango máximo: un año por ejecución (puedes encadenar varias).")
    return d, h


def _ejecutar_indice(fn: Callable[[HttpClient, IndexStore], Awaitable[int]]) -> None:
    async def _go(rt: Runtime) -> int:
        async with HttpClient(rt.settings) as http:
            return await fn(http, rt.indices)

    with runtime() as rt:
        n = asyncio.run(_go(rt))
        console.print(f"[green]Hecho:[/] {n} registros nuevos en {rt.indices.path}")


def _p(msg: str) -> None:
    console.print(f"  {msg}", highlight=False)


@indice_app.command("boe")
def indice_boe(desde: Desde = None, hasta: Hasta = None) -> None:
    """Sumarios del BOE (API de datos abiertos): títulos de todas las disposiciones."""
    from osintsuite.indices.builders import indexar_boe

    d, h = _rango(desde, hasta)
    _ejecutar_indice(lambda http, st: indexar_boe(http, st, d, h, _p))


@indice_app.command("borme")
def indice_borme(
    desde: Desde = None,
    hasta: Hasta = None,
    provincia: Annotated[
        list[str] | None,
        typer.Option("--provincia", "-p", help="Limitar a provincias (repetible), p. ej. SEVILLA."),
    ] = None,
) -> None:
    """BORME sección A: nombramientos, ceses y otros actos con cargos (unas 50 peticiones/día)."""
    from osintsuite.indices.builders import indexar_borme

    d, h = _rango(desde, hasta)
    _ejecutar_indice(lambda http, st: indexar_borme(http, st, d, h, provincia, _p))


@indice_app.command("placsp")
def indice_placsp(
    paginas: Annotated[
        int, typer.Option(min=1, max=200, help="Páginas del feed (≈280 anuncios cada una).")
    ] = 3,
) -> None:
    """Adjudicaciones recientes de la Plataforma de Contratación del Sector Público."""
    from osintsuite.indices.builders import indexar_placsp

    _ejecutar_indice(lambda http, st: indexar_placsp(http, st, paginas, _p))


@indice_app.command("indultos")
def indice_indultos(
    limite: Annotated[
        int | None, typer.Option(help="Máximo de indultos a descargar en esta ejecución.")
    ] = None,
) -> None:
    """Indultos 1996-2018 (Civio) con el título del BOE. Reanudable: ~10 600 peticiones en total."""
    from osintsuite.indices.builders import indexar_indultos

    _ejecutar_indice(lambda http, st: indexar_indultos(http, st, limite, _p))


@indice_app.command("locales")
def indice_locales(
    datos: Annotated[Path, typer.Option(help="Carpeta data/ del repositorio original.")] = Path(
        "data"
    ),
    incluir_testra: Annotated[
        bool,
        typer.Option(
            "--incluir-testra",
            help="Importar también los edictos de TESTRA (DNI y matrículas de terceros).",
        ),
    ] = False,
) -> None:
    """Importa los datasets locales (contratos de Cádiz/Andalucía y PDFs) con pypdf."""
    from osintsuite.indices.builders import importar_locales

    if not datos.is_dir():
        raise typer.BadParameter(f"No existe la carpeta {datos}")
    with runtime() as rt:
        resumen = importar_locales(rt.indices, datos, incluir_testra, _p)
        console.print(f"[green]Hecho:[/] {resumen}")


@indice_app.command("estado")
def indice_estado() -> None:
    """Mostrar qué hay en el índice local."""
    with runtime() as rt:
        t = Table(title=f"Índice local · {rt.indices.path}")
        for col in ("Tabla", "Filas", "Actualizado"):
            t.add_column(col)
        for e in rt.indices.estado():
            t.add_row(e.tabla, str(e.filas), e.actualizado or "—")
        console.print(t)


@app.command("actualizar-wmn")
def actualizar_wmn() -> None:
    """Descargar la última versión del dataset de WhatsMyName."""
    from osintsuite.sources.cuentas import WMN_URL, _cargar_wmn

    async def _go(settings: Settings) -> int:
        async with HttpClient(settings) as http:
            r = await http.get(WMN_URL, cache_ttl=0)
            if not r.ok:
                raise typer.BadParameter(f"GitHub respondió {r.status}")
            n = len(r.json()["sites"])
            (settings.data_dir / "wmn-data.json").write_bytes(r.content)
            return n

    with runtime() as rt:
        n = asyncio.run(_go(rt.settings))
        _cargar_wmn.cache_clear()
        console.print(f"WhatsMyName actualizado: {n} sitios.")


@app.command("limpiar-cache")
def limpiar_cache() -> None:
    """Vaciar la caché HTTP."""
    settings = Settings()
    if settings.cache_path.exists():
        cache = DiskCache(settings.cache_path)
        n = cache.clear()
        cache.close()
        console.print(f"Caché vaciada ({n} entradas).")
    else:
        console.print("No hay caché.")


# ------------------------------------------------------------------- casos


def _cargar(rt: Runtime, case_id: str) -> Case:
    case = rt.repo.get_case(case_id)
    if case is None:
        err.print(f"[red]No existe el caso {case_id}[/]")
        raise typer.Exit(1)
    return case


@caso_app.command("listar")
def caso_listar() -> None:
    """Listar los casos guardados."""
    with runtime() as rt:
        t = Table(title="Casos")
        for col in ("ID", "Fecha (UTC)", "Objetivo", "Estado", "Hallazgos", "Finalidad"):
            t.add_column(col)
        for c in rt.repo.list_cases():
            t.add_row(
                c.id,
                c.created_at.strftime("%Y-%m-%d %H:%M"),
                f"{c.target.value} ({c.target.type.value})",
                c.status.value,
                str(c.findings),
                c.purpose,
            )
        console.print(t)


@caso_app.command("ver")
def caso_ver(case_id: str, report: Report = None, salida: Salida = SALIDA_DEF) -> None:
    """Mostrar un caso y, opcionalmente, exportarlo."""
    formatos = _formatos(report)
    with runtime() as rt:
        from osintsuite.correlation import merge_findings

        case = _cargar(rt, case_id)
        case.findings = merge_findings(case.findings)
        mostrar_caso(case)
        _exportar(case, formatos, salida)


@caso_app.command("retomar")
def caso_retomar(
    case_id: str, depth: Annotated[int | None, typer.Option(min=0, max=3)] = None
) -> None:
    """Repetir las fuentes que fallaron o no llegaron a ejecutarse."""
    with runtime() as rt:
        _cargar(rt, case_id)

        async def _go() -> Case:
            async with HttpClient(rt.settings) as http:
                inv = Investigation(rt.settings, rt.repo, rt.registry, http, indices=rt.indices)
                opts = InvestigationOptions(depth=depth or 0, on_progress=_progreso)
                return await inv.resume(case_id, opts)

        mostrar_caso(asyncio.run(_go()))


@caso_app.command("comparar")
def caso_comparar(caso_a: str, caso_b: str) -> None:
    """Comparar las entidades de dos casos."""
    with runtime() as rt:
        d = diff_cases(_cargar(rt, caso_a), _cargar(rt, caso_b))
        for titulo, lista, estilo in (
            (f"Solo en {caso_a}", d.only_in_a, "red"),
            (f"Solo en {caso_b}", d.only_in_b, "green"),
            ("En ambos", d.common, "dim"),
        ):
            console.print(f"[bold {estilo}]{titulo}[/] ({len(lista)})")
            for e in lista:
                console.print(f"  {e.type.value}: {e.value}", highlight=False)


@caso_app.command("borrar")
def caso_borrar(
    case_id: str,
    si: Annotated[bool, typer.Option("--si", help="No pedir confirmación.")] = False,
) -> None:
    """Borrar un caso, sus evidencias y su log (no se puede deshacer)."""
    with runtime() as rt:
        _cargar(rt, case_id)
        if not si and not typer.confirm(f"¿Borrar definitivamente el caso {case_id}?"):
            raise typer.Abort()
        delete_case_files(rt.settings, case_id)
        rt.repo.delete_case(case_id)
        console.print(f"Caso {case_id} borrado.")


@caso_app.command("purgar")
def caso_purgar(
    si: Annotated[bool, typer.Option("--si", help="No pedir confirmación.")] = False,
) -> None:
    """Borrar los casos más antiguos que OSINT_CASE_RETENTION_DAYS."""
    with runtime() as rt:
        dias = rt.settings.case_retention_days
        if not si and not typer.confirm(f"¿Borrar los casos con más de {dias} días?"):
            raise typer.Abort()

        borrados = purge_expired(rt.settings, rt.repo)
        console.print(f"Casos borrados: {len(borrados)}")


def legacy_main(tipo: EntityType, pregunta: str) -> None:
    """Punto de entrada de los envoltorios ``Buscador*.py`` (compatibilidad con el original)."""
    console.print(
        f"[dim]osintsuite {__version__} · modo compatible con Dante's Gates Minimal "
        "Version (Quantika14)[/]"
    )
    valor = typer.prompt(pregunta)
    finalidad = typer.prompt("Finalidad de la investigación")
    investigar(tipo, valor, finalidad, 0, None, "json,md", SALIDA_DEF, False, False)


if __name__ == "__main__":  # pragma: no cover
    app()
