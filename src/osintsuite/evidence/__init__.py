"""Preservación de evidencias: copia en bruto, SHA-256, fecha UTC y URL de origen."""

from __future__ import annotations

import hashlib
import json
import mimetypes
from pathlib import Path

from osintsuite.models import Evidence, utcnow

_EXTENSIONES = {
    "text/html": ".html",
    "application/json": ".json",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "application/xml": ".xml",
    "text/xml": ".xml",
}


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _extension(content_type: str | None) -> str:
    if not content_type:
        return ".bin"
    base = content_type.split(";", 1)[0].strip().lower()
    return _EXTENSIONES.get(base) or mimetypes.guess_extension(base) or ".bin"


class EvidenceStore:
    """Guarda las evidencias de un caso en ``<cases_dir>/<case_id>/evidencias/``.

    El nombre del fichero es su SHA-256, así que el mismo contenido no se duplica y cualquiera
    puede comprobar la integridad recalculando el hash. Junto a cada fichero se escribe un
    ``.json`` con la URL, la fecha UTC y el tipo de contenido.
    """

    def __init__(self, cases_dir: Path) -> None:
        self.cases_dir = cases_dir

    def case_dir(self, case_id: str) -> Path:
        if not case_id or any(c in case_id for c in "/\\.:"):
            raise ValueError(f"ID de caso no válido: {case_id!r}")
        return self.cases_dir / case_id

    def save(
        self, case_id: str, url: str, content: bytes, content_type: str | None = None
    ) -> Evidence:
        digest = sha256_bytes(content)
        folder = self.case_dir(case_id) / "evidencias"
        folder.mkdir(parents=True, exist_ok=True)
        filename = digest + _extension(content_type)
        target = folder / filename
        if not target.exists():
            target.write_bytes(content)
        evidence = Evidence(
            url=url,
            retrieved_at=utcnow(),
            sha256=digest,
            path=f"evidencias/{filename}",
            content_type=content_type,
            size=len(content),
        )
        meta = folder / f"{filename}.json"
        registros: list[dict[str, object]] = []
        if meta.exists():
            registros = json.loads(meta.read_text(encoding="utf-8"))
        registros.append(evidence.model_dump(mode="json"))
        meta.write_text(json.dumps(registros, ensure_ascii=False, indent=2), encoding="utf-8")
        return evidence

    def verify(self, case_id: str, evidence: Evidence) -> bool:
        """Comprueba que el fichero guardado sigue teniendo el hash registrado."""
        if evidence.path is None:
            return False
        path = self.case_dir(case_id) / evidence.path
        return path.is_file() and sha256_bytes(path.read_bytes()) == evidence.sha256
