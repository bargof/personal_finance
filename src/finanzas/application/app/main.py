from __future__ import annotations

from pathlib import Path

import streamlit as st

from finanzas.config.logging import setup_logging
from finanzas.config.settings import settings
from finanzas.data.seed import preparar_base

APP_DIR = Path(__file__).resolve().parent
PAGINAS_DIR = APP_DIR / "app_pages"


@st.cache_resource
def _arrancar() -> bool:
    """
    Prepara logging y base de datos una sola vez por proceso.

    Deja la aplicación utilizable en un clon recién bajado del repositorio:
    si el archivo SQLite no existe, se crea y se siembra aquí mismo.
    """
    setup_logging(level=settings.log_level, log_file=str(settings.log_file_path))
    preparar_base()

    return True


def main() -> None:
    """Inicializa la navegación multipágina de Streamlit."""
    st.set_page_config(
        page_title="Finanzas personales",
        page_icon=":material/account_balance:",
        layout="wide",
    )
    _arrancar()

    dashboard = st.Page(
        APP_DIR / "streamlit_app.py",
        title="Dashboard",
        icon=":material/dashboard:",
        default=True,
    )
    movimientos = st.Page(
        PAGINAS_DIR / "movimientos.py",
        title="Movimientos",
        icon=":material/receipt_long:",
    )
    presupuesto = st.Page(
        PAGINAS_DIR / "presupuesto.py",
        title="Presupuesto",
        icon=":material/savings:",
    )
    metas = st.Page(
        PAGINAS_DIR / "metas.py",
        title="Metas",
        icon=":material/flag:",
    )
    patrimonio = st.Page(
        PAGINAS_DIR / "patrimonio.py",
        title="Patrimonio",
        icon=":material/account_balance_wallet:",
    )
    suscripciones = st.Page(
        PAGINAS_DIR / "suscripciones.py",
        title="Suscripciones",
        icon=":material/autorenew:",
    )
    analisis = st.Page(
        PAGINAS_DIR / "analisis.py",
        title="Análisis",
        icon=":material/insights:",
    )
    catalogos = st.Page(
        PAGINAS_DIR / "catalogos.py",
        title="Catálogos",
        icon=":material/category:",
    )
    configuracion = st.Page(
        PAGINAS_DIR / "configuracion.py",
        title="Configuración",
        icon=":material/settings:",
    )

    navegacion = st.navigation(
        {
            "Resumen": [dashboard, analisis],
            "Captura": [movimientos, presupuesto, metas],
            "Balance": [patrimonio, suscripciones],
            "Ajustes": [catalogos, configuracion],
        }
    )

    navegacion.run()
