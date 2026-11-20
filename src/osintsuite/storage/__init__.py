"""Persistencia de casos.

Por defecto se usa SQLite (un único fichero, sin instalar nada). La interfaz
:class:`Repository` permite añadir otros motores (MongoDB está previsto como extra opcional).
"""

from __future__ import annotations

from osintsuite.storage.base import CaseDiff, CaseSummary, Repository, diff_cases
from osintsuite.storage.sqlite import SQLiteRepository

__all__ = [
    "CaseDiff",
    "CaseSummary",
    "Repository",
    "SQLiteRepository",
    "diff_cases",
    "open_repository",
]


def open_repository(db_url: str) -> Repository:
    if db_url.startswith("sqlite"):
        return SQLiteRepository(db_url)
    raise ValueError(f"Motor de base de datos no soportado todavía: {db_url.split(':', 1)[0]}")
