"""Persistencia de casos.

Por defecto se usa SQLite (un único fichero, sin instalar nada). MongoDB es opcional
(``pip install danterodia[mongo]`` y ``DANTERODIA_DATABASE_URL=mongodb://…``).
"""

from __future__ import annotations

from danterodia.storage.base import CaseDiff, CaseSummary, Repository, diff_cases
from danterodia.storage.sqlite import SQLiteRepository

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
    if db_url.startswith(("mongodb://", "mongodb+srv://")):
        from danterodia.storage.mongo import MongoRepository

        return MongoRepository(db_url)
    raise ValueError(f"Motor de base de datos no soportado: {db_url.split(':', 1)[0]}")
