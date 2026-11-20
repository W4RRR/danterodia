"""Cliente HTTP común para todas las fuentes.

Concentra en un único sitio lo que en el código original faltaba o estaba repetido:

- **timeouts** siempre (el original tenía 31 peticiones sin timeout);
- **reintentos** con *backoff* exponencial y *jitter*, respetando ``Retry-After``;
- **límite por dominio**: concurrencia máxima e intervalo mínimo entre peticiones;
- **caché en disco** (SQLite) con TTL para no repetir consultas;
- **protección anti-SSRF**: solo ``http``/``https`` y nada de IPs privadas o locales,
  comprobado también en cada redirección;
- **Tor aislado**: las direcciones ``.onion`` van por un cliente separado con el proxy de Tor,
  y nunca se mezclan con el tráfico normal;
- **TLS verificado** siempre y tamaño máximo de respuesta.
"""

from __future__ import annotations

import asyncio
import email.utils
import hashlib
import ipaddress
import json
import logging
import random
import sqlite3
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from osintsuite.config import Settings

log = logging.getLogger("osintsuite.http")

RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
CACHEABLE_STATUS = frozenset({200, 203, 204, 300, 301, 404, 410})
MAX_REDIRECTS = 5

Resolver = Callable[[str], Awaitable[list[str]]]


class HttpError(Exception):
    """Error de red no recuperable tras los reintentos."""


class BlockedURLError(HttpError):
    """La URL no está permitida (esquema, red privada, .onion sin Tor…)."""


class ResponseTooLargeError(HttpError):
    """La respuesta supera ``max_response_bytes``."""


@dataclass(slots=True)
class HttpResponse:
    url: str
    status: int
    headers: dict[str, str]
    content: bytes
    from_cache: bool = False
    elapsed: float = 0.0

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def content_type(self) -> str | None:
        return self.headers.get("content-type")

    @property
    def text(self) -> str:
        charset = "utf-8"
        ctype = self.content_type or ""
        if "charset=" in ctype:
            charset = ctype.split("charset=", 1)[1].split(";", 1)[0].strip() or "utf-8"
        try:
            return self.content.decode(charset, errors="replace")
        except LookupError:
            return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.content)


# --------------------------------------------------------------------- caché


