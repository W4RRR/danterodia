from __future__ import annotations

import pytest

from danterodia.identifiers import (
    anonimizar_documento,
    coincide_nombre,
    extraer,
    tipo_documento,
    tokens_nombre,
    valida_cif,
    valida_dni,
    valida_iban,
    valida_nie,
)


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        ("12345678Z", True),
        ("12345678-z", True),
        ("12.345.678 Z", True),
        ("12345678A", False),
        ("1234567Z", False),
        ("ABCDEFGHZ", False),
    ],
)
def test_dni(valor: str, esperado: bool) -> None:
    assert valida_dni(valor) is esperado


def test_nie() -> None:
    assert valida_nie("X1234567L")
    assert valida_nie("Y1234567X")
    assert not valida_nie("X1234567A")
    assert not valida_nie("12345678Z")


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        ("A58818501", True),  # sociedad anónima: control numérico
        ("B00000000", True),
        ("Q2826000H", True),  # organismo público: control con letra
        ("Q2826000A", False),
        ("A5881850J", False),  # tipo A no admite letra
        ("Z1234567A", False),
        ("B0000000", False),
    ],
)
def test_cif(valor: str, esperado: bool) -> None:
    assert valida_cif(valor) is esperado


def test_tipo_documento_y_anonimizar() -> None:
    assert tipo_documento("12345678Z") == "DNI"
    assert tipo_documento("X1234567L") == "NIE"
    assert tipo_documento("B00000000") == "CIF"
    assert tipo_documento("hola") is None
    assert anonimizar_documento("12345678Z") == "****5678Z"


def test_iban() -> None:
    assert valida_iban("ES91 2100 0418 4502 0005 1332")
    assert not valida_iban("ES91 2100 0418 4502 0005 1333")
    assert not valida_iban("ES91 2100 0418")
    assert not valida_iban("XX")


def test_extraer_valida_lo_que_encuentra() -> None:
    texto = (
        "Contacto: Ana.Perez@Ejemplo.es o +34 600 11 22 33 (también 954 000 000). "
        "DNI 12345678Z y falso 12345678A. CIF B00000000. IBAN ES91 2100 0418 4502 0005 1332 "
        "y otro inválido ES00 0000 0000 0000 0000 0000. Repite ana.perez@ejemplo.es."
    )
    e = extraer(texto)
    assert e.emails == ["ana.perez@ejemplo.es"]
    assert e.telefonos == ["+34600112233", "+34954000000"]
    assert e.documentos == ["12345678Z"]
    assert e.cifs == ["B00000000"]
    assert e.ibans == ["ES9121000418450200051332"]


def test_tokens_y_coincidencia_de_nombres() -> None:
    assert tokens_nombre("Doña María de la Fuente") == ["MARIA", "FUENTE"]
    assert tokens_nombre("Muñoz") == ["MUÑOZ"]
    assert tokens_nombre("Muñoz", conservar_enie=False) == ["MUNOZ"]
    # El BORME escribe «APELLIDOS NOMBRE»; el orden no importa.
    assert coincide_nombre("Ana María Pérez Ruiz", "PEREZ RUIZ ANA MARIA")
    assert coincide_nombre("Jose Munoz", "MUÑOZ LOPEZ JOSE")
    assert not coincide_nombre("Ana Pérez Ruiz", "PEREZ GARCIA ANA")
    assert not coincide_nombre("", "LO QUE SEA")
