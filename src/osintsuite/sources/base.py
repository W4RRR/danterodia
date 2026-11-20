"""Interfaz común de las fuentes (plugins) y su registro.

Una fuente es una clase que hereda de :class:`Source`, declara qué tipos de entidad sabe
investigar y devuelve una lista de :class:`~osintsuite.models.Finding`. No necesita saber
nada de casos, base de datos ni informes: eso lo hace el orquestador.

Las fuentes se descubren por *entry points* del grupo ``osintsuite.sources``, así que un
paquete externo puede añadir las suyas sin tocar este código (ver ``docs/CREAR_PLUGIN.md``).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, ClassVar

from osintsuite.models import Entity, EntityType, Finding

if TYPE_CHECKING:
    from osintsuite.config import Settings
    from osintsuite.evidence import EvidenceStore
    from osintsuite.http import HttpClient

ENTRY_POINT_GROUP = "osintsuite.sources"


@dataclass(slots=True)
class SourceContext:
    """Lo que el orquestador pone a disposición de cada fuente."""

    http: HttpClient
    settings: Settings
    evidence: EvidenceStore
    case_id: str
    log: logging.Logger
    depth: int = 0


class Source(ABC):
    #: Identificador corto y estable (se usa en la configuración y en los informes).
    name: ClassVar[str]
    #: Descripción de una línea para ``osint fuentes``.
    description: ClassVar[str] = ""
    #: Tipos de entidad que acepta.
    input_types: ClassVar[frozenset[EntityType]]
    #: Nombre del campo de :class:`Settings` con la clave de API (o ``None`` si no hace falta).
    requires_api_key: ClassVar[str | None] = None
    #: Trata datos penales o de categoría especial: desactivada salvo ``include_sensitive``.
    sensitive: ClassVar[bool] = False
    #: No hace peticiones de red (útil para tests y modo sin conexión).
    offline: ClassVar[bool] = False

    def accepts(self, entity: Entity) -> bool:
        return entity.type in self.input_types

    def availability(self, settings: Settings) -> tuple[bool, str]:
        """¿Se puede usar con esta configuración? Devuelve ``(disponible, motivo)``."""
        if self.sensitive and not settings.include_sensitive:
            return False, "fuente sensible: actívala con OSINT_INCLUDE_SENSITIVE=true"
        if self.requires_api_key and not settings.api_key(self.requires_api_key):
            return False, f"falta la clave OSINT_{self.requires_api_key.upper()}"
        return True, "disponible"

    @abstractmethod
    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        """Investiga ``target`` y devuelve los hallazgos. Puede lanzar excepciones:
        el orquestador las registra y continúa con el resto de fuentes."""

    def finding(self, target: Entity, entity: Entity, **kwargs: object) -> Finding:
        """Atajo para crear un hallazgo de esta fuente relacionado con ``target``."""
        return Finding(source=self.name, entity=entity, related_to=target.key, **kwargs)


class SourceRegistry:
    def __init__(self, sources: list[Source] | None = None) -> None:
        self._sources: dict[str, Source] = {}
        for s in sources or []:
            self.register(s)

    def register(self, source: Source) -> None:
        if source.name in self._sources:
            raise ValueError(f"Fuente duplicada: {source.name}")
        self._sources[source.name] = source

    @classmethod
    def discover(cls) -> SourceRegistry:
        """Carga todas las fuentes instaladas (entry points ``osintsuite.sources``)."""
        registry = cls()
        log = logging.getLogger("osintsuite.sources")
        for ep in entry_points(group=ENTRY_POINT_GROUP):
            try:
                source_cls = ep.load()
                registry.register(source_cls())
            except (ImportError, AttributeError, TypeError, ValueError) as exc:
                log.warning("No se pudo cargar la fuente %s: %s", ep.name, exc)
        return registry

    def all(self) -> list[Source]:
        return sorted(self._sources.values(), key=lambda s: s.name)

    def get(self, name: str) -> Source:
        return self._sources[name]

    def __contains__(self, name: object) -> bool:
        return name in self._sources

    def select(
        self, settings: Settings, names: list[str] | None = None
    ) -> tuple[list[Source], dict[str, str]]:
        """Fuentes activas según la configuración, y motivo de las descartadas."""
        wanted = names or settings.enabled_sources
        unknown = [n for n in (wanted or []) if n not in self._sources]
        if unknown:
            raise KeyError(f"Fuentes desconocidas: {', '.join(unknown)}")
        activas: list[Source] = []
        descartadas: dict[str, str] = {}
        for source in self.all():
            if wanted is not None and source.name not in wanted:
                continue
            if source.name in settings.disabled_sources:
                descartadas[source.name] = "desactivada en la configuración"
                continue
            ok, motivo = source.availability(settings)
            if ok:
                activas.append(source)
            else:
                descartadas[source.name] = motivo
        return activas, descartadas
