"""Fuentes oficiales españolas consultadas a través del índice local.

El índice se construye con ``danterodia indice …`` (ver :mod:`danterodia.indices.builders`).
Si una tabla está vacía, la fuente se marca como «omitida» explicando qué comando ejecutar.
"""

from __future__ import annotations

from danterodia.identifiers import coincide_nombre, limpiar_id, valida_cif
from danterodia.indices import IndexStore
from danterodia.indices.builders import es_empresa
from danterodia.models import Entity, EntityType, Finding
from danterodia.sources.base import Source, SourceContext, SourceSkipped

PERSONA_O_EMPRESA = frozenset({EntityType.PERSONA, EntityType.EMPRESA})


def _indice(ctx: SourceContext, tabla: str, comando: str) -> IndexStore:
    store = ctx.index()
    if store.contar(tabla) == 0:
        raise SourceSkipped(f"índice vacío: ejecuta `{comando}`")
    return store


class Boe(Source):
    name = "boe"
    description = "Disposiciones del BOE que mencionan al objetivo en su título (índice local)"
    input_types = PERSONA_O_EMPRESA

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        store = _indice(ctx, "boe_items", "danterodia indice boe --desde AAAA-MM-DD")
        out = []
        for fila in store.buscar("boe_items", target.value, columna="titulo", limite=100):
            if not coincide_nombre(target.value, fila["titulo"]):
                continue
            doc = Entity(
                type=EntityType.DOCUMENTO,
                value=fila["id"],
                attributes={"titulo": fila["titulo"], "fecha": fila["fecha"]},
            )
            out.append(
                self.finding(
                    target,
                    doc,
                    relation="mencionado_en_boe",
                    title=fila["titulo"][:300],
                    description=f"{fila['seccion']} · {fila['departamento']} · {fila['fecha']}",
                    confidence=0.7,
                    source_url=fila["url_html"],
                    data={**fila, "coincidencia": "nombre"},
                )
            )
        return out


class Borme(Source):
    name = "borme"
    description = "Cargos societarios en el BORME sección A (nombramientos, ceses…; índice local)"
    input_types = PERSONA_O_EMPRESA

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        store = _indice(ctx, "borme_actos", "danterodia indice borme --desde AAAA-MM-DD")
        out = []
        if target.type == EntityType.PERSONA:
            for f in store.buscar("borme_actos", target.value, columna="persona", limite=500):
                if not coincide_nombre(target.value, f["persona"]):
                    continue
                empresa = Entity(type=EntityType.EMPRESA, value=f["empresa"])
                out.append(
                    self.finding(
                        target,
                        empresa,
                        relation=f"cargo:{f['acto']}",
                        title=f"{f['acto']}: {f['cargo']} en {f['empresa']} ({f['fecha']})",
                        description=f"Nombre en el BORME: {f['persona']} · {f['provincia']}",
                        confidence=0.65,
                        source_url=f["url"],
                        data={**f, "coincidencia": "nombre"},
                    )
                )
        else:
            for f in store.buscar("borme_actos", target.value, columna="empresa", limite=500):
                if not coincide_nombre(target.value, f["empresa"]):
                    continue
                tipo = EntityType.EMPRESA if es_empresa(f["persona"]) else EntityType.PERSONA
                out.append(
                    self.finding(
                        target,
                        Entity(type=tipo, value=f["persona"]),
                        relation=f"cargo:{f['acto']}",
                        title=f"{f['acto']}: {f['persona']} ({f['cargo']}, {f['fecha']})",
                        confidence=0.85,
                        source_url=f["url"],
                        data=f,
                    )
                )
        return out


class Contratacion(Source):
    name = "contratacion"
    description = "Adjudicaciones de contratos públicos (PLACSP y datasets locales; índice local)"
    input_types = PERSONA_O_EMPRESA

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        store = _indice(ctx, "contratos", "danterodia indice placsp (o `indice locales`)")
        cif = limpiar_id(target.attributes.get("cif") or target.value)
        if valida_cif(cif):
            filas = store.buscar_exacto("contratos", "nif", cif)
            exacto = True
        else:
            filas = [
                f
                for f in store.buscar("contratos", target.value, columna="adjudicatario")
                if coincide_nombre(target.value, f["adjudicatario"])
            ]
            exacto = False
        out = []
        for f in filas:
            organo = Entity(
                type=EntityType.EMPRESA,
                value=f["organo"] or "órgano desconocido",
                attributes={"tipo": "organismo_publico"},
            )
            out.append(
                self.finding(
                    target,
                    organo,
                    relation="adjudicatario_de",
                    title=f"{f['objeto'][:160] or 'Contrato'} — "
                    f"{f['importe'] or '¿importe?'} € ({f['fecha']})",
                    description=f"Adjudicatario: {f['adjudicatario']} {f['nif']} · {f['origen']}",
                    confidence=0.95 if exacto else 0.6,
                    source_url=f["url"],
                    data={**f, "coincidencia": "nif" if exacto else "nombre"},
                )
            )
        return out


class Indultos(Source):
    name = "indultos"
    description = "Indultos publicados en el BOE 1996-2018 (Civio + BOE; índice local)"
    input_types = frozenset({EntityType.PERSONA})
    sensitive = True  # datos penales (art. 10 RGPD)

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        store = _indice(ctx, "indultos", "danterodia indice indultos")
        out = []
        for f in store.buscar("indultos", target.value, columna="titulo"):
            if not coincide_nombre(target.value, f["titulo"]):
                continue
            doc = Entity(type=EntityType.DOCUMENTO, value=f["id"], sensitive=True)
            out.append(
                self.finding(
                    target,
                    doc,
                    relation="indultado_segun_boe",
                    title=f["titulo"][:300],
                    description=f"Delito: {f['delito'][:200]} · Ministro: {f['ministro']} "
                    f"· {f['fecha']}",
                    confidence=0.6,
                    source_url=f"https://www.boe.es/diario_boe/txt.php?id={f['id']}",
                    data={
                        **f,
                        "coincidencia": "nombre",
                        "fuente_dataset": "Civio, El Indultómetro",
                    },
                )
            )
        return out


class DocumentosLocales(Source):
    name = "documentos_locales"
    description = "Búsqueda en los PDF locales de data/ (sustituye a pdfgrep; índice local)"
    input_types = PERSONA_O_EMPRESA

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        store = _indice(ctx, "documentos", "danterodia indice locales")
        out = []
        for f in store.buscar("documentos", target.value, limite=50):
            if f["sensible"] and not ctx.settings.include_sensitive:
                continue
            if not coincide_nombre(target.value, f["texto"]):
                continue
            pos = f["texto"].upper().find(target.value.split()[-1].upper())
            extracto = (
                f["texto"][max(0, pos - 120) : pos + 200].replace("\n", " ") if pos >= 0 else ""
            )
            doc = Entity(
                type=EntityType.DOCUMENTO,
                value=f"{f['ruta']}#p{f['pagina']}",
                sensitive=bool(f["sensible"]),
            )
            out.append(
                self.finding(
                    target,
                    doc,
                    relation="mencionado_en_documento",
                    title=f"{f['ruta']} (página {f['pagina']})",
                    description=extracto,
                    confidence=0.5,
                    data={"ruta": f["ruta"], "pagina": f["pagina"], "coincidencia": "nombre"},
                )
            )
        return out
