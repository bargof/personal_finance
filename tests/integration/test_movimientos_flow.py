from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from finanzas.application.services.catalogos_service import (
    CatalogoEnUsoError,
    CatalogosService,
    NombreDuplicadoError,
)
from finanzas.application.services.movimientos_service import (
    MovimientoInvalidoError,
    MovimientosService,
)
from finanzas.data.database import connect
from finanzas.data.repositories.catalogos_repository import CatalogosRepository
from finanzas.data.repositories.movimientos_repository import MovimientosRepository
from finanzas.domain.entities import Movimiento
from finanzas.domain.enums import EstadoMovimiento, TipoMovimiento

# ═══════════════════════════════════════════════════════════
# Flujo completo: servicio → repositorio → SQLite → vista
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def servicio(movimientos: MovimientosRepository) -> MovimientosService:
    """Servicio de movimientos sobre la base de prueba."""
    return MovimientosService(movimientos)


@pytest.fixture
def servicio_catalogos(catalogos: CatalogosRepository) -> CatalogosService:
    """Servicio de catálogos sobre la base de prueba."""
    return CatalogosService(catalogos)


def test_registrar_y_recuperar_un_movimiento(servicio, ids_catalogo):
    """Lo capturado vuelve con sus columnas derivadas ya resueltas."""
    movimiento_id = servicio.registrar(
        fecha=date(2026, 8, 2),
        tipo=TipoMovimiento.GASTO,
        monto=6500.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
        descripcion="Renta mensual",
    )

    guardado = servicio.obtener(movimiento_id)

    assert guardado["descripcion"] == "Renta mensual"
    assert guardado["categoria"] == "Vivienda"
    assert guardado["periodo"] == "2026-08"
    assert guardado["gasto_real"] == 6500.0
    assert guardado["impacto_caja"] == -6500.0
    assert guardado["ingreso_real"] == 0.0


def test_la_vista_calcula_el_signo_no_la_captura(servicio, ids_catalogo):
    """Ingreso y gasto se capturan igual; la vista los distingue."""
    servicio.registrar(
        fecha=date(2026, 8, 1),
        tipo=TipoMovimiento.INGRESO,
        monto=18000.0,
        categoria_id=ids_catalogo["sueldo"],
        cuenta_id=ids_catalogo["cuenta"],
    )
    servicio.registrar(
        fecha=date(2026, 8, 2),
        tipo=TipoMovimiento.GASTO,
        monto=6500.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
    )

    resumen = servicio.resumen_mensual()
    agosto = resumen[resumen["periodo"] == "2026-08"].iloc[0]

    assert agosto["ingresos"] == 18000.0
    assert agosto["gastos"] == 6500.0
    assert agosto["disponible"] == 11500.0


def test_un_movimiento_pendiente_no_mueve_los_agregados(servicio, ids_catalogo):
    """Registrar algo pendiente lo deja visible sin afectar los números."""
    servicio.registrar(
        fecha=date(2026, 8, 10),
        tipo=TipoMovimiento.GASTO,
        monto=999.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
        estado=EstadoMovimiento.PENDIENTE,
    )

    del_periodo = servicio.del_periodo("2026-08")

    assert len(del_periodo) == 1
    assert del_periodo["gasto_real"].sum() == 0.0


def test_confirmar_hace_que_el_gasto_cuente(servicio, ids_catalogo):
    """Confirmar es lo que incorpora el movimiento a los cálculos."""
    movimiento_id = servicio.registrar(
        fecha=date(2026, 8, 10),
        tipo=TipoMovimiento.GASTO,
        monto=999.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
        estado=EstadoMovimiento.PENDIENTE,
    )

    servicio.confirmar(movimiento_id)

    assert servicio.del_periodo("2026-08")["gasto_real"].sum() == 999.0


def test_actualizar_solo_cambia_lo_indicado(servicio, ids_catalogo):
    """Los campos que no se pasan conservan su valor."""
    movimiento_id = servicio.registrar(
        fecha=date(2026, 8, 2),
        tipo=TipoMovimiento.GASTO,
        monto=6500.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
        descripcion="Renta mensual",
        proyecto="Hogar",
    )

    servicio.actualizar(movimiento_id, monto=7000.0)
    actualizado = servicio.obtener(movimiento_id)

    assert actualizado["monto"] == 7000.0
    assert actualizado["descripcion"] == "Renta mensual"
    assert actualizado["proyecto"] == "Hogar"


def test_duplicar_copia_todo_menos_la_fecha(servicio, ids_catalogo):
    """El atajo para el gasto que se repite cada mes."""
    original_id = servicio.registrar(
        fecha=date(2026, 8, 2),
        tipo=TipoMovimiento.GASTO,
        monto=6500.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
        descripcion="Renta mensual",
    )

    copia_id = servicio.duplicar(original_id, date(2026, 9, 2))
    copia = servicio.obtener(copia_id)

    assert copia["descripcion"] == "Renta mensual"
    assert copia["monto"] == 6500.0
    assert copia["periodo"] == "2026-09"


def test_monto_cero_se_rechaza_con_un_mensaje_util(servicio, ids_catalogo):
    """El error explica la regla, no el detalle técnico."""
    with pytest.raises(MovimientoInvalidoError, match="mayor que cero"):
        servicio.registrar(
            fecha=date(2026, 8, 2),
            tipo=TipoMovimiento.GASTO,
            monto=0.0,
            categoria_id=ids_catalogo["vivienda"],
            cuenta_id=ids_catalogo["cuenta"],
        )


