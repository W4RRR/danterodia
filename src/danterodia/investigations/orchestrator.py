"""Orquestador: ejecuta las fuentes sobre el objetivo y, si se pide, sobre lo que descubren.

Flujo (``--depth N``):

1. Nivel 0: todas las fuentes activas que aceptan el tipo del objetivo, en paralelo.
2. De los hallazgos se extraen entidades nuevas «pivotables» (email, nick, dominio…).
3. Si ``nivel < N``, esas entidades se investigan en el nivel siguiente, **sin repetir**
   ninguna ya vista y con un máximo total (``max_pivot_entities``).

Cada ejecución de fuente queda registrada (:class:`SourceRun`): si una fuente falla o tarda
demasiado, se apunta el error y la investigación sigue.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

from danterodia.config import Settings
from danterodia.correlation import ajustar_por_frecuencia, merge_findings
from danterodia.dnsutil import DnsResolver
from danterodia.evidence import EvidenceStore
from danterodia.http import HttpClient, HttpError
from danterodia.indices import IndexStore
from danterodia.logs import case_logger
from danterodia.models import (
    PIVOTABLE_TYPES,
    Case,
    CaseStatus,
    Entity,
    Finding,
    SourceRun,
    SourceStatus,
    utcnow,
)
from danterodia.sources.base import Source, SourceContext, SourceRegistry, SourceSkipped
from danterodia.storage.base import Repository

log = logging.getLogger("danterodia.investigacion")

ProgressCallback = Callable[[SourceRun], None]


@dataclass(slots=True)
class InvestigationOptions:
    depth: int = 0
    sources: list[str] | None = None
    #: Confianza mínima de un hallazgo para pivotar sobre su entidad.
    pivot_min_confidence: float = 0.5
    on_progress: ProgressCallback | None = None


@dataclass(slots=True)
class _Estado:
    case: Case
    vistos: set[str] = field(default_factory=set)
    #: (fuente, entidad) ya ejecutadas, para no repetir al retomar un caso.
    hechas: set[tuple[str, str]] = field(default_factory=set)


class Investigation:
    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        registry: SourceRegistry,
        http: HttpClient,
        *,
        indices: IndexStore | None = None,
        dns: DnsResolver | None = None,
    ) -> None:
        self.settings = settings
        self.repo = repository
        self.registry = registry
        self.http = http
        self.indices = indices
        self.dns = dns or DnsResolver(timeout=min(settings.timeout, 10.0))
        self.evidence = EvidenceStore(settings.cases_dir)

    # ------------------------------------------------------------ API

    async def run(
        self, target: Entity, purpose: str, options: InvestigationOptions | None = None
    ) -> Case:
        """Crea un caso nuevo y lo investiga."""
        options = options or InvestigationOptions()
        sources, descartadas = self.registry.select(self.settings, options.sources)
        case = Case(
            target=target,
            purpose=purpose,
            depth=options.depth,
            sources=[s.name for s in sources],
        )
        self.repo.create_case(case)
        estado = _Estado(case=case, vistos={target.key})
        for nombre, motivo in descartadas.items():
            if not self.registry.get(nombre).accepts(target):
                continue  # no aplica a este tipo de objetivo: no se registra
            self._registrar(
                estado,
                SourceRun(
                    source=nombre,
                    target=target.key,
                    status=SourceStatus.SKIPPED,
                    finished_at=utcnow(),
                    error=motivo,
                ),
                options,
            )
        return await self._ejecutar(estado, sources, [target], options)

    async def resume(self, case_id: str, options: InvestigationOptions | None = None) -> Case:
        """Retoma un caso: repite las ejecuciones que fallaron o no llegaron a hacerse."""
        case = self.repo.get_case(case_id)
        if case is None:
            raise KeyError(f"No existe el caso {case_id}")
        options = options or InvestigationOptions(depth=case.depth)
        options.depth = max(options.depth, case.depth)
        sources, _ = self.registry.select(self.settings, options.sources or case.sources or None)
        estado = _Estado(case=case)
        estado.hechas = {
            (r.source, r.target)
            for r in case.runs
            if r.status in {SourceStatus.OK, SourceStatus.EMPTY}
        }
        estado.vistos = set(case.entities())
        case.status = CaseStatus.RUNNING
        case.depth = options.depth
        # Se vuelve a recorrer desde el objetivo: lo ya hecho se salta gracias a `hechas`.
        return await self._ejecutar(estado, sources, [case.target], options)

    # ------------------------------------------------------------ interno

    async def _ejecutar(
        self,
        estado: _Estado,
        sources: list[Source],
        nivel_inicial: list[Entity],
        options: InvestigationOptions,
    ) -> Case:
        case = estado.case
        case_dir = self.evidence.case_dir(case.id)
        clog, handler = case_logger(case.id, case_dir)
        clog.info(
            "Caso iniciado",
            extra={
                "objetivo": case.target.key,
                "finalidad": case.purpose,
                "fuentes": [s.name for s in sources],
                "depth": options.depth,
            },
        )
        try:
            pendientes = nivel_inicial
            for nivel in range(options.depth + 1):
                if not pendientes:
                    break
                nuevos = await self._nivel(estado, sources, pendientes, nivel, clog, options)
                if nivel == options.depth:
                    break
                pendientes = self._siguientes(estado, nuevos, options)
            ajustados = ajustar_por_frecuencia(case)
            if ajustados:
                self.repo.add_findings(case.id, case.findings)  # guarda la confianza ajustada
                clog.info("Confianza ajustada por frecuencia del nombre en %d hallazgos", ajustados)
            # En la base de datos quedan los hallazgos en bruto; al caso devuelto se le
            # entregan ya deduplicados (la deduplicación es reproducible: ver correlation).
            case.findings = merge_findings(case.findings)
            fallos = any(r.status in {SourceStatus.FAILED, SourceStatus.TIMEOUT} for r in case.runs)
            case.status = CaseStatus.PARTIAL if fallos else CaseStatus.DONE
            case.finished_at = utcnow()
            self.repo.update_case(case)
            clog.info(
                "Caso terminado",
                extra={"estado": case.status.value, "hallazgos": len(case.findings)},
            )
        finally:
            clog.removeHandler(handler)
            handler.close()
        return case

    async def _nivel(
        self,
        estado: _Estado,
        sources: list[Source],
        entidades: list[Entity],
        nivel: int,
        clog: logging.Logger,
        options: InvestigationOptions,
    ) -> list[Finding]:
        tareas = [
            self._una(estado, source, entidad, nivel, clog, options)
            for entidad in entidades
            for source in sources
            if source.accepts(entidad) and (source.name, entidad.key) not in estado.hechas
        ]
        resultados = await asyncio.gather(*tareas)
        nuevos = [f for lote in resultados for f in lote]
        if nuevos:
            self.repo.add_findings(estado.case.id, nuevos)
            estado.case.findings.extend(nuevos)
        return nuevos

    async def _una(
        self,
        estado: _Estado,
        source: Source,
        entidad: Entity,
        nivel: int,
        clog: logging.Logger,
        options: InvestigationOptions,
    ) -> list[Finding]:
        ctx = SourceContext(
            http=self.http,
            settings=self.settings,
            evidence=self.evidence,
            case_id=estado.case.id,
            log=clog.getChild(source.name),
            depth=nivel,
            dns=self.dns,
            indices=self.indices,
        )
        run = SourceRun(source=source.name, target=entidad.key, status=SourceStatus.OK, depth=nivel)
        inicio = time.monotonic()
        limite = source.timeout or self.settings.source_timeout
        findings: list[Finding] = []
        try:
            findings = await asyncio.wait_for(source.run(entidad, ctx), timeout=limite)
            run.status = SourceStatus.OK if findings else SourceStatus.EMPTY
        except TimeoutError:
            run.status = SourceStatus.TIMEOUT
            run.error = f"superó {limite:.0f} s"
        except SourceSkipped as exc:
            run.status = SourceStatus.SKIPPED
            run.error = str(exc)
        except HttpError as exc:
            run.status = SourceStatus.FAILED
            run.error = f"red: {exc}"
        except Exception as exc:  # noqa: BLE001 - una fuente rota no debe tumbar el caso
            run.status = SourceStatus.FAILED
            run.error = f"{type(exc).__name__}: {exc}"
            clog.exception("La fuente %s falló con %s", source.name, entidad.key)
        run.finished_at = utcnow()
        run.findings = len(findings)
        for f in findings:
            if f.related_to is None:
                f.related_to = entidad.key
        clog.info(
            "Fuente %s sobre %s: %s",
            source.name,
            entidad.key,
            run.status.value,
            extra={
                "fuente": source.name,
                "entidad": entidad.key,
                "estado": run.status.value,
                "hallazgos": run.findings,
                "error": run.error,
                "segundos": round(time.monotonic() - inicio, 2),
            },
        )
        self._registrar(estado, run, options)
        return findings

    def _registrar(self, estado: _Estado, run: SourceRun, options: InvestigationOptions) -> None:
        estado.case.runs.append(run)
        if run.status in {SourceStatus.OK, SourceStatus.EMPTY}:
            estado.hechas.add((run.source, run.target))
        self.repo.add_run(estado.case.id, run)
        if options.on_progress is not None:
            options.on_progress(run)

    def _siguientes(
        self, estado: _Estado, findings: list[Finding], options: InvestigationOptions
    ) -> list[Entity]:
        siguientes: list[Entity] = []
        for f in sorted(findings, key=lambda x: x.confidence, reverse=True):
            e = f.entity
            if (
                e.type in PIVOTABLE_TYPES
                and not e.sensitive
                and f.confidence >= options.pivot_min_confidence
                and e.key not in estado.vistos
            ):
                if len(estado.vistos) >= self.settings.max_pivot_entities:
                    log.warning(
                        "Límite de %d entidades alcanzado; no se pivota más",
                        self.settings.max_pivot_entities,
                    )
                    break
                estado.vistos.add(e.key)
                siguientes.append(e)
        return siguientes


def purge_expired(settings: Settings, repo: Repository) -> list[str]:
    """Borra los casos más antiguos que ``case_retention_days`` (minimización, art. 5.1.e RGPD)."""
    limite = utcnow() - timedelta(days=settings.case_retention_days)
    borrados = []
    for case_id in repo.expired_cases(limite):
        delete_case_files(settings, case_id)
        if repo.delete_case(case_id):
            borrados.append(case_id)
    return borrados


def delete_case_files(settings: Settings, case_id: str) -> None:
    """Borra la carpeta del caso (evidencias y log)."""
    folder = EvidenceStore(settings.cases_dir).case_dir(case_id)
    if folder.is_dir():
        shutil.rmtree(folder)
