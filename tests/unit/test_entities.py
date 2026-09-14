from __future__ import annotations

from datetime import date

import pytest

from finanzas.domain.entities import (
    CierreMensual,
    LineaPresupuesto,
    Meta,
    Movimiento,
    PosicionPatrimonial,
    Suscripcion,
)
from finanzas.domain.enums import (
    EstadoMeta,
    EstadoMovimiento,
    EstadoPresupuesto,
    FrecuenciaCobro,
    Liquidez,
    Necesidad,
    TipoMovimiento,
    TipoPatrimonio,
)

# ═══════════════════════════════════════════════════════════
# Movimiento: las columnas que en el Excel eran fórmulas
# ═══════════════════════════════════════════════════════════


def _movimiento(tipo: TipoMovimiento, **extras) -> Movimiento:
    """
    Arma un movimiento mínimo del tipo indicado, ya pagado.

    La fecha de pago va explícita porque sin ella el movimiento sería un
    devengado y no movería caja. El caso devengado tiene sus propias
    pruebas en `tests/integration/test_devengado.py`.
    """
    extras.setdefault("fecha_pago", date(2026, 8, 15))
    return Movimiento(
        fecha=date(2026, 8, 15),
        tipo=tipo,
        monto=1000.0,
        categoria_id=1,
        cuenta_id=1,
        **extras,
    )


@pytest.mark.parametrize(
    ("tipo", "esperado"),
    [
        (TipoMovimiento.INGRESO, 1000.0),
        (TipoMovimiento.GASTO, -1000.0),
        (TipoMovimiento.AHORRO, -1000.0),
        (TipoMovimiento.INVERSION, -1000.0),
        (TipoMovimiento.TRANSFERENCIA, 0.0),
    ],
)
def test_impacto_en_caja_depende_del_tipo(tipo, esperado):
    """El signo lo pone el tipo, no la captura."""
    assert _movimiento(tipo).impacto_caja == esperado


def test_transferencia_no_es_ingreso_ni_gasto():
    """Mover dinero entre cuentas propias no crea ni consume nada."""
    traspaso = _movimiento(TipoMovimiento.TRANSFERENCIA)

    assert traspaso.impacto_caja == 0.0
    assert traspaso.gasto_real == 0.0
    assert traspaso.patrimonio_creado == 0.0


def test_gasto_pendiente_no_cuenta_como_gasto_real():
    """
    Sólo lo confirmado alimenta presupuesto e indicadores.

    La caja sí se mueve si el movimiento tiene fecha de pago: `estado`
    dice si el gasto ocurrió y `fecha_pago` si el dinero salió, y son
    preguntas independientes.
    """
    pendiente = _movimiento(TipoMovimiento.GASTO, estado=EstadoMovimiento.PENDIENTE)

    assert pendiente.gasto_real == 0.0
    assert pendiente.impacto_caja == -1000.0


def test_ahorro_confirmado_crea_patrimonio():
    """Ahorro e inversión confirmados suman al patrimonio construido."""
    ahorro = _movimiento(TipoMovimiento.AHORRO)
    inversion = _movimiento(TipoMovimiento.INVERSION)

    assert ahorro.patrimonio_creado == 1000.0
    assert inversion.patrimonio_creado == 1000.0


def test_periodo_sale_de_la_fecha():
    """El periodo es el mes de la fecha, en formato YYYY-MM."""
    assert _movimiento(TipoMovimiento.GASTO).periodo == "2026-08"


# ═══════════════════════════════════════════════════════════
# Presupuesto
# ═══════════════════════════════════════════════════════════


def test_presupuesto_manual_gana_al_promedio():
    """Si hay monto manual, el promedio con recorte se ignora."""
    linea = LineaPresupuesto(
        periodo="2026-08",
        categoria_id=1,
        monto_manual=2000.0,
        promedio_3m=1500.0,
        pct_recorte=0.5,
    )

    assert linea.presupuesto_activo == 2000.0


def test_sin_monto_manual_se_usa_el_promedio_con_recorte():
    """El recorte baja el promedio histórico hacia la meta."""
    linea = LineaPresupuesto(
        periodo="2026-08",
        categoria_id=1,
        promedio_3m=1000.0,
        pct_recorte=0.15,
    )

    assert linea.presupuesto_activo == pytest.approx(850.0)


