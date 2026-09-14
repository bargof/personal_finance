from __future__ import annotations

import pandas as pd

from finanzas.data.database import connect
from finanzas.domain.entities import Proyecto

# ═══════════════════════════════════════════════════════════
# Proyectos
#
# El movimiento guarda el nombre del proyecto, no un id, así
# que renombrar tiene que arrastrar los movimientos en la misma
# transacción o el proyecto se parte en dos.
# ═══════════════════════════════════════════════════════════

_CAMPOS = (
    "nombre",
    "descripcion",
    "presupuesto",
    "fecha_inicio",
    "fecha_fin",
    "activo",
    "notas",
)


class ProyectosRepository:
    """Acceso al catálogo de proyectos y a sus totales."""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path

    def listar(self, solo_activos: bool = False) -> pd.DataFrame:
        """
        Devuelve los proyectos con su gasto acumulado.

        Incluye los que sólo existen como nombre en algún movimiento, que
        es como se creaban antes de que hubiera catálogo.
        """
        filtro = "WHERE activo = 1" if solo_activos else ""
        with connect(self._db_path) as conexion:
            df = pd.read_sql_query(
                f"SELECT * FROM v_proyectos {filtro} ORDER BY gasto DESC", conexion
            )

        if df.empty:
            return df

        for columna in ("desde", "hasta", "fecha_inicio", "fecha_fin"):
            df[columna] = pd.to_datetime(df[columna], errors="coerce")
        df["activo"] = df["activo"].astype(bool)
        df["declarado"] = df["declarado"].astype(bool)

        return df

    def nombres(self, solo_activos: bool = False) -> list[str]:
        """Devuelve los nombres de proyecto para poblar selectores."""
        df = self.listar(solo_activos=solo_activos)
        if df.empty:
            return []

        return sorted(df["proyecto"].tolist())

    def crear(self, proyecto: Proyecto) -> int:
        """Da de alta un proyecto y devuelve su id."""
        columnas = ", ".join(_CAMPOS)
        marcadores = ", ".join("?" for _ in _CAMPOS)

        with connect(self._db_path) as conexion:
            cursor = conexion.execute(
                f"INSERT INTO proyectos ({columnas}) VALUES ({marcadores})",
                _a_valores(proyecto),
            )
            return int(cursor.lastrowid)

    def actualizar(self, proyecto_id: int, proyecto: Proyecto) -> None:
        """
        Reemplaza los datos de un proyecto.

        Si cambió el nombre, arrastra los movimientos que lo usaban: el
        vínculo es por nombre, así que dejarlos atrás partiría el proyecto
        en dos.
        """
        asignaciones = ", ".join(f"{campo} = ?" for campo in _CAMPOS)

        with connect(self._db_path) as conexion:
            anterior = conexion.execute(
                "SELECT nombre FROM proyectos WHERE id = ?", (proyecto_id,)
            ).fetchone()

            conexion.execute(
                f"UPDATE proyectos SET {asignaciones} WHERE id = ?",
                [*_a_valores(proyecto), proyecto_id],
            )

            if anterior is not None and anterior["nombre"] != proyecto.nombre.strip():
                conexion.execute(
                    "UPDATE movimientos SET proyecto = ? WHERE proyecto = ?",
                    (proyecto.nombre.strip(), anterior["nombre"]),
                )

    def eliminar(self, proyecto_id: int, desligar: bool = True) -> None:
        """
        Elimina un proyecto del catálogo.

        Por defecto desliga sus movimientos en vez de borrarlos: el gasto
        ocurrió aunque el proyecto deje de interesar.
        """
        with connect(self._db_path) as conexion:
            fila = conexion.execute(
                "SELECT nombre FROM proyectos WHERE id = ?", (proyecto_id,)
            ).fetchone()
            if fila is None:
                return

            if desligar:
                conexion.execute(
                    "UPDATE movimientos SET proyecto = '' WHERE proyecto = ?",
                    (fila["nombre"],),
                )

            conexion.execute("DELETE FROM proyectos WHERE id = ?", (proyecto_id,))

    def adoptar(self, nombre: str) -> int:
        """
        Da de alta en el catálogo un proyecto que sólo existía como texto.

        Los proyectos anteriores al catálogo viven en `movimientos.proyecto`
        y no tienen ficha; esto les da una sin tocar sus movimientos.
        """
        return self.crear(Proyecto(nombre=nombre))


def _a_valores(proyecto: Proyecto) -> list[object]:
    """Aplana un proyecto al orden de columnas de `_CAMPOS`."""
    return [
        proyecto.nombre.strip(),
        proyecto.descripcion.strip(),
        float(proyecto.presupuesto),
        proyecto.fecha_inicio.isoformat() if proyecto.fecha_inicio else None,
        proyecto.fecha_fin.isoformat() if proyecto.fecha_fin else None,
        int(proyecto.activo),
        proyecto.notas.strip(),
    ]
