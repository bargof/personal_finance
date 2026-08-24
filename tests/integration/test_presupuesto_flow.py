from __future__ import annotations

from datetime import date

import pytest

from finanzas.application.services.analytics_service import AnalyticsService
from finanzas.application.services.movimientos_service import MovimientosService
from finanzas.application.services.presupuesto_service import PresupuestoService
from finanzas.data.repositories.catalogos_repository import CatalogosRepository
from finanzas.data.repositories.movimientos_repository import MovimientosRepository
from finanzas.data.repositories.patrimonio_repository import PatrimonioRepository
from finanzas.data.repositories.presupuesto_repository import PresupuestoRepository
from finanzas.data.repositories.suscripciones_repository import SuscripcionesRepository
from finanzas.domain.enums import TipoMovimiento

# ═══════════════════════════════════════════════════════════
# Presupuesto y tablero, contra movimientos reales
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def presupuesto(db_path: str) -> PresupuestoService:
    """Servicio de presupuesto sobre la base de prueba."""
    return PresupuestoService(
        PresupuestoRepository(db_path), CatalogosRepository(db_path)
    )


@pytest.fixture
def analytics(db_path: str, presupuesto: PresupuestoService) -> AnalyticsService:
    """Servicio de analítica cableado a la base de prueba."""
    return AnalyticsService(
        movimientos=MovimientosRepository(db_path),
        presupuesto=presupuesto,
        patrimonio=PatrimonioRepository(db_path),
        suscripciones=SuscripcionesRepository(db_path),
        catalogos=CatalogosRepository(db_path),
    )


@pytest.fixture
def con_historial(
    movimientos: MovimientosRepository, ids_catalogo: dict[str, int]
) -> MovimientosService:
    """
    Tres meses previos con 900 de gasto mensual en Restaurantes,
    más 1000 en el mes en curso.
    """
    servicio = MovimientosService(movimientos)

    for mes in (5, 6, 7):
        servicio.registrar(
            fecha=date(2026, mes, 10),
            tipo=TipoMovimiento.GASTO,
            monto=900.0,
            categoria_id=ids_catalogo["restaurantes"],
            cuenta_id=ids_catalogo["cuenta"],
        )

    servicio.registrar(
        fecha=date(2026, 8, 10),
        tipo=TipoMovimiento.GASTO,
        monto=1000.0,
        categoria_id=ids_catalogo["restaurantes"],
        cuenta_id=ids_catalogo["cuenta"],
    )

    return servicio


def _linea(tablero, categoria: str):
    """Extrae la fila de una categoría del tablero de presupuesto."""
    return tablero[tablero["categoria"] == categoria].iloc[0]


def test_el_promedio_sale_de_los_tres_meses_previos(con_historial, presupuesto):
    """Sin monto manual, el punto de partida es el gasto real reciente."""
    fila = _linea(presupuesto.tablero("2026-08"), "Restaurantes")

    assert fila["promedio_3m"] == pytest.approx(900.0)
    assert fila["presupuesto_activo"] == pytest.approx(900.0)
    assert fila["gasto_del_mes"] == pytest.approx(1000.0)
    assert fila["estado"] == "Excedido"


def test_el_recorte_baja_el_presupuesto(con_historial, presupuesto, ids_catalogo):
    """El % de recorte aplica sobre el promedio, no sobre el gasto."""
    presupuesto._repo.guardar_linea(
        "2026-08", ids_catalogo["restaurantes"], monto_manual=0.0, pct_recorte=0.15
    )

    fila = _linea(presupuesto.tablero("2026-08"), "Restaurantes")

    assert fila["presupuesto_activo"] == pytest.approx(765.0)


def test_el_monto_manual_manda(con_historial, presupuesto, ids_catalogo):
    """Un monto manual mayor que cero ignora promedio y recorte."""
    presupuesto._repo.guardar_linea(
        "2026-08", ids_catalogo["restaurantes"], monto_manual=1500.0, pct_recorte=0.5
    )

    fila = _linea(presupuesto.tablero("2026-08"), "Restaurantes")

    assert fila["presupuesto_activo"] == pytest.approx(1500.0)
    assert fila["disponible"] == pytest.approx(500.0)
    assert fila["estado"] == "En orden"


