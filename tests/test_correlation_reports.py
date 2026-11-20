from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import networkx as nx
import pytest

from osintsuite.correlation import build_graph, combine, merge_findings
from osintsuite.models import Case, Entity, EntityType, Evidence, Finding
from osintsuite.reports import EXPORTERS, export, json_schema, to_csv, to_json, to_markdown

H1, H2 = "a" * 64, "b" * 64


def _case() -> Case:
    t = Entity(type=EntityType.USERNAME, value="pepe")
    email = Entity(type=EntityType.EMAIL, value="pepe@ejemplo.es")
    malicioso = Entity(type=EntityType.URL, value='=HYPERLINK("http://x")')
    return Case(
        target=t,
        purpose="Prueba de informes",
        sources=["a", "b"],
        findings=[
            Finding(
                source="a",
                entity=email,
                related_to=t.key,
                relation="usa_email",
                confidence=0.6,
                evidence=[Evidence(url="u1", sha256=H1)],
            ),
            Finding(
                source="a",
                entity=email,
                related_to=t.key,
                relation="usa_email",
                confidence=0.4,
                evidence=[Evidence(url="u2", sha256=H2)],
            ),
            Finding(
                source="b", entity=email, related_to=t.key, relation="usa_email", confidence=0.6
            ),
            Finding(source="b", entity=malicioso, related_to=t.key, title="<script>x</script>|y"),
            Finding(source="ine", entity=t, related_to=t.key, relation="atributo"),
        ],
    )


def test_combine() -> None:
    assert combine([]) == 0.0
    assert combine([0.6, 0.6]) == 0.84
    assert combine([1.0, 0.1]) == 1.0


def test_merge_conserva_mayor_confianza_y_une_evidencias() -> None:
    merged = merge_findings(_case().findings)
    de_a = [f for f in merged if f.source == "a"]
    assert len(de_a) == 1 and de_a[0].confidence == 0.6
    assert {e.sha256 for e in de_a[0].evidence} == {H1, H2}
    # Si el duplicado llega después con más confianza, gana él y conserva las evidencias.
    f1, f2 = _case().findings[1], _case().findings[0]
    (m,) = merge_findings([f1, f2])
    assert m.confidence == 0.6 and {e.sha256 for e in m.evidence} == {H1, H2}


def test_grafo() -> None:
    g = build_graph(_case())
    assert g.nodes["username:pepe"]["target"] is True
    arista = g.edges["username:pepe", "email:pepe@ejemplo.es"]
    assert arista["weight"] == 0.84  # a (0.6) y b (0.6) combinadas
    assert arista["sources"] == "a,b"
    assert not g.has_edge("username:pepe", "username:pepe")  # atributos no son aristas


def test_json_y_esquema() -> None:
    data = json.loads(to_json(_case()))
    assert data["schema_version"] == "1.0"
    assert data["case"]["purpose"] == "Prueba de informes"
    assert len(data["case"]["findings"]) == 4  # deduplicado
    schema = json.loads(json_schema())
    assert "findings" in schema["properties"]


def test_csv_neutraliza_formulas() -> None:
    filas = list(csv.reader(io.StringIO(to_csv(_case()))))
    assert filas[0][0] == "id"
    valores = [f[3] for f in filas[1:]]
    assert '\'=HYPERLINK("http://x")' in valores


def test_markdown_escapa() -> None:
    md = to_markdown(_case())
    assert "<script>" not in md and "&lt;script&gt;" in md and "\\|y" in md


@pytest.mark.parametrize("fmt", sorted(EXPORTERS))
def test_export_todos(fmt: str, tmp_path: Path) -> None:
    path = export(_case(), fmt, tmp_path / "out")
    assert path.exists() and path.stat().st_size > 0
    if fmt in {"gexf", "graphml"}:
        g = (nx.read_gexf if fmt == "gexf" else nx.read_graphml)(path)
        assert g.number_of_edges() == 2


def test_export_formato_desconocido(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        export(_case(), "pdf", tmp_path)
