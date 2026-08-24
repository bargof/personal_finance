from __future__ import annotations

import pandas as pd
import pytest
from pandera.errors import SchemaError

from finanzas.data.schemas import (
    validar_movimientos,
    validar_patrimonio,
    validar_resumen_mensual,
)

# ═══════════════════════════════════════════════════════════
# Contratos de datos
#
# Vigilan la frontera entre la base y los cálculos. Cada
# prueba rompe una regla a propósito para comprobar que el
# contrato la atrapa en vez de dejar pasar un dato absurdo.
# ═══════════════════════════════════════════════════════════


def _movimientos(**cambios) -> pd.DataFrame:
    """Un DataFrame de movimientos válido, con los cambios pedidos."""
    base = {
        "id": [1, 2],
        "fecha": pd.to_datetime(["2026-08-01", "2026-08-02"]),
        "periodo": ["2026-08", "2026-08"],
        "tipo": ["Ingreso", "Gasto"],
        "monto": [18000.0, 6500.0],
        "categoria": ["Sueldo", "Vivienda"],
        "cuenta": ["Cuenta principal", "Cuenta principal"],
        "necesidad": ["Esencial", "Esencial"],
        "naturaleza": ["Fijo", "Fijo"],
        "estado": ["Confirmado", "Confirmado"],
        "impacto_caja": [18000.0, -6500.0],
        "gasto_real": [0.0, 6500.0],
        "patrimonio_creado": [0.0, 0.0],
        "ingreso_real": [18000.0, 0.0],
    }
    base.update(cambios)

    return pd.DataFrame(base)


def test_un_movimiento_valido_pasa_el_contrato():
    """El caso feliz no debe disparar nada."""
    assert len(validar_movimientos(_movimientos())) == 2


def test_el_contrato_rechaza_un_monto_no_positivo():
    """El monto siempre se captura en positivo."""
    with pytest.raises(SchemaError):
        validar_movimientos(_movimientos(monto=[18000.0, -6500.0]))


def test_el_contrato_rechaza_un_tipo_fuera_del_catalogo():
    """El vocabulario de tipos está cerrado."""
    with pytest.raises(SchemaError):
        validar_movimientos(_movimientos(tipo=["Ingreso", "Regalo"]))


def test_el_contrato_rechaza_un_periodo_mal_formado():
    """El periodo debe poder ordenarse como texto: YYYY-MM."""
    with pytest.raises(SchemaError):
        validar_movimientos(_movimientos(periodo=["2026-08", "ago-2026"]))


def test_el_contrato_detecta_un_ingreso_con_impacto_incoherente():
    """Un ingreso tiene que sumar su monto completo a la caja."""
    with pytest.raises(SchemaError):
        validar_movimientos(_movimientos(impacto_caja=[9000.0, -6500.0]))


def test_el_contrato_detecta_una_transferencia_que_mueve_la_caja():
    """Mover dinero entre cuentas propias es neutro por definición."""
    datos = _movimientos(
        tipo=["Transferencia", "Gasto"],
        impacto_caja=[18000.0, -6500.0],
        ingreso_real=[0.0, 0.0],
    )

    with pytest.raises(SchemaError):
        validar_movimientos(datos)


def test_el_contrato_rechaza_un_gasto_real_negativo():
    """El gasto consumido nunca es negativo."""
    with pytest.raises(SchemaError):
        validar_movimientos(_movimientos(gasto_real=[0.0, -6500.0]))


# ═══════════════════════════════════════════════════════════
# Resumen mensual
# ═══════════════════════════════════════════════════════════


def test_el_resumen_mensual_no_admite_periodos_repetidos():
    """Un periodo duplicado significaría doble conteo en la tendencia."""
    duplicado = pd.DataFrame(
        {
            "periodo": ["2026-08", "2026-08"],
            "ingresos": [18000.0, 100.0],
            "gastos": [11362.0, 0.0],
            "ahorro_inversion": [3750.0, 0.0],
            "disponible": [2888.0, 100.0],
        }
    )

    with pytest.raises(SchemaError):
        validar_resumen_mensual(duplicado)


def test_un_resumen_mensual_valido_pasa():
    """El caso feliz del agregado por periodo."""
    valido = pd.DataFrame(
        {
            "periodo": ["2026-07", "2026-08"],
            "ingresos": [21640.0, 18000.0],
            "gastos": [11229.0, 11362.0],
            "ahorro_inversion": [3575.0, 3750.0],
            "disponible": [6836.0, 2888.0],
        }
    )

    assert len(validar_resumen_mensual(valido)) == 2


# ═══════════════════════════════════════════════════════════
# Patrimonio
# ═══════════════════════════════════════════════════════════


def test_el_patrimonio_exige_saldos_en_positivo():
    """Los pasivos también se capturan en positivo."""
    negativo = pd.DataFrame(
        {
            "nombre": ["Tarjeta"],
            "tipo": ["Pasivo"],
            "saldo": [-9000.0],
            "liquidez": ["No aplica"],
        }
    )

    with pytest.raises(SchemaError):
        validar_patrimonio(negativo)


def test_el_patrimonio_solo_admite_activo_o_pasivo():
    """No hay un tercer lado del balance."""
    invalido = pd.DataFrame(
        {
            "nombre": ["Cuenta"],
            "tipo": ["Mixto"],
            "saldo": [1000.0],
            "liquidez": ["Alta"],
        }
    )

    with pytest.raises(SchemaError):
        validar_patrimonio(invalido)
