"""Dominios e IP: RDAP, Certificate Transparency, Wayback Machine, tecnologías web y
geolocalización. Shodan y Censys son opcionales (con clave)."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from danterodia.http import HttpError
from danterodia.models import Entity, EntityType, Finding
from danterodia.sources.base import Source, SourceContext

DOMINIO = frozenset({EntityType.DOMINIO})
IP = frozenset({EntityType.IP})


def _vcard(entidad: dict[str, Any]) -> dict[str, str]:
    """Extrae nombre/organización/email de un vCard RDAP (jCard)."""
    out: dict[str, str] = {}
    for campo in entidad.get("vcardArray", [None, []])[1]:
        if len(campo) >= 4 and campo[0] in {"fn", "org", "email", "adr"}:
            valor = campo[3]
            if isinstance(valor, list):
                valor = ", ".join(str(v) for v in valor if v)
            if valor:
                out[campo[0]] = str(valor)
    return out


class Rdap(Source):
    name = "rdap"
    description = "Registro del dominio o de la IP (RDAP, sucesor oficial de WHOIS)"
    input_types = DOMINIO | IP

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        tipo = "ip" if target.type == EntityType.IP else "domain"
        url = f"https://rdap.org/{tipo}/{target.value}"
        r = await ctx.http.get(url, headers={"Accept": "application/rdap+json"})
        if r.status == 404:
            return [
                self.finding(
                    target,
                    target,
                    relation="rdap",
                    title="Sin registro RDAP (¿no existe?)",
                    confidence=0.7,
                    source_url=url,
                )
            ]
        if not r.ok:
            raise HttpError(f"RDAP respondió {r.status}")
        d = r.json()
        eventos = {e.get("eventAction"): e.get("eventDate") for e in d.get("events", [])}
        datos: dict[str, Any] = {
            "handle": d.get("handle"),
            "estado": d.get("status"),
            "registro": eventos.get("registration"),
            "expiracion": eventos.get("expiration"),
            "ultimo_cambio": eventos.get("last changed"),
            "nameservers": [ns.get("ldhName", "").lower() for ns in d.get("nameservers", [])],
        }
        if tipo == "ip":
            datos.update(
                {
                    "rango": f"{d.get('startAddress')} - {d.get('endAddress')}",
                    "nombre_red": d.get("name"),
                    "pais": d.get("country"),
                }
            )
        entidades = []
        for ent in d.get("entities", []):
            vc = _vcard(ent)
            roles = ent.get("roles", [])
            # Los datos de personas físicas suelen estar ocultos por el RGPD («REDACTED»).
            if vc and not any("redact" in v.lower() for v in vc.values()):
                entidades.append({"roles": roles, **vc})
        datos["contactos"] = entidades
        evidencia = ctx.evidence.save(ctx.case_id, url, r.content, "application/rdap+json")
        titulo = (
            f"Registrado {datos['registro'] or '?'} · expira {datos['expiracion'] or '?'}"
            if tipo == "domain"
            else f"Red {datos.get('nombre_red')} ({datos.get('rango')})"
        )
        out = [
            self.finding(
                target,
                target,
                relation="rdap",
                title=titulo,
                confidence=1.0,
                source_url=url,
                evidence=[evidencia],
                data=datos,
            )
        ]
        for c in entidades:
            nombre = c.get("org") or c.get("fn")
            if nombre and "registrar" not in c["roles"]:
                out.append(
                    self.finding(
                        target,
                        Entity(type=EntityType.EMPRESA, value=nombre),
                        relation="titular_" + "_".join(c["roles"]),
                        title=f"{', '.join(c['roles'])}: {nombre}",
                        confidence=0.8,
                        source_url=url,
                    )
                )
            if email := c.get("email"):
                out.append(
                    self.finding(
                        target,
                        Entity(type=EntityType.EMAIL, value=email),
                        relation="email_contacto_rdap",
                        title=f"Contacto: {email}",
                        confidence=0.8,
                        source_url=url,
                    )
                )
        return out


class CrtSh(Source):
    name = "crtsh"
    description = "Subdominios en certificados TLS públicos (Certificate Transparency, crt.sh)"
    input_types = DOMINIO
    timeout = 180.0

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        dominio = target.value.lower()
        url = "https://crt.sh/"
        r = await ctx.http.get(url, params={"q": f"%.{dominio}", "output": "json"})
        if not r.ok:
            raise HttpError(f"crt.sh respondió {r.status} (suele fallar a ratos; reintenta)")
        subdominios: dict[str, str] = {}
        for cert in r.json():
            for nombre in str(cert.get("name_value", "")).splitlines():
                n = nombre.strip().lower().lstrip("*.")
                if n.endswith("." + dominio) and n != dominio:
                    subdominios.setdefault(n, cert.get("not_before", ""))
        evidencia = ctx.evidence.save(
            ctx.case_id, f"{url}?q=%.{dominio}&output=json", r.content, "application/json"
        )
        return [
            self.finding(
                target,
                Entity(type=EntityType.DOMINIO, value=sub),
                relation="subdominio",
                title=f"{sub} (certificado desde {fecha[:10]})",
                confidence=0.9,
                source_url=f"https://crt.sh/?q={sub}",
                evidence=[evidencia],
            )
            for sub, fecha in sorted(subdominios.items())[:500]
        ]


class Wayback(Source):
    name = "wayback"
    description = "Histórico en la Wayback Machine (primera y última captura, nº de capturas)"
    input_types = DOMINIO | frozenset({EntityType.URL})

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        url = "https://web.archive.org/cdx/search/cdx"
        base = {"url": target.value, "output": "json", "fl": "timestamp,original,statuscode"}
        primero = (await ctx.http.get(url, params={**base, "limit": 1})).json()
        ultimo = (await ctx.http.get(url, params={**base, "limit": -1})).json()
        if len(primero) < 2:
            return []
        f0, f1 = primero[1][0], ultimo[1][0] if len(ultimo) > 1 else primero[1][0]
        original_ultimo = ultimo[1][1] if len(ultimo) > 1 else primero[1][1]

        def legible(ts: str) -> str:
            return f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"

        return [
            self.finding(
                target,
                target,
                relation="historico_web",
                title=f"Archivado desde {legible(f0)} hasta {legible(f1)}",
                confidence=1.0,
                source_url=f"https://web.archive.org/web/{f0}/{primero[1][1]}",
                data={
                    "primera_captura": f0,
                    "ultima_captura": f1,
                    "url_primera": f"https://web.archive.org/web/{f0}/{primero[1][1]}",
                    "url_ultima": f"https://web.archive.org/web/{f1}/{original_ultimo}",
                },
            )
        ]


#: Huellas sencillas y explicables (cabecera o patrón en el HTML → tecnología).
HUELLAS: list[tuple[str, str, str]] = [
    ("header:server", r"nginx", "nginx"),
    ("header:server", r"apache", "Apache"),
    ("header:server", r"cloudflare", "Cloudflare"),
    ("header:server", r"microsoft-iis", "IIS"),
    ("header:x-powered-by", r"php", "PHP"),
    ("header:x-powered-by", r"asp\.net", "ASP.NET"),
    ("header:x-powered-by", r"express", "Express (Node.js)"),
    ("header:x-generator", r"drupal", "Drupal"),
    ("header:cf-ray", r".", "Cloudflare"),
    ("header:x-vercel-id", r".", "Vercel"),
    ("header:x-amz-cf-id", r".", "Amazon CloudFront"),
    ("html", r"wp-content/|wp-includes/", "WordPress"),
    ("html", r"cdn\.shopify\.com", "Shopify"),
    ("html", r"/_next/static/", "Next.js"),
    ("html", r"static\.wixstatic\.com", "Wix"),
    ("html", r"googletagmanager\.com/gtm\.js", "Google Tag Manager"),
    ("html", r"google-analytics\.com|gtag\(", "Google Analytics"),
    ("html", r"joomla", "Joomla"),
    ("html", r"prestashop", "PrestaShop"),
    ("html", r"jquery(\.min)?\.js", "jQuery"),
]


class Tecnologias(Source):
    name = "tecnologias"
    description = "Tecnologías web por cabeceras y HTML (huellas explicables) y enlaces sociales"
    input_types = DOMINIO | frozenset({EntityType.URL})

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        url = target.value if target.type == EntityType.URL else f"https://{target.value}/"
        r = await ctx.http.get(url)
        html = r.text if "html" in (r.content_type or "") else ""
        detectadas: dict[str, str] = {}
        for donde, patron, nombre in HUELLAS:
            if donde.startswith("header:"):
                valor = r.headers.get(donde.split(":", 1)[1], "")
                if valor and re.search(patron, valor, re.I):
                    detectadas.setdefault(nombre, f"cabecera {donde[7:]}: {valor[:60]}")
            elif html and re.search(patron, html, re.I):
                detectadas.setdefault(nombre, f"patrón «{patron}» en el HTML")
        soup = BeautifulSoup(html, "lxml") if html else None
        generator = soup.find("meta", attrs={"name": "generator"}) if soup else None
        if generator and generator.get("content"):
            detectadas.setdefault(str(generator["content"]), "meta generator")
        titulo = soup.title.get_text(strip=True) if soup and soup.title else ""
        evidencia = ctx.evidence.save(ctx.case_id, r.url, r.content, r.content_type)
        out = [
            self.finding(
                target,
                target,
                relation="tecnologias",
                title=(", ".join(sorted(detectadas)) or "Sin tecnologías reconocidas")
                + (f" · «{titulo[:80]}»" if titulo else ""),
                confidence=0.8,
                source_url=r.url,
                evidence=[evidencia],
                data={
                    "http_status": r.status,
                    "tecnologias": detectadas,
                    "titulo": titulo,
                    "cabeceras_seguridad": {
                        h: r.headers.get(h)
                        for h in (
                            "strict-transport-security",
                            "content-security-policy",
                            "x-frame-options",
                        )
                    },
                },
            )
        ]
        redes = (
            "facebook.com",
            "x.com",
            "twitter.com",
            "instagram.com",
            "linkedin.com",
            "youtube.com",
            "tiktok.com",
            "github.com",
        )
        vistos: set[str] = set()
        for a in soup.find_all("a", href=True) if soup else []:
            href = str(a["href"])
            host = (urlsplit(href).hostname or "").removeprefix("www.")
            if host in redes and href not in vistos and len(vistos) < 20:
                vistos.add(href)
                out.append(
                    self.finding(
                        target,
                        Entity(type=EntityType.URL, value=href, attributes={"plataforma": host}),
                        relation="enlace_red_social",
                        title=f"Enlace a {host}",
                        confidence=0.85,
                        source_url=r.url,
                    )
                )
        return out


class IpInfo(Source):
    name = "ipinfo"
    description = "Geolocalización aproximada, ASN y nombre inverso de una IP (ipinfo.io)"
    input_types = IP

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        token = ctx.settings.api_key("ipinfo_token")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        r = await ctx.http.get(f"https://ipinfo.io/{target.value}/json", headers=headers)
        if not r.ok:
            raise HttpError(f"ipinfo respondió {r.status}")
        d = r.json()
        ptr = await ctx.dns.reverse(target.value)
        datos = {
            k: d.get(k)
            for k in (
                "hostname",
                "city",
                "region",
                "country",
                "loc",
                "org",
                "postal",
                "timezone",
                "anycast",
            )
        }
        datos["ptr"] = ptr
        out = [
            self.finding(
                target,
                target,
                relation="geolocalizacion_ip",
                title=f"{d.get('org') or '?'} · {d.get('city') or '?'}, {d.get('country') or '?'}",
                description="Ubicación aproximada: suele ser la del proveedor, no la del usuario.",
                confidence=0.7,
                source_url=f"https://ipinfo.io/{target.value}",
                data=datos,
            )
        ]
        for nombre in {*ptr, *([d["hostname"]] if d.get("hostname") else [])}:
            out.append(
                self.finding(
                    target,
                    Entity(type=EntityType.DOMINIO, value=nombre),
                    relation="dns_inverso",
                    title=f"PTR: {nombre}",
                    confidence=0.9,
                )
            )
        return out


class Shodan(Source):
    name = "shodan"
    description = "Puertos y servicios expuestos de una IP (Shodan, necesita clave)"
    input_types = IP
    requires_api_key = "shodan_api_key"

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        r = await ctx.http.get(
            f"https://api.shodan.io/shodan/host/{target.value}",
            params={"key": ctx.settings.api_key("shodan_api_key") or ""},
        )
        if r.status == 404:
            return []
        if not r.ok:
            raise HttpError(f"Shodan respondió {r.status}")
        d = r.json()
        servicios = [
            f"{s.get('port')}/{s.get('transport')} {s.get('product') or ''}".strip()
            for s in d.get("data", [])
        ]
        return [
            self.finding(
                target,
                target,
                relation="servicios_expuestos",
                title=f"{len(servicios)} servicios: {', '.join(servicios[:8])}",
                confidence=0.9,
                source_url=f"https://www.shodan.io/host/{target.value}",
                data={
                    "puertos": d.get("ports"),
                    "servicios": servicios,
                    "vulns": d.get("vulns"),
                    "org": d.get("org"),
                    "ultima_actualizacion": d.get("last_update"),
                },
            )
        ]


class Censys(Source):
    name = "censys"
    description = "Servicios expuestos de una IP (Censys Search v2, necesita ID y secreto)"
    input_types = IP
    requires_api_key = "censys_api_id"

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        import base64

        api_id = ctx.settings.api_key("censys_api_id") or ""
        cred = f"{api_id}:{ctx.settings.api_key('censys_api_secret') or ''}"
        r = await ctx.http.get(
            f"https://search.censys.io/api/v2/hosts/{target.value}",
            headers={"Authorization": "Basic " + base64.b64encode(cred.encode()).decode()},
        )
        if r.status == 404:
            return []
        if not r.ok:
            raise HttpError(f"Censys respondió {r.status}")
        res = r.json().get("result", {})
        servicios = [f"{s.get('port')}/{s.get('service_name')}" for s in res.get("services", [])]
        return [
            self.finding(
                target,
                target,
                relation="servicios_expuestos",
                title=f"{len(servicios)} servicios: {', '.join(servicios[:8])}",
                confidence=0.9,
                source_url=f"https://search.censys.io/hosts/{target.value}",
                data={"servicios": servicios, "asn": res.get("autonomous_system")},
            )
        ]