class DiskCache:
    """Caché HTTP mínima en SQLite (clave = método + URL + cuerpo + cabeceras relevantes)."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS http_cache ("
            " key TEXT PRIMARY KEY, url TEXT, status INTEGER, headers TEXT,"
            " content BLOB, stored_at REAL)"
        )
        self._db.commit()
        self._lock = asyncio.Lock()

    @staticmethod
    def make_key(method: str, url: str, body: bytes | None, headers: Mapping[str, str]) -> str:
        h = hashlib.sha256()
        h.update(method.upper().encode())
        h.update(b"\0" + url.encode())
        h.update(b"\0" + (body or b""))
        for name in ("accept", "accept-language", "authorization"):
            if name in headers:
                # Las claves cambian la respuesta, pero no se guardan en claro: solo su hash.
                h.update(b"\0" + name.encode() + b"=" + headers[name].encode())
        return h.hexdigest()

    async def get(self, key: str, ttl: float) -> HttpResponse | None:
        async with self._lock:
            row = self._db.execute(
                "SELECT url, status, headers, content, stored_at FROM http_cache WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None or time.time() - row[4] > ttl:
            return None
        return HttpResponse(
            url=row[0], status=row[1], headers=json.loads(row[2]), content=row[3], from_cache=True
        )

    async def set(self, key: str, response: HttpResponse) -> None:
        async with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO http_cache VALUES (?, ?, ?, ?, ?, ?)",
                (
                    key,
                    response.url,
                    response.status,
                    json.dumps(response.headers),
                    response.content,
                    time.time(),
                ),
            )
            self._db.commit()

    def clear(self) -> int:
        n = self._db.execute("DELETE FROM http_cache").rowcount
        self._db.commit()
        return n

    def close(self) -> None:
        self._db.close()


# --------------------------------------------------------------- límites


@dataclass
class _DomainLimiter:
    semaphore: asyncio.Semaphore
    interval: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last: float = 0.0

    async def wait_turn(self) -> None:
        async with self.lock:
            delay = self.last + self.interval - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self.last = time.monotonic()


# -------------------------------------------------------------- SSRF


async def system_resolver(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None)
    return sorted({str(info[4][0]) for info in infos})


def _ip_is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip.split("%", 1)[0])
    return addr.is_global and not addr.is_multicast


def is_onion(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host.endswith(".onion")


# -------------------------------------------------------------- cliente


class HttpClient:
    """Cliente asíncrono compartido. Úsalo como ``async with HttpClient(settings) as http:``."""

    def __init__(
        self,
        settings: Settings,
        *,
        resolver: Resolver | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        cache: DiskCache | None = None,
    ) -> None:
        self.settings = settings
        self._resolver = resolver or system_resolver
        self._transport = transport
        self._cache = cache
        if self._cache is None and settings.cache_enabled:
            self._cache = DiskCache(settings.cache_path)
        self._global = asyncio.Semaphore(settings.max_concurrency)
        self._domains: dict[str, _DomainLimiter] = {}
        self._client: httpx.AsyncClient | None = None
        self._tor_client: httpx.AsyncClient | None = None

    # -- ciclo de vida
    def _build(self, proxy: str | None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(self.settings.timeout),
            headers={"User-Agent": self.settings.user_agent},
            follow_redirects=False,  # se siguen a mano para validar cada salto
            verify=True,
            http2=self._transport is None,
            proxy=proxy if self._transport is None else None,
            transport=self._transport,
        )

    async def __aenter__(self) -> HttpClient:
        self._client = self._build(self.settings.proxy)
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        for client in (self._client, self._tor_client):
            if client is not None:
                await client.aclose()
        self._client = self._tor_client = None
        if self._cache is not None:
            self._cache.close()
            self._cache = None

    def _client_for(self, url: str) -> httpx.AsyncClient:
        if is_onion(url):
            if self._tor_client is None:
                self._tor_client = self._build(self.settings.tor_proxy)
            return self._tor_client
        if self._client is None:
            raise RuntimeError("HttpClient no iniciado: usa 'async with HttpClient(...)'")
        return self._client

    # -- validación
    async def check_url(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"}:
            raise BlockedURLError(f"Esquema no permitido: {parts.scheme!r} ({url})")
        host = parts.hostname
        if not host:
            raise BlockedURLError(f"URL sin host: {url}")
        if host.lower().endswith(".onion") or self.settings.allow_private_networks:
            return  # las .onion las resuelve Tor; no hay IP local que comprobar
        if host.lower().rstrip(".") in {
            "localhost",
            "localhost.localdomain",
        } or host.lower().endswith(".localhost"):
            raise BlockedURLError(f"Host local no permitido: {host}")
        try:
            ips = [str(ipaddress.ip_address(host))]
        except ValueError:
            try:
                ips = await self._resolver(host)
            except OSError as exc:
                raise HttpError(f"No se pudo resolver {host}: {exc}") from exc
        bloqueadas = [ip for ip in ips if not _ip_is_public(ip)]
        if not ips or bloqueadas:
            raise BlockedURLError(
                f"{host} apunta a una dirección no pública ({', '.join(bloqueadas) or 'ninguna'})"
            )

    def _limiter(self, url: str) -> _DomainLimiter:
        host = (urlsplit(url).hostname or "").lower()
        if host not in self._domains:
            self._domains[host] = _DomainLimiter(
                semaphore=asyncio.Semaphore(self.settings.per_domain_concurrency),
                interval=self.settings.per_domain_interval,
            )
        return self._domains[host]

    # -- API pública
    async def get(
        self,
        url: str,
        *,
        params: Mapping[str, str | int] | None = None,
        headers: Mapping[str, str] | None = None,
        cache_ttl: float | None = None,
    ) -> HttpResponse:
        return await self.request("GET", url, params=params, headers=headers, cache_ttl=cache_ttl)

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str | int] | None = None,
        headers: Mapping[str, str] | None = None,
        content: bytes | None = None,
        cache_ttl: float | None = None,
    ) -> HttpResponse:
        """Hace la petición con todas las protecciones. No lanza por códigos 4xx/5xx finales."""
        if params:
            url = str(httpx.URL(url, params=dict(params)))
        hdrs = {k.lower(): v for k, v in (headers or {}).items()}
        ttl = self.settings.cache_ttl if cache_ttl is None else cache_ttl
        key = DiskCache.make_key(method, url, content, hdrs)
        cache = self._cache if method.upper() == "GET" and ttl > 0 else None
        if cache is not None:
            cached = await cache.get(key, ttl)
            if cached is not None:
                log.debug("caché: %s", url)
                return cached

        response = await self._with_retries(method, url, hdrs, content)
        if cache is not None and response.status in CACHEABLE_STATUS:
            await cache.set(key, response)
        return response

    async def _with_retries(
        self, method: str, url: str, headers: dict[str, str], content: bytes | None
    ) -> HttpResponse:
        intentos = self.settings.max_retries + 1
        ultimo_error: Exception | None = None
        for intento in range(intentos):
            response: HttpResponse | None = None
            try:
                response = await self._follow(method, url, headers, content)
            except (httpx.TransportError, httpx.DecodingError) as exc:
                ultimo_error = exc
                log.debug("intento %d/%d fallido para %s: %s", intento + 1, intentos, url, exc)
            else:
                if response.status not in RETRY_STATUS or intento == intentos - 1:
                    return response
                log.debug("HTTP %d en %s; reintentando", response.status, url)
            if intento < intentos - 1:
                await asyncio.sleep(self._backoff(intento, response))
        raise HttpError(f"{method} {url} falló tras {intentos} intentos: {ultimo_error}")

    def _backoff(self, intento: int, response: HttpResponse | None) -> float:
        if response is not None and (retry_after := response.headers.get("retry-after")):
            espera = _parse_retry_after(retry_after)
            if espera is not None:
                return min(espera, 120.0)
        base = self.settings.backoff_base * (2**intento)
        return float(base + random.uniform(0, base / 2))  # noqa: S311 - jitter, no criptografía

    async def _follow(
        self, method: str, url: str, headers: dict[str, str], content: bytes | None
    ) -> HttpResponse:
        actual = url
        for _ in range(MAX_REDIRECTS + 1):
            await self.check_url(actual)
            response = await self._send(method, actual, headers, content)
            location = response.headers.get("location")
            if response.status in {301, 302, 303, 307, 308} and location:
                actual = urljoin(actual, location)
                if response.status == 303:
                    method, content = "GET", None
                continue
            return response
        raise HttpError(f"Demasiadas redirecciones desde {url}")

    async def _send(
        self, method: str, url: str, headers: dict[str, str], content: bytes | None
    ) -> HttpResponse:
        client = self._client_for(url)
        limiter = self._limiter(url)
        async with self._global, limiter.semaphore:
            await limiter.wait_turn()
            inicio = time.monotonic()
            log.debug("%s %s", method, url)
            async with client.stream(method, url, headers=headers, content=content) as r:
                cuerpo = bytearray()
                async for chunk in r.aiter_bytes():
                    cuerpo.extend(chunk)
                    if len(cuerpo) > self.settings.max_response_bytes:
                        raise ResponseTooLargeError(
                            f"{url} supera {self.settings.max_response_bytes} bytes"
                        )
                return HttpResponse(
                    url=str(r.url),
                    status=r.status_code,
                    headers={k.lower(): v for k, v in r.headers.items()},
                    content=bytes(cuerpo),
                    elapsed=time.monotonic() - inicio,
                )


def _parse_retry_after(value: str) -> float | None:
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())
