from __future__ import annotations

import sqlite3

import pytest

from finanzas.application.services.patrimonio_service import (
    CuentaYaEnBalanceError,
    PatrimonioService,
)
from finanzas.application.services.suscripciones_service import SuscripcionesService
from finanzas.data.database import connect
from finanzas.data.repositories.catalogos_repository import CatalogosRepository
from finanzas.data.repositories.patrimonio_repository import PatrimonioRepository
from finanzas.data.repositories.suscripciones_repository import SuscripcionesRepository
from finanzas.data.seed import preparar_base, sembrar_catalogos
from finanzas.domain.enums import Liquidez, TipoPatrimonio

# ═══════════════════════════════════════════════════════════
# Integridad del esquema
#
# Lo que estas pruebas cuidan no se ve en la interfaz: ids que
# no se desbocan, catálogos que no resucitan y clasificaciones
# que no se cruzan.
# ═══════════════════════════════════════════════════════════


def _secuencias(db_path: str) -> dict[str, int]:
    """Devuelve el contador AUTOINCREMENT de cada tabla."""
    with connect(db_path) as conexion:
        filas = conexion.execute("SELECT name, seq FROM sqlite_sequence").fetchall()

    return {fila["name"]: int(fila["seq"]) for fila in filas}


# ═══════════════════════════════════════════════════════════
# Sembrado: idempotente en filas y también en ids
# ═══════════════════════════════════════════════════════════


def test_sembrar_de_nuevo_no_consume_ids(db_path: str):
    """
    Resembrar no mueve los contadores AUTOINCREMENT.

    Regresión del bug que infló los ids: `INSERT ... ON CONFLICT DO
    NOTHING` reserva el siguiente id antes de detectar el conflicto de
    `UNIQUE` y el `DO NOTHING` no lo devuelve, así que cada corrida
    quemaba un id por cada fila que ya existía.
    """
    antes = _secuencias(db_path)

    for _ in range(5):
        insertados = sembrar_catalogos(db_path)
        assert insertados == {
            "categorias": 0,
            "subcategorias": 0,
            "cuentas": 0,
            "medios_pago": 0,
        }

    assert _secuencias(db_path) == antes


def test_los_ids_del_catalogo_sembrado_son_contiguos(db_path: str):
    """Una base recién sembrada numera cada catálogo de 1 a N."""
    with connect(db_path) as conexion:
        for tabla in ("categorias", "subcategorias", "cuentas", "medios_pago"):
            fila = conexion.execute(
                f"SELECT COUNT(*) AS n, MIN(id) AS minimo, MAX(id) AS maximo "
                f"FROM {tabla}"
            ).fetchone()

            assert fila["minimo"] == 1
            assert fila["maximo"] == fila["n"], f"{tabla} tiene huecos de id"


def test_preparar_base_no_resucita_lo_borrado(db_path: str, catalogos):
    """
    Un catálogo borrado a propósito no vuelve en el siguiente arranque.

    Los catálogos son del usuario en cuanto los toca; sembrar en cada
    arranque revivía las categorías base que había eliminado.
    """
    medio_id = catalogos.mapa_nombre_id("medios_pago")["Domiciliación"]
    catalogos.eliminar_medio_pago(medio_id)

    preparar_base(db_path)

    assert "Domiciliación" not in catalogos.mapa_nombre_id("medios_pago")


# ═══════════════════════════════════════════════════════════
# Patrimonio ligado a una cuenta del catálogo
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def patrimonio(db_path: str) -> PatrimonioService:
    """Servicio de patrimonio sobre la base de prueba."""
    return PatrimonioService(PatrimonioRepository(db_path))


