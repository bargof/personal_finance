from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from finanzas.application.services.catalogos_service import CatalogosService
from finanzas.application.services.movimientos_service import MovimientosService
from finanzas.application.services.patrimonio_service import PatrimonioService
from finanzas.config.settings import settings
from finanzas.data.repositories.catalogos_repository import CatalogosRepository
from finanzas.data.seed import preparar_base
from finanzas.domain.enums import TipoCuenta, TipoMovimiento

# ═══════════════════════════════════════════════════════════
# El visor de cuentas del dashboard
#
# Enseña de un vistazo lo que hay en cada cuenta y lo que se
# debe en cada deuda. Es estado de hoy, no del periodo: tiene
# que estar aunque el mes elegido no tenga un solo movimiento.
# ═══════════════════════════════════════════════════════════

DASHBOARD = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "finanzas"
    / "application"
    / "app"
    / "streamlit_app.py"
)


@pytest.fixture
def base_ui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Base limpia a la que apunta la configuración global durante la prueba."""
    ruta = tmp_path / "ui.db"
    preparar_base(str(ruta))
    monkeypatch.setattr(type(settings), "db_path", property(lambda _self: ruta))
    # Los servicios y los cálculos se cachean por proceso; sin esto, la
    # prueba vería la base de la prueba anterior.
    st.cache_resource.clear()
    st.cache_data.clear()

    return str(ruta)


@pytest.fixture
def con_deuda(base_ui: str) -> str:
    """
    Una deuda modelada como cuenta de préstamo, con un abono hecho.

    Es el caso de una colegiatura que se paga en partes: el gasto se
    contó una vez, la deuda vive en su cuenta y cada abono es un
    traspaso hacia ella.
    """
    CatalogosService().crear_cuenta("Colegiatura", TipoCuenta.PRESTAMO, "Universidad")
    catalogos = CatalogosRepository(base_ui)
    cuentas = catalogos.mapa_nombre_id("cuentas")

    ayer = date.today() - timedelta(days=1)
    PatrimonioService().verificar_saldo(cuentas["Colegiatura"], ayer, 30_000.0)
    MovimientosService().registrar(
        fecha=date.today(),
        tipo=TipoMovimiento.TRANSFERENCIA,
        monto=5_000.0,
        categoria_id=catalogos.mapa_nombre_id("categorias")["Traspaso entre cuentas"],
        cuenta_id=cuentas["Cuenta principal"],
        cuenta_destino_id=cuentas["Colegiatura"],
        descripcion="Abono a la colegiatura",
    )

    return base_ui


def _dashboard() -> AppTest:
    prueba = AppTest.from_file(str(DASHBOARD), default_timeout=60)
    prueba.run()

    assert not prueba.exception, [str(e) for e in prueba.exception]
    return prueba


def _metricas(prueba: AppTest) -> dict[str, str]:
    return {metrica.label: metrica.value for metrica in prueba.metric}


def test_el_visor_lista_todas_las_cuentas(base_ui):
    """Cada cuenta del catálogo tiene su tarjeta, con o sin movimientos."""
    prueba = _dashboard()
    metricas = _metricas(prueba)

    nombres = set(CatalogosService().cuentas()["nombre"])
    assert nombres <= set(metricas)
    assert {"Disponible", "Deuda en cuentas", "Por pagar"} <= set(metricas)


def test_una_deuda_se_ve_en_positivo_y_baja_con_el_abono(con_deuda):
    """
    La cuenta de préstamo enseña lo que debes, y el abono la reduce.

    30.000 de deuda menos un abono de 5.000 dejan 25.000 debiendo; el
    abono no es un gasto, así que no aparece como tal.
    """
    prueba = _dashboard()
    metricas = _metricas(prueba)

    assert metricas["Colegiatura"] == "$25,000"
    assert metricas["Deuda en cuentas"] == "$25,000"
    assert metricas["Gastos"] == "$0"


def test_el_visor_esta_aunque_el_periodo_no_tenga_movimientos(con_deuda):
    """
    «Cuánto tengo» no depende del mes que se esté mirando.

    Antes, un periodo sin movimientos cortaba la página entera y no
    quedaba dónde ver las cuentas.
    """
    prueba = AppTest.from_file(str(DASHBOARD), default_timeout=60)
    prueba.session_state["periodo_activo"] = "2020-01"
    prueba.run()

    assert not prueba.exception, [str(e) for e in prueba.exception]
    assert "Colegiatura" in _metricas(prueba)
