from __future__ import annotations

from datetime import date

import pytest

from finanzas.domain.plan import (
    ORIGEN_FALTANTE,
    Ajuste,
    Decision,
    EntradaMes,
    Obligacion,
    proyectar,
    resolver_mes,
)

# ═══════════════════════════════════════════════════════════
# El plan de pagos: qué se paga primero cuando no alcanza.
# ═══════════════════════════════════════════════════════════

SEPTIEMBRE = date(2026, 9, 1)

RENTA = Obligacion(
    clave="renta",
    concepto="Renta",
    origen="Gasto fijo",
    monto=3_000.0,
    vence=date(2026, 10, 1),
    parcial=False,
)
TARJETA = Obligacion(
    clave="tdc",
    concepto="BBVA TDC",
    origen="Deuda exigible",
    monto=5_000.0,
    posponible=True,
    tasa_anual=0.6,
    genera_interes=True,
)
ADEUDO = Obligacion(
    clave="adeudo",
    concepto="Adeudo ITAM",
    origen="Deuda exigible",
    monto=4_000.0,
    posponible=True,
    tope=date(2026, 12, 31),
)


def _por_clave(plan):
    return {r.obligacion.clave: r for r in plan.resoluciones}


def test_si_alcanza_se_paga_todo():
    plan = resolver_mes(SEPTIEMBRE, 20_000.0, 0.0, [RENTA, TARJETA, ADEUDO])

    assert plan.pagado == 12_000.0
    assert plan.pospuesto == plan.falta == 0
    assert plan.queda == 8_000.0


def test_primero_la_renta_luego_la_tarjeta_y_al_final_el_adeudo():
    """Con 8,000 no alcanza: el adeudo, que no cuesta, es lo que se pospone."""
    plan = resolver_mes(SEPTIEMBRE, 8_000.0, 0.0, [ADEUDO, TARJETA, RENTA])
    r = _por_clave(plan)

    assert r["renta"].pagado == 3_000.0
    assert r["tdc"].pagado == 5_000.0
    assert r["adeudo"].pospuesto == 4_000.0
    assert [x.obligacion.clave for x in plan.resoluciones] == ["renta", "tdc", "adeudo"]


def test_una_tarjeta_se_paga_en_parte_si_no_alcanza():
    plan = resolver_mes(SEPTIEMBRE, 5_000.0, 0.0, [RENTA, TARJETA])
    r = _por_clave(plan)

    assert r["tdc"].pagado == 2_000.0
    assert r["tdc"].pospuesto == 3_000.0


def test_la_renta_no_se_paga_a_medias_ni_se_pospone():
    """Lo que no se puede posponer y no alcanza, falta."""
    plan = resolver_mes(SEPTIEMBRE, 1_000.0, 0.0, [RENTA])

    assert plan.falta == 2_000.0
    assert plan.pospuesto == 0


def test_un_gasto_posponible_sin_partes_se_paga_entero_o_nada():
    luz = Obligacion("luz", "Luz", "Gasto fijo", 800.0, posponible=True, parcial=False)

    plan = resolver_mes(SEPTIEMBRE, 500.0, 0.0, [luz])

    assert plan.pagado == 0
    assert plan.pospuesto == 800.0


def test_la_tarjeta_va_antes_aunque_no_tenga_tasa_capturada():
    tarjeta_sin_tasa = Obligacion(
        "tdc", "MP TDC", "Saldo", 3_000.0, posponible=True, genera_interes=True
    )
    plan = resolver_mes(SEPTIEMBRE, 3_000.0, 0.0, [ADEUDO, tarjeta_sin_tasa])

    assert _por_clave(plan)["tdc"].pagado == 3_000.0


def test_la_tarjeta_mas_cara_primero():
    barata = Obligacion("a", "A", "", 1_000.0, posponible=True, tasa_anual=0.3)
    cara = Obligacion("b", "B", "", 1_000.0, posponible=True, tasa_anual=0.9)

    plan = resolver_mes(SEPTIEMBRE, 1_000.0, 0.0, [barata, cara])

    assert _por_clave(plan)["b"].pagado == 1_000.0


