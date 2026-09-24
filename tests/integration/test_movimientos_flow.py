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


def test_el_filtro_de_cuenta_mira_origen_y_destino(servicio, ids_catalogo):
    """
    Un traspaso aparece desde las dos cuentas que toca.

    Se captura una sola vez, desde la que envía. Si al revisar la que
    recibe no saliera, parecería que falta por registrar y acabaría
    capturado dos veces: una como enviado y otra como recibido.
    """
    servicio.registrar(
        fecha=date(2026, 8, 5),
        tipo=TipoMovimiento.TRANSFERENCIA,
        monto=1_500.0,
        categoria_id=ids_catalogo["ahorro"],
        cuenta_id=ids_catalogo["cuenta"],
        cuenta_destino_id=ids_catalogo["ahorro_cuenta"],
        descripcion="SPEI enviado",
    )

    desde_el_origen = servicio.buscar(cuentas=["Cuenta principal"])
    desde_el_destino = servicio.buscar(cuentas=["Cuenta ahorro"])

    assert len(desde_el_origen) == 1
    assert len(desde_el_destino) == 1
    assert desde_el_destino.iloc[0]["descripcion"] == "SPEI enviado"


def test_el_filtro_de_cuenta_no_trae_las_ajenas(servicio, ids_catalogo):
    """Mirar las dos patas no es dejar de filtrar."""
    servicio.registrar(
        fecha=date(2026, 8, 5),
        tipo=TipoMovimiento.GASTO,
        monto=420.0,
        categoria_id=ids_catalogo["restaurantes"],
        cuenta_id=ids_catalogo["efectivo"],
    )

    assert servicio.buscar(cuentas=["Cuenta principal"]).empty


def test_el_filtro_de_monto_acota_por_los_dos_lados(servicio, ids_catalogo):
    """Cada extremo filtra por su cuenta y los dos se combinan."""
    for importe in (99.0, 420.0, 6_500.0):
        servicio.registrar(
            fecha=date(2026, 8, 5),
            tipo=TipoMovimiento.GASTO,
            monto=importe,
            categoria_id=ids_catalogo["restaurantes"],
            cuenta_id=ids_catalogo["efectivo"],
        )

    assert len(servicio.buscar(monto_min=100.0)) == 2
    assert len(servicio.buscar(monto_max=500.0)) == 2
    assert len(servicio.buscar(monto_min=100.0, monto_max=500.0)) == 1
    assert len(servicio.buscar()) == 3


def test_el_mismo_monto_arriba_y_abajo_busca_la_cifra_exacta(servicio, ids_catalogo):
    """
    Es como se rastrea un cobro concreto, y el redondeo no debe estorbar.

    El importe se guarda como REAL: sin holgura, pedir 2.398,99 por los
    dos lados dependería de cómo cayeran los decimales.
    """
    servicio.registrar(
        fecha=date(2026, 8, 5),
        tipo=TipoMovimiento.GASTO,
        monto=2_398.99,
        categoria_id=ids_catalogo["restaurantes"],
        cuenta_id=ids_catalogo["efectivo"],
    )
    servicio.registrar(
        fecha=date(2026, 8, 5),
        tipo=TipoMovimiento.GASTO,
        monto=2_399.00,
        categoria_id=ids_catalogo["restaurantes"],
        cuenta_id=ids_catalogo["efectivo"],
    )

    exacto = servicio.buscar(monto_min=2_398.99, monto_max=2_398.99)

    assert len(exacto) == 1
    assert exacto.iloc[0]["monto"] == 2_398.99


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


# ═══════════════════════════════════════════════════════════
# Reglas por tipo de movimiento
#
# Un ingreso no es esencial ni deseo, un ahorro entra a una
# cuenta de ahorro, y el ahorro del mes es neto.
# ═══════════════════════════════════════════════════════════


def test_un_ingreso_no_conserva_banderas_de_gasto(servicio, ids_catalogo):
    """Lo que no aplica se guarda neutro, para que ningún reporte lo cuente."""
    from finanzas.domain.enums import Necesidad

    ingreso_id = servicio.registrar(
        fecha=date(2026, 8, 1),
        tipo=TipoMovimiento.INGRESO,
        monto=20_000.0,
        categoria_id=ids_catalogo["sueldo"],
        cuenta_id=ids_catalogo["cuenta"],
        necesidad=Necesidad.DESEO,
        recurrente=True,
        planeado=False,
        empresa="ITAM",
        lugar="Oficina",
        fecha_pago=None,
    )
    fila = servicio.obtener(ingreso_id)

    assert fila["necesidad"] == "Esencial"
    assert not fila["recurrente"]
    assert fila["planeado"]
    assert fila["empresa"] == ""
    assert fila["lugar"] == ""
    # Un ingreso ocurre o no ocurre: queda pagado en su fecha.
    assert fila["pagado"]


