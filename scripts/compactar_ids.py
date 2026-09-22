"""
Compacta los ids de los catálogos y resetea los contadores AUTOINCREMENT.

Arregla la secuela de un bug del sembrado: `INSERT ... ON CONFLICT DO
NOTHING` sobre una tabla `AUTOINCREMENT` reserva el siguiente id antes de
detectar el conflicto de `UNIQUE`, y el `DO NOTHING` no lo devuelve. Como
el sembrado corría en cada arranque, cada uno quemaba un id por cada fila
que ya existía: las subcategorías nuevas acabaron en el rango 319-336 en
vez de 54-71.

El origen ya está corregido en `finanzas.data.seed`; este script sólo
limpia lo que quedó. Renumera cada catálogo a 1..N preservando el orden
actual de los ids, propaga el cambio a las claves foráneas y deja
`sqlite_sequence` en el máximo real.

Uso
---
    poetry run python scripts/compactar_ids.py --dry-run
    poetry run python scripts/compactar_ids.py
    poetry run python scripts/compactar_ids.py --db ruta/alterna.db
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from finanzas.config.logging import setup_logging
from finanzas.config.settings import settings

# ═══════════════════════════════════════════════════════════
# Qué renumerar y quién lo referencia
#
# Cada entrada declara la tabla de catálogo y las columnas que
# apuntan a ella, para que el remapeo no deje huérfanos.
# ═══════════════════════════════════════════════════════════

#: (tabla, [(tabla_hija, columna_fk), ...])
_CATALOGOS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "categorias",
        (
            ("subcategorias", "categoria_id"),
            ("movimientos", "categoria_id"),
            ("presupuestos", "categoria_id"),
            ("suscripciones", "categoria_id"),
        ),
    ),
    (
        "subcategorias",
        (
            ("movimientos", "subcategoria_id"),
            ("suscripciones", "subcategoria_id"),
        ),
    ),
    (
        "cuentas",
        (
            ("movimientos", "cuenta_id"),
            ("suscripciones", "cuenta_id"),
            ("patrimonio", "cuenta_id"),
        ),
    ),
    ("medios_pago", (("movimientos", "medio_pago_id"),)),
    ("movimientos", ()),
    ("patrimonio", ()),
    ("metas", ()),
    ("suscripciones", ()),
    ("presupuestos", ()),
    ("saldos_verificados", ()),
)


def _columnas_de(conexion: sqlite3.Connection, tabla: str) -> set[str]:
    """Devuelve los nombres de columna de una tabla, vacío si no existe."""
    filas = conexion.execute(f"PRAGMA table_info({tabla})").fetchall()
    return {fila[1] for fila in filas}


def _plan_de_renumerado(conexion: sqlite3.Connection, tabla: str) -> dict[int, int]:
    """
    Calcula el mapa {id_viejo: id_nuevo} que compacta la tabla a 1..N.

    Sólo incluye los ids que cambian, para poder reportar el trabajo real.
    """
    ids = [
        fila[0]
        for fila in conexion.execute(f"SELECT id FROM {tabla} ORDER BY id").fetchall()
    ]

    return {viejo: nuevo for nuevo, viejo in enumerate(ids, start=1) if viejo != nuevo}


def _renumerar(
    conexion: sqlite3.Connection,
    tabla: str,
    referencias: tuple[tuple[str, str], ...],
    mapa: dict[int, int],
) -> None:
    """
    Aplica el mapa de ids a la tabla y a todo lo que la referencia.

    Desplaza los ids a un rango temporal negativo antes de asentarlos en
    su valor final: renumerar en un solo paso choca con la clave primaria
    en cuanto el id destino ya está ocupado por otra fila.
    """
    for viejo, nuevo in mapa.items():
        conexion.execute(f"UPDATE {tabla} SET id = ? WHERE id = ?", (-nuevo, viejo))
        for tabla_hija, columna in referencias:
            if columna not in _columnas_de(conexion, tabla_hija):
                continue
            conexion.execute(
                f"UPDATE {tabla_hija} SET {columna} = ? WHERE {columna} = ?",
                (-nuevo, viejo),
            )

    conexion.execute(f"UPDATE {tabla} SET id = -id WHERE id < 0")
    for tabla_hija, columna in referencias:
        if columna not in _columnas_de(conexion, tabla_hija):
            continue
        conexion.execute(
            f"UPDATE {tabla_hija} SET {columna} = -{columna} WHERE {columna} < 0"
        )


def _resetear_secuencia(conexion: sqlite3.Connection, tabla: str) -> int | None:
    """
    Deja `sqlite_sequence` en el id máximo real de la tabla.

    Returns
    -------
    int or None
        El valor anterior del contador, o None si la tabla no tenía uno.
    """
    fila = conexion.execute(
        "SELECT seq FROM sqlite_sequence WHERE name = ?", (tabla,)
    ).fetchone()
    if fila is None:
        return None

    anterior = int(fila[0])
    maximo = conexion.execute(f"SELECT COALESCE(MAX(id), 0) FROM {tabla}").fetchone()[0]
    conexion.execute(
        "UPDATE sqlite_sequence SET seq = ? WHERE name = ?", (int(maximo), tabla)
    )

    return anterior


def _respaldar(ruta: Path) -> Path:
    """Copia el archivo SQLite junto al original, con marca de tiempo."""
    sello = datetime.now().strftime("%Y%m%d-%H%M%S")
    destino = ruta.with_name(f"{ruta.stem}.respaldo-{sello}{ruta.suffix}")
    shutil.copy2(ruta, destino)

    return destino


def main() -> int:
    """Compacta los ids y reporta lo que cambió."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Muestra lo que haría sin escribir nada.",
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

    if not ruta.exists():
        logger.error("No existe la base %s", ruta)
        return 1

    if not argumentos.dry_run:
        respaldo = _respaldar(ruta)
        logger.info("Respaldo creado en %s", respaldo)

    conexion = sqlite3.connect(ruta)
    # Las claves foráneas quedan apagadas a propósito: el remapeo pasa por
    # un estado intermedio donde los hijos apuntan a ids negativos. La
    # verificación al final confirma que no quedó nada suelto.
    conexion.execute("PRAGMA foreign_keys = OFF")

    cambios: list[str] = []

    try:
        conexion.execute("BEGIN")

        for tabla, referencias in _CATALOGOS:
            if not _columnas_de(conexion, tabla):
                continue

            mapa = _plan_de_renumerado(conexion, tabla)
            if mapa:
                ejemplo = ", ".join(
                    f"{viejo}→{nuevo}" for viejo, nuevo in list(mapa.items())[:4]
                )
                sufijo = ", …" if len(mapa) > 4 else ""
                cambios.append(
                    f"{tabla}: {len(mapa)} ids renumerados ({ejemplo}{sufijo})"
                )

                if not argumentos.dry_run:
                    _renumerar(conexion, tabla, referencias, mapa)

            if argumentos.dry_run:
                continue

            anterior = _resetear_secuencia(conexion, tabla)
            maximo = conexion.execute(
                f"SELECT COALESCE(MAX(id), 0) FROM {tabla}"
            ).fetchone()[0]
            if anterior is not None and anterior != maximo:
                cambios.append(f"{tabla}: contador {anterior} → {maximo}")

        if argumentos.dry_run:
            conexion.execute("ROLLBACK")
        else:
            sueltas = conexion.execute("PRAGMA foreign_key_check").fetchall()
            if sueltas:
                conexion.execute("ROLLBACK")
                logger.error(
                    "Se abortó el compactado: %d referencias quedarían rotas. "
                    "La base no se modificó.",
                    len(sueltas),
                )
                return 1

            conexion.execute("COMMIT")
    except Exception:
        conexion.execute("ROLLBACK")
        raise
    finally:
        conexion.close()

    if not cambios:
        logger.info("Nada que compactar: los ids ya están en 1..N.")
        return 0

    encabezado = (
        "Cambios que se harían:" if argumentos.dry_run else "Cambios aplicados:"
    )
    logger.info(encabezado)
    for linea in cambios:
        logger.info("  %s", linea)

    return 0


if __name__ == "__main__":
    sys.exit(main())
