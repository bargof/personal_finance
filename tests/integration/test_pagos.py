from __future__ import annotations

from datetime import date

import pytest

from finanzas.application.services.movimientos_service import MovimientosService
from finanzas.application.services.pagos_service import (
    COMO_OTRO,
    COMO_RESTRINGIDA,
    DEUDA_EXIGIBLE,
    GASTO_SIN_PAGAR,
    SALDO_TARJETA,
    PagosService,
)
from finanzas.application.services.patrimonio_service import PatrimonioService
from finanzas.application.services.suscripciones_service import SuscripcionesService
from finanzas.data.repositories.catalogos_repository import CatalogosRepository
from finanzas.data.repositories.ingresos_fijos_repository import (
    IngresosFijosRepository,
)
from finanzas.data.repositories.movimientos_repository import MovimientosRepository
from finanzas.data.repositories.patrimonio_repository import PatrimonioRepository
from finanzas.data.repositories.plan_repository import PlanRepository
from finanzas.data.repositories.suscripciones_repository import SuscripcionesRepository
from finanzas.domain.calendario import cobro_en_mes
from finanzas.domain.enums import (
    ClaseCargo,
    EstadoIngreso,
    EstadoMovimiento,
    EstadoPago,
    FrecuenciaCobro,
    TipoMovimiento,
)

# ═══════════════════════════════════════════════════════════
# Pagos del mes: cargos fijos, lo exigible de las deudas y lo
# gastado sin pagar, cada peso contado una sola vez.
# ═══════════════════════════════════════════════════════════

HOY = date(2026, 10, 15)
OCTUBRE = date(2026, 10, 1)


@pytest.fixture
def patrimonio(db_path: str) -> PatrimonioService:
    return PatrimonioService(PatrimonioRepository(db_path))


@pytest.fixture
def cargos(db_path: str) -> SuscripcionesService:
    return SuscripcionesService(
        SuscripcionesRepository(db_path), CatalogosRepository(db_path)
    )


@pytest.fixture
def servicio(movimientos: MovimientosRepository) -> MovimientosService:
    return MovimientosService(movimientos)


@pytest.fixture
def pagos(db_path, movimientos, patrimonio) -> PagosService:
    return PagosService(
        movimientos,
        SuscripcionesRepository(db_path),
        patrimonio,
        IngresosFijosRepository(db_path),
        PlanRepository(db_path),
    )


def _renta(cargos, ids_catalogo, **campos) -> int:
    datos = {
        "servicio": "Renta",
        "costo_por_cobro": 6500.0,
        "categoria_id": ids_catalogo["vivienda"],
        "proximo_cobro": date(2026, 11, 1),
        "clase": ClaseCargo.GASTO_FIJO,
        "necesidad": "Esencial",
    }
    datos.update(campos)
    return cargos.crear(**datos)


# ── Calendario ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("referencia", "frecuencia", "mes", "esperado"),
    [
        (date(2026, 11, 1), FrecuenciaCobro.MENSUAL, (2026, 10), date(2026, 10, 1)),
        (date(2026, 11, 1), FrecuenciaCobro.MENSUAL, (2027, 2), date(2027, 2, 1)),
        (date(2026, 1, 31), FrecuenciaCobro.MENSUAL, (2026, 2), date(2026, 2, 28)),
        (date(2026, 9, 8), FrecuenciaCobro.ANUAL, (2026, 10), None),
        (date(2026, 9, 8), FrecuenciaCobro.ANUAL, (2027, 9), date(2027, 9, 8)),
        (date(2026, 8, 10), FrecuenciaCobro.BIMESTRAL, (2026, 10), date(2026, 10, 10)),
        (date(2026, 8, 10), FrecuenciaCobro.BIMESTRAL, (2026, 11), None),
    ],
)
def test_cuando_toca_un_cargo_en_un_mes(referencia, frecuencia, mes, esperado):
    assert cobro_en_mes(referencia, frecuencia, *mes) == esperado


def test_un_cargo_sin_movimiento_esta_por_pagar(pagos, cargos, ids_catalogo):
    _renta(cargos, ids_catalogo, proximo_cobro=date(2026, 11, 20))

    fila = pagos.calendario(OCTUBRE, HOY).iloc[0]

    assert fila["fecha"] == date(2026, 10, 20)
    assert fila["estado"] == str(EstadoPago.POR_PAGAR)


def test_un_cargo_sin_pagar_despues_de_su_fecha_esta_vencido(
    pagos, cargos, ids_catalogo
):
    _renta(cargos, ids_catalogo)

    assert pagos.calendario(OCTUBRE, HOY).iloc[0]["estado"] == str(EstadoPago.VENCIDO)


def test_el_gasto_de_su_categoria_lo_paga(pagos, cargos, servicio, ids_catalogo):
    _renta(cargos, ids_catalogo)
    pago = servicio.registrar(
        fecha=date(2026, 9, 30),
        tipo=TipoMovimiento.GASTO,
        monto=6500.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
        descripcion="Transferencia al casero",
    )

    fila = pagos.calendario(OCTUBRE, HOY).iloc[0]

    assert fila["estado"] == str(EstadoPago.PAGADO)
    assert fila["movimiento_id"] == pago
    assert fila["pagado_el"] == date(2026, 9, 30)


def test_un_gasto_que_lo_nombra_lo_paga_aunque_cambie_la_categoria(
    pagos, cargos, servicio, ids_catalogo
):
    cargos.crear(
        servicio="Apple Music",
        costo_por_cobro=75.0,
        proximo_cobro=date(2026, 10, 6),
    )
    servicio.registrar(
        fecha=date(2026, 10, 7),
        tipo=TipoMovimiento.GASTO,
        monto=79.0,
        categoria_id=ids_catalogo["restaurantes"],
        cuenta_id=ids_catalogo["tarjeta"],
        descripcion="Apple Music octubre",
    )

    assert pagos.calendario(OCTUBRE, HOY).iloc[0]["estado"] == str(EstadoPago.PAGADO)


