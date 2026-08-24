from __future__ import annotations

import pandas as pd

from finanzas.data.repositories.catalogos_repository import CatalogosRepository
from finanzas.data.repositories.presupuesto_repository import PresupuestoRepository
from finanzas.domain.entities import LineaPresupuesto

# ═══════════════════════════════════════════════════════════
# Presupuesto
#
# El servicio arma las entidades `LineaPresupuesto` para que
# las reglas (presupuesto activo, disponible, semáforo) vivan
# en el dominio y no se repitan en cada página.
# ═══════════════════════════════════════════════════════════


class PresupuestoService:
    """Casos de uso sobre el presupuesto mensual por categoría."""

    def __init__(
        self,
        repositorio: PresupuestoRepository | None = None,
        catalogos: CatalogosRepository | None = None,
    ) -> None:
        self._repo = repositorio or PresupuestoRepository()
        self._catalogos = catalogos or CatalogosRepository()

    def lineas(self, periodo: str) -> list[LineaPresupuesto]:
        """Devuelve las líneas de presupuesto del periodo como entidades."""
        tablero = self._repo.tablero(periodo)

        return [
            LineaPresupuesto(
                periodo=periodo,
                categoria_id=int(fila.categoria_id),
                categoria=fila.categoria,
                monto_manual=float(fila.monto_manual),
                pct_recorte=float(fila.pct_recorte),
                promedio_3m=float(fila.promedio_3m),
                gasto_del_mes=float(fila.gasto_del_mes),
            )
            for fila in tablero.itertuples()
        ]

    def tablero(self, periodo: str, umbral_alerta: float = 0.9) -> pd.DataFrame:
        """
        Devuelve el tablero de presupuesto listo para mostrar.

        Parameters
        ----------
        periodo : str
            Periodo en formato YYYY-MM.
        umbral_alerta : float
            % de uso a partir del cual una categoría pasa a 'Atención'.

        Returns
        -------
        pandas.DataFrame
            Una fila por categoría de gasto, con presupuesto activo,
            disponible, % usado y semáforo.
        """
        lineas = self.lineas(periodo)
        if not lineas:
            return pd.DataFrame(
                columns=[
                    "categoria_id",
                    "categoria",
                    "monto_manual",
                    "pct_recorte",
                    "promedio_3m",
                    "presupuesto_activo",
                    "gasto_del_mes",
                    "disponible",
                    "pct_usado",
                    "estado",
                ]
            )

        return pd.DataFrame(
            [
                {
                    "categoria_id": linea.categoria_id,
                    "categoria": linea.categoria,
                    "monto_manual": linea.monto_manual,
                    "pct_recorte": linea.pct_recorte,
                    "promedio_3m": linea.promedio_3m,
                    "presupuesto_activo": linea.presupuesto_activo,
                    "gasto_del_mes": linea.gasto_del_mes,
                    "disponible": linea.disponible,
                    "pct_usado": linea.pct_usado,
                    "estado": str(linea.estado(umbral_alerta)),
                }
                for linea in lineas
            ]
        )

    def presupuesto_total(self, periodo: str) -> float:
        """Suma del presupuesto activo de todas las categorías del periodo."""
        return sum(linea.presupuesto_activo for linea in self.lineas(periodo))

    def categorias_excedidas(self, periodo: str, umbral_alerta: float = 0.9) -> int:
        """Cuenta las categorías que ya rebasaron su presupuesto."""
        tablero = self.tablero(periodo, umbral_alerta)
        if tablero.empty:
            return 0
        return int((tablero["estado"] == "Excedido").sum())

    def guardar(self, periodo: str, lineas: pd.DataFrame) -> int:
        """
        Guarda las ediciones del tablero.

        Sólo se persisten `monto_manual` y `pct_recorte`: lo demás se
        recalcula desde los movimientos en cada lectura.
        """
        columnas = ["categoria_id", "monto_manual", "pct_recorte"]
        faltantes = [columna for columna in columnas if columna not in lineas.columns]
        if faltantes:
            raise ValueError(f"Faltan columnas en el presupuesto: {faltantes}")

        editables = lineas[columnas].copy()
        editables["monto_manual"] = (
            pd.to_numeric(editables["monto_manual"], errors="coerce")
            .fillna(0.0)
            .clip(lower=0)
        )
        editables["pct_recorte"] = (
            pd.to_numeric(editables["pct_recorte"], errors="coerce")
            .fillna(0.0)
            .clip(lower=0, upper=1)
        )

        return self._repo.guardar_lineas(periodo, editables)

    def copiar_desde(self, origen: str, destino: str) -> int:
        """Copia el presupuesto de un periodo a otro."""
        if origen == destino:
            raise ValueError("El periodo de origen y el de destino son el mismo.")

        return self._repo.copiar_periodo(origen, destino)

    def periodos_con_presupuesto(self) -> list[str]:
        """Devuelve los periodos que ya tienen presupuesto capturado."""
        return self._repo.periodos_con_presupuesto()
