"""Envoltorio de compatibilidad con Dante's Gates Minimal Version (Quantika14).

Antes: `python BuscadorTelefonos.py`. Ahora equivale a la nueva CLI; el script original está en
`legacy/BuscadorTelefonos.py`. Para más opciones usa directamente `osint --help`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from osintsuite.cli import legacy_main
from osintsuite.models import EntityType

if __name__ == "__main__":
    legacy_main(EntityType.TELEFONO, "Teléfono (con prefijo, p. ej. +34…)")
