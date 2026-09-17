from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from finanzas.application.services.importacion_service import ImportacionService
from finanzas.application.services.movimientos_service import MovimientosService
from finanzas.config.settings import settings
from finanzas.data.seed import preparar_base

# ═══════════════════════════════════════════════════════════
# El asistente de importación, recorrido como lo recorre el
# usuario: con clics.
#
# Los servicios ya están probados por su cuenta. Esto fija que
# la página los llame. Hubo una versión en la que el servicio
# de autoguardado existía, tenía pruebas, y la interfaz nunca
# lo invocaba: nada fallaba y nada se guardaba.
# ═══════════════════════════════════════════════════════════

PAGINA = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "finanzas"
    / "application"
    / "app"
    / "app_pages"
    / "importar.py"
)

DOCUMENTO = b"""Fecha: 22 agosto 2026
mercado pago
Periodo 22 julio - 21 agosto
Movimientos
27/07 Compra en STR*AMAZON $ 2,398.99
01/08 Compra en DIDI $ 100.00
10/08 Pago del resumen del julio/2026 - $ 3,519.77"""


@pytest.fixture
def base_ui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Base limpia a la que apunta la configuración global durante la prueba."""
    ruta = tmp_path / "ui.db"
    preparar_base(str(ruta))
    monkeypatch.setattr(type(settings), "db_path", property(lambda _self: ruta))
    # Los servicios se cachean por proceso; sin esto, la prueba vería la
    # base de la prueba anterior.
    st.cache_resource.clear()

    return str(ruta)


def _asistente(base_ui: str) -> AppTest:
    """La página con un documento leído y en el paso de completar."""
    resultado = ImportacionService().leer_documento(DOCUMENTO)

    prueba = AppTest.from_file(str(PAGINA), default_timeout=60)
    prueba.session_state["importacion"] = resultado
    prueba.session_state["paso"] = "completar"
    prueba.session_state["indice"] = 0
    prueba.run()

    assert not prueba.exception, [str(e) for e in prueba.exception]
    return prueba


def _control(prueba: AppTest, tipo: str, etiqueta: str):
    """Localiza un control por su etiqueta."""
    encontrados = [w for w in getattr(prueba, tipo) if w.label == etiqueta]
    assert encontrados, f"no hay {tipo} con etiqueta «{etiqueta}»"
    return encontrados[0]


def test_el_asistente_tiene_los_mismos_campos_que_registrar(base_ui):
    """
    Un movimiento importado no debe quedar más pobre que uno capturado.

    Lo que el formulario de Movimientos permite poner, éste también.
    """
    prueba = _asistente(base_ui)

    etiquetas = {
        w.label
        for tipo in (
            "date_input",
            "segmented_control",
            "number_input",
            "selectbox",
            "text_input",
            "checkbox",
            "toggle",
            "text_area",
        )
        for w in getattr(prueba, tipo)
    }
    esperadas = {
        "Fecha",
        "Tipo",
        "Monto",
        "Categoría",
        "Subcategoría",
        "Cuenta",
        "Descripción",
        "Medio de pago",
        "Esencial o deseo",
        "Fijo o variable",
        "Proyecto o persona",
        "Etiquetas",
        "Estado",
        "Nota o comprobante",
        "Es un gasto recurrente",
        "Estaba planeado",
        "Ya se pagó",
        "Apuntar los productos",
    }

    assert esperadas <= etiquetas, sorted(esperadas - etiquetas)


def test_la_fecha_arranca_con_la_del_banco(base_ui):
    """El campo viene prellenado con lo que dijo el documento."""
    prueba = _asistente(base_ui)

    assert _control(prueba, "date_input", "Fecha").value == date(2026, 7, 27)


def test_fecha_y_monto_no_se_pueden_editar(base_ui):
    """
    Las dos casillas bloqueadas del asistente.

    Son con lo que se reconoce el movimiento al reimportar: corregidas
    dejarían de coincidir con lo que el banco va a repetir.
    """
    prueba = _asistente(base_ui)

    assert _control(prueba, "date_input", "Fecha").disabled
    assert _control(prueba, "number_input", "Monto").disabled


def test_avanzar_guarda_con_fecha_y_monto_del_banco(base_ui):
    """Lo que llega a la base es lo que dijo el documento, sin cambios."""
    prueba = _asistente(base_ui)

    _control(prueba, "button", "Siguiente").click().run()

    guardados = MovimientosService().buscar()
    assert len(guardados) == 1
    assert guardados.iloc[0]["fecha"].date() == date(2026, 7, 27)
    assert guardados.iloc[0]["monto"] == 2_398.99


def test_avanzar_guarda_aunque_no_se_cambie_nada(base_ui):
    """Siguiente escribe; no hace falta tocar un botón de guardar."""
    prueba = _asistente(base_ui)

    _control(prueba, "button", "Siguiente").click().run()

    assert len(MovimientosService().buscar()) == 1


def test_volver_atras_muestra_lo_corregido(base_ui):
    """Anterior no devuelve los campos a lo que dijo el banco."""
    prueba = _asistente(base_ui)

    _control(prueba, "text_input", "Descripción").set_value("Lo mío").run()
    _control(prueba, "button", "Siguiente").click().run()
    _control(prueba, "button", "Anterior").click().run()

    assert _control(prueba, "text_input", "Descripción").value == "Lo mío"


def test_corregir_y_volver_a_avanzar_no_duplica(base_ui):
    """
    Pasar dos veces por el mismo movimiento reemplaza, no acumula.

    Anterior también guarda al salir del movimiento en que está, así que
    tras el recorrido hay dos movimientos distintos en la base, pero el
    corregido aparece una sola vez.
    """
    prueba = _asistente(base_ui)

    _control(prueba, "button", "Siguiente").click().run()
    _control(prueba, "button", "Anterior").click().run()
    _control(prueba, "text_input", "Descripción").set_value("Corregido").run()
    _control(prueba, "button", "Siguiente").click().run()

    guardados = MovimientosService().buscar()
    amazon = guardados[guardados["descripcion_banco"] == "STR*AMAZON"]
    assert len(amazon) == 1
    assert amazon.iloc[0]["descripcion"] == "Corregido"


def test_el_avance_sobrevive_a_recargar_la_pagina(base_ui):
    """
    Una sesión nueva sin `session_state` retoma donde se quedó.

    Es lo que pasa al tocar F5: Streamlit olvida todo, y la página lo
    recupera de la base.
    """
    prueba = _asistente(base_ui)
    _control(prueba, "text_input", "Descripción").set_value("Lo mío").run()
    _control(prueba, "button", "Siguiente").click().run()

    recargada = AppTest.from_file(str(PAGINA), default_timeout=60)
    recargada.run()

    assert not recargada.exception
    assert recargada.session_state["paso"] == "completar"
    assert recargada.session_state["indice"] == 1
    primero = recargada.session_state["importacion"].candidatos[0]
    assert primero.descripcion == "Lo mío"
    assert primero.ya_guardado


def test_el_pago_de_tarjeta_llega_como_transferencia_y_se_puede_cambiar(base_ui):
    """El tipo se sugiere desde el documento pero lo decide el usuario."""
    prueba = _asistente(base_ui)
    prueba.session_state["indice"] = 2
    prueba.run()

    tipo = _control(prueba, "segmented_control", "Tipo")
    assert tipo.value == "Transferencia"

    tipo.set_value("Gasto").run()
    _control(prueba, "button", "Siguiente").click().run()

    guardados = MovimientosService().buscar()
    assert guardados.iloc[0]["tipo"] == "Gasto"


def test_marcar_ya_se_pago_abre_la_fecha_de_pago(base_ui):
    """
    Marcar la casilla muestra la fecha, prellenada con la del documento.

    Hubo una versión en que esta ruta reventaba por una referencia a un
    campo que ya no existía; sólo se veía al marcar la casilla.
    """
    prueba = _asistente(base_ui)

    _control(prueba, "checkbox", "Ya se pagó").check().run()

    assert not prueba.exception, [str(e) for e in prueba.exception]
    assert _control(prueba, "date_input", "Fecha de pago").value == date(2026, 7, 27)


def test_ya_pagado_se_guarda_con_su_fecha_de_pago(base_ui):
    """Un importado marcado como pagado deja de ser adeudo."""
    prueba = _asistente(base_ui)

    _control(prueba, "checkbox", "Ya se pagó").check().run()
    _control(prueba, "button", "Siguiente").click().run()

    guardado = MovimientosService().buscar().iloc[0]
    assert guardado["fecha_pago"].date() == date(2026, 7, 27)
    assert guardado["por_pagar"] == 0


def test_saltar_no_guarda(base_ui):
    """Saltar un movimiento lo deja fuera de la base."""
    prueba = _asistente(base_ui)

    _control(prueba, "button", "Saltar").click().run()

    assert MovimientosService().buscar().empty


def test_terminar_lleva_al_resumen_con_lo_guardado(base_ui):
    """Terminar cierra la edición y cuenta lo escrito."""
    prueba = _asistente(base_ui)

    _control(prueba, "button", "Siguiente").click().run()
    _control(prueba, "button", "Terminar").click().run()

    assert prueba.session_state["paso"] == "guardar"
    assert len(MovimientosService().buscar()) == 2
