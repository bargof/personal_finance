from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from finanzas.application.services.movimientos_service import MovimientosService
from finanzas.config.settings import settings
from finanzas.data.repositories.catalogos_repository import CatalogosRepository
from finanzas.data.seed import preparar_base
from finanzas.domain.enums import TipoMovimiento

# ═══════════════════════════════════════════════════════════
# La tabla de «Explorar y editar», filtrada desde la pantalla
#
# Los filtros están probados en el servicio; esto fija que la
# página los conecte. Un control que no llega a la consulta no
# falla: sólo no filtra.
# ═══════════════════════════════════════════════════════════

MOVIMIENTOS = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "finanzas"
    / "application"
    / "app"
    / "app_pages"
    / "movimientos.py"
)


@pytest.fixture
def base_ui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Base limpia a la que apunta la configuración global durante la prueba."""
    ruta = tmp_path / "ui.db"
    preparar_base(str(ruta))
    monkeypatch.setattr(type(settings), "db_path", property(lambda _self: ruta))
    # Los servicios y las búsquedas se cachean por proceso; sin esto, la
    # prueba vería la base de la prueba anterior.
    st.cache_resource.clear()
    st.cache_data.clear()

    return str(ruta)


@pytest.fixture
def dos_gastos(base_ui: str) -> list[int]:
    """Dos gastos del mismo día por importes distintos, y sus ids."""
    catalogos = CatalogosRepository(base_ui)
    categoria = catalogos.mapa_nombre_id("categorias")["Compras"]
    cuenta = catalogos.mapa_nombre_id("cuentas")["Cuenta principal"]

    return [
        MovimientosService().registrar(
            fecha=date(2026, 7, 27),
            tipo=TipoMovimiento.GASTO,
            monto=importe,
            categoria_id=categoria,
            cuenta_id=cuenta,
            descripcion=descripcion,
        )
        for importe, descripcion in ((2_398.99, "Audífonos"), (120.0, "Taxi"))
    ]


def _control(prueba: AppTest, tipo: str, etiqueta: str):
    """Localiza un control por su etiqueta."""
    encontrados = [w for w in getattr(prueba, tipo) if w.label == etiqueta]
    assert encontrados, f"no hay {tipo} con etiqueta «{etiqueta}»"
    return encontrados[0]


def _campo(prueba: AppTest, tipo: str, clave: str):
    """
    Localiza un control por su clave.

    La pestaña de capturar se dibuja siempre, así que por etiqueta hay
    dos «Descripción»: la del movimiento nuevo y la del que se edita.
    """
    encontrados = [w for w in getattr(prueba, tipo) if w.key == clave]
    assert encontrados, f"no hay {tipo} con clave «{clave}»"
    return encontrados[0]


def _elemento_tabla(prueba: AppTest):
    """
    La tabla de movimientos.

    Se reconoce por sus columnas: el editor de productos del detalle
    también es un dataframe, y va sin filtrar por delante.
    """
    tablas = [
        elemento
        for elemento in prueba.dataframe
        if "descripcion_banco" in list(getattr(elemento.value, "columns", []))
    ]
    assert tablas, "no hay tabla de movimientos en pantalla"
    return tablas[0]


def _tabla(prueba: AppTest):
    """Lo que la tabla de movimientos enseña."""
    return _elemento_tabla(prueba).value


def _columnas_editables(prueba: AppTest) -> set[str]:
    """Las columnas que la tabla deja tocar."""
    configuradas = json.loads(_elemento_tabla(prueba).proto.columns)

    return {
        nombre
        for nombre, config in configuradas.items()
        if not config.get("disabled") and nombre != "_index"
    }


def _hay_tabla(prueba: AppTest) -> bool:
    """Si la tabla de movimientos está en pantalla."""
    return any(
        "descripcion_banco" in list(getattr(elemento.value, "columns", []))
        for elemento in prueba.dataframe
    )


def _en_detalle(ids: list[int], indice: int = 0) -> AppTest:
    """La página con un recorrido abierto sobre esos movimientos."""
    prueba = AppTest.from_file(str(MOVIMIENTOS), default_timeout=60)
    # Las claves son el contrato de la pestaña con la tabla: abrir el
    # detalle es dejar dicho qué se revisa y por cuál se va.
    prueba.session_state["edicion_movimientos"] = ids
    prueba.session_state["indice_edicion"] = indice
    prueba.run()

    assert not prueba.exception, [str(e) for e in prueba.exception]
    return prueba


def test_el_mismo_monto_arriba_y_abajo_deja_solo_ese(dos_gastos):
    """
    El atajo para rastrear un cobro concreto: su importe exacto.

    Es lo que hace falta para perseguir un posible duplicado sin pasar
    por el aviso que lo señaló.
    """
    prueba = AppTest.from_file(str(MOVIMIENTOS), default_timeout=60)
    prueba.run()

    _control(prueba, "date_input", "Rango de fechas").set_value(
        (date(2026, 7, 1), date(2026, 7, 31))
    ).run()
    _control(prueba, "number_input", "Monto desde").set_value(2_398.99).run()
    _control(prueba, "number_input", "Monto hasta").set_value(2_398.99).run()

    assert not prueba.exception, [str(e) for e in prueba.exception]
    assert list(_tabla(prueba)["descripcion"]) == ["Audífonos"]


def test_cada_extremo_del_monto_filtra_por_su_cuenta(dos_gastos):
    """Sólo el mínimo, o sólo el máximo, también acotan."""
    prueba = AppTest.from_file(str(MOVIMIENTOS), default_timeout=60)
    prueba.run()

    _control(prueba, "date_input", "Rango de fechas").set_value(
        (date(2026, 7, 1), date(2026, 7, 31))
    ).run()

    _control(prueba, "number_input", "Monto desde").set_value(1_000.0).run()
    assert list(_tabla(prueba)["descripcion"]) == ["Audífonos"]

    _control(prueba, "number_input", "Monto desde").set_value(None).run()
    _control(prueba, "number_input", "Monto hasta").set_value(1_000.0).run()
    assert list(_tabla(prueba)["descripcion"]) == ["Taxi"]


def test_sin_monto_no_filtra_nada(dos_gastos):
    """Los dos vacíos dejan la tabla como estaba."""
    prueba = AppTest.from_file(str(MOVIMIENTOS), default_timeout=60)
    prueba.run()

    _control(prueba, "date_input", "Rango de fechas").set_value(
        (date(2026, 7, 1), date(2026, 7, 31))
    ).run()

    assert len(_tabla(prueba)) == 2


# ── El recorrido, uno por pantalla ───────────────────────


def test_abrir_en_detalle_quita_la_tabla(dos_gastos):
    """
    Con un recorrido abierto, la pestaña es el recorrido.

    La tabla y sus filtros estorbarían encima de lo que se corrige, así
    que no se dibujan: lo mismo que hace el asistente de importación.
    """
    prueba = _en_detalle(dos_gastos)

    assert not _hay_tabla(prueba)
    etiquetas = {boton.label for boton in prueba.button}
    assert "Guardar y siguiente" in etiquetas
    assert "Volver a la tabla" in etiquetas
    assert not any(w.label == "Rango de fechas" for w in prueba.date_input)


def test_el_ultimo_del_recorrido_termina_en_vez_de_seguir(dos_gastos):
    """Con uno solo, o en el último, no hay siguiente al que ir."""
    prueba = _en_detalle(dos_gastos[:1])

    etiquetas = {boton.label for boton in prueba.button}
    assert "Guardar y terminar" in etiquetas
    assert "Guardar y siguiente" not in etiquetas


def test_guardar_y_siguiente_escribe_y_avanza(dos_gastos):
    """Avanzar guarda, como en la importación: no hay que acordarse."""
    prueba = _en_detalle(dos_gastos)

    _campo(prueba, "text_input", f"edit_{dos_gastos[0]}_desc").set_value(
        "Corregido"
    ).run()
    _control(prueba, "button", "Guardar y siguiente").click().run()

    assert prueba.session_state["indice_edicion"] == 1
    assert MovimientosService().obtener(dos_gastos[0])["descripcion"] == "Corregido"


def test_terminar_cierra_el_recorrido_y_devuelve_la_tabla(dos_gastos):
    """El último guarda y deja la pestaña como estaba."""
    prueba = _en_detalle(dos_gastos[:1])

    _control(prueba, "button", "Guardar y terminar").click().run()

    assert "edicion_movimientos" not in prueba.session_state
    assert _hay_tabla(prueba)


def test_volver_a_la_tabla_no_guarda(dos_gastos):
    """Salir a mitad de una corrección la descarta, y lo dice el pie."""
    prueba = _en_detalle(dos_gastos)

    _campo(prueba, "text_input", f"edit_{dos_gastos[0]}_desc").set_value(
        "A medias"
    ).run()
    _control(prueba, "button", "Volver a la tabla").click().run()

    assert "edicion_movimientos" not in prueba.session_state
    assert MovimientosService().obtener(dos_gastos[0])["descripcion"] == "Audífonos"


# ── Lo que se edita en la propia tabla ───────────────────


def test_en_la_tabla_solo_se_edita_la_descripcion(dos_gastos):
    """
    La descripción se corrige de pasada; lo demás, no.

    Cambiar el tipo o la cuenta arrastra reglas —quién paga, qué destino
    hace falta, qué categorías valen— que una celda suelta no puede
    aplicar. Esas columnas van bloqueadas y se corrigen en el detalle.
    """
    prueba = AppTest.from_file(str(MOVIMIENTOS), default_timeout=60)
    prueba.run()

    _control(prueba, "date_input", "Rango de fechas").set_value(
        (date(2026, 7, 1), date(2026, 7, 31))
    ).run()

    editables = _columnas_editables(prueba)

    assert "descripcion" in editables
    assert "abrir" in editables
    assert not editables & {"monto", "tipo", "cuenta", "fecha", "descripcion_banco"}


def test_la_casilla_de_abrir_empieza_desmarcada(dos_gastos):
    """
    La selección es una casilla porque el editor no trae selección de
    filas. Arranca en falso: nada abierto hasta que se marca.
    """
    prueba = AppTest.from_file(str(MOVIMIENTOS), default_timeout=60)
    prueba.run()

    _control(prueba, "date_input", "Rango de fechas").set_value(
        (date(2026, 7, 1), date(2026, 7, 31))
    ).run()

    assert not _tabla(prueba)["abrir"].any()
    assert "edicion_movimientos" not in prueba.session_state


# ── Lo que sobrevive a una recarga ───────────────────────


def test_los_filtros_vuelven_de_la_url(dos_gastos):
    """
    Recargar abre una sesión nueva, así que los filtros viven en la URL.

    Sin eso, cada F5 devolvía la vista a los últimos noventa días y
    había que rehacer el filtrado a mano.
    """
    prueba = AppTest.from_file(str(MOVIMIENTOS), default_timeout=60)
    prueba.query_params["fechas"] = ["2026-07-01", "2026-07-31"]
    prueba.query_params["hasta"] = "1000"
    prueba.run()

    assert not prueba.exception, [str(e) for e in prueba.exception]
    assert list(_tabla(prueba)["descripcion"]) == ["Taxi"]
    assert _control(prueba, "number_input", "Monto hasta").value == 1_000
