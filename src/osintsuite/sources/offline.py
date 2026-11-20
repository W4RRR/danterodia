"""Fuentes que no necesitan conexión: identificadores, teléfonos, emails y listas locales."""

from __future__ import annotations

import csv
import io
from functools import lru_cache
from importlib.resources import files

import phonenumbers
from email_validator import EmailNotValidError, validate_email
from phonenumbers import carrier, geocoder, timezone

from osintsuite.identifiers import (
    CIF_TIPOS,
    anonimizar_documento,
    coincide_nombre,
    limpiar_id,
    tipo_documento,
)
from osintsuite.models import Entity, EntityType, Finding
from osintsuite.sources.base import Source, SourceContext

# --------------------------------------------------------------- identificadores


class Identificadores(Source):
    name = "identificadores"
    description = "Valida CIF/NIF de empresas y DNI/NIE (dígito de control), sin conexión"
    input_types = frozenset({EntityType.EMPRESA, EntityType.PERSONA})
    offline = True

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        if target.type == EntityType.EMPRESA:
            valor = limpiar_id(target.attributes.get("cif") or target.value)
            tipo = tipo_documento(valor)
            if tipo != "CIF":
                return []
            return [
                self.finding(
                    target,
                    target,
                    relation="identificador_valido",
                    title=f"CIF válido: {valor} ({CIF_TIPOS.get(valor[0], 'tipo desconocido')})",
                    confidence=1.0,
                    data={"cif": valor, "forma_juridica": CIF_TIPOS.get(valor[0])},
                )
            ]
        dni = target.attributes.get("dni")
        if not dni:
            return []
        tipo = tipo_documento(str(dni))
        return [
            self.finding(
                target,
                target,
                relation="identificador_valido" if tipo else "identificador_invalido",
                title=f"{tipo or 'Documento'} {anonimizar_documento(str(dni))}: "
                + ("letra de control correcta" if tipo else "NO supera la validación"),
                confidence=1.0,
                data={"tipo": tipo},
            )
        ]


# ---------------------------------------------------------------------- teléfono

_TIPOS_LINEA = {
    phonenumbers.PhoneNumberType.MOBILE: "móvil",
    phonenumbers.PhoneNumberType.FIXED_LINE: "fijo",
    phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE: "fijo o móvil",
    phonenumbers.PhoneNumberType.TOLL_FREE: "gratuito",
    phonenumbers.PhoneNumberType.PREMIUM_RATE: "tarificación especial",
    phonenumbers.PhoneNumberType.SHARED_COST: "coste compartido",
    phonenumbers.PhoneNumberType.VOIP: "VoIP",
    phonenumbers.PhoneNumberType.PERSONAL_NUMBER: "número personal",
    phonenumbers.PhoneNumberType.UAN: "número corporativo",
}


class Telefono(Source):
    name = "telefono"
    description = "Validez, país, tipo de línea, operador original y zona horaria (phonenumbers)"
    input_types = frozenset({EntityType.TELEFONO})
    offline = True

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        try:
            num = phonenumbers.parse(target.value, "ES")
        except phonenumbers.NumberParseException as exc:
            return [
                self.finding(
                    target,
                    target,
                    relation="numero_invalido",
                    title=f"No es un número válido: {exc}",
                    confidence=1.0,
                )
            ]
        valido = phonenumbers.is_valid_number(num)
        e164 = phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
        datos = {
            "e164": e164,
            "internacional": phonenumbers.format_number(
                num, phonenumbers.PhoneNumberFormat.INTERNATIONAL
            ),
            "valido": valido,
            "pais": phonenumbers.region_code_for_number(num),
            "zona": geocoder.description_for_number(num, "es"),
            "tipo": _TIPOS_LINEA.get(phonenumbers.number_type(num), "desconocido"),
            # Es el operador ORIGINAL del rango: con la portabilidad puede haber cambiado.
            "operador_original": carrier.name_for_number(num, "es") or None,
            "zonas_horarias": list(timezone.time_zones_for_number(num)),
        }
        titulo = (
            f"{datos['internacional']}: {datos['tipo']}, {datos['pais']}"
            + (
                f", operador original {datos['operador_original']}"
                if datos["operador_original"]
                else ""
            )
            if valido
            else f"{e164}: número NO válido según el plan de numeración"
        )
        return [
            self.finding(
                target,
                target,
                relation="analisis_numero",
                title=titulo,
                confidence=0.95,
                description="El operador es el que tenía asignado el rango; puede haber "
                "cambiado por portabilidad.",
                data=datos,
            )
        ]


# ------------------------------------------------------------------------ email


