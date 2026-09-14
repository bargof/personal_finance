from __future__ import annotations

import pandas as pd

from finanzas.data.database import connect
from finanzas.domain.entities import ProductoDeMovimiento

# ═══════════════════════════════════════════════════════════
# Productos de un movimiento
#
# Detalle de una compra de varias cosas. Se reemplazan en
# bloque en vez de actualizarse uno por uno: editar la lista de
# un ticket es rehacerla, no corregir filas sueltas.
# ═══════════════════════════════════════════════════════════


class ProductosRepository:
    """Acceso al desglose en productos de los movimientos."""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path

    def de_movimiento(self, movimiento_id: int) -> pd.DataFrame:
        """Devuelve los productos de un movimiento, en su orden."""
        with connect(self._db_path) as conexion:
            return pd.read_sql_query(
                "SELECT * FROM v_movimiento_productos "
                "WHERE movimiento_id = ? ORDER BY orden, id",
                conexion,
                params=(movimiento_id,),
            )

    def desglose(self, movimiento_id: int) -> dict[str, float]:
        """
        Devuelve cuánto del movimiento está desglosado.

        Returns
        -------
        dict
            Cuántos productos hay, cuánto suman y cuánto queda sin
            desglosar, que puede ser cero o positivo sin que sea un error.
        """
        with connect(self._db_path) as conexion:
            fila = conexion.execute(
                "SELECT * FROM v_movimientos_desglose WHERE movimiento_id = ?",
                (movimiento_id,),
            ).fetchone()

        if fila is None:
            return {"productos": 0, "desglosado": 0.0, "sin_desglosar": 0.0}

        return {
            "productos": int(fila["productos"]),
            "desglosado": float(fila["desglosado"]),
            "sin_desglosar": float(fila["sin_desglosar"]),
        }

    def reemplazar(
        self, movimiento_id: int, productos: list[ProductoDeMovimiento]
    ) -> int:
        """
        Deja el movimiento con exactamente estos productos.

        Reemplazar en bloque evita tener que llevar la cuenta de cuáles se
        editaron, cuáles se borraron y cuáles son nuevos.

        Returns
        -------
        int
            Cuántos productos quedaron.
        """
        with connect(self._db_path) as conexion:
            conexion.execute(
                "DELETE FROM movimiento_productos WHERE movimiento_id = ?",
                (movimiento_id,),
            )

            filas = [
                (
                    movimiento_id,
                    producto.producto.strip(),
                    float(producto.cantidad),
                    float(producto.precio_unitario),
                    orden,
                    producto.nota.strip(),
                )
                for orden, producto in enumerate(productos)
                if producto.producto.strip()
            ]
            if filas:
                conexion.executemany(
                    """
                    INSERT INTO movimiento_productos
                        (movimiento_id, producto, cantidad, precio_unitario,
                         orden, nota)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    filas,
                )

        return len(filas)

    def buscar(self, texto: str) -> pd.DataFrame:
        """
        Busca un artículo entre todos los desgloses.

        Es la razón de ser de guardar los productos: poder preguntar
        cuánto se lleva gastado en algo que nunca fue una categoría.
        """
        with connect(self._db_path) as conexion:
            df = pd.read_sql_query(
                "SELECT * FROM v_movimiento_productos "
                "WHERE producto LIKE ? ORDER BY fecha DESC",
                conexion,
                params=(f"%{texto}%",),
            )

        if not df.empty:
            df["fecha"] = pd.to_datetime(df["fecha"])

        return df