def test_una_posicion_ligada_trae_el_nombre_de_su_cuenta(
    patrimonio: PatrimonioService, ids_catalogo: dict[str, int]
):
    """El balance resuelve la cuenta ligada sin recapturar su nombre."""
    patrimonio.crear(
        nombre="Cuenta principal",
        tipo=TipoPatrimonio.ACTIVO,
        saldo=25_000.0,
        cuenta_id=ids_catalogo["cuenta"],
        liquidez=Liquidez.ALTA,
    )

    fila = patrimonio.balance().iloc[0]

    assert fila["cuenta"] == "Cuenta principal"
    assert fila["aporte_a_patrimonio"] == 25_000.0


def test_una_posicion_sin_ligar_deja_la_cuenta_vacia(patrimonio: PatrimonioService):
    """Lo que no es cuenta —la casa, el auto— vive en el balance sin vínculo."""
    patrimonio.crear(nombre="Casa", tipo=TipoPatrimonio.ACTIVO, saldo=1_800_000.0)

    fila = patrimonio.balance().iloc[0]

    assert fila["cuenta"] == ""
    assert fila["cuenta_id"] is None or fila["cuenta_id"] != fila["cuenta_id"]


def test_una_cuenta_no_puede_estar_dos_veces_en_el_balance(
    patrimonio: PatrimonioService, ids_catalogo: dict[str, int]
):
    """Duplicar la cuenta duplicaría su saldo en el patrimonio neto."""
    patrimonio.crear(
        nombre="Cuenta principal",
        tipo=TipoPatrimonio.ACTIVO,
        saldo=25_000.0,
        cuenta_id=ids_catalogo["cuenta"],
    )

    with pytest.raises(CuentaYaEnBalanceError):
        patrimonio.crear(
            nombre="La misma cuenta",
            tipo=TipoPatrimonio.ACTIVO,
            saldo=1.0,
            cuenta_id=ids_catalogo["cuenta"],
        )


def test_actualizar_el_saldo_conserva_la_cuenta_ligada(
    patrimonio: PatrimonioService, ids_catalogo: dict[str, int]
):
    """La operación mensual no debe romper el vínculo con la cuenta."""
    posicion_id = patrimonio.crear(
        nombre="Cuenta principal",
        tipo=TipoPatrimonio.ACTIVO,
        saldo=25_000.0,
        cuenta_id=ids_catalogo["cuenta"],
    )

    patrimonio.actualizar_saldo(posicion_id, 31_000.0)
    fila = patrimonio.balance().iloc[0]

    assert fila["saldo"] == 31_000.0
    assert fila["cuenta"] == "Cuenta principal"


def test_las_cuentas_sin_saldo_se_reportan_como_pendientes(
    patrimonio: PatrimonioService, ids_catalogo: dict[str, int]
):
    """Un balance incompleto tiene que poder decir qué le falta."""
    pendientes_antes = set(patrimonio.cuentas_sin_posicion()["nombre"])
    assert "Cuenta principal" in pendientes_antes

    patrimonio.crear(
        nombre="Cuenta principal",
        tipo=TipoPatrimonio.ACTIVO,
        saldo=25_000.0,
        cuenta_id=ids_catalogo["cuenta"],
    )

    pendientes = set(patrimonio.cuentas_sin_posicion()["nombre"])

    assert "Cuenta principal" not in pendientes
    assert pendientes == pendientes_antes - {"Cuenta principal"}


def test_borrar_la_cuenta_no_borra_su_saldo_del_balance(
    patrimonio: PatrimonioService, catalogos, ids_catalogo: dict[str, int]
):
    """
    Dar de baja una cuenta desliga la posición, no la elimina.

    El saldo es un hecho del balance: perderlo al limpiar el catálogo
    falsearía el patrimonio neto hacia abajo.
    """
    patrimonio.crear(
        nombre="Cuenta principal",
        tipo=TipoPatrimonio.ACTIVO,
        saldo=25_000.0,
        cuenta_id=ids_catalogo["cuenta"],
    )

    catalogos.eliminar_cuenta(ids_catalogo["cuenta"])
    fila = patrimonio.balance().iloc[0]

    assert fila["saldo"] == 25_000.0
    assert fila["cuenta"] == ""
    assert patrimonio.resumen()["patrimonio_neto"] == 25_000.0


