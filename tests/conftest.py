from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

from osintsuite.config import Settings
from osintsuite.http import HttpClient
from osintsuite.storage import SQLiteRepository

PUBLIC_IP = "93.184.215.14"


async def fake_resolver(host: str) -> list[str]:
    """Resolvedor DNS sin red: todo es público salvo los nombres que empiezan por 'interno'."""
    if host.startswith("interno"):
        return ["10.0.0.5"]
    return [PUBLIC_IP]


@pytest.fixture(autouse=True)
def _aislar_entorno(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Ninguna prueba lee el .env del usuario ni variables OSINT_* reales."""
    import os

    for key in list(os.environ):
        if key.startswith("OSINT_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)


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
