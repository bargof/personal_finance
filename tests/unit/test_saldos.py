from __future__ import annotations

from datetime import date

import pytest

from finanzas.domain import captura
from finanzas.domain.enums import TipoCuenta, TipoMovimiento
from finanzas.domain.saldos import Ancla, Flujo, Libro, Sentido

# ═══════════════════════════════════════════════════════════
# El libro de una cuenta
#
# Flujos y anclas, sin base de datos: la deducción del saldo
# es aritmética pura y aquí se fija en los dos sentidos.
# ═══════════════════════════════════════════════════════════


def _libro(anclas: list[tuple[str, float]] = ()) -> Libro:
    """Un libro con tres movimientos de agosto y las anclas dadas."""
    return Libro(
        flujos=[
            Flujo(date(2026, 8, 5), -1_000.0),
            Flujo(date(2026, 8, 15), 20_000.0),
            Flujo(date(2026, 8, 20), -500.0),
        ],
        anclas=[Ancla(date.fromisoformat(f), s) for f, s in anclas],
    )


def test_sin_anclas_se_suma_desde_cero():
    saldo = _libro().saldo_a(date(2026, 8, 31))

    assert saldo.saldo == 18_500.0
    assert saldo.sentido == Sentido.SIN_ANCLA
    assert not saldo.verificado
    assert saldo.movimientos == 3


def test_hacia_adelante_desde_la_ultima_ancla_anterior():
    libro = _libro([("2026-07-31", 10_000.0), ("2026-08-10", 9_000.0)])

    saldo = libro.saldo_a(date(2026, 8, 31))

    # Manda la ancla del 10, no la del 31 de julio.
    assert saldo.ancla.fecha == date(2026, 8, 10)
    assert saldo.saldo == 28_500.0
    assert saldo.sentido == Sentido.ADELANTE
    assert saldo.movimientos == 2


def test_hacia_atras_desde_la_primera_ancla_posterior():
    libro = _libro([("2026-08-31", 28_500.0)])

    inicio = libro.saldo_a(date(2026, 7, 31))
    medio = libro.saldo_a(date(2026, 8, 10))

    assert inicio.saldo == 10_000.0
    assert inicio.sentido == Sentido.ATRAS
    assert medio.saldo == 9_000.0


def test_el_ancla_incluye_los_movimientos_de_su_propio_dia():
    """Un saldo «al cierre del día» ya tiene dentro lo que pasó ese día."""
    libro = _libro([("2026-08-05", 9_000.0)])

    assert libro.saldo_a(date(2026, 8, 5)).saldo == 9_000.0
    assert libro.saldo_a(date(2026, 8, 4)).saldo == 10_000.0


def test_dos_anclas_que_cuadran_no_reportan_nada():
    libro = _libro([("2026-07-31", 10_000.0), ("2026-08-31", 28_500.0)])

    assert libro.descuadres() == []


def test_dos_anclas_que_no_cuadran_dicen_cuanto_falta():
    libro = _libro([("2026-07-31", 10_000.0), ("2026-08-31", 28_200.0)])

    (descuadre,) = libro.descuadres()

    assert descuadre.esperado == 28_500.0
    assert descuadre.diferencia == -300.0
    assert descuadre.movimientos == 3


def test_un_centavo_de_redondeo_no_es_descuadre():
    libro = _libro([("2026-07-31", 10_000.0), ("2026-08-31", 28_500.004)])

    assert libro.descuadres() == []


# ═══════════════════════════════════════════════════════════
# Tipos de cuenta
# ═══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("tipo", "pasivo", "ahorro", "liquida"),
    [
        (TipoCuenta.EFECTIVO, False, False, True),
        (TipoCuenta.DEBITO, False, False, True),
        (TipoCuenta.AHORRO, False, True, True),
        (TipoCuenta.INVERSION, False, True, False),
        (TipoCuenta.VALES, False, False, False),
        (TipoCuenta.CREDITO, True, False, False),
        (TipoCuenta.PRESTAMO, True, False, False),
    ],
)
def test_lo_que_cada_tipo_de_cuenta_implica(tipo, pasivo, ahorro, liquida):
    assert tipo.es_pasivo is pasivo
    assert tipo.guarda_ahorro is ahorro
    assert tipo.es_liquida is liquida
    assert (tipo.lado == "Pasivo") is pasivo


# ═══════════════════════════════════════════════════════════
# Reglas de captura por tipo
# ═══════════════════════════════════════════════════════════


def test_solo_el_gasto_lleva_las_banderas_de_gasto():
    campos = ("necesidad", "naturaleza", "recurrente", "planeado", "pago", "empresa")
    for campo in campos:
        assert captura.admite(TipoMovimiento.GASTO, campo)
        assert not captura.admite(TipoMovimiento.INGRESO, campo)
        assert not captura.admite(TipoMovimiento.AHORRO, campo)
        assert not captura.admite(TipoMovimiento.TRANSFERENCIA, campo)


def test_lo_que_mueve_dinero_entre_cuentas_pide_destino():
    assert captura.con_destino(TipoMovimiento.TRANSFERENCIA)
    assert captura.con_destino(TipoMovimiento.AHORRO)
    assert captura.con_destino(TipoMovimiento.INVERSION)
    assert not captura.con_destino(TipoMovimiento.GASTO)
    assert not captura.con_destino(TipoMovimiento.INGRESO)


def test_con_tarjeta_el_pago_no_se_pregunta():
    assert captura.pago_lo_decide_la_cuenta(TipoMovimiento.GASTO, "Crédito")
    assert not captura.pago_lo_decide_la_cuenta(TipoMovimiento.GASTO, "Débito")
    # En lo que no es gasto la pregunta ni existe.
    assert captura.pago_lo_decide_la_cuenta(TipoMovimiento.INGRESO, "Débito")


@pytest.mark.parametrize(
    ("tipo", "origen", "destino", "nivel", "fragmento"),
    [
        (TipoMovimiento.TRANSFERENCIA, "Débito", None, "error", "a dónde llega"),
        (TipoMovimiento.TRANSFERENCIA, "Débito", "Crédito", "info", "pago de tarjeta"),
        (TipoMovimiento.TRANSFERENCIA, "Débito", "Efectivo", "info", "efectivo"),
        (TipoMovimiento.TRANSFERENCIA, "Ahorro", "Débito", "aviso", "retiro"),
        (TipoMovimiento.TRANSFERENCIA, "Débito", "Ahorro", "aviso", "aportación"),
        (TipoMovimiento.AHORRO, "Débito", "Débito", "error", "no es una cuenta"),
        (TipoMovimiento.AHORRO, "Débito", "Ahorro", "info", "suma a tu ahorro"),
    ],
)
def test_el_formulario_explica_lo_que_significa_cada_traspaso(
    tipo, origen, destino, nivel, fragmento
):
    pista = captura.describir_traspaso(tipo, origen, destino)

    assert pista is not None
    assert pista[0] == nivel
    assert fragmento.lower() in pista[1].lower()


def test_un_gasto_no_tiene_pista_de_traspaso():
    assert captura.describir_traspaso(TipoMovimiento.GASTO, "Débito", None) is None
