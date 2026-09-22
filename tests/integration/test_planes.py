from __future__ import annotations

from datetime import date

import pytest

from finanzas.application.services.deseos_service import (
    DeseoInvalidoError,
    DeseosService,
)
from finanzas.application.services.movimientos_service import MovimientosService
from finanzas.application.services.proyectos_service import (
    NombreDeProyectoDuplicadoError,
    ProyectoInvalidoError,
    ProyectosService,
)
from finanzas.data.database import connect
from finanzas.data.repositories.deseos_repository import DeseosRepository
from finanzas.data.repositories.movimientos_repository import MovimientosRepository
from finanzas.data.repositories.proyectos_repository import ProyectosRepository
from finanzas.domain.enums import EstadoDeseo, Prioridad, TipoMovimiento

# ═══════════════════════════════════════════════════════════
# Proyectos y lista de deseos
#
# Los dos responden a la misma pregunta desde lados distintos:
# el proyecto, cuánto llevo gastado en esto; el deseo, cuánto
# me falta para poder comprarlo.
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def proyectos(db_path: str) -> ProyectosService:
    """Servicio de proyectos sobre la base de prueba."""
    return ProyectosService(ProyectosRepository(db_path))


@pytest.fixture
def deseos(db_path: str) -> DeseosService:
    """Servicio de deseos sobre la base de prueba."""
    return DeseosService(DeseosRepository(db_path))


@pytest.fixture
def servicio(movimientos: MovimientosRepository) -> MovimientosService:
    """Servicio de movimientos, para asignar gasto a los proyectos."""
    return MovimientosService(movimientos)


# ═══════════════════════════════════════════════════════════
# Proyectos
# ═══════════════════════════════════════════════════════════


def test_un_proyecto_existe_desde_que_se_crea(proyectos):
    """Sin un solo movimiento todavía, para poder planearlo antes."""
    proyectos.crear(nombre="Remodelación", presupuesto=50_000.0)
    df = proyectos.listar()

    assert list(df["proyecto"]) == ["Remodelación"]
    assert df.iloc[0]["movimientos"] == 0
    assert df.iloc[0]["presupuesto"] == 50_000.0


def test_el_proyecto_acumula_el_gasto_de_sus_movimientos(
    proyectos, servicio, ids_catalogo
):
    """Lo que se le carga se refleja en su avance."""
    proyectos.crear(nombre="Remodelación", presupuesto=50_000.0)
    for monto in (12_000.0, 8_000.0):
        servicio.registrar(
            fecha=date(2026, 8, 1),
            tipo=TipoMovimiento.GASTO,
            monto=monto,
            categoria_id=ids_catalogo["vivienda"],
            cuenta_id=ids_catalogo["cuenta"],
            proyecto="Remodelación",
        )

    fila = proyectos.listar().iloc[0]

    assert fila["gasto"] == 20_000.0
    assert fila["disponible"] == 30_000.0
    assert fila["pct_usado"] == pytest.approx(0.4)


def test_un_proyecto_sin_presupuesto_no_inventa_un_avance(proyectos):
    """Sin tope no hay proporción que calcular."""
    proyectos.crear(nombre="Viaje")
    fila = proyectos.listar().iloc[0]

    assert fila["presupuesto"] == 0
    assert fila["pct_usado"] == 0.0


def test_renombrar_un_proyecto_arrastra_sus_movimientos(
    proyectos, servicio, ids_catalogo
):
    """
    El vínculo es por nombre, así que renombrar sin arrastrar lo partiría.

    Los movimientos quedarían apuntando a un proyecto que ya no existe y
    el gasto acumulado se perdería.
    """
    proyecto_id = proyectos.crear(nombre="Remodelación")
    servicio.registrar(
        fecha=date(2026, 8, 1),
        tipo=TipoMovimiento.GASTO,
        monto=12_000.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
        proyecto="Remodelación",
    )

    proyectos.actualizar(proyecto_id, nombre="Remodelación cocina")
    fila = proyectos.listar().iloc[0]

    assert fila["proyecto"] == "Remodelación cocina"
    assert fila["gasto"] == 12_000.0
    assert len(servicio.buscar(proyectos=["Remodelación cocina"])) == 1


def test_no_puede_haber_dos_proyectos_con_el_mismo_nombre(proyectos):
    """El nombre es la clave con la que los movimientos los encuentran."""
    proyectos.crear(nombre="Viaje")

    with pytest.raises(NombreDeProyectoDuplicadoError):
        proyectos.crear(nombre="Viaje")


