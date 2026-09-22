from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

# ═══════════════════════════════════════════════════════════
# Saldo de una cuenta a una fecha
#
# El saldo no se captura: se deduce de los movimientos a partir
# de un saldo verificado —un «ancla»— que es lo único que se
# captura. Un ancla dice «el día D esta cuenta cerró con S»;
# desde ahí se suma hacia adelante o se resta hacia atrás.
#
# Restar hacia atrás es lo que permite cargar estados de cuenta
# viejos sin saber cuánto había al principio: basta con anclar
# el saldo de hoy y el sistema deduce cómo se llegó hasta ahí.
#
# Los importes van con el signo del libro: en una cuenta de
# crédito el saldo es negativo cuando se debe.
# ═══════════════════════════════════════════════════════════


@dataclass(slots=True, frozen=True)
class Ancla:
    """Un saldo verificado de una cuenta al cierre de un día."""

    fecha: date
    saldo: float
    origen: str = "Manual"
    id: int | None = None


@dataclass(slots=True, frozen=True)
class Flujo:
    """Lo que un movimiento ya pagado le hizo a la cuenta ese día."""

    fecha: date
    monto: float


class Sentido(StrEnum):
    """Desde dónde se dedujo el saldo."""

    ADELANTE = "Desde un saldo anterior"
    ATRAS = "Desde un saldo posterior"
    SIN_ANCLA = "Sin saldo verificado"


@dataclass(slots=True)
class SaldoDeducido:
    """El saldo de una cuenta a una fecha, y de dónde salió."""

    fecha: date
    saldo: float
    sentido: Sentido
    ancla: Ancla | None = None
    movimientos: int = 0

    @property
    def verificado(self) -> bool:
        """Indica si el saldo descansa en un dato real y no en un cero."""
        return self.ancla is not None


@dataclass(slots=True)
class Descuadre:
    """
    Dos anclas consecutivas que los movimientos no conectan.

    Es la señal de que falta un estado de cuenta por importar, o de que
    algo se registró en la cuenta equivocada.
    """

    desde: Ancla
    hasta: Ancla
    esperado: float
    diferencia: float
    movimientos: int = 0

    @property
    def faltan(self) -> float:
        """Cuánto dinero entró (positivo) o salió sin registrarse."""
        return self.diferencia


@dataclass(slots=True)
class Libro:
    """Los flujos y las anclas de una cuenta, listos para deducir saldos."""

    flujos: list[Flujo] = field(default_factory=list)
    anclas: list[Ancla] = field(default_factory=list)

    def saldo_a(self, fecha: date) -> SaldoDeducido:
        """
        Deduce el saldo al cierre de `fecha`.

        Manda el ancla más cercana: la última anterior o igual a la fecha,
        y si no hay, la primera posterior. Sin anclas se suma desde cero y
        se dice que el número no está verificado.
        """
        anteriores = [a for a in self.anclas if a.fecha <= fecha]
        if anteriores:
            ancla = max(anteriores, key=lambda a: a.fecha)
            entre = [f for f in self.flujos if ancla.fecha < f.fecha <= fecha]
            return SaldoDeducido(
                fecha=fecha,
                saldo=round(ancla.saldo + sum(f.monto for f in entre), 2),
                sentido=Sentido.ADELANTE,
                ancla=ancla,
                movimientos=len(entre),
            )

        posteriores = [a for a in self.anclas if a.fecha > fecha]
        if posteriores:
            ancla = min(posteriores, key=lambda a: a.fecha)
            entre = [f for f in self.flujos if fecha < f.fecha <= ancla.fecha]
            return SaldoDeducido(
                fecha=fecha,
                saldo=round(ancla.saldo - sum(f.monto for f in entre), 2),
                sentido=Sentido.ATRAS,
                ancla=ancla,
                movimientos=len(entre),
            )

        hasta = [f for f in self.flujos if f.fecha <= fecha]
        return SaldoDeducido(
            fecha=fecha,
            saldo=round(sum(f.monto for f in hasta), 2),
            sentido=Sentido.SIN_ANCLA,
            movimientos=len(hasta),
        )

    def descuadres(self, tolerancia: float = 0.01) -> list[Descuadre]:
        """
        Compara cada par de anclas consecutivas con los movimientos entre ellas.

        Entre dos saldos verificados, los movimientos tienen que explicar
        exactamente la diferencia. Cuando no, o falta un movimiento o sobra
        uno, y la diferencia dice de cuánto.
        """
        ordenadas = sorted(self.anclas, key=lambda a: a.fecha)
        hallazgos: list[Descuadre] = []

        for anterior, siguiente in zip(ordenadas, ordenadas[1:]):
            entre = [
                f for f in self.flujos if anterior.fecha < f.fecha <= siguiente.fecha
            ]
            esperado = round(anterior.saldo + sum(f.monto for f in entre), 2)
            diferencia = round(siguiente.saldo - esperado, 2)
            if abs(diferencia) > tolerancia:
                hallazgos.append(
                    Descuadre(
                        desde=anterior,
                        hasta=siguiente,
                        esperado=esperado,
                        diferencia=diferencia,
                        movimientos=len(entre),
                    )
                )

        return hallazgos
