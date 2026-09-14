from __future__ import annotations

import pandas as pd

from finanzas.data.repositories.productos_repository import ProductosRepository
from finanzas.domain.entities import ProductoDeMovimiento

# ═══════════════════════════════════════════════════════════
# Productos de un movimiento
#
# El detalle de una compra de varias cosas. Guardarlo permite
# preguntar después cuánto se lleva gastado en algo que nunca
# fue una categoría propia.
# ═══════════════════════════════════════════════════════════


class ProductosService:
    """Casos de uso sobre el desglose en productos."""

    def __init__(self, repositorio: ProductosRepository | None = None) -> None:
        self._repo = repositorio or ProductosRepository()

    def de_movimiento(self, movimiento_id: int) -> pd.DataFrame:
        """Devuelve los productos de un movimiento, en su orden."""
        return self._repo.de_movimiento(movimiento_id)

    def desglose(self, movimiento_id: int) -> dict[str, float]:
        """Devuelve cuánto del movimiento está detallado y cuánto no."""
        return self._repo.desglose(movimiento_id)

    def reemplazar(self, movimiento_id: int, filas: pd.DataFrame) -> int:
        """
        Deja el movimiento con exactamente estos productos.

        Recibe la tabla tal como sale del editor, así que descarta las
        filas vacías que deja al agregar renglones.
        """
        if filas is None or filas.empty:
            return self._repo.reemplazar(movimiento_id, [])

        productos = [
            ProductoDeMovimiento(
                producto=str(fila.get("producto") or "").strip(),
                cantidad=float(fila.get("cantidad") or 1),
                precio_unitario=float(fila.get("precio_unitario") or 0),
                nota=str(fila.get("nota") or ""),
            )
            for _, fila in filas.iterrows()
            if str(fila.get("producto") or "").strip()
        ]

        return self._repo.reemplazar(movimiento_id, productos)

    def buscar(self, texto: str) -> pd.DataFrame:
        """
        Busca un artículo entre todos los desgloses.

        Es la razón de ser de guardar los productos: saber cuánto se lleva
        gastado en algo que nunca fue una categoría.
        """
        return self._repo.buscar(texto)
