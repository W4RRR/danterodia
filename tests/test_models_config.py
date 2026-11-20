from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from osintsuite.config import Settings
from osintsuite.models import Case, Entity, EntityType, Finding, normalize_value


@pytest.mark.parametrize(
    ("tipo", "valor", "esperado"),
    [
        (EntityType.PERSONA, "  José   PÉREZ  Núñez ", "jose perez nunez"),
        (EntityType.EMAIL, "Ana.Lopez@Ejemplo.ES", "ana.lopez@ejemplo.es"),
        (EntityType.DOMINIO, "Ejemplo.COM.", "ejemplo.com"),
        (EntityType.TELEFONO, "+34 600-11 22 33", "+34600112233"),
        (EntityType.TELEFONO, "600 11 22 33", "600112233"),
        (EntityType.IP, "2001:0db8::0001", "2001:db8::1"),
        (EntityType.IP, "no-es-ip", "no-es-ip"),
        (EntityType.URL, "https://a.es/X", "https://a.es/X"),
    ],
)
def test_normalize(tipo: EntityType, valor: str, esperado: str) -> None:
    assert normalize_value(tipo, valor) == esperado


def test_entity_key_y_igualdad() -> None:
    a = Entity(type=EntityType.EMAIL, value="X@Y.com")
    b = Entity(type=EntityType.EMAIL, value="x@y.COM")
    assert a.key == "email:x@y.com"
    assert a == b
    assert len({a, b}) == 1
    assert a != Entity(type=EntityType.USERNAME, value="x@y.com")


def test_entity_vacia_no_valida() -> None:
    with pytest.raises(ValidationError):
        Entity(type=EntityType.EMAIL, value="")


def test_finding_confianza_acotada() -> None:
    e = Entity(type=EntityType.EMAIL, value="a@b.c")
    with pytest.raises(ValidationError):
        Finding(source="x", entity=e, confidence=1.5)


def test_case_entities_y_finalidad() -> None:
    target = Entity(type=EntityType.USERNAME, value="pepe")
    f = Finding(source="s", entity=Entity(type=EntityType.EMAIL, value="p@e.es"))
    case = Case(target=target, purpose="Prueba de test", findings=[f])
    assert set(case.entities()) == {"username:pepe", "email:p@e.es"}
    with pytest.raises(ValidationError):
        Case(target=target, purpose="no")


def test_settings_listas_csv_y_secretos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSINT_DISABLED_SOURCES", "ddg, mojeek")
    monkeypatch.setenv("OSINT_HIBP_API_KEY", "secreto-123")
    s = Settings()
    assert s.disabled_sources == ["ddg", "mojeek"]
    assert s.api_key("hibp_api_key") == "secreto-123"
    assert s.api_key("brave_api_key") is None
    dump = s.public_dump()
    assert dump["hibp_api_key"] == "configurada"
    assert dump["brave_api_key"] == "no configurada"
    assert "secreto-123" not in str(dump)


def test_settings_lista_json_y_clave_vacia() -> None:
    s = Settings(enabled_sources='["ine"]', emailrep_api_key=SecretStr(""))
    assert s.enabled_sources == ["ine"]
    assert s.api_key("emailrep_api_key") is None
    assert s.public_dump()["emailrep_api_key"] == "vacía"


def test_settings_rutas(tmp_path: object) -> None:
    s = Settings(data_dir="d")
    assert s.cases_dir.name == "casos"
    assert s.db_url.startswith("sqlite:///") and s.db_url.endswith("osintsuite.db")
    assert Settings(database_url="sqlite://").db_url == "sqlite://"
