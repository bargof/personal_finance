from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from finanzas.analytics import aggregations as agg
from finanzas.analytics.kpis import (
    ResumenPeriodo,
    ScoreFinanciero,
    calcular_score,
    siguiente_mejor_accion,
)
from finanzas.application.services.patrimonio_service import PatrimonioService
from finanzas.application.services.presupuesto_service import PresupuestoService
from finanzas.data.repositories.catalogos_repository import CatalogosRepository
from finanzas.data.repositories.movimientos_repository import MovimientosRepository
from finanzas.data.repositories.patrimonio_repository import PatrimonioRepository
from finanzas.data.repositories.suscripciones_repository import SuscripcionesRepository
from finanzas.data.schemas import validar_movimientos, validar_resumen_mensual
from finanzas.domain.entities import ReglasFinancieras

# ═══════════════════════════════════════════════════════════
# Analítica del periodo
#
# Este servicio es el único que sabe combinar movimientos,
# presupuesto, patrimonio y suscripciones. Las páginas piden
# un `TableroPeriodo` y se limitan a dibujarlo.
# ═══════════════════════════════════════════════════════════


@dataclass(slots=True)
class TableroPeriodo:
    """Todo lo que el dashboard necesita de un periodo, ya calculado."""

    periodo: str
    reglas: ReglasFinancieras
    resumen: ResumenPeriodo
    score: ScoreFinanciero
    movimientos: pd.DataFrame
    tendencia: pd.DataFrame
    presupuesto: pd.DataFrame
    por_categoria: pd.DataFrame
    fugas: dict[str, float]
    categorias_excedidas: int
    accion_sugerida: str

    #: Cuántos meses con datos abarca. Uno en un mes concreto; en el
    #: histórico, los que haya, y lo comparable con un presupuesto mensual
    #: es entonces el promedio.
    meses: int = 1

    @property
    def etiqueta(self) -> str:
        """Nombre legible del periodo, por ejemplo 'agosto 2026'."""
        return agg.etiqueta_periodo(self.periodo)

    @property
    def es_historico(self) -> bool:
        """Indica si abarca todo lo registrado y no un mes."""
        return agg.es_historico(self.periodo)

    @property
    def hay_datos(self) -> bool:
        """Indica si el periodo tiene al menos un movimiento."""
        return not self.movimientos.empty

    @property
    def categoria_mayor_gasto(self) -> tuple[str, float] | None:
        """Categoría con más gasto del periodo y su monto."""
        if self.por_categoria.empty:
            return None
        fila = self.por_categoria.iloc[0]
        return str(fila["categoria"]), float(fila["gasto"])


