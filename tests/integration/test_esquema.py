from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from finanzas.application.services.movimientos_service import MovimientosService
from finanzas.application.services.patrimonio_service import PatrimonioService
from finanzas.application.services.suscripciones_service import SuscripcionesService
from finanzas.data.database import connect
from finanzas.data.repositories.catalogos_repository import CatalogosRepository
from finanzas.data.repositories.movimientos_repository import MovimientosRepository
from finanzas.data.repositories.patrimonio_repository import PatrimonioRepository
from finanzas.data.repositories.suscripciones_repository import SuscripcionesRepository
from finanzas.data.seed import preparar_base, sembrar_catalogos
from finanzas.domain.enums import TipoMovimiento, TipoPatrimonio
from finanzas.domain.saldos import Sentido

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


def test_una_posicion_que_no_es_cuenta_vive_en_el_balance(
    patrimonio: PatrimonioService,
):
    """Lo que no es cuenta —la casa, el auto— se captura con su valor."""
    patrimonio.crear(nombre="Casa", tipo=TipoPatrimonio.ACTIVO, saldo=1_800_000.0)

    fila = patrimonio.balance().iloc[0]

    assert fila["cuenta"] == ""
    assert fila["aporte_a_patrimonio"] == 1_800_000.0
    assert patrimonio.resumen()["patrimonio_neto"] == 1_800_000.0


def test_actualizar_el_valor_de_una_posicion(patrimonio: PatrimonioService):
    """La operación ocasional: sólo cambia el número."""
    posicion_id = patrimonio.crear(
        nombre="Auto", tipo=TipoPatrimonio.ACTIVO, saldo=250_000.0
    )

    patrimonio.actualizar_saldo(posicion_id, 230_000.0)
    fila = patrimonio.balance().iloc[0]

    assert fila["saldo"] == 230_000.0


# ═══════════════════════════════════════════════════════════
# Saldos deducidos y verificados
#
# El saldo de una cuenta no se captura: se deduce de los
# movimientos a partir de un saldo verificado. Estas pruebas
# fijan que la deducción funcione en los dos sentidos y que
# avise cuando algo no cuadra.
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def movimientos_servicio(db_path: str) -> MovimientosService:
    """Servicio de movimientos sobre la base de prueba."""
    return MovimientosService(MovimientosRepository(db_path))


def _gasto(servicio, ids, monto: float, dia: int) -> int:
    return servicio.registrar(
        fecha=date(2026, 8, dia),
        tipo=TipoMovimiento.GASTO,
        monto=monto,
        categoria_id=ids["vivienda"],
        cuenta_id=ids["cuenta"],
    )


def test_sin_saldo_verificado_se_suma_desde_cero_y_se_avisa(
    patrimonio: PatrimonioService, movimientos_servicio, ids_catalogo
):
    """Un saldo sin ancla no es mentira, pero tampoco es verdad: se marca."""
    _gasto(movimientos_servicio, ids_catalogo, 1_000.0, 5)

    saldos = patrimonio.saldos(date(2026, 8, 31)).set_index("cuenta")
    principal = saldos.loc["Cuenta principal"]

    assert principal["saldo"] == -1_000.0
    assert not principal["verificado"]
    assert principal["sentido"] == str(Sentido.SIN_ANCLA)
    assert "Cuenta principal" in set(patrimonio.cuentas_sin_ancla()["nombre"])


def test_el_saldo_se_deduce_hacia_adelante_desde_el_ancla(
    patrimonio: PatrimonioService, movimientos_servicio, ids_catalogo
):
    """Con un saldo verificado antes, se suman los movimientos posteriores."""
    patrimonio.verificar_saldo(ids_catalogo["cuenta"], date(2026, 7, 31), 10_000.0)
    _gasto(movimientos_servicio, ids_catalogo, 1_000.0, 5)
    _gasto(movimientos_servicio, ids_catalogo, 500.0, 20)

    al_10 = patrimonio.saldos(date(2026, 8, 10)).set_index("cuenta")
    al_31 = patrimonio.saldos(date(2026, 8, 31)).set_index("cuenta")

    assert al_10.loc["Cuenta principal", "saldo"] == 9_000.0
    assert al_31.loc["Cuenta principal", "saldo"] == 8_500.0
    assert al_31.loc["Cuenta principal", "sentido"] == str(Sentido.ADELANTE)


def test_el_saldo_se_deduce_hacia_atras_desde_el_saldo_de_hoy(
    patrimonio: PatrimonioService, movimientos_servicio, ids_catalogo
):
    """
    El caso de quien carga estados viejos: sólo sabe cuánto tiene hoy.

    Con el saldo de hoy y los movimientos, el sistema deduce cuánto había
    al principio.
    """
    _gasto(movimientos_servicio, ids_catalogo, 1_000.0, 5)
    _gasto(movimientos_servicio, ids_catalogo, 500.0, 20)
    patrimonio.verificar_saldo(ids_catalogo["cuenta"], date(2026, 8, 31), 8_500.0)

    inicio = patrimonio.saldos(date(2026, 7, 31)).set_index("cuenta")
    medio = patrimonio.saldos(date(2026, 8, 10)).set_index("cuenta")

    assert inicio.loc["Cuenta principal", "saldo"] == 10_000.0
    assert inicio.loc["Cuenta principal", "sentido"] == str(Sentido.ATRAS)
    assert medio.loc["Cuenta principal", "saldo"] == 9_000.0


