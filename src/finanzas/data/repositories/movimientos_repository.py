from __future__ import annotations

from datetime import date

import pandas as pd

from finanzas.data.database import connect
from finanzas.domain.entities import Movimiento

# ═══════════════════════════════════════════════════════════
# Movimientos: la tabla que alimenta todo lo demás.
#
# Las lecturas salen de la vista `v_movimientos`, que ya trae
# los nombres de catálogo y las columnas derivadas.
# ═══════════════════════════════════════════════════════════

#: Campos que se persisten tal cual; lo derivado lo calcula la vista.
_CAMPOS_ESCRITURA = (
    "fecha",
    "tipo",
    "monto",
    "categoria_id",
    "subcategoria_id",
    "cuenta_id",
    "medio_pago_id",
    "descripcion",
    "necesidad",
    "naturaleza",
    "recurrente",
    "planeado",
    "proyecto",
    "etiquetas",
    "nota",
    "estado",
)


class MovimientosRepository:
    """Lectura y escritura de movimientos."""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path

    # ── Lectura ──────────────────────────────────────────

    def listar(
        self,
        desde: date | None = None,
        hasta: date | None = None,
        tipos: list[str] | None = None,
        categorias: list[str] | None = None,
        cuentas: list[str] | None = None,
        estado: str | None = None,
        texto: str | None = None,
        limite: int | None = None,
    ) -> pd.DataFrame:
        """
        Devuelve movimientos enriquecidos, filtrados por los criterios dados.

        Parameters
        ----------
        desde, hasta : date, optional
            Rango de fechas inclusivo.
        tipos, categorias, cuentas : list of str, optional
            Filtros por valor del catálogo. Una lista vacía no filtra.
        estado : str, optional
            'Confirmado' o 'Pendiente'.
        texto : str, optional
            Búsqueda parcial sobre descripción, etiquetas y nota.
        limite : int, optional
            Máximo de filas a devolver, de la más reciente hacia atrás.

        Returns
        -------
        pandas.DataFrame
            Movimientos con nombres de catálogo y columnas derivadas.
        """
        condiciones: list[str] = []
        parametros: list[object] = []

        if desde is not None:
            condiciones.append("fecha >= ?")
            parametros.append(desde.isoformat())
        if hasta is not None:
            condiciones.append("fecha <= ?")
            parametros.append(hasta.isoformat())
        if estado:
            condiciones.append("estado = ?")
            parametros.append(estado)
        if texto:
            condiciones.append(
                "(descripcion LIKE ? OR etiquetas LIKE ? OR nota LIKE ?)"
            )
            patron = f"%{texto}%"
            parametros.extend([patron, patron, patron])

        for columna, valores in (
            ("tipo", tipos),
            ("categoria", categorias),
            ("cuenta", cuentas),
        ):
            if valores:
                marcadores = ", ".join("?" for _ in valores)
                condiciones.append(f"{columna} IN ({marcadores})")
                parametros.extend(valores)

        consulta = "SELECT * FROM v_movimientos"
        if condiciones:
            consulta += " WHERE " + " AND ".join(condiciones)
        consulta += " ORDER BY fecha DESC, id DESC"
        if limite is not None:
            consulta += " LIMIT ?"
            parametros.append(limite)

        with connect(self._db_path) as conexion:
            df = pd.read_sql_query(consulta, conexion, params=parametros)

        return _tipar(df)

    def del_periodo(self, periodo: str) -> pd.DataFrame:
        """Devuelve los movimientos de un periodo YYYY-MM."""
        with connect(self._db_path) as conexion:
            df = pd.read_sql_query(
                "SELECT * FROM v_movimientos WHERE periodo = ? ORDER BY fecha",
                conexion,
                params=(periodo,),
            )
        return _tipar(df)

    def resumen_mensual(self, ultimos_meses: int | None = None) -> pd.DataFrame:
        """Devuelve ingresos, gastos y ahorro agregados por periodo."""
        consulta = "SELECT * FROM v_resumen_mensual ORDER BY periodo"
        with connect(self._db_path) as conexion:
            df = pd.read_sql_query(consulta, conexion)

        if ultimos_meses is not None and not df.empty:
            df = df.tail(ultimos_meses).reset_index(drop=True)

        return df

    def periodos_disponibles(self) -> list[str]:
        """Devuelve los periodos con movimientos, del más reciente al más viejo."""
        with connect(self._db_path) as conexion:
            filas = conexion.execute(
                "SELECT DISTINCT periodo FROM v_movimientos ORDER BY periodo DESC"
            ).fetchall()

        return [fila["periodo"] for fila in filas]

    def obtener(self, movimiento_id: int) -> pd.Series | None:
        """Devuelve un movimiento por id, o None si no existe."""
        with connect(self._db_path) as conexion:
            df = pd.read_sql_query(
                "SELECT * FROM v_movimientos WHERE id = ?",
                conexion,
                params=(movimiento_id,),
            )

        if df.empty:
            return None
        return _tipar(df).iloc[0]

    # ── Escritura ────────────────────────────────────────

    def crear(self, movimiento: Movimiento) -> int:
        """Inserta un movimiento y devuelve su id."""
        valores = _a_valores(movimiento)
        columnas = ", ".join(_CAMPOS_ESCRITURA)
        marcadores = ", ".join("?" for _ in _CAMPOS_ESCRITURA)

        with connect(self._db_path) as conexion:
            cursor = conexion.execute(
                f"INSERT INTO movimientos ({columnas}) VALUES ({marcadores})",
                valores,
            )
            return int(cursor.lastrowid)

    def crear_muchos(self, movimientos: list[Movimiento]) -> int:
        """Inserta varios movimientos en una sola transacción."""
        if not movimientos:
            return 0

        columnas = ", ".join(_CAMPOS_ESCRITURA)
        marcadores = ", ".join("?" for _ in _CAMPOS_ESCRITURA)

        with connect(self._db_path) as conexion:
            conexion.executemany(
                f"INSERT INTO movimientos ({columnas}) VALUES ({marcadores})",
                [_a_valores(movimiento) for movimiento in movimientos],
            )

        return len(movimientos)

    def actualizar(self, movimiento_id: int, movimiento: Movimiento) -> None:
        """Reemplaza los datos de un movimiento existente."""
        asignaciones = ", ".join(f"{campo} = ?" for campo in _CAMPOS_ESCRITURA)
        valores = [*_a_valores(movimiento), movimiento_id]

        with connect(self._db_path) as conexion:
            conexion.execute(
                f"""
                UPDATE movimientos
                   SET {asignaciones}, actualizado_en = datetime('now')
                 WHERE id = ?
                """,
                valores,
            )

    def eliminar(self, movimiento_id: int) -> None:
        """Elimina un movimiento."""
        with connect(self._db_path) as conexion:
            conexion.execute("DELETE FROM movimientos WHERE id = ?", (movimiento_id,))

    def eliminar_muchos(self, movimiento_ids: list[int]) -> int:
        """Elimina varios movimientos y devuelve cuántos se borraron."""
        if not movimiento_ids:
            return 0

        marcadores = ", ".join("?" for _ in movimiento_ids)
        with connect(self._db_path) as conexion:
            cursor = conexion.execute(
                f"DELETE FROM movimientos WHERE id IN ({marcadores})",
                movimiento_ids,
            )
            return cursor.rowcount


