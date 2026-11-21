"""Fuentes de red con respuestas simuladas (respx). Ninguna petición sale a internet."""

from __future__ import annotations

import hashlib
from typing import Any

import httpx
import pytest
import respx

from danterodia.http import HttpError
from danterodia.models import Entity, EntityType
from danterodia.sources import SourceContext, cuentas
from danterodia.sources.cuentas import (
    DnsCorreo,
    EmailRep,
    Gravatar,
    Hibp,
    Pgp,
    WhatsMyName,
    evaluar_wmn,
)
from danterodia.sources.infraestructura import (
    Censys,
    CrtSh,
    IpInfo,
    Rdap,
    Shodan,
    Tecnologias,
    Wayback,
)
from danterodia.sources.wikidata import Wikidata, _fecha
from tests.conftest import FakeDns, fixture_json

DOM = Entity(type=EntityType.DOMINIO, value="example.com")
IP = Entity(type=EntityType.IP, value="8.8.8.8")
EMAIL = Entity(type=EntityType.EMAIL, value="Ana@Ejemplo.es")


# ------------------------------------------------------------- Wikidata


@respx.mock
async def test_wikidata(ctx: SourceContext) -> None:
    respx.get("https://www.wikidata.org/w/api.php", params={"action": "wbsearchentities"}).mock(
        return_value=httpx.Response(200, json=fixture_json("wikidata_search.json"))
    )
    respx.get("https://www.wikidata.org/w/api.php", params={"action": "wbgetentities"}).mock(
        return_value=httpx.Response(
            200,
            json={
                "entities": {
                    "Q36180": {"labels": {"es": {"value": "escritor"}}},
                    "Q29": {"labels": {"es": {"value": "España"}}},
                }
            },
        )
    )
    entidad = fixture_json("wikidata_Q5682.json")
    entidad["entities"]["Q5682"]["claims"]["P2002"] = [
        {"mainsnak": {"snaktype": "value", "datavalue": {"value": "cuenta_ficticia"}}}
    ]
    entidad["entities"]["Q5682"]["claims"]["P856"] = [
        {"mainsnak": {"snaktype": "value", "datavalue": {"value": "https://www.ejemplo.es/"}}}
    ]
    respx.get("https://www.wikidata.org/wiki/Special:EntityData/Q5682.json").mock(
        return_value=httpx.Response(200, json=entidad)
    )
    # Los otros candidatos (crucero, biblioteca) no son humanos.
    for q in ("Q3396067", "Q85687119"):
        respx.get(f"https://www.wikidata.org/wiki/Special:EntityData/{q}.json").mock(
            return_value=httpx.Response(200, json={"entities": {q: {"claims": {}}}})
        )

    fs = await Wikidata().run(Entity(type=EntityType.PERSONA, value="Miguel de Cervantes"), ctx)
    identidad = next(f for f in fs if f.relation == "posible_identidad")
    assert identidad.entity.attributes["nacimiento"] == "1547-09-29"
    assert identidad.entity.attributes["wikipedia"].startswith("https://es.wikipedia.org/wiki/")
    assert "religion" not in identidad.data and "partido_politico" not in identidad.data
    assert identidad.evidence and identidad.evidence[0].sha256
    assert {f.relation for f in fs} >= {"cuenta_oficial:x", "web_oficial"}
    assert any(f.entity.key == "dominio:ejemplo.es" for f in fs)


def test_wikidata_fecha() -> None:
    assert _fecha({"time": "+1547-09-29T00:00:00Z", "precision": 11}) == "1547-09-29"
    assert _fecha({"time": "+1547-00-00T00:00:00Z", "precision": 9}) == "1547"
    assert _fecha({"time": "+1547-09-00T00:00:00Z", "precision": 10}) == "1547-09"


# ------------------------------------------------------- dominio e IP


@respx.mock
async def test_rdap_dominio_e_ip(ctx: SourceContext) -> None:
    respx.get("https://rdap.org/domain/example.com").mock(
        return_value=httpx.Response(200, json=fixture_json("rdap_example_com.json"))
    )
    (f, *_resto) = await Rdap().run(DOM, ctx)
    assert f.data["registro"].startswith("1995") and f.data["nameservers"]
    respx.get("https://rdap.org/ip/8.8.8.8").mock(
        return_value=httpx.Response(
            200,
            json={
                "startAddress": "8.8.8.0",
                "endAddress": "8.8.8.255",
                "name": "GOGL",
                "events": [],
                "entities": [
                    {
                        "roles": ["registrant"],
                        "vcardArray": [
                            "vcard",
                            [
                                ["fn", {}, "text", "Google LLC"],
                                ["email", {}, "text", "noc@ejemplo.com"],
                            ],
                        ],
                    },
                    {
                        "roles": ["abuse"],
                        "vcardArray": ["vcard", [["fn", {}, "text", "REDACTED FOR PRIVACY"]]],
                    },
                ],
            },
        )
    )
    fs = await Rdap().run(IP, ctx)
    assert "GOGL" in fs[0].title
    assert {f.entity.key for f in fs[1:]} == {"empresa:google llc", "email:noc@ejemplo.com"}
    respx.get("https://rdap.org/domain/noexiste.es").mock(return_value=httpx.Response(404))
    (nx,) = await Rdap().run(Entity(type=EntityType.DOMINIO, value="noexiste.es"), ctx)
    assert "Sin registro" in nx.title
    respx.get("https://rdap.org/domain/error.es").mock(return_value=httpx.Response(400))
    with pytest.raises(HttpError):
        await Rdap().run(Entity(type=EntityType.DOMINIO, value="error.es"), ctx)


