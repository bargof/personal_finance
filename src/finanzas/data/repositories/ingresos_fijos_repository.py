from __future__ import annotations

import pandas as pd

from finanzas.data.database import connect

# ═══════════════════════════════════════════════════════════
# Ingresos fijos: lo que entra cada mes aunque aún no se haya
# registrado, como la nómina en sus quincenas.
# ═══════════════════════════════════════════════════════════

_CAMPOS = ("concepto", "monto", "dia", "categoria_id", "cuenta_id", "texto", "activo")


class IngresosFijosRepository:
    """Lectura y escritura del catálogo de ingresos fijos."""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path

    def listar(self, solo_activos: bool = False) -> pd.DataFrame:
        """Devuelve los ingresos fijos con el nombre de su categoría y cuenta."""
        filtro = "WHERE i.activo = 1" if solo_activos else ""
        with connect(self._db_path) as conexion:
            df = pd.read_sql_query(
                f"""
                SELECT
                    i.*,
                    COALESCE(c.nombre, '')  AS categoria,
                    COALESCE(cu.nombre, '') AS cuenta
                FROM ingresos_fijos i
                LEFT JOIN categorias c  ON c.id  = i.categoria_id
                LEFT JOIN cuentas    cu ON cu.id = i.cuenta_id
                {filtro}
                ORDER BY i.dia, i.concepto
                """,
                conexion,
            )

        if not df.empty:
            df["activo"] = df["activo"].astype(bool)
        return df

    def crear(self, **valores: object) -> int:
        """Da de alta un ingreso fijo y devuelve su id."""
        columnas = ", ".join(_CAMPOS)
        marcadores = ", ".join("?" for _ in _CAMPOS)
        with connect(self._db_path) as conexion:
            cursor = conexion.execute(
                f"INSERT INTO ingresos_fijos ({columnas}) VALUES ({marcadores})",
                [valores[campo] for campo in _CAMPOS],
            )
            return int(cursor.lastrowid)

    def actualizar(self, ingreso_id: int, **valores: object) -> None:
        """Reemplaza los datos de un ingreso fijo."""
        asignaciones = ", ".join(f"{campo} = ?" for campo in _CAMPOS)
        with connect(self._db_path) as conexion:
            conexion.execute(
                f"UPDATE ingresos_fijos SET {asignaciones} WHERE id = ?",
                [*(valores[campo] for campo in _CAMPOS), ingreso_id],
            )

    def eliminar(self, ingreso_id: int) -> None:
        """Elimina un ingreso fijo."""
        with connect(self._db_path) as conexion:
            conexion.execute("DELETE FROM ingresos_fijos WHERE id = ?", (ingreso_id,))
