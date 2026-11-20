"""Exportación de casos.

Formatos de esta fase: JSON (con esquema), CSV, Markdown, GEXF, GraphML y CSV para Maltego.
El informe HTML completo (cronología, grafo interactivo, anexo de evidencias) llega en la Fase 3.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable
from pathlib import Path

import networkx as nx

from osintsuite import __version__
from osintsuite.correlation import build_graph, merge_findings
from osintsuite.models import Case, utcnow

SCHEMA_VERSION = "1.0"


def to_json(case: Case) -> str:
    payload = {
        # El esquema completo se obtiene con `osint esquema` (JSON Schema de `Case`).
        "schema_version": SCHEMA_VERSION,
        "generator": f"osintsuite {__version__}",
        "generated_at": utcnow().isoformat(),
        "case": json.loads(
            case.model_copy(update={"findings": merge_findings(case.findings)}).model_dump_json()
        ),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def json_schema() -> str:
    return json.dumps(Case.model_json_schema(), ensure_ascii=False, indent=2)


CSV_COLUMNS = [
    "id",
    "fuente",
    "tipo",
    "valor",
    "relacion",
    "origen",
    "titulo",
    "confianza",
    "fecha_utc",
    "url_origen",
    "sensible",
    "evidencias_sha256",
]


def _csv_seguro(valor: str) -> str:
    """Evita la inyección de fórmulas al abrir el CSV en Excel/LibreOffice."""
    return "'" + valor if valor[:1] in ("=", "+", "-", "@", "\t", "\r") else valor


def to_csv(case: Case) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for f in merge_findings(case.findings):
        w.writerow(
            [
                _csv_seguro(str(x))
                for x in (
                    f.id,
                    f.source,
                    f.entity.type.value,
                    f.entity.value,
                    f.relation,
                    f.related_to or "",
                    f.title,
                    f"{f.confidence:.2f}",
                    f.timestamp.isoformat(),
                    f.source_url or "",
                    "sí" if f.entity.sensitive else "no",
                    " ".join(e.sha256 for e in f.evidence),
                )
            ]
        )
    return buf.getvalue()


def _md(texto: str) -> str:
    return texto.replace("|", "\\|").replace("\n", " ").replace("<", "&lt;").replace(">", "&gt;")


def to_markdown(case: Case) -> str:
    findings = merge_findings(case.findings)
    lines = [
        f"# Caso {case.id}",
        "",
        f"- **Objetivo**: {_md(case.target.value)} ({case.target.type.value})",
        f"- **Finalidad declarada**: {_md(case.purpose)}",
        f"- **Inicio (UTC)**: {case.created_at.isoformat()}",
        f"- **Estado**: {case.status.value} · **Profundidad**: {case.depth}",
        f"- **Fuentes**: {', '.join(case.sources) or '—'}",
        "",
        "## Hallazgos",
        "",
        "| Fuente | Tipo | Valor | Relación | Confianza | Título | URL |",
        "|---|---|---|---|---|---|---|",
    ]
    for f in sorted(findings, key=lambda x: x.confidence, reverse=True):
        lines.append(
            f"| {f.source} | {f.entity.type.value} | {_md(f.entity.value)} | {f.relation} | "
            f"{f.confidence:.2f} | {_md(f.title)} | {_md(f.source_url or '')} |"
        )
    lines += [
        "",
        "## Ejecución de fuentes",
        "",
        "| Fuente | Entidad | Estado | Hallazgos | Detalle |",
        "|---|---|---|---|---|",
    ]
    for r in case.runs:
        lines.append(
            f"| {r.source} | {_md(r.target)} | {r.status.value} | {r.findings} | "
            f"{_md(r.error or '')} |"
        )
    return "\n".join(lines) + "\n"


def _graph_text(case: Case, writer: Callable[..., None]) -> str:
    buf = io.BytesIO()
    writer(build_graph(case), buf)
    return buf.getvalue().decode("utf-8")


def to_gexf(case: Case) -> str:
    return _graph_text(case, nx.write_gexf)


def to_graphml(case: Case) -> str:
    return _graph_text(case, nx.write_graphml)


def to_maltego_csv(case: Case) -> str:
    """CSV de aristas para el importador de tablas de Maltego (una fila por relación)."""
    g = build_graph(case)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "origen_tipo",
            "origen_valor",
            "destino_tipo",
            "destino_valor",
            "relacion",
            "fuentes",
            "peso",
        ]
    )
    for a, b, d in g.edges(data=True):
        na, nb = g.nodes[a], g.nodes[b]
        w.writerow(
            [
                _csv_seguro(str(x))
                for x in (
                    na["type"],
                    na["label"],
                    nb["type"],
                    nb["label"],
                    d["relations"],
                    d["sources"],
                    d["weight"],
                )
            ]
        )
    return buf.getvalue()


EXPORTERS: dict[str, tuple[str, Callable[[Case], str]]] = {
    "json": (".json", to_json),
    "csv": (".csv", to_csv),
    "md": (".md", to_markdown),
    "gexf": (".gexf", to_gexf),
    "graphml": (".graphml", to_graphml),
    "maltego": ("-maltego.csv", to_maltego_csv),
}


def export(case: Case, fmt: str, out_dir: Path) -> Path:
    if fmt not in EXPORTERS:
        raise ValueError(f"Formato desconocido: {fmt}. Opciones: {', '.join(EXPORTERS)}")
    suffix, fn = EXPORTERS[fmt]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"caso-{case.id}{suffix}"
    path.write_text(fn(case), encoding="utf-8", newline="")
    return path