@respx.mock
async def test_crtsh(ctx: SourceContext) -> None:
    datos = fixture_json("crtsh_example_com.json")
    datos.append(
        {"name_value": "*.wild.example.com\nexample.com\notro.es", "not_before": "2020-01-01"}
    )
    respx.get("https://crt.sh/").mock(return_value=httpx.Response(200, json=datos))
    fs = await CrtSh().run(DOM, ctx)
    subs = {f.entity.value for f in fs}
    assert "wild.example.com" in subs and "example.com" not in subs and "otro.es" not in subs


@respx.mock
async def test_crtsh_caido(ctx: SourceContext) -> None:
    respx.get("https://crt.sh/").mock(return_value=httpx.Response(404))
    with pytest.raises(HttpError, match=r"crt\.sh"):
        await CrtSh().run(DOM, ctx)


@respx.mock
async def test_wayback(ctx: SourceContext) -> None:
    cab = ["timestamp", "original", "statuscode"]
    respx.get("https://web.archive.org/cdx/search/cdx", params={"limit": "1"}).mock(
        return_value=httpx.Response(
            200, json=[cab, ["20020120142510", "http://example.com:80/", "200"]]
        )
    )
    respx.get("https://web.archive.org/cdx/search/cdx", params={"limit": "-1"}).mock(
        return_value=httpx.Response(
            200, json=[cab, ["20261120000000", "http://example.com/", "200"]]
        )
    )
    (f,) = await Wayback().run(DOM, ctx)
    assert "2002-01-20" in f.title and "2026-11-20" in f.title


@respx.mock
async def test_wayback_sin_capturas(ctx: SourceContext) -> None:
    respx.get("https://web.archive.org/cdx/search/cdx").mock(
        return_value=httpx.Response(200, json=[])
    )
    assert await Wayback().run(DOM, ctx) == []


@respx.mock
async def test_tecnologias(ctx: SourceContext) -> None:
    html = (
        '<html><head><title>Mi web</title><meta name="generator" content="WordPress 6.5">'
        '<script src="/wp-content/x.js"></script></head><body>'
        '<a href="https://www.linkedin.com/company/ejemplo">in</a>'
        '<a href="https://otra.es">otra</a></body></html>'
    )
    respx.get("https://example.com/").mock(
        return_value=httpx.Response(
            200, text=html, headers={"content-type": "text/html", "server": "nginx", "cf-ray": "1"}
        )
    )
    fs = await Tecnologias().run(DOM, ctx)
    tec = fs[0].data["tecnologias"]
    assert {"nginx", "Cloudflare", "WordPress", "WordPress 6.5"} <= set(tec)
    assert fs[0].data["titulo"] == "Mi web"
    assert [f.entity.value for f in fs[1:]] == ["https://www.linkedin.com/company/ejemplo"]


@respx.mock
async def test_ipinfo(ctx: SourceContext) -> None:
    ctx.dns = FakeDns({("8.8.8.8", "PTR"): ["dns.google"]})
    respx.get("https://ipinfo.io/8.8.8.8/json").mock(
        return_value=httpx.Response(200, json=fixture_json("ipinfo_8888.json"))
    )
    fs = await IpInfo().run(IP, ctx)
    assert "Google" in fs[0].title and fs[0].data["ptr"] == ["dns.google"]
    assert [f.entity.value for f in fs[1:]] == ["dns.google"]


