"""Repositorio SQLite con SQLModel.

Las columnas que se usan para filtrar (caso, fuente, entidad, confianza) son columnas reales;
el resto del hallazgo se guarda como JSON para no tener que migrar el esquema cada vez que una
fuente añade un campo.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import JSON, Column, event
from sqlalchemy.engine import Engine
from sqlmodel import Field, Session, SQLModel, col, create_engine, delete, func, select

from osintsuite.models import Case, CaseStatus, Entity, EntityType, Finding, SourceRun
from osintsuite.storage.base import CaseSummary


class CaseRow(SQLModel, table=True):
    __tablename__ = "cases"

    id: str = Field(primary_key=True)
    target_type: str
    target_value: str
    target_key: str = Field(index=True)
    target_attributes: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    purpose: str
    created_at: datetime = Field(index=True)
    finished_at: datetime | None = None
    status: str
    depth: int = 0
    sources: list[str] = Field(default_factory=list, sa_column=Column(JSON))


class FindingRow(SQLModel, table=True):
    __tablename__ = "findings"

    id: str = Field(primary_key=True)
    case_id: str = Field(foreign_key="cases.id", index=True)
    source: str = Field(index=True)
    entity_key: str = Field(index=True)
    related_to: str | None = None
    confidence: float
    payload: dict[str, Any] = Field(sa_column=Column(JSON))


class RunRow(SQLModel, table=True):
    __tablename__ = "source_runs"

    id: int | None = Field(default=None, primary_key=True)
    case_id: str = Field(foreign_key="cases.id", index=True)
    source: str
    target: str
    status: str
    payload: dict[str, Any] = Field(sa_column=Column(JSON))


def _to_db(dt: datetime) -> datetime:
    """Las fechas se guardan siempre en UTC y con zona horaria (las ingenuas se asumen UTC)."""
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


def _from_db(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


def _enable_sqlite_fk(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn: Any, _record: Any) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


class SQLiteRepository:
    def __init__(self, db_url: str) -> None:
        if db_url.startswith("sqlite:///") and not db_url.endswith(":memory:"):
            Path(db_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(db_url, connect_args={"check_same_thread": False})
        _enable_sqlite_fk(self.engine)
        SQLModel.metadata.create_all(self.engine)

    # -- escritura
    def create_case(self, case: Case) -> None:
        with Session(self.engine) as s:
            s.add(self._case_row(case))
            s.commit()
        if case.findings:
            self.add_findings(case.id, case.findings)
        for run in case.runs:
            self.add_run(case.id, run)

    def update_case(self, case: Case) -> None:
        with Session(self.engine) as s:
            s.merge(self._case_row(case))
            s.commit()

    def add_findings(self, case_id: str, findings: list[Finding]) -> None:
        with Session(self.engine) as s:
            for f in findings:
                s.merge(
                    FindingRow(
                        id=f.id,
                        case_id=case_id,
                        source=f.source,
                        entity_key=f.entity.key,
                        related_to=f.related_to,
                        confidence=f.confidence,
                        payload=f.model_dump(mode="json"),
                    )
                )
            s.commit()

    def add_run(self, case_id: str, run: SourceRun) -> None:
        with Session(self.engine) as s:
            s.add(
                RunRow(
                    case_id=case_id,
                    source=run.source,
                    target=run.target,
                    status=run.status.value,
                    payload=run.model_dump(mode="json"),
                )
            )
            s.commit()

    def delete_case(self, case_id: str) -> bool:
        with Session(self.engine) as s:
            s.exec(delete(FindingRow).where(col(FindingRow.case_id) == case_id))
            s.exec(delete(RunRow).where(col(RunRow.case_id) == case_id))
            result = s.exec(delete(CaseRow).where(col(CaseRow.id) == case_id))
            s.commit()
            return bool(result.rowcount)

    # -- lectura
    def get_case(self, case_id: str) -> Case | None:
        with Session(self.engine) as s:
            row = s.get(CaseRow, case_id)
            if row is None:
                return None
            findings = s.exec(select(FindingRow).where(col(FindingRow.case_id) == case_id)).all()
            runs = s.exec(
                select(RunRow).where(col(RunRow.case_id) == case_id).order_by(col(RunRow.id))
            ).all()
            return Case(
                id=row.id,
                target=Entity(
                    type=EntityType(row.target_type),
                    value=row.target_value,
                    attributes=row.target_attributes or {},
                ),
                purpose=row.purpose,
                created_at=_from_db(row.created_at),
                finished_at=_from_db(row.finished_at),
                status=CaseStatus(row.status),
                depth=row.depth,
                sources=list(row.sources or []),
                findings=[Finding.model_validate(f.payload) for f in findings],
                runs=[SourceRun.model_validate(r.payload) for r in runs],
            )

    def list_cases(self) -> list[CaseSummary]:
        with Session(self.engine) as s:
            counts = dict(
                s.exec(
                    select(FindingRow.case_id, func.count()).group_by(col(FindingRow.case_id))
                ).all()
            )
            rows = s.exec(select(CaseRow).order_by(col(CaseRow.created_at).desc())).all()
            return [
                CaseSummary(
                    id=r.id,
                    target=Entity(type=EntityType(r.target_type), value=r.target_value),
                    purpose=r.purpose,
                    created_at=_from_db(r.created_at) or r.created_at,
                    status=CaseStatus(r.status),
                    findings=int(counts.get(r.id, 0)),
                )
                for r in rows
            ]

    def expired_cases(self, before: datetime) -> list[str]:
        with Session(self.engine) as s:
            return list(
                s.exec(select(CaseRow.id).where(col(CaseRow.created_at) < _to_db(before))).all()
            )

    def close(self) -> None:
        self.engine.dispose()

    @staticmethod
    def _case_row(case: Case) -> CaseRow:
        return CaseRow(
            id=case.id,
            target_type=case.target.type.value,
            target_value=case.target.value,
            target_key=case.target.key,
            target_attributes=case.target.attributes,
            purpose=case.purpose,
            created_at=_to_db(case.created_at),
            finished_at=_to_db(case.finished_at) if case.finished_at else None,
            status=case.status.value,
            depth=case.depth,
            sources=case.sources,
        )