def test_un_monto_muy_distinto_no_lo_paga(pagos, cargos, servicio, ids_catalogo):
    _renta(cargos, ids_catalogo)
    servicio.registrar(
        fecha=date(2026, 10, 1),
        tipo=TipoMovimiento.GASTO,
        monto=450.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
        descripcion="Foco del baño",
    )

    assert pagos.calendario(OCTUBRE, HOY).iloc[0]["estado"] != str(EstadoPago.PAGADO)


def test_un_movimiento_paga_un_solo_cargo(pagos, cargos, servicio, ids_catalogo):
    _renta(cargos, ids_catalogo, servicio="Renta depa")
    _renta(cargos, ids_catalogo, servicio="Renta bodega")
    servicio.registrar(
        fecha=date(2026, 10, 1),
        tipo=TipoMovimiento.GASTO,
        monto=6500.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
    )

    estados = pagos.calendario(OCTUBRE, HOY)["estado"].tolist()

    assert estados.count(str(EstadoPago.PAGADO)) == 1


def test_un_cargo_registrado_sin_pagar_no_se_cuenta_dos_veces(
    pagos, cargos, servicio, ids_catalogo
):
    """Ya está entre los gastos sin pagar: ahí se cuenta, y sólo ahí."""
    # Sin la renta de noviembre adelantada, que aquí no se prueba.
    pagos.fijar_anticipacion(0)
    _renta(cargos, ids_catalogo)
    servicio.registrar(
        fecha=date(2026, 10, 1),
        tipo=TipoMovimiento.GASTO,
        monto=6500.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
        fecha_pago=None,
    )

    fila = pagos.calendario(OCTUBRE, HOY).iloc[0]
    resumen = pagos.resumen(OCTUBRE, HOY)
    lista = pagos.por_pagar(OCTUBRE, HOY)

    assert fila["en_sin_pagar"]
    assert resumen.fijos_pendientes == 0
    assert resumen.sin_pagar == 6500.0
    assert resumen.por_pagar == 6500.0
    assert lista["origen"].tolist() == [GASTO_SIN_PAGAR]


def test_un_cargo_anual_no_toca_fuera_de_su_mes(pagos, cargos, ids_catalogo):
    cargos.crear(
        servicio="Telcel anual",
        costo_por_cobro=2400.0,
        frecuencia=FrecuenciaCobro.ANUAL,
        proximo_cobro=date(2027, 9, 8),
    )

    assert pagos.calendario(OCTUBRE, HOY).empty


def test_un_cargo_dado_de_baja_no_aparece(pagos, cargos, ids_catalogo):
    cargo = _renta(cargos, ids_catalogo)
    cargos.cambiar_estado(cargo, activa=False)

    assert pagos.calendario(OCTUBRE, HOY).empty


# ── Lo exigible de las deudas ────────────────────────────


def _deuda_de_tarjeta(servicio, patrimonio, ids_catalogo, debe: float = 10_000.0):
    """Una tarjeta que debe `debe` desde el 1 de octubre."""
    patrimonio.verificar_saldo(ids_catalogo["tarjeta"], date(2026, 10, 1), debe)


def test_una_deuda_sin_exigible_no_pide_nada(patrimonio, servicio, ids_catalogo):
    _deuda_de_tarjeta(servicio, patrimonio, ids_catalogo)

    fila = patrimonio.exigibles(HOY).set_index("cuenta_id").loc[ids_catalogo["tarjeta"]]

    assert fila["deuda"] == 10_000.0
    assert fila["pendiente"] == 0
    assert fila["estado"] == ""


def test_lo_exigible_baja_con_los_abonos_posteriores(
    patrimonio, servicio, ids_catalogo
):
    _deuda_de_tarjeta(servicio, patrimonio, ids_catalogo)
    patrimonio.fijar_exigible(
        ids_catalogo["tarjeta"],
        3_000.0,
        fecha_limite=date(2026, 10, 20),
        declarado_el=date(2026, 10, 5),
    )
    for fecha, monto in ((date(2026, 10, 3), 500.0), (date(2026, 10, 10), 1_000.0)):
        servicio.registrar(
            fecha=fecha,
            tipo=TipoMovimiento.TRANSFERENCIA,
            monto=monto,
            categoria_id=ids_catalogo["ahorro"],
            cuenta_id=ids_catalogo["cuenta"],
            cuenta_destino_id=ids_catalogo["tarjeta"],
        )

    fila = patrimonio.exigibles(HOY).set_index("cuenta_id").loc[ids_catalogo["tarjeta"]]

    # El abono del 3 es anterior a lo declarado y no cuenta.
    assert fila["abonado"] == 1_000.0
    assert fila["pendiente"] == 2_000.0
    assert fila["estado"] == str(EstadoPago.POR_PAGAR)
    assert fila["dias"] == 5


def test_lo_exigible_cubierto_queda_pagado(patrimonio, servicio, ids_catalogo):
    _deuda_de_tarjeta(servicio, patrimonio, ids_catalogo)
    patrimonio.fijar_exigible(
        ids_catalogo["tarjeta"], 1_000.0, declarado_el=date(2026, 10, 5)
    )
    servicio.registrar(
        fecha=date(2026, 10, 6),
        tipo=TipoMovimiento.TRANSFERENCIA,
        monto=1_500.0,
        categoria_id=ids_catalogo["ahorro"],
        cuenta_id=ids_catalogo["cuenta"],
        cuenta_destino_id=ids_catalogo["tarjeta"],
    )

    fila = patrimonio.exigibles(HOY).set_index("cuenta_id").loc[ids_catalogo["tarjeta"]]

    assert fila["pendiente"] == 0
    assert fila["estado"] == str(EstadoPago.PAGADO)