@respx.mock
async def test_shodan_y_censys(ctx: SourceContext) -> None:
    from pydantic import SecretStr

    ctx.settings = ctx.settings.model_copy(
        update={
            "shodan_api_key": SecretStr("k"),
            "censys_api_id": SecretStr("i"),
            "censys_api_secret": SecretStr("s"),
        }
    )
    respx.get("https://api.shodan.io/shodan/host/8.8.8.8").mock(
        return_value=httpx.Response(
            200,
            json={
                "ports": [53, 443],
                "data": [
                    {"port": 53, "transport": "udp", "product": "Google DNS"},
                    {"port": 443, "transport": "tcp"},
                ],
            },
        )
    )
    (s,) = await Shodan().run(IP, ctx)
    assert "53/udp Google DNS" in s.title
    censys = respx.get("https://search.censys.io/api/v2/hosts/8.8.8.8").mock(
        return_value=httpx.Response(
            200, json={"result": {"services": [{"port": 53, "service_name": "DNS"}]}}
        )
    )
    (c,) = await Censys().run(IP, ctx)
    assert "53/DNS" in c.title
    assert censys.calls.last.request.headers["authorization"].startswith("Basic ")
    respx.get("https://api.shodan.io/shodan/host/1.1.1.1").mock(return_value=httpx.Response(404))
    assert await Shodan().run(Entity(type=EntityType.IP, value="1.1.1.1"), ctx) == []


# ---------------------------------------------------------------- email


async def test_dns_correo(ctx: SourceContext) -> None:
    ctx.dns = FakeDns(
        {
            ("ejemplo.es", "MX"): ["10 mx.ejemplo.es"],
            ("ejemplo.es", "A"): ["93.184.215.14"],
            ("ejemplo.es", "TXT"): ["v=spf1 include:_spf.google.com ~all", "otra cosa"],
            ("_dmarc.ejemplo.es", "TXT"): ["v=DMARC1; p=reject; rua=mailto:x@ejemplo.es"],
            ("google._domainkey.ejemplo.es", "TXT"): ["v=DKIM1; k=rsa; p=MIIB"],
        }
    )
    fs = await DnsCorreo().run(EMAIL, ctx)
    d = fs[0].data
    assert d["dmarc_politica"] == "reject" and d["dkim_selectores"] == ["google"]
    assert d["suplantable"] is False and "correctos" in fs[0].title
    assert {f.entity.key for f in fs[1:]} == {"dominio:mx.ejemplo.es", "ip:93.184.215.14"}

    ctx.dns = FakeDns({("debil.es", "TXT"): ["v=spf1 +all"], ("debil.es", "MX"): ["0 ."]})
    (f,) = await DnsCorreo().run(Entity(type=EntityType.DOMINIO, value="debil.es"), ctx)
    assert "+all" in f.title and "sin DMARC" in f.title and "MX nulo" in f.title
    assert f.data["suplantable"] is True


@respx.mock
async def test_gravatar(ctx: SourceContext) -> None:
    email = b"ana@ejemplo.es"
    sha = hashlib.sha256(email).hexdigest()
    md5 = hashlib.md5(email, usedforsecurity=False).hexdigest()
    respx.get(f"https://gravatar.com/avatar/{sha}").mock(
        return_value=httpx.Response(200, content=b"img")
    )
    respx.get(f"https://gravatar.com/{md5}.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "entry": [
                    {
                        "preferredUsername": "anaejemplo",
                        "displayName": "Ana E.",
                        "profileUrl": "https://gravatar.com/anaejemplo",
                        "accounts": [
                            {"shortname": "github", "url": "https://github.com/anaejemplo"}
                        ],
                    }
                ]
            },
        )
    )
    fs = await Gravatar().run(EMAIL, ctx)  # el email se normaliza a minúsculas para el hash
    assert {f.relation for f in fs} == {
        "avatar_gravatar",
        "usuario_gravatar",
        "nombre_mostrado",
        "cuenta_verificada_gravatar",
    }
    respx.get(f"https://gravatar.com/avatar/{hashlib.sha256(b'x@y.es').hexdigest()}").mock(
        return_value=httpx.Response(404)
    )
    assert await Gravatar().run(Entity(type=EntityType.EMAIL, value="x@y.es"), ctx) == []


@respx.mock
async def test_pgp(ctx: SourceContext) -> None:
    respx.get("https://keys.openpgp.org/vks/v1/by-email/Ana@Ejemplo.es").mock(
        return_value=httpx.Response(200, content=b"-----BEGIN PGP PUBLIC KEY BLOCK-----\n...")
    )
    (f,) = await Pgp().run(EMAIL, ctx)
    assert f.relation == "clave_pgp" and f.evidence
    respx.get("https://keys.openpgp.org/vks/v1/by-email/x@y.es").mock(
        return_value=httpx.Response(404)
    )
    assert await Pgp().run(Entity(type=EntityType.EMAIL, value="x@y.es"), ctx) == []


