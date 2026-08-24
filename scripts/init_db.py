"""
Crea la base SQLite y siembra los catálogos base.

Uso
---
    poetry run python scripts/init_db.py
    poetry run python scripts/init_db.py --reset
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from finanzas.config.logging import setup_logging
from finanzas.config.settings import settings
from finanzas.data.seed import preparar_base


def main() -> int:
    """Prepara la base de datos y reporta el resultado."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Borra el archivo SQLite antes de crearlo de nuevo.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Ruta alterna al archivo SQLite.",
    )
    argumentos = parser.parse_args()

    logger = setup_logging(level=settings.log_level)
    ruta = argumentos.db or settings.db_path

    if argumentos.reset and ruta.exists():
        ruta.unlink()
        logger.warning("Base anterior eliminada: %s", ruta)

    preparar_base(str(ruta))
    logger.info("Base lista en %s", ruta)

    return 0


if __name__ == "__main__":
    sys.exit(main())