def test_lo_exigible_nunca_pasa_de_la_deuda(patrimonio, servicio, ids_catalogo):
    _deuda_de_tarjeta(servicio, patrimonio, ids_catalogo, debe=800.0)
    patrimonio.fijar_exigible(
        ids_catalogo["tarjeta"], 1_000.0, declarado_el=date(2026, 10, 5)
    )

    fila = patrimonio.exigibles(HOY).set_index("cuenta_id").loc[ids_catalogo["tarjeta"]]

    assert fila["pendiente"] == 800.0


def test_lo_exigible_vencido(patrimonio, servicio, ids_catalogo):
    _deuda_de_tarjeta(servicio, patrimonio, ids_catalogo)
    patrimonio.fijar_exigible(
        ids_catalogo["tarjeta"],
        1_000.0,
        fecha_limite=date(2026, 10, 10),
        declarado_el=date(2026, 10, 5),
    )

    fila = patrimonio.exigibles(HOY).set_index("cuenta_id").loc[ids_catalogo["tarjeta"]]

    assert fila["estado"] == str(EstadoPago.VENCIDO)
    assert fila["dias"] == -5


def test_capturar_otra_vez_lo_corrige(patrimonio, servicio, ids_catalogo):
    _deuda_de_tarjeta(servicio, patrimonio, ids_catalogo)
    patrimonio.fijar_exigible(ids_catalogo["tarjeta"], 1_000.0, declarado_el=HOY)
    patrimonio.fijar_exigible(ids_catalogo["tarjeta"], 2_500.0, declarado_el=HOY)

    fila = patrimonio.exigibles(HOY).set_index("cuenta_id").loc[ids_catalogo["tarjeta"]]

    assert fila["exigible"] == 2_500.0


def test_quitar_lo_exigible(patrimonio, servicio, ids_catalogo):
    _deuda_de_tarjeta(servicio, patrimonio, ids_catalogo)
    patrimonio.fijar_exigible(ids_catalogo["tarjeta"], 1_000.0, declarado_el=HOY)
    patrimonio.quitar_exigible(ids_catalogo["tarjeta"])

    fila = patrimonio.exigibles(HOY).set_index("cuenta_id").loc[ids_catalogo["tarjeta"]]

    assert fila["estado"] == ""


def test_una_cuenta_que_no_es_deuda_no_tiene_exigible(patrimonio, ids_catalogo):
    with pytest.raises(ValueError, match="deuda"):
        patrimonio.fijar_exigible(ids_catalogo["cuenta"], 1_000.0)


def test_lo_exigible_no_admite_negativos(patrimonio, ids_catalogo):
    with pytest.raises(ValueError, match="positivo"):
        patrimonio.fijar_exigible(ids_catalogo["tarjeta"], -1.0)


# ── La vista completa ────────────────────────────────────


def test_el_resumen_junta_todo_sin_duplicar(
    pagos, cargos, patrimonio, servicio, ids_catalogo
):
    patrimonio.verificar_saldo(ids_catalogo["cuenta"], date(2026, 10, 1), 20_000.0)
    _deuda_de_tarjeta(servicio, patrimonio, ids_catalogo)
    patrimonio.fijar_exigible(
        ids_catalogo["tarjeta"],
        3_000.0,
        fecha_limite=date(2026, 10, 20),
        declarado_el=date(2026, 10, 5),
    )
    _renta(cargos, ids_catalogo, proximo_cobro=date(2026, 10, 25))
    servicio.registrar(
        fecha=date(2026, 10, 1),
        tipo=TipoMovimiento.INGRESO,
        monto=25_000.0,
        categoria_id=ids_catalogo["sueldo"],
        cuenta_id=ids_catalogo["cuenta"],
    )
    servicio.registrar(
        fecha=date(2026, 10, 30),
        tipo=TipoMovimiento.INGRESO,
        monto=5_000.0,
        categoria_id=ids_catalogo["sueldo"],
        cuenta_id=ids_catalogo["cuenta"],
        estado=EstadoMovimiento.PENDIENTE,
    )

    resumen = pagos.resumen(OCTUBRE, HOY)
    lista = pagos.por_pagar(OCTUBRE, HOY)

    assert resumen.recibido == 25_000.0
    assert resumen.por_recibir == 5_000.0
    assert resumen.ingresos == 30_000.0
    assert resumen.fijos_pendientes == 6_500.0
    assert resumen.exigible == 3_000.0
    # La tarjeta debe 10,000: 3,000 exigibles y 7,000 de resto.
    assert resumen.saldo_tarjetas == 7_000.0
    assert resumen.por_pagar == 16_500.0
    assert resumen.queda == pytest.approx(resumen.disponible - 16_500.0)
    assert set(lista["origen"]) == {
        str(ClaseCargo.GASTO_FIJO),
        DEUDA_EXIGIBLE,
        SALDO_TARJETA,
    }
    # Lo exigible vence antes que la renta y va primero.
    assert lista.iloc[0]["origen"] == DEUDA_EXIGIBLE


def test_lo_vencido_va_primero(pagos, cargos, ids_catalogo):
    _renta(cargos, ids_catalogo, servicio="Renta", proximo_cobro=date(2026, 10, 25))
    _renta(cargos, ids_catalogo, servicio="Luz", proximo_cobro=date(2026, 10, 2))

    lista = pagos.por_pagar(OCTUBRE, HOY)

    assert lista.iloc[0]["concepto"] == "Luz"
    assert lista.iloc[0]["estado"] == str(EstadoPago.VENCIDO)


# ── La clase del cargo ───────────────────────────────────


