from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from danterodia.cli import app
from danterodia.models import Entity, EntityType
from danterodia.sources.ine import clave, estimar, separar_nombre, tablas

runner = CliRunner()


def test_clave_conserva_enie() -> None:
    assert clave("  muñoz  ") == "MUÑOZ"
    assert clave("José María") == "JOSE MARIA"
    t = tablas()
    # Bug del original: convertía Ñ en N y leía la fila equivocada (MUNOZ: 150 vs MUÑOZ: >100.000).
    assert t.apellidos["MUÑOZ"].primero > 100_000
    assert t.apellidos["MUNOZ"].primero < 1_000


def test_separar_nombre() -> None:
    assert separar_nombre(Entity(type=EntityType.PERSONA, value="María José García López")) == (
        "María José",
        "García",
        "López",
    )
    assert separar_nombre(Entity(type=EntityType.PERSONA, value="Ana García")) == (
        "Ana",
        "García",
        None,
    )
    assert separar_nombre(Entity(type=EntityType.PERSONA, value="Cher")) == ("Cher", None, None)
    e = Entity(
        type=EntityType.PERSONA,
        value="x",
        attributes={"nombre": "Juan Carlos", "apellido1": "De la Fuente"},
    )
    assert separar_nombre(e) == ("Juan Carlos", "De la Fuente", None)


def test_estimar() -> None:
    comun = estimar("Antonio", "García", "García")
    assert comun["frecuencia_nombre_hombres"] > 500_000  # type: ignore[operator]
    assert comun["personas_estimadas"] > 100  # type: ignore[operator]
    raro = estimar("Zlatan", "Zvunca", "Zuica")
    assert raro["personas_estimadas"] < 1  # type: ignore[operator]
    # Nombre compuesto que no existe entero: usa el primer nombre.
    assert estimar("Antonio Xyzw", None, None)["frecuencia_nombre_hombres"] > 0  # type: ignore[operator]
    assert estimar("Nadie", "Inexistentez", None)["frecuencia_apellido1"] == 0


@pytest.fixture
def entorno(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("DANTERODIA_DATA_DIR", str(tmp_path / "datos"))
    monkeypatch.setenv("DANTERODIA_HIBP_API_KEY", "no-mostrar-esto")
    monkeypatch.setenv("COLUMNS", "250")  # que Rich no recorte las tablas
    return tmp_path


def test_cli_flujo_completo(entorno: Path) -> None:
    r = runner.invoke(app, ["version"])
    assert r.exit_code == 0 and "danterodia" in r.stdout

    r = runner.invoke(app, ["fuentes"])
    assert r.exit_code == 0 and "ine" in r.stdout

    r = runner.invoke(app, ["config"])
    assert r.exit_code == 0 and "no-mostrar-esto" not in r.stdout and "configurada" in r.stdout

    r = runner.invoke(app, ["esquema"])
    assert r.exit_code == 0 and json.loads(r.stdout)["title"] == "Case"

    salida = entorno / "informes"
    r = runner.invoke(
        app,
        [
            "persona",
            "Ana García López",
            "-f",
            "Prueba de la CLI",
            "-s",
            "ine",
            "-r",
            "json,md",
            "-o",
            str(salida),
        ],
    )
    assert r.exit_code == 0, r.stdout
    assert "ine" in r.stdout
    informes = sorted(p.name for p in salida.iterdir())
    assert len(informes) == 2
    case_id = informes[0].removeprefix("caso-").rsplit(".", 1)[0]

    r = runner.invoke(app, ["caso", "listar"])
    assert r.exit_code == 0 and case_id in r.stdout

    r = runner.invoke(app, ["caso", "ver", case_id, "-r", "csv", "-o", str(salida)])
    assert r.exit_code == 0 and "Prueba de la CLI" in r.stdout

    r = runner.invoke(app, ["caso", "retomar", case_id])
    assert r.exit_code == 0

    r = runner.invoke(app, ["caso", "comparar", case_id, case_id])
    assert r.exit_code == 0 and "En ambos" in r.stdout

    r = runner.invoke(app, ["caso", "borrar", case_id, "--si"])
    assert r.exit_code == 0
    r = runner.invoke(app, ["caso", "ver", case_id])
    assert r.exit_code == 1

    r = runner.invoke(app, ["caso", "purgar", "--si"])
    assert r.exit_code == 0 and "0" in r.stdout

    r = runner.invoke(app, ["limpiar-cache"])
    assert r.exit_code == 0


def test_cli_errores(entorno: Path) -> None:
    r = runner.invoke(app, ["nick", "pepe", "-f", "Prueba de errores", "-r", "pdf"])
    assert r.exit_code != 0 and "Formato no válido" in r.output
    r = runner.invoke(app, ["nick", "pepe", "-f", "Prueba de errores", "-s", "no_existe"])
    assert r.exit_code != 0 and "desconocidas" in r.output
    # Sin finalidad y sin terminal interactiva: error claro.
    r = runner.invoke(app, ["email", "a@b.es"])
    assert r.exit_code != 0 and "finalidad" in r.output.lower()
    # Finalidad pedida por pantalla.
    r = runner.invoke(app, ["nick", "pepe", "-s", "ine"], input="Prueba con prompt\n")
    assert r.exit_code != 0 or "Prueba" in r.output
