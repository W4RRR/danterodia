"""Buscadores con dorks, prensa española y verificación de noticias."""

from __future__ import annotations

import asyncio
import math
import re
import warnings
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from importlib.resources import files
from typing import Any
from urllib.parse import urlsplit

import phonenumbers
from rapidfuzz import fuzz

from osintsuite import search
from osintsuite.http import HttpError
from osintsuite.identifiers import coincide_nombre, extraer
from osintsuite.models import Entity, EntityType, Finding
from osintsuite.search import SearchResult
from osintsuite.sources.base import Source, SourceContext

REDES_SOCIALES = frozenset(
    {
        "facebook.com",
        "instagram.com",
        "youtube.com",
        "twitter.com",
        "x.com",
        "linkedin.com",
        "pinterest.com",
        "tiktok.com",
        "reddit.com",
        "threads.net",
        "bsky.app",
    }
)
#: Verificadores españoles (miembros de la IFCN o EDMO).
VERIFICADORES = {
    "maldita.es": "Maldita",
    "newtral.es": "Newtral",
    "verifica.efe.com": "EFE Verifica",
    "rtve.es": "RTVE Verifica",
}


@lru_cache(maxsize=1)
def dominios_prensa() -> frozenset[str]:
    texto = files("osintsuite.data").joinpath("prensa_es.txt").read_text(encoding="utf-8")
    return frozenset(line.strip().lower() for line in texto.splitlines() if line.strip())