def test_un_gasto_fijo_no_infla_las_suscripciones(cargos, ids_catalogo):
    _renta(cargos, ids_catalogo)
    cargos.crear(servicio="Streaming", costo_por_cobro=200.0)

    resumen = cargos.resumen()

    assert resumen["suscripciones_mensual"] == 200.0
    assert resumen["fijos_mensual"] == 6_500.0
    assert SuscripcionesRepository(cargos._repo._db_path).costo_mensual_total() == 200.0


def test_un_gasto_fijo_nunca_es_candidato_a_cancelar(cargos, ids_catalogo):
    _renta(cargos, ids_catalogo, necesidad="Deseo")

    assert not cargos.listar().iloc[0]["candidato_a_cancelar"]


def test_la_clase_se_puede_cambiar(cargos, ids_catalogo):
    cargo = cargos.crear(servicio="Gimnasio", costo_por_cobro=500.0)
    cargos.actualizar(cargo, clase=ClaseCargo.GASTO_FIJO)

    assert cargos.listar().iloc[0]["clase"] == str(ClaseCargo.GASTO_FIJO)


# ── Días fijos: el ciclo de pago ─────────────────────────

from finanzas.application.services.patrimonio_service import (  # noqa: E402
    FUENTE_CAPTURADO,
    FUENTE_CORTE,
    FUENTE_PARCIALIDAD,
    FUENTE_SIN_MONTO,
)
from finanzas.domain.calendario import ciclo_de_pago  # noqa: E402


@pytest.mark.parametrize(
    ("hoy", "dia_pago", "dia_corte", "corte", "limite"),
    [
        # Tarjeta que corta el 5 y se paga el 25.
        (date(2026, 10, 15), 25, 5, date(2026, 10, 5), date(2026, 10, 25)),
        # El día de corte ya abre el ciclo nuevo.
        (date(2026, 10, 5), 25, 5, date(2026, 10, 5), date(2026, 10, 25)),
        # Pasado el límite y antes del siguiente corte, sigue el mismo ciclo.
        (date(2026, 10, 28), 25, 5, date(2026, 10, 5), date(2026, 10, 25)),
        # Corta el 20 y se paga el 10 del mes siguiente.
        (date(2026, 10, 25), 10, 20, date(2026, 10, 20), date(2026, 11, 10)),
        # Un corte del 31 en un mes de 30.
        (date(2026, 11, 30), 20, 31, date(2026, 11, 30), date(2026, 12, 20)),
        # Préstamo que se paga el 10: el día 10 todavía es del ciclo que vence.
        (date(2026, 10, 10), 10, None, date(2026, 9, 10), date(2026, 10, 10)),
        (date(2026, 10, 11), 10, None, date(2026, 10, 10), date(2026, 11, 10)),
    ],
)
def test_el_ciclo_de_pago(hoy, dia_pago, dia_corte, corte, limite):
    ciclo = ciclo_de_pago(hoy, dia_pago, dia_corte)

    assert (ciclo.corte, ciclo.limite) == (corte, limite)


def _gasto_con_tarjeta(servicio, ids_catalogo, fecha, monto):
    servicio.registrar(
        fecha=fecha,
        tipo=TipoMovimiento.GASTO,
        monto=monto,
        categoria_id=ids_catalogo["restaurantes"],
        cuenta_id=ids_catalogo["tarjeta"],
    )


def _abono(servicio, ids_catalogo, fecha, monto, destino=None):
    servicio.registrar(
        fecha=fecha,
        tipo=TipoMovimiento.TRANSFERENCIA,
        monto=monto,
        categoria_id=ids_catalogo["ahorro"],
        cuenta_id=ids_catalogo["cuenta"],
        cuenta_destino_id=destino or ids_catalogo["tarjeta"],
    )


@pytest.fixture
def tarjeta_con_ciclo(patrimonio, servicio, ids_catalogo):
    """Corta el 5 y se paga el 25; al corte del 5 de octubre debía 10,500."""
    patrimonio.verificar_saldo(ids_catalogo["tarjeta"], date(2026, 10, 1), 10_000.0)
    _gasto_con_tarjeta(servicio, ids_catalogo, date(2026, 10, 3), 500.0)
    # Después del corte: es del ciclo siguiente.
    _gasto_con_tarjeta(servicio, ids_catalogo, date(2026, 10, 10), 300.0)
    patrimonio.fijar_calendario_deuda(ids_catalogo["tarjeta"], dia_pago=25, dia_corte=5)
    return ids_catalogo["tarjeta"]


def _de(patrimonio, cuenta_id, hoy=HOY):
    return patrimonio.exigibles(hoy).set_index("cuenta_id").loc[cuenta_id]


def test_la_tarjeta_pide_lo_que_debia_al_corte(
    patrimonio, servicio, ids_catalogo, tarjeta_con_ciclo
):
    _abono(servicio, ids_catalogo, date(2026, 10, 8), 2_000.0)

    fila = _de(patrimonio, tarjeta_con_ciclo)

    assert fila["fuente"] == FUENTE_CORTE
    assert fila["exigible"] == 10_500.0
    assert fila["abonado"] == 2_000.0
    assert fila["pendiente"] == 8_500.0
    assert fila["fecha_limite"] == date(2026, 10, 25)
    assert fila["estado"] == str(EstadoPago.POR_PAGAR)


def test_la_tarjeta_sin_pagar_despues_del_limite_esta_vencida(
    patrimonio, tarjeta_con_ciclo
):
    fila = _de(patrimonio, tarjeta_con_ciclo, hoy=date(2026, 10, 28))

    assert fila["estado"] == str(EstadoPago.VENCIDO)


def test_el_abono_antes_del_corte_no_cuenta_para_este_ciclo(
    patrimonio, servicio, ids_catalogo, tarjeta_con_ciclo
):
    """Ya está descontado del saldo al corte."""
    _abono(servicio, ids_catalogo, date(2026, 10, 4), 1_000.0)

    fila = _de(patrimonio, tarjeta_con_ciclo)

    assert fila["exigible"] == 9_500.0
    assert fila["abonado"] == 0


