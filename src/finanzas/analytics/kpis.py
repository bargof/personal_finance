from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from finanzas.domain.entities import ReglasFinancieras

# ═══════════════════════════════════════════════════════════
# Indicadores del tablero
#
# El score replica la fórmula del Excel y reparte 100 puntos
# entre cuatro componentes. Cada uno se calcula por separado
# para poder explicarle al usuario de dónde salió su número.
# ═══════════════════════════════════════════════════════════

#: Puntos máximos por componente del score.
PESO_AHORRO = 35
PESO_PRESUPUESTO = 25
PESO_FONDO = 25
PESO_DESEOS = 15


@dataclass(slots=True)
class ResumenPeriodo:
    """Cifras base de un periodo, ya calculadas desde los movimientos."""

    periodo: str
    ingresos: float = 0.0
    gastos: float = 0.0
    ahorro_inversion: float = 0.0
    gasto_esencial: float = 0.0
    presupuesto_total: float = 0.0
    activos_liquidos: float = 0.0
    gasto_esencial_promedio_3m: float = 0.0
    suscripciones_mensuales: float = 0.0
    patrimonio_neto: float = 0.0
    movimientos: int = 0

    @property
    def disponible(self) -> float:
        """Lo que queda tras cubrir gastos y apartar ahorro e inversión."""
        return self.ingresos - self.gastos - self.ahorro_inversion

    @property
    def tasa_ahorro(self) -> float:
        """Proporción del ingreso que se convirtió en ahorro o inversión."""
        if self.ingresos == 0:
            return 0.0
        return self.ahorro_inversion / self.ingresos

    @property
    def presupuesto_utilizado(self) -> float:
        """Gasto del periodo contra el presupuesto activo total."""
        if self.presupuesto_total == 0:
            return 0.0
        return self.gastos / self.presupuesto_total

    @property
    def pct_esencial(self) -> float:
        """Proporción del gasto que fue indispensable."""
        if self.gastos == 0:
            return 0.0
        return self.gasto_esencial / self.gastos

    @property
    def pct_deseos(self) -> float:
        """Proporción del gasto que fue discrecional."""
        if self.gastos == 0:
            return 0.0
        return 1 - self.pct_esencial

    @property
    def meses_fondo_emergencia(self) -> float:
        """Meses de gasto esencial que cubren los activos líquidos."""
        if self.gasto_esencial_promedio_3m == 0:
            return 0.0
        return self.activos_liquidos / self.gasto_esencial_promedio_3m


@dataclass(slots=True)
class ScoreFinanciero:
    """Score sobre 100 y el desglose que lo explica."""

    ahorro: float
    presupuesto: float
    fondo_emergencia: float
    deseos: float

    @property
    def total(self) -> int:
        """Puntaje redondeado sobre 100."""
        return round(
            self.ahorro + self.presupuesto + self.fondo_emergencia + self.deseos
        )

    @property
    def desglose(self) -> pd.DataFrame:
        """Tabla del score por componente, con su máximo alcanzable."""
        return pd.DataFrame(
            {
                "componente": [
                    "Ahorro e inversión",
                    "Disciplina de presupuesto",
                    "Fondo de emergencia",
                    "Control de deseos",
                ],
                "puntos": [
                    round(self.ahorro, 1),
                    round(self.presupuesto, 1),
                    round(self.fondo_emergencia, 1),
                    round(self.deseos, 1),
                ],
                "maximo": [
                    PESO_AHORRO,
                    PESO_PRESUPUESTO,
                    PESO_FONDO,
                    PESO_DESEOS,
                ],
            }
        )


def calcular_score(
    resumen: ResumenPeriodo, reglas: ReglasFinancieras
) -> ScoreFinanciero:
    """
    Calcula el score financiero del periodo.

    Reparte 100 puntos entre cuatro señales: cuánto se ahorró frente a la
    meta, qué tan dentro del presupuesto se gastó, cuántos meses cubre el
    fondo de emergencia y qué tanto se contuvo el gasto discrecional.

    Parameters
    ----------
    resumen : ResumenPeriodo
        Cifras base del periodo.
    reglas : ReglasFinancieras
        Metas y umbrales configurados por el usuario.

    Returns
    -------
    ScoreFinanciero
        Puntaje total y su desglose por componente.
    """
    # ── Ahorro: qué tan cerca se quedó la tasa de la meta ──
    if resumen.ingresos == 0 or reglas.meta_ahorro_inversion == 0:
        punto_ahorro = 0.0
    else:
        avance = resumen.tasa_ahorro / reglas.meta_ahorro_inversion
        punto_ahorro = min(PESO_AHORRO, avance * PESO_AHORRO)

    # ── Presupuesto: se penaliza gastar por encima del plan ──
    utilizado = resumen.presupuesto_utilizado
    if utilizado == 0:
        punto_presupuesto = 0.0
    else:
        punto_presupuesto = min(1.0, 1 / utilizado) * PESO_PRESUPUESTO

    # ── Fondo de emergencia: meses cubiertos frente al objetivo ──
    if reglas.meses_fondo_emergencia == 0:
        punto_fondo = 0.0
    else:
        cobertura = resumen.meses_fondo_emergencia / reglas.meses_fondo_emergencia
        punto_fondo = min(1.0, cobertura) * PESO_FONDO

    # ── Deseos: puntaje completo mientras no rebasen el techo ──
    #
    # Un mes sin gasto no gana estos puntos: no hay disciplina que premiar
    # donde no hubo decisión de gasto.
    deseos = resumen.pct_deseos
    if resumen.gastos == 0:
        punto_deseos = 0.0
    elif deseos <= reglas.max_deseos or reglas.max_deseos >= 1:
        punto_deseos = float(PESO_DESEOS)
    else:
        exceso = (deseos - reglas.max_deseos) / (1 - reglas.max_deseos)
        punto_deseos = max(0.0, PESO_DESEOS * (1 - exceso))

    return ScoreFinanciero(
        ahorro=punto_ahorro,
        presupuesto=punto_presupuesto,
        fondo_emergencia=punto_fondo,
        deseos=punto_deseos,
    )


def siguiente_mejor_accion(
    resumen: ResumenPeriodo,
    reglas: ReglasFinancieras,
    categorias_excedidas: int,
) -> str:
    """
    Sugiere la acción con mayor impacto según el estado del periodo.

    Es una regla determinista y ordenada por urgencia. Cuando se conecten
    los modelos, este es el punto natural para reemplazarla por una
    recomendación generada.
    """
    if resumen.ingresos > 0 and resumen.disponible < 0:
        return "Estás gastando más de lo que ingresa: revisa las categorías excedidas."

    if resumen.meses_fondo_emergencia < reglas.meses_fondo_emergencia:
        faltan = reglas.meses_fondo_emergencia - resumen.meses_fondo_emergencia
        return (
            "Prioriza el fondo de emergencia: te faltan "
            f"{faltan:.1f} meses de cobertura."
        )

    if categorias_excedidas > 0:
        return (
            f"Tienes {categorias_excedidas} categorías excedidas: "
            "ajusta el presupuesto o el gasto."
        )

    if resumen.tasa_ahorro < reglas.meta_ahorro_inversion:
        brecha = (reglas.meta_ahorro_inversion - resumen.tasa_ahorro) * resumen.ingresos
        return f"Sube tu ahorro del mes en {brecha:,.0f} para alcanzar tu meta."

    if resumen.pct_deseos > reglas.max_deseos:
        return "El gasto discrecional rebasó tu techo: revisa suscripciones y salidas."

    return "Vas en ruta: considera subir la meta de ahorro o adelantar una meta."
