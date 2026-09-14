from __future__ import annotations

import pandas as pd

from finanzas.data.database import connect
from finanzas.domain.entities import Suscripcion
from finanzas.domain.enums import COBROS_POR_ANIO

# ═══════════════════════════════════════════════════════════
# Suscripciones: el gasto que se renueva solo y que casi
# nunca se revisa. El costo mensual se normaliza para poder
# comparar un cobro anual con uno mensual.
# ═══════════════════════════════════════════════════════════

_CAMPOS_ESCRITURA = (
    "servicio",
    "categoria_id",
    "subcategoria_id",
    "costo_por_cobro",
    "frecuencia",
    "proximo_cobro",
    "cuenta_id",
    "renovacion_automatica",
    "necesidad",
    "activa",
    "notas",
)


class SuscripcionesRepository:
    """Lectura y escritura de suscripciones recurrentes."""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path

    def listar(self, solo_activas: bool = False) -> pd.DataFrame:
        """
        Devuelve las suscripciones con su costo mensual y anual calculados.

        Parameters
        ----------
        solo_activas : bool
            Si es True, omite las suscripciones dadas de baja.
        """
        filtro = "WHERE s.activa = 1" if solo_activas else ""
        consulta = f"""
            SELECT
                s.*,
                COALESCE(c.nombre, '')   AS categoria,
                COALESCE(sub.nombre, '') AS subcategoria,
                COALESCE(cu.nombre, '')  AS cuenta
            FROM suscripciones s
            LEFT JOIN categorias    c   ON c.id   = s.categoria_id
            LEFT JOIN subcategorias sub ON sub.id = s.subcategoria_id
            LEFT JOIN cuentas       cu  ON cu.id  = s.cuenta_id
            {filtro}
            ORDER BY s.activa DESC, s.servicio
        """
        with connect(self._db_path) as conexion:
            df = pd.read_sql_query(consulta, conexion)

        if df.empty:
            return df

        df["proximo_cobro"] = pd.to_datetime(df["proximo_cobro"], errors="coerce")
        cobros = df["frecuencia"].map(COBROS_POR_ANIO).fillna(12)
        df["costo_anual"] = df["costo_por_cobro"] * cobros
        df["costo_mensual"] = df["costo_anual"] / 12
        df["activa"] = df["activa"].astype(bool)
        df["renovacion_automatica"] = df["renovacion_automatica"].astype(bool)
        df["candidato_a_cancelar"] = (
            df["activa"] & (df["necesidad"] == "Deseo") & df["renovacion_automatica"]
        )

        return df

    def costo_mensual_total(self, solo_activas: bool = True) -> float:
        """Devuelve el costo mensual normalizado de todas las suscripciones."""
        df = self.listar(solo_activas=solo_activas)
        if df.empty:
            return 0.0
        return float(df["costo_mensual"].sum())

    def crear(self, suscripcion: Suscripcion) -> int:
        """Inserta una suscripción y devuelve su id."""
        columnas = ", ".join(_CAMPOS_ESCRITURA)
        marcadores = ", ".join("?" for _ in _CAMPOS_ESCRITURA)

        with connect(self._db_path) as conexion:
            cursor = conexion.execute(
                f"INSERT INTO suscripciones ({columnas}) VALUES ({marcadores})",
                _a_valores(suscripcion),
            )
            return int(cursor.lastrowid)

    def actualizar(self, suscripcion_id: int, suscripcion: Suscripcion) -> None:
        """Reemplaza los datos de una suscripción."""
        asignaciones = ", ".join(f"{campo} = ?" for campo in _CAMPOS_ESCRITURA)

        with connect(self._db_path) as conexion:
            conexion.execute(
                f"UPDATE suscripciones SET {asignaciones} WHERE id = ?",
                [*_a_valores(suscripcion), suscripcion_id],
            )

    def cambiar_estado(self, suscripcion_id: int, activa: bool) -> None:
        """Da de alta o de baja una suscripción sin perder su historial."""
        with connect(self._db_path) as conexion:
            conexion.execute(
                "UPDATE suscripciones SET activa = ? WHERE id = ?",
                (int(activa), suscripcion_id),
            )

    def eliminar(self, suscripcion_id: int) -> None:
        """Elimina una suscripción."""
        with connect(self._db_path) as conexion:
            conexion.execute(
                "DELETE FROM suscripciones WHERE id = ?", (suscripcion_id,)
            )


def _a_valores(suscripcion: Suscripcion) -> list[object]:
    """Aplana una suscripción al orden de columnas de `_CAMPOS_ESCRITURA`."""
    return [
        suscripcion.servicio.strip(),
        suscripcion.categoria_id,
        suscripcion.subcategoria_id,
        float(suscripcion.costo_por_cobro),
        str(suscripcion.frecuencia),
        suscripcion.proximo_cobro.isoformat() if suscripcion.proximo_cobro else None,
        suscripcion.cuenta_id,
        int(suscripcion.renovacion_automatica),
        str(suscripcion.necesidad),
        int(suscripcion.activa),
        suscripcion.notas.strip(),
    ]
