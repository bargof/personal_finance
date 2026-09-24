from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from finanzas.application.app.components import (
    ABRIR_EXPLORAR,
    FOCO_PARECIDOS,
    PESTANA_MOVIMIENTOS,
)
from finanzas.application.services.importacion_service import ImportacionService
from finanzas.application.services.movimientos_service import MovimientosService
from finanzas.config.settings import settings
from finanzas.data.repositories.catalogos_repository import CatalogosRepository
from finanzas.data.seed import preparar_base
from finanzas.domain.enums import TipoMovimiento

# ═══════════════════════════════════════════════════════════
# El aviso de posible duplicado, visto desde la pantalla
#
# El servicio que los busca está probado aparte. Esto fija que
# las pantallas donde se captura lo consulten y que «Ver más»
# deje a la vista lo que hay que comparar: un aviso que nadie
# dibuja no evita ningún duplicado.
# ═══════════════════════════════════════════════════════════

APP_DIR = (
    Path(__file__).resolve().parents[2] / "src" / "finanzas" / "application" / "app"
)
MOVIMIENTOS = APP_DIR / "app_pages" / "movimientos.py"
IMPORTAR = APP_DIR / "app_pages" / "importar.py"

#: Un documento de una línea, con la misma cifra que el movimiento ya
#: registrado por la fixture y otro folio: el caso que la deduplicación
#: por folio no caza.
DOCUMENTO = b"""Fecha: 22 agosto 2026
mercado pago
Periodo 22 julio - 21 agosto
Movimientos
27/07 Compra en STR*AMAZON $ 2,398.99"""


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
def ya_registrado(base_ui: str) -> int:
    """Un gasto del 27 de julio por lo mismo que trae el documento."""
    catalogos = CatalogosRepository(base_ui)
    return MovimientosService().registrar(
        fecha=date(2026, 7, 27),
        tipo=TipoMovimiento.GASTO,
        monto=2_398.99,
        categoria_id=catalogos.mapa_nombre_id("categorias")["Compras"],
        cuenta_id=catalogos.mapa_nombre_id("cuentas")["Cuenta principal"],
        descripcion="Audífonos",
        fecha_banco=date(2026, 7, 27),
    )


def _control(prueba: AppTest, tipo: str, etiqueta: str):
    """Localiza un control por su etiqueta."""
    encontrados = [w for w in getattr(prueba, tipo) if w.label == etiqueta]
    assert encontrados, f"no hay {tipo} con etiqueta «{etiqueta}»"
    return encontrados[0]


def _avisos(prueba: AppTest) -> list[str]:
    """Los textos de aviso que la página dejó en pantalla."""
    return [str(aviso.value) for aviso in prueba.warning]


def _hay_aviso_de_duplicado(prueba: AppTest) -> bool:
    return any("Posible duplicado" in texto for texto in _avisos(prueba))


# ── Al capturar a mano ───────────────────────────────────


def test_capturar_algo_ya_registrado_avisa(ya_registrado):
    """Misma fecha y mismo monto que algo de la base: sale el aviso."""
    prueba = AppTest.from_file(str(MOVIMIENTOS), default_timeout=60)
    prueba.run()

    _control(prueba, "date_input", "Fecha").set_value(date(2026, 7, 27)).run()
    _control(prueba, "number_input", "Monto").set_value(2_398.99).run()

    assert not prueba.exception, [str(e) for e in prueba.exception]
    assert _hay_aviso_de_duplicado(prueba)


def test_el_aviso_nombra_al_que_ya_esta(ya_registrado):
    """Sirve para reconocerlo sin abrirlo: id, fecha y descripción."""
    prueba = AppTest.from_file(str(MOVIMIENTOS), default_timeout=60)
    prueba.run()

    _control(prueba, "date_input", "Fecha").set_value(date(2026, 7, 27)).run()
    _control(prueba, "number_input", "Monto").set_value(2_398.99).run()

    resumenes = [str(bloque.value) for bloque in prueba.markdown]
    assert any("Audífonos" in texto for texto in resumenes)


def test_otro_monto_no_avisa(ya_registrado):
    """El aviso no aparece por aparecer: la cifra tiene que ser la misma."""
    prueba = AppTest.from_file(str(MOVIMIENTOS), default_timeout=60)
    prueba.run()

    _control(prueba, "date_input", "Fecha").set_value(date(2026, 7, 27)).run()
    _control(prueba, "number_input", "Monto").set_value(1_000.0).run()

    assert not _hay_aviso_de_duplicado(prueba)


def test_sin_monto_no_avisa(ya_registrado):
    """Recién abierta la página, con el monto en cero, no hay nada que decir."""
    prueba = AppTest.from_file(str(MOVIMIENTOS), default_timeout=60)
    prueba.run()

    assert not _hay_aviso_de_duplicado(prueba)