def test_al_llegar_a_su_tope_ya_no_se_puede_posponer():
    plan = resolver_mes(date(2026, 12, 1), 0.0, 0.0, [ADEUDO])

    assert plan.falta == 4_000.0


# ── Decisiones a mano ───────────────────────────────────


def test_posponer_a_mano_libera_el_dinero():
    plan = resolver_mes(
        SEPTIEMBRE,
        5_000.0,
        0.0,
        [TARJETA, ADEUDO],
        {"tdc": Ajuste(Decision.POSPONER)},
    )
    r = _por_clave(plan)

    assert r["tdc"].pospuesto == 5_000.0
    assert r["tdc"].manual
    assert r["adeudo"].pagado == 4_000.0


def test_la_renta_no_se_deja_posponer_a_mano():
    plan = resolver_mes(
        SEPTIEMBRE, 3_000.0, 0.0, [RENTA], {"renta": Ajuste(Decision.POSPONER)}
    )

    assert _por_clave(plan)["renta"].pagado == 3_000.0


def test_pagar_una_parte_a_mano():
    plan = resolver_mes(
        SEPTIEMBRE,
        10_000.0,
        0.0,
        [TARJETA],
        {"tdc": Ajuste(Decision.PARCIAL, 1_500.0)},
    )
    r = _por_clave(plan)["tdc"]

    assert r.pagado == 1_500.0
    assert r.pospuesto == 3_500.0


def test_pagar_a_mano_va_antes_que_la_sugerencia():
    """Decidir pagar el adeudo lo pone por delante de la tarjeta."""
    plan = resolver_mes(
        SEPTIEMBRE,
        4_000.0,
        0.0,
        [TARJETA, ADEUDO],
        {"adeudo": Ajuste(Decision.PAGAR)},
    )
    r = _por_clave(plan)

    assert r["adeudo"].pagado == 4_000.0
    assert r["tdc"].pospuesto == 5_000.0


# ── Proyección ───────────────────────────────────────────


def test_lo_pospuesto_llega_al_mes_siguiente_con_intereses():
    meses = [
        EntradaMes(SEPTIEMBRE, 0.0, (TARJETA,)),
        EntradaMes(date(2026, 10, 1), 10_000.0),
    ]

    septiembre, octubre = proyectar(0.0, meses)

    assert septiembre.pospuesto == 5_000.0
    # 60 % al año: 5 % al mes sobre 5,000.
    assert octubre.intereses == 250.0
    assert octubre.pagado == 5_250.0
    assert octubre.queda == pytest.approx(10_000.0 - 5_250.0)


def test_lo_que_falta_se_vuelve_una_deuda_nueva_y_va_primero():
    """
    La renta no alcanzó: se cubre con una deuda nueva, «Faltante de
    septiembre», que en octubre se paga antes que la tarjeta.
    """
    meses = [
        EntradaMes(SEPTIEMBRE, 0.0, (RENTA,)),
        EntradaMes(date(2026, 10, 1), 3_000.0, (TARJETA,)),
    ]

    septiembre, octubre = proyectar(1_000.0, meses)
    primero = octubre.resoluciones[0]

    assert septiembre.falta == 2_000.0
    assert primero.obligacion.concepto == "Faltante de septiembre"
    assert primero.obligacion.origen == ORIGEN_FALTANTE
    assert primero.obligacion.posponible
    assert primero.pagado == 2_000.0
    # La renta ya no vuelve: la cubrió la deuda nueva.
    assert all(r.obligacion.clave != "renta" for r in octubre.resoluciones)


def test_el_faltante_tambien_se_puede_posponer():
    octubre = date(2026, 10, 1)
    meses = [
        EntradaMes(SEPTIEMBRE, 0.0, (RENTA,)),
        EntradaMes(octubre, 5_000.0),
        EntradaMes(date(2026, 11, 1), 0.0),
    ]

    _, en_octubre, noviembre = proyectar(
        0.0, meses, {octubre: {"faltante:2026-09": Ajuste(Decision.POSPONER)}}
    )

    assert en_octubre.pospuesto == 3_000.0
    assert noviembre.resoluciones[0].obligacion.clave == "faltante:2026-09"