class AnalyticsService:
    """Construye los indicadores y cortes que consumen las páginas."""

    def __init__(
        self,
        movimientos: MovimientosRepository | None = None,
        presupuesto: PresupuestoService | None = None,
        patrimonio: PatrimonioRepository | None = None,
        suscripciones: SuscripcionesRepository | None = None,
        catalogos: CatalogosRepository | None = None,
    ) -> None:
        self._movimientos = movimientos or MovimientosRepository()
        self._presupuesto = presupuesto or PresupuestoService()
        # El balance se deduce, no se lee: el servicio de patrimonio es
        # quien sabe sumar libros y anclas.
        self._patrimonio = PatrimonioService(patrimonio or PatrimonioRepository())
        self._suscripciones = suscripciones or SuscripcionesRepository()
        self._catalogos = catalogos or CatalogosRepository()

    # ── Tablero completo ─────────────────────────────────

    def tablero(self, periodo: str) -> TableroPeriodo:
        """
        Arma el tablero de un periodo.

        Parameters
        ----------
        periodo : str
            Periodo en formato YYYY-MM, o `HISTORICO` para todo lo
            registrado. En el histórico las cifras son totales; lo que se
            compara contra el presupuesto —que es mensual— es el promedio
            por mes, y la tendencia abarca desde el primer mes con datos.

        Returns
        -------
        TableroPeriodo
            Resumen, score, tendencia, presupuesto y cortes de gasto.

        Raises
        ------
        pandera.errors.SchemaError
            Si los datos que salen de la base rompen su contrato. Se valida
            aquí, una vez por tablero, y no en cada lectura: es el punto
            donde los datos pasan de la base a los cálculos.
        """
        reglas = self._catalogos.leer_reglas()
        historico = agg.es_historico(periodo)
        referencia = agg.periodo_de(date.today()) if historico else periodo

        movimientos = (
            self._movimientos.listar()
            if historico
            else self._movimientos.del_periodo(periodo)
        )
        if not movimientos.empty:
            movimientos = validar_movimientos(movimientos)

        resumen_mensual = self._movimientos.resumen_mensual()
        if not resumen_mensual.empty:
            resumen_mensual = validar_resumen_mensual(resumen_mensual)

        meses = 1
        ventana = 12
        if historico:
            meses = (
                max(1, int(movimientos["periodo"].nunique()))
                if not movimientos.empty
                else 1
            )
            if not resumen_mensual.empty:
                primero = str(resumen_mensual["periodo"].min())
                ventana = max(12, agg.meses_entre(primero, referencia) + 1)

        if historico:
            tablero_presupuesto = self._presupuesto.tablero_historico(
                referencia, movimientos, meses, reglas.alerta_presupuesto
            )
        else:
            tablero_presupuesto = self._presupuesto.tablero(
                periodo, reglas.alerta_presupuesto
            )

        resumen = self._construir_resumen(
            periodo, movimientos, tablero_presupuesto, referencia, meses
        )
        score = calcular_score(resumen, reglas)

        excedidas = (
            int((tablero_presupuesto["estado"] == "Excedido").sum())
            if not tablero_presupuesto.empty
            else 0
        )

        return TableroPeriodo(
            periodo=periodo,
            reglas=reglas,
            resumen=resumen,
            score=score,
            movimientos=movimientos,
            tendencia=agg.tendencia_mensual(resumen_mensual, referencia, ventana),
            presupuesto=tablero_presupuesto,
            por_categoria=agg.gasto_por_categoria(movimientos),
            fugas=agg.fugas(movimientos, reglas.umbral_gasto_pequeno),
            categorias_excedidas=excedidas,
            accion_sugerida=siguiente_mejor_accion(resumen, reglas, excedidas),
            meses=meses,
        )

    # ── Cortes individuales ──────────────────────────────

    def mezcla(self, movimientos: pd.DataFrame, dimension: str) -> pd.DataFrame:
        """Reparte el gasto por necesidad, naturaleza o medio de pago."""
        return agg.mezcla_de_gasto(movimientos, dimension)

    def calendario(self, movimientos: pd.DataFrame, periodo: str) -> pd.DataFrame:
        """Devuelve el gasto de cada día del periodo."""
        return agg.calendario_de_gasto(movimientos, periodo)

    def por_subcategoria(self, movimientos: pd.DataFrame, top: int = 15):
        """Devuelve las subcategorías con más gasto."""
        return agg.gasto_por_subcategoria(movimientos, top)

    def por_cuenta(self, movimientos: pd.DataFrame) -> pd.DataFrame:
        """Devuelve entradas, salidas y flujo neto por cuenta."""
        return agg.flujo_por_cuenta(movimientos)

    def periodos_disponibles(self) -> list[str]:
        """Devuelve los periodos con movimientos registrados."""
        return self._movimientos.periodos_disponibles()

    # ── Construcción del resumen ─────────────────────────

    def _construir_resumen(
        self,
        periodo: str,
        movimientos: pd.DataFrame,
        presupuesto: pd.DataFrame,
        referencia: str | None = None,
        meses: int = 1,
    ) -> ResumenPeriodo:
        """
        Reúne las cifras base del periodo desde sus distintas fuentes.

        `referencia` es el mes contra el que se mira lo mensual (fondo de
        emergencia, presupuesto); en un mes concreto es él mismo. `meses`
        escala el presupuesto mensual al total del periodo, para que el
        % usado del histórico sea «gasto mensual promedio sobre presupuesto».
        """
        resumen = ResumenPeriodo(periodo=periodo)
        referencia = referencia or periodo

        if not movimientos.empty:
            resumen.ingresos = float(movimientos["ingreso_real"].sum())
            resumen.gastos = float(movimientos["gasto_real"].sum())
            # Ahorro neto: lo que entró al ahorro menos lo que salió de él.
            resumen.ahorro_inversion = float(
                movimientos["patrimonio_creado"].sum()
                - movimientos["ahorro_retirado"].sum()
            )
            resumen.movimientos = int(len(movimientos))

            esenciales = movimientos[movimientos["necesidad"] == "Esencial"]
            resumen.gasto_esencial = float(esenciales["gasto_real"].sum())

        if not presupuesto.empty:
            resumen.presupuesto_total = float(
                presupuesto["presupuesto_activo"].sum() * max(meses, 1)
            )

        resumen.activos_liquidos = self._patrimonio.activos_liquidos()
        resumen.patrimonio_neto = self._patrimonio.resumen()["patrimonio_neto"]
        resumen.suscripciones_mensuales = self._suscripciones.costo_mensual_total()
        resumen.gasto_esencial_promedio_3m = self._gasto_esencial_promedio(referencia)

        return resumen

    def _gasto_esencial_promedio(self, periodo: str, meses: int = 3) -> float:
        """
        Promedia el gasto esencial de los meses previos al periodo.

        Es el denominador del fondo de emergencia: cuánto cuesta un mes de
        vida sin gasto discrecional.
        """
        periodos = [
            agg.desplazar_periodo(periodo, -desfase) for desfase in range(1, meses + 1)
        ]

        total = 0.0
        for previo in periodos:
            movimientos = self._movimientos.del_periodo(previo)
            if movimientos.empty:
                continue
            esenciales = movimientos[movimientos["necesidad"] == "Esencial"]
            total += float(esenciales["gasto_real"].sum())

        return total / meses if meses else 0.0
