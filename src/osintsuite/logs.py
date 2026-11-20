"""Logging estructurado.

- Consola: mensajes legibles con Rich (nivel configurable).
- Fichero por caso: ``casos/<id>/caso.log.jsonl``, una línea JSON por evento, para poder
  auditar después qué se consultó, cuándo y con qué resultado.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.logging import RichHandler

ROOT_LOGGER = "osintsuite"

_EXTRA_IGNORADOS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Formatea cada registro como una línea JSON (incluye los ``extra=`` del registro)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _EXTRA_IGNORADOS:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configura el logger raíz de la aplicación (idempotente)."""
    logger = logging.getLogger(ROOT_LOGGER)
    logger.setLevel(logging.DEBUG)
    if not any(isinstance(h, RichHandler) for h in logger.handlers):
        console = RichHandler(rich_tracebacks=False, show_path=False, markup=False)
        console.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(console)
    for h in logger.handlers:
        if isinstance(h, RichHandler):
            h.setLevel(level)
    logger.propagate = False
    return logger


class CaseLogHandler(logging.FileHandler):
    """Handler que escribe en JSONL los eventos de un caso concreto."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(path, encoding="utf-8")
        self.setFormatter(JsonFormatter())
        self.setLevel(logging.DEBUG)


def case_logger(case_id: str, case_dir: Path) -> tuple[logging.Logger, CaseLogHandler]:
    """Devuelve un logger hijo para el caso y su handler (para cerrarlo al terminar)."""
    logger = logging.getLogger(f"{ROOT_LOGGER}.caso.{case_id}")
    handler = CaseLogHandler(case_dir / "caso.log.jsonl")
    logger.addHandler(handler)
    return logger, handler