def test_el_umbral_de_alerta_marca_antes_del_cien_por_ciento(
    con_historial, presupuesto, ids_catalogo
):
    """Atención llega antes que Excedido, para poder reaccionar."""
    presupuesto._repo.guardar_linea(
        "2026-08", ids_catalogo["restaurantes"], monto_manual=1050.0, pct_recorte=0.0
    )

    fila = _linea(presupuesto.tablero("2026-08", umbral_alerta=0.9), "Restaurantes")

    assert fila["estado"] == "Atención"


def test_copiar_el_presupuesto_a_otro_periodo(con_historial, presupuesto, ids_catalogo):
    """Copiar evita recapturar el mismo plan cada mes."""
    presupuesto._repo.guardar_linea(
        "2026-08", ids_catalogo["restaurantes"], monto_manual=1200.0, pct_recorte=0.0
    )

    presupuesto.copiar_desde("2026-08", "2026-09")
    fila = _linea(presupuesto.tablero("2026-09"), "Restaurantes")

    assert fila["monto_manual"] == pytest.approx(1200.0)


def test_copiar_al_mismo_periodo_es_un_error(presupuesto):
    """Copiar un periodo sobre sí mismo no tiene sentido."""
    with pytest.raises(ValueError, match="el mismo"):
        presupuesto.copiar_desde("2026-08", "2026-08")


def test_guardar_recorta_los_valores_fuera_de_rango(
    con_historial, presupuesto, ids_catalogo
):
    """Un recorte negativo o mayor que 1 se ajusta en vez de romper."""
    tablero = presupuesto.tablero("2026-08")
    tablero.loc[tablero["categoria"] == "Restaurantes", "pct_recorte"] = 2.5
    tablero.loc[tablero["categoria"] == "Restaurantes", "monto_manual"] = -100.0

    presupuesto.guardar("2026-08", tablero)
    fila = _linea(presupuesto.tablero("2026-08"), "Restaurantes")

    assert fila["pct_recorte"] == pytest.approx(1.0)
    assert fila["monto_manual"] == pytest.approx(0.0)


def test_guardar_exige_las_columnas_editables(presupuesto):
    """Guardar un DataFrame incompleto falla con un mensaje claro."""
    import pandas as pd

    with pytest.raises(ValueError, match="Faltan columnas"):
        presupuesto.guardar("2026-08", pd.DataFrame({"categoria": ["Vivienda"]}))


# ═══════════════════════════════════════════════════════════
# Tablero completo
# ═══════════════════════════════════════════════════════════


def test_el_tablero_reune_todas_las_piezas(con_historial, analytics):
    """El dashboard recibe resumen, score, tendencia y cortes ya listos."""
    tablero = analytics.tablero("2026-08")

    assert tablero.periodo == "2026-08"
    assert tablero.etiqueta == "agosto 2026"
    assert tablero.hay_datos
    assert tablero.resumen.gastos == pytest.approx(1000.0)
    assert len(tablero.tendencia) == 12
    assert tablero.categoria_mayor_gasto == ("Restaurantes", 1000.0)
    assert tablero.accion_sugerida


def test_el_tablero_de_un_periodo_vacio_no_rompe(analytics):
    """Un mes sin movimientos devuelve un tablero vacío, no una excepción."""
    tablero = analytics.tablero("2020-01")

    assert not tablero.hay_datos
    assert tablero.score.total == 0
    assert tablero.categoria_mayor_gasto is None


def test_las_categorias_excedidas_se_cuentan(con_historial, analytics):
    """El conteo que muestra el dashboard sale del mismo tablero."""
    tablero = analytics.tablero("2026-08")

    assert tablero.categorias_excedidas == 1