# ═══════════════════════════════════════════════════════════
# Suscripciones con subcategoría
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def suscripciones(db_path: str) -> SuscripcionesService:
    """Servicio de suscripciones sobre la base de prueba."""
    return SuscripcionesService(
        SuscripcionesRepository(db_path), CatalogosRepository(db_path)
    )


@pytest.fixture
def streaming(catalogos: CatalogosRepository) -> dict[str, int]:
    """La subcategoría «Streaming» y la categoría de la que cuelga."""
    subcategorias = catalogos.listar_subcategorias()
    fila = subcategorias[subcategorias["nombre"] == "Streaming"].iloc[0]

    return {"categoria_id": int(fila["categoria_id"]), "id": int(fila["id"])}


def test_una_suscripcion_guarda_y_resuelve_su_subcategoria(
    suscripciones: SuscripcionesService, streaming: dict[str, int]
):
    """La subcategoría vuelve resuelta a nombre, como la categoría."""
    suscripciones.crear(
        servicio="Netflix",
        costo_por_cobro=299.0,
        categoria_id=streaming["categoria_id"],
        subcategoria_id=streaming["id"],
    )

    fila = suscripciones.listar().iloc[0]

    assert fila["categoria"] == "Suscripciones"
    assert fila["subcategoria"] == "Streaming"


def test_una_subcategoria_de_otra_categoria_se_rechaza(
    suscripciones: SuscripcionesService,
    streaming: dict[str, int],
    catalogos: CatalogosRepository,
):
    """
    El esquema no puede exigir que las dos claves concuerden.

    `categoria_id` y `subcategoria_id` van por separado, así que nada
    en SQLite impide guardar Suscripciones → Nómina. Lo valida el
    servicio.
    """
    subcategorias = catalogos.listar_subcategorias()
    ajena = int(subcategorias[subcategorias["nombre"] == "Nómina"].iloc[0]["id"])

    with pytest.raises(ValueError, match="no pertenece"):
        suscripciones.crear(
            servicio="Mal clasificada",
            costo_por_cobro=100.0,
            categoria_id=streaming["categoria_id"],
            subcategoria_id=ajena,
        )


def test_una_suscripcion_sin_subcategoria_es_valida(
    suscripciones: SuscripcionesService, streaming: dict[str, int]
):
    """La subcategoría es opcional: no clasificar a detalle no es un error."""
    suscripciones.crear(
        servicio="Spotify",
        costo_por_cobro=129.0,
        categoria_id=streaming["categoria_id"],
    )

    assert suscripciones.listar().iloc[0]["subcategoria"] == ""


def test_actualizar_una_suscripcion_conserva_su_subcategoria(
    suscripciones: SuscripcionesService, streaming: dict[str, int]
):
    """Cambiar el costo no debe desclasificar la suscripción."""
    suscripcion_id = suscripciones.crear(
        servicio="Netflix",
        costo_por_cobro=299.0,
        categoria_id=streaming["categoria_id"],
        subcategoria_id=streaming["id"],
    )

    suscripciones.actualizar(suscripcion_id, costo_por_cobro=349.0)
    fila = suscripciones.listar().iloc[0]

    assert fila["costo_por_cobro"] == 349.0
    assert fila["subcategoria"] == "Streaming"


def test_borrar_la_subcategoria_desliga_la_suscripcion(
    suscripciones: SuscripcionesService,
    streaming: dict[str, int],
    db_path: str,
):
    """Al eliminar la subcategoría, la suscripción sobrevive sin ella."""
    suscripciones.crear(
        servicio="Netflix",
        costo_por_cobro=299.0,
        categoria_id=streaming["categoria_id"],
        subcategoria_id=streaming["id"],
    )

    with connect(db_path) as conexion:
        conexion.execute("DELETE FROM subcategorias WHERE id = ?", (streaming["id"],))

    fila = suscripciones.listar().iloc[0]

    assert fila["servicio"] == "Netflix"
    assert fila["subcategoria"] == ""