def test_dos_saldos_verificados_que_no_cuadran_se_reportan(
    patrimonio: PatrimonioService, movimientos_servicio, ids_catalogo
):
    """Entre dos anclas, los movimientos tienen que explicar la diferencia."""
    patrimonio.verificar_saldo(ids_catalogo["cuenta"], date(2026, 7, 31), 10_000.0)
    _gasto(movimientos_servicio, ids_catalogo, 1_000.0, 5)
    patrimonio.verificar_saldo(ids_catalogo["cuenta"], date(2026, 8, 31), 8_700.0)

    descuadres = patrimonio.descuadres()

    assert len(descuadres) == 1
    fila = descuadres.iloc[0]
    assert fila["cuenta"] == "Cuenta principal"
    assert fila["esperado"] == 9_000.0
    # Faltan 300 que salieron sin registrarse.
    assert fila["diferencia"] == -300.0


def test_el_saldo_de_una_tarjeta_se_captura_como_lo_ensena_el_banco(
    patrimonio: PatrimonioService, movimientos_servicio, ids_catalogo
):
    """Deber 2,000 se captura como 2,000 y en el libro es -2,000."""
    patrimonio.verificar_saldo(ids_catalogo["tarjeta"], date(2026, 7, 31), 2_000.0)
    movimientos_servicio.registrar(
        fecha=date(2026, 8, 5),
        tipo=TipoMovimiento.GASTO,
        monto=500.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["tarjeta"],
    )

    saldos = patrimonio.saldos(date(2026, 8, 31)).set_index("cuenta")
    tarjeta = saldos.loc["Tarjeta crédito"]
    resumen = patrimonio.resumen(date(2026, 8, 31))

    assert tarjeta["saldo"] == -2_500.0
    assert tarjeta["saldo_visto"] == 2_500.0
    assert tarjeta["lado"] == "Pasivo"
    assert resumen["pasivos"] == 2_500.0


def test_verificar_el_mismo_dia_dos_veces_corrige_en_vez_de_duplicar(
    patrimonio: PatrimonioService, ids_catalogo
):
    """Una cuenta cierra un día con un solo saldo."""
    patrimonio.verificar_saldo(ids_catalogo["cuenta"], date(2026, 7, 31), 10_000.0)
    patrimonio.verificar_saldo(ids_catalogo["cuenta"], date(2026, 7, 31), 12_000.0)

    anclas = patrimonio.anclas()

    assert len(anclas) == 1
    assert anclas.iloc[0]["saldo"] == 12_000.0


def test_la_evolucion_mensual_se_deduce(
    patrimonio: PatrimonioService, movimientos_servicio, ids_catalogo
):
    """El cierre de cada mes ya no se captura: sale de los saldos."""
    patrimonio.verificar_saldo(ids_catalogo["cuenta"], date(2026, 7, 31), 10_000.0)
    _gasto(movimientos_servicio, ids_catalogo, 1_000.0, 5)

    evolucion = patrimonio.evolucion(hasta=date(2026, 8, 31)).set_index("periodo")

    assert evolucion.loc["2026-07", "patrimonio_neto"] == 10_000.0
    assert evolucion.loc["2026-08", "patrimonio_neto"] == 9_000.0
    assert evolucion.loc["2026-08", "cambio_mensual"] == -1_000.0


def test_el_balance_capturado_antes_se_convierte_en_saldo_verificado(tmp_path):
    """
    Una base vieja traía el saldo de la cuenta como posición del balance.

    Al migrar, ese saldo no se pierde: pasa a ser un saldo verificado en su
    fecha de corte, que es exactamente lo que era.
    """
    ruta = tmp_path / "vieja.db"
    preparar_base(str(ruta))
    with connect(str(ruta)) as conexion:
        cuenta = conexion.execute(
            "SELECT id FROM cuentas WHERE nombre = 'Tarjeta crédito'"
        ).fetchone()["id"]
        conexion.execute(
            "INSERT INTO patrimonio (nombre, tipo, saldo, cuenta_id, fecha_corte) "
            "VALUES (?, 'Pasivo', ?, ?, ?)",
            ("Tarjeta crédito", 8_000.0, cuenta, "2026-08-31"),
        )

    preparar_base(str(ruta))
    anclas = PatrimonioService(PatrimonioRepository(str(ruta))).anclas()

    assert len(anclas) == 1
    assert anclas.iloc[0]["cuenta"] == "Tarjeta crédito"
    assert anclas.iloc[0]["saldo"] == -8_000.0
    with connect(str(ruta)) as conexion:
        ligadas = conexion.execute(
            "SELECT COUNT(*) AS n FROM patrimonio WHERE cuenta_id IS NOT NULL"
        ).fetchone()["n"]
    assert ligadas == 0


def test_las_cuentas_de_banco_viejas_pasan_a_debito(tmp_path):
    """«Banco» era el tipo genérico; ahora es Débito, y el resto no se toca."""
    ruta = tmp_path / "vieja.db"
    conexion = sqlite3.connect(ruta)
    conexion.executescript(
        """
        CREATE TABLE cuentas (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre      TEXT NOT NULL UNIQUE,
            tipo        TEXT NOT NULL DEFAULT 'Banco',
            institucion TEXT NOT NULL DEFAULT '',
            activa      INTEGER NOT NULL DEFAULT 1
        );
        INSERT INTO cuentas (nombre, tipo) VALUES
            ('BBVA TDD', 'Banco'),
            ('BBVA TDC', 'Crédito'),
            ('Rara', 'Cripto');
        """
    )
    conexion.commit()
    conexion.close()

    preparar_base(str(ruta))
    catalogo = CatalogosRepository(str(ruta))
    tipos = catalogo.tipos_de_cuentas()
    nombres = catalogo.mapa_nombre_id("cuentas")

    assert tipos[nombres["BBVA TDD"]] == "Débito"
    assert tipos[nombres["BBVA TDC"]] == "Crédito"
    assert tipos[nombres["Rara"]] == "Otro"


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
