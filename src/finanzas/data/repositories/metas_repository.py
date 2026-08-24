from __future__ import annotations

import pandas as pd

from finanzas.data.database import connect
from finanzas.domain.entities import Meta

# ═══════════════════════════════════════════════════════════
# Metas financieras
# ═══════════════════════════════════════════════════════════

_CAMPOS_ESCRITURA = (
    "objetivo",
    "tipo",
    "monto_meta",
    "acumulado",
    "aporte_mensual_planeado",
    "fecha_limite",
    "prioridad",
    "vehiculo",
    "notas",
)


class MetasRepository:
    """Lectura y escritura de metas financieras."""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path

    def listar(self) -> pd.DataFrame:
        """Devuelve todas las metas ordenadas por prioridad y fecha límite."""
        consulta = """
            SELECT *
            FROM metas
            ORDER BY CASE prioridad
                        WHEN 'Alta'  THEN 1
                        WHEN 'Media' THEN 2
                        ELSE 3
                     END,
                     COALESCE(fecha_limite, '9999-12-31')
        """
        with connect(self._db_path) as conexion:
            df = pd.read_sql_query(consulta, conexion)

        if not df.empty:
            df["fecha_limite"] = pd.to_datetime(df["fecha_limite"], errors="coerce")

        return df

    def entidades(self) -> list[Meta]:
        """Devuelve las metas como entidades del dominio."""
        df = self.listar()
        return [
            Meta(
                id=int(fila.id),
                objetivo=fila.objetivo,
                tipo=fila.tipo,
                monto_meta=float(fila.monto_meta),
                acumulado=float(fila.acumulado),
                aporte_mensual_planeado=float(fila.aporte_mensual_planeado),
                fecha_limite=(
                    fila.fecha_limite.date() if pd.notna(fila.fecha_limite) else None
                ),
                prioridad=fila.prioridad,
                vehiculo=fila.vehiculo,
                notas=fila.notas,
            )
            for fila in df.itertuples()
        ]

    def crear(self, meta: Meta) -> int:
        """Inserta una meta y devuelve su id."""
        columnas = ", ".join(_CAMPOS_ESCRITURA)
        marcadores = ", ".join("?" for _ in _CAMPOS_ESCRITURA)

        with connect(self._db_path) as conexion:
            cursor = conexion.execute(
                f"INSERT INTO metas ({columnas}) VALUES ({marcadores})",
                _a_valores(meta),
            )
            return int(cursor.lastrowid)

    def actualizar(self, meta_id: int, meta: Meta) -> None:
        """Reemplaza los datos de una meta existente."""
        asignaciones = ", ".join(f"{campo} = ?" for campo in _CAMPOS_ESCRITURA)

        with connect(self._db_path) as conexion:
            conexion.execute(
                f"UPDATE metas SET {asignaciones} WHERE id = ?",
                [*_a_valores(meta), meta_id],
            )

    def abonar(self, meta_id: int, monto: float) -> None:
        """Suma una aportación al acumulado de la meta."""
        with connect(self._db_path) as conexion:
            conexion.execute(
                "UPDATE metas SET acumulado = acumulado + ? WHERE id = ?",
                (float(monto), meta_id),
            )

    def eliminar(self, meta_id: int) -> None:
        """Elimina una meta."""
        with connect(self._db_path) as conexion:
            conexion.execute("DELETE FROM metas WHERE id = ?", (meta_id,))


def _a_valores(meta: Meta) -> list[object]:
    """Aplana una meta al orden de columnas de `_CAMPOS_ESCRITURA`."""
    return [
        meta.objetivo.strip(),
        meta.tipo,
        float(meta.monto_meta),
        float(meta.acumulado),
        float(meta.aporte_mensual_planeado),
        meta.fecha_limite.isoformat() if meta.fecha_limite else None,
        str(meta.prioridad),
        meta.vehiculo.strip(),
        meta.notas.strip(),
    ]
