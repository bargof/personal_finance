from __future__ import annotations

import calendar
from dataclasses import dataclass, field, replace
from datetime import date
from enum import StrEnum

# ═══════════════════════════════════════════════════════════
# Plan de pagos: qué pagar primero cuando no alcanza
#
# Cada obligación dice si se puede posponer, hasta cuándo y
# cuánto cuesta hacerlo. Con eso se reparte el dinero de cada
# mes en este orden:
#
# 1. Lo que no se puede posponer —la renta, lo atrasado, lo que
#    llegó a su fecha tope—, por fecha.
# 2. Lo que cuesta posponer, de la tasa más alta a la más baja:
#    una tarjeta antes que un adeudo sin intereses.
# 3. Lo que se puede posponer gratis, lo de tope más cercano
#    primero.
#
# Posponer es dejarlo para el mes siguiente, no para siempre: lo que
# no se paga llega al otro mes como algo que toca pagar —con sus
# intereses, si genera— y la sugerencia ya no lo vuelve a posponer.
# Si tampoco alcanza, se ve como faltante; volver a posponerlo es
# una decisión que se toma a mano en ese mes. Así cada concepto
# aparece una sola vez por mes, con lo que se debe de él.
#
# Cada mes tiene sus propias decisiones a mano, y mandan sobre la
# sugerencia de ese mes.
# ═══════════════════════════════════════════════════════════


class Decision(StrEnum):
    """Lo que el usuario decidió hacer con una obligación este mes."""

    PAGAR = "pagar"
    PARCIAL = "parcial"
    POSPONER = "posponer"


@dataclass(frozen=True, slots=True)
class Obligacion:
    """Algo que hay que pagar, con las reglas para decidir sobre ello."""

    clave: str
    concepto: str
    origen: str
    monto: float
    vence: date | None = None
    posponible: bool = False
    #: Hasta cuándo se puede posponer; en ese mes ya hay que pagarlo.
    tope: date | None = None
    #: Lo que cuesta al año dejarlo sin pagar, en proporción (0.6 = 60 %).
    tasa_anual: float = 0.0
    #: Una tarjeta genera intereses aunque no se haya capturado su tasa.
    genera_interes: bool = False
    #: Si se puede pagar una parte: una deuda sí, la renta no.
    parcial: bool = True
    #: Quedó sin pagar un mes anterior sin poder posponerse.
    atrasada: bool = False
    #: Viene de un mes anterior sin pagar: pospuesta o sin dinero. Toca
    #: pagarla; posponerla otra vez sólo se hace a mano.
    arrastrada: bool = False

    def puede_posponerse(self, fin_de_mes: date) -> bool:
        """Si este mes todavía se puede dejar para el siguiente."""
        return (
            self.posponible
            and not self.atrasada
            and not (self.tope is not None and self.tope <= fin_de_mes)
        )

    def se_pospone_sola(self, fin_de_mes: date) -> bool:
        """Si la sugerencia puede posponerla: sólo si no viene ya pospuesta."""
        return self.puede_posponerse(fin_de_mes) and not self.arrastrada

    def obligatoria(self, fin_de_mes: date) -> bool:
        """Si la sugerencia la paga antes que lo que puede esperar."""
        return not self.se_pospone_sola(fin_de_mes)

    @property
    def costo_mensual(self) -> float:
        """Lo que cuesta posponerla un mes."""
        return _interes(self, self.monto)


@dataclass(frozen=True, slots=True)
class Resolucion:
    """Qué pasa con una obligación en un mes del plan."""

    obligacion: Obligacion
    pagado: float
    pospuesto: float
    falta: float
    razon: str
    manual: bool = False


@dataclass(slots=True)
class MesDelPlan:
    """Un mes del plan: lo que había, lo que se decidió y lo que quedó."""

    mes: date
    efectivo_inicial: float
    ingresos: float
    #: Lo que lo pospuesto el mes anterior generó de intereses al llegar.
    intereses: float = 0.0
    resoluciones: list[Resolucion] = field(default_factory=list)

    @property
    def disponible(self) -> float:
        return round(self.efectivo_inicial + self.ingresos, 2)

    @property
    def obligaciones(self) -> float:
        return round(sum(r.obligacion.monto for r in self.resoluciones), 2)

    @property
    def pagado(self) -> float:
        return round(sum(r.pagado for r in self.resoluciones), 2)

    @property
    def pospuesto(self) -> float:
        return round(sum(r.pospuesto for r in self.resoluciones), 2)

    @property
    def falta(self) -> float:
        return round(sum(r.falta for r in self.resoluciones), 2)

    @property
    def a_pagar(self) -> float:
        """Lo que se decidió o toca pagar este mes, alcance o no."""
        return round(self.pagado + self.falta, 2)

    @property
    def balance(self) -> float:
        """Lo que se tiene menos lo que toca pagar; negativo es lo que falta."""
        return round(self.disponible - self.a_pagar, 2)

    @property
    def queda(self) -> float:
        return round(max(self.disponible - self.pagado, 0.0), 2)


