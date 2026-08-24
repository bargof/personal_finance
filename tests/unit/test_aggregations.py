from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from finanzas.analytics import aggregations as agg

# ═══════════════════════════════════════════════════════════
# Utilidades de periodo
# ═══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("periodo", "meses", "esperado"),
    [
        ("2026-08", 1, "2026-09"),
        ("2026-08", -1, "2026-07"),
        ("2026-12", 1, "2027-01"),
        ("2026-01", -1, "2025-12"),
        ("2026-08", -12, "2025-08"),
    ],
)
def test_desplazar_periodo_cruza_el_fin_de_anio(periodo, meses, esperado):
    """Sumar meses debe respetar el cambio de año en ambos sentidos."""
    assert agg.desplazar_periodo(periodo, meses) == esperado


def test_etiqueta_periodo_en_espanol():
    """El periodo se muestra legible, no como código."""
    assert agg.etiqueta_periodo("2026-08") == "agosto 2026"


def test_etiqueta_periodo_tolera_basura():
    """Un valor inesperado se devuelve tal cual en vez de reventar."""
    assert agg.etiqueta_periodo("no-es-un-periodo") == "no-es-un-periodo"


# ═══════════════════════════════════════════════════════════
# Movimientos de ejemplo
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def movimientos() -> pd.DataFrame:
    """Cuatro gastos de agosto con mezcla de necesidad y planeación."""
    return pd.DataFrame(
        {
            "fecha": pd.to_datetime(
                ["2026-08-02", "2026-08-05", "2026-08-05", "2026-08-20"]
            ),
            "dia": [2, 5, 5, 20],
            "categoria": ["Vivienda", "Restaurantes", "Transporte", "Restaurantes"],
            "subcategoria": ["Renta/Hipoteca", "Comida fuera", "", "Café/Snacks"],
            "cuenta": ["Cuenta principal", "Tarjeta crédito", "Efectivo", "Efectivo"],
            "medio_pago": ["Transferencia", "Crédito", "", "Efectivo"],
            "necesidad": ["Esencial", "Deseo", "Esencial", "Deseo"],
            "naturaleza": ["Fijo", "Variable", "Variable", "Variable"],
            "planeado": [True, False, True, False],
            "monto": [6500.0, 420.0, 300.0, 150.0],
            "gasto_real": [6500.0, 420.0, 300.0, 150.0],
            "impacto_caja": [-6500.0, -420.0, -300.0, -150.0],
        }
    )


def test_gasto_por_categoria_ordena_y_reparte(movimientos):
    """Las categorías salen de mayor a menor con su peso relativo."""
    resultado = agg.gasto_por_categoria(movimientos)

    assert list(resultado["categoria"]) == ["Vivienda", "Restaurantes", "Transporte"]
    assert resultado.iloc[0]["gasto"] == 6500.0
    assert resultado["pct_del_total"].sum() == pytest.approx(1.0)


def test_mezcla_por_necesidad(movimientos):
    """El gasto se reparte entre esencial y deseo."""
    resultado = agg.mezcla_de_gasto(movimientos, "necesidad")
    por_necesidad = dict(zip(resultado["necesidad"], resultado["gasto"], strict=True))

    assert por_necesidad["Esencial"] == 6800.0
    assert por_necesidad["Deseo"] == 570.0


def test_mezcla_rellena_el_medio_de_pago_vacio(movimientos):
    """Un medio de pago en blanco se etiqueta en vez de desaparecer."""
    resultado = agg.mezcla_de_gasto(movimientos, "medio_pago")

    assert "Sin especificar" in set(resultado["medio_pago"])


def test_mezcla_rechaza_una_dimension_desconocida(movimientos):
    """Pedir un corte que no existe es un error del programador."""
    with pytest.raises(ValueError, match="Dimensión no soportada"):
        agg.mezcla_de_gasto(movimientos, "color_favorito")


def test_fugas_separa_microgastos_de_lo_no_planeado(movimientos):
    """Cada bolsa de fuga se cuenta por su propio criterio."""
    resultado = agg.fugas(movimientos, umbral_gasto_pequeno=200.0)

    assert resultado["microgastos_monto"] == 150.0
    assert resultado["microgastos_cantidad"] == 1
    assert resultado["gasto_no_planeado"] == 570.0
    assert resultado["deseos_no_planeados"] == 570.0


def test_fugas_en_un_periodo_vacio():
    """Sin movimientos, todas las bolsas valen cero."""
    resultado = agg.fugas(pd.DataFrame(), umbral_gasto_pequeno=200.0)

    assert resultado["microgastos_monto"] == 0.0
    assert resultado["microgastos_cantidad"] == 0


def test_calendario_incluye_los_dias_sin_gasto(movimientos):
    """Los días en cero son la señal, así que aparecen explícitos."""
    calendario = agg.calendario_de_gasto(movimientos, "2026-08")

    assert len(calendario) == 31
    assert calendario.loc[calendario["dia"] == 5, "gasto"].iloc[0] == 720.0
    assert bool(calendario.loc[calendario["dia"] == 1, "sin_gasto"].iloc[0])
    assert int(calendario["sin_gasto"].sum()) == 28


def test_calendario_de_febrero_bisiesto():
    """El número de días sale del calendario, no de una constante."""
    calendario = agg.calendario_de_gasto(pd.DataFrame(), "2028-02")

    assert len(calendario) == 29


def test_flujo_por_cuenta_separa_entradas_y_salidas(movimientos):
    """Entradas y salidas se cuentan por separado antes del neto."""
    resultado = agg.flujo_por_cuenta(movimientos)
    efectivo = resultado[resultado["cuenta"] == "Efectivo"].iloc[0]

    assert efectivo["salidas"] == 450.0
    assert efectivo["entradas"] == 0.0
    assert efectivo["flujo_neto"] == -450.0


# ═══════════════════════════════════════════════════════════
# Tendencia
# ═══════════════════════════════════════════════════════════


def test_tendencia_rellena_los_meses_sin_datos():
    """Un mes sin movimientos vale cero, no desaparece de la serie."""
    resumen = pd.DataFrame(
        {
            "periodo": ["2026-07", "2026-08"],
            "ingresos": [18000.0, 18000.0],
            "gastos": [11229.0, 11362.0],
            "ahorro_inversion": [3575.0, 3750.0],
            "disponible": [3196.0, 2888.0],
            "movimientos": [13, 12],
        }
    )

    tendencia = agg.tendencia_mensual(resumen, "2026-08", meses=12)

    assert len(tendencia) == 12
    assert tendencia.iloc[-1]["periodo"] == "2026-08"
    assert tendencia.iloc[0]["periodo"] == "2025-09"
    assert tendencia.iloc[0]["ingresos"] == 0.0


def test_tendencia_sin_ningun_dato():
    """Sin histórico, la serie existe igual y vale cero."""
    tendencia = agg.tendencia_mensual(pd.DataFrame(), "2026-08", meses=6)

    assert len(tendencia) == 6
    assert tendencia["gastos"].sum() == 0.0


def test_periodo_de_una_fecha():
    """Una fecha se convierte en su periodo mensual."""
    assert agg.periodo_de(date(2026, 8, 23)) == "2026-08"
