"""Buscadores, prensa, verificación de noticias, informe HTML y comandos de índice."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr
from typer.testing import CliRunner

from danterodia import search
from danterodia.cli import app
from danterodia.http import HttpClient, HttpError
from danterodia.models import Case, Entity, EntityType, Evidence, Finding
from danterodia.reports import to_html
from danterodia.search import SearchResult, cargar_dorks, render_dorks
from danterodia.sources import SourceContext
from danterodia.sources.web import (
    Articulo,
    Buscadores,
    Prensa,
    VerificarNoticia,
    clasificar,
    es_anuncio,
    es_verificacion,
    puntuar,
    relevante,
    tfidf_coseno,
    variables_dork,
)
from tests.conftest import fixture_json, fixture_text

ARTICULO = "https://www.elpais.com/espana/2026-11-18/presupuesto.html"


def _ddg_falso(resultados: dict[str, list[dict[str, Any]]]) -> Any:
    """Sustituye a DuckDuckGo: devuelve resultados según una palabra clave de la consulta."""

    def fake(metodo: str, query: str, *args: Any) -> list[dict[str, Any]]:
        for clave, filas in resultados.items():
            if (clave in query and (metodo == "news") == clave.startswith("news:")) or (
                clave.removeprefix("news:") in query
                and metodo == "news"
                and clave.startswith("news:")
            ):
                return filas
        return []

    return fake


# ------------------------------------------------------------ utilidades


def test_clasificar_anuncios_y_relevancia() -> None:
    assert clasificar("https://www.elpais.com/x") == "prensa"
    assert clasificar("https://m.elmundo.es/x") == "prensa"
    assert clasificar("https://es.linkedin.com/in/x") == "redes_sociales"
    assert clasificar("https://ejemplo.org") == "otros"
    assert es_anuncio("https://www.bing.com/aclick?ld=1") and not es_anuncio("https://bing.com/x")
    p = Entity(type=EntityType.PERSONA, value="Ana Pérez Ruiz")
    assert relevante(p, "u", "Entrevista a ANA PEREZ RUIZ") and not relevante(p, "u", "Ana Ruiz")
    d = Entity(type=EntityType.DOMINIO, value="ejemplo.es")
    assert relevante(d, "https://www.ejemplo.es/a", "") and relevante(
        d, "https://otro.es", "ver ejemplo.es"
    )
    assert not relevante(d, "https://otro.es", "nada")
    t = Entity(type=EntityType.TELEFONO, value="+34 600 11 22 33")
    assert relevante(t, "u", "llama al 600-112-233") and not relevante(t, "u", "600 11 22 34")
    assert relevante(Entity(type=EntityType.USERNAME, value="Pepe"), "u", "@pepe dijo")


def test_dorks_y_variables(settings: Any) -> None:
    dorks = cargar_dorks()
    assert set(dorks) >= {"persona", "empresa", "username", "email", "telefono", "dominio"}
    v = variables_dork(Entity(type=EntityType.TELEFONO, value="600112233"))
    assert v["e164"] == "+34600112233" and v["compacto"] == "600112233"
    v = variables_dork(Entity(type=EntityType.EMAIL, value="ana@ejemplo.es"))
    assert v["usuario"] == "ana" and v["dominio"] == "ejemplo.es"
    assert render_dorks(['"{valor}"', '"{e164}"', '"{valor}"'], {"valor": "x"}) == ['"x"']
    (settings.data_dir / "dorks.toml").write_text(
        "[persona]\nplantillas = ['\"{valor}\" propio']\n", encoding="utf-8"
    )
    assert cargar_dorks(settings.data_dir)["persona"] == ['"{valor}" propio']
    assert variables_dork(Entity(type=EntityType.TELEFONO, value="abc")) == {"valor": "abc"}


def test_tfidf_y_puntuacion() -> None:
    a = "el ayuntamiento aprueba el presupuesto municipal de vivienda"
    b = "el ayuntamiento aprobó ayer el presupuesto municipal para vivienda y transporte"
    c = "receta de tortilla de patatas con cebolla"
    corpus = [a, b, c]
    assert tfidf_coseno(a, b, corpus) > tfidf_coseno(a, c, corpus)
    assert tfidf_coseno("", "x", ["", "x"]) == 0.0

    buena = Articulo(
        url="https://www.elpais.com/x",
        titulo="Aprobado el presupuesto",
        texto="Según el informe de la intervención",
        autores=["Lucía"],
        fecha="2026-11-18",
    )
    puntos, senales = puntuar(buena, corroboraciones=2, verificaciones=[])
    assert puntos >= 80 and {s.nombre for s in senales} >= {
        "medio_conocido",
        "corroborada",
        "con_autor",
    }
    mala = Articulo(
        url="http://bulos.example/x",
        titulo="¡¡URGENTE!! LO QUE NO QUIEREN QUE SEPAS",
        texto="compártelo ya, censurado",
    )
    puntos, senales = puntuar(mala, 0, [{"valoracion": "Falso: es un bulo"}])
    nombres = {s.nombre for s in senales}
    assert (
        puntos <= 10
        and {
            "desmentida",
            "lenguaje_sensacionalista",
            "titular_mayusculas",
            "exclamaciones",
            "sin_corroborar",
            "sin_fecha",
            "sin_autor",
        }
        <= nombres
    )
    puntos, senales = puntuar(mala, 1, [{"valoracion": "Contexto"}])
    assert {"corroborada_parcial", "verificaciones_relacionadas"} <= {s.nombre for s in senales}


def test_es_verificacion() -> None:
    titulo = "El Ayuntamiento de Villaficticia aprueba el presupuesto de 2027"
    ok = SearchResult(
        title="Es falso que Villaficticia apruebe el presupuesto de 2027",
        url="https://maldita.es/x",
        snippet="presupuesto Villaficticia",
        engine="ddg",
    )
    assert es_verificacion(ok, "maldita.es", titulo)
    otro_sub = SearchResult(
        title=ok.title, url="https://transparentia.newtral.es/x", snippet="", engine="ddg"
    )
    assert not es_verificacion(otro_sub, "newtral.es", titulo)
    rtve = SearchResult(
        title=ok.title, url="https://www.rtve.es/noticias/x", snippet="", engine="ddg"
    )
    assert not es_verificacion(rtve, "rtve.es", titulo)
    nada = SearchResult(title="El cautivo", url="https://maldita.es/y", snippet="", engine="ddg")
    assert not es_verificacion(nada, "maldita.es", titulo)


# ----------------------------------------------------------- buscadores


@respx.mock
async def test_motores_oficiales(http: HttpClient) -> None:
    respx.get("https://api.search.brave.com/res/v1/web/search").mock(
        return_value=httpx.Response(
            200,
            json={"web": {"results": [{"title": "B", "url": "https://b.es", "description": "d"}]}},
        )
    )
    respx.get("https://www.googleapis.com/customsearch/v1").mock(
        return_value=httpx.Response(
            200, json={"items": [{"title": "G", "link": "https://g.es", "snippet": "s"}]}
        )
    )
    respx.get("https://api.mojeek.com/search").mock(
        return_value=httpx.Response(
            200,
            json={"response": {"results": [{"title": "M", "url": "https://m.es", "desc": "x"}]}},
        )
    )
    assert (await search.brave(http, "k", "q"))[0].engine == "brave"
    assert (await search.google_cse(http, "k", "cx", "q"))[0].url == "https://g.es"
    assert (await search.mojeek_api(http, "k", "q"))[0].title == "M"
    respx.get("https://api.search.brave.com/res/v1/web/search").mock(
        return_value=httpx.Response(401)
    )
    with pytest.raises(HttpError):
        await search.brave(http, "k", "otra")


async def test_ddg_filtra_urls_relativas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        search,
        "_ddg_sync",
        lambda *a: [
            {"title": "ok", "href": "https://a.es", "body": "b"},
            {"title": "rel", "href": "/go/v1/x"},
            {"title": "news", "url": "https://n.es", "date": "2026-11-18", "source": "N"},
        ],
    )
    r = await search.ddg("q", news=True)
    assert [x.url for x in r] == ["https://a.es", "https://n.es"] and r[0].engine == "ddg_news"


@respx.mock
async def test_buscadores(ctx: SourceContext, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        search,
        "_ddg_sync",
        lambda metodo, q, *a: [
            {
                "title": "Ana Pérez Ruiz, nueva directora",
                "href": "https://www.elpais.com/a",
                "body": "contacto ana@ejemplo.es",
            },
            {
                "title": "Perfil",
                "href": "https://es.linkedin.com/in/ana",
                "body": "Ana Pérez Ruiz - LinkedIn",
            },
            {"title": "Otra Ana", "href": "https://otra.es", "body": "Ana Ruiz"},
            {"title": "Anuncio", "href": "https://www.bing.com/aclick?x", "body": "Ana Pérez Ruiz"},
        ],
    )
    ctx.settings = ctx.settings.model_copy(update={"brave_api_key": SecretStr("k")})
    respx.get("https://api.search.brave.com/res/v1/web/search").mock(
        return_value=httpx.Response(500)
    )
    fs = await Buscadores().run(Entity(type=EntityType.PERSONA, value="Ana Pérez Ruiz"), ctx)
    urls = {f.entity.value: f.data.get("categoria") for f in fs if f.entity.type == EntityType.URL}
    assert urls == {
        "https://www.elpais.com/a": "prensa",
        "https://es.linkedin.com/in/ana": "redes_sociales",
    }
    assert any(f.entity.key == "email:ana@ejemplo.es" for f in fs)


# ------------------------------------------------------------ prensa


@respx.mock
async def test_prensa(ctx: SourceContext, monkeypatch: pytest.MonkeyPatch) -> None:
    filas = [
        {
            "title": "El Ayuntamiento de Villaficticia aprueba el presupuesto de 2027",
            "url": ARTICULO,
            "body": "Marta Ficticia Pérez",
            "date": "2026-11-18",
        },
        {
            "title": "El Ayuntamiento de Villaficticia aprueba el presupuesto de 2027 ",
            "url": "https://www.abc.es/copia",
            "body": "duplicado",
        },
        {"title": "Blog", "url": "https://blog.example/x", "body": "Marta Ficticia Pérez"},
    ]
    monkeypatch.setattr(
        search, "_ddg_sync", lambda metodo, q, *a: filas if metodo == "news" else []
    )
    respx.get(ARTICULO).mock(
        return_value=httpx.Response(
            200,
            text=fixture_text("articulo.html"),
            headers={"content-type": "text/html; charset=utf-8"},
        )
    )
    (f,) = await Prensa().run(Entity(type=EntityType.PERSONA, value="Marta Ficticia Pérez"), ctx)
    assert f.entity.value == ARTICULO and "Villaficticia" in f.title
    assert f.data["emails"] == ["prensa@villaficticia.es"] and f.data["telefonos"] == [
        "+34954000000"
    ]
    assert f.evidence and f.data["coincidencia"] == "nombre"


@respx.mock
async def test_prensa_articulo_bloqueado_usa_wayback(
    ctx: SourceContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        search,
        "_ddg_sync",
        lambda metodo, q, *a: (
            [{"title": "Marta Ficticia Pérez en el pleno", "url": ARTICULO, "body": "x"}]
            if metodo == "news"
            else []
        ),
    )
    respx.get(ARTICULO).mock(return_value=httpx.Response(403))
    respx.get(f"https://web.archive.org/web/2id_/{ARTICULO}").mock(
        return_value=httpx.Response(
            200, text=fixture_text("articulo.html"), headers={"content-type": "text/html"}
        )
    )
    (f,) = await Prensa().run(Entity(type=EntityType.PERSONA, value="Marta Ficticia Pérez"), ctx)
    assert f.entity.value == ARTICULO and f.evidence[0].url.startswith("https://web.archive.org/")


# ----------------------------------------------------- verificar noticia


@respx.mock
async def test_verificar_noticia(ctx: SourceContext, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(metodo: str, query: str, *a: Any) -> list[dict[str, Any]]:
        if query.startswith("site:maldita.es"):
            return [
                {
                    "title": "Falso: Villaficticia no aprueba el presupuesto de 2027",
                    "href": "https://maldita.es/bulo",
                    "body": "Es un bulo sobre el presupuesto de Villaficticia",
                }
            ]
        if query.startswith("site:"):
            return []
        if metodo == "news":
            return [
                {
                    "title": "Villaficticia aprueba su presupuesto para 2027",
                    "url": "https://www.abc.es/v",
                    "body": "presupuesto",
                }
            ]
        return [
            {
                "title": "El Ayuntamiento de Villaficticia aprueba el presupuesto de 2027",
                "href": "https://www.elmundo.es/v",
                "body": "x",
            },
            {"title": "Mismo medio", "href": "https://www.elpais.com/otra", "body": "x"},
        ]

    monkeypatch.setattr(search, "_ddg_sync", fake)
    html = {"content-type": "text/html; charset=utf-8"}
    respx.get(ARTICULO).mock(
        return_value=httpx.Response(200, text=fixture_text("articulo.html"), headers=html)
    )
    respx.get("https://www.elmundo.es/v").mock(
        return_value=httpx.Response(200, text=fixture_text("articulo.html"), headers=html)
    )
    respx.get("https://www.abc.es/v").mock(return_value=httpx.Response(500))
    ctx.settings = ctx.settings.model_copy(
        update={"factcheck_api_key": SecretStr("k"), "max_retries": 0}
    )
    respx.get("https://factchecktools.googleapis.com/v1alpha1/claims:search").mock(
        return_value=httpx.Response(
            200,
            json={
                "claims": [
                    {
                        "text": "x",
                        "claimReview": [
                            {
                                "publisher": {"name": "Newtral"},
                                "url": "https://www.newtral.es/c",
                                "textualRating": "Engañoso",
                            }
                        ],
                    }
                ]
            },
        )
    )

    fs = await VerificarNoticia().run(Entity(type=EntityType.URL, value=ARTICULO), ctx)
    puntuacion = fs[0]
    assert puntuacion.relation == "puntuacion_verificacion"
    nombres = {s["senal"] for s in puntuacion.data["senales"]}
    assert "desmentida" in nombres and "con_autor" in nombres
    assert puntuacion.data["puntuacion"] < 65
    relaciones = [f.relation for f in fs]
    assert "misma_historia" in relaciones and relaciones.count("verificacion") == 2
    assert all("elpais.com" not in f.entity.value for f in fs if f.relation == "misma_historia")


@respx.mock
async def test_verificar_noticia_sin_articulo(ctx: SourceContext) -> None:
    respx.get("https://www.ejemplo.es/vacia").mock(
        return_value=httpx.Response(
            200, text="<html><body></body></html>", headers={"content-type": "text/html"}
        )
    )
    (f,) = await VerificarNoticia().run(
        Entity(type=EntityType.URL, value="https://www.ejemplo.es/vacia"), ctx
    )
    assert "No se pudo extraer" in f.title


# ------------------------------------------------------------ informe HTML


def test_html_autocontenido_y_escapado() -> None:
    t = Entity(type=EntityType.PERSONA, value="Ana <b>Pérez</b>")
    malicioso = Finding(
        source="buscadores",
        entity=Entity(type=EntityType.URL, value="https://x.es"),
        related_to=t.key,
        title="<script>alert(1)</script>",
        confidence=0.4,
        evidence=[Evidence(url="https://x.es", sha256="a" * 64, path="evidencias/a.html")],
        data={
            "fecha": "2026-11-18",
            "ajuste_confianza": "×0.5",
            "confianza_original": 0.8,
            "senales": [{"senal": "s", "puntos": 5, "explicacion": "ok"}],
        },
    )
    html = to_html(Case(target=t, purpose="Prueba del informe", findings=[malicioso]))
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;alert(1)" in html
    assert "Ana &lt;b&gt;Pérez&lt;/b&gt;" in html
    assert "vis-network" in html and 'id="datos-grafo"' in html
    assert "a" * 64 in html and "2026-11-18" in html and "×0.5" in html
    assert (
        "https://" not in html.split("<script>")[0].split("<style>")[1].split("</style>")[0]
    )  # sin CSS externo


# --------------------------------------------------------------- CLI índice

runner = CliRunner()


@pytest.fixture
def entorno(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("DANTERODIA_DATA_DIR", str(tmp_path / "datos"))
    monkeypatch.setenv("DANTERODIA_PER_DOMAIN_INTERVAL", "0")
    monkeypatch.setenv("COLUMNS", "200")
    return tmp_path


@respx.mock
def test_cli_indices(entorno: Path) -> None:
    respx.get("https://www.boe.es/datosabiertos/api/boe/sumario/20241119").mock(
        return_value=httpx.Response(200, json=fixture_json("boe_sumario_20241119.json"))
    )
    r = runner.invoke(app, ["indice", "boe", "--desde", "2024-11-19", "--hasta", "2024-11-19"])
    assert r.exit_code == 0, r.output
    assert "registros nuevos" in r.output
    r = runner.invoke(app, ["indice", "estado"])
    assert r.exit_code == 0 and "boe_items" in r.output

    respx.get("https://www.boe.es/datosabiertos/api/borme/sumario/20241119").mock(
        return_value=httpx.Response(404)
    )
    r = runner.invoke(
        app, ["indice", "borme", "--desde", "2024-11-19", "--hasta", "2024-11-19", "-p", "SEVILLA"]
    )
    assert r.exit_code == 0

    from danterodia.indices.builders import PLACSP_FEED

    respx.get(PLACSP_FEED).mock(return_value=httpx.Response(500))
    assert runner.invoke(app, ["indice", "placsp", "--paginas", "1"]).exit_code == 0
    respx.get(url__startswith="https://www.boe.es/diario_boe/xml.php").mock(
        return_value=httpx.Response(404)
    )
    assert runner.invoke(app, ["indice", "indultos", "--limite", "1"]).exit_code == 0

    datos = entorno / "data"
    (datos / "Adjudicaciones").mkdir(parents=True)
    r = runner.invoke(app, ["indice", "locales", "--datos", str(datos)])
    assert r.exit_code == 0 and "paginas_pdf" in r.output
    assert runner.invoke(app, ["indice", "locales", "--datos", str(entorno / "no")]).exit_code != 0

    for malo in (
        ["--desde", "2024-13-01"],
        ["--desde", "2024-11-20", "--hasta", "2024-11-19"],
        ["--desde", "2020-01-01", "--hasta", "2024-01-01"],
    ):
        assert runner.invoke(app, ["indice", "boe", *malo]).exit_code != 0


@respx.mock
def test_cli_actualizar_wmn(entorno: Path) -> None:
    from danterodia.sources.cuentas import WMN_URL

    respx.get(WMN_URL).mock(return_value=httpx.Response(200, json={"sites": [{"name": "x"}]}))
    r = runner.invoke(app, ["actualizar-wmn"])
    assert r.exit_code == 0 and "1 sitios" in r.output
    assert (entorno / "datos" / "wmn-data.json").exists()
    respx.get(WMN_URL).mock(return_value=httpx.Response(404))
    assert runner.invoke(app, ["actualizar-wmn"]).exit_code != 0
