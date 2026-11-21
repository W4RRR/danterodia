"""Índice local y sus constructores (BOE, BORME, PLACSP, indultos y datos locales)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import respx

from danterodia.http import HttpClient
from danterodia.indices import IndexStore, fts_query
from danterodia.indices.builders import (
    es_empresa,
    importar_locales,
    indexar_boe,
    indexar_borme,
    indexar_indultos,
    indexar_placsp,
    items_sumario_boe,
    leer_civio,
    parsear_actos,
    parsear_feed_placsp,
    parsear_xml_borme,
)
from tests.conftest import FIXTURES, fixture_json, fixture_text

BOE_API = "https://www.boe.es/datosabiertos/api"


def test_fts_query_es_segura() -> None:
    assert fts_query("José Pérez") == '"JOSE" "PEREZ"'
    assert fts_query('a" OR 1=1 --', "titulo").startswith("titulo : (")
    assert '"' not in fts_query('x"y').strip('"').replace('" "', "")


def test_store_insertar_buscar_y_estado(indices: IndexStore) -> None:
    filas = [
        {
            "id": "BOE-A-1",
            "fecha": "2024-11-19",
            "seccion": "I",
            "departamento": "MINISTERIO",
            "epigrafe": "",
            "titulo": "Real Decreto por el que se nombra a don José Muñoz López",
            "url_html": "u",
            "url_pdf": "p",
        },
    ]
    assert indices.insertar("boe_items", filas) == 1
    assert indices.insertar("boe_items", filas) == 0  # duplicado ignorado
    assert [r["id"] for r in indices.buscar("boe_items", "jose munoz", columna="titulo")] == [
        "BOE-A-1"
    ]
    assert indices.buscar("boe_items", "   ") == []
    assert indices.contar("boe_items") == 1
    estado = {e.tabla: e.filas for e in indices.estado()}
    assert estado["boe_items"] == 1 and estado["borme_actos"] == 0
    assert indices.vacio(["borme_actos"]) and not indices.vacio(["boe_items"])
    indices.set_meta("x", "1")
    assert indices.get_meta("x") == "1" and indices.marcado("x") and not indices.marcado("y")


def test_items_sumario_boe() -> None:
    items = list(items_sumario_boe(fixture_json("boe_sumario_20241119.json")))
    assert items and all("identificador" in i and i["titulo"] for i in items)
    assert items[0]["seccion"].startswith("I.")
    assert items[0]["departamento"]


def test_parsear_borme_sintetico() -> None:
    actos = parsear_xml_borme((FIXTURES / "borme_A_sintetico.xml").read_bytes())
    tuplas = [(a.anuncio, a.acto, a.cargo, a.persona) for a in actos]
    assert ("500001", "Nombramientos", "Adm. Solid.", "PEREZ RUIZ ANA MARIA") in tuplas
    assert ("500001", "Nombramientos", "Adm. Solid.", "MUÑOZ LOPEZ JOSE") in tuplas
    assert ("500002", "Ceses/Dimisiones", "Adm. Unico", "GARCIA VEGA LUIS") in tuplas
    assert ("500002", "Nombramientos", "Apoderado", "PEREZ RUIZ ANA MARIA") in tuplas
    assert ("500002", "Nombramientos", "Adm. Unico", "HOLDING INVENTADO SL") in tuplas
    # Frases que no son nombres no se cuelan como «personas».
    assert not any("Administrador" in a.persona for a in actos)
    assert not any(a.anuncio == "500003" for a in actos)
    assert actos[0].empresa == "CONSERVAS FICTICIAS DEL SUR SOCIEDAD LIMITADA"
    assert es_empresa("HOLDING INVENTADO SL") and not es_empresa("PEREZ RUIZ ANA MARIA")
    assert parsear_actos("sin formato", "Nombramientos. Adm. Unico: X.") == []


def test_parsear_feed_placsp() -> None:
    filas, siguiente = parsear_feed_placsp((FIXTURES / "placsp_sintetico.atom").read_bytes())
    assert siguiente == "https://contrataciondelsectorpublico.gob.es/feed_2.atom"
    assert len(filas) == 1
    f = filas[0]
    assert f["adjudicatario"] == "PAPELERIA INVENTADA SL" and f["nif"] == "B00000000"
    assert f["organo"] == "Ayuntamiento de Villaficticia" and f["importe"] == "12500.00"
    assert f["fecha"] == "2026-11-10" and f["objeto"].startswith("Suministro")


@respx.mock
async def test_indexar_boe_incremental(http: HttpClient, indices: IndexStore) -> None:
    ruta = respx.get(f"{BOE_API}/boe/sumario/20241119").mock(
        return_value=httpx.Response(200, json=fixture_json("boe_sumario_20241119.json"))
    )
    respx.get(f"{BOE_API}/boe/sumario/20241120").mock(return_value=httpx.Response(404))
    respx.get(f"{BOE_API}/boe/sumario/20241121").mock(return_value=httpx.Response(500))
    mensajes: list[str] = []
    n = await indexar_boe(http, indices, date(2024, 11, 19), date(2024, 11, 21), mensajes.append)
    assert n > 0 and indices.contar("boe_items") == n
    assert indices.marcado("boe:20241119") and indices.marcado("boe:20241120")
    assert not indices.marcado("boe:20241121")  # se reintentará
    assert any("500" in m for m in mensajes)
    # Segunda ejecución: el día ya indexado no se vuelve a pedir.
    await indexar_boe(http, indices, date(2024, 11, 19), date(2024, 11, 19))
    assert ruta.call_count == 1
    assert ruta.calls.last.request.headers["accept"] == "application/json"


@respx.mock
async def test_indexar_borme(http: HttpClient, indices: IndexStore) -> None:
    sumario = fixture_json("borme_sumario_20241119.json")
    respx.get(f"{BOE_API}/borme/sumario/20241119").mock(
        return_value=httpx.Response(200, json=sumario)
    )
    items = sumario["data"]["sumario"]["diario"][0]["seccion"][0]["item"]
    xml = (FIXTURES / "borme_A_sintetico.xml").read_bytes()
    respx.get(items[0]["url_xml"]).mock(return_value=httpx.Response(200, content=xml))
    respx.get(items[1]["url_xml"]).mock(return_value=httpx.Response(503))
    n = await indexar_borme(http, indices, date(2024, 11, 19), date(2024, 11, 19))
    assert n == 5
    assert not indices.marcado("borme:20241119")  # una provincia falló: el día queda pendiente
    fila = indices.buscar("borme_actos", "Ana María Pérez Ruiz", columna="persona")[0]
    assert fila["provincia"] == items[0]["titulo"] and "txt.php" in fila["url"]

    # Filtro por provincia: solo se descarga la indicada.
    respx.get(f"{BOE_API}/borme/sumario/20241118").mock(
        return_value=httpx.Response(200, json=sumario)
    )
    await indexar_borme(
        http, indices, date(2024, 11, 18), date(2024, 11, 18), provincias=[items[0]["titulo"]]
    )
    assert indices.marcado(f"borme:20241118:{items[0]['titulo'].upper()}")


@respx.mock
async def test_indexar_placsp_sigue_paginas(http: HttpClient, indices: IndexStore) -> None:
    contenido = (FIXTURES / "placsp_sintetico.atom").read_bytes()
    from danterodia.indices.builders import PLACSP_FEED

    respx.get(PLACSP_FEED).mock(return_value=httpx.Response(200, content=contenido))
    segunda = respx.get("https://contrataciondelsectorpublico.gob.es/feed_2.atom").mock(
        return_value=httpx.Response(404)
    )
    assert await indexar_placsp(http, indices, paginas=3) == 1
    assert segunda.call_count == 1
    assert indices.buscar_exacto("contratos", "nif", "B00000000")[0]["origen"] == "PLACSP"


def test_leer_civio_unificado() -> None:
    filas = leer_civio()
    assert len(filas) == 10582
    assert {f["gender"] for f in filas} <= {"Hombre", "Mujer", ""}
    assert all(f["id"].startswith("BOE-A-") for f in filas)


@respx.mock
async def test_indexar_indultos_reanudable(http: HttpClient, indices: IndexStore) -> None:
    primeros = leer_civio()[:2]
    for f in primeros:
        respx.get(f"https://www.boe.es/diario_boe/xml.php?id={f['id']}").mock(
            return_value=httpx.Response(
                200,
                content=f"<documento><metadatos><titulo>Real Decreto por el que se indulta a "
                f"don Juan Inventado {f['id']}</titulo></metadatos></documento>".encode(),
            )
        )
    respx.get(url__startswith="https://www.boe.es/diario_boe/xml.php").mock(
        return_value=httpx.Response(404)
    )
    assert await indexar_indultos(http, indices, limite=2) == 2
    # Segunda ejecución: los dos ya están; los siguientes fallan (404) y no se insertan.
    assert await indexar_indultos(http, indices, limite=2) == 0
    assert indices.contar("indultos") == 2
    fila = indices.buscar("indultos", "Juan Inventado", columna="titulo")[0]
    assert fila["delito"] and fila["ministro"]


def _pdf_minimo(texto: str) -> bytes:
    """PDF de una página con una línea de texto (sin dependencias externas)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({texto}) Tj ET".encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return bytes(out)


