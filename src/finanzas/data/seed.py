from __future__ import annotations

import logging

from finanzas.config.settings import settings
from finanzas.data.database import connect, init_database, tabla_vacia
from finanzas.data.repositories.catalogos_repository import CatalogosRepository
from finanzas.domain.entities import ReglasFinancieras
from finanzas.domain.enums import TipoCuenta

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# Catálogos base
#
# Son los mismos de la hoja «Catálogos» del Excel original,
# más las categorías de ingreso, ahorro e inversión que allá
# vivían mezcladas con las de gasto.
# ═══════════════════════════════════════════════════════════

#: (categoría, tipo, [subcategorías]) en el orden en que se muestran.
CATEGORIAS_BASE: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Vivienda", "Gasto", ("Renta/Hipoteca", "Mantenimiento", "Hogar")),
    ("Servicios", "Gasto", ("Luz", "Agua", "Gas", "Internet", "Telefonía")),
    ("Supermercado", "Gasto", ("Despensa",)),
    ("Restaurantes", "Gasto", ("Comida fuera", "Café/Snacks")),
    ("Transporte", "Gasto", ("Gasolina", "Transporte público", "Taxi/App")),
    ("Salud", "Gasto", ("Médico", "Medicinas", "Seguro")),
    ("Educación", "Gasto", ("Cursos", "Libros")),
    ("Entretenimiento", "Gasto", ("Cine/Eventos", "Hobbies")),
    ("Compras", "Gasto", ("Ropa", "Otro")),
    ("Suscripciones", "Gasto", ("Streaming", "Software")),
    ("Mascotas", "Gasto", ("Veterinario",)),
    ("Viajes", "Gasto", ("Hospedaje", "Vuelos")),
    ("Regalos", "Gasto", ("Otro",)),
    ("Impuestos", "Gasto", ("Otro",)),
    # Pagar la tarjeta o el capital de un préstamo no es gasto: es un
    # traspaso a una cuenta de deuda. Lo que sí se gasta son los intereses.
    ("Deudas", "Gasto", ("Intereses de tarjeta", "Intereses de préstamo")),
    ("Cuidado personal", "Gasto", ("Skincare", "Otro")),
    ("Tecnología", "Gasto", ("Hardware", "Software")),
    ("Donativos", "Gasto", ("Otro",)),
    ("Comisiones", "Gasto", ("Comisión bancaria",)),
    ("Otros", "Gasto", ("Otro",)),
    ("Sueldo", "Ingreso", ("Nómina", "Aguinaldo", "Bono")),
    ("Ingresos extra", "Ingreso", ("Freelance", "Venta", "Reembolso")),
    ("Rendimientos", "Ingreso", ("Intereses", "Dividendos")),
    ("Otros ingresos", "Ingreso", ("Otro",)),
    ("Ahorro programado", "Ahorro", ("Fondo de emergencia", "Meta")),
    ("Aportación a inversión", "Inversión", ("Portafolio", "Retiro")),
    ("Traspaso entre cuentas", "Transferencia", ("Otro",)),
)

#: Cuentas de arranque, con su tipo. El tipo no es decorativo: decide de
#: qué lado del balance cae la cuenta y qué significa mover dinero a ella.
CUENTAS_BASE: tuple[tuple[str, str], ...] = (
    ("Efectivo", str(TipoCuenta.EFECTIVO)),
    ("Cuenta principal", str(TipoCuenta.DEBITO)),
    ("Cuenta ahorro", str(TipoCuenta.AHORRO)),
    ("Tarjeta crédito", str(TipoCuenta.CREDITO)),
    ("Inversiones", str(TipoCuenta.INVERSION)),
    ("Otra", str(TipoCuenta.OTRO)),
)

#: Medios de pago del catálogo original.
MEDIOS_PAGO_BASE: tuple[str, ...] = (
    "Efectivo",
    "Débito",
    "Crédito",
    "Transferencia",
    "Domiciliación",
    "Otro",
)


