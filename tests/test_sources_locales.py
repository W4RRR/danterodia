"""Fuentes sin conexión y fuentes que consultan el índice local."""

from __future__ import annotations

import pytest

from danterodia.correlation import ajustar_por_frecuencia, factor_frecuencia
from danterodia.indices import IndexStore
from danterodia.models import Case, Entity, EntityType, Finding
from danterodia.sources import SourceContext, SourceSkipped
from danterodia.sources.ine import IneFrecuencia, separar_nombre
from danterodia.sources.offline import (
    EmailBasico,
    Identificadores,
    PoliticosCasosJudiciales,
    Telefono,
    dominios_desechables,
    lista_politicos,
)
from danterodia.sources.oficiales import Boe, Borme, Contratacion, DocumentosLocales, Indultos


def P(valor: str, **attrs: object) -> Entity:
    return Entity(type=EntityType.PERSONA, value=valor, attributes=attrs)


def E(valor: str) -> Entity:
    return Entity(type=EntityType.EMPRESA, value=valor)


# ------------------------------------------------------------------ offline


async def test_identificadores(ctx: SourceContext) -> None:
    src = Identificadores()
    (f,) = await src.run(E("B-00000000"), ctx)
    assert f.data["cif"] == "B00000000" and "limitada" in f.title.lower()
    assert await src.run(E("Empresa sin CIF"), ctx) == []
    (dni,) = await src.run(P("Ana", dni="12345678Z"), ctx)
    assert "****5678Z" in dni.title and "12345678Z" not in dni.title  # anonimizado
    (malo,) = await src.run(P("Ana", dni="12345678A"), ctx)
    assert malo.relation == "identificador_invalido"
    assert await src.run(P("Ana"), ctx) == []


async def test_telefono(ctx: SourceContext) -> None:
    (f,) = await Telefono().run(Entity(type=EntityType.TELEFONO, value="600 11 22 33"), ctx)
    assert f.data["e164"] == "+34600112233" and f.data["pais"] == "ES"
    assert f.data["tipo"] == "móvil" and f.data["valido"]
    (fijo,) = await Telefono().run(Entity(type=EntityType.TELEFONO, value="+34 954 00 00 00"), ctx)
    assert fijo.data["tipo"] in {"fijo", "fijo o móvil"}
    (malo,) = await Telefono().run(Entity(type=EntityType.TELEFONO, value="+34 123"), ctx)
    assert "NO válido" in malo.title or malo.relation == "numero_invalido"
    (raro,) = await Telefono().run(Entity(type=EntityType.TELEFONO, value="abc"), ctx)
    assert raro.relation == "numero_invalido"


async def test_email_basico(ctx: SourceContext) -> None:
    assert "mailinator.com" in dominios_desechables()
    fs = await EmailBasico().run(Entity(type=EntityType.EMAIL, value="Ana.Perez+x@Empresa.es"), ctx)
    rel = {f.relation: f for f in fs}
    assert rel["analisis_email"].data["dominio"] == "empresa.es"
    assert rel["mismo_alias"].entity.value == "Ana.Perez"
    assert rel["dominio_email"].entity.key == "dominio:empresa.es"
    gmail = await EmailBasico().run(Entity(type=EntityType.EMAIL, value="x@gmail.com"), ctx)
    assert "dominio_email" not in {f.relation for f in gmail}
    tmp = await EmailBasico().run(Entity(type=EntityType.EMAIL, value="x@mailinator.com"), ctx)
    assert tmp[0].data["desechable"] is True
    (malo,) = await EmailBasico().run(Entity(type=EntityType.EMAIL, value="no-es-email"), ctx)
    assert malo.relation == "email_invalido"


async def test_politicos_es_sensible(ctx: SourceContext) -> None:
    src = PoliticosCasosJudiciales()
    assert src.sensitive and not src.availability(ctx.settings)[0]
    nombre = lista_politicos()[0]["Nombre"]
    fs = await src.run(P(nombre), ctx)
    assert fs and fs[0].entity.sensitive and "condenado" in fs[0].description
    assert await src.run(P("Nombre Totalmente Inventado Xyz"), ctx) == []


def test_ine_particulas() -> None:
    assert separar_nombre(P("Miguel de Cervantes Saavedra")) == (
        "Miguel",
        "de Cervantes",
        "Saavedra",
    )
    assert separar_nombre(P("Ana de la")) == ("Ana", "de la", None)


async def test_ine_y_ajuste_de_confianza(ctx: SourceContext) -> None:
    target = P("Antonio García García")
    (ine,) = await IneFrecuencia().run(target, ctx)
    assert ine.data["personas_estimadas"] > 100
    menc = Finding(
        source="boe",
        entity=Entity(type=EntityType.DOCUMENTO, value="BOE-1"),
        related_to=target.key,
        confidence=0.7,
        data={"coincidencia": "nombre"},
    )
    otro = Finding(source="borme", entity=E("X SL"), related_to=target.key, confidence=0.7)
    case = Case(target=target, purpose="Prueba de ajuste", findings=[ine, menc, otro])
    assert ajustar_por_frecuencia(case) == 1
    assert menc.confidence < 0.7 and menc.data["confianza_original"] == 0.7
    assert "INE" in menc.data["ajuste_confianza"]
    assert otro.confidence == 0.7  # no depende solo del nombre
    assert ajustar_por_frecuencia(case) == 0  # idempotente
    assert factor_frecuencia(1) == 1.0 and factor_frecuencia(10) == 0.5
    sin_ine = Case(target=target, purpose="Prueba sin INE", findings=[menc])
    assert ajustar_por_frecuencia(sin_ine) == 0


