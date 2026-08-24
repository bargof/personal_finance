from __future__ import annotations

import pandas as pd

from finanzas.data.database import connect

# ═══════════════════════════════════════════════════════════
# Presupuesto por categoría y periodo.
#
# Sólo se guardan las dos decisiones del usuario: el monto
# manual y el % de recorte. El promedio de 3 meses y el gasto
# del periodo se recalculan siempre desde los movimientos.
# ═══════════════════════════════════════════════════════════


class PresupuestoRepository:
    """Acceso a las líneas de presupuesto y a sus insumos calculados."""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path

    def tablero(self, periodo: str) -> pd.DataFrame:
        """
        Devuelve una fila por categoría de gasto con todo lo necesario
        para evaluar el presupuesto del periodo.

        Parameters
        ----------
        periodo : str
            Periodo en formato YYYY-MM.

        Returns
        -------
        pandas.DataFrame
            Columnas: categoria_id, categoria, monto_manual, pct_recorte,
            promedio_3m y gasto_del_mes.
        """
        consulta = """
            WITH meses_previos AS (
                SELECT DISTINCT periodo
                FROM v_movimientos
                WHERE periodo < :periodo
                ORDER BY periodo DESC
                LIMIT 3
            ),
            promedio AS (
                SELECT categoria_id, SUM(gasto_real) / 3.0 AS promedio_3m
                FROM v_movimientos
                WHERE periodo IN (SELECT periodo FROM meses_previos)
                GROUP BY categoria_id
            ),
            del_mes AS (
                SELECT categoria_id, SUM(gasto_real) AS gasto_del_mes
                FROM v_movimientos
                WHERE periodo = :periodo
                GROUP BY categoria_id
            )
            SELECT
                c.id                            AS categoria_id,
                c.nombre                        AS categoria,
                COALESCE(p.monto_manual, 0.0)   AS monto_manual,
                COALESCE(p.pct_recorte, 0.0)    AS pct_recorte,
                COALESCE(pr.promedio_3m, 0.0)   AS promedio_3m,
                COALESCE(dm.gasto_del_mes, 0.0) AS gasto_del_mes
            FROM categorias c
            LEFT JOIN presupuestos p
                   ON p.categoria_id = c.id AND p.periodo = :periodo
            LEFT JOIN promedio pr ON pr.categoria_id = c.id
            LEFT JOIN del_mes  dm ON dm.categoria_id = c.id
            WHERE c.activa = 1 AND c.tipo = 'Gasto'
            ORDER BY c.orden, c.nombre
        """
        with connect(self._db_path) as conexion:
            return pd.read_sql_query(consulta, conexion, params={"periodo": periodo})

    def guardar_linea(
        self,
        periodo: str,
        categoria_id: int,
        monto_manual: float,
        pct_recorte: float,
    ) -> None:
        """Crea o actualiza el presupuesto de una categoría en un periodo."""
        with connect(self._db_path) as conexion:
            conexion.execute(
                """
                INSERT INTO presupuestos
                       (periodo, categoria_id, monto_manual, pct_recorte)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(periodo, categoria_id) DO UPDATE SET
                    monto_manual = excluded.monto_manual,
                    pct_recorte  = excluded.pct_recorte
                """,
                (periodo, categoria_id, float(monto_manual), float(pct_recorte)),
            )

    def guardar_lineas(self, periodo: str, lineas: pd.DataFrame) -> int:
        """
        Guarda en bloque las líneas editadas de un periodo.

        Parameters
        ----------
        periodo : str
            Periodo en formato YYYY-MM.
        lineas : pandas.DataFrame
            Debe traer las columnas categoria_id, monto_manual y pct_recorte.

        Returns
        -------
        int
            Número de líneas guardadas.
        """
        requeridas = {"categoria_id", "monto_manual", "pct_recorte"}
        faltantes = requeridas - set(lineas.columns)
        if faltantes:
            raise ValueError(f"Faltan columnas en el presupuesto: {sorted(faltantes)}")

        registros = [
            (
                periodo,
                int(fila.categoria_id),
                float(fila.monto_manual or 0),
                float(fila.pct_recorte or 0),
            )
            for fila in lineas.itertuples()
        ]

        with connect(self._db_path) as conexion:
            conexion.executemany(
                """
                INSERT INTO presupuestos
                       (periodo, categoria_id, monto_manual, pct_recorte)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(periodo, categoria_id) DO UPDATE SET
                    monto_manual = excluded.monto_manual,
                    pct_recorte  = excluded.pct_recorte
                """,
                registros,
            )

        return len(registros)

    def copiar_periodo(self, origen: str, destino: str) -> int:
        """Duplica el presupuesto de un periodo en otro y devuelve las filas."""
        with connect(self._db_path) as conexion:
            cursor = conexion.execute(
                """
                INSERT INTO presupuestos
                       (periodo, categoria_id, monto_manual, pct_recorte)
                SELECT ?, categoria_id, monto_manual, pct_recorte
                FROM presupuestos
                WHERE periodo = ?
                ON CONFLICT(periodo, categoria_id) DO UPDATE SET
                    monto_manual = excluded.monto_manual,
                    pct_recorte  = excluded.pct_recorte
                """,
                (destino, origen),
            )
            return cursor.rowcount

    def periodos_con_presupuesto(self) -> list[str]:
        """Devuelve los periodos que ya tienen presupuesto capturado."""
        with connect(self._db_path) as conexion:
            filas = conexion.execute(
                "SELECT DISTINCT periodo FROM presupuestos ORDER BY periodo DESC"
            ).fetchall()

        return [fila["periodo"] for fila in filas]
