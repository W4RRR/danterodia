"""Motores de búsqueda.

- **DuckDuckGo** con la librería ``ddgs`` (mantenida). No necesita clave, pero limita el ritmo:
  se serializan las consultas con una pausa mínima.
- **APIs oficiales opcionales** (con clave): Brave Search, Google Programmable Search y Mojeek.

El scraping de HTML del original (Google, Bing, Dogpile, Yahoo…) se retiró: Google y Dogpile
bloquean, y Mojeek ahora exige JavaScript (comprobado el 20/11/2026).
"""

from __future__ import annotations

import asyncio
import logging
import time
import tomllib
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from danterodia.http import HttpClient, HttpError

log = logging.getLogger("danterodia.search")


@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    engine: str
    date: str | None = None
    source: str | None = None


class _Ritmo:
    """Garantiza un intervalo mínimo entre consultas a DuckDuckGo (evita bloqueos)."""

    def __init__(self, intervalo: float) -> None:
        self.intervalo = intervalo
        self._lock = asyncio.Lock()
        self._ultimo = 0.0

    async def esperar(self) -> None:
        async with self._lock:
            espera = self._ultimo + self.intervalo - time.monotonic()
            if espera > 0:
                await asyncio.sleep(espera)
            self._ultimo = time.monotonic()


_ritmo_ddg = _Ritmo(2.0)


def _ddg_sync(
    metodo: str, query: str, max_results: int, timeout: float, proxy: str | None
) -> list[dict[str, Any]]:
    from ddgs import DDGS
    from ddgs.exceptions import DDGSException

    try:
        cliente = DDGS(timeout=int(timeout), proxy=proxy)
        fn = cliente.news if metodo == "news" else cliente.text
        return list(fn(query, region="es-es", safesearch="moderate", max_results=max_results) or [])
    except DDGSException as exc:  # sin resultados o limitado
        log.info("DuckDuckGo (%s) sin resultados para %r: %s", metodo, query, exc)
        return []


async def ddg(
    query: str,
    *,
    max_results: int = 10,
    news: bool = False,
    segundos: float = 20.0,
    proxy: str | None = None,
) -> list[SearchResult]:
    await _ritmo_ddg.esperar()
    filas = await asyncio.to_thread(
        _ddg_sync, "news" if news else "text", query, max_results, segundos, proxy
    )
    return [
        SearchResult(
            title=str(f.get("title", "")),
            url=str(f.get("href") or f.get("url") or ""),
            snippet=str(f.get("body", "")),
            engine="ddg_news" if news else "ddg",
            date=f.get("date"),
            source=f.get("source"),
        )
        for f in filas
        if str(f.get("href") or f.get("url") or "").startswith(("http://", "https://"))
    ]


async def brave(
    http: HttpClient, key: str, query: str, max_results: int = 10
) -> list[SearchResult]:
    r = await http.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": min(max_results, 20), "country": "es", "search_lang": "es"},
        headers={"X-Subscription-Token": key, "Accept": "application/json"},
    )
    if not r.ok:
        raise HttpError(f"Brave Search respondió {r.status}")
    return [
        SearchResult(
            title=w.get("title", ""),
            url=w["url"],
            snippet=w.get("description", ""),
            engine="brave",
            date=w.get("age"),
        )
        for w in r.json().get("web", {}).get("results", [])
    ]


async def google_cse(
    http: HttpClient, key: str, cx: str, query: str, max_results: int = 10
) -> list[SearchResult]:
    r = await http.get(
        "https://www.googleapis.com/customsearch/v1",
        params={
            "key": key,
            "cx": cx,
            "q": query,
            "num": min(max_results, 10),
            "gl": "es",
            "hl": "es",
        },
    )
    if not r.ok:
        raise HttpError(f"Google CSE respondió {r.status}")
    return [
        SearchResult(
            title=i.get("title", ""),
            url=i["link"],
            snippet=i.get("snippet", ""),
            engine="google_cse",
        )
        for i in r.json().get("items", [])
    ]


async def mojeek_api(
    http: HttpClient, key: str, query: str, max_results: int = 10
) -> list[SearchResult]:
    r = await http.get(
        "https://api.mojeek.com/search",
        params={"q": query, "api_key": key, "fmt": "json", "t": min(max_results, 20), "lb": "es"},
    )
    if not r.ok:
        raise HttpError(f"Mojeek API respondió {r.status}")
    return [
        SearchResult(
            title=i.get("title", ""), url=i["url"], snippet=i.get("desc", ""), engine="mojeek"
        )
        for i in r.json().get("response", {}).get("results", [])
    ]


# ------------------------------------------------------------------- dorks


def cargar_dorks(data_dir: Path | None = None) -> dict[str, list[str]]:
    """Plantillas de dorks. El usuario puede sobrescribirlas en ``<data_dir>/dorks.toml``."""
    texto = files("danterodia.data").joinpath("dorks.toml").read_text(encoding="utf-8")
    plantillas = {k: list(v["plantillas"]) for k, v in tomllib.loads(texto).items()}
    if data_dir is not None and (propio := data_dir / "dorks.toml").is_file():
        for k, v in tomllib.loads(propio.read_text(encoding="utf-8")).items():
            plantillas[k] = list(v["plantillas"])
    return plantillas


def render_dorks(plantillas: list[str], variables: dict[str, str]) -> list[str]:
    out = []
    for p in plantillas:
        try:
            q = p.format(**variables)
        except KeyError:
            continue  # la plantilla usa una variable que este objetivo no tiene
        if q not in out:
            out.append(q)
    return out