def dominio_de(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.").removeprefix("m.")


def es_prensa(url: str) -> bool:
    d = dominio_de(url)
    return any(d == p or d.endswith("." + p) for p in dominios_prensa())


def clasificar(url: str) -> str:
    d = dominio_de(url)
    if es_prensa(url):
        return "prensa"
    if any(d == r or d.endswith("." + r) for r in REDES_SOCIALES):
        return "redes_sociales"
    return "otros"


_ANUNCIOS = ("bing.com/aclick", "duckduckgo.com/y.js", "googleadservices.com", "doubleclick.net")


def es_anuncio(url: str) -> bool:
    return any(a in url for a in _ANUNCIOS)


def relevante(target: Entity, url: str, texto: str) -> bool:
    """¿El resultado menciona de verdad el objetivo? Evita el ruido de los buscadores, que
    devuelven resultados aunque no contengan la frase exacta."""
    t = texto.lower()
    match target.type:
        case EntityType.PERSONA | EntityType.EMPRESA:
            return coincide_nombre(target.value, texto)
        case EntityType.DOMINIO:
            host = dominio_de(url)
            d = target.value.lower()
            return host == d or host.endswith("." + d) or d in t
        case EntityType.TELEFONO:
            digitos = re.sub(r"\D", "", target.value)[-9:]
            return digitos in re.sub(r"\D", "", texto)
        case _:
            return target.value.lower() in t


def variables_dork(target: Entity) -> dict[str, str]:
    v = {"valor": target.value}
    if target.type == EntityType.EMAIL and "@" in target.value:
        v["usuario"], v["dominio"] = target.value.split("@", 1)
    if target.type == EntityType.TELEFONO:
        try:
            n = phonenumbers.parse(target.value, "ES")
            v["e164"] = phonenumbers.format_number(n, phonenumbers.PhoneNumberFormat.E164)
            v["nacional"] = phonenumbers.format_number(n, phonenumbers.PhoneNumberFormat.NATIONAL)
            v["compacto"] = v["nacional"].replace(" ", "")
        except phonenumbers.NumberParseException:
            pass
    if target.type == EntityType.PERSONA:
        partes = target.value.split()
        v["nombre"], v["apellidos"] = partes[0], " ".join(partes[1:])
    return v


async def buscar_en_motores(
    ctx: SourceContext, query: str, max_results: int = 10, news: bool = False
) -> list[SearchResult]:
    """Consulta DuckDuckGo y, si hay clave, Brave/Google CSE/Mojeek. Un motor caído no
    impide usar los demás."""
    s = ctx.settings
    tareas = [
        search.ddg(query, max_results=max_results, news=news, segundos=s.timeout, proxy=s.proxy)
    ]
    if not news:
        if key := s.api_key("brave_api_key"):
            tareas.append(search.brave(ctx.http, key, query, max_results))
        if (key := s.api_key("google_cse_key")) and s.google_cse_cx:
            tareas.append(search.google_cse(ctx.http, key, s.google_cse_cx, query, max_results))
        if key := s.api_key("mojeek_api_key"):
            tareas.append(search.mojeek_api(ctx.http, key, query, max_results))
    resultados: list[SearchResult] = []
    for r in await asyncio.gather(*tareas, return_exceptions=True):
        if isinstance(r, BaseException):
            ctx.log.warning("Motor de búsqueda falló para %r: %s", query, r)
        else:
            resultados.extend(r)
    return resultados


# ================================================================ buscadores


class Buscadores(Source):
    name = "buscadores"
    description = "Dorks en DuckDuckGo (y Brave/Google CSE/Mojeek si hay clave), clasificados"
    input_types = frozenset(
        {
            EntityType.PERSONA,
            EntityType.EMPRESA,
            EntityType.USERNAME,
            EntityType.EMAIL,
            EntityType.TELEFONO,
            EntityType.DOMINIO,
        }
    )
    timeout = 300.0
    max_dorks = 4

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        plantillas = search.cargar_dorks(ctx.settings.data_dir).get(target.type.value, [])
        dorks = search.render_dorks(plantillas, variables_dork(target))[: self.max_dorks]
        vistos: dict[str, tuple[SearchResult, str]] = {}
        for dork in dorks:
            for res in await buscar_en_motores(ctx, dork):
                vistos.setdefault(res.url, (res, dork))
        out: list[Finding] = []
        for url, (res, dork) in vistos.items():
            if es_anuncio(url):
                continue
            categoria = clasificar(url)
            texto = f"{res.title} {res.snippet} {url}"
            if not relevante(target, url, texto):
                continue
            out.append(
                self.finding(
                    target,
                    Entity(type=EntityType.URL, value=url, attributes={"categoria": categoria}),
                    relation=f"mencionado_en:{categoria}",
                    title=res.title[:200],
                    description=res.snippet[:300],
                    confidence=0.35,
                    source_url=url,
                    data={
                        "dork": dork,
                        "motor": res.engine,
                        "categoria": categoria,
                        "coincidencia": "nombre" if target.type == EntityType.PERSONA else "texto",
                    },
                )
            )
            for email in extraer(texto).emails:
                if email != target.value.lower():
                    out.append(
                        self.finding(
                            target,
                            Entity(type=EntityType.EMAIL, value=email),
                            relation="email_junto_a_mencion",
                            confidence=0.3,
                            title=f"Email en un resultado: {email}",
                            source_url=url,
                        )
                    )
        return out


# =================================================================== prensa


@dataclass(slots=True)
class Articulo:
    url: str
    titulo: str = ""
    texto: str = ""
    autores: list[str] = field(default_factory=list)
    fecha: str | None = None
    html: bytes = b""
    #: URL de la copia de la Wayback Machine si el medio bloqueó la descarga directa.
    archivo: str | None = None


#: Códigos con los que algunos medios bloquean a clientes que no son navegadores.
BLOQUEO = frozenset({401, 403, 429, 451})


async def descargar_articulo(ctx: SourceContext, url: str) -> Articulo:
    """Descarga con el cliente seguro y extrae el artículo con newspaper4k.

    Si el medio bloquea a clientes identificados como herramientas (403…), se usa la última
    copia de la Wayback Machine en vez de disfrazarse de navegador: es transparente y además
    deja una evidencia verificable por terceros.
    """
    r = await ctx.http.get(url)
    archivo = None
    if r.status in BLOQUEO:
        archivo = f"https://web.archive.org/web/2id_/{url}"
        ctx.log.info("%s respondió %d; se usa la copia de la Wayback Machine", url, r.status)
        r = await ctx.http.get(archivo)
    if not r.ok:
        raise HttpError(f"{url} respondió {r.status}")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from newspaper import Article

        art = Article(url, language="es", fetch_images=False)
        art.download(input_html=r.text)
        art.parse()
    fecha = art.publish_date.isoformat() if isinstance(art.publish_date, datetime) else None
    return Articulo(
        url=url if archivo else r.url,
        titulo=art.title or "",
        texto=art.text or "",
        autores=list(art.authors or []),
        fecha=fecha,
        html=r.content,
        archivo=archivo,
    )


class Prensa(Source):
    name = "prensa"
    description = "Noticias en medios españoles (DuckDuckGo News + newspaper4k), deduplicadas"
    input_types = frozenset({EntityType.PERSONA, EntityType.EMPRESA})
    timeout = 300.0
    max_articulos = 6

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        resultados = await buscar_en_motores(ctx, f'"{target.value}"', max_results=25, news=True)
        resultados += [
            r
            for r in await buscar_en_motores(ctx, f'"{target.value}" noticia', 15)
            if es_prensa(r.url)
        ]
        unicos: list[SearchResult] = []
        for r in resultados:
            if not es_prensa(r.url):
                continue
            # Deduplicación: la misma noticia replicada en varios medios del mismo grupo.
            if any(fuzz.token_set_ratio(r.title, u.title) > 90 for u in unicos):
                continue
            unicos.append(r)
        out: list[Finding] = []
        for r in unicos[: self.max_articulos]:
            try:
                art = await descargar_articulo(ctx, r.url)
            except (HttpError, ValueError) as exc:
                ctx.log.info("No se pudo extraer %s: %s", r.url, exc)
                art = Articulo(url=r.url, titulo=r.title, texto=r.snippet)
            if not coincide_nombre(target.value, f"{art.titulo} {art.texto} {r.snippet}"):
                continue
            evidencia = (
                ctx.evidence.save(ctx.case_id, art.archivo or art.url, art.html, "text/html")
                if art.html
                else None
            )
            ext = extraer(art.texto)
            out.append(
                self.finding(
                    target,
                    Entity(
                        type=EntityType.URL,
                        value=art.url,
                        attributes={"medio": dominio_de(art.url), "fecha": art.fecha or r.date},
                    ),
                    relation="mencionado_en:prensa",
                    title=art.titulo or r.title,
                    description=(art.texto or r.snippet)[:400],
                    confidence=0.55,
                    source_url=art.url,
                    evidence=[evidencia] if evidencia else [],
                    data={
                        "medio": dominio_de(art.url),
                        "fecha": art.fecha or r.date,
                        "autores": art.autores,
                        "copia_archivada": art.archivo,
                        "coincidencia": "nombre",
                        # Minimización: no se guardan DNI ni IBAN encontrados, solo cuántos hay.
                        "emails": ext.emails,
                        "telefonos": ext.telefonos,
                        "documentos_identidad_detectados": len(ext.documentos),
                    },
                )
            )
        return out


# ===================================================== verificación de noticias

PALABRAS_SENSACIONALISTAS = (
    "urgente",
    "última hora",
    "bomba",
    "escándalo",
    "increíble",
    "no quieren que sepas",
    "compártelo",
    "compartelo",
    "difunde",
    "censurado",
    "lo que nadie te cuenta",
    "milagro",
    "100% real",
    "impactante",
)
PALABRAS_FUENTES = ("estudio", "universidad", "informe", "fuente", "según", "sentencia", "datos de")


def es_verificacion(r: SearchResult, dominio: str, titulo: str) -> bool:
    """¿Es este resultado una verificación sobre la misma historia?

    Exige el dominio exacto del verificador (no subdominios como bases de datos de cargos),
    la sección de verificación en RTVE y un titular parecido con al menos dos palabras
    significativas en común.
    """
    host = dominio_de(r.url)
    if host != dominio:
        return False
    if dominio == "rtve.es" and "/verifica" not in r.url:
        return False
    comunes = set(_tokens(titulo)) & set(_tokens(f"{r.title} {r.snippet}"))
    significativas = {t for t in comunes if len(t) > 4}
    return fuzz.token_set_ratio(titulo, r.title) >= 60 and len(significativas) >= 2


def _tokens(texto: str) -> list[str]:
    return [t for t in re.findall(r"[a-záéíóúñü]{3,}", texto.lower())]


def tfidf_coseno(a: str, b: str, corpus: list[str]) -> float:
    """Similitud coseno TF-IDF entre ``a`` y ``b`` (implementación mínima y explicable)."""
    docs = [Counter(_tokens(d)) for d in corpus]
    n = len(docs)
    df: Counter[str] = Counter()
    for d in docs:
        df.update(d.keys())

    def vec(texto: str) -> dict[str, float]:
        tf = Counter(_tokens(texto))
        return {t: c * (math.log((1 + n) / (1 + df[t])) + 1) for t, c in tf.items()}

    va, vb = vec(a), vec(b)
    num = sum(v * vb.get(t, 0.0) for t, v in va.items())
    den = math.sqrt(sum(v * v for v in va.values())) * math.sqrt(sum(v * v for v in vb.values()))
    return round(num / den, 4) if den else 0.0


@dataclass(slots=True)
class Senal:
    nombre: str
    puntos: int
    explicacion: str


def puntuar(
    art: Articulo, corroboraciones: int, verificaciones: list[dict[str, Any]]
) -> tuple[int, list[Senal]]:
    """Puntuación 0-100 a partir de señales con peso conocido. Empieza en 50."""
    s: list[Senal] = []
    texto = f"{art.titulo}\n{art.texto}".lower()
    if es_prensa(art.url):
        s.append(
            Senal(
                "medio_conocido", 10, f"{dominio_de(art.url)} está en la lista de medios españoles"
            )
        )
    else:
        s.append(
            Senal("medio_desconocido", -10, f"{dominio_de(art.url)} no es un medio reconocido")
        )
    if corroboraciones >= 2:
        s.append(
            Senal("corroborada", 20, f"{corroboraciones} medios publican una historia parecida")
        )
    elif corroboraciones == 1:
        s.append(Senal("corroborada_parcial", 8, "un medio más publica una historia parecida"))
    else:
        s.append(Senal("sin_corroborar", -15, "ningún otro medio publica una historia parecida"))
    falsas = [
        v
        for v in verificaciones
        if re.search(r"fals|bulo|engañ|manipul|sin pruebas", v.get("valoracion", "").lower())
    ]
    if falsas:
        s.append(
            Senal(
                "desmentida",
                -35,
                f"{len(falsas)} verificación(es) la califican como falsa o engañosa",
            )
        )
    elif verificaciones:
        s.append(
            Senal("verificaciones_relacionadas", -5, "hay verificaciones sobre el tema: revísalas")
        )
    s.append(
        Senal("con_fecha", 5, "tiene fecha de publicación")
        if art.fecha
        else Senal("sin_fecha", -5, "no tiene fecha de publicación")
    )
    s.append(
        Senal("con_autor", 5, f"firmada por {', '.join(art.autores[:3])}")
        if art.autores
        else Senal("sin_autor", -5, "no está firmada")
    )
    sensacional = [p for p in PALABRAS_SENSACIONALISTAS if p in texto]
    if sensacional:
        s.append(
            Senal(
                "lenguaje_sensacionalista",
                -5 * min(3, len(sensacional)),
                f"expresiones: {', '.join(sensacional[:5])}",
            )
        )
    if art.titulo and sum(c.isupper() for c in art.titulo) > 0.5 * sum(
        c.isalpha() for c in art.titulo
    ):
        s.append(Senal("titular_mayusculas", -5, "titular mayoritariamente en mayúsculas"))
    if art.titulo.count("!") >= 2:
        s.append(Senal("exclamaciones", -5, "varias exclamaciones en el titular"))
    citas = [p for p in PALABRAS_FUENTES if p in texto]
    if citas:
        s.append(Senal("cita_fuentes", 5, f"menciona fuentes: {', '.join(citas[:4])}"))
    if art.url.startswith("https://"):
        s.append(Senal("https", 2, "se sirve por HTTPS"))
    total = max(0, min(100, 50 + sum(x.puntos for x in s)))
    return total, s


class VerificarNoticia(Source):
    name = "verificar_noticia"
    description = "Verificación de una noticia: corroboración, fact-checkers y puntuación explicada"
    input_types = frozenset({EntityType.URL})
    timeout = 400.0
    max_comparar = 6

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        art = await descargar_articulo(ctx, target.value)
        if not art.titulo:
            return [
                self.finding(
                    target,
                    target,
                    relation="verificacion",
                    title="No se pudo extraer un artículo de esta URL",
                    confidence=0.3,
                )
            ]
        evidencia = ctx.evidence.save(ctx.case_id, art.archivo or art.url, art.html, "text/html")
        origen = dominio_de(art.url)

        # 1. La misma historia en otros medios.
        candidatos = [
            r
            for r in await buscar_en_motores(ctx, art.titulo, max_results=15)
            if dominio_de(r.url) != origen
        ]
        candidatos += [
            r
            for r in await buscar_en_motores(ctx, art.titulo, max_results=15, news=True)
            if dominio_de(r.url) != origen
        ]
        vistos: set[str] = set()
        comparados: list[tuple[SearchResult, Articulo]] = []
        for r in candidatos:
            if r.url in vistos or len(comparados) >= self.max_comparar:
                continue
            vistos.add(r.url)
            try:
                comparados.append((r, await descargar_articulo(ctx, r.url)))
            except (HttpError, ValueError):
                comparados.append((r, Articulo(url=r.url, titulo=r.title, texto=r.snippet)))
        corpus = [art.texto] + [a.texto for _, a in comparados]
        out: list[Finding] = []
        corroboran = 0
        for r, otro in comparados:
            sim_texto = tfidf_coseno(art.texto, otro.texto, corpus) if otro.texto else 0.0
            sim_titulo = fuzz.token_set_ratio(art.titulo, otro.titulo or r.title) / 100
            similitud = round(max(sim_texto, 0.8 * sim_titulo), 3)
            if similitud >= 0.35:
                corroboran += int(es_prensa(otro.url))
                out.append(
                    self.finding(
                        target,
                        Entity(
                            type=EntityType.URL,
                            value=otro.url,
                            attributes={"medio": dominio_de(otro.url)},
                        ),
                        relation="misma_historia",
                        title=otro.titulo or r.title,
                        description=f"Similitud de texto (TF-IDF) {sim_texto:.2f}, "
                        f"de titular {sim_titulo:.2f}",
                        confidence=similitud,
                        source_url=otro.url,
                        data={"similitud_texto": sim_texto, "similitud_titular": sim_titulo},
                    )
                )

        # 2. Verificadores españoles y Google Fact Check Tools.
        verificaciones: list[dict[str, Any]] = []
        palabras = " ".join(sorted(set(_tokens(art.titulo)), key=len, reverse=True)[:6])
        for dominio, nombre in VERIFICADORES.items():
            consulta = f"site:{dominio} {palabras}" + (" verifica" if dominio == "rtve.es" else "")
            for r in (await buscar_en_motores(ctx, consulta, max_results=5))[:3]:
                if es_verificacion(r, dominio, art.titulo):
                    verificaciones.append(
                        {
                            "verificador": nombre,
                            "url": r.url,
                            "titulo": r.title,
                            "valoracion": r.title + " " + r.snippet,
                        }
                    )
        if key := ctx.settings.api_key("factcheck_api_key"):
            fc = await ctx.http.get(
                "https://factchecktools.googleapis.com/v1alpha1/claims:search",
                params={"query": art.titulo[:200], "languageCode": "es", "key": key},
            )
            if fc.ok:
                for claim in fc.json().get("claims", []):
                    for rev in claim.get("claimReview", []):
                        verificaciones.append(
                            {
                                "verificador": rev.get("publisher", {}).get("name", "?"),
                                "url": rev.get("url"),
                                "titulo": rev.get("title") or claim.get("text", ""),
                                "valoracion": rev.get("textualRating", ""),
                            }
                        )
        for v in verificaciones:
            if v.get("url"):
                out.append(
                    self.finding(
                        target,
                        Entity(
                            type=EntityType.URL,
                            value=v["url"],
                            attributes={"verificador": v["verificador"]},
                        ),
                        relation="verificacion",
                        title=f"{v['verificador']}: {v['titulo'][:160]}",
                        description=f"Valoración: {v['valoracion'][:200]}",
                        confidence=0.6,
                        source_url=v["url"],
                    )
                )

        # 3. Puntuación explicada.
        puntos, senales = puntuar(art, corroboran, verificaciones)
        etiqueta = "fiable" if puntos >= 65 else "dudosa" if puntos >= 40 else "muy dudosa"
        out.insert(
            0,
            self.finding(
                target,
                target,
                relation="puntuacion_verificacion",
                title=f"Puntuación {puntos}/100 ({etiqueta}): {art.titulo[:150]}",
                description="; ".join(f"{x.puntos:+d} {x.explicacion}" for x in senales),
                confidence=1.0,
                source_url=art.url,
                evidence=[evidencia],
                data={
                    "puntuacion": puntos,
                    "etiqueta": etiqueta,
                    "titulo": art.titulo,
                    "fecha": art.fecha,
                    "autores": art.autores,
                    "copia_archivada": art.archivo,
                    "medio": origen,
                    "senales": [
                        {"senal": x.nombre, "puntos": x.puntos, "explicacion": x.explicacion}
                        for x in senales
                    ],
                    "metodo": "Empieza en 50 y suma o resta según señales observables. Es una "
                    "ayuda para priorizar, no un veredicto.",
                },
            ),
        )
        return out
