"""Repositorio MongoDB (opcional: ``pip install danterodia[mongo]``).

El original exigía MongoDB siempre; aquí es una alternativa para quien ya lo use o quiera
compartir casos entre varios equipos. Se activa con
``DANTERODIA_DATABASE_URL=mongodb://usuario:clave@host:27017/danterodia``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from danterodia.models import Case, CaseStatus, Entity, Finding, SourceRun
from danterodia.storage.base import CaseSummary


def _utc(dt: datetime) -> datetime:
    """MongoDB devuelve fechas sin zona horaria (siempre UTC)."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class MongoRepository:
    def __init__(self, url: str, client: Any | None = None) -> None:
        if client is None:
            try:
                from pymongo import MongoClient
            except ImportError as exc:  # pragma: no cover - depende de la instalación
                raise RuntimeError(
                    "Para usar MongoDB instala el extra: pip install 'danterodia[mongo]'"
                ) from exc
            client = MongoClient(url, serverSelectionTimeoutMS=5000, tz_aware=True)
        self.client = client
        nombre = url.rsplit("/", 1)[-1].split("?", 1)[0] or "danterodia"
        self.db = client[nombre]
        try:
            self.db.findings.create_index("case_id")
            self.db.runs.create_index("case_id")
        except Exception as exc:
            raise RuntimeError(
                f"No se pudo conectar a MongoDB ({url.split('@')[-1]}): {exc}"
            ) from exc

    # -- escritura
    def create_case(self, case: Case) -> None:
        self.db.cases.insert_one(self._doc(case))
        if case.findings:
            self.add_findings(case.id, case.findings)
        for run in case.runs:
            self.add_run(case.id, run)

    def update_case(self, case: Case) -> None:
        self.db.cases.replace_one({"_id": case.id}, self._doc(case), upsert=True)

    def add_findings(self, case_id: str, findings: list[Finding]) -> None:
        for f in findings:
            doc = {"_id": f.id, "case_id": case_id, **f.model_dump(mode="json")}
            self.db.findings.replace_one({"_id": f.id}, doc, upsert=True)

    def add_run(self, case_id: str, run: SourceRun) -> None:
        self.db.runs.insert_one({"case_id": case_id, **run.model_dump(mode="json")})

    def delete_case(self, case_id: str) -> bool:
        self.db.findings.delete_many({"case_id": case_id})
        self.db.runs.delete_many({"case_id": case_id})
        return bool(self.db.cases.delete_one({"_id": case_id}).deleted_count)

    # -- lectura
    def get_case(self, case_id: str) -> Case | None:
        doc = self.db.cases.find_one({"_id": case_id})
        if doc is None:
            return None
        findings = [
            Finding.model_validate({k: v for k, v in f.items() if k not in {"_id", "case_id"}})
            for f in self.db.findings.find({"case_id": case_id})
        ]
        runs = [
            SourceRun.model_validate({k: v for k, v in r.items() if k not in {"_id", "case_id"}})
            for r in self.db.runs.find({"case_id": case_id}).sort("_id", 1)
        ]
        return Case.model_validate({**doc["case"], "findings": findings, "runs": runs})

    def list_cases(self) -> list[CaseSummary]:
        out = []
        for doc in self.db.cases.find().sort("created_at", -1):
            c = doc["case"]
            out.append(
                CaseSummary(
                    id=doc["_id"],
                    target=Entity.model_validate(c["target"]),
                    purpose=c["purpose"],
                    created_at=_utc(doc["created_at"]),
                    status=CaseStatus(c["status"]),
                    findings=self.db.findings.count_documents({"case_id": doc["_id"]}),
                )
            )
        return out

    def expired_cases(self, before: datetime) -> list[str]:
        return [d["_id"] for d in self.db.cases.find({"created_at": {"$lt": before}}, {"_id": 1})]

    def close(self) -> None:
        self.client.close()

    @staticmethod
    def _doc(case: Case) -> dict[str, Any]:
        datos = case.model_dump(mode="json", exclude={"findings", "runs"})
        # `created_at` como fecha nativa para poder filtrar por antigüedad (purgar).
        return {"_id": case.id, "created_at": case.created_at, "case": datos}