def test_un_ahorro_necesita_una_cuenta_de_ahorro(servicio, ids_catalogo):
    """Sin destino no hay a dónde sumar el ahorro."""
    from finanzas.application.services.movimientos_service import (
        MovimientoInvalidoError,
    )

    with pytest.raises(MovimientoInvalidoError, match="ahorro"):
        servicio.registrar(
            fecha=date(2026, 8, 1),
            tipo=TipoMovimiento.AHORRO,
            monto=1_000.0,
            categoria_id=ids_catalogo["ahorro"],
            cuenta_id=ids_catalogo["cuenta"],
        )

    with pytest.raises(MovimientoInvalidoError, match="Ahorro o Inversión"):
        servicio.registrar(
            fecha=date(2026, 8, 1),
            tipo=TipoMovimiento.AHORRO,
            monto=1_000.0,
            categoria_id=ids_catalogo["ahorro"],
            cuenta_id=ids_catalogo["cuenta"],
            cuenta_destino_id=ids_catalogo["efectivo"],
        )


def test_el_ahorro_del_mes_es_neto(servicio, ids_catalogo):
    """Meter 7,000 y sacar 3,000 es haber ahorrado 4,000, no 7,000."""
    servicio.registrar(
        fecha=date(2026, 8, 1),
        tipo=TipoMovimiento.AHORRO,
        monto=7_000.0,
        categoria_id=ids_catalogo["ahorro"],
        cuenta_id=ids_catalogo["cuenta"],
        cuenta_destino_id=ids_catalogo["ahorro_cuenta"],
    )
    servicio.registrar(
        fecha=date(2026, 8, 20),
        tipo=TipoMovimiento.TRANSFERENCIA,
        monto=3_000.0,
        categoria_id=ids_catalogo["ahorro"],
        cuenta_id=ids_catalogo["ahorro_cuenta"],
        cuenta_destino_id=ids_catalogo["cuenta"],
    )

    resumen = servicio.resumen_mensual().set_index("periodo").loc["2026-08"]
    flujo = servicio.flujo_por_cuenta().set_index("cuenta")

    assert resumen["aportaciones"] == 7_000.0
    assert resumen["retiros"] == 3_000.0
    assert resumen["ahorro_inversion"] == 4_000.0
    # Y las dos cuentas cuadran: lo que salió de una entró a la otra.
    assert flujo.loc["Cuenta ahorro", "flujo_neto"] == 4_000.0
    assert flujo.loc["Cuenta principal", "flujo_neto"] == -4_000.0


def test_un_traspaso_al_apartado_tambien_es_ahorro(servicio, ids_catalogo):
    """Lo dicen las cuentas, no el tipo: así aportación y retiro se miden igual."""
    servicio.registrar(
        fecha=date(2026, 8, 1),
        tipo=TipoMovimiento.TRANSFERENCIA,
        monto=500.0,
        categoria_id=ids_catalogo["ahorro"],
        cuenta_id=ids_catalogo["cuenta"],
        cuenta_destino_id=ids_catalogo["ahorro_cuenta"],
    )

    fila = servicio.buscar().iloc[0]

    assert fila["patrimonio_creado"] == 500.0
    assert fila["ahorro_retirado"] == 0.0


def test_el_medio_de_pago_se_deduce_de_la_cuenta(servicio, ids_catalogo):
    """Pagar con la tarjeta es pagar a crédito, sin tener que decirlo."""
    gasto_id = servicio.registrar(
        fecha=date(2026, 8, 1),
        tipo=TipoMovimiento.GASTO,
        monto=100.0,
        categoria_id=ids_catalogo["restaurantes"],
        cuenta_id=ids_catalogo["tarjeta"],
    )

    assert servicio.obtener(gasto_id)["medio_pago"] == "Crédito"


def test_cambiar_un_gasto_a_traspaso_lo_deja_pagado(servicio, ids_catalogo):
    """El caso del pago de tarjeta capturado como gasto y corregido después."""
    gasto_id = servicio.registrar(
        fecha=date(2026, 8, 29),
        tipo=TipoMovimiento.GASTO,
        monto=6_784.48,
        categoria_id=ids_catalogo["restaurantes"],
        cuenta_id=ids_catalogo["cuenta"],
        fecha_pago=None,
    )
    assert servicio.total_por_pagar() == 6_784.48

    servicio.actualizar(
        gasto_id,
        tipo=TipoMovimiento.TRANSFERENCIA,
        cuenta_destino_id=ids_catalogo["tarjeta"],
    )
    fila = servicio.obtener(gasto_id)

    assert servicio.total_por_pagar() == 0.0
    assert fila["pagado"]
    assert fila["gasto_real"] == 0.0
    assert fila["cuenta_destino"] == "Tarjeta crédito"


# ═══════════════════════════════════════════════════════════
# Posibles duplicados: fecha y monto, nada más
#
# El mismo cobro puede llegar por dos caminos —capturado a
# mano e importado, o importado desde dos documentos— y cada
# documento le pone su propio folio. Por eso esta búsqueda
# mira lo único que los dos comparten.
# ═══════════════════════════════════════════════════════════


