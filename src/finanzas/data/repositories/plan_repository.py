from __future__ import annotations

from datetime import date

from finanzas.data.database import connect

# ═══════════════════════════════════════════════════════════
# Plan de pagos: lo que el usuario decidió a mano sobre cada
# concepto del mes, y los ajustes sueltos de la vista.
# ═══════════════════════════════════════════════════════════


class PlanRepository:
    """Lectura y escritura del plan de pagos."""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path

    def ajustes(self, mes: date) -> dict[str, tuple[str, float | None]]:
        """Devuelve {clave: (decisión, monto)} de lo decidido a mano en el mes."""
        with connect(self._db_path) as conexion:
            filas = conexion.execute(
                "SELECT clave, decision, monto FROM plan_pagos WHERE mes = ?",
                (_mes(mes),),
            ).fetchall()

        return {fila["clave"]: (fila["decision"], fila["monto"]) for fila in filas}

    def guardar_ajuste(
        self, mes: date, clave: str, decision: str, monto: float | None
    ) -> None:
        """Crea o reemplaza la decisión sobre un concepto del mes."""
        with connect(self._db_path) as conexion:
            conexion.execute(
                """
                INSERT INTO plan_pagos (mes, clave, decision, monto)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(mes, clave) DO UPDATE SET
                    decision = excluded.decision,
                    monto = excluded.monto
                """,
                (_mes(mes), clave, decision, monto),
            )

    def quitar_ajuste(self, mes: date, clave: str) -> None:
        """Devuelve un concepto a lo que sugiera el plan."""
        with connect(self._db_path) as conexion:
            conexion.execute(
                "DELETE FROM plan_pagos WHERE mes = ? AND clave = ?", (_mes(mes), clave)
            )

    def quitar_ajustes(self, mes: date) -> None:
        """Olvida todo lo decidido a mano en el mes."""
        with connect(self._db_path) as conexion:
            conexion.execute("DELETE FROM plan_pagos WHERE mes = ?", (_mes(mes),))

    def retiros(self) -> dict[tuple[str, int], float]:
        """Todos los retiros simulados: {(«AAAA-MM», cuenta_id): monto}."""
        with connect(self._db_path) as conexion:
            filas = conexion.execute(
                "SELECT mes, cuenta_id, monto FROM plan_retiros"
            ).fetchall()
        return {(f["mes"], int(f["cuenta_id"])): float(f["monto"]) for f in filas}

    def guardar_retiro(self, mes: date, cuenta_id: int, monto: float) -> None:
        """Fija el retiro simulado de un mes; cero lo quita."""
        with connect(self._db_path) as conexion:
            if monto <= 0:
                conexion.execute(
                    "DELETE FROM plan_retiros WHERE mes = ? AND cuenta_id = ?",
                    (_mes(mes), cuenta_id),
                )
                return
            conexion.execute(
                """
                INSERT INTO plan_retiros (mes, cuenta_id, monto) VALUES (?, ?, ?)
                ON CONFLICT(mes, cuenta_id) DO UPDATE SET monto = excluded.monto
                """,
                (_mes(mes), cuenta_id, float(monto)),
            )

    def simulados(self) -> list[dict[str, object]]:
        """Los ingresos y aportaciones simulados, en el orden en que se dieron."""
        with connect(self._db_path) as conexion:
            filas = conexion.execute(
                "SELECT id, tipo, concepto, monto, dia, mes, cuenta_id "
                "FROM plan_simulados ORDER BY id"
            ).fetchall()
        return [dict(fila) for fila in filas]

    def crear_simulado(
        self,
        tipo: str,
        concepto: str,
        monto: float,
        dia: int,
        mes: date | None,
        cuenta_id: int | None,
    ) -> int:
        """Da de alta un ingreso o una aportación simulados."""
        with connect(self._db_path) as conexion:
            cursor = conexion.execute(
                """
                INSERT INTO plan_simulados (tipo, concepto, monto, dia, mes, cuenta_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (tipo, concepto, monto, dia, _mes(mes) if mes else None, cuenta_id),
            )
            return int(cursor.lastrowid)

    def eliminar_simulado(self, simulado_id: int) -> None:
        """Quita un ingreso o una aportación simulados."""
        with connect(self._db_path) as conexion:
            conexion.execute("DELETE FROM plan_simulados WHERE id = ?", (simulado_id,))

    def valor(self, clave: str) -> str | None:
        """Lee un valor suelto de `configuracion`, o None si no está."""
        with connect(self._db_path) as conexion:
            fila = conexion.execute(
                "SELECT valor FROM configuracion WHERE clave = ?", (clave,)
            ).fetchone()
        return None if fila is None else fila["valor"]

    def guardar_valor(self, clave: str, valor: str, descripcion: str = "") -> None:
        """Guarda un valor suelto en `configuracion`."""
        with connect(self._db_path) as conexion:
            conexion.execute(
                """
                INSERT INTO configuracion (clave, valor, descripcion)
                VALUES (?, ?, ?)
                ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor
                """,
                (clave, valor, descripcion),
            )


def _mes(mes: date) -> str:
    return f"{mes.year:04d}-{mes.month:02d}"