def test_un_proyecto_no_puede_terminar_antes_de_empezar(proyectos):
    """Un rango invertido es un error de captura, no un dato."""
    with pytest.raises(ProyectoInvalidoError, match="anterior"):
        proyectos.crear(
            nombre="Obra",
            fecha_inicio=date(2026, 8, 1),
            fecha_fin=date(2026, 7, 1),
        )


def test_los_proyectos_viejos_aparecen_aunque_no_tengan_ficha(
    proyectos, servicio, ids_catalogo
):
    """
    Antes del catálogo, un proyecto era sólo texto en el movimiento.

    Siguen contando y se pueden adoptar para ponerles presupuesto.
    """
    servicio.registrar(
        fecha=date(2026, 8, 1),
        tipo=TipoMovimiento.GASTO,
        monto=5_000.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
        proyecto="Mudanza",
    )

    fila = proyectos.listar().iloc[0]
    assert fila["proyecto"] == "Mudanza"
    assert not fila["declarado"]

    proyectos.adoptar("Mudanza")
    fila = proyectos.listar().iloc[0]

    assert fila["declarado"]
    assert fila["gasto"] == 5_000.0


def test_eliminar_un_proyecto_no_borra_su_gasto(proyectos, servicio, ids_catalogo):
    """El gasto ocurrió aunque el proyecto deje de interesar."""
    proyecto_id = proyectos.crear(nombre="Viaje")
    servicio.registrar(
        fecha=date(2026, 8, 1),
        tipo=TipoMovimiento.GASTO,
        monto=5_000.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
        proyecto="Viaje",
    )

    proyectos.eliminar(proyecto_id)

    assert proyectos.listar().empty
    assert len(servicio.buscar()) == 1


def test_cerrar_un_proyecto_lo_saca_de_los_activos(proyectos):
    """Terminarlo no es borrarlo: su histórico sigue ahí."""
    proyecto_id = proyectos.crear(nombre="Viaje")

    proyectos.cerrar(proyecto_id)

    assert proyectos.listar(solo_activos=True).empty
    assert len(proyectos.listar()) == 1


# ═══════════════════════════════════════════════════════════
# Lista de deseos
# ═══════════════════════════════════════════════════════════


def test_un_deseo_que_alcanza_se_marca_como_tal(deseos):
    """Con saldo de sobra, el semáforo va en verde."""
    deseos.agregar(nombre="Audífonos", costo=2_000.0)
    fila = deseos.listar(saldo=5_000.0).iloc[0]

    assert fila["alcance"] == str(EstadoDeseo.ALCANZA)
    assert fila["cobertura"] == 1.0
    assert fila["faltante"] == 0.0


def test_un_deseo_casi_cubierto_se_distingue_del_que_falta_mucho(deseos):
    """
    Tres estados, no dos.

    «Casi» dice que vale la pena esperar unos días; «falta mucho» dice que
    no es la decisión de este mes, y mezclarlos pierde esa diferencia.
    """
    deseos.agregar(nombre="Casi", costo=1_000.0)
    deseos.agregar(nombre="Lejos", costo=10_000.0)

    df = deseos.listar(saldo=800.0).set_index("nombre")

    assert df.loc["Casi", "alcance"] == str(EstadoDeseo.CASI)
    assert df.loc["Lejos", "alcance"] == str(EstadoDeseo.LEJOS)


def test_el_faltante_dice_cuanto_hace_falta(deseos):
    """Lo accionable no es la proporción sino el número que falta juntar."""
    deseos.agregar(nombre="Bicicleta", costo=8_000.0)
    fila = deseos.listar(saldo=3_000.0).iloc[0]

    assert fila["faltante"] == 5_000.0
    assert fila["cobertura"] == pytest.approx(0.375)


def test_sin_saldo_nada_alcanza(deseos):
    """Cero disponible es cero, no «casi»."""
    deseos.agregar(nombre="Audífonos", costo=2_000.0)
    fila = deseos.listar(saldo=0.0).iloc[0]

    assert fila["alcance"] == str(EstadoDeseo.LEJOS)
    assert fila["cobertura"] == 0.0


def test_el_resumen_cuenta_cuantos_alcanzan(deseos):
    """La cifra que decide si hoy se compra algo."""
    deseos.agregar(nombre="Barato", costo=500.0)
    deseos.agregar(nombre="Medio", costo=1_500.0)
    deseos.agregar(nombre="Caro", costo=20_000.0)

    resumen = deseos.resumen(saldo=2_000.0)

    assert resumen["deseos"] == 3
    assert resumen["costo_total"] == 22_000.0
    assert resumen["alcanzan"] == 2
    assert resumen["falta_total"] == 20_000.0


