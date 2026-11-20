"""Wikidata (datos estructurados) + enlace a Wikipedia en español.

Sustituye a las librerías ``wikipedia`` (abandonada en 2014) y ``wikipedia-api``, y al
scraping de la ficha HTML del original. Wikidata da fechas, ocupaciones y cuentas oficiales
como datos, sin depender del maquetado de Wikipedia.

**Minimización**: no se extraen religión (P140), orientación ni datos de salud. La afiliación
política (P102) es una categoría especial (art. 9 RGPD): solo se incluye con ``--sensibles``,
y aun así se marca como sensible.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from osintsuite.identifiers import tokens_nombre
from osintsuite.models import Entity, EntityType, Finding
from osintsuite.sources.base import Source, SourceContext

API = "https://www.wikidata.org/w/api.php"
ENTITY = "https://www.wikidata.org/wiki/Special:EntityData/{}.json"

HUMANO = "Q5"
#: Propiedades con fecha.
FECHAS = {"P569": "nacimiento", "P570": "fallecimiento", "P571": "fundacion"}
#: Propiedades con otras entidades (se resuelve su etiqueta).
RELACIONES = {
    "P106": "ocupacion",
    "P39": "cargo",
    "P108": "empleador",
    "P27": "nacionalidad",
    "P19": "lugar_nacimiento",
    "P159": "sede",
    "P452": "sector",
    "P112": "fundador",
    "P169": "director_ejecutivo",
    "P749": "matriz",
}
SENSIBLES = {"P102": "partido_politico"}
#: Cuentas y webs oficiales → entidades para pivotar.
CUENTAS = {
    "P2002": ("x", "https://x.com/{}"),
    "P2003": ("instagram", "https://www.instagram.com/{}"),
    "P2013": ("facebook", "https://www.facebook.com/{}"),
    "P4264": ("linkedin", "https://www.linkedin.com/company/{}"),
    "P6634": ("linkedin", "https://www.linkedin.com/in/{}"),
    "P2037": ("github", "https://github.com/{}"),
    "P2397": ("youtube", "https://www.youtube.com/channel/{}"),
    "P7085": ("tiktok", "https://www.tiktok.com/@{}"),
}


def _valores(entity: dict[str, Any], prop: str) -> list[Any]:
    out = []
    for claim in entity.get("claims", {}).get(prop, []):
        snak = claim.get("mainsnak", {})
        if snak.get("snaktype") == "value":
            out.append(snak["datavalue"]["value"])
    return out


def _fecha(valor: dict[str, Any]) -> str:
    """``+1547-09-29T00:00:00Z`` con precisión 11 → ``1547-09-29``; precisión 9 → ``1547``."""
    t = str(valor.get("time", "")).lstrip("+")
    precision = int(valor.get("precision", 11))
    return t[:4] if precision <= 9 else t[:7] if precision == 10 else t[:10]


def _parecido(buscado: str, etiqueta: str) -> float:
    a, b = (
        set(tokens_nombre(buscado, conservar_enie=False)),
        set(tokens_nombre(etiqueta, conservar_enie=False)),
    )
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class Wikidata(Source):
    name = "wikidata"
    description = "Wikidata y Wikipedia: identidad, fechas, cargos y cuentas oficiales"
    input_types = frozenset({EntityType.PERSONA, EntityType.EMPRESA})

    async def _etiquetas(self, ctx: SourceContext, ids: list[str]) -> dict[str, str]:
        etiquetas: dict[str, str] = {}
        for i in range(0, len(ids), 50):
            r = await ctx.http.get(
                API,
                params={
                    "action": "wbgetentities",
                    "ids": "|".join(ids[i : i + 50]),
                    "props": "labels",
                    "languages": "es|en",
                    "format": "json",
                },
            )
            for qid, ent in r.json().get("entities", {}).items():
                labels = ent.get("labels", {})
                etiquetas[qid] = (labels.get("es") or labels.get("en") or {}).get("value", qid)
        return etiquetas

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        r = await ctx.http.get(
            API,
            params={
                "action": "wbsearchentities",
                "search": target.value,
                "language": "es",
                "uselang": "es",
                "type": "item",
                "limit": 5,
                "format": "json",
            },
        )
        candidatos = r.json().get("search", [])
        out: list[Finding] = []
        for cand in candidatos:
            parecido = _parecido(target.value, cand.get("label", ""))
            if parecido < 0.5:
                continue
            qid = cand["id"]
            resp = await ctx.http.get(ENTITY.format(qid))
            entidad = resp.json()["entities"].get(qid, {})
            es_humano = any(v.get("id") == HUMANO for v in _valores(entidad, "P31"))
            if (target.type == EntityType.PERSONA) != es_humano:
                continue
            out.extend(await self._hallazgos(target, ctx, qid, cand, entidad, parecido, resp.url))
        return out

    async def _hallazgos(
        self,
        target: Entity,
        ctx: SourceContext,
        qid: str,
        cand: dict[str, Any],
        entidad: dict[str, Any],
        parecido: float,
        url_json: str,
    ) -> list[Finding]:
        props = {**RELACIONES, **(SENSIBLES if ctx.settings.include_sensitive else {})}
        ids = sorted({v["id"] for p in props for v in _valores(entidad, p) if isinstance(v, dict)})
        etiquetas = await self._etiquetas(ctx, ids) if ids else {}
        atributos: dict[str, Any] = {"wikidata": qid, "descripcion": cand.get("description")}
        for p, nombre in FECHAS.items():
            if vals := _valores(entidad, p):
                atributos[nombre] = _fecha(vals[0])
        for p, nombre in props.items():
            vals = [
                etiquetas.get(v["id"], v["id"]) for v in _valores(entidad, p) if isinstance(v, dict)
            ]
            if vals:
                atributos[nombre] = vals
        sitelink = entidad.get("sitelinks", {}).get("eswiki", {}).get("title")
        wiki_url = (
            f"https://es.wikipedia.org/wiki/{sitelink.replace(' ', '_')}" if sitelink else None
        )
        if wiki_url:
            atributos["wikipedia"] = wiki_url
        sensible = "partido_politico" in atributos
        identidad = Entity(
            type=target.type,
            value=cand.get("label", target.value),
            attributes=atributos,
            sensitive=sensible,
        )
        evidencia = ctx.evidence.save(
            ctx.case_id, url_json, _bytes_json(entidad), "application/json"
        )
        out = [
            self.finding(
                target,
                identidad,
                relation="posible_identidad",
                title=f"{cand.get('label')} — {cand.get('description') or 'sin descripción'}",
                description="Coincidencia por nombre en Wikidata: confirma que es la misma persona "
                "o empresa antes de usar estos datos.",
                confidence=round(0.4 + 0.4 * parecido, 2),
                source_url=f"https://www.wikidata.org/wiki/{qid}",
                evidence=[evidencia],
                data={"coincidencia": "nombre", **atributos},
            )
        ]
        for p, (red, plantilla) in CUENTAS.items():
            for handle in _valores(entidad, p):
                if isinstance(handle, str):
                    out.append(
                        self.finding(
                            identidad,
                            Entity(
                                type=EntityType.USERNAME,
                                value=handle,
                                attributes={"plataforma": red},
                            ),
                            relation=f"cuenta_oficial:{red}",
                            title=f"Cuenta de {red} declarada en Wikidata",
                            confidence=0.9,
                            source_url=plantilla.format(handle),
                        )
                    )
        for web in _valores(entidad, "P856"):
            if isinstance(web, str):
                host = (urlsplit(web).hostname or "").removeprefix("www.")
                if host:
                    out.append(
                        self.finding(
                            identidad,
                            Entity(type=EntityType.DOMINIO, value=host),
                            relation="web_oficial",
                            title=f"Web oficial: {web}",
                            confidence=0.9,
                            source_url=web,
                        )
                    )
        return out


def _bytes_json(data: dict[str, Any]) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode("utf-8")