def test_lo_capturado_en_el_ciclo_manda_sobre_el_corte(
    patrimonio, servicio, ids_catalogo, tarjeta_con_ciclo
):
    """Con compras a meses, el saldo al corte exagera el pago."""
    patrimonio.fijar_exigible(
        tarjeta_con_ciclo, 3_000.0, declarado_el=date(2026, 10, 5)
    )
    _abono(servicio, ids_catalogo, date(2026, 10, 8), 2_000.0)

    fila = _de(patrimonio, tarjeta_con_ciclo)

    assert fila["fuente"] == FUENTE_CAPTURADO
    assert fila["pendiente"] == 1_000.0
    # Sin fecha capturada, toma la del ciclo.
    assert fila["fecha_limite"] == date(2026, 10, 25)


def test_lo_capturado_en_un_ciclo_anterior_ya_no_manda(patrimonio, tarjeta_con_ciclo):
    patrimonio.fijar_exigible(tarjeta_con_ciclo, 3_000.0, declarado_el=date(2026, 9, 5))

    assert _de(patrimonio, tarjeta_con_ciclo)["fuente"] == FUENTE_CORTE


@pytest.fixture
def prestamo(catalogos, patrimonio):
    """Debe 20,000 desde el 1 de septiembre; se paga el 10, 3,000 al mes."""
    cuenta = catalogos.crear_cuenta("Préstamo escuela", "Préstamo")
    patrimonio.verificar_saldo(cuenta, date(2026, 9, 1), 20_000.0)
    patrimonio.fijar_calendario_deuda(cuenta, dia_pago=10, pago_mensual=3_000.0)
    return cuenta


def test_el_prestamo_pide_su_parcialidad(patrimonio, servicio, ids_catalogo, prestamo):
    # La del 10 de octubre, pagada a tiempo; y un adelanto de la siguiente.
    _abono(servicio, ids_catalogo, date(2026, 10, 9), 3_000.0, destino=prestamo)
    _abono(servicio, ids_catalogo, date(2026, 10, 12), 1_000.0, destino=prestamo)

    fila = _de(patrimonio, prestamo)

    assert fila["fuente"] == FUENTE_PARCIALIDAD
    assert fila["exigible"] == 3_000.0
    assert fila["pendiente"] == 2_000.0
    assert fila["fecha_limite"] == date(2026, 11, 10)
    assert fila["estado"] == str(EstadoPago.POR_PAGAR)


def test_la_parcialidad_que_no_se_pago_se_arrastra_vencida(
    patrimonio, servicio, ids_catalogo, prestamo
):
    """
    La del 10 de octubre no se pagó: sigue debiéndose, ya vencida.

    Lo que se abona después paga primero lo atrasado.
    """
    _abono(servicio, ids_catalogo, date(2026, 10, 12), 1_000.0, destino=prestamo)

    fila = _de(patrimonio, prestamo)

    assert fila["exigible"] == 6_000.0
    assert fila["pendiente"] == 5_000.0
    assert fila["estado"] == str(EstadoPago.VENCIDO)


def test_un_dia_de_pago_sin_monto_lo_avisa(patrimonio, catalogos):
    cuenta = catalogos.crear_cuenta("Préstamo familiar", "Préstamo")
    patrimonio.verificar_saldo(cuenta, date(2026, 9, 1), 5_000.0)
    patrimonio.fijar_calendario_deuda(cuenta, dia_pago=20)

    fila = _de(patrimonio, cuenta)

    assert fila["fuente"] == FUENTE_SIN_MONTO
    assert fila["fecha_limite"] == date(2026, 10, 20)
    assert fila["pendiente"] == 0
    assert fila["estado"] == ""


def test_el_ciclo_de_la_tarjeta_llega_a_pagos_del_mes(pagos, tarjeta_con_ciclo):
    """
    Lo del corte vence este ciclo; lo comprado después, el siguiente.

    Juntos suman la deuda de la tarjeta: 10,500 al corte y 300 después.
    """
    lista = pagos.por_pagar(OCTUBRE, HOY).set_index("origen")

    assert lista.loc[DEUDA_EXIGIBLE, "monto"] == 10_500.0
    assert lista.loc[DEUDA_EXIGIBLE, "fecha_limite"] == date(2026, 10, 25)
    assert lista.loc[SALDO_TARJETA, "monto"] == 300.0
    # Corta el 5 de noviembre y se paga el 25.
    assert lista.loc[SALDO_TARJETA, "fecha_limite"] == date(2026, 11, 25)


def test_una_tarjeta_sin_ciclo_pide_todo_su_saldo(
    pagos, patrimonio, servicio, ids_catalogo
):
    _deuda_de_tarjeta(servicio, patrimonio, ids_catalogo, debe=4_200.0)

    lista = pagos.por_pagar(OCTUBRE, HOY)
    resumen = pagos.resumen(OCTUBRE, HOY)

    assert lista["origen"].tolist() == [SALDO_TARJETA]
    assert lista.iloc[0]["monto"] == 4_200.0
    assert lista.iloc[0]["fecha_limite"] is None
    assert resumen.por_pagar == 4_200.0


def test_una_tarjeta_pagada_al_corte_pide_solo_lo_nuevo(
    pagos, servicio, ids_catalogo, tarjeta_con_ciclo
):
    _abono(servicio, ids_catalogo, date(2026, 10, 8), 10_500.0)

    lista = pagos.por_pagar(OCTUBRE, HOY)

    assert lista["origen"].tolist() == [SALDO_TARJETA]
    assert lista.iloc[0]["monto"] == 300.0