def test_comprar_un_deseo_lo_saca_de_la_lista_sin_borrarlo(deseos):
    """Conservarlo permite ver después qué se quiso y qué se compró."""
    deseo_id = deseos.agregar(nombre="Audífonos", costo=2_000.0)

    deseos.marcar_comprado(deseo_id, date(2026, 8, 20))

    assert deseos.listar().empty
    historial = deseos.listar(incluir_comprados=True)
    assert len(historial) == 1
    assert historial.iloc[0]["comprado"]


def test_se_puede_devolver_un_deseo_a_la_lista(deseos):
    """Marcarlo comprado por error tiene vuelta atrás."""
    deseo_id = deseos.agregar(nombre="Audífonos", costo=2_000.0)
    deseos.marcar_comprado(deseo_id)

    deseos.devolver_a_la_lista(deseo_id)

    assert len(deseos.listar()) == 1


def test_los_deseos_se_ordenan_por_prioridad(deseos):
    """Lo urgente primero, y a igualdad de urgencia lo barato."""
    deseos.agregar(nombre="Baja", costo=100.0, prioridad=Prioridad.BAJA)
    deseos.agregar(nombre="Alta", costo=9_000.0, prioridad=Prioridad.ALTA)
    deseos.agregar(nombre="Media", costo=500.0, prioridad=Prioridad.MEDIA)

    assert list(deseos.listar()["nombre"]) == ["Alta", "Media", "Baja"]


def test_un_deseo_necesita_nombre(deseos):
    """Una lista de cosas sin nombre no sirve de nada."""
    with pytest.raises(DeseoInvalidoError, match="nombre"):
        deseos.agregar(nombre="   ", costo=100.0)


def test_el_saldo_sale_de_los_saldos_deducidos(deseos, db_path):
    """
    Se compara contra lo que hay, no contra lo que se capturó como gasto.

    Una cuenta sin saldo a favor no aparece: no hay con qué comprar.
    """
    from finanzas.application.services.patrimonio_service import PatrimonioService
    from finanzas.data.repositories.patrimonio_repository import PatrimonioRepository

    patrimonio = PatrimonioService(PatrimonioRepository(db_path))
    with connect(db_path) as conexion:
        cuenta = conexion.execute(
            "SELECT id FROM cuentas WHERE nombre = 'Cuenta principal'"
        ).fetchone()["id"]
    patrimonio.verificar_saldo(cuenta, date.today(), 15_000.0)

    saldos = deseos.saldos_por_cuenta()

    assert len(saldos) == 1
    assert saldos.iloc[0]["saldo"] == 15_000.0
    assert saldos.iloc[0]["cuenta"] == "Cuenta principal"


def test_los_pasivos_no_cuentan_como_saldo_disponible(deseos, db_path):
    """Deber en la tarjeta no es tener con qué comprar."""
    from finanzas.application.services.patrimonio_service import PatrimonioService
    from finanzas.data.repositories.patrimonio_repository import PatrimonioRepository

    patrimonio = PatrimonioService(PatrimonioRepository(db_path))
    with connect(db_path) as conexion:
        cuenta = conexion.execute(
            "SELECT id FROM cuentas WHERE nombre = 'Tarjeta crédito'"
        ).fetchone()["id"]
    patrimonio.verificar_saldo(cuenta, date.today(), 8_000.0)

    assert deseos.saldos_por_cuenta().empty


# ═══════════════════════════════════════════════════════════
# Productos de un movimiento
#
# El detalle de una compra de varias cosas. No parten el
# movimiento: es un solo gasto con una categoría, y esto dice
# qué había dentro.
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def productos(db_path: str):
    """Servicio de productos sobre la base de prueba."""
    from finanzas.application.services.productos_service import ProductosService
    from finanzas.data.repositories.productos_repository import ProductosRepository

    return ProductosService(ProductosRepository(db_path))


@pytest.fixture
def compra(servicio, ids_catalogo) -> int:
    """Una compra de varias cosas, sin detallar todavía."""
    return servicio.registrar(
        fecha=date(2026, 8, 5),
        tipo=TipoMovimiento.GASTO,
        monto=2_399.00,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
        descripcion="Compra de oficina",
    )


def _tabla(filas: list[dict]):
    """Arma la tabla como la entrega el editor de la página."""
    import pandas as pd

    return pd.DataFrame(filas)


