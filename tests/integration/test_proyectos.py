from __future__ import annotations

from datetime import date

import pytest

from finanzas.application.services.movimientos_service import MovimientosService
from finanzas.data.repositories.movimientos_repository import MovimientosRepository
from finanzas.domain.enums import TipoMovimiento

# ═══════════════════════════════════════════════════════════
# Proyectos
#
# La categoría dice qué clase de gasto fue; el proyecto, para
# qué esfuerzo se hizo. Un viaje cruza transporte, comida y
# hospedaje, y sólo agrupándolo se sabe cuánto costó.
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def servicio(movimientos: MovimientosRepository) -> MovimientosService:
    """Servicio de movimientos sobre la base de prueba."""
    return MovimientosService(movimientos)


@pytest.fixture
def viaje(servicio: MovimientosService, ids_catalogo: dict[str, int]) -> str:
    """Un viaje repartido entre tres categorías distintas."""
    for categoria, monto, fecha in (
        ("vivienda", 8_500.0, date(2026, 7, 10)),
        ("restaurantes", 890.0, date(2026, 7, 13)),
        ("vivienda", 650.0, date(2026, 7, 14)),
    ):
        servicio.registrar(
            fecha=fecha,
            tipo=TipoMovimiento.GASTO,
            monto=monto,
            categoria_id=ids_catalogo[categoria],
            cuenta_id=ids_catalogo["cuenta"],
            proyecto="Viaje Oaxaca",
        )

    return "Viaje Oaxaca"


def test_un_proyecto_suma_a_traves_de_categorias(servicio, viaje):
    """Agrupar por proyecto cruza categorías a propósito."""
    resumen = servicio.proyectos().set_index("proyecto")

    assert resumen.loc[viaje, "movimientos"] == 3
    assert resumen.loc[viaje, "gasto"] == 10_040.0


def test_un_proyecto_reporta_su_lapso(servicio, viaje):
    """Un proyecto dura lo que dura, y sus extremos lo dicen."""
    resumen = servicio.proyectos().set_index("proyecto")

    assert resumen.loc[viaje, "desde"].date() == date(2026, 7, 10)
    assert resumen.loc[viaje, "hasta"].date() == date(2026, 7, 14)


def test_los_movimientos_sin_proyecto_no_aparecen(servicio, viaje, ids_catalogo):
    """El resumen es de proyectos, no de todo el gasto."""
    servicio.registrar(
        fecha=date(2026, 8, 5),
        tipo=TipoMovimiento.GASTO,
        monto=700.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
        descripcion="Despensa sin proyecto",
    )

    resumen = servicio.proyectos()

    assert list(resumen["proyecto"]) == [viaje]
    assert resumen["gasto"].sum() == 10_040.0


def test_el_filtro_por_proyecto_aisla_sus_movimientos(servicio, viaje, ids_catalogo):
    """Poder ver el detalle es la mitad de la utilidad del agrupado."""
    servicio.registrar(
        fecha=date(2026, 8, 1),
        tipo=TipoMovimiento.GASTO,
        monto=12_000.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
        proyecto="Remodelación cocina",
    )

    del_viaje = servicio.buscar(proyectos=[viaje])

    assert len(del_viaje) == 3
    assert del_viaje["monto"].sum() == 10_040.0


def test_se_puede_filtrar_por_varios_proyectos(servicio, viaje, ids_catalogo):
    """Comparar dos esfuerzos exige poder pedir los dos."""
    servicio.registrar(
        fecha=date(2026, 8, 1),
        tipo=TipoMovimiento.GASTO,
        monto=12_000.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
        proyecto="Remodelación cocina",
    )

    ambos = servicio.buscar(proyectos=[viaje, "Remodelación cocina"])

    assert len(ambos) == 4


def test_un_proyecto_reporta_lo_que_aun_debe(servicio, ids_catalogo):
    """El devengado también se agrupa: un proyecto puede deber dinero."""
    servicio.registrar(
        fecha=date(2026, 8, 1),
        tipo=TipoMovimiento.GASTO,
        monto=12_000.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
        proyecto="Remodelación cocina",
        fecha_pago=None,
    )
    servicio.registrar(
        fecha=date(2026, 8, 3),
        tipo=TipoMovimiento.GASTO,
        monto=5_000.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
        proyecto="Remodelación cocina",
    )

    resumen = servicio.proyectos().set_index("proyecto")

    assert resumen.loc["Remodelación cocina", "gasto"] == 17_000.0
    assert resumen.loc["Remodelación cocina", "por_pagar"] == 12_000.0


def test_el_neto_descuenta_lo_que_el_proyecto_devolvio(servicio, ids_catalogo):
    """Un proyecto que recupera dinero no cuesta lo que gastó."""
    servicio.registrar(
        fecha=date(2026, 7, 10),
        tipo=TipoMovimiento.GASTO,
        monto=5_000.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
        proyecto="Evento",
    )
    servicio.registrar(
        fecha=date(2026, 7, 20),
        tipo=TipoMovimiento.INGRESO,
        monto=2_000.0,
        categoria_id=ids_catalogo["sueldo"],
        cuenta_id=ids_catalogo["cuenta"],
        proyecto="Evento",
    )

    resumen = servicio.proyectos().set_index("proyecto")

    assert resumen.loc["Evento", "gasto"] == 5_000.0
    assert resumen.loc["Evento", "ingreso"] == 2_000.0
    assert resumen.loc["Evento", "neto"] == -3_000.0


def test_los_proyectos_usados_se_ofrecen_para_reutilizar(servicio, viaje):
    """
    Escribir el nombre a mano cada vez crea proyectos gemelos.

    Un acento o una mayúscula de diferencia bastan para partir en dos lo
    que debería ser un solo esfuerzo, así que la captura ofrece los ya
    usados.
    """
    assert servicio.nombres_de_proyecto() == [viaje]


def test_la_busqueda_de_texto_tambien_mira_el_proyecto(servicio, viaje):
    """Si lo escribiste ahí, buscarlo debería encontrarlo."""
    assert len(servicio.buscar(texto="Oaxaca")) == 3


def test_editar_un_movimiento_conserva_su_proyecto(servicio, viaje, ids_catalogo):
    """Cambiar el monto no debe sacarlo del proyecto."""
    movimiento_id = int(servicio.buscar(proyectos=[viaje]).iloc[0]["id"])

    servicio.actualizar(movimiento_id, monto=999.0)

    assert len(servicio.buscar(proyectos=[viaje])) == 3


def test_sin_proyectos_el_resumen_viene_vacio(servicio, ids_catalogo):
    """El estado vacío también tiene que responder."""
    servicio.registrar(
        fecha=date(2026, 8, 5),
        tipo=TipoMovimiento.GASTO,
        monto=700.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
    )

    assert servicio.proyectos().empty
    assert servicio.nombres_de_proyecto() == []
