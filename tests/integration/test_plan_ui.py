from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from finanzas.application.services.catalogos_service import CatalogosService
from finanzas.application.services.pagos_service import PagosService
from finanzas.application.services.patrimonio_service import PatrimonioService
from finanzas.config.settings import settings
from finanzas.data.seed import preparar_base

# ═══════════════════════════════════════════════════════════
# El plan de pagos recorrido con clics: decidir en una cuenta
# se guarda y cambia con cuánto se termina el año.
# ═══════════════════════════════════════════════════════════

PAGINA = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "finanzas"
    / "application"
    / "app"
    / "app_pages"
    / "pagos.py"
)


@pytest.fixture
def con_tarjeta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    """Una tarjeta que debe 4,000 y 20,000 disponibles: alcanza para todo."""
    ruta = tmp_path / "ui.db"
    preparar_base(str(ruta))
    monkeypatch.setattr(type(settings), "db_path", property(lambda _self: ruta))
    st.cache_resource.clear()
    st.cache_data.clear()

    cuentas = {
        fila.nombre: int(fila.id) for fila in CatalogosService().cuentas().itertuples()
    }
    patrimonio = PatrimonioService()
    ayer = date.today().replace(day=1)
    patrimonio.verificar_saldo(cuentas["Cuenta principal"], ayer, 20_000.0)
    patrimonio.verificar_saldo(cuentas["Tarjeta crédito"], ayer, 4_000.0)
    patrimonio.fijar_reglas_deuda(cuentas["Tarjeta crédito"], tasa_anual=0.6)
    return cuentas["Tarjeta crédito"]


def _pagina() -> AppTest:
    prueba = AppTest.from_file(str(PAGINA), default_timeout=60)
    prueba.run()
    assert not prueba.exception, [str(e) for e in prueba.exception]
    return prueba


def _cierre(prueba: AppTest) -> str:
    return next(m.value for m in prueba.metric if m.label.startswith("Terminas "))


def test_cada_cuenta_tiene_su_selector(con_tarjeta):
    prueba = _pagina()

    selectores = [s for s in prueba.segmented_control if s.label == "Qué hacer"]

    assert len(selectores) == 1
    assert selectores[0].value == "Pagar"
    assert "Posponer" in selectores[0].options


def test_posponer_se_guarda_y_cambia_el_cierre(con_tarjeta):
    prueba = _pagina()
    antes = _cierre(prueba)

    selector = next(s for s in prueba.segmented_control if s.label == "Qué hacer")
    selector.set_value("Posponer").run()
    assert not prueba.exception, [str(e) for e in prueba.exception]

    mes = date.today().replace(day=1)
    decisiones = PagosService().decisiones(mes)
    assert decisiones[f"tarjeta:{con_tarjeta}"][0] == "posponer"
    # Pospuesta, la tarjeta sigue debiéndose con intereses: termina peor.
    assert _cierre(prueba) != antes


def test_pagar_una_parte_deja_el_campo_a_la_vista(con_tarjeta):
    """
    El campo del monto no debe desaparecer al elegir «Pagar una parte».

    Pasaba cuando lo que alcanza a pagarse era el monto completo: el
    selector se deducía del pago, volvía a «Pagar» y escondía el campo.
    """
    prueba = _pagina()
    selector = next(s for s in prueba.segmented_control if s.label == "Qué hacer")
    selector.set_value("Pagar una parte").run()
    assert not prueba.exception, [str(e) for e in prueba.exception]

    selector = next(s for s in prueba.segmented_control if s.label == "Qué hacer")
    campo = [n for n in prueba.number_input if n.label == "Cuánto pagas ahora"]
    assert selector.value == "Pagar una parte"
    assert len(campo) == 1

    campo[0].set_value(1_500.0).run()
    assert not prueba.exception, [str(e) for e in prueba.exception]

    mes = date.today().replace(day=1)
    assert PagosService().decisiones(mes)[f"tarjeta:{con_tarjeta}"] == (
        "parcial",
        1_500.0,
    )
    campo = [n for n in prueba.number_input if n.label == "Cuánto pagas ahora"]
    assert len(campo) == 1
    assert campo[0].value == 1_500.0