@lru_cache(maxsize=1)
def dominios_desechables() -> frozenset[str]:
    texto = files("osintsuite.data").joinpath("email_desechables.txt").read_text(encoding="utf-8")
    return frozenset(
        line.strip().lower()
        for line in texto.splitlines()
        if line.strip() and not line.startswith("#")
    )


#: Proveedores gratuitos más comunes (para decidir si el dominio merece investigarse).
PROVEEDORES_GRATUITOS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "hotmail.com",
        "hotmail.es",
        "outlook.com",
        "outlook.es",
        "live.com",
        "msn.com",
        "yahoo.com",
        "yahoo.es",
        "ymail.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "aol.com",
        "protonmail.com",
        "proton.me",
        "pm.me",
        "gmx.com",
        "gmx.es",
        "gmx.net",
        "mail.com",
        "zoho.com",
        "yandex.com",
        "yandex.ru",
        "tutanota.com",
        "tuta.io",
        "tuta.com",
        "telefonica.net",
        "movistar.es",
        "terra.es",
        "orange.es",
        "ono.com",
    }
)


class EmailBasico(Source):
    name = "email"
    description = "Sintaxis, proveedor, dominio desechable y alias (sin conexión)"
    input_types = frozenset({EntityType.EMAIL})
    offline = True

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        try:
            info = validate_email(target.value, check_deliverability=False)
        except EmailNotValidError as exc:
            return [
                self.finding(
                    target,
                    target,
                    relation="email_invalido",
                    title=f"Email no válido: {exc}",
                    confidence=1.0,
                )
            ]
        local, dominio = info.local_part, info.domain.lower()
        desechable = dominio in dominios_desechables()
        gratuito = dominio in PROVEEDORES_GRATUITOS
        tipo = "desechable" if desechable else "gratuito" if gratuito else "dominio propio"
        out = [
            self.finding(
                target,
                target,
                relation="analisis_email",
                title=f"Sintaxis correcta · {tipo}",
                confidence=1.0,
                data={
                    "normalizado": info.normalized,
                    "usuario": local,
                    "dominio": dominio,
                    "desechable": desechable,
                    "proveedor_gratuito": gratuito,
                },
            ),
            # El mismo usuario puede existir como nick en otras plataformas (pivoting).
            self.finding(
                target,
                Entity(type=EntityType.USERNAME, value=local.split("+")[0]),
                relation="mismo_alias",
                title="Posible nick derivado del email",
                confidence=0.5,
            ),
        ]
        if not gratuito and not desechable:
            out.append(
                self.finding(
                    target,
                    Entity(type=EntityType.DOMINIO, value=dominio),
                    relation="dominio_email",
                    title="Dominio del email (organización)",
                    confidence=0.95,
                )
            )
        return out


# -------------------------------------------------------------- listas locales


@lru_cache(maxsize=1)
def lista_politicos() -> list[dict[str, str]]:
    texto = (
        files("osintsuite.data")
        .joinpath("politicos_casos_judiciales_2019.tsv")
        .read_text(encoding="utf-8")
    )
    lector = csv.reader(io.StringIO(texto), delimiter="\t")
    cabecera = [c.strip() for c in next(lector)]
    return [dict(zip(cabecera, (c.strip() for c in fila), strict=False)) for fila in lector if fila]


class PoliticosCasosJudiciales(Source):
    name = "politicos_judicial"
    description = "Lista local (Wikipedia, mayo 2019) de políticos implicados en casos judiciales"
    input_types = frozenset({EntityType.PERSONA})
    sensitive = True  # datos relativos a infracciones penales (art. 10 RGPD)
    offline = True

    async def run(self, target: Entity, ctx: SourceContext) -> list[Finding]:
        out = []
        for fila in lista_politicos():
            nombre = fila.get("Nombre", "")
            if nombre and coincide_nombre(target.value, nombre):
                out.append(
                    self.finding(
                        target,
                        Entity(
                            type=EntityType.PERSONA,
                            value=nombre,
                            sensitive=True,
                            attributes={"partido": fila.get("Vinculado al Partido")},
                        ),
                        relation="aparece_en_lista_casos_judiciales",
                        title=f"{nombre}: caso {fila.get('Caso')} "
                        f"({fila.get('Grado de implicación')})",
                        description="Instantánea de mayo de 2019. Imputado/investigado NO equivale "
                        "a condenado; contrasta con la fuente y fechas actuales.",
                        confidence=0.6,
                        source_url="https://es.wikipedia.org/wiki/Anexo:Pol%C3%ADticos_de_Espa%C3%B1a_implicados_en_casos_judiciales",
                        data={**fila, "coincidencia": "nombre", "licencia": "CC BY-SA 4.0"},
                    )
                )
        return out
