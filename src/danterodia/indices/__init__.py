"""Índice local de fuentes oficiales y datasets (SQLite + FTS5).

¿Por qué un índice local? La API de datos abiertos del BOE **no permite buscar por nombre**:
solo devuelve el sumario de un día o un documento concreto. Para saber si una persona aparece
en el BORME hay que recorrer los días que interesen y guardar lo publicado. Es lo que hacía el
original con MongoDB y bots de fuerza bruta; aquí se hace con la API oficial, de forma
incremental (no se repiten días ya indexados) y en un único fichero SQLite.

La búsqueda usa FTS5 con ``remove_diacritics`` (insensible a tildes) y después se filtra con
:func:`danterodia.identifiers.coincide_nombre` para exigir que estén todos los términos.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from danterodia.identifiers import tokens_nombre

_TOKENIZER = "unicode61 remove_diacritics 2"

ESQUEMA = f"""
CREATE TABLE IF NOT EXISTS meta (clave TEXT PRIMARY KEY, valor TEXT);

CREATE TABLE IF NOT EXISTS boe_items (
    id TEXT PRIMARY KEY, fecha TEXT, seccion TEXT, departamento TEXT, epigrafe TEXT,
    titulo TEXT, url_html TEXT, url_pdf TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS boe_fts USING fts5(
    titulo, departamento, content='boe_items', content_rowid='rowid', tokenize='{_TOKENIZER}');

CREATE TABLE IF NOT EXISTS borme_actos (
    id INTEGER PRIMARY KEY, cve TEXT, fecha TEXT, provincia TEXT, anuncio TEXT,
    empresa TEXT, acto TEXT, cargo TEXT, persona TEXT, url TEXT,
    UNIQUE (cve, anuncio, acto, cargo, persona));
CREATE VIRTUAL TABLE IF NOT EXISTS borme_fts USING fts5(
    empresa, persona, content='borme_actos', content_rowid='id', tokenize='{_TOKENIZER}');

CREATE TABLE IF NOT EXISTS contratos (
    id TEXT PRIMARY KEY, origen TEXT, fecha TEXT, organo TEXT, objeto TEXT, importe TEXT,
    adjudicatario TEXT, nif TEXT, url TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS contratos_fts USING fts5(
    adjudicatario, nif, organo, objeto, content='contratos', content_rowid='rowid',
    tokenize='{_TOKENIZER}');

CREATE TABLE IF NOT EXISTS indultos (
    id TEXT PRIMARY KEY, fecha TEXT, ministro TEXT, delito TEXT, genero TEXT, titulo TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS indultos_fts USING fts5(
    titulo, content='indultos', content_rowid='rowid', tokenize='{_TOKENIZER}');

CREATE TABLE IF NOT EXISTS documentos (
    id INTEGER PRIMARY KEY, ruta TEXT, pagina INTEGER, sensible INTEGER, texto TEXT,
    UNIQUE (ruta, pagina));
CREATE VIRTUAL TABLE IF NOT EXISTS documentos_fts USING fts5(
    texto, content='documentos', content_rowid='id', tokenize='{_TOKENIZER}');
"""

#: Tabla de contenido → (tabla FTS, columnas indexadas)
FTS = {
    "boe_items": ("boe_fts", ("titulo", "departamento")),
    "borme_actos": ("borme_fts", ("empresa", "persona")),
    "contratos": ("contratos_fts", ("adjudicatario", "nif", "organo", "objeto")),
    "indultos": ("indultos_fts", ("titulo",)),
    "documentos": ("documentos_fts", ("texto",)),
}


def fts_query(texto: str, columna: str | None = None) -> str:
    """Construye una consulta FTS5 segura: cada término entre comillas (sin operadores)."""
    terminos = [t.replace('"', "") for t in tokens_nombre(texto, conservar_enie=False)]
    if not terminos:
        terminos = [t.replace('"', "") for t in texto.split() if t.strip('"')]
    consulta = " ".join(f'"{t}"' for t in terminos)
    return f"{columna} : ({consulta})" if columna else consulta


@dataclass(frozen=True, slots=True)
class EstadoIndice:
    tabla: str
    filas: int
    actualizado: str | None


class IndexStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(ESQUEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # ---------------------------------------------------------------- meta
    def get_meta(self, clave: str) -> str | None:
        row = self.db.execute("SELECT valor FROM meta WHERE clave = ?", (clave,)).fetchone()
        return None if row is None else str(row[0])

    def set_meta(self, clave: str, valor: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (clave, valor))
        self.db.commit()

    def marcado(self, clave: str) -> bool:
        return self.get_meta(clave) is not None

    # ---------------------------------------------------------------- escritura
    def insertar(self, tabla: str, filas: Iterable[dict[str, Any]]) -> int:
        """Inserta filas (ignorando duplicadas) y mantiene el índice FTS sincronizado."""
        fts, cols = FTS[tabla]
        n = 0
        for fila in filas:
            campos = list(fila)
            cur = self.db.execute(
                f"INSERT OR IGNORE INTO {tabla} ({', '.join(campos)}) "  # noqa: S608 - nombres internos
                f"VALUES ({', '.join('?' for _ in campos)})",
                [fila[c] for c in campos],
            )
            if cur.rowcount:
                n += 1
                self.db.execute(
                    f"INSERT INTO {fts} (rowid, {', '.join(cols)}) "  # noqa: S608
                    f"VALUES (?, {', '.join('?' for _ in cols)})",
                    [cur.lastrowid, *[fila.get(c) for c in cols]],
                )
        self.db.commit()
        return n

    # ---------------------------------------------------------------- lectura
    def buscar(
        self, tabla: str, texto: str, columna: str | None = None, limite: int = 200
    ) -> list[dict[str, Any]]:
        fts, _ = FTS[tabla]
        consulta = fts_query(texto, columna)
        if not consulta.strip():
            return []
        pk = "id" if tabla in {"borme_actos", "documentos"} else "rowid"
        rows = self.db.execute(
            f"SELECT t.* FROM {fts} f JOIN {tabla} t ON t.{pk} = f.rowid "  # noqa: S608
            f"WHERE {fts} MATCH ? ORDER BY rank LIMIT ?",
            (consulta, limite),
        ).fetchall()
        return [dict(r) for r in rows]

    def buscar_exacto(self, tabla: str, columna: str, valor: str) -> list[dict[str, Any]]:
        if columna not in FTS[tabla][1] and columna != "id":
            raise ValueError(columna)
        rows = self.db.execute(
            f"SELECT * FROM {tabla} WHERE {columna} = ?",  # noqa: S608
            (valor,),
        ).fetchall()
        return [dict(r) for r in rows]

    def contar(self, tabla: str) -> int:
        if tabla not in FTS:
            raise ValueError(tabla)
        return int(self.db.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0])  # noqa: S608

    def estado(self) -> list[EstadoIndice]:
        return [EstadoIndice(t, self.contar(t), self.get_meta(f"actualizado:{t}")) for t in FTS]

    def vacio(self, tablas: Sequence[str]) -> bool:
        return all(self.contar(t) == 0 for t in tablas)