def test_una_compra_puede_detallarse_en_productos(productos, compra):
    """El importe de cada partida sale de cantidad por precio."""
    productos.reemplazar(
        compra,
        _tabla(
            [
                {"producto": "Monitor", "cantidad": 1, "precio_unitario": 1_500.0},
                {"producto": "Cable", "cantidad": 2, "precio_unitario": 149.5},
                {"producto": "Soporte", "cantidad": 1, "precio_unitario": 600.0},
            ]
        ),
    )

    df = productos.de_movimiento(compra)

    assert list(df["producto"]) == ["Monitor", "Cable", "Soporte"]
    assert list(df["importe"]) == [1_500.0, 299.0, 600.0]


def test_el_movimiento_sigue_siendo_uno_solo(productos, compra, servicio):
    """
    Detallar no parte el gasto.

    Es la diferencia con repartir entre categorías: aquí sigue habiendo
    una compra de 2,399 en su categoría.
    """
    productos.reemplazar(
        compra,
        _tabla([{"producto": "Monitor", "cantidad": 1, "precio_unitario": 1_500.0}]),
    )

    movimientos = servicio.buscar()

    assert len(movimientos) == 1
    assert movimientos.iloc[0]["monto"] == 2_399.00


def test_el_desglose_dice_cuanto_falta_por_apuntar(productos, compra):
    """Un detalle parcial es legítimo y se informa, no se corrige."""
    productos.reemplazar(
        compra,
        _tabla([{"producto": "Monitor", "cantidad": 1, "precio_unitario": 1_500.0}]),
    )

    desglose = productos.desglose(compra)

    assert desglose["productos"] == 1
    assert desglose["desglosado"] == 1_500.0
    assert desglose["sin_desglosar"] == pytest.approx(899.0)


def test_reemplazar_deja_exactamente_lo_que_se_le_pasa(productos, compra):
    """
    Editar la lista de un ticket es rehacerla, no corregir filas sueltas.

    Reemplazar en bloque evita llevar la cuenta de qué se editó, qué se
    borró y qué es nuevo.
    """
    productos.reemplazar(
        compra,
        _tabla(
            [
                {"producto": "Monitor", "cantidad": 1, "precio_unitario": 1_500.0},
                {"producto": "Cable", "cantidad": 2, "precio_unitario": 149.5},
            ]
        ),
    )
    productos.reemplazar(
        compra,
        _tabla([{"producto": "Sólo esto", "cantidad": 1, "precio_unitario": 10.0}]),
    )

    df = productos.de_movimiento(compra)

    assert list(df["producto"]) == ["Sólo esto"]


def test_las_filas_vacias_del_editor_se_descartan(productos, compra):
    """El editor deja renglones en blanco al agregar; no son productos."""
    productos.reemplazar(
        compra,
        _tabla(
            [
                {"producto": "Monitor", "cantidad": 1, "precio_unitario": 1_500.0},
                {"producto": "   ", "cantidad": 1, "precio_unitario": 0.0},
                {"producto": None, "cantidad": None, "precio_unitario": None},
            ]
        ),
    )

    assert len(productos.de_movimiento(compra)) == 1


def test_borrar_el_movimiento_se_lleva_su_detalle(productos, compra, servicio):
    """Los productos son parte del movimiento, no entidades propias."""
    productos.reemplazar(
        compra,
        _tabla([{"producto": "Monitor", "cantidad": 1, "precio_unitario": 1_500.0}]),
    )

    servicio.eliminar(compra)

    assert productos.de_movimiento(compra).empty


def test_se_puede_buscar_un_articulo_entre_todas_las_compras(
    productos, compra, servicio, ids_catalogo
):
    """
    Es la razón de ser de guardar el detalle.

    Permite preguntar cuánto se lleva gastado en algo que nunca fue una
    categoría propia.
    """
    otra = servicio.registrar(
        fecha=date(2026, 9, 1),
        tipo=TipoMovimiento.GASTO,
        monto=400.0,
        categoria_id=ids_catalogo["restaurantes"],
        cuenta_id=ids_catalogo["cuenta"],
    )
    productos.reemplazar(
        compra,
        _tabla([{"producto": "Cable HDMI", "cantidad": 2, "precio_unitario": 149.5}]),
    )
    productos.reemplazar(
        otra,
        _tabla([{"producto": "Cable USB", "cantidad": 1, "precio_unitario": 200.0}]),
    )

    encontrados = productos.buscar("Cable")

    assert len(encontrados) == 2
    assert set(encontrados["producto"]) == {"Cable HDMI", "Cable USB"}


def test_un_movimiento_sin_detalle_no_reporta_productos(productos, compra):
    """Lo normal es no detallar: el estado vacío también tiene que contestar."""
    desglose = productos.desglose(compra)

    assert desglose["productos"] == 0
    assert desglose["desglosado"] == 0.0
    assert desglose["sin_desglosar"] == 2_399.00
