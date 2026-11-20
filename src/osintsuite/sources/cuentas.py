"""Nick (WhatsMyName) y email (DNS, Gravatar, PGP, HIBP, emailrep).

Todo es **pasivo**: se consulta información pública o APIs oficiales. A diferencia del
original no se rellenan formularios de login ni de recuperación de contraseña, ni se
sondea SMTP (``RCPT TO``): esas técnicas tocan la cuenta de la víctima (le llegan avisos o
bloqueos), pueden ser ilegales y meten tu IP en listas negras.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import quote

from osintsuite.http import HttpError
from osintsuite.models import Entity, EntityType, Finding
from osintsuite.sources.base import Source, SourceContext

# =============================================================== WhatsMyName

WMN_URL = "https://raw.githubusercontent.com/WebBreacher/WhatsMyName/main/wmn-data.json"
CATEGORIAS_EXCLUIDAS = frozenset({"xx NSFW xx"})


@lru_cache(maxsize=4)
def _cargar_wmn(ruta_usuario: str | None) -> dict[str, Any]:
    """Usa la copia actualizada por el usuario (``osint actualizar-wmn``) o la incluida."""
    if ruta_usuario:
        p = Path(ruta_usuario)
        if p.is_file():
            return dict(json.loads(p.read_text(encoding="utf-8")))
    return dict(
        json.loads(files("osintsuite.data").joinpath("wmn-data.json").read_text(encoding="utf-8"))
    )


def evaluar_wmn(site: dict[str, Any], status: int, body: str) -> bool | None:
    """``True`` existe, ``False`` no existe, ``None`` indeterminado.

    WhatsMyName define para cada sitio el código y el texto esperados cuando la cuenta existe
    (``e_code``/``e_string``) y cuando no (``m_code``/``m_string``). Exigir **código y texto**
    evita el falso positivo clásico del original: sitios que responden 200 a cualquier nick.
    """
    if status == site.get("e_code") and site.get("e_string", "") in body:
        if site.get("m_string") and site["m_string"] in body and site.get("m_code") == status:
            return None  # contradictorio
        return True
    if status == site.get("m_code") and (not site.get("m_string") or site["m_string"] in body):
        return False
    return None


class WhatsMyName(Source):
    name = "whatsmyname"
    description = "Comprueba el nick en ~690 webs (dataset abierto WhatsMyName, CC BY-SA 4.0)"
    input_types = frozenset({EntityType.USERNAME})
    timeout = 600.0

    async def _comprobar(
        self, site: dict[str, Any], nick: str, ctx: SourceContext
    ) -> tuple[dict[str, Any], bool | None, bytes, str]:
        cuenta = quote(nick, safe="")
        url = site["uri_check"].replace("{account}", cuenta)
        headers = dict(site.get("headers") or {})
        body = site.get("post_body")
        try:
            if body:
                r = await ctx.http.request(
                    "POST",
                    url,
                    headers=headers,
                    content=body.replace("{account}", cuenta).encode(),
                    cache_ttl=0,
                    retries=0,
                    timeout=10,
                )
            else:
                r = await ctx.http.get(
                    url, headers=headers, cache_ttl=6 * 3600, retries=0, timeout=10
                )
        except HttpError as exc:
            ctx.log.debug("WMN %s: %s", site["name"], exc)
            return site, None, b"", url
        return site, evaluar_wmn(site, r.status, r.text), r.content, url

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        datos = _cargar_wmn(str(ctx.settings.data_dir / "wmn-data.json"))
        sites = [
            s
            for s in datos["sites"]
            if s.get("valid", True) is not False and s.get("cat") not in CATEGORIAS_EXCLUIDAS
        ]
        resultados = await asyncio.gather(*(self._comprobar(s, target.value, ctx) for s in sites))
        out = []
        indeterminados = 0
        for site, existe, contenido, url in resultados:
            if existe is None:
                indeterminados += 1
                continue
            if not existe:
                continue
            perfil = site.get("uri_pretty", site["uri_check"]).replace("{account}", target.value)
            protegido = bool(site.get("protection"))
            evidencia = ctx.evidence.save(ctx.case_id, url, contenido, "text/html")
            out.append(
                self.finding(
                    target,
                    Entity(
                        type=EntityType.URL,
                        value=perfil,
                        attributes={"plataforma": site["name"], "categoria": site.get("cat")},
                    ),
                    relation="tiene_perfil",
                    title=f"{site['name']} ({site.get('cat', '?')})",
                    description="Detectado por código HTTP y texto esperado (WhatsMyName)."
                    + (
                        " El sitio tiene protección anti-bots: verifica a mano."
                        if protegido
                        else ""
                    ),
                    confidence=0.6 if protegido else 0.8,
                    source_url=perfil,
                    evidence=[evidencia],
                    data={"sitio": site["name"], "categoria": site.get("cat")},
                )
            )
        ctx.log.info(
            "WhatsMyName: %d sitios, %d encontrados, %d indeterminados",
            len(sites),
            len(out),
            indeterminados,
        )
        return out


# ===================================================================== DNS

DKIM_SELECTORES = ("default", "google", "selector1", "selector2", "k1", "mail", "dkim", "s1", "s2")


class DnsCorreo(Source):
    name = "dns"
    description = (
        "Registros DNS (A, AAAA, MX, NS, TXT, CAA) y seguridad del correo: SPF, DMARC, DKIM"
    )
    input_types = frozenset({EntityType.DOMINIO, EntityType.EMAIL})

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        dominio = target.value.split("@")[-1].lower()
        q = ctx.dns.query
        tipos = ("A", "AAAA", "MX", "NS", "TXT", "CAA")
        registros = dict(
            zip(tipos, await asyncio.gather(*(q(dominio, t) for t in tipos)), strict=True)
        )
        dmarc = [t for t in await q(f"_dmarc.{dominio}", "TXT") if t.lower().startswith("v=dmarc1")]
        spf = [t for t in registros["TXT"] if t.lower().startswith("v=spf1")]
        dkim_res = await asyncio.gather(
            *(q(f"{s}._domainkey.{dominio}", "TXT") for s in DKIM_SELECTORES)
        )
        dkim = [
            s for s, r in zip(DKIM_SELECTORES, dkim_res, strict=True) if any("p=" in t for t in r)
        ]
        politica = None
        if dmarc:
            for parte in dmarc[0].split(";"):
                if parte.strip().lower().startswith("p="):
                    politica = parte.strip()[2:]
        riesgo = []
        mx_validos = [mx for mx in registros["MX"] if mx.split()[-1] not in {"", "0", "."}]
        if registros["MX"] and not mx_validos:
            riesgo.append("MX nulo (RFC 7505): el dominio declara que no recibe correo")
        elif not registros["MX"]:
            riesgo.append("sin MX: el dominio no recibe correo")
        if not spf:
            riesgo.append("sin SPF")
        elif spf[0].rstrip().endswith("+all"):
            riesgo.append("SPF con +all (cualquiera puede enviar)")
        if not dmarc:
            riesgo.append("sin DMARC")
        elif politica == "none":
            riesgo.append("DMARC en modo p=none (solo monitoriza)")
        suplantable = not spf or not dmarc or politica == "none"
        out = [
            self.finding(
                target,
                Entity(type=EntityType.DOMINIO, value=dominio)
                if target.type == EntityType.EMAIL
                else target,
                relation="dns",
                title=f"DNS de {dominio}: "
                + ("; ".join(riesgo) if riesgo else "SPF y DMARC correctos"),
                description="Sin SPF/DMARC estrictos es más fácil suplantar el dominio (phishing).",
                confidence=1.0,
                data={
                    **{k.lower(): v for k, v in registros.items()},
                    "spf": spf,
                    "dmarc": dmarc,
                    "dmarc_politica": politica,
                    "dkim_selectores": dkim,
                    "suplantable": suplantable,
                },
            )
        ]
        for mx in mx_validos:
            host = mx.split()[-1]
            out.append(
                self.finding(
                    target,
                    Entity(type=EntityType.DOMINIO, value=host),
                    relation="servidor_correo",
                    title=f"MX {mx}",
                    confidence=1.0,
                )
            )
        for ip in registros["A"] + registros["AAAA"]:
            out.append(
                self.finding(
                    target,
                    Entity(type=EntityType.IP, value=ip),
                    relation="resuelve_a",
                    title=f"{dominio} → {ip}",
                    confidence=1.0,
                )
            )
        return out


# ================================================================ Gravatar / PGP


class Gravatar(Source):
    name = "gravatar"
    description = "Perfil público de Gravatar asociado al email (por hash, sin revelar el email)"
    input_types = frozenset({EntityType.EMAIL})

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        email = target.value.strip().lower().encode()
        sha = hashlib.sha256(email).hexdigest()
        md5 = hashlib.md5(email, usedforsecurity=False).hexdigest()  # el perfil legado usa MD5
        avatar = await ctx.http.get(f"https://gravatar.com/avatar/{sha}?d=404&s=80")
        if avatar.status != 200:
            return []
        out = [
            self.finding(
                target,
                Entity(type=EntityType.URL, value=f"https://gravatar.com/avatar/{sha}"),
                relation="avatar_gravatar",
                title="Tiene avatar en Gravatar",
                confidence=0.95,
                source_url=f"https://gravatar.com/avatar/{sha}",
            )
        ]
        perfil = await ctx.http.get(f"https://gravatar.com/{md5}.json")
        if perfil.ok:
            entradas = perfil.json().get("entry", [])
            for e in entradas[:1]:
                if user := e.get("preferredUsername"):
                    out.append(
                        self.finding(
                            target,
                            Entity(type=EntityType.USERNAME, value=user),
                            relation="usuario_gravatar",
                            title=f"Usuario Gravatar: {user}",
                            confidence=0.9,
                            source_url=e.get("profileUrl"),
                        )
                    )
                if nombre := e.get("displayName"):
                    out.append(
                        self.finding(
                            target,
                            Entity(type=EntityType.PERSONA, value=nombre),
                            relation="nombre_mostrado",
                            title=f"Nombre en Gravatar: {nombre}",
                            confidence=0.5,
                            source_url=e.get("profileUrl"),
                        )
                    )
                for cuenta in e.get("accounts", []):
                    if url := cuenta.get("url"):
                        out.append(
                            self.finding(
                                target,
                                Entity(
                                    type=EntityType.URL,
                                    value=url,
                                    attributes={"plataforma": cuenta.get("shortname")},
                                ),
                                relation="cuenta_verificada_gravatar",
                                title=f"Cuenta enlazada: {cuenta.get('shortname')}",
                                confidence=0.85,
                                source_url=url,
                            )
                        )
        return out


class Pgp(Source):
    name = "pgp"
    description = "Claves PGP publicadas en keys.openpgp.org para el email"
    input_types = frozenset({EntityType.EMAIL})

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        url = f"https://keys.openpgp.org/vks/v1/by-email/{quote(target.value, safe='@')}"
        r = await ctx.http.get(url)
        if r.status != 200 or b"BEGIN PGP PUBLIC KEY" not in r.content:
            return []
        evidencia = ctx.evidence.save(ctx.case_id, url, r.content, "text/plain")
        return [
            self.finding(
                target,
                target,
                relation="clave_pgp",
                title="Tiene clave PGP pública verificada en keys.openpgp.org",
                confidence=0.95,
                source_url=url,
                evidence=[evidencia],
            )
        ]


# ======================================================== HIBP / emailrep (clave)


class Hibp(Source):
    name = "hibp"
    description = "Have I Been Pwned: brechas en las que aparece el email (API oficial, de pago)"
    input_types = frozenset({EntityType.EMAIL})
    requires_api_key = "hibp_api_key"

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        clave = ctx.settings.api_key("hibp_api_key") or ""
        url = f"https://haveibeenpwned.com/api/v3/breachedaccount/{quote(target.value, safe='')}"
        r = await ctx.http.get(
            url,
            params={"truncateResponse": "false"},
            headers={"hibp-api-key": clave},
            cache_ttl=7 * 86400,
        )
        if r.status == 404:
            return []
        if r.status == 401:
            raise HttpError("HIBP rechaza la clave (401)")
        if not r.ok:
            raise HttpError(f"HIBP respondió {r.status}")
        out = []
        for b in r.json():
            # Solo metadatos de la brecha: NUNCA se buscan ni se muestran contraseñas.
            out.append(
                self.finding(
                    # Documento (no dominio) para no pivotar sobre webs como linkedin.com.
                    target,
                    Entity(
                        type=EntityType.DOCUMENTO,
                        value=f"brecha:{b['Name']}",
                        attributes={"dominio": b.get("Domain")},
                    ),
                    relation="en_brecha",
                    title=f"Brecha {b.get('Title', b['Name'])} ({b.get('BreachDate')})",
                    description="Datos expuestos: " + ", ".join(b.get("DataClasses", [])),
                    confidence=0.95,
                    source_url=f"https://haveibeenpwned.com/PwnedWebsites#{b['Name']}",
                    data={
                        k: b.get(k)
                        for k in ("Name", "BreachDate", "PwnCount", "DataClasses", "IsVerified")
                    },
                )
            )
        return out


class EmailRep(Source):
    name = "emailrep"
    description = "Reputación del email en emailrep.io (necesita clave gratuita)"
    input_types = frozenset({EntityType.EMAIL})
    requires_api_key = "emailrep_api_key"

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        r = await ctx.http.get(
            f"https://emailrep.io/{quote(target.value, safe='@')}",
            headers={"Key": ctx.settings.api_key("emailrep_api_key") or ""},
        )
        if not r.ok:
            raise HttpError(f"emailrep respondió {r.status}: {r.text[:120]}")
        d = r.json()
        det = d.get("details", {})
        out = [
            self.finding(
                target,
                target,
                relation="reputacion",
                title=f"Reputación {d.get('reputation')} · sospechoso: {d.get('suspicious')}",
                confidence=0.8,
                source_url="https://emailrep.io/",
                data={
                    "reputation": d.get("reputation"),
                    "suspicious": d.get("suspicious"),
                    "references": d.get("references"),
                    **{
                        k: det.get(k)
                        for k in (
                            "blacklisted",
                            "malicious_activity",
                            "credentials_leaked",
                            "data_breach",
                            "first_seen",
                            "last_seen",
                            "domain_reputation",
                            "new_domain",
                            "free_provider",
                            "disposable",
                            "spoofable",
                            "spf_strict",
                            "dmarc_enforced",
                            "profiles",
                        )
                    },
                },
            )
        ]
        return out