def test_el_aviso_va_despues_del_boton_de_registrar(ya_registrado):
    """
    El aviso se lee al final, no en medio de la captura.

    Es un aviso, no un campo: estorba si se cuela entre el formulario y
    el botón con el que se termina.
    """
    prueba = AppTest.from_file(str(MOVIMIENTOS), default_timeout=60)
    prueba.run()

    _control(prueba, "date_input", "Fecha").set_value(date(2026, 7, 27)).run()
    _control(prueba, "number_input", "Monto").set_value(2_398.99).run()

    etiquetas = [boton.label for boton in prueba.button]
    assert etiquetas.index("Ver más") > etiquetas.index("Registrar movimiento")


def test_lo_recien_registrado_no_se_avisa_a_si_mismo(base_ui):
    """
    Guardar no debe disparar el aviso por el movimiento que se acaba de
    guardar: el formulario conserva sus valores y sería su propio
    duplicado.
    """
    prueba = AppTest.from_file(str(MOVIMIENTOS), default_timeout=60)
    prueba.run()

    _control(prueba, "number_input", "Monto").set_value(500.0).run()
    _control(prueba, "button", "Registrar movimiento").click().run()

    assert not prueba.exception, [str(e) for e in prueba.exception]
    assert len(MovimientosService().buscar()) == 1
    assert not _hay_aviso_de_duplicado(prueba)


def test_ver_mas_abre_explorar_con_esos_registros(ya_registrado):
    """
    «Ver más» cambia de pestaña y deja la tabla con los parecidos.

    Es el mismo sitio de siempre —la tabla que se selecciona para abrir
    el detalle—, sólo que alimentada por el aviso en vez de por los
    filtros.
    """
    prueba = AppTest.from_file(str(MOVIMIENTOS), default_timeout=60)
    prueba.run()

    _control(prueba, "date_input", "Fecha").set_value(date(2026, 7, 27)).run()
    _control(prueba, "number_input", "Monto").set_value(2_398.99).run()
    _control(prueba, "button", "Ver más").click().run()

    assert not prueba.exception, [str(e) for e in prueba.exception]
    assert prueba.session_state[FOCO_PARECIDOS]["monto"] == 2_398.99
    assert prueba.session_state[PESTANA_MOVIMIENTOS] == "Explorar y editar"
    # La petición se consume: no debe volver a mover la pestaña sola.
    assert ABRIR_EXPLORAR not in prueba.session_state
    assert any("Posibles duplicados" in str(m.value) for m in prueba.markdown)


def test_volver_a_los_filtros_deja_la_tabla_como_estaba(ya_registrado):
    """El foco es temporal: se quita y vuelven los filtros de siempre."""
    prueba = AppTest.from_file(str(MOVIMIENTOS), default_timeout=60)
    prueba.session_state[FOCO_PARECIDOS] = {
        "fecha": date(2026, 7, 27),
        "monto": 2_398.99,
        "excluir": [],
    }
    prueba.run()

    _control(prueba, "button", "Volver a los filtros").click().run()

    assert FOCO_PARECIDOS not in prueba.session_state
    assert any(w.label == "Rango de fechas" for w in prueba.date_input)


def test_editar_no_se_avisa_a_si_mismo(ya_registrado):
    """
    Abrir un movimiento para corregirlo no lo denuncia como duplicado.

    El foco enseña el que ya está; al no haber otro con esa cifra, el
    formulario de edición no debe avisar de nada.
    """
    prueba = AppTest.from_file(str(MOVIMIENTOS), default_timeout=60)
    prueba.session_state[FOCO_PARECIDOS] = {
        "fecha": date(2026, 7, 27),
        "monto": 2_398.99,
        "excluir": [ya_registrado],
    }
    prueba.run()

    assert not prueba.exception, [str(e) for e in prueba.exception]
    assert not _hay_aviso_de_duplicado(prueba)


# ── Al completar lo importado ────────────────────────────


def test_el_asistente_avisa_del_parecido_de_otra_cuenta(base_ui, ya_registrado):
    """
    El duplicado que la deduplicación deja pasar: el de la otra cuenta.

    Contrastar exige que lo registrado toque la cuenta del documento,
    porque la misma cifra el mismo día en otra cuenta casi siempre es
    otro movimiento. Cuando no lo es —el mismo cobro visto desde el
    banco que lo procesa y desde el que lo liquida, cada uno con su
    folio— la línea llega como nueva, y el aviso es lo único que queda
    antes de guardarla dos veces.
    """
    servicio = ImportacionService()
    resultado = servicio.leer_documento(DOCUMENTO)
    resultado.cuenta_id = CatalogosRepository(base_ui).mapa_nombre_id("cuentas")[
        "Tarjeta crédito"
    ]
    servicio.contrastar(resultado)
    assert resultado.a_importar, "la línea debería llegar como nueva"

    prueba = AppTest.from_file(str(IMPORTAR), default_timeout=60)
    prueba.session_state["importacion"] = resultado
    prueba.session_state["paso"] = "completar"
    prueba.session_state["indice"] = 0
    prueba.run()

    assert not prueba.exception, [str(e) for e in prueba.exception]
    assert _hay_aviso_de_duplicado(prueba)
    assert any("Audífonos" in str(bloque.value) for bloque in prueba.markdown)
