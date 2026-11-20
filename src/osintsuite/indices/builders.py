"""Construcción incremental del índice local a partir de fuentes oficiales y datasets.

Todas las descargas pasan por :class:`~osintsuite.http.HttpClient` (límite de ritmo por
dominio, reintentos y caché), así que se puede interrumpir y volver a lanzar sin repetir
trabajo: los días ya indexados quedan marcados en la tabla ``meta``.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from importlib.resources import files
from pathlib import Path
from typing import Any

from lxml import etree

from osintsuite.http import HttpClient
from osintsuite.indices import IndexStore
from osintsuite.models import utcnow

log = logging.getLogger("osintsuite.indices")

BOE_API = "https://www.boe.es/datosabiertos/api"
BOE_XML = "https://www.boe.es/diario_boe/xml.php?id="
PLACSP_FEED = (
    "https://contrataciondelsectorpublico.gob.es/sindicacion/sindicacion_643/"
    "licitacionesPerfilesContratanteCompleto3.atom"
)
JSON_HEADERS = {"Accept": "application/json"}

Progress = Callable[[str], None]


def _nada(_: str) -> None:
    return None


def dias(desde: date, hasta: date) -> Iterator[date]:
    d = desde
    while d <= hasta:
        yield d
        d += timedelta(days=1)


def _como_lista(valor: Any) -> list[Any]:
    if valor is None:
        return []
    return valor if isinstance(valor, list) else [valor]


# ====================================================================== BOE


def items_sumario_boe(sumario: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Recorre el sumario del BOE (estructura anidada en la que cada nivel puede ser un objeto
    o una lista) y devuelve cada disposición con su sección, departamento y epígrafe."""

    def walk(nodo: Any, ctx: dict[str, str]) -> Iterator[dict[str, Any]]:
        if isinstance(nodo, list):
            for n in nodo:
                yield from walk(n, ctx)
            return
        if not isinstance(nodo, dict):
            return
        if "identificador" in nodo and "titulo" in nodo:
            yield {**ctx, **nodo}
            return
        nuevo = dict(ctx)
        for clave, valor in nodo.items():
            if clave in {"seccion", "departamento", "epigrafe", "item", "diario"}:
                for hijo in _como_lista(valor):
                    sub = dict(nuevo)
                    if clave in {"seccion", "departamento", "epigrafe"} and isinstance(hijo, dict):
                        sub[clave] = str(hijo.get("nombre", ""))
                    yield from walk(hijo, sub)

    datos = sumario.get("data", {}).get("sumario", {})
    yield from walk(datos.get("diario", []), {})


async def indexar_boe(
    http: HttpClient, store: IndexStore, desde: date, hasta: date, progreso: Progress = _nada
) -> int:
    total = 0
    for d in dias(desde, hasta):
        clave = f"boe:{d:%Y%m%d}"
        if store.marcado(clave):
            continue
        r = await http.get(f"{BOE_API}/boe/sumario/{d:%Y%m%d}", headers=JSON_HEADERS)
        if r.status == 404:
            store.set_meta(clave, "sin publicación")
            continue
        if not r.ok:
            progreso(f"BOE {d}: HTTP {r.status}, se reintentará en la próxima ejecución")
            continue
        filas = [
            {
                "id": it["identificador"],
                "fecha": d.isoformat(),
                "seccion": it.get("seccion", ""),
                "departamento": it.get("departamento", ""),
                "epigrafe": it.get("epigrafe", ""),
                "titulo": it.get("titulo", ""),
                "url_html": it.get("url_html"),
                "url_pdf": (it.get("url_pdf") or {}).get("texto")
                if isinstance(it.get("url_pdf"), dict)
                else it.get("url_pdf"),
            }
            for it in items_sumario_boe(r.json())
        ]
        n = store.insertar("boe_items", filas)
        store.set_meta(clave, str(n))
        total += n
        progreso(f"BOE {d}: {n} disposiciones")
    store.set_meta("actualizado:boe_items", utcnow().isoformat())
    return total


# ==================================================================== BORME