def test_lo_que_sobra_se_lleva_al_mes_siguiente():
    meses = [EntradaMes(SEPTIEMBRE, 5_000.0), EntradaMes(date(2026, 10, 1), 1_000.0)]

    _, octubre = proyectar(2_000.0, meses)

    assert octubre.efectivo_inicial == 7_000.0
    assert octubre.disponible == 8_000.0


def test_lo_pospuesto_llega_al_mes_siguiente_para_pagarse():
    """
    Posponer es dejarlo para el mes siguiente, no para siempre.

    En octubre llega como algo que toca pagar: si no alcanza, falta; la
    sugerencia ya no lo vuelve a posponer.
    """
    meses = [
        EntradaMes(date(2026, m, 1), 1_000.0, (ADEUDO,) if m == 9 else ())
        for m in (9, 10, 11, 12)
    ]

    plan = proyectar(0.0, meses)

    assert [p.pospuesto for p in plan] == [3_000.0, 0.0, 0.0, 0.0]
    assert [p.falta for p in plan] == [0.0, 2_000.0, 1_000.0, 0.0]
    assert sum(p.pagado for p in plan) == 4_000.0
    # Un solo renglón del adeudo por mes, no una copia por cada vez.
    assert all(
        sum(1 for r in p.resoluciones if r.obligacion.clave == "adeudo") <= 1
        for p in plan
    )
    assert plan[1].resoluciones[0].obligacion.arrastrada


def test_volver_a_posponerlo_se_decide_en_ese_mes():
    octubre = date(2026, 10, 1)
    meses = [
        EntradaMes(SEPTIEMBRE, 0.0, (ADEUDO,)),
        EntradaMes(octubre, 0.0),
        EntradaMes(date(2026, 11, 1), 5_000.0),
    ]

    _, en_octubre, noviembre = proyectar(
        0.0, meses, {octubre: {"adeudo": Ajuste(Decision.POSPONER)}}
    )

    assert en_octubre.pospuesto == 4_000.0
    assert en_octubre.falta == 0
    assert noviembre.pagado == 4_000.0


def test_cada_mes_tiene_sus_propias_decisiones():
    meses = [
        EntradaMes(SEPTIEMBRE, 10_000.0, (TARJETA,)),
        EntradaMes(date(2026, 10, 1), 10_000.0),
    ]

    septiembre, octubre = proyectar(
        0.0, meses, {SEPTIEMBRE: {"tdc": Ajuste(Decision.POSPONER)}}
    )

    assert septiembre.pospuesto == 5_000.0
    # En octubre nadie decidió posponerla otra vez: se paga, con intereses.
    assert octubre.pospuesto == 0
    assert octubre.pagado == 5_250.0


def test_lo_que_vas_a_pagar_puede_pasar_de_lo_que_tienes():
    """El faltante se ve: tienes 1,000, vas a pagar 3,000 y te faltan 2,000."""
    plan = resolver_mes(SEPTIEMBRE, 1_000.0, 0.0, [RENTA])

    assert plan.pagado == 1_000.0
    assert plan.a_pagar == 3_000.0
    assert plan.balance == -2_000.0
    assert plan.queda == 0


def test_pagar_una_parte_de_la_renta_deja_el_resto_para_el_mes_siguiente():
    """
    Pagar una parte es una decisión: el resto pasa al mes siguiente y
    «Vas a pagar» baja, aunque la renta no se pueda posponer.
    """
    octubre = date(2026, 10, 1)
    meses = [
        EntradaMes(SEPTIEMBRE, 0.0, (RENTA,)),
        EntradaMes(octubre, 5_000.0),
    ]

    septiembre, en_octubre = proyectar(
        10_000.0, meses, {SEPTIEMBRE: {"renta": Ajuste(Decision.PARCIAL, 1_000.0)}}
    )

    assert septiembre.a_pagar == 1_000.0
    assert septiembre.pospuesto == 2_000.0
    assert septiembre.falta == 0
    assert en_octubre.resoluciones[0].obligacion.monto == 2_000.0