def test_importar_locales(tmp_path: Path, indices: IndexStore) -> None:
    data = tmp_path / "data"
    adj = data / "Adjudicaciones"
    (adj / "Cádiz").mkdir(parents=True)
    (adj / "Sevilla").mkdir()
    (data / "Testra").mkdir()
    (adj / "Cádiz" / "contratos_menores.json").write_text(
        json.dumps(
            {
                "resources": [
                    {
                        "ca:referenciaContrato": "1/2015",
                        "ca:objeto": "Sillas",
                        "ca:entidadContratante": "DIPUTACION",
                        "ca:importeAdjudicacion": "61.44",
                        "ca:adjudicatario": "MUEBLES INVENTADOS SL ",
                        "ca:fechaFormalizacion": "2015-01-01",
                        "uri": "https://ejemplo/1",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (adj / "adjudicaciones.csv").write_text(
        '{ "data" : { "Adjudicatario" : "OBRAS FICTICIAS SA", "NIF" : "a58818501", "site" : "Andalucia", "Date" : "16/05/2008", "Importe" : "211.876,07€" } }\n\n',
        encoding="utf-8",
    )
    (adj / "Sevilla" / "contratos.pdf").write_bytes(_pdf_minimo("Adjudicatario OBRAS FICTICIAS SA"))
    (adj / "Sevilla" / "roto.pdf").write_bytes(b"no es un pdf")
    (data / "Testra" / "edicto.pdf").write_bytes(_pdf_minimo("SANCIONADO PEPE INVENTADO"))

    r = importar_locales(indices, data)
    assert r == {"cadiz": 1, "adjudicaciones": 1, "paginas_pdf": 1}
    assert indices.buscar_exacto("contratos", "nif", "A58818501")[0]["importe"] == "211.876,07"
    assert indices.buscar("documentos", "OBRAS FICTICIAS")[0]["sensible"] == 0
    assert indices.buscar("documentos", "PEPE INVENTADO") == []  # TESTRA no se importa por defecto

    r = importar_locales(indices, data, incluir_testra=True)
    assert r["paginas_pdf"] == 1
    assert indices.buscar("documentos", "PEPE INVENTADO")[0]["sensible"] == 1


def test_fixture_texto() -> None:
    assert "CONSERVAS" in fixture_text("borme_A_sintetico.xml")
