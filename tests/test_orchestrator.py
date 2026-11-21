"""Orquestador con fuentes falsas: fallos aislados, timeouts, pivoting y retomar casos."""

from __future__ import annotations

import asyncio
from typing import ClassVar

import pytest

from danterodia.config import Settings
from danterodia.http import HttpClient, HttpError
from danterodia.investigations import Investigation, InvestigationOptions
from danterodia.investigations.orchestrator import purge_expired
from danterodia.models import CaseStatus, Entity, EntityType, Finding, SourceRun, SourceStatus
from danterodia.sources import Source, SourceContext, SourceRegistry
from danterodia.storage import SQLiteRepository

NICK = frozenset({EntityType.USERNAME})
EMAIL = frozenset({EntityType.EMAIL})


class FuenteNick(Source):
    name = "nick_falsa"
    input_types = NICK

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        email = Entity(type=EntityType.EMAIL, value=f"{target.value}@ejemplo.es")
        perfil = Entity(type=EntityType.URL, value=f"https://red.es/{target.value}")
        return [
            self.finding(target, email, relation="usa_email", confidence=0.8),
            self.finding(target, perfil, relation="tiene_perfil", confidence=0.9),
            # Duplicado exacto con menos confianza: la deduplicación debe quedarse con el otro.
            self.finding(target, perfil, relation="tiene_perfil", confidence=0.3),
        ]


class FuenteEmail(Source):
    name = "email_falsa"
    input_types = EMAIL
    llamadas: ClassVar[list[str]] = []

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        FuenteEmail.llamadas.append(target.key)
        # Devuelve otra vez el nick original: no debe investigarse dos veces.
        nick = Entity(type=EntityType.USERNAME, value=target.value.split("@")[0])
        dominio = Entity(type=EntityType.DOMINIO, value="ejemplo.es")
        return [
            Finding(source=self.name, entity=nick, confidence=0.9),  # sin related_to
            self.finding(target, dominio, relation="dominio", confidence=0.95),
        ]


class FuenteRota(Source):
    name = "rota"
    input_types = NICK
    fallos: ClassVar[int] = 1

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        if FuenteRota.fallos > 0:
            FuenteRota.fallos -= 1
            raise ValueError("selector HTML cambiado")
        return []


class FuenteRed(Source):
    name = "red_caida"
    input_types = NICK

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        raise HttpError("503")


class FuenteLenta(Source):
    name = "lenta"
    input_types = NICK

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        await asyncio.sleep(10)
        return []


class FuenteConClave(Source):
    name = "con_clave"
    input_types = NICK
    requires_api_key = "hibp_api_key"

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        raise AssertionError("no debería ejecutarse sin clave")


class FuenteSensible(Source):
    name = "sensible"
    input_types = NICK
    sensitive = True

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        raise AssertionError("no debería ejecutarse sin --sensibles")


@pytest.fixture
def registry() -> SourceRegistry:
    FuenteEmail.llamadas = []
    FuenteRota.fallos = 1
    return SourceRegistry(
        [
            FuenteNick(),
            FuenteEmail(),
            FuenteRota(),
            FuenteRed(),
            FuenteLenta(),
            FuenteConClave(),
            FuenteSensible(),
        ]
    )


@pytest.fixture
def fast(settings: Settings) -> Settings:
    return settings.model_copy(update={"source_timeout": 0.2})


def _por_fuente(runs: list[SourceRun]) -> dict[str, SourceStatus]:
    return {r.source: r.status for r in runs if r.depth == 0}


async def test_fallos_aislados_y_omitidas(
    fast: Settings, repo: SQLiteRepository, registry: SourceRegistry, http: HttpClient
) -> None:
    progreso: list[SourceRun] = []
    inv = Investigation(fast, repo, registry, http)
    case = await inv.run(
        Entity(type=EntityType.USERNAME, value="pepe"),
        "Prueba automatizada",
        InvestigationOptions(on_progress=progreso.append),
    )
    estados = _por_fuente(case.runs)
    assert estados["nick_falsa"] == SourceStatus.OK
    assert estados["rota"] == SourceStatus.FAILED
    assert estados["red_caida"] == SourceStatus.FAILED
    assert estados["lenta"] == SourceStatus.TIMEOUT
    assert estados["con_clave"] == SourceStatus.SKIPPED
    assert estados["sensible"] == SourceStatus.SKIPPED
    assert "email_falsa" not in estados  # no acepta usernames y depth=0
    assert case.status == CaseStatus.PARTIAL
    assert len(progreso) == len(case.runs)
    # Deduplicado: el perfil aparece una vez, con la confianza mayor.
    perfiles = [f for f in case.findings if f.entity.type == EntityType.URL]
    assert len(perfiles) == 1 and perfiles[0].confidence == 0.9
    # Persistido.
    guardado = repo.get_case(case.id)
    assert guardado is not None and guardado.status == CaseStatus.PARTIAL
    assert len(guardado.runs) == len(case.runs)
    # Log del caso en JSONL.
    log = (fast.cases_dir / case.id / "caso.log.jsonl").read_text(encoding="utf-8")
    assert "Prueba automatizada" in log and "selector HTML cambiado" in log