def test_monto_negativo_se_rechaza(servicio, ids_catalogo):
    """El signo lo pone el tipo; capturar en negativo es un error."""
    with pytest.raises(MovimientoInvalidoError):
        servicio.registrar(
            fecha=date(2026, 8, 2),
            tipo=TipoMovimiento.GASTO,
            monto=-100.0,
            categoria_id=ids_catalogo["vivienda"],
            cuenta_id=ids_catalogo["cuenta"],
        )


def test_monto_absurdo_se_rechaza(servicio, ids_catalogo):
    """Un cero de más al teclear se atrapa antes de guardarse."""
    with pytest.raises(MovimientoInvalidoError, match="máximo permitido"):
        servicio.registrar(
            fecha=date(2026, 8, 2),
            tipo=TipoMovimiento.GASTO,
            monto=1e12,
            categoria_id=ids_catalogo["vivienda"],
            cuenta_id=ids_catalogo["cuenta"],
        )


def test_los_filtros_se_combinan(servicio, ids_catalogo):
    """Buscar por tipo, categoría y texto acota el resultado."""
    servicio.registrar(
        fecha=date(2026, 8, 5),
        tipo=TipoMovimiento.GASTO,
        monto=420.0,
        categoria_id=ids_catalogo["restaurantes"],
        cuenta_id=ids_catalogo["efectivo"],
        descripcion="Comida con amigos",
    )
    servicio.registrar(
        fecha=date(2026, 8, 6),
        tipo=TipoMovimiento.GASTO,
        monto=6500.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
        descripcion="Renta",
    )

    resultado = servicio.buscar(categorias=["Restaurantes"], texto="amigos")

    assert len(resultado) == 1
    assert resultado.iloc[0]["descripcion"] == "Comida con amigos"


def test_eliminar_muchos_devuelve_el_conteo(servicio, ids_catalogo):
    """El borrado en bloque reporta cuántas filas se fueron."""
    ids = [
        servicio.registrar(
            fecha=date(2026, 8, dia),
            tipo=TipoMovimiento.GASTO,
            monto=100.0,
            categoria_id=ids_catalogo["vivienda"],
            cuenta_id=ids_catalogo["cuenta"],
        )
        for dia in (1, 2, 3)
    ]

    assert servicio.eliminar_muchos(ids[:2]) == 2
    assert len(servicio.buscar()) == 1


def test_registrar_muchos_en_una_transaccion(servicio, ids_catalogo):
    """La carga en bloque valida cada movimiento antes de escribir."""
    lote = [
        Movimiento(
            fecha=date(2026, 8, dia),
            tipo=TipoMovimiento.GASTO,
            monto=100.0 * dia,
            categoria_id=ids_catalogo["vivienda"],
            cuenta_id=ids_catalogo["cuenta"],
        )
        for dia in (1, 2, 3)
    ]

    assert servicio.registrar_muchos(lote) == 3
    assert len(servicio.buscar()) == 3


# ═══════════════════════════════════════════════════════════
# Integridad referencial
# ═══════════════════════════════════════════════════════════


def test_no_se_puede_borrar_una_categoria_con_movimientos(
    servicio, servicio_catalogos, ids_catalogo
):
    """Borrar una categoría en uso perdería el histórico: se impide."""
    servicio.registrar(
        fecha=date(2026, 8, 2),
        tipo=TipoMovimiento.GASTO,
        monto=6500.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
    )

    with pytest.raises(CatalogoEnUsoError, match="Desactívala"):
        servicio_catalogos.eliminar_categoria(ids_catalogo["vivienda"])


def test_no_se_repiten_nombres_de_categoria(servicio_catalogos):
    """El catálogo pierde su valor si admite duplicados."""
    with pytest.raises(NombreDuplicadoError):
        servicio_catalogos.crear_categoria("Vivienda", "Gasto")


def test_la_base_rechaza_un_monto_no_positivo(db_path, ids_catalogo):
    """La restricción vive también en el esquema, no sólo en Python."""
    with pytest.raises(sqlite3.IntegrityError), connect(db_path) as conexion:
        conexion.execute(
            """
            INSERT INTO movimientos (fecha, tipo, monto, categoria_id, cuenta_id)
            VALUES ('2026-08-02', 'Gasto', -100, ?, ?)
            """,
            (ids_catalogo["vivienda"], ids_catalogo["cuenta"]),
        )


def test_la_base_rechaza_un_tipo_desconocido(db_path, ids_catalogo):
    """El vocabulario de tipos está cerrado en el esquema."""
    with pytest.raises(sqlite3.IntegrityError), connect(db_path) as conexion:
        conexion.execute(
            """
            INSERT INTO movimientos (fecha, tipo, monto, categoria_id, cuenta_id)
            VALUES ('2026-08-02', 'Regalo', 100, ?, ?)
            """,
            (ids_catalogo["vivienda"], ids_catalogo["cuenta"]),
        )


def test_eliminar_una_subcategoria_no_borra_sus_movimientos(
    servicio, servicio_catalogos, catalogos, ids_catalogo
):
    """El movimiento sobrevive y queda sin subcategoría."""
    subcategorias = catalogos.listar_subcategorias(ids_catalogo["vivienda"])
    subcategoria_id = int(subcategorias.iloc[0]["id"])

    movimiento_id = servicio.registrar(
        fecha=date(2026, 8, 2),
        tipo=TipoMovimiento.GASTO,
        monto=6500.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
        subcategoria_id=subcategoria_id,
    )

    servicio_catalogos.eliminar_subcategoria(subcategoria_id)
    sobreviviente = servicio.obtener(movimiento_id)

    assert sobreviviente is not None
    assert sobreviviente["subcategoria"] == ""
    assert sobreviviente["monto"] == 6500.0
