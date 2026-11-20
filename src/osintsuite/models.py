"""Modelo de datos unificado.

Todo lo que descubre una fuente se representa como un :class:`Finding` que apunta a una
:class:`Entity`. Así el orquestador, la correlación y los informes no necesitan saber nada
de cada fuente concreta.
"""

from __future__ import annotations

import ipaddress
import re
import unicodedata
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utcnow() -> datetime:
    """Fecha y hora actual en UTC (siempre con zona horaria)."""
    return datetime.now(UTC)


class EntityType(StrEnum):
    PERSONA = "persona"
    EMAIL = "email"
    TELEFONO = "telefono"
    USERNAME = "username"
    DOMINIO = "dominio"
    IP = "ip"
    EMPRESA = "empresa"
    URL = "url"
    DOCUMENTO = "documento"


#: Tipos que tiene sentido volver a investigar al hacer *pivoting* (``--depth``).
PIVOTABLE_TYPES: frozenset[EntityType] = frozenset(
    {
        EntityType.EMAIL,
        EntityType.USERNAME,
        EntityType.DOMINIO,
        EntityType.TELEFONO,
        EntityType.EMPRESA,
        EntityType.IP,
    }
)


def _sin_acentos(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in descompuesto if not unicodedata.combining(c))


_ESPACIOS = re.compile(r"\s+")


def normalize_value(entity_type: EntityType, value: str) -> str:
    """Devuelve una forma canónica del valor para poder deduplicar.

    Ejemplos: ``"José  PÉREZ"`` → ``"jose perez"``, ``"Ejemplo.COM."`` → ``"ejemplo.com"``.
    """
    v = _ESPACIOS.sub(" ", value.strip())
    match entity_type:
        case EntityType.PERSONA | EntityType.EMPRESA:
            return _sin_acentos(v).casefold()
        case EntityType.EMAIL | EntityType.USERNAME:
            return v.casefold()
        case EntityType.DOMINIO:
            return v.casefold().rstrip(".")
        case EntityType.TELEFONO:
            return ("+" if v.startswith("+") else "") + re.sub(r"\D", "", v)
        case EntityType.IP:
            try:
                return str(ipaddress.ip_address(v))
            except ValueError:
                return v
        case _:
            return v


class Entity(BaseModel):
    """Algo que se puede investigar o que aparece en una investigación."""

    model_config = ConfigDict(frozen=True)

    type: EntityType
    value: str = Field(min_length=1)
    attributes: dict[str, Any] = Field(default_factory=dict)
    #: Datos de categoría especial o penales (art. 9 y 10 RGPD): se tratan con más cuidado.
    sensitive: bool = False

    @field_validator("value")
    @classmethod
    def _limpia(cls, v: str) -> str:
        return v.strip()

    @property
    def key(self) -> str:
        """Identificador estable para deduplicar: ``tipo:valor_normalizado``."""
        return f"{self.type.value}:{normalize_value(self.type, self.value)}"

    def __hash__(self) -> int:
        return hash(self.key)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Entity) and other.key == self.key


class Evidence(BaseModel):
    """Prueba preservada de un hallazgo: qué se descargó, cuándo y su huella SHA-256."""

    url: str
    retrieved_at: datetime = Field(default_factory=utcnow)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    #: Ruta relativa a la carpeta del caso (``evidencias/<sha256>.<ext>``).
    path: str | None = None
    content_type: str | None = None
    size: int | None = None


class Finding(BaseModel):
    """Un dato descubierto por una fuente."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    source: str
    #: La entidad descubierta (p. ej. una cuenta, un email, una empresa).
    entity: Entity
    #: Clave de la entidad investigada que llevó a este hallazgo.
    related_to: str | None = None
    #: Tipo de relación con ``related_to`` (``"tiene_cuenta"``, ``"cargo_en"``…).
    relation: str = "relacionado_con"
    title: str = ""
    description: str = ""
    timestamp: datetime = Field(default_factory=utcnow)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    source_url: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)

    @property
    def dedup_key(self) -> tuple[str, str, str | None, str]:
        return (self.source, self.entity.key, self.related_to, self.relation)


class SourceStatus(StrEnum):
    OK = "ok"
    EMPTY = "sin_resultados"
    SKIPPED = "omitida"
    FAILED = "error"
    TIMEOUT = "timeout"


class SourceRun(BaseModel):
    """Registro de la ejecución de una fuente sobre una entidad."""

    source: str
    target: str  # clave de la entidad
    status: SourceStatus
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    findings: int = 0
    error: str | None = None
    depth: int = 0


class CaseStatus(StrEnum):
    RUNNING = "en_curso"
    DONE = "terminado"
    PARTIAL = "parcial"  # terminó, pero alguna fuente falló


class Case(BaseModel):
    """Una investigación: objetivo, finalidad declarada, fuentes usadas y resultados."""

    id: str = Field(
        default_factory=lambda: utcnow().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    )
    target: Entity
    #: Finalidad declarada por quien investiga (principio de limitación, art. 5.1.b RGPD).
    purpose: str = Field(min_length=5)
    created_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    status: CaseStatus = CaseStatus.RUNNING
    depth: int = Field(default=0, ge=0)
    sources: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    runs: list[SourceRun] = Field(default_factory=list)

    def entities(self) -> dict[str, Entity]:
        """Todas las entidades del caso (objetivo incluido), indexadas por su clave."""
        result = {self.target.key: self.target}
        for f in self.findings:
            result.setdefault(f.entity.key, f.entity)
        return result