async def test_pivoting_sin_repetir(
    settings: Settings, repo: SQLiteRepository, http: HttpClient
) -> None:
    registry = SourceRegistry([FuenteNick(), FuenteEmail()])
    FuenteEmail.llamadas = []
    inv = Investigation(settings, repo, registry, http)
    case = await inv.run(
        Entity(type=EntityType.USERNAME, value="pepe"),
        "Prueba de pivoting",
        InvestigationOptions(depth=2),
    )
    assert case.status == CaseStatus.DONE
    assert FuenteEmail.llamadas == ["email:pepe@ejemplo.es"]
    # El nick original no se reinvestiga en el nivel 2 aunque reaparezca.
    nick_runs = [r for r in case.runs if r.source == "nick_falsa"]
    assert len(nick_runs) == 1
    keys = set(case.entities())
    assert {"username:pepe", "email:pepe@ejemplo.es", "dominio:ejemplo.es"} <= keys
    # El hallazgo sin related_to queda enlazado a la entidad investigada.
    assert all(f.related_to for f in case.findings)


async def test_limite_de_pivoting(
    settings: Settings, repo: SQLiteRepository, http: HttpClient
) -> None:
    s = settings.model_copy(update={"max_pivot_entities": 1})
    FuenteEmail.llamadas = []
    inv = Investigation(s, repo, SourceRegistry([FuenteNick(), FuenteEmail()]), http)
    await inv.run(
        Entity(type=EntityType.USERNAME, value="pepe"),
        "Prueba de límite",
        InvestigationOptions(depth=1),
    )
    assert FuenteEmail.llamadas == []


async def test_confianza_minima_para_pivotar(
    settings: Settings, repo: SQLiteRepository, http: HttpClient
) -> None:
    FuenteEmail.llamadas = []
    inv = Investigation(settings, repo, SourceRegistry([FuenteNick(), FuenteEmail()]), http)
    await inv.run(
        Entity(type=EntityType.USERNAME, value="pepe"),
        "Prueba de confianza",
        InvestigationOptions(depth=1, pivot_min_confidence=0.85),
    )
    assert FuenteEmail.llamadas == []  # el email tenía 0.8


async def test_retomar_repite_solo_lo_fallido(
    fast: Settings, repo: SQLiteRepository, http: HttpClient
) -> None:
    registry = SourceRegistry([FuenteNick(), FuenteRota()])
    FuenteRota.fallos = 1
    inv = Investigation(fast, repo, registry, http)
    case = await inv.run(Entity(type=EntityType.USERNAME, value="pepe"), "Prueba de retomar")
    assert case.status == CaseStatus.PARTIAL
    retomado = await inv.resume(case.id)
    nuevos = retomado.runs[len(case.runs) :]
    assert [r.source for r in nuevos] == ["rota"]
    assert nuevos[0].status == SourceStatus.EMPTY
    with pytest.raises(KeyError):
        await inv.resume("no-existe")


async def test_fuentes_desconocidas_y_desactivadas(
    settings: Settings, repo: SQLiteRepository, registry: SourceRegistry, http: HttpClient
) -> None:
    inv = Investigation(settings, repo, registry, http)
    with pytest.raises(KeyError):
        await inv.run(
            Entity(type=EntityType.USERNAME, value="x"),
            "Prueba",
            InvestigationOptions(sources=["no_existe"]),
        )
    s = settings.model_copy(update={"disabled_sources": ["nick_falsa"]})
    activas, descartadas = registry.select(s)
    assert "nick_falsa" not in [a.name for a in activas]
    assert descartadas["nick_falsa"].startswith("desactivada")
    sens = settings.model_copy(update={"include_sensitive": True})
    assert "sensible" in [a.name for a in registry.select(sens)[0]]
    with pytest.raises(ValueError, match="duplicada"):
        registry.register(FuenteNick())


def test_purgar_casos_caducados(settings: Settings, repo: SQLiteRepository) -> None:
    from datetime import timedelta

    from danterodia.models import Case, utcnow

    viejo = Case(
        target=Entity(type=EntityType.EMAIL, value="a@b.es"),
        purpose="Caso antiguo",
        created_at=utcnow() - timedelta(days=settings.case_retention_days + 1),
    )
    nuevo = Case(target=Entity(type=EntityType.EMAIL, value="a@b.es"), purpose="Caso reciente")
    repo.create_case(viejo)
    repo.create_case(nuevo)
    (settings.cases_dir / viejo.id).mkdir()
    assert purge_expired(settings, repo) == [viejo.id]
    assert not (settings.cases_dir / viejo.id).exists()
    assert repo.get_case(nuevo.id) is not None


def test_discover_encuentra_ine() -> None:
    assert "ine" in SourceRegistry.discover()
