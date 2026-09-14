from __future__ import annotations

from datetime import date

import pandas as pd

from finanzas.data.database import connect
from finanzas.domain.entities import Deseo

# ═══════════════════════════════════════════════════════════
# Lista de deseos
#
# Lo que se quiere comprar y todavía no. Nada de esto toca el
# gasto: un deseo no es un movimiento hasta que se compra.
# ═══════════════════════════════════════════════════════════

_CAMPOS = (
    "nombre",
    "costo",
    "categoria_id",
    "prioridad",
    "enlace",
    "notas",
    "comprado_en",
)


class DeseosRepository:
    """Acceso a la lista de deseos y a los saldos con que se comparan."""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path

    def listar(self, incluir_comprados: bool = False) -> pd.DataFrame:
        """Devuelve los deseos, por prioridad y luego por costo."""
        filtro = "" if incluir_comprados else "WHERE comprado = 0"
        with connect(self._db_path) as conexion:
            df = pd.read_sql_query(
                f"""
                SELECT * FROM v_deseos {filtro}
                 ORDER BY CASE prioridad
                              WHEN 'Alta' THEN 0
                              WHEN 'Media' THEN 1
                              ELSE 2
                          END, costo
                """,
                conexion,
            )

        if df.empty:
            return df

        df["comprado_en"] = pd.to_datetime(df["comprado_en"], errors="coerce")
        df["comprado"] = df["comprado"].astype(bool)

        return df

    def saldos_por_cuenta(self) -> pd.DataFrame:
        """
        Devuelve el saldo disponible en cada cuenta del balance.

        Sale de las posiciones patrimoniales ligadas a una cuenta, que es
        donde vive el saldo: una cuenta sin posición todavía no dice
        cuánto tiene.
        """
        with connect(self._db_path) as conexion:
            return pd.read_sql_query(
                "SELECT * FROM v_saldos_cuentas ORDER BY saldo DESC", conexion
            )

    def crear(self, deseo: Deseo) -> int:
        """Agrega un deseo a la lista y devuelve su id."""
        columnas = ", ".join(_CAMPOS)
        marcadores = ", ".join("?" for _ in _CAMPOS)

        with connect(self._db_path) as conexion:
            cursor = conexion.execute(
                f"INSERT INTO deseos ({columnas}) VALUES ({marcadores})",
                _a_valores(deseo),
            )
            return int(cursor.lastrowid)

    def actualizar(self, deseo_id: int, deseo: Deseo) -> None:
        """Reemplaza los datos de un deseo."""
        asignaciones = ", ".join(f"{campo} = ?" for campo in _CAMPOS)

        with connect(self._db_path) as conexion:
            conexion.execute(
                f"UPDATE deseos SET {asignaciones} WHERE id = ?",
                [*_a_valores(deseo), deseo_id],
            )

    def marcar_comprado(self, deseo_id: int, cuando: date | None = None) -> None:
        """
        Saca un deseo de la lista sin borrarlo.

        Conservarlo permite ver después qué se quiso y qué se terminó
        comprando, que es la mitad de para qué sirve la lista.
        """
        with connect(self._db_path) as conexion:
            conexion.execute(
                "UPDATE deseos SET comprado_en = ? WHERE id = ?",
                ((cuando or date.today()).isoformat(), deseo_id),
            )

    def devolver_a_la_lista(self, deseo_id: int) -> None:
        """Deshace la compra de un deseo."""
        with connect(self._db_path) as conexion:
            conexion.execute(
                "UPDATE deseos SET comprado_en = NULL WHERE id = ?", (deseo_id,)
            )

    def eliminar(self, deseo_id: int) -> None:
        """Borra un deseo de la lista."""
        with connect(self._db_path) as conexion:
            conexion.execute("DELETE FROM deseos WHERE id = ?", (deseo_id,))


def _a_valores(deseo: Deseo) -> list[object]:
    """Aplana un deseo al orden de columnas de `_CAMPOS`."""
    return [
        deseo.nombre.strip(),
        float(deseo.costo),
        deseo.categoria_id,
        str(deseo.prioridad),
        deseo.enlace.strip(),
        deseo.notas.strip(),
        deseo.comprado_en.isoformat() if deseo.comprado_en else None,
    ]
