"""
Importa el Excel del sistema financiero personal a SQLite.

Uso
---
    poetry run python scripts/import_excel.py
    poetry run python scripts/import_excel.py --excel ruta/al/archivo.xlsx
    poetry run python scripts/import_excel.py --sin-ejemplos --reset
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from finanzas.config.logging import setup_logging
from finanzas.config.settings import settings
from finanzas.data.excel_import import importar_excel


def main() -> int:
    """Ejecuta la importación y reporta el resultado."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--excel",
        type=Path,
        default=None,
        help="Ruta al .xlsx. Por defecto, la de la configuración.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Ruta alterna al archivo SQLite.",
    )
    parser.add_argument(
        "--sin-ejemplos",
        action="store_true",
        help="Omite las filas marcadas como «Ejemplo» en el Excel.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Borra la base antes de importar, para no duplicar registros.",
    )
    argumentos = parser.parse_args()

    logger = setup_logging(level=settings.log_level)
    ruta_excel = argumentos.excel or settings.excel_source_path
    ruta_db = argumentos.db or settings.db_path

    if argumentos.reset and ruta_db.exists():
        ruta_db.unlink()
        logger.warning("Base anterior eliminada: %s", ruta_db)

    try:
        resultado = importar_excel(
            ruta=ruta_excel,
            db_path=str(ruta_db),
            incluir_ejemplos=not argumentos.sin_ejemplos,
        )
    except FileNotFoundError as error:
        logger.error("%s", error)
        return 1

    logger.info("Importado desde %s: %s", ruta_excel.name, resultado.resumen())

    for omitido in resultado.omitidos[:20]:
        logger.warning("Omitido → %s", omitido)

    if len(resultado.omitidos) > 20:
        logger.warning("… y %s filas más", len(resultado.omitidos) - 20)

    return 0


if __name__ == "__main__":
    sys.exit(main())