@dataclass(frozen=True, slots=True)
class EntradaMes:
    """Lo que un mes trae de nuevo: sus ingresos y sus obligaciones."""

    mes: date
    ingresos: float
    obligaciones: tuple[Obligacion, ...] = ()


@dataclass(frozen=True, slots=True)
class Ajuste:
    """Una decisión a mano sobre una obligación en un mes."""

    decision: Decision
    monto: float | None = None


def fin_de_mes(mes: date) -> date:
    return date(mes.year, mes.month, calendar.monthrange(mes.year, mes.month)[1])


def mes_siguiente(mes: date) -> date:
    indice = mes.year * 12 + mes.month
    return date(indice // 12, indice % 12 + 1, 1)


def _prioridad(obligacion: Obligacion, fin: date) -> tuple:
    """Menor va primero."""
    return (
        not obligacion.obligatoria(fin),
        -obligacion.tasa_anual,
        not obligacion.genera_interes,
        obligacion.tope or date.max,
        obligacion.vence or date.max,
        obligacion.concepto,
    )


def _razon(obligacion: Obligacion, fin: date) -> str:
    """Por qué va en su lugar, dicho para el usuario."""
    if obligacion.atrasada:
        return "Quedó sin pagar el mes anterior y no se puede posponer"
    if not obligacion.posponible:
        return "No se puede posponer"
    if obligacion.tope is not None and obligacion.tope <= fin:
        return f"Llegó a su fecha tope ({obligacion.tope:%d/%m/%Y})"
    if obligacion.arrastrada:
        return "Viene del mes anterior: ahora toca pagarlo"
    if obligacion.tasa_anual > 0:
        return f"Posponerlo cuesta {obligacion.tasa_anual:.0%} al año"
    if obligacion.genera_interes:
        return "Genera intereses (captura su tasa para medirlo)"
    if obligacion.tope is not None:
        return f"Se puede posponer sin costo hasta {obligacion.tope:%d/%m/%Y}"
    return "Se puede posponer sin costo"


def resolver_mes(
    mes: date,
    efectivo_inicial: float,
    ingresos: float,
    obligaciones: list[Obligacion],
    ajustes: dict[str, Ajuste] | None = None,
) -> MesDelPlan:
    """
    Reparte el dinero de un mes entre sus obligaciones.

    Primero se aplican las decisiones a mano; con lo que queda, se paga
    en orden de prioridad. Lo que la sugerencia puede posponer y no
    alcanza es `pospuesto`; lo demás que no alcanza, `falta`.
    """
    ajustes = ajustes or {}
    fin = fin_de_mes(mes)
    plan = MesDelPlan(mes=mes, efectivo_inicial=efectivo_inicial, ingresos=ingresos)
    efectivo = efectivo_inicial + ingresos

    # Lo que se decidió pagar a mano va antes que nada: es un compromiso.
    def orden(o: Obligacion) -> tuple:
        ajuste = ajustes.get(o.clave)
        a_mano = ajuste is not None and ajuste.decision != Decision.POSPONER
        return (not a_mano, *_prioridad(o, fin))

    resoluciones: dict[str, Resolucion] = {}
    for obligacion in sorted(obligaciones, key=orden):
        ajuste = ajustes.get(obligacion.clave)
        puede = obligacion.puede_posponerse(fin)
        sola = obligacion.se_pospone_sola(fin)
        razon = _razon(obligacion, fin)

        # Posponer a mano sólo si todavía se puede: la renta no, y lo que
        # llegó a su tope tampoco. Si no se puede, se trata como si nada.
        if ajuste is not None and ajuste.decision == Decision.POSPONER:
            if puede:
                resoluciones[obligacion.clave] = Resolucion(
                    obligacion, 0.0, obligacion.monto, 0.0, "Decidiste posponerlo", True
                )
                continue
            ajuste = None

        a_mano = ajuste is not None
        objetivo = obligacion.monto
        if a_mano and ajuste.decision == Decision.PARCIAL:
            objetivo = min(max(float(ajuste.monto or 0.0), 0.0), obligacion.monto)

        if a_mano or not sola or obligacion.parcial:
            pagado = min(objetivo, max(efectivo, 0.0))
        else:
            # La renta no se paga a medias: o alcanza entera o se pospone.
            pagado = objetivo if efectivo >= objetivo else 0.0
        pagado = round(pagado, 2)
        efectivo -= pagado

        if a_mano:
            # Lo que se decidió pagar y no alcanzó, falta. Lo que se dejó
            # fuera a propósito al pagar una parte pasa al mes siguiente,
            # aunque sea algo que no se pospone: fue una decisión, no un
            # faltante, y así «Vas a pagar» dice lo que se decidió pagar.
            falta = round(objetivo - pagado, 2)
            pospuesto = round(obligacion.monto - objetivo, 2)
            razon = (
                "Decidiste pagar una parte"
                if ajuste.decision == Decision.PARCIAL
                else "Decidiste pagarlo"
            )
        elif sola:
            pospuesto, falta = round(obligacion.monto - pagado, 2), 0.0
        else:
            pospuesto, falta = 0.0, round(obligacion.monto - pagado, 2)

        resoluciones[obligacion.clave] = Resolucion(
            obligacion, pagado, pospuesto, falta, razon, a_mano
        )

    # En el orden en que se pagaron no se lee bien: se presentan por
    # prioridad, que es el orden en que conviene pensarlas.
    plan.resoluciones = [
        resoluciones[o.clave]
        for o in sorted(obligaciones, key=lambda o: _prioridad(o, fin))
    ]
    return plan


def proyectar(
    efectivo_hoy: float,
    meses: list[EntradaMes],
    ajustes: dict[date, dict[str, Ajuste]] | None = None,
) -> list[MesDelPlan]:
    """
    Resuelve mes tras mes, llevando lo que queda y lo que no se pagó.

    Lo que no se paga —pospuesto o faltante— llega al mes siguiente una
    sola vez, con la misma clave y con sus intereses, como algo que toca
    pagar. Lo que sobra un mes es el efectivo con el que empieza el
    siguiente. `ajustes` trae las decisiones a mano de cada mes.
    """
    ajustes = ajustes or {}
    resultado: list[MesDelPlan] = []
    efectivo = efectivo_hoy
    arrastre: list[Obligacion] = []
    intereses = 0.0

    for entrada in meses:
        plan = resolver_mes(
            entrada.mes,
            efectivo,
            entrada.ingresos,
            [*arrastre, *entrada.obligaciones],
            ajustes.get(entrada.mes),
        )
        plan.intereses = intereses
        resultado.append(plan)

        efectivo = plan.queda
        arrastre = []
        intereses = 0.0
        # Lo pospuesto llega al mes siguiente tal cual, con sus intereses.
        for resolucion in plan.resoluciones:
            if resolucion.pospuesto <= 0:
                continue
            base = resolucion.obligacion
            interes = _interes(base, resolucion.pospuesto)
            intereses += interes
            arrastre.append(
                replace(
                    base,
                    monto=round(resolucion.pospuesto + interes, 2),
                    arrastrada=True,
                )
            )
        # Lo que no alcanzó se cubre con una deuda nueva —como pedirlo
        # prestado—, que se paga el mes siguiente con las mismas decisiones
        # que cualquier otra cosa.
        if plan.falta > 0:
            arrastre.append(faltante_de(plan))

    return resultado


#: Así se llama el origen de la deuda que cubre lo que no alcanzó.
ORIGEN_FALTANTE = "Deuda por faltante"


def faltante_de(plan: MesDelPlan) -> Obligacion:
    """
    La deuda nueva que cubre lo que no alcanzó en un mes.

    Llega al mes siguiente como algo que toca pagar; se puede pagar en
    partes o volver a posponer, sin fecha tope ni intereses: lo que
    cueste pedirlo prestado no se sabe.
    """
    return Obligacion(
        clave=f"faltante:{plan.mes:%Y-%m}",
        concepto=f"Faltante de {_NOMBRE_MES[plan.mes.month - 1]}",
        origen=ORIGEN_FALTANTE,
        monto=plan.falta,
        vence=None,
        posponible=True,
        parcial=True,
        arrastrada=True,
    )


_NOMBRE_MES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def _interes(obligacion: Obligacion, monto: float) -> float:
    return round(monto * obligacion.tasa_anual / 12, 2)