def test_parecidos_encuentra_el_mismo_dia_y_el_mismo_monto(servicio, ids_catalogo):
    """El caso directo: ya está registrado, tal cual."""
    servicio.registrar(
        fecha=date(2026, 8, 2),
        tipo=TipoMovimiento.GASTO,
        monto=349.0,
        categoria_id=ids_catalogo["restaurantes"],
        cuenta_id=ids_catalogo["cuenta"],
        descripcion="Comida",
    )

    parecidos = servicio.parecidos(date(2026, 8, 2), 349.0)

    assert len(parecidos) == 1
    assert parecidos.iloc[0]["descripcion"] == "Comida"


def test_parecidos_admite_un_dia_de_diferencia_pero_no_dos(servicio, ids_catalogo):
    """
    El banco aplica al día siguiente; a dos días ya son otra cosa.

    La fecha del banco se fija igual que la del movimiento para medir
    sólo el margen: lo capturado a mano asume por defecto que el banco
    lo aplica un día después, y eso ensancharía la ventana.
    """
    servicio.registrar(
        fecha=date(2026, 8, 2),
        tipo=TipoMovimiento.GASTO,
        monto=349.0,
        categoria_id=ids_catalogo["restaurantes"],
        cuenta_id=ids_catalogo["cuenta"],
        fecha_banco=date(2026, 8, 2),
    )

    assert len(servicio.parecidos(date(2026, 8, 3), 349.0)) == 1
    assert len(servicio.parecidos(date(2026, 8, 1), 349.0)) == 1
    assert servicio.parecidos(date(2026, 8, 4), 349.0).empty


def test_parecidos_cubre_el_dia_en_que_el_banco_lo_aplicara(servicio, ids_catalogo):
    """
    Lo capturado a mano se reconoce también por la fecha que tendrá en
    el banco: es la que traerá el estado de cuenta al importarlo.
    """
    servicio.registrar(
        fecha=date(2026, 8, 2),
        tipo=TipoMovimiento.GASTO,
        monto=349.0,
        categoria_id=ids_catalogo["restaurantes"],
        cuenta_id=ids_catalogo["cuenta"],
    )

    assert len(servicio.parecidos(date(2026, 8, 4), 349.0)) == 1
    assert servicio.parecidos(date(2026, 8, 5), 349.0).empty


def test_parecidos_tambien_casa_con_la_fecha_del_banco(servicio, ids_catalogo):
    """
    Un movimiento corrido a su fecha de compra sigue siendo reconocible.

    Lo que se captura puede coincidir con cualquiera de las dos fechas
    del registrado: la suya o la que reportó el banco.
    """
    servicio.registrar(
        fecha=date(2026, 8, 2),
        tipo=TipoMovimiento.GASTO,
        monto=349.0,
        categoria_id=ids_catalogo["restaurantes"],
        cuenta_id=ids_catalogo["cuenta"],
        fecha_banco=date(2026, 8, 10),
    )

    assert len(servicio.parecidos(date(2026, 8, 10), 349.0)) == 1


def test_parecidos_no_mira_ni_la_cuenta_ni_el_folio(servicio, ids_catalogo):
    """
    El duplicado que más cuesta ver viene del otro banco.

    El mismo cobro aparece en el estado de quien lo procesa y en el de
    la cuenta que lo liquida, cada uno con su folio y su cuenta, y la
    deduplicación por folio no los empareja.
    """
    servicio.registrar(
        fecha=date(2026, 8, 2),
        tipo=TipoMovimiento.GASTO,
        monto=349.0,
        categoria_id=ids_catalogo["restaurantes"],
        cuenta_id=ids_catalogo["tarjeta"],
        referencia_externa="MP-11111",
    )

    parecidos = servicio.parecidos(date(2026, 8, 2), 349.0)

    assert len(parecidos) == 1
    assert parecidos.iloc[0]["referencia_externa"] == "MP-11111"


def test_parecidos_excluye_lo_que_se_le_diga(servicio, ids_catalogo):
    """Al editar, el movimiento no es su propio duplicado."""
    movimiento_id = servicio.registrar(
        fecha=date(2026, 8, 2),
        tipo=TipoMovimiento.GASTO,
        monto=349.0,
        categoria_id=ids_catalogo["restaurantes"],
        cuenta_id=ids_catalogo["cuenta"],
    )

    assert servicio.parecidos(date(2026, 8, 2), 349.0, excluir=[movimiento_id]).empty


def test_parecidos_ignora_otro_monto(servicio, ids_catalogo):
    """Un peso de diferencia ya es otro movimiento."""
    servicio.registrar(
        fecha=date(2026, 8, 2),
        tipo=TipoMovimiento.GASTO,
        monto=349.0,
        categoria_id=ids_catalogo["restaurantes"],
        cuenta_id=ids_catalogo["cuenta"],
    )

    assert servicio.parecidos(date(2026, 8, 2), 350.0).empty


def test_parecidos_sin_monto_no_busca_nada(servicio):
    """Mientras el monto está en cero no hay nada que comparar."""
    assert servicio.parecidos(date(2026, 8, 2), 0.0).empty