@respx.mock
async def test_hibp_y_emailrep(ctx: SourceContext) -> None:
    from pydantic import SecretStr

    ctx.settings = ctx.settings.model_copy(
        update={
            "hibp_api_key": SecretStr("h"),
            "emailrep_api_key": SecretStr("e"),
            "max_retries": 0,
        }
    )
    ruta = respx.get(url__startswith="https://haveibeenpwned.com/api/v3/breachedaccount/").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "Name": "Ficticia",
                    "Title": "Ficticia",
                    "Domain": "ficticia.com",
                    "BreachDate": "2020-01-01",
                    "DataClasses": ["Email addresses", "Passwords"],
                }
            ],
        )
    )
    (b,) = await Hibp().run(EMAIL, ctx)
    assert b.entity.type == EntityType.DOCUMENTO and "Passwords" in b.description
    assert ruta.calls.last.request.headers["hibp-api-key"] == "h"
    for codigo, esperado in ((404, None), (401, "401")):
        respx.get(url__startswith="https://haveibeenpwned.com/api/v3/breachedaccount/").mock(
            return_value=httpx.Response(codigo)
        )
        if esperado:
            with pytest.raises(HttpError, match=esperado):
                await Hibp().run(Entity(type=EntityType.EMAIL, value=f"c{codigo}@y.es"), ctx)
        else:
            assert await Hibp().run(Entity(type=EntityType.EMAIL, value="c404@y.es"), ctx) == []
    respx.get("https://emailrep.io/Ana@Ejemplo.es").mock(
        return_value=httpx.Response(
            200,
            json={
                "reputation": "high",
                "suspicious": False,
                "details": {"profiles": ["github"], "spoofable": False},
            },
        )
    )
    (r,) = await EmailRep().run(EMAIL, ctx)
    assert "high" in r.title and r.data["profiles"] == ["github"]
    respx.get("https://emailrep.io/x@y.es").mock(return_value=httpx.Response(429, text="limite"))
    with pytest.raises(HttpError):
        await EmailRep().run(Entity(type=EntityType.EMAIL, value="x@y.es"), ctx)
    assert not Hibp().availability(ctx.settings.model_copy(update={"hibp_api_key": None}))[0]


# ----------------------------------------------------------- WhatsMyName

SITIO: dict[str, Any] = {
    "name": "Ficticia",
    "uri_check": "https://ficticia.es/u/{account}",
    "e_code": 200,
    "e_string": "perfil-de",
    "m_code": 404,
    "m_string": "No existe",
    "cat": "social",
}


def test_evaluar_wmn() -> None:
    assert evaluar_wmn(SITIO, 200, "<div class=perfil-de>") is True
    assert evaluar_wmn(SITIO, 404, "No existe") is False
    assert (
        evaluar_wmn(SITIO, 200, "pagina generica") is None
    )  # 200 sin el texto: no se da por buena
    assert evaluar_wmn({**SITIO, "m_code": 200}, 200, "perfil-de No existe") is None
    assert evaluar_wmn({**SITIO, "m_string": ""}, 404, "") is False


@respx.mock
async def test_whatsmyname(ctx: SourceContext, monkeypatch: pytest.MonkeyPatch) -> None:
    sitios = [
        SITIO,
        {
            **SITIO,
            "name": "Protegida",
            "uri_check": "https://protegida.es/{account}",
            "protection": ["cloudflare"],
        },
        {
            **SITIO,
            "name": "PorPost",
            "uri_check": "https://post.es/api",
            "post_body": '{"u":"{account}"}',
        },
        {**SITIO, "name": "NoExiste", "uri_check": "https://noexiste.es/{account}"},
        {**SITIO, "name": "Caida", "uri_check": "https://caida.es/{account}"},
        {**SITIO, "name": "Adultos", "uri_check": "https://nsfw.es/{account}", "cat": "xx NSFW xx"},
        {**SITIO, "name": "Invalida", "uri_check": "https://invalida.es/{account}", "valid": False},
    ]
    monkeypatch.setattr(cuentas, "_cargar_wmn", lambda ruta: {"sites": sitios})
    respx.get("https://ficticia.es/u/pepe").mock(
        return_value=httpx.Response(200, text="perfil-de pepe")
    )
    respx.get("https://protegida.es/pepe").mock(return_value=httpx.Response(200, text="perfil-de"))
    post = respx.post("https://post.es/api").mock(
        return_value=httpx.Response(200, text="perfil-de")
    )
    respx.get("https://noexiste.es/pepe").mock(return_value=httpx.Response(404, text="No existe"))
    respx.get("https://caida.es/pepe").mock(side_effect=httpx.ConnectError("caída"))
    fs = await WhatsMyName().run(Entity(type=EntityType.USERNAME, value="pepe"), ctx)
    por_sitio = {f.data["sitio"]: f for f in fs}
    assert set(por_sitio) == {"Ficticia", "Protegida", "PorPost"}
    assert por_sitio["Ficticia"].confidence == 0.8 and por_sitio["Protegida"].confidence == 0.6
    assert post.calls.last.request.content == b'{"u":"pepe"}'
    assert all(f.evidence for f in fs)