def test_se_puede_decidir_en_un_mes_siguiente(con_tarjeta):
    """Posponer en septiembre y decidir en octubre qué pasa con ella."""
    prueba = _pagina()
    selector = next(s for s in prueba.segmented_control if s.label == "Qué hacer")
    selector.set_value("Posponer").run()

    meses = next(s for s in prueba.segmented_control if s.label == "Mes")
    siguiente = meses.options[1]
    meses.set_value(siguiente).run()
    assert not prueba.exception, [str(e) for e in prueba.exception]

    tarjeta = next(s for s in prueba.segmented_control if s.label == "Qué hacer")
    # Llega del mes anterior como algo que toca pagar.
    assert tarjeta.value == "Pagar"
    tarjeta.set_value("Posponer").run()
    assert not prueba.exception, [str(e) for e in prueba.exception]

    hoy = date.today()
    indice = hoy.year * 12 + hoy.month
    octubre = date(indice // 12, indice % 12 + 1, 1)
    assert PagosService().decisiones(octubre)[f"tarjeta:{con_tarjeta}"][0] == "posponer"


def test_simular_un_retiro_del_fondo(con_tarjeta):
    catalogos = CatalogosService()
    fondo = catalogos.crear_cuenta("Fondo de ahorro", "Ahorro")
    catalogos.actualizar_cuenta(fondo, "Fondo de ahorro", "Ahorro", "", True, True)
    PatrimonioService().verificar_saldo(fondo, date.today().replace(day=1), 10_000.0)

    prueba = _pagina()
    meses = next(s for s in prueba.segmented_control if s.label == "Mes")
    meses.set_value(meses.options[1]).run()

    campo = next(
        n for n in prueba.number_input if n.label == "Retirar de Fondo de ahorro"
    )
    assert not campo.disabled
    campo.set_value(2_500.0).run()
    boton = next(b for b in prueba.button if b.label == "Aplicar" and not b.disabled)
    boton.click().run()
    assert not prueba.exception, [str(e) for e in prueba.exception]

    hoy = date.today()
    indice = hoy.year * 12 + hoy.month
    siguiente = date(indice // 12, indice % 12 + 1, 1)
    assert PagosService().retiro_simulado(siguiente, fondo) == 2_500.0


def test_las_cifras_de_arriba_siguen_las_decisiones(con_tarjeta):
    prueba = _pagina()

    def cifra(nombre):
        return next(m.value for m in prueba.metric if m.label == nombre)

    antes = cifra("Por pagar")
    selector = next(s for s in prueba.segmented_control if s.label == "Qué hacer")
    selector.set_value("Posponer").run()

    assert antes == "$4,000"
    assert cifra("Por pagar") == "$0"
    assert cifra("Queda después de pagar") == "$20,000"


def test_pagar_una_parte_de_algo_que_no_se_pospone_mueve_las_cifras(con_tarjeta):
    """La renta a medias: «Vas a pagar» baja y el resto pasa al mes siguiente."""
    from finanzas.application.services.suscripciones_service import (
        SuscripcionesService,
    )

    hoy = date.today()
    SuscripcionesService().crear(
        servicio="Renta",
        costo_por_cobro=3_000.0,
        proximo_cobro=date(hoy.year, hoy.month, min(hoy.day + 1, 28)),
        clase="Gasto fijo",
    )
    prueba = _pagina()

    def cifra(nombre):
        return next(m.value for m in prueba.metric if m.label == nombre)

    renta = next(
        i
        for i, s in enumerate(
            s for s in prueba.segmented_control if s.label == "Qué hacer"
        )
        if "Posponer" not in s.options
    )
    antes = cifra("Vas a pagar")
    [s for s in prueba.segmented_control if s.label == "Qué hacer"][renta].set_value(
        "Pagar una parte"
    ).run()
    campo = next(n for n in prueba.number_input if n.label == "Cuánto pagas ahora")
    campo.set_value(1_000.0).run()
    assert not prueba.exception, [str(e) for e in prueba.exception]

    assert cifra("Vas a pagar") != antes
    pospones = next(m for m in prueba.metric if m.label.startswith("Pospones"))
    assert pospones.value == "$2,000"
