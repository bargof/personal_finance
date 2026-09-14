from __future__ import annotations

import pandas as pd

from finanzas.data.database import connect
from finanzas.domain.entities import CierreMensual, PosicionPatrimonial

# ═══════════════════════════════════════════════════════════
# Patrimonio: la foto actual del balance y los cierres
# mensuales que registran cómo se llegó hasta ahí.
# ═══════════════════════════════════════════════════════════

_CAMPOS_POSICION = (
    "nombre",
    "tipo",
    "cuenta_id",
    "subtipo",
    "institucion",
    "saldo",
    "liquidez",
    "tasa_anual",
    "fecha_corte",
    "moneda",
    "notas",
)

_CAMPOS_CIERRE = (
    "efectivo",
    "ahorro",
    "inversiones",
    "otros_activos",
    "deudas",
    "notas",
)


class PatrimonioRepository:
    """Acceso al balance de activos y pasivos y a los cierres mensuales."""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path

    # ── Posiciones ───────────────────────────────────────

    def listar(self) -> pd.DataFrame:
        """
        Devuelve activos y pasivos ordenados por lado del balance y saldo.

        Lee `v_patrimonio`, que añade el nombre de la cuenta ligada y el
        aporte con signo al patrimonio neto.
        """
        with connect(self._db_path) as conexion:
            df = pd.read_sql_query(
                "SELECT * FROM v_patrimonio ORDER BY tipo, saldo DESC", conexion
            )

        if not df.empty:
            df["fecha_corte"] = pd.to_datetime(df["fecha_corte"], errors="coerce")

        return df

    def cuentas_sin_posicion(self) -> pd.DataFrame:
        """
        Devuelve las cuentas activas que aún no tienen saldo en el balance.

        Es el pendiente que impide que el estado de situación financiera
        esté incompleto sin avisar.
        """
        with connect(self._db_path) as conexion:
            return pd.read_sql_query(
                "SELECT * FROM v_cuentas_sin_posicion ORDER BY nombre", conexion
            )

    def resumen(self) -> dict[str, float]:
        """
        Devuelve activos, pasivos y patrimonio neto.

        Los pasivos incluyen los adeudos generados —lo gastado que aún no
        se paga—, que se reportan aparte en `por_pagar` porque no son una
        posición capturada sino la suma de los movimientos devengados.
        """
        with connect(self._db_path) as conexion:
            fila = conexion.execute("SELECT * FROM v_patrimonio_neto").fetchone()

        if fila is None or fila["patrimonio_neto"] is None:
            return {
                "activos": 0.0,
                "pasivos": 0.0,
                "por_pagar": 0.0,
                "patrimonio_neto": 0.0,
            }

        return {
            "activos": float(fila["activos"]),
            "pasivos": float(fila["pasivos"]),
            "por_pagar": float(fila["por_pagar"]),
            "patrimonio_neto": float(fila["patrimonio_neto"]),
        }

    def activos_liquidos(self) -> float:
        """Suma de los activos de liquidez alta: el colchón disponible ya."""
        with connect(self._db_path) as conexion:
            fila = conexion.execute(
                """
                SELECT COALESCE(SUM(saldo), 0) AS total
                FROM patrimonio
                WHERE tipo = 'Activo' AND liquidez = 'Alta'
                """
            ).fetchone()

        return float(fila["total"])

    def crear(self, posicion: PosicionPatrimonial) -> int:
        """Inserta una posición patrimonial y devuelve su id."""
        columnas = ", ".join(_CAMPOS_POSICION)
        marcadores = ", ".join("?" for _ in _CAMPOS_POSICION)

        with connect(self._db_path) as conexion:
            cursor = conexion.execute(
                f"INSERT INTO patrimonio ({columnas}) VALUES ({marcadores})",
                _a_valores_posicion(posicion),
            )
            return int(cursor.lastrowid)

    def actualizar(self, posicion_id: int, posicion: PosicionPatrimonial) -> None:
        """Reemplaza los datos de una posición patrimonial."""
        asignaciones = ", ".join(f"{campo} = ?" for campo in _CAMPOS_POSICION)

        with connect(self._db_path) as conexion:
            conexion.execute(
                f"UPDATE patrimonio SET {asignaciones} WHERE id = ?",
                [*_a_valores_posicion(posicion), posicion_id],
            )

    def eliminar(self, posicion_id: int) -> None:
        """Elimina una posición patrimonial."""
        with connect(self._db_path) as conexion:
            conexion.execute("DELETE FROM patrimonio WHERE id = ?", (posicion_id,))

    # ── Cierres mensuales ────────────────────────────────

    def listar_cierres(self) -> pd.DataFrame:
        """Devuelve los cierres con patrimonio neto y cambio mensual."""
        with connect(self._db_path) as conexion:
            df = pd.read_sql_query(
                "SELECT * FROM cierres_mensuales ORDER BY periodo", conexion
            )

        if df.empty:
            return df

        df["patrimonio_neto"] = (
            df["efectivo"] + df["ahorro"] + df["inversiones"] + df["otros_activos"]
        ) - df["deudas"]
        df["cambio_mensual"] = df["patrimonio_neto"].diff().fillna(0.0)

        return df

    def guardar_cierre(self, cierre: CierreMensual) -> None:
        """Crea o actualiza el cierre de un periodo."""
        columnas = ", ".join(("periodo", *_CAMPOS_CIERRE))
        marcadores = ", ".join("?" for _ in range(len(_CAMPOS_CIERRE) + 1))
        actualizaciones = ", ".join(
            f"{campo} = excluded.{campo}" for campo in _CAMPOS_CIERRE
        )

        with connect(self._db_path) as conexion:
            conexion.execute(
                f"""
                INSERT INTO cierres_mensuales ({columnas}) VALUES ({marcadores})
                ON CONFLICT(periodo) DO UPDATE SET {actualizaciones}
                """,
                (
                    cierre.periodo,
                    float(cierre.efectivo),
                    float(cierre.ahorro),
                    float(cierre.inversiones),
                    float(cierre.otros_activos),
                    float(cierre.deudas),
                    cierre.notas.strip(),
                ),
            )

    def eliminar_cierre(self, periodo: str) -> None:
        """Elimina el cierre de un periodo."""
        with connect(self._db_path) as conexion:
            conexion.execute(
                "DELETE FROM cierres_mensuales WHERE periodo = ?", (periodo,)
            )

    def cierre_desde_patrimonio(self, periodo: str) -> CierreMensual:
        """
        Construye el cierre de un periodo a partir del balance actual.

        Agrupa los activos por subtipo para prellenar la captura del cierre,
        de modo que el usuario sólo confirme en vez de retecleaer saldos.
        """
        with connect(self._db_path) as conexion:
            filas = conexion.execute(
                "SELECT tipo, subtipo, saldo FROM patrimonio"
            ).fetchall()

        cierre = CierreMensual(periodo=periodo)
        for fila in filas:
            saldo = float(fila["saldo"])
            if fila["tipo"] == "Pasivo":
                cierre.deudas += saldo
                continue

            subtipo = (fila["subtipo"] or "").lower()
            if "ahorro" in subtipo:
                cierre.ahorro += saldo
            elif "invers" in subtipo:
                cierre.inversiones += saldo
            elif "efectivo" in subtipo or "banco" in subtipo:
                cierre.efectivo += saldo
            else:
                cierre.otros_activos += saldo

        return cierre


def _a_valores_posicion(posicion: PosicionPatrimonial) -> list[object]:
    """Aplana una posición al orden de columnas de `_CAMPOS_POSICION`."""
    return [
        posicion.nombre.strip(),
        str(posicion.tipo),
        posicion.cuenta_id,
        posicion.subtipo.strip(),
        posicion.institucion.strip(),
        float(posicion.saldo),
        str(posicion.liquidez),
        float(posicion.tasa_anual),
        posicion.fecha_corte.isoformat() if posicion.fecha_corte else None,
        posicion.moneda,
        posicion.notas.strip(),
    ]
