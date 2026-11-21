"""Envoltorio de compatibilidad con Dante's Gates Minimal Version (Quantika14).

Antes: `python BuscadorPersonas.py`. Ahora equivale a la nueva CLI; el script original está en
`legacy/BuscadorPersonas.py`. Para más opciones usa directamente `danterodia --help`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from danterodia.cli import legacy_main
from danterodia.models import EntityType

if __name__ == "__main__":
    legacy_main(EntityType.PERSONA, "Nombre y apellidos")