# ═══════════════════════════════════════════════════════════
# Conversión entre la entidad y la fila de SQLite
# ═══════════════════════════════════════════════════════════


def _a_valores(movimiento: Movimiento) -> list[object]:
    """Aplana un movimiento al orden de columnas de `_CAMPOS_ESCRITURA`."""
    return [
        movimiento.fecha.isoformat(),
        str(movimiento.tipo),
        float(movimiento.monto),
        movimiento.categoria_id,
        movimiento.subcategoria_id,
        movimiento.cuenta_id,
        movimiento.medio_pago_id,
        movimiento.descripcion.strip(),
        str(movimiento.necesidad),
        str(movimiento.naturaleza),
        int(movimiento.recurrente),
        int(movimiento.planeado),
        movimiento.proyecto.strip(),
        movimiento.etiquetas.strip(),
        movimiento.nota.strip(),
        str(movimiento.estado),
    ]


def _tipar(df: pd.DataFrame) -> pd.DataFrame:
    """Convierte fechas y banderas a los tipos que espera la capa analítica."""
    if df.empty:
        return df

    df = df.copy()
    df["fecha"] = pd.to_datetime(df["fecha"])
    for bandera in ("recurrente", "planeado"):
        if bandera in df.columns:
            df[bandera] = df[bandera].astype(bool)

    return df
