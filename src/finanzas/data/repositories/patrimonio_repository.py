from __future__ import annotations

from datetime import date

import pandas as pd

from finanzas.data.database import connect
from finanzas.domain.entities import PosicionPatrimonial, SaldoVerificado
from finanzas.domain.enums import TipoCuenta
from finanzas.domain.saldos import Ancla, Descuadre, Flujo, Libro, SaldoDeducido

# ═══════════════════════════════════════════════════════════
# Patrimonio: los saldos de las cuentas, deducidos de los
# movimientos y anclados en saldos verificados, más las
# posiciones que no son cuenta (la casa, el auto).
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


class PatrimonioRepository:
    """Acceso a saldos verificados, libros por cuenta y posiciones."""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path

    # ── Libros por cuenta ────────────────────────────────

    def libros(self, solo_activas: bool = True) -> dict[int, Libro]:
        """
        Devuelve el libro de cada cuenta: sus flujos y sus anclas.

        Es la materia prima para deducir saldos a cualquier fecha; el
        cálculo vive en el dominio (`Libro`) y aquí sólo se lee.
        """
        filtro = "WHERE activa = 1" if solo_activas else ""
        with connect(self._db_path) as conexion:
            cuentas = conexion.execute(f"SELECT id FROM cuentas {filtro}").fetchall()
            flujos = conexion.execute(
                "SELECT cuenta_id, fecha, movimiento FROM v_flujo_cuentas"
            ).fetchall()
            anclas = conexion.execute(
                "SELECT id, cuenta_id, fecha, saldo, origen FROM saldos_verificados"
            ).fetchall()

        libros = {int(fila["id"]): Libro() for fila in cuentas}
        for fila in flujos:
            libro = libros.get(int(fila["cuenta_id"]))
            if libro is not None:
                libro.flujos.append(
                    Flujo(
                        fecha=date.fromisoformat(fila["fecha"]),
                        monto=float(fila["movimiento"]),
                    )
                )
        for fila in anclas:
            libro = libros.get(int(fila["cuenta_id"]))
            if libro is not None:
                libro.anclas.append(
                    Ancla(
                        fecha=date.fromisoformat(fila["fecha"]),
                        saldo=float(fila["saldo"]),
                        origen=fila["origen"],
                        id=int(fila["id"]),
                    )
                )

        return libros

    def cuentas(self, solo_activas: bool = True) -> pd.DataFrame:
        """Devuelve las cuentas con su tipo, para etiquetar los saldos."""
        filtro = "WHERE activa = 1" if solo_activas else ""
        with connect(self._db_path) as conexion:
            return pd.read_sql_query(
                f"SELECT id, nombre, tipo, institucion, activa, "
                f"dia_corte, dia_pago, pago_mensual, restringida, "
                f"tasa_anual, posponer_hasta "
                f"FROM cuentas {filtro} ORDER BY nombre",
                conexion,
            )

    def saldos_a(
        self,
        fecha: date,
        solo_activas: bool = True,
        libros: dict[int, Libro] | None = None,
    ) -> pd.DataFrame:
        """
        Deduce el saldo de cada cuenta al cierre de `fecha`.

        Parameters
        ----------
        libros : dict, optional
            Los libros ya leídos, para deducir varias fechas seguidas sin
            volver a la base por cada una.

        Returns
        -------
        pandas.DataFrame
            Una fila por cuenta con `saldo` (signo del libro), `saldo_visto`
            (como lo enseña el banco), de qué ancla salió y cuántos
            movimientos median entre el ancla y la fecha.
        """
        cuentas = self.cuentas(solo_activas)
        if libros is None:
            libros = self.libros(solo_activas)

        filas: list[dict[str, object]] = []
        for cuenta in cuentas.itertuples():
            tipo = TipoCuenta(cuenta.tipo)
            deducido: SaldoDeducido = libros[int(cuenta.id)].saldo_a(fecha)
            saldo = deducido.saldo
            filas.append(
                {
                    "cuenta_id": int(cuenta.id),
                    "cuenta": cuenta.nombre,
                    "tipo": str(tipo),
                    "institucion": cuenta.institucion,
                    "lado": str(tipo.lado),
                    "liquidez": str(tipo.liquidez),
                    "saldo": saldo,
                    "saldo_visto": -saldo if tipo.es_pasivo else saldo,
                    "verificado": deducido.verificado,
                    "sentido": str(deducido.sentido),
                    "ancla_fecha": deducido.ancla.fecha if deducido.ancla else None,
                    "ancla_saldo": deducido.ancla.saldo if deducido.ancla else None,
                    "ancla_origen": deducido.ancla.origen if deducido.ancla else "",
                    "movimientos": deducido.movimientos,
                    "restringida": bool(cuenta.restringida),
                }
            )

        df = pd.DataFrame(
            filas,
            columns=[
                "cuenta_id",
                "cuenta",
                "tipo",
                "institucion",
                "lado",
                "liquidez",
                "saldo",
                "saldo_visto",
                "verificado",
                "sentido",
                "ancla_fecha",
                "ancla_saldo",
                "ancla_origen",
                "movimientos",
                "restringida",
            ],
        )
        if not df.empty:
            df["ancla_fecha"] = pd.to_datetime(df["ancla_fecha"], errors="coerce")

        return df

    def descuadres(self) -> list[tuple[int, Descuadre]]:
        """Devuelve, por cuenta, los pares de anclas que los movimientos no explican."""
        hallazgos: list[tuple[int, Descuadre]] = []
        for cuenta_id, libro in self.libros(solo_activas=False).items():
            for descuadre in libro.descuadres():
                hallazgos.append((cuenta_id, descuadre))

        return hallazgos

    # ── Saldos verificados ───────────────────────────────

    def listar_anclas(self) -> pd.DataFrame:
        """Devuelve los saldos verificados, del más reciente al más viejo."""
        with connect(self._db_path) as conexion:
            df = pd.read_sql_query(
                "SELECT * FROM v_saldos_verificados ORDER BY fecha DESC, cuenta",
                conexion,
            )

        if not df.empty:
            df["fecha"] = pd.to_datetime(df["fecha"])

        return df

    def guardar_ancla(self, ancla: SaldoVerificado) -> int:
        """
        Crea o reemplaza el saldo verificado de una cuenta en una fecha.

        Una cuenta cierra un día con un solo saldo: capturarlo dos veces es
        corregirlo, no duplicarlo.
        """
        with connect(self._db_path) as conexion:
            cursor = conexion.execute(
                """
                INSERT INTO saldos_verificados (cuenta_id, fecha, saldo, origen, nota)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(cuenta_id, fecha) DO UPDATE SET
                    saldo = excluded.saldo,
                    origen = excluded.origen,
                    nota = excluded.nota
                """,
                (
                    ancla.cuenta_id,
                    ancla.fecha.isoformat(),
                    float(ancla.saldo),
                    str(ancla.origen),
                    ancla.nota.strip(),
                ),
            )
            if cursor.lastrowid:
                return int(cursor.lastrowid)

            fila = conexion.execute(
                "SELECT id FROM saldos_verificados WHERE cuenta_id = ? AND fecha = ?",
                (ancla.cuenta_id, ancla.fecha.isoformat()),
            ).fetchone()
            return int(fila["id"])

    def eliminar_ancla(self, ancla_id: int) -> None:
        """Elimina un saldo verificado."""
        with connect(self._db_path) as conexion:
            conexion.execute("DELETE FROM saldos_verificados WHERE id = ?", (ancla_id,))

    def cuentas_sin_ancla(self) -> pd.DataFrame:
        """
        Devuelve las cuentas activas sin ningún saldo verificado.

        Su saldo se está sumando desde cero, que casi nunca es verdad.
        """
        with connect(self._db_path) as conexion:
            return pd.read_sql_query(
                """
                SELECT cu.id, cu.nombre, cu.tipo, cu.institucion
                FROM cuentas cu
                WHERE cu.activa = 1
                  AND NOT EXISTS (
                      SELECT 1 FROM saldos_verificados sv WHERE sv.cuenta_id = cu.id
                  )
                ORDER BY cu.nombre
                """,
                conexion,
            )

    # ── Lo exigible de cada deuda ────────────────────────

    def exigibles(self) -> pd.DataFrame:
        """
        Devuelve lo declarado exigible por cuenta y lo abonado desde entonces.

        El abono es todo lo que entró a la cuenta desde el día en que se
        declaró, incluido: en una deuda, lo que entra es lo que se pagó.
        """
        with connect(self._db_path) as conexion:
            df = pd.read_sql_query(
                """
                SELECT
                    e.cuenta_id,
                    e.monto,
                    e.declarado_el,
                    e.fecha_limite,
                    e.nota,
                    COALESCE((
                        SELECT SUM(f.movimiento)
                        FROM v_flujo_cuentas f
                        WHERE f.cuenta_id = e.cuenta_id
                          AND f.movimiento > 0
                          AND f.fecha >= e.declarado_el
                    ), 0) AS abonado
                FROM exigibles e
                """,
                conexion,
            )

        for columna in ("declarado_el", "fecha_limite"):
            df[columna] = pd.to_datetime(df[columna], errors="coerce")
        return df

    def guardar_exigible(
        self,
        cuenta_id: int,
        monto: float,
        declarado_el: date,
        fecha_limite: date | None,
        nota: str,
    ) -> None:
        """Crea o reemplaza lo exigible de una cuenta: hay uno por cuenta."""
        with connect(self._db_path) as conexion:
            conexion.execute(
                """
                INSERT INTO exigibles
                    (cuenta_id, monto, declarado_el, fecha_limite, nota)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(cuenta_id) DO UPDATE SET
                    monto = excluded.monto,
                    declarado_el = excluded.declarado_el,
                    fecha_limite = excluded.fecha_limite,
                    nota = excluded.nota
                """,
                (
                    cuenta_id,
                    float(monto),
                    declarado_el.isoformat(),
                    fecha_limite.isoformat() if fecha_limite else None,
                    nota.strip(),
                ),
            )

    def abonos(
        self, cuenta_id: int, despues_de: date, hasta: date | None = None
    ) -> float:
        """
        Suma lo que entró a una cuenta después de `despues_de`.

        En una deuda, lo que entra es lo que se pagó. `hasta` cierra el
        rango, incluido.
        """
        condicion = "AND fecha <= ?" if hasta is not None else ""
        parametros: list[object] = [cuenta_id, despues_de.isoformat()]
        if hasta is not None:
            parametros.append(hasta.isoformat())

        with connect(self._db_path) as conexion:
            fila = conexion.execute(
                f"""
                SELECT COALESCE(SUM(movimiento), 0) AS total
                FROM v_flujo_cuentas
                WHERE cuenta_id = ? AND movimiento > 0 AND fecha > ? {condicion}
                """,
                parametros,
            ).fetchone()

        return float(fila["total"])

    def guardar_calendario_deuda(
        self,
        cuenta_id: int,
        dia_corte: int | None,
        dia_pago: int | None,
        pago_mensual: float | None,
    ) -> None:
        """Fija los días de corte y de pago, y la parcialidad, de una deuda."""
        with connect(self._db_path) as conexion:
            conexion.execute(
                """
                UPDATE cuentas
                   SET dia_corte = ?, dia_pago = ?, pago_mensual = ?
                 WHERE id = ?
                """,
                (dia_corte, dia_pago, pago_mensual, cuenta_id),
            )

    def guardar_reglas_deuda(
        self, cuenta_id: int, tasa_anual: float | None, posponer_hasta: date | None
    ) -> None:
        """Fija lo que cuesta posponer una deuda y hasta cuándo se puede."""
        with connect(self._db_path) as conexion:
            conexion.execute(
                "UPDATE cuentas SET tasa_anual = ?, posponer_hasta = ? WHERE id = ?",
                (
                    tasa_anual,
                    posponer_hasta.isoformat() if posponer_hasta else None,
                    cuenta_id,
                ),
            )

    def eliminar_exigible(self, cuenta_id: int) -> None:
        """Quita lo exigible de una cuenta."""
        with connect(self._db_path) as conexion:
            conexion.execute("DELETE FROM exigibles WHERE cuenta_id = ?", (cuenta_id,))

    # ── Adeudos a una fecha ──────────────────────────────

    def por_pagar_a(self, fecha: date) -> float:
        """
        Suma lo gastado hasta `fecha` que a esa fecha aún no se había pagado.

        Es el pasivo que no está en ninguna cuenta: la renta que se debe,
        lo que alguien pagó por uno.
        """
        with connect(self._db_path) as conexion:
            fila = conexion.execute(
                """
                SELECT COALESCE(SUM(monto), 0) AS total
                FROM movimientos
                WHERE tipo = 'Gasto'
                  AND estado = 'Confirmado'
                  AND fecha <= ?
                  AND (fecha_pago IS NULL OR fecha_pago > ?)
                """,
                (fecha.isoformat(), fecha.isoformat()),
            ).fetchone()

        return float(fila["total"])

    def primera_fecha(self) -> date | None:
        """Devuelve la fecha más antigua con movimientos o anclas, o None."""
        with connect(self._db_path) as conexion:
            fila = conexion.execute(
                """
                SELECT MIN(fecha) AS fecha FROM (
                    SELECT MIN(fecha) AS fecha FROM movimientos
                    UNION ALL
                    SELECT MIN(fecha) FROM saldos_verificados
                )
                """
            ).fetchone()

        if fila is None or fila["fecha"] is None:
            return None
        return date.fromisoformat(fila["fecha"])

    # ── Posiciones que no son cuenta ─────────────────────

    def listar(self) -> pd.DataFrame:
        """
        Devuelve las posiciones capturadas, ordenadas por lado y saldo.

        Lee `v_patrimonio`, que añade el aporte con signo al patrimonio.
        """
        with connect(self._db_path) as conexion:
            df = pd.read_sql_query(
                "SELECT * FROM v_patrimonio ORDER BY tipo, saldo DESC", conexion
            )

        if not df.empty:
            df["fecha_corte"] = pd.to_datetime(df["fecha_corte"], errors="coerce")

        return df

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