def test_un_prestamo_no_suma_su_saldo_completo(pagos, prestamo):
    """Un préstamo se paga por parcialidades: sólo cuenta lo exigible."""
    lista = pagos.por_pagar(OCTUBRE, HOY)

    assert SALDO_TARJETA not in set(lista["origen"])


def test_quitar_los_dias_vuelve_a_lo_de_antes(patrimonio, tarjeta_con_ciclo):
    patrimonio.fijar_calendario_deuda(tarjeta_con_ciclo, dia_pago=None)

    fila = _de(patrimonio, tarjeta_con_ciclo)

    assert fila["fuente"] == ""
    assert fila["fecha_limite"] is None


@pytest.mark.parametrize(
    ("cuenta", "campos", "mensaje"),
    [
        ("cuenta", {"dia_pago": 10}, "deuda"),
        ("tarjeta", {"dia_pago": 32}, "del 1 al 31"),
        ("tarjeta", {"dia_pago": 10, "pago_mensual": -1.0}, "positivo"),
    ],
)
def test_los_dias_fijos_se_validan(patrimonio, ids_catalogo, cuenta, campos, mensaje):
    with pytest.raises(ValueError, match=mensaje):
        patrimonio.fijar_calendario_deuda(ids_catalogo[cuenta], **campos)


def test_un_prestamo_no_tiene_dia_de_corte(patrimonio, prestamo):
    with pytest.raises(ValueError, match="corte"):
        patrimonio.fijar_calendario_deuda(prestamo, dia_pago=10, dia_corte=5)


# ── Cada cifra cuadra con su desglose ────────────────────


def test_cada_desglose_suma_su_cifra(pagos, cargos, patrimonio, servicio, ids_catalogo):
    """Lo que se enseña al hacer clic tiene que sumar lo que dice la tarjeta."""
    patrimonio.verificar_saldo(ids_catalogo["cuenta"], date(2026, 10, 1), 20_000.0)
    patrimonio.verificar_saldo(ids_catalogo["efectivo"], date(2026, 10, 1), 1_500.0)
    _deuda_de_tarjeta(servicio, patrimonio, ids_catalogo)
    patrimonio.fijar_exigible(ids_catalogo["tarjeta"], 3_000.0, declarado_el=HOY)
    _renta(cargos, ids_catalogo, proximo_cobro=date(2026, 10, 25))
    for monto, estado in ((25_000.0, None), (1_200.0, None), (5_000.0, "Pendiente")):
        servicio.registrar(
            fecha=date(2026, 10, 2),
            tipo=TipoMovimiento.INGRESO,
            monto=monto,
            categoria_id=ids_catalogo["sueldo"],
            cuenta_id=ids_catalogo["cuenta"],
            **({"estado": estado} if estado else {}),
        )
    servicio.registrar(
        fecha=date(2026, 10, 3),
        tipo=TipoMovimiento.GASTO,
        monto=450.0,
        categoria_id=ids_catalogo["restaurantes"],
        cuenta_id=ids_catalogo["cuenta"],
        fecha_pago=None,
    )

    resumen = pagos.resumen(OCTUBRE, HOY)
    ingresos = pagos.ingresos(OCTUBRE, HOY)
    confirmados = ingresos[ingresos["estado"] == str(EstadoMovimiento.CONFIRMADO)]

    assert confirmados["monto"].sum() == resumen.recibido == 26_200.0
    assert ingresos["monto"].sum() == resumen.ingresos
    assert patrimonio.activos_liquidos_detalle(HOY)["monto"].sum() == pytest.approx(
        resumen.disponible
    )
    assert pagos.por_pagar(OCTUBRE, HOY)["monto"].sum() == pytest.approx(
        resumen.por_pagar
    )


def test_una_cuenta_en_negativo_aparece_pero_no_suma(patrimonio, ids_catalogo):
    patrimonio.verificar_saldo(ids_catalogo["cuenta"], date(2026, 10, 1), -300.0)

    detalle = patrimonio.activos_liquidos_detalle(HOY).set_index("concepto")

    assert detalle.loc["Cuenta principal", "monto"] == 0
    assert "negativo" in detalle.loc["Cuenta principal", "nota"]


# ── Ingresos fijos y cuentas restringidas ────────────────


@pytest.fixture
def nomina(pagos, ids_catalogo):
    """Dos quincenas que llegan a la cuenta principal y dicen «NOMINA»."""
    for concepto, monto, dia in (
        ("Nómina · 1ª quincena", 4_300.0, 14),
        ("Nómina · 2ª quincena", 3_060.0, 30),
    ):
        pagos.crear_ingreso_fijo(
            concepto=concepto,
            monto=monto,
            dia=dia,
            categoria_id=ids_catalogo["sueldo"],
            cuenta_id=ids_catalogo["cuenta"],
            texto="NOMINA",
        )


def _ingreso(servicio, ids_catalogo, fecha, monto, descripcion, **campos):
    datos = {
        "fecha": fecha,
        "tipo": TipoMovimiento.INGRESO,
        "monto": monto,
        "categoria_id": ids_catalogo["sueldo"],
        "cuenta_id": ids_catalogo["cuenta"],
        "descripcion": descripcion,
    }
    datos.update(campos)
    return servicio.registrar(**datos)


def test_la_nomina_cuenta_aunque_no_se_haya_registrado(pagos, nomina):
    resumen = pagos.resumen(OCTUBRE, HOY)
    fijos = pagos.ingresos_fijos_del_mes(OCTUBRE, HOY)

    assert resumen.ingresos == 7_360.0
    assert resumen.recibido == 0
    assert fijos["estado"].tolist() == [
        str(EstadoIngreso.ATRASADO),
        str(EstadoIngreso.POR_RECIBIR),
    ]


