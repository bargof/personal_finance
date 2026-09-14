from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from finanzas.config.settings import settings
from finanzas.data.excel_import import importar_excel
from finanzas.data.seed import preparar_base

# ═══════════════════════════════════════════════════════════
# Humo de la interfaz
#
# Cada página se ejecuta de principio a fin contra una base
# real. Atrapa lo que el linter no ve: una columna que ya no
# existe, un selector sin opciones, un import roto.
# ═══════════════════════════════════════════════════════════

APP_DIR = (
    Path(__file__).resolve().parents[2] / "src" / "finanzas" / "application" / "app"
)

PAGINAS = (
    "streamlit_app.py",
    "app_pages/movimientos.py",
    "app_pages/importar.py",
    "app_pages/presupuesto.py",
    "app_pages/metas.py",
    "app_pages/proyectos.py",
    "app_pages/deseos.py",
    "app_pages/patrimonio.py",
    "app_pages/suscripciones.py",
    "app_pages/analisis.py",
    "app_pages/catalogos.py",
    "app_pages/configuracion.py",
)


@pytest.fixture(scope="module")
def base_con_datos(tmp_path_factory) -> str:
    """
    Base poblada con el Excel original si está disponible.

    Sin el Excel, las páginas se prueban igual contra una base sembrada:
    el estado vacío también tiene que renderizar.
    """
    ruta = tmp_path_factory.mktemp("ui") / "ui.db"
    preparar_base(str(ruta))

    if settings.excel_source_path.exists():
        importar_excel(settings.excel_source_path, db_path=str(ruta))

    return str(ruta)


@pytest.fixture(autouse=True)
def _apuntar_a_la_base(base_con_datos: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirige la configuración global a la base de prueba."""
    monkeypatch.setattr(
        type(settings), "db_path", property(lambda _self: Path(base_con_datos))
    )


@pytest.mark.parametrize("pagina", PAGINAS)
def test_la_pagina_renderiza_sin_excepciones(pagina: str) -> None:
    """Cada página se ejecuta completa y no deja una excepción en pantalla."""
    prueba = AppTest.from_file(str(APP_DIR / pagina), default_timeout=60)
    prueba.run()

    assert not prueba.exception, [str(error) for error in prueba.exception]


def test_el_dashboard_muestra_sus_indicadores() -> None:
    """El dashboard llega hasta la fila de métricas, no sólo al título."""
    prueba = AppTest.from_file(str(APP_DIR / "streamlit_app.py"), default_timeout=60)
    prueba.run()

    assert not prueba.exception
    etiquetas = {metrica.label for metrica in prueba.metric}

    assert {"Ingresos", "Gastos", "Score financiero"} <= etiquetas