# ═══════════════════════════════════════════════════════════
# Migraciones
# ═══════════════════════════════════════════════════════════


def test_las_migraciones_son_idempotentes(db_path: str):
    """Volver a migrar una base ya migrada no intenta nada."""
    from finanzas.data.database import aplicar_migraciones

    assert aplicar_migraciones(db_path) == []


def test_una_base_del_esquema_anterior_se_migra(tmp_path):
    """
    Una base creada sin las columnas nuevas las recibe al arrancar.

    Simula el clon que ya tenía datos antes del cambio de esquema.
    """
    ruta = tmp_path / "vieja.db"
    conexion = sqlite3.connect(ruta)
    conexion.executescript(
        """
        CREATE TABLE cuentas (
            id     INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL UNIQUE,
            tipo   TEXT NOT NULL DEFAULT 'Banco',
            activa INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE patrimonio (
            id     INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            tipo   TEXT NOT NULL CHECK (tipo IN ('Activo', 'Pasivo')),
            saldo  REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE suscripciones (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            servicio        TEXT NOT NULL,
            costo_por_cobro REAL NOT NULL DEFAULT 0
        );
        INSERT INTO patrimonio (nombre, tipo, saldo) VALUES ('Casa', 'Activo', 100);
        INSERT INTO suscripciones (servicio, costo_por_cobro) VALUES ('Vieja', 50);
        """
    )
    conexion.commit()
    conexion.close()

    preparar_base(str(ruta))

    with connect(str(ruta)) as conexion:
        columnas_patrimonio = {
            fila["name"]
            for fila in conexion.execute("PRAGMA table_info(patrimonio)").fetchall()
        }
        columnas_suscripciones = {
            fila["name"]
            for fila in conexion.execute("PRAGMA table_info(suscripciones)").fetchall()
        }
        # Los datos anteriores siguen ahí.
        casa = conexion.execute(
            "SELECT saldo FROM patrimonio WHERE nombre = 'Casa'"
        ).fetchone()

    assert "cuenta_id" in columnas_patrimonio
    assert "subcategoria_id" in columnas_suscripciones
    assert casa["saldo"] == 100


def test_un_relleno_no_tumba_una_base_a_la_que_le_falta_su_columna(tmp_path):
    """
    Migrar tiene que funcionar aunque la base venga de muy atrás.

    El relleno de `descripcion_banco` lee la nota; si una base antigua no
    la tiene, se omite el relleno y la columna se añade igual. Fallar aquí
    dejaría la aplicación sin poder abrir.
    """
    ruta = tmp_path / "muy_vieja.db"
    conexion = sqlite3.connect(ruta)
    conexion.executescript(
        """
        CREATE TABLE movimientos (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha        TEXT NOT NULL,
            tipo         TEXT NOT NULL,
            monto        REAL NOT NULL,
            categoria_id INTEGER NOT NULL,
            cuenta_id    INTEGER NOT NULL,
            estado       TEXT NOT NULL DEFAULT 'Confirmado'
        );
        INSERT INTO movimientos (fecha, tipo, monto, categoria_id, cuenta_id)
        VALUES ('2026-07-01', 'Gasto', 300, 1, 1);
        """
    )
    conexion.commit()
    conexion.close()

    preparar_base(str(ruta))

    with connect(str(ruta)) as conexion:
        columnas = {
            fila["name"]
            for fila in conexion.execute("PRAGMA table_info(movimientos)").fetchall()
        }
        fila = conexion.execute(
            "SELECT descripcion_banco, fecha_pago FROM movimientos"
        ).fetchone()

    assert {"descripcion_banco", "referencia_externa"} <= columnas
    assert fila["descripcion_banco"] == ""
    # El relleno que sí podía correr, corrió.
    assert fila["fecha_pago"] == "2026-07-01"