def test_al_llegar_manda_el_monto_real(pagos, servicio, ids_catalogo, nomina):
    _ingreso(
        servicio, ids_catalogo, date(2026, 10, 14), 4_296.10, "PAGO DE NOMINA ITAM"
    )

    resumen = pagos.resumen(OCTUBRE, HOY)
    fijos = pagos.ingresos_fijos_del_mes(OCTUBRE, HOY).set_index("concepto")

    primera = fijos.loc["Nómina · 1ª quincena"]
    assert primera["estado"] == str(EstadoIngreso.RECIBIDO)
    assert primera["recibido"] == 4_296.10
    assert resumen.recibido == 4_296.10
    assert resumen.por_recibir == 3_060.0
    # No se cuenta dos veces: la que llegó reemplaza a la esperada.
    assert resumen.ingresos == pytest.approx(4_296.10 + 3_060.0)


def test_cada_quincena_se_queda_con_su_pago(pagos, servicio, ids_catalogo, nomina):
    _ingreso(servicio, ids_catalogo, date(2026, 10, 14), 4_300.0, "PAGO DE NOMINA")
    _ingreso(servicio, ids_catalogo, date(2026, 10, 28), 3_100.0, "PAGO DE NOMINA")

    fijos = pagos.ingresos_fijos_del_mes(OCTUBRE, date(2026, 10, 31))

    assert fijos["recibido"].tolist() == [4_300.0, 3_100.0]


def test_los_demas_ingresos_se_suman_encima(pagos, servicio, ids_catalogo, nomina):
    _ingreso(
        servicio,
        ids_catalogo,
        date(2026, 10, 5),
        400.0,
        "Reembolso comida",
        categoria_id=ids_catalogo["restaurantes"],
    )

    resumen = pagos.resumen(OCTUBRE, HOY)
    registrados = pagos.ingresos(OCTUBRE, HOY)

    assert resumen.ingresos == 7_360.0 + 400.0
    assert registrados["cuenta_como"].tolist() == [COMO_OTRO]


def test_la_nomina_apuntada_como_pendiente_no_se_cuenta_dos_veces(
    pagos, servicio, ids_catalogo, nomina
):
    _ingreso(
        servicio,
        ids_catalogo,
        date(2026, 10, 30),
        3_000.0,
        "NOMINA proyectada",
        estado=EstadoMovimiento.PENDIENTE,
    )

    resumen = pagos.resumen(OCTUBRE, HOY)
    segunda = pagos.ingresos_fijos_del_mes(OCTUBRE, HOY).iloc[1]

    assert segunda["monto"] == 3_000.0
    assert segunda["estado"] == str(EstadoIngreso.POR_RECIBIR)
    assert resumen.ingresos == 4_300.0 + 3_000.0


def test_lo_que_entra_a_una_cuenta_restringida_no_cuenta(
    pagos, servicio, catalogos, ids_catalogo, nomina
):
    fondo = catalogos.crear_cuenta("Fondo de ahorro", "Ahorro")
    catalogos.actualizar_cuenta(fondo, "Fondo de ahorro", "Ahorro", "", True, True)
    _ingreso(
        servicio,
        ids_catalogo,
        date(2026, 10, 30),
        2_491.58,
        "Aportación al fondo de ahorro",
        cuenta_id=fondo,
    )

    resumen = pagos.resumen(OCTUBRE, HOY)
    registrados = pagos.ingresos(OCTUBRE, HOY)

    assert resumen.ingresos == 7_360.0
    assert registrados["cuenta_como"].tolist() == [COMO_RESTRINGIDA]


def test_una_cuenta_restringida_no_es_disponible(patrimonio, catalogos, ids_catalogo):
    fondo = catalogos.crear_cuenta("Fondo de ahorro", "Ahorro")
    patrimonio.verificar_saldo(fondo, date(2026, 10, 1), 20_000.0)
    patrimonio.verificar_saldo(ids_catalogo["cuenta"], date(2026, 10, 1), 1_000.0)
    antes = patrimonio.activos_liquidos(HOY)

    catalogos.actualizar_cuenta(fondo, "Fondo de ahorro", "Ahorro", "", True, True)
    detalle = patrimonio.activos_liquidos_detalle(HOY).set_index("concepto")

    assert antes == 21_000.0
    assert patrimonio.activos_liquidos(HOY) == 1_000.0
    assert detalle.loc["Fondo de ahorro", "monto"] == 0
    assert "Restringida" in detalle.loc["Fondo de ahorro", "nota"]


def test_editar_una_cuenta_sin_decir_nada_no_quita_la_restriccion(catalogos):
    fondo = catalogos.crear_cuenta("Fondo de ahorro", "Ahorro")
    catalogos.actualizar_cuenta(fondo, "Fondo de ahorro", "Ahorro", "", True, True)
    catalogos.actualizar_cuenta(fondo, "Fondo ITAM", "Ahorro", "", True)

    cuentas = catalogos.listar_cuentas(solo_activas=False).set_index("id")

    assert cuentas.loc[fondo, "restringida"] == 1


def test_el_historial_de_un_ingreso_fijo(pagos, servicio, ids_catalogo, nomina):
    for mes, monto in ((7, 4_353.72), (8, 4_222.14), (9, 4_296.10)):
        _ingreso(servicio, ids_catalogo, date(2026, mes, 14), monto, "PAGO DE NOMINA")
    primera = int(pagos.ingresos_fijos().iloc[0]["id"])

    historial = pagos.historial_ingreso_fijo(primera, meses=6, hoy=HOY)

    assert historial["recibido"].tolist() == [4_296.10, 4_222.14, 4_353.72]


@pytest.mark.parametrize(
    ("campos", "mensaje"),
    [
        ({"concepto": " "}, "concepto"),
        ({"monto": -1.0}, "positivo"),
        ({"dia": 0}, "del 1 al 31"),
    ],
)
def test_un_ingreso_fijo_se_valida(pagos, campos, mensaje):
    datos = {"concepto": "Nómina", "monto": 100.0, "dia": 15} | campos
    with pytest.raises(ValueError, match=mensaje):
        pagos.crear_ingreso_fijo(**datos)


