"""Correlación: deduplicación de hallazgos, confianza combinada y grafo de entidades.

**Confianza combinada.** Si varias fuentes *independientes* encuentran la misma relación,
la confianza sube. Se usa la regla «o ruidoso»: ``1 − Π(1 − cᵢ)``. Con dos fuentes al 0,6
el resultado es 0,84. Es fácil de explicar y nunca pasa de 1. Los hallazgos repetidos de la
*misma* fuente no suman: se queda el de mayor confianza.

**Nombres comunes.** Una coincidencia solo por nombre con «María García López» vale mucho
menos que con un nombre único: ver :func:`ajustar_por_frecuencia`.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

import networkx as nx

from danterodia.models import Case, Finding


def combine(confidences: list[float]) -> float:
    resto = 1.0
    for c in confidences:
        resto *= 1.0 - c
    return round(1.0 - resto, 4)


def factor_frecuencia(personas: float) -> float:
    """Cuánto creer una coincidencia **solo por nombre** según cuánta gente lo comparte.

    ``1 / (1 + log10(N))``: nombre único → 1; 10 personas → 0,5; 100 → 0,33; 10 000 → 0,2.
    Penaliza sin llegar a anular, porque el nombre sigue siendo un indicio.
    """
    return round(1.0 / (1.0 + math.log10(max(1.0, personas))), 3)


def ajustar_por_frecuencia(case: Case) -> int:
    """Rebaja la confianza de los hallazgos que dependen solo del nombre del objetivo.

    Usa la estimación de la fuente ``ine``. Devuelve cuántos hallazgos se ajustaron y deja la
    confianza original y el motivo en ``data`` (para que el ajuste sea explicable).
    """
    estimacion = next(
        (
            f.data.get("personas_estimadas")
            for f in case.findings
            if f.source == "ine" and f.entity.key == case.target.key
        ),
        None,
    )
    if not isinstance(estimacion, int | float) or estimacion <= 1:
        return 0
    factor = factor_frecuencia(float(estimacion))
    n = 0
    for f in case.findings:
        if f.data.get("coincidencia") != "nombre" or "confianza_original" in f.data:
            continue
        if f.related_to != case.target.key:
            continue
        f.data["confianza_original"] = f.confidence
        f.data["ajuste_confianza"] = (
            f"nombre compartido por ~{estimacion:,.0f} personas según el INE: ×{factor}"
        ).replace(",", ".")
        f.confidence = round(f.confidence * factor, 3)
        n += 1
    return n


def merge_findings(findings: list[Finding]) -> list[Finding]:
    """Quita duplicados exactos (misma fuente, entidad, origen y relación).

    Conserva el de mayor confianza y une sus evidencias. El orden de entrada se mantiene.
    """
    mejores: dict[tuple[str, str, str | None, str], Finding] = {}
    for f in findings:
        k = f.dedup_key
        actual = mejores.get(k)
        if actual is None:
            mejores[k] = f.model_copy(deep=True)
            continue
        if f.confidence > actual.confidence:
            ganador, otras = f.model_copy(deep=True), actual.evidence
            mejores[k] = ganador
        else:
            ganador, otras = actual, f.evidence
        hashes = {e.sha256 for e in ganador.evidence}
        ganador.evidence.extend(e for e in otras if e.sha256 not in hashes)
    return list(mejores.values())


def build_graph(case: Case) -> Any:
    """Grafo no dirigido: nodos = entidades, aristas = relaciones (con fuentes y confianza)."""
    g = nx.Graph()
    entidades = case.entities()
    for key, e in entidades.items():
        g.add_node(
            key,
            label=e.value,
            type=e.type.value,
            sensitive=e.sensitive,
            target=(key == case.target.key),
        )
    por_arista: dict[tuple[str, str], list[Finding]] = defaultdict(list)
    for f in merge_findings(case.findings):
        origen = f.related_to or case.target.key
        if origen == f.entity.key:
            continue  # atributo del propio nodo, no una relación
        a, b = sorted((origen, f.entity.key))
        por_arista[(a, b)].append(f)
    for (a, b), fs in por_arista.items():
        if a not in g:
            g.add_node(
                a, label=a.split(":", 1)[-1], type=a.split(":", 1)[0], sensitive=False, target=False
            )
        # Por fuente se toma la mayor confianza, y luego se combinan las fuentes.
        por_fuente: dict[str, float] = {}
        for f in fs:
            por_fuente[f.source] = max(por_fuente.get(f.source, 0.0), f.confidence)
        g.add_edge(
            a,
            b,
            relations=",".join(sorted({f.relation for f in fs})),
            sources=",".join(sorted(por_fuente)),
            weight=combine(list(por_fuente.values())),
            findings=len(fs),
        )
    return g
