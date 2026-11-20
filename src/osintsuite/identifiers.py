"""Identificadores españoles y extractores de datos en texto.

Sustituye a ``modules/parsers.py`` del original, corrigiendo sus fallos:

- la regex de DNI estaba anclada con ``^…$`` y nunca encontraba nada dentro de un texto;
- no se comprobaba la letra de control (DNI/NIE) ni el dígito de control (CIF);
- la regex de IBAN daba muchos falsos positivos: ahora se valida el módulo 97;
- los emails se descartaban si medían 20 caracteres o más.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

LETRAS_DNI = "TRWAGMYFPDXBNJZSQVHLCKE"
_NIE_PREFIJO = {"X": "0", "Y": "1", "Z": "2"}
_CIF_LETRA_CONTROL = "JABCDEFGHI"
#: Tipo de entidad según la primera letra del CIF/NIF de persona jurídica.
CIF_TIPOS = {
    "A": "Sociedad anónima",
    "B": "Sociedad de responsabilidad limitada",
    "C": "Sociedad colectiva",
    "D": "Sociedad comanditaria",
    "E": "Comunidad de bienes",
    "F": "Sociedad cooperativa",
    "G": "Asociación o fundación",
    "H": "Comunidad de propietarios",
    "J": "Sociedad civil",
    "N": "Entidad extranjera",
    "P": "Corporación local",
    "Q": "Organismo público",
    "R": "Congregación o institución religiosa",
    "S": "Órgano de la Administración del Estado o de las CC. AA.",
    "U": "Unión temporal de empresas",
    "V": "Otro tipo",
    "W": "Establecimiento permanente de entidad no residente",
}


def limpiar_id(valor: str) -> str:
    return re.sub(r"[\s.\-]", "", valor).upper()


def valida_dni(valor: str) -> bool:
    v = limpiar_id(valor)
    if not re.fullmatch(r"\d{8}[A-Z]", v):
        return False
    return LETRAS_DNI[int(v[:8]) % 23] == v[8]


def valida_nie(valor: str) -> bool:
    v = limpiar_id(valor)
    if not re.fullmatch(r"[XYZ]\d{7}[A-Z]", v):
        return False
    numero = _NIE_PREFIJO[v[0]] + v[1:8]
    return LETRAS_DNI[int(numero) % 23] == v[8]


def valida_cif(valor: str) -> bool:
    """Valida un NIF de persona jurídica (antiguo CIF) por su carácter de control."""
    v = limpiar_id(valor)
    if not re.fullmatch(r"[ABCDEFGHJNPQRSUVW]\d{7}[0-9A-J]", v):
        return False
    digitos = v[1:8]
    pares = sum(int(d) for d in digitos[1::2])
    impares = 0
    for d in digitos[0::2]:
        doble = int(d) * 2
        impares += doble // 10 + doble % 10
    control = (10 - (pares + impares) % 10) % 10
    letra, digito = _CIF_LETRA_CONTROL[control], str(control)
    final = v[8]
    if v[0] in "PQRSNW":  # entidades que usan letra
        return final == letra
    if v[0] in "ABEH":  # entidades que usan número
        return final == digito
    return final in (letra, digito)


def tipo_documento(valor: str) -> str | None:
    if valida_dni(valor):
        return "DNI"
    if valida_nie(valor):
        return "NIE"
    if valida_cif(valor):
        return "CIF"
    return None


def valida_iban(valor: str) -> bool:
    v = limpiar_id(valor)
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", v):
        return False
    if v.startswith("ES") and len(v) != 24:
        return False
    reordenado = v[4:] + v[:4]
    numero = "".join(str(int(c, 36)) for c in reordenado)
    return int(numero) % 97 == 1


def anonimizar_documento(valor: str) -> str:
    """``12345678Z`` → ``****5678Z``: para logs e informes (minimización)."""
    v = limpiar_id(valor)
    return "*" * max(0, len(v) - 5) + v[-5:]


# ------------------------------------------------------------ extractores

_RE_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,253}\.[A-Za-z]{2,24}\b")
_RE_DNI = re.compile(r"\b(?:\d{8}|[XYZ]\d{7})[\s\-]?[A-Za-z]\b")
_RE_CIF = re.compile(r"\b[ABCDEFGHJNPQRSUVW][\-\s]?\d{7}[0-9A-J]\b")
_RE_IBAN = re.compile(r"\bES\d{2}(?:[ ]?\d{4}){5}\b")
_RE_TEL_ES = re.compile(
    # 9 dígitos (empieza por 6, 7, 8 o 9) con separadores opcionales: «600 11 22 33», «954 000 000».
    r"(?<![\d+])(?:\+34[\s.\-]?|0034[\s.\-]?)?[6789]\d{2}(?:[\s.\-]?\d){6}(?!\d)"
)


@dataclass(frozen=True, slots=True)
class Extraidos:
    emails: list[str]
    telefonos: list[str]
    documentos: list[str]
    cifs: list[str]
    ibans: list[str]


def _unicos(valores: list[str]) -> list[str]:
    return list(dict.fromkeys(valores))


def extraer(texto: str) -> Extraidos:
    """Extrae y **valida** emails, teléfonos españoles, DNI/NIE, CIF e IBAN de un texto."""
    emails = [e.lower().rstrip(".") for e in _RE_EMAIL.findall(texto)]
    telefonos = [re.sub(r"[\s.\-]", "", t) for t in _RE_TEL_ES.findall(texto)]
    telefonos = [t if t.startswith("+") else "+34" + t.removeprefix("0034") for t in telefonos]
    documentos = [limpiar_id(d) for d in _RE_DNI.findall(texto)]
    documentos = [d for d in documentos if valida_dni(d) or valida_nie(d)]
    cifs = [limpiar_id(c) for c in _RE_CIF.findall(texto) if valida_cif(c)]
    ibans = [limpiar_id(i) for i in _RE_IBAN.findall(texto) if valida_iban(i)]
    return Extraidos(
        _unicos(emails), _unicos(telefonos), _unicos(documentos), _unicos(cifs), _unicos(ibans)
    )


# ------------------------------------------------------------ nombres


def tokens_nombre(nombre: str, *, conservar_enie: bool = True) -> list[str]:
    """Tokens normalizados de un nombre: mayúsculas, sin tildes y sin partículas («de», «la»…).

    Con ``conservar_enie=False`` la «Ñ» también se convierte en «N»: útil para *buscar*
    (más tolerante con quien escribe «Munoz»), no para consultar frecuencias del INE.
    """
    out = []
    for ch in unicodedata.normalize("NFD", nombre):
        if unicodedata.combining(ch) and (ch != "̃" or not conservar_enie):
            continue
        out.append(ch)
    limpio = unicodedata.normalize("NFC", "".join(out)).upper()
    limpio = re.sub(r"[^A-ZÑÇ0-9 ]", " ", limpio)
    particulas = {
        "DE",
        "DEL",
        "LA",
        "LAS",
        "LOS",
        "Y",
        "I",
        "DA",
        "DO",
        "DON",
        "DOÑA",
        "DONA",
        "DÑA",
    }
    return [t for t in limpio.split() if t not in particulas]


def coincide_nombre(buscado: str, candidato: str) -> bool:
    """¿Contiene ``candidato`` todos los términos de ``buscado``, en cualquier orden?

    El BORME escribe «APELLIDO1 APELLIDO2 NOMBRE» y el BOE «don Nombre Apellido1 Apellido2»,
    por eso se compara como conjunto de términos y no como cadena. Ignora tildes y «ñ».
    """
    b = tokens_nombre(buscado, conservar_enie=False)
    c = set(tokens_nombre(candidato, conservar_enie=False))
    return bool(b) and all(t in c for t in b)