# --------------------------------------------------------- índice local


def _poblar(store: IndexStore) -> None:
    store.insertar(
        "boe_items",
        [
            {
                "id": "BOE-A-2024-1",
                "fecha": "2024-11-19",
                "seccion": "II. Autoridades",
                "departamento": "MINISTERIO",
                "epigrafe": "Nombramientos",
                "titulo": "Real Decreto por el que se nombra a doña Ana María Pérez Ruiz",
                "url_html": "https://www.boe.es/diario_boe/txt.php?id=BOE-A-2024-1",
                "url_pdf": None,
            }
        ],
    )
    store.insertar(
        "borme_actos",
        [
            {
                "cve": "BORME-A-1",
                "fecha": "2024-11-19",
                "provincia": "SEVILLA",
                "anuncio": "1",
                "empresa": "CONSERVAS FICTICIAS SL",
                "acto": "Nombramientos",
                "cargo": "Adm. Solid.",
                "persona": "PEREZ RUIZ ANA MARIA",
                "url": "u1",
            },
            {
                "cve": "BORME-A-1",
                "fecha": "2024-11-19",
                "provincia": "SEVILLA",
                "anuncio": "1",
                "empresa": "CONSERVAS FICTICIAS SL",
                "acto": "Nombramientos",
                "cargo": "Adm. Unico",
                "persona": "HOLDING INVENTADO SL",
                "url": "u1",
            },
        ],
    )
    store.insertar(
        "contratos",
        [
            {
                "id": "c1",
                "origen": "PLACSP",
                "fecha": "2026-11-10",
                "organo": "Ayuntamiento de Villaficticia",
                "objeto": "Suministro",
                "importe": "12500.00",
                "adjudicatario": "CONSERVAS FICTICIAS SL",
                "nif": "B00000000",
                "url": "https://placsp/1",
            }
        ],
    )
    store.insertar(
        "indultos",
        [
            {
                "id": "BOE-A-1999-1",
                "fecha": "1999-01-01",
                "ministro": "X",
                "delito": "robo",
                "genero": "Mujer",
                "titulo": "Real Decreto por el que se indulta a doña Ana María Pérez Ruiz",
            }
        ],
    )
    store.insertar(
        "documentos",
        [
            {
                "ruta": "Adjudicaciones/a.pdf",
                "pagina": 1,
                "sensible": 0,
                "texto": "Adjudicado a PEREZ RUIZ ANA MARIA",
            },
            {
                "ruta": "Testra/e.pdf",
                "pagina": 1,
                "sensible": 1,
                "texto": "Sancionada PEREZ RUIZ ANA MARIA",
            },
        ],
    )


async def test_fuentes_de_indice(ctx: SourceContext, indices: IndexStore) -> None:
    persona = P("Ana María Pérez Ruiz")
    for fuente in (Boe(), Borme(), Contratacion(), Indultos(), DocumentosLocales()):
        with pytest.raises(SourceSkipped, match="índice vacío"):
            await fuente.run(persona, ctx)
    _poblar(indices)

    (boe,) = await Boe().run(persona, ctx)
    assert boe.entity.value == "BOE-A-2024-1" and boe.data["coincidencia"] == "nombre"

    borme = await Borme().run(persona, ctx)
    assert [f.entity.value for f in borme] == ["CONSERVAS FICTICIAS SL"]
    assert borme[0].relation == "cargo:Nombramientos"
    cargos = await Borme().run(E("Conservas Ficticias"), ctx)
    tipos = {f.entity.value: f.entity.type for f in cargos}
    assert tipos == {
        "PEREZ RUIZ ANA MARIA": EntityType.PERSONA,
        "HOLDING INVENTADO SL": EntityType.EMPRESA,
    }

    (por_cif,) = await Contratacion().run(E("B00000000"), ctx)
    assert por_cif.confidence == 0.95 and por_cif.data["coincidencia"] == "nif"
    (por_nombre,) = await Contratacion().run(E("Conservas Ficticias"), ctx)
    assert por_nombre.confidence == 0.6

    (indulto,) = await Indultos().run(persona, ctx)
    assert indulto.entity.sensitive and "robo" in indulto.description

    docs = await DocumentosLocales().run(persona, ctx)
    assert [f.data["ruta"] for f in docs] == ["Adjudicaciones/a.pdf"]  # TESTRA oculto
    ctx.settings = ctx.settings.model_copy(update={"include_sensitive": True})
    assert len(await DocumentosLocales().run(persona, ctx)) == 2


async def test_sin_indice(ctx: SourceContext) -> None:
    ctx.indices = None
    with pytest.raises(SourceSkipped, match="danterodia indice"):
        await Boe().run(P("Ana Pérez"), ctx)
