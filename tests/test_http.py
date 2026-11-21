from __future__ import annotations

import httpx
import pytest
import respx

from danterodia.config import Settings
from danterodia.http import (
    BlockedURLError,
    DiskCache,
    HttpClient,
    HttpError,
    HttpResponse,
    ResponseTooLargeError,
    _parse_retry_after,
    is_onion,
)
from tests.conftest import fake_resolver


@respx.mock
async def test_get_ok_y_cache(http: HttpClient) -> None:
    ruta = respx.get("https://api.ejemplo.es/x").mock(
        return_value=httpx.Response(
            200, json={"ok": True}, headers={"content-type": "application/json"}
        )
    )
    r1 = await http.get("https://api.ejemplo.es/x")
    r2 = await http.get("https://api.ejemplo.es/x")
    assert r1.ok and r1.json() == {"ok": True}
    assert not r1.from_cache and r2.from_cache
    assert ruta.call_count == 1
    # Con TTL 0 no se usa la caché.
    await http.get("https://api.ejemplo.es/x", cache_ttl=0)
    assert ruta.call_count == 2


@respx.mock
async def test_params_y_user_agent(http: HttpClient, settings: Settings) -> None:
    ruta = respx.get("https://api.ejemplo.es/buscar", params={"q": "ana garcía"}).mock(
        return_value=httpx.Response(200, text="hola")
    )
    r = await http.get("https://api.ejemplo.es/buscar", params={"q": "ana garcía"})
    assert r.text == "hola"
    assert ruta.calls.last.request.headers["user-agent"] == settings.user_agent


@respx.mock
async def test_reintenta_503_y_luego_ok(http: HttpClient) -> None:
    ruta = respx.get("https://a.es/").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(429, headers={"retry-after": "0"}),
            httpx.Response(200, text="ok"),
        ]
    )
    r = await http.get("https://a.es/", cache_ttl=0)
    assert r.status == 200 and ruta.call_count == 3


@respx.mock
async def test_devuelve_ultimo_error_http(http: HttpClient) -> None:
    respx.get("https://a.es/").mock(return_value=httpx.Response(503))
    r = await http.get("https://a.es/", cache_ttl=0)
    assert r.status == 503 and not r.ok


@respx.mock
async def test_error_de_red_tras_reintentos(http: HttpClient) -> None:
    ruta = respx.get("https://a.es/").mock(side_effect=httpx.ConnectError("caído"))
    with pytest.raises(HttpError, match="3 intentos"):
        await http.get("https://a.es/")
    assert ruta.call_count == 3


@respx.mock
async def test_redireccion_segura_y_bloqueada(http: HttpClient) -> None:
    respx.get("https://a.es/r").mock(
        return_value=httpx.Response(302, headers={"location": "/final"})
    )
    respx.get("https://a.es/final").mock(return_value=httpx.Response(200, text="fin"))
    r = await http.get("https://a.es/r")
    assert r.url == "https://a.es/final" and r.text == "fin"

    respx.get("https://a.es/ssrf").mock(
        return_value=httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})
    )
    with pytest.raises(BlockedURLError):
        await http.get("https://a.es/ssrf")


@respx.mock
async def test_demasiadas_redirecciones(http: HttpClient) -> None:
    respx.get("https://a.es/bucle").mock(
        return_value=httpx.Response(301, headers={"location": "/bucle"})
    )
    with pytest.raises(HttpError, match="redirecciones"):
        await http.get("https://a.es/bucle", cache_ttl=0)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://a.es/x",
        "http://localhost/",
        "http://127.0.0.1:8080/",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data/",
        "http://interno.empresa/",  # el resolvedor falso lo resuelve a 10.0.0.5
        "http:///sin-host",
    ],
)
async def test_ssrf_bloqueado(http: HttpClient, url: str) -> None:
    with pytest.raises(BlockedURLError):
        await http.check_url(url)


async def test_redes_privadas_permitidas_si_se_configura(settings: Settings) -> None:
    s = settings.model_copy(update={"allow_private_networks": True})
    async with HttpClient(s, resolver=fake_resolver) as client:
        await client.check_url("http://127.0.0.1/")


async def test_resolucion_fallida(settings: Settings) -> None:
    async def roto(host: str) -> list[str]:
        raise OSError("sin DNS")

    async with HttpClient(settings, resolver=roto) as client:
        with pytest.raises(HttpError, match="resolver"):
            await client.check_url("https://a.es/")


@respx.mock
async def test_respuesta_demasiado_grande(settings: Settings) -> None:
    s = settings.model_copy(update={"max_response_bytes": 1024})
    respx.get("https://a.es/grande").mock(return_value=httpx.Response(200, content=b"x" * 5000))
    async with HttpClient(s, resolver=fake_resolver) as client:
        with pytest.raises(ResponseTooLargeError):
            await client.get("https://a.es/grande")


async def test_onion_usa_cliente_tor_separado(http: HttpClient) -> None:
    assert is_onion("http://abcdef.onion/x") and not is_onion("https://onion.es/")
    normal = http._client_for("https://a.es/")
    tor = http._client_for("http://abcdefghijklmnop.onion/")
    assert tor is not normal
    await http.check_url("http://abcdefghijklmnop.onion/")  # no se resuelve por DNS


async def test_cliente_sin_iniciar(settings: Settings) -> None:
    client = HttpClient(settings, resolver=fake_resolver)
    with pytest.raises(RuntimeError):
        client._client_for("https://a.es/")
    await client.aclose()


def test_parse_retry_after() -> None:
    assert _parse_retry_after("7") == 7.0
    assert _parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0
    assert _parse_retry_after("basura") is None


def test_backoff_respeta_retry_after(settings: Settings) -> None:
    client = HttpClient(settings.model_copy(update={"backoff_base": 1.0}), cache=None)
    resp = HttpResponse(url="u", status=429, headers={"retry-after": "500"}, content=b"")
    assert client._backoff(0, resp) == 120.0  # tope de 2 minutos
    assert 2.0 <= client._backoff(1, None) <= 3.0


def test_disk_cache(tmp_path: object, settings: Settings) -> None:
    cache = DiskCache(settings.cache_path)
    k1 = DiskCache.make_key("GET", "https://a.es", None, {"authorization": "Bearer 1"})
    k2 = DiskCache.make_key("GET", "https://a.es", None, {"authorization": "Bearer 2"})
    assert k1 != k2
    assert cache.clear() == 0
    cache.close()


def test_response_text_charset() -> None:
    r = HttpResponse(
        url="u",
        status=200,
        headers={"content-type": "text/html; charset=latin-1"},
        content="ñ".encode("latin-1"),
    )
    assert r.text == "ñ"
    r2 = HttpResponse(
        url="u", status=200, headers={"content-type": "text/html; charset=nope"}, content=b"abc"
    )
    assert r2.text == "abc" and r2.content_type is not None
