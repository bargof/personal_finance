from __future__ import annotations

import pytest

from finanzas.analytics.kpis import (
    PESO_AHORRO,
    PESO_DESEOS,
    PESO_FONDO,
    PESO_PRESUPUESTO,
    ResumenPeriodo,
    calcular_score,
    siguiente_mejor_accion,
)
from finanzas.domain.entities import ReglasFinancieras

# ═══════════════════════════════════════════════════════════
# Score financiero
#
# El caso de referencia es el mes de agosto 2026 del Excel
# original, que da 83 puntos.
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def reglas() -> ReglasFinancieras:
    """Reglas por defecto del Excel de origen."""
    return ReglasFinancieras(
        meta_ahorro_inversion=0.20,
        meses_fondo_emergencia=6,
        max_deseos=0.30,
        alerta_presupuesto=0.90,
    )


@pytest.fixture
def agosto_2026() -> ResumenPeriodo:
    """Cifras de agosto 2026 tal como salían del Excel."""
    return ResumenPeriodo(
        periodo="2026-08",
        ingresos=18000.0,
        gastos=11362.0,
        ahorro_inversion=3750.0,
        gasto_esencial=9638.0,
        presupuesto_total=10728.583333333334,
        activos_liquidos=22000.0,
        gasto_esencial_promedio_3m=9472.0,
    )


def test_score_reproduce_el_del_excel(agosto_2026, reglas):
    """El score total debe coincidir con el que calculaba la hoja."""
    assert calcular_score(agosto_2026, reglas).total == 83


def test_derivados_del_resumen(agosto_2026):
    """Las proporciones se calculan desde las cifras, no se capturan."""
    assert agosto_2026.disponible == pytest.approx(2888.0)
    assert agosto_2026.tasa_ahorro == pytest.approx(0.2083333, rel=1e-5)
    assert agosto_2026.pct_esencial == pytest.approx(0.8482661, rel=1e-5)
    assert agosto_2026.meses_fondo_emergencia == pytest.approx(2.3226351, rel=1e-5)


def test_score_perfecto_cuando_todo_esta_en_meta(reglas):
    """Cumplir las cuatro señales da los 100 puntos."""
    resumen = ResumenPeriodo(
        periodo="2026-08",
        ingresos=10000.0,
        gastos=5000.0,
        ahorro_inversion=2000.0,
        gasto_esencial=5000.0,
        presupuesto_total=5000.0,
        activos_liquidos=60000.0,
        gasto_esencial_promedio_3m=5000.0,
    )

    assert calcular_score(resumen, reglas).total == 100


def test_periodo_vacio_no_rompe_el_score(reglas):
    """Un mes sin datos da cero, no una división entre cero."""
    score = calcular_score(ResumenPeriodo(periodo="2026-01"), reglas)

    assert score.total == 0


def test_gastar_el_doble_del_presupuesto_parte_los_puntos(reglas):
    """La disciplina de presupuesto cae en proporción al exceso."""
    resumen = ResumenPeriodo(
        periodo="2026-08",
        ingresos=10000.0,
        gastos=10000.0,
        presupuesto_total=5000.0,
    )
    score = calcular_score(resumen, reglas)

    assert score.presupuesto == pytest.approx(PESO_PRESUPUESTO / 2)


def test_los_pesos_suman_cien():
    """El score se reparte entre cuatro componentes sobre 100."""
    total = PESO_AHORRO + PESO_PRESUPUESTO + PESO_FONDO + PESO_DESEOS

    assert total == 100


def test_desglose_expone_cada_componente(agosto_2026, reglas):
    """El usuario puede ver de dónde salió su número."""
    desglose = calcular_score(agosto_2026, reglas).desglose

    assert len(desglose) == 4
    assert desglose["maximo"].sum() == 100


# ═══════════════════════════════════════════════════════════
# Siguiente mejor acción
# ═══════════════════════════════════════════════════════════


def test_el_deficit_gana_a_cualquier_otra_recomendacion(reglas):
    """Gastar más de lo que entra es lo primero que hay que resolver."""
    resumen = ResumenPeriodo(periodo="2026-08", ingresos=10000.0, gastos=12000.0)
    accion = siguiente_mejor_accion(resumen, reglas, categorias_excedidas=3)

    assert "más de lo que ingresa" in accion


def test_sin_fondo_de_emergencia_se_prioriza_el_colchon(agosto_2026, reglas):
    """Con el fondo corto, la recomendación apunta ahí."""
    accion = siguiente_mejor_accion(agosto_2026, reglas, categorias_excedidas=6)

    assert "fondo de emergencia" in accion


def test_todo_en_orden_sugiere_subir_la_meta(reglas):
    """Cuando no hay nada que corregir, la acción es avanzar."""
    resumen = ResumenPeriodo(
        periodo="2026-08",
        ingresos=10000.0,
        gastos=5000.0,
        ahorro_inversion=2500.0,
        gasto_esencial=5000.0,
        presupuesto_total=5000.0,
        activos_liquidos=60000.0,
        gasto_esencial_promedio_3m=5000.0,
    )
    accion = siguiente_mejor_accion(resumen, reglas, categorias_excedidas=0)

    assert "Vas en ruta" in accion