# ── Los cargos de los primeros días del mes siguiente ────

SEPTIEMBRE = date(2026, 9, 1)


def _renta_pagada(servicio, ids_catalogo, fecha):
    return servicio.registrar(
        fecha=fecha,
        tipo=TipoMovimiento.GASTO,
        monto=6500.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
        descripcion="Renta",
    )


def test_la_renta_del_1_se_pide_al_cerrar_el_mes_anterior(
    pagos, cargos, servicio, ids_catalogo
):
    _renta(cargos, ids_catalogo, proximo_cobro=date(2026, 10, 1))
    _renta_pagada(servicio, ids_catalogo, date(2026, 9, 1))

    calendario = pagos.calendario(SEPTIEMBRE, date(2026, 9, 29))
    lista = pagos.por_pagar(SEPTIEMBRE, date(2026, 9, 29))

    octubre = calendario[calendario["adelantado"]].iloc[0]
    assert octubre["fecha"] == date(2026, 10, 1)
    assert octubre["estado"] == str(EstadoPago.POR_PAGAR)
    assert lista["fecha_limite"].tolist() == [date(2026, 10, 1)]
    assert pagos.resumen(SEPTIEMBRE, date(2026, 9, 29)).fijos_pendientes == 6500.0


def test_la_renta_del_1_se_pide_desde_cualquier_dia_del_mes(
    pagos, cargos, servicio, ids_catalogo
):
    """No depende del día de hoy: se paga con el dinero de este mes."""
    _renta(cargos, ids_catalogo, proximo_cobro=date(2026, 10, 1))
    _renta_pagada(servicio, ids_catalogo, date(2026, 9, 1))

    calendario = pagos.calendario(SEPTIEMBRE, date(2026, 9, 15))

    assert calendario[calendario["adelantado"]]["fecha"].tolist() == [date(2026, 10, 1)]


def test_lo_que_vence_despues_del_1_se_queda_en_su_mes(pagos, cargos, ids_catalogo):
    """Apple Music el 6 de octubre no se pide en septiembre."""
    cargos.crear(
        servicio="Música", costo_por_cobro=75.0, proximo_cobro=date(2026, 10, 6)
    )

    calendario = pagos.calendario(SEPTIEMBRE, date(2026, 9, 29))

    assert not calendario["adelantado"].any()


def test_un_mes_que_ya_paso_sigue_pidiendo_la_renta_del_1(
    pagos, cargos, servicio, ids_catalogo
):
    """El 1 de octubre, septiembre sigue debiendo la renta de octubre."""
    _renta(cargos, ids_catalogo, proximo_cobro=date(2026, 10, 1))
    _renta_pagada(servicio, ids_catalogo, date(2026, 9, 1))

    hoy = date(2026, 10, 1)
    calendario = pagos.calendario(SEPTIEMBRE, hoy)
    octubre = calendario[calendario["adelantado"]].iloc[0]

    assert octubre["fecha"] == date(2026, 10, 1)
    assert octubre["estado"] != str(EstadoPago.PAGADO)
    assert pagos.por_pagar(SEPTIEMBRE, hoy)["fecha_limite"].tolist() == [
        date(2026, 10, 1)
    ]


def test_un_mes_que_ya_paso_muestra_la_renta_del_1_pagada(
    pagos, cargos, servicio, ids_catalogo
):
    _renta(cargos, ids_catalogo, proximo_cobro=date(2026, 10, 1))
    _renta_pagada(servicio, ids_catalogo, date(2026, 9, 1))
    octubre = _renta_pagada(servicio, ids_catalogo, date(2026, 10, 1))

    calendario = pagos.calendario(SEPTIEMBRE, date(2026, 10, 2)).set_index("fecha")

    assert calendario.loc[date(2026, 10, 1), "movimiento_id"] == octubre
    assert calendario.loc[date(2026, 10, 1), "estado"] == str(EstadoPago.PAGADO)


def test_la_renta_pagada_por_adelantado_queda_pagada(
    pagos, cargos, servicio, ids_catalogo
):
    """La de septiembre no se queda con el pago de octubre."""
    _renta(cargos, ids_catalogo, proximo_cobro=date(2026, 10, 1))
    septiembre = _renta_pagada(servicio, ids_catalogo, date(2026, 9, 1))
    octubre = _renta_pagada(servicio, ids_catalogo, date(2026, 9, 28))

    calendario = pagos.calendario(SEPTIEMBRE, date(2026, 9, 29)).set_index("fecha")

    assert calendario.loc[date(2026, 9, 1), "movimiento_id"] == septiembre
    assert calendario.loc[date(2026, 10, 1), "movimiento_id"] == octubre
    assert calendario.loc[date(2026, 10, 1), "estado"] == str(EstadoPago.PAGADO)


def test_la_anticipacion_se_puede_cambiar(pagos, cargos, servicio, ids_catalogo):
    """Con 1, la renta del 1 va en el mes anterior; con 0, en su propio mes."""
    _renta(cargos, ids_catalogo, proximo_cobro=date(2026, 10, 1))
    _renta_pagada(servicio, ids_catalogo, date(2026, 9, 1))
    cierre = date(2026, 9, 29)

    assert pagos.anticipacion_dias() == 1
    assert pagos.calendario(SEPTIEMBRE, cierre)["adelantado"].any()

    pagos.fijar_anticipacion(0)

    assert pagos.anticipacion_dias() == 0
    assert not pagos.calendario(SEPTIEMBRE, cierre)["adelantado"].any()


def test_la_anticipacion_se_valida(pagos):
    with pytest.raises(ValueError, match="0 a 31"):
        pagos.fijar_anticipacion(40)
