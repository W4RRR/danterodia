from __future__ import annotations

from datetime import timedelta

import pytest

from danterodia.config import Settings
from danterodia.evidence import EvidenceStore, sha256_bytes
from danterodia.models import (
    Case,
    CaseStatus,
    Entity,
    EntityType,
    Finding,
    SourceRun,
    SourceStatus,
    utcnow,
)
from danterodia.storage import SQLiteRepository, diff_cases, open_repository


def _case(valor: str = "pepe") -> Case:
    t = Entity(type=EntityType.USERNAME, value=valor, attributes={"origen": "test"})
    return Case(
        target=t,
        purpose="Prueba de almacenamiento",
        sources=["a"],
        findings=[
            Finding(
                source="a",
                entity=Entity(type=EntityType.EMAIL, value="p@e.es"),
                related_to=t.key,
                data={"x": [1, 2]},
            )
        ],
        runs=[SourceRun(source="a", target=t.key, status=SourceStatus.OK, findings=1)],
    )


def test_crear_leer_listar_borrar(repo: SQLiteRepository) -> None:
    c = _case()
    repo.create_case(c)
    leido = repo.get_case(c.id)
    assert leido is not None
    assert leido.target == c.target and leido.target.attributes == {"origen": "test"}
    assert leido.created_at == c.created_at and leido.created_at.tzinfo is not None
    assert leido.findings[0].data == {"x": [1, 2]}
    assert leido.runs[0].status == SourceStatus.OK

    c.status, c.finished_at = CaseStatus.DONE, utcnow()
    repo.update_case(c)
    resumen = repo.list_cases()
    assert [(r.id, r.findings, r.status) for r in resumen] == [(c.id, 1, CaseStatus.DONE)]

    assert repo.delete_case(c.id) is True
    assert repo.get_case(c.id) is None
    assert repo.delete_case(c.id) is False


def test_add_findings_idempotente(repo: SQLiteRepository) -> None:
    c = _case()
    repo.create_case(c)
    repo.add_findings(c.id, c.findings)  # el mismo id: se actualiza, no se duplica
    leido = repo.get_case(c.id)
    assert leido is not None and len(leido.findings) == 1


def test_expirados(repo: SQLiteRepository) -> None:
    c = _case()
    repo.create_case(c)
    assert repo.expired_cases(utcnow() + timedelta(seconds=1)) == [c.id]
    assert repo.expired_cases(utcnow() - timedelta(days=1)) == []


def test_diff() -> None:
    a, b = _case(), _case()
    b.findings = [Finding(source="a", entity=Entity(type=EntityType.DOMINIO, value="e.es"))]
    d = diff_cases(a, b)
    assert [e.key for e in d.only_in_a] == ["email:p@e.es"]
    assert [e.key for e in d.only_in_b] == ["dominio:e.es"]
    assert [e.key for e in d.common] == ["username:pepe"]


def test_open_repository(settings: Settings) -> None:
    r = open_repository(settings.db_url)
    r.close()
    with pytest.raises(ValueError, match="no soportado"):
        open_repository("postgresql://localhost/x")


def test_evidencias(settings: Settings) -> None:
    store = EvidenceStore(settings.cases_dir)
    ev = store.save("caso1", "https://a.es/x", b"<html>hola</html>", "text/html; charset=utf-8")
    assert ev.sha256 == sha256_bytes(b"<html>hola</html>")
    assert ev.path == f"evidencias/{ev.sha256}.html" and ev.size == 17
    assert store.verify("caso1", ev)
    # Mismo contenido desde otra URL: un fichero, dos registros de metadatos.
    ev2 = store.save("caso1", "https://b.es/y", b"<html>hola</html>", "text/html")
    assert ev2.path == ev.path
    meta = (settings.cases_dir / "caso1" / f"{ev.path}.json").read_text(encoding="utf-8")
    assert "https://a.es/x" in meta and "https://b.es/y" in meta
    # Manipular el fichero rompe la verificación.
    (settings.cases_dir / "caso1" / ev.path).write_bytes(b"alterado")
    assert not store.verify("caso1", ev)
    assert not store.verify("caso1", ev.model_copy(update={"path": None}))
    assert store.save("caso1", "u", b"%PDF", "application/pdf").path.endswith(".pdf")  # type: ignore[union-attr]
    assert store.save("caso1", "u", b"?", None).path.endswith(".bin")  # type: ignore[union-attr]


@pytest.mark.parametrize("malo", ["", "../fuera", "a/b", "c:\\x", "a.b"])
def test_case_id_no_valido(settings: Settings, malo: str) -> None:
    with pytest.raises(ValueError):
        EvidenceStore(settings.cases_dir).case_dir(malo)
