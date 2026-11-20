"""Interfaz común de almacenamiento y utilidades independientes del motor."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from osintsuite.models import Case, CaseStatus, Entity, Finding, SourceRun


@dataclass(frozen=True, slots=True)
class CaseSummary:
    id: str
    target: Entity
    purpose: str
    created_at: datetime
    status: CaseStatus
    findings: int


class Repository(Protocol):
    def create_case(self, case: Case) -> None: ...
    def update_case(self, case: Case) -> None: ...
    def add_findings(self, case_id: str, findings: list[Finding]) -> None: ...
    def add_run(self, case_id: str, run: SourceRun) -> None: ...
    def get_case(self, case_id: str) -> Case | None: ...
    def list_cases(self) -> list[CaseSummary]: ...
    def delete_case(self, case_id: str) -> bool: ...
    def expired_cases(self, before: datetime) -> list[str]: ...
    def close(self) -> None: ...


@dataclass(slots=True)
class CaseDiff:
    """Diferencias entre dos casos (normalmente, dos ejecuciones sobre el mismo objetivo)."""

    only_in_a: list[Entity] = field(default_factory=list)
    only_in_b: list[Entity] = field(default_factory=list)
    common: list[Entity] = field(default_factory=list)


def diff_cases(a: Case, b: Case) -> CaseDiff:
    ea, eb = a.entities(), b.entities()
    return CaseDiff(
        only_in_a=[ea[k] for k in sorted(ea.keys() - eb.keys())],
        only_in_b=[eb[k] for k in sorted(eb.keys() - ea.keys())],
        common=[ea[k] for k in sorted(ea.keys() & eb.keys())],
    )
