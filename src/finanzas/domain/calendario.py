from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

from finanzas.domain.enums import FrecuenciaCobro

# ═══════════════════════════════════════════════════════════
# Calendario de cargos fijos
#
# Un cargo fijo guarda una sola fecha —el próximo cobro— y su
# frecuencia. De ahí sale cuándo toca en cualquier mes, hacia
# adelante o hacia atrás, sin guardar cada ocurrencia.
# ═══════════════════════════════════════════════════════════

#: Meses entre un cobro y el siguiente, por frecuencia.
MESES_ENTRE_COBROS: dict[str, int] = {
    FrecuenciaCobro.MENSUAL: 1,
    FrecuenciaCobro.BIMESTRAL: 2,
    FrecuenciaCobro.TRIMESTRAL: 3,
    FrecuenciaCobro.SEMESTRAL: 6,
    FrecuenciaCobro.ANUAL: 12,
}


def cobro_en_mes(referencia: date, frecuencia: str, anio: int, mes: int) -> date | None:
    """
    Devuelve cuándo toca el cargo en ese mes, o None si ese mes no toca.

    `referencia` es cualquier cobro conocido, normalmente el próximo. El
    día se conserva, salvo en meses más cortos: un cargo del 31 cae el
    último día de febrero.
    """
    paso = MESES_ENTRE_COBROS.get(str(frecuencia), 1)
    distancia = (anio - referencia.year) * 12 + (mes - referencia.month)
    if distancia % paso:
        return None

    ultimo = calendar.monthrange(anio, mes)[1]
    return date(anio, mes, min(referencia.day, ultimo))


# ═══════════════════════════════════════════════════════════
# Ciclo de pago de una deuda
#
# Una tarjeta corta un día fijo y se paga otro, también fijo;
# un préstamo se paga un día fijo. De esos días sale, para
# cualquier fecha, en qué ciclo se está: desde cuándo cuentan
# los abonos y para cuándo hay que pagar.
# ═══════════════════════════════════════════════════════════


def _dia_en(anio: int, mes: int, dia: int) -> date:
    """El día `dia` de ese mes, o el último si el mes es más corto."""
    return date(anio, mes, min(dia, calendar.monthrange(anio, mes)[1]))


def _mes_vecino(fecha: date, salto: int) -> tuple[int, int]:
    indice = fecha.year * 12 + fecha.month - 1 + salto
    return indice // 12, indice % 12 + 1


def ultimo_dia(dia: int, hasta: date, incluido: bool = True) -> date:
    """La fecha más reciente con ese día del mes, en o antes de `hasta`."""
    candidata = _dia_en(hasta.year, hasta.month, dia)
    if candidata < hasta or (incluido and candidata == hasta):
        return candidata
    return _dia_en(*_mes_vecino(hasta, -1), dia)


def siguiente_dia(dia: int, despues_de: date) -> date:
    """La primera fecha con ese día del mes, estrictamente después."""
    candidata = _dia_en(despues_de.year, despues_de.month, dia)
    if candidata > despues_de:
        return candidata
    return _dia_en(*_mes_vecino(despues_de, 1), dia)


@dataclass(frozen=True, slots=True)
class CicloDePago:
    """
    El ciclo en curso de una deuda.

    Los abonos cuentan para este ciclo si caen después de `corte`, y lo
    del ciclo hay que pagarlo a más tardar en `limite`. `corte_anterior`
    abre el ciclo previo, para saber si quedó algo sin pagar.
    """

    corte: date
    limite: date
    corte_anterior: date


def ciclo_de_pago(
    hoy: date, dia_pago: int, dia_corte: int | None = None
) -> CicloDePago:
    """
    Devuelve el ciclo de pago en curso a la fecha `hoy`.

    Con día de corte —una tarjeta—, el ciclo empieza en el último corte y
    se paga el primer día de pago después de él; sigue siendo el mismo
    ciclo, ya vencido si no se pagó, hasta el siguiente corte. Sin día de
    corte —un préstamo—, el ciclo va de un día de pago al siguiente, y
    el día de pago mismo todavía pertenece al que vence.
    """
    if dia_corte is not None:
        corte = ultimo_dia(dia_corte, hoy)
        anterior = ultimo_dia(dia_corte, corte, incluido=False)
        return CicloDePago(
            corte=corte, limite=siguiente_dia(dia_pago, corte), corte_anterior=anterior
        )

    # El primer día de pago en o después de hoy.
    limite = siguiente_dia(dia_pago, date.fromordinal(hoy.toordinal() - 1))
    corte = ultimo_dia(dia_pago, limite, incluido=False)
    return CicloDePago(
        corte=corte,
        limite=limite,
        corte_anterior=ultimo_dia(dia_pago, corte, incluido=False),
    )