@pytest.mark.parametrize(
    ("gasto", "esperado"),
    [
        (500.0, EstadoPresupuesto.EN_ORDEN),
        (950.0, EstadoPresupuesto.ATENCION),
        (1200.0, EstadoPresupuesto.EXCEDIDO),
    ],
)
def test_semaforo_del_presupuesto(gasto, esperado):
    """El umbral de alerta marca la categoría antes de que se exceda."""
    linea = LineaPresupuesto(
        periodo="2026-08",
        categoria_id=1,
        monto_manual=1000.0,
        gasto_del_mes=gasto,
    )

    assert linea.estado(umbral_alerta=0.9) == esperado


def test_categoria_sin_presupuesto_pero_con_gasto_pide_configurarse():
    """Gastar sin presupuesto es una señal, no un estado neutro."""
    linea = LineaPresupuesto(periodo="2026-08", categoria_id=1, gasto_del_mes=300.0)

    assert linea.estado(umbral_alerta=0.9) == EstadoPresupuesto.CONFIGURAR


# ═══════════════════════════════════════════════════════════
# Metas
# ═══════════════════════════════════════════════════════════


def test_aporte_necesario_reparte_lo_que_falta():
    """Lo que falta, dividido entre los meses que quedan."""
    meta = Meta(
        objetivo="Fondo",
        monto_meta=60000.0,
        acumulado=10000.0,
        fecha_limite=date(2027, 1, 1),
    )
    hoy = date(2026, 9, 1)

    assert meta.meses_restantes(hoy) == 4
    assert meta.aporte_necesario(hoy) == pytest.approx(12500.0)


def test_meta_en_ruta_cuando_el_aporte_alcanza():
    """Aportar al menos lo necesario mantiene la meta en ruta."""
    meta = Meta(
        objetivo="Viaje",
        monto_meta=12000.0,
        acumulado=0.0,
        aporte_mensual_planeado=3000.0,
        fecha_limite=date(2027, 1, 1),
    )

    assert meta.estado(date(2026, 9, 1)) == EstadoMeta.EN_RUTA


def test_meta_pide_ajuste_cuando_el_aporte_se_queda_corto():
    """Si el aporte planeado no alcanza, el estado lo dice."""
    meta = Meta(
        objetivo="Viaje",
        monto_meta=12000.0,
        aporte_mensual_planeado=500.0,
        fecha_limite=date(2027, 1, 1),
    )

    assert meta.estado(date(2026, 9, 1)) == EstadoMeta.AJUSTAR


def test_meta_lograda_ignora_la_fecha_vencida():
    """Alcanzar el monto gana sobre haber pasado la fecha."""
    meta = Meta(
        objetivo="Fondo",
        monto_meta=1000.0,
        acumulado=1000.0,
        fecha_limite=date(2020, 1, 1),
    )

    assert meta.estado(date(2026, 9, 1)) == EstadoMeta.LOGRADA


# ═══════════════════════════════════════════════════════════
# Suscripciones y patrimonio
# ═══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("frecuencia", "mensual"),
    [
        (FrecuenciaCobro.MENSUAL, 120.0),
        (FrecuenciaCobro.TRIMESTRAL, 40.0),
        (FrecuenciaCobro.ANUAL, 10.0),
    ],
)
def test_costo_mensual_normaliza_la_frecuencia(frecuencia, mensual):
    """Un cobro anual y uno mensual sólo se comparan normalizados."""
    suscripcion = Suscripcion(
        servicio="Streaming", costo_por_cobro=120.0, frecuencia=frecuencia
    )

    assert suscripcion.costo_mensual == pytest.approx(mensual)


def test_suscripcion_esencial_no_es_candidata_a_cancelar():
    """Sólo lo discrecional con renovación automática entra a revisión."""
    esencial = Suscripcion(
        servicio="Almacenamiento",
        costo_por_cobro=49.0,
        necesidad=Necesidad.ESENCIAL,
    )

    assert not esencial.candidato_a_cancelar


def test_pasivo_resta_del_patrimonio():
    """El pasivo se captura en positivo y el cálculo le pone el signo."""
    deuda = PosicionPatrimonial(
        nombre="Tarjeta",
        tipo=TipoPatrimonio.PASIVO,
        saldo=9000.0,
        liquidez=Liquidez.NO_APLICA,
    )

    assert deuda.aporte_a_patrimonio == -9000.0


def test_cierre_calcula_patrimonio_neto():
    """Activos menos deudas, sin depender de una fórmula externa."""
    cierre = CierreMensual(
        periodo="2026-08",
        efectivo=12000.0,
        ahorro=10300.0,
        inversiones=18050.0,
        otros_activos=8000.0,
        deudas=8200.0,
    )

    assert cierre.patrimonio_neto == pytest.approx(40150.0)
