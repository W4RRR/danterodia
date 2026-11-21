"""Contrato del repositorio MongoDB.

- Siempre: con ``mongomock`` (Mongo simulado en memoria).
- Además, contra un MongoDB real si se define ``TEST_MONGO_URL``, por ejemplo con
  ``docker compose --profile mongo up -d mongo`` y
  ``TEST_MONGO_URL=mongodb://localhost:27017/danterodia_test``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import timedelta

import mongomock
import pytest

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
from danterodia.storage import Repository, diff_cases, open_repository
from danterodia.storage.mongo import MongoRepository

URL_REAL = os.environ.get("TEST_MONGO_URL")


@pytest.fixture(params=["mongomock", "real"])
def repo(request: pytest.FixtureRequest) -> Iterator[Repository]:
    if request.param == "mongomock":
        r = MongoRepository(
            "mongodb://simulado/danterodia_test", client=mongomock.MongoClient(tz_aware=True)
        )
    else:
        if not URL_REAL:
            pytest.skip("define TEST_MONGO_URL para probar contra un MongoDB real")
        r = MongoRepository(URL_REAL)
        r.client.drop_database(r.db.name)
        r = MongoRepository(URL_REAL)
    yield r
    r.close()


def _case() -> Case:
    t = Entity(type=EntityType.USERNAME, value="pepe")
    return Case(
        target=t,
        purpose="Prueba de MongoDB",
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


def test_contrato_mongo(repo: Repository) -> None:
    c = _case()
    repo.create_case(c)
    leido = repo.get_case(c.id)
    assert leido is not None
    assert leido.target == c.target and leido.purpose == c.purpose
    assert leido.findings[0].data == {"x": [1, 2]} and leido.runs[0].status == SourceStatus.OK
    assert leido.created_at.tzinfo is not None

    repo.add_findings(c.id, c.findings)  # idempotente
    assert len(repo.get_case(c.id).findings) == 1  # type: ignore[union-attr]

    c.status = CaseStatus.DONE
    repo.update_case(c)
    (resumen,) = repo.list_cases()
    assert (resumen.id, resumen.findings, resumen.status) == (c.id, 1, CaseStatus.DONE)
    assert resumen.created_at.tzinfo is not None

    assert repo.expired_cases(utcnow() + timedelta(seconds=1)) == [c.id]
    assert repo.expired_cases(utcnow() - timedelta(days=1)) == []
    assert diff_cases(leido, leido).common

    assert repo.delete_case(c.id) is True
    assert repo.get_case(c.id) is None
    assert repo.delete_case(c.id) is False


def test_open_repository_mongo(monkeypatch: pytest.MonkeyPatch) -> None:
    import pymongo

    monkeypatch.setattr(
        pymongo, "MongoClient", lambda *a, **k: mongomock.MongoClient(tz_aware=True)
    )
    r = open_repository("mongodb://localhost:27017/otra_base")
    assert isinstance(r, MongoRepository) and r.db.name == "otra_base"
    r.close()
