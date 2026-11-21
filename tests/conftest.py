"""Configuración común de los tests.

Los tests **no usan internet**: las peticiones HTTP se simulan con ``respx`` (y ``respx``
falla si una petición no está simulada), el DNS se sustituye por un resolvedor falso y
DuckDuckGo por una función que no devuelve nada salvo que el test la sustituya.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest

from danterodia import search
from danterodia.config import Settings
from danterodia.dnsutil import DnsResolver
from danterodia.evidence import EvidenceStore
from danterodia.http import HttpClient
from danterodia.indices import IndexStore
from danterodia.sources import SourceContext
from danterodia.storage import SQLiteRepository

PUBLIC_IP = "93.184.215.14"
FIXTURES = Path(__file__).parent / "fixtures"


def fixture_text(nombre: str) -> str:
    return (FIXTURES / nombre).read_text(encoding="utf-8")


def fixture_json(nombre: str) -> Any:
    return json.loads(fixture_text(nombre))


async def fake_resolver(host: str) -> list[str]:
    """Resolvedor DNS sin red: todo es público salvo los nombres que empiezan por 'interno'."""
    if host.startswith("interno"):
        return ["10.0.0.5"]
    return [PUBLIC_IP]


class FakeDns(DnsResolver):
    """DNS simulado: ``registros[(nombre, tipo)] = [...]``."""

    def __init__(self, registros: dict[tuple[str, str], list[str]] | None = None) -> None:
        self.registros = registros or {}

    async def query(self, name: str, rdtype: str) -> list[str]:
        return list(self.registros.get((name, rdtype), []))

    async def reverse(self, ip: str) -> list[str]:
        return list(self.registros.get((ip, "PTR"), []))


@pytest.fixture(autouse=True)
def _aislar_entorno(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Ninguna prueba lee el .env del usuario, variables DANTERODIA_* reales ni la red."""
    for key in list(os.environ):
        if key.startswith("DANTERODIA_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("danterodia.http.system_resolver", fake_resolver)
    monkeypatch.setattr(search, "_ddg_sync", lambda *a, **k: [])
    monkeypatch.setattr(search._ritmo_ddg, "intervalo", 0.0)

    async def _sin_dns(self: DnsResolver, name: str, rdtype: str) -> list[str]:
        return []

    monkeypatch.setattr(DnsResolver, "query", _sin_dns)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = Settings(
        data_dir=tmp_path / "datos",
        backoff_base=0,
        per_domain_interval=0,
        max_retries=2,
        timeout=5,
        source_timeout=2,
    )
    s.ensure_dirs()
    return s


@pytest.fixture
async def http(settings: Settings) -> AsyncIterator[HttpClient]:
    async with HttpClient(settings, resolver=fake_resolver) as client:
        yield client


@pytest.fixture
def repo(settings: Settings) -> Iterator[SQLiteRepository]:
    r = SQLiteRepository(settings.db_url)
    yield r
    r.close()


@pytest.fixture
def indices(settings: Settings) -> Iterator[IndexStore]:
    store = IndexStore(settings.data_dir / "indices.sqlite")
    yield store
    store.close()


@pytest.fixture
def ctx(settings: Settings, http: HttpClient, indices: IndexStore) -> SourceContext:
    return SourceContext(
        http=http,
        settings=settings,
        evidence=EvidenceStore(settings.cases_dir),
        case_id="caso-test",
        log=logging.getLogger("danterodia.test"),
        dns=FakeDns(),
        indices=indices,
    )