def sembrar_catalogos(db_path: str | None = None) -> dict[str, int]:
    """
    Inserta los catálogos base si aún no existen.

    Es idempotente en filas y también en ids. La distinción importa: con
    `INSERT ... ON CONFLICT DO NOTHING` sobre una tabla `AUTOINCREMENT`,
    SQLite reserva el siguiente id *antes* de detectar el conflicto de
    `UNIQUE` y el `DO NOTHING` no lo devuelve, así que cada corrida
    quemaba un id por cada fila que ya existía. `WHERE NOT EXISTS` no
    llega a intentar el insert, y el contador queda intacto.

    Returns
    -------
    dict
        Cuántos elementos nuevos se insertaron por catálogo.
    """
    insertados = {"categorias": 0, "subcategorias": 0, "cuentas": 0, "medios_pago": 0}

    with connect(db_path) as conexion:
        for orden, (categoria, tipo, subcategorias) in enumerate(CATEGORIAS_BASE):
            cursor = conexion.execute(
                """
                INSERT INTO categorias (nombre, tipo, orden)
                SELECT ?, ?, ?
                WHERE NOT EXISTS (SELECT 1 FROM categorias WHERE nombre = ?)
                """,
                (categoria, tipo, orden, categoria),
            )
            insertados["categorias"] += cursor.rowcount

            categoria_id = conexion.execute(
                "SELECT id FROM categorias WHERE nombre = ?", (categoria,)
            ).fetchone()["id"]

            for subcategoria in subcategorias:
                cursor = conexion.execute(
                    """
                    INSERT INTO subcategorias (categoria_id, nombre)
                    SELECT ?, ?
                    WHERE NOT EXISTS (
                        SELECT 1 FROM subcategorias
                        WHERE categoria_id = ? AND nombre = ?
                    )
                    """,
                    (categoria_id, subcategoria, categoria_id, subcategoria),
                )
                insertados["subcategorias"] += cursor.rowcount

        for cuenta, tipo_cuenta in CUENTAS_BASE:
            cursor = conexion.execute(
                """
                INSERT INTO cuentas (nombre, tipo)
                SELECT ?, ?
                WHERE NOT EXISTS (SELECT 1 FROM cuentas WHERE nombre = ?)
                """,
                (cuenta, tipo_cuenta, cuenta),
            )
            insertados["cuentas"] += cursor.rowcount

        for medio in MEDIOS_PAGO_BASE:
            cursor = conexion.execute(
                """
                INSERT INTO medios_pago (nombre)
                SELECT ?
                WHERE NOT EXISTS (SELECT 1 FROM medios_pago WHERE nombre = ?)
                """,
                (medio, medio),
            )
            insertados["medios_pago"] += cursor.rowcount

    logger.info("Catálogos sembrados: %s", insertados)
    return insertados


def sembrar_reglas(db_path: str | None = None) -> None:
    """Guarda las reglas financieras por defecto si la tabla está vacía."""
    repositorio = CatalogosRepository(db_path)

    with connect(db_path) as conexion:
        ya_hay = conexion.execute("SELECT COUNT(*) AS n FROM configuracion").fetchone()[
            "n"
        ]

    if ya_hay:
        return

    repositorio.guardar_reglas(
        ReglasFinancieras(
            moneda=settings.moneda,
            meta_ahorro_inversion=settings.meta_ahorro_inversion,
            meses_fondo_emergencia=settings.meses_fondo_emergencia,
            max_deseos=settings.max_deseos,
            umbral_gasto_pequeno=settings.umbral_gasto_pequeno,
            alerta_presupuesto=settings.alerta_presupuesto,
            dia_inicio_ciclo=settings.dia_inicio_ciclo,
        )
    )
    logger.info("Reglas financieras inicializadas con los valores por defecto")


def preparar_base(db_path: str | None = None) -> None:
    """
    Deja la base lista para usarse: esquema, catálogos y reglas.

    Se llama al arrancar la aplicación, de modo que un archivo borrado o
    un clon recién bajado del repositorio funcionen sin pasos manuales.

    Los catálogos se siembran sólo cuando están vacíos. Sembrar en cada
    arranque resucitaba las categorías base que el usuario había borrado
    a propósito, y los catálogos son suyos en cuanto los toca.
    """
    init_database(db_path)

    if tabla_vacia("categorias", db_path):
        sembrar_catalogos(db_path)
    else:
        logger.debug("Catálogos ya poblados: se omite la siembra")

    sembrar_reglas(db_path)