#: Actos del BORME que nombran a personas (o sociedades) con un cargo.
ACTOS_CON_CARGOS = (
    "Nombramientos",
    "Ceses/Dimisiones",
    "Revocaciones",
    "Reelecciones",
    "Cancelaciones de oficio de nombramientos",
    "Declaración de unipersonalidad",
    "Cambio de identidad del socio único",
)
_OTROS_ACTOS = (
    "Constitución",
    "Cambio de domicilio social",
    "Ampliación de capital",
    "Reducción de capital",
    "Disolución",
    "Extinción",
    "Fusión por absorción",
    "Modificaciones estatutarias",
    "Otros conceptos",
    "Cambio de objeto social",
    "Cambio de denominación social",
    "Pérdida del caracter de unipersonalidad",
    "Ampliacion del objeto social",
    "Escisión parcial",
    "Transformación de sociedad",
    "Situación concursal",
    "Fe de erratas",
    "Datos registrales",
    "Depósito de libros",
    "Primera inscripción",
    "Reapertura hoja registral",
    "Emisión de obligaciones",
    "Sucursal",
    "Transformación",
    "Anotación preventiva",
    "Empresario Individual",
    "Página web de la sociedad",
    "Desembolso de dividendos pasivos",
)
_RE_ACTO = re.compile(
    r"(?P<acto>" + "|".join(re.escape(a) for a in ACTOS_CON_CARGOS + _OTROS_ACTOS) + r")\.\s*"
)
#: Un cargo empieza por mayúscula + minúscula («Adm. Solid.», «Apoderado», «Con.Delegado»);
#: los nombres van en MAYÚSCULAS, así que no se confunden.
_RE_CARGO = re.compile(
    r"(?P<cargo>[A-ZÁÉÍÓÚ][a-záéíóúñü]+\.?(?:[\s./\-]*[A-Za-záéíóúñüÁÉÍÓÚ]+\.?){0,5})\s?:\s"
)
_RE_EMPRESA = re.compile(
    r"\b(S\.?L\.?U?|S\.?A\.?U?|SOCIEDAD|SL|SA|SLU|SLP|COOPERATIVA|LIMITADA|ANONIMA|ASOCIACION|"
    r"FUNDACION|S\.?COOP|SCP|CB|UTE|GMBH|LTD|BV|SAS|SRL)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ActoBorme:
    anuncio: str
    empresa: str
    acto: str
    cargo: str
    persona: str


def _parece_nombre(texto: str) -> bool:
    """El BORME escribe nombres y razones sociales en MAYÚSCULAS; frases como
    «Administradores solidarios a Administrador unico» no lo son."""
    letras = [c for c in texto if c.isalpha()]
    return len(letras) >= 3 and sum(c.isupper() for c in letras) / len(letras) > 0.85


def es_empresa(nombre: str) -> bool:
    return bool(_RE_EMPRESA.search(nombre))


def parsear_actos(empresa_linea: str, parrafo: str) -> list[ActoBorme]:
    """Extrae (acto, cargo, persona) de un anuncio de la sección A del BORME."""
    m = re.match(r"\s*(\d+)\s*-\s*(.+?)\.?\s*$", empresa_linea)
    anuncio, empresa = (m.group(1), m.group(2).strip()) if m else ("", empresa_linea.strip())
    texto = re.sub(r"\s+", " ", parrafo)
    resultado: list[ActoBorme] = []
    cortes = list(_RE_ACTO.finditer(texto))
    for i, corte in enumerate(cortes):
        acto = corte.group("acto")
        if acto not in ACTOS_CON_CARGOS:
            continue
        fin = cortes[i + 1].start() if i + 1 < len(cortes) else len(texto)
        segmento = texto[corte.end() : fin]
        cargos = list(_RE_CARGO.finditer(segmento))
        for j, c in enumerate(cargos):
            fin_c = cargos[j + 1].start() if j + 1 < len(cargos) else len(segmento)
            valor = segmento[c.end() : fin_c].strip().rstrip(".").strip()
            for persona in (p.strip().rstrip(".") for p in valor.split(";")):
                if _parece_nombre(persona):
                    resultado.append(
                        ActoBorme(anuncio, empresa, acto, c.group("cargo").strip(), persona)
                    )
    return resultado


def parsear_xml_borme(contenido: bytes) -> list[ActoBorme]:
    root = etree.fromstring(
        contenido, parser=etree.XMLParser(resolve_entities=False, no_network=True)
    )
    parrafos = root.findall(".//texto/p")
    actos: list[ActoBorme] = []
    empresa_actual: str | None = None
    for p in parrafos:
        texto = "".join(p.itertext())
        if p.get("class") == "articulo":
            empresa_actual = texto
        elif empresa_actual is not None:
            actos.extend(parsear_actos(empresa_actual, texto))
    return actos


async def indexar_borme(
    http: HttpClient,
    store: IndexStore,
    desde: date,
    hasta: date,
    provincias: list[str] | None = None,
    progreso: Progress = _nada,
) -> int:
    total = 0
    filtro = {p.upper() for p in provincias} if provincias else None
    for d in dias(desde, hasta):
        clave = f"borme:{d:%Y%m%d}" + (f":{','.join(sorted(filtro))}" if filtro else "")
        if store.marcado(clave) or store.marcado(f"borme:{d:%Y%m%d}"):
            continue
        r = await http.get(f"{BOE_API}/borme/sumario/{d:%Y%m%d}", headers=JSON_HEADERS)
        if r.status == 404:
            store.set_meta(clave, "sin publicación")
            continue
        if not r.ok:
            progreso(f"BORME {d}: HTTP {r.status}, se reintentará")
            continue
        diarios = _como_lista(r.json().get("data", {}).get("sumario", {}).get("diario"))
        items = [
            it
            for di in diarios
            for sec in _como_lista(di.get("seccion"))
            if sec.get("codigo") == "A"
            for it in _como_lista(sec.get("item"))
        ]
        n_dia, completo = 0, True
        for it in items:
            provincia = str(it.get("titulo", ""))
            if filtro and provincia.upper() not in filtro:
                continue
            url = it.get("url_xml")
            if not url:
                continue
            rx = await http.get(url)
            if not rx.ok:
                completo = False
                continue
            filas = [
                {
                    "cve": it["identificador"],
                    "fecha": d.isoformat(),
                    "provincia": provincia,
                    "anuncio": a.anuncio,
                    "empresa": a.empresa,
                    "acto": a.acto,
                    "cargo": a.cargo,
                    "persona": a.persona,
                    "url": url.replace("xml.php", "txt.php"),
                }
                for a in parsear_xml_borme(rx.content)
            ]
            n_dia += store.insertar("borme_actos", filas)
        if completo:
            store.set_meta(clave, str(n_dia))
        total += n_dia
        progreso(f"BORME {d}: {n_dia} actos con cargos")
    store.set_meta("actualizado:borme_actos", utcnow().isoformat())
    return total


# =================================================================== PLACSP

_NS = {
    "a": "http://www.w3.org/2005/Atom",
    "cbc": "urn:dgpe:names:draft:codice:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:dgpe:names:draft:codice:schema:xsd:CommonAggregateComponents-2",
    "cbc-place-ext": "urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonBasicComponents-2",
    "cac-place-ext": "urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonAggregateComponents-2",
}


def _txt(el: Any, ruta: str) -> str:
    found = el.find(ruta, _NS)
    return (found.text or "").strip() if found is not None and found.text else ""


def parsear_feed_placsp(contenido: bytes) -> tuple[list[dict[str, Any]], str | None]:
    """Devuelve (contratos adjudicados, URL de la página siguiente)."""
    root = etree.fromstring(
        contenido, parser=etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
    )
    siguiente = None
    for link in root.findall("a:link", _NS):
        if link.get("rel") == "next":
            siguiente = link.get("href")
    filas: list[dict[str, Any]] = []
    for e in root.findall("a:entry", _NS):
        entry_id = _txt(e, "a:id")
        link = e.find("a:link", _NS)
        url = link.get("href") if link is not None else None
        organo = _txt(
            e, ".//cac-place-ext:LocatedContractingParty/cac:Party/cac:PartyName/cbc:Name"
        )
        objeto = _txt(e, ".//cac:ProcurementProject/cbc:Name") or _txt(e, "a:title")
        for res in e.findall(".//cac:TenderResult", _NS):
            for wp in res.findall("cac:WinningParty", _NS):
                nif = _txt(wp, "cac:PartyIdentification/cbc:ID")
                nombre = _txt(wp, "cac:PartyName/cbc:Name")
                if not nombre and not nif:
                    continue
                filas.append(
                    {
                        "id": f"{entry_id}#{nif or nombre}",
                        "origen": "PLACSP",
                        "fecha": _txt(res, "cbc:AwardDate") or _txt(e, "a:updated")[:10],
                        "organo": organo,
                        "objeto": objeto,
                        "importe": _txt(res, ".//cbc:TaxExclusiveAmount")
                        or _txt(res, ".//cbc:PayableAmount"),
                        "adjudicatario": nombre,
                        "nif": nif.upper(),
                        "url": url,
                    }
                )
    return filas, siguiente


async def indexar_placsp(
    http: HttpClient, store: IndexStore, paginas: int = 3, progreso: Progress = _nada
) -> int:
    url: str | None = PLACSP_FEED
    total = 0
    for i in range(paginas):
        if not url:
            break
        r = await http.get(url, cache_ttl=3600)
        if not r.ok:
            progreso(f"PLACSP página {i + 1}: HTTP {r.status}")
            break
        filas, url = parsear_feed_placsp(r.content)
        n = store.insertar("contratos", filas)
        total += n
        progreso(f"PLACSP página {i + 1}: {n} adjudicaciones nuevas")
    store.set_meta("actualizado:contratos", utcnow().isoformat())
    return total


# ================================================================ indultos


def leer_civio() -> list[dict[str, str]]:
    texto = (
        files("osintsuite.data")
        .joinpath("civio_indultos_1996-2018.csv")
        .read_text(encoding="utf-8")
    )
    return list(csv.DictReader(io.StringIO(texto)))


async def indexar_indultos(
    http: HttpClient, store: IndexStore, limite: int | None = None, progreso: Progress = _nada
) -> int:
    """Une el dataset de Civio con el título de cada Real Decreto del BOE (que incluye el
    nombre de la persona indultada). Es reanudable: solo descarga lo que falta."""
    filas = leer_civio()
    hechos = {r[0] for r in store.db.execute("SELECT id FROM indultos").fetchall()}
    pendientes = [f for f in filas if f["id"] not in hechos]
    if limite is not None:
        pendientes = pendientes[:limite]
    total = 0
    for k, f in enumerate(pendientes, 1):
        r = await http.get(BOE_XML + f["id"], cache_ttl=30 * 86400)
        if not r.ok:
            continue
        root = etree.fromstring(
            r.content, parser=etree.XMLParser(resolve_entities=False, no_network=True)
        )
        titulo = (root.findtext(".//titulo") or "").strip()
        total += store.insertar(
            "indultos",
            [
                {
                    "id": f["id"],
                    "fecha": f["pardon_date"],
                    "ministro": f["minister"],
                    "delito": f["crime"],
                    "genero": f["gender"],
                    "titulo": titulo,
                }
            ],
        )
        if k % 50 == 0:
            progreso(f"Indultos: {k}/{len(pendientes)}")
    store.set_meta("actualizado:indultos", utcnow().isoformat())
    return total


# ========================================================= datos locales


def _importe_es(valor: str) -> str:
    return valor.replace("€", "").strip()


def importar_locales(
    store: IndexStore, data_dir: Path, incluir_testra: bool = False, progreso: Progress = _nada
) -> dict[str, int]:
    """Importa los datasets del repositorio original (``data/``) al índice.

    Sustituye a ``pdfgrep`` (solo Linux y con inyección de comandos) por ``pypdf``.
    Los edictos de TESTRA contienen DNI y matrículas de terceros: no se importan salvo que se
    pida explícitamente, y entonces se marcan como sensibles.
    """
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    resumen: dict[str, int] = {}
    adj = data_dir / "Adjudicaciones"

    cadiz = adj / "Cádiz" / "contratos_menores.json"
    if cadiz.exists():
        datos = json.loads(cadiz.read_text(encoding="utf-8"))
        filas = [
            {
                "id": f"cadiz:{r.get('ca:referenciaContrato')}:{k}",
                "origen": "Diputación de Cádiz (dataset local)",
                "fecha": r.get("ca:fechaFormalizacion") or r.get("ca:nombrePdf", ""),
                "organo": r.get("ca:entidadContratante", ""),
                "objeto": r.get("ca:objeto", ""),
                "importe": r.get("ca:importeAdjudicacion", ""),
                "adjudicatario": (r.get("ca:adjudicatario") or "").strip(),
                "nif": "",
                "url": r.get("uri") or None,
            }
            for k, r in enumerate(datos.get("resources", []))
        ]
        resumen["cadiz"] = store.insertar("contratos", filas)

    andalucia = adj / "adjudicaciones.csv"
    if andalucia.exists():
        filas = []
        with andalucia.open(encoding="utf-8") as fh:
            for k, linea in enumerate(fh):
                linea = linea.strip()
                if not linea:
                    continue
                d = json.loads(linea).get("data", {})
                filas.append(
                    {
                        "id": f"adjudicaciones:{k}",
                        "origen": f"Dataset local adjudicaciones ({d.get('site', '')})",
                        "fecha": d.get("Date", ""),
                        "organo": d.get("site", ""),
                        "objeto": "",
                        "importe": _importe_es(d.get("Importe", "")),
                        "adjudicatario": (d.get("Adjudicatario") or "").strip(),
                        "nif": (d.get("NIF") or "").strip().upper(),
                        "url": None,
                    }
                )
        resumen["adjudicaciones"] = store.insertar("contratos", filas)

    pdfs = sorted(adj.rglob("*.pdf")) + sorted(adj.rglob("*.PDF"))
    if incluir_testra:
        pdfs += sorted((data_dir / "Testra").glob("*.[pP][dD][fF]"))
    n_paginas = 0
    for pdf in pdfs:
        sensible = int("Testra" in pdf.parts)
        rel = pdf.relative_to(data_dir).as_posix()
        try:
            reader = PdfReader(pdf)
            filas = [
                {
                    "ruta": rel,
                    "pagina": i + 1,
                    "sensible": sensible,
                    "texto": (page.extract_text() or ""),
                }
                for i, page in enumerate(reader.pages)
            ]
        except (PdfReadError, ValueError, KeyError) as exc:
            log.warning("No se pudo leer %s: %s", rel, exc)
            continue
        n_paginas += store.insertar("documentos", filas)
        progreso(f"PDF {rel}: {len(filas)} páginas")
    resumen["paginas_pdf"] = n_paginas
    store.set_meta("actualizado:documentos", utcnow().isoformat())
    store.set_meta("actualizado:contratos", utcnow().isoformat())
    return resumen
