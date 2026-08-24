from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from finanzas.config.settings import settings

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# Esquema relacional
#
# Sólo se persiste lo que el usuario captura. Todo lo que en
# el Excel era una columna con fórmula (impacto en caja, gasto
# real, patrimonio creado, mes/año/semana) se resuelve en la
# vista `v_movimientos`, de modo que no pueda desincronizarse.
# ═══════════════════════════════════════════════════════════

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

-- ── Catálogos ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS categorias (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre  TEXT    NOT NULL UNIQUE,
    tipo    TEXT    NOT NULL DEFAULT 'Gasto'
            CHECK (tipo IN ('Ingreso', 'Gasto', 'Ahorro', 'Inversión',
                            'Transferencia')),
    activa  INTEGER NOT NULL DEFAULT 1 CHECK (activa IN (0, 1)),
    orden   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS subcategorias (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    categoria_id INTEGER NOT NULL REFERENCES categorias(id) ON DELETE CASCADE,
    nombre       TEXT    NOT NULL,
    activa       INTEGER NOT NULL DEFAULT 1 CHECK (activa IN (0, 1)),
    UNIQUE (categoria_id, nombre)
);

CREATE TABLE IF NOT EXISTS cuentas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre      TEXT    NOT NULL UNIQUE,
    tipo        TEXT    NOT NULL DEFAULT 'Banco',
    institucion TEXT    NOT NULL DEFAULT '',
    activa      INTEGER NOT NULL DEFAULT 1 CHECK (activa IN (0, 1))
);

CREATE TABLE IF NOT EXISTS medios_pago (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT    NOT NULL UNIQUE,
    activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1))
);

-- ── Movimientos ──────────────────────────────────────────

CREATE TABLE IF NOT EXISTS movimientos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha           TEXT    NOT NULL,
    tipo            TEXT    NOT NULL
                    CHECK (tipo IN ('Ingreso', 'Gasto', 'Ahorro', 'Inversión',
                                    'Transferencia')),
    monto           REAL    NOT NULL CHECK (monto > 0),
    categoria_id    INTEGER NOT NULL REFERENCES categorias(id),
    subcategoria_id INTEGER          REFERENCES subcategorias(id),
    cuenta_id       INTEGER NOT NULL REFERENCES cuentas(id),
    medio_pago_id   INTEGER          REFERENCES medios_pago(id),
    descripcion     TEXT    NOT NULL DEFAULT '',
    necesidad       TEXT    NOT NULL DEFAULT 'Esencial'
                    CHECK (necesidad IN ('Esencial', 'Deseo')),
    naturaleza      TEXT    NOT NULL DEFAULT 'Variable'
                    CHECK (naturaleza IN ('Fijo', 'Variable')),
    recurrente      INTEGER NOT NULL DEFAULT 0 CHECK (recurrente IN (0, 1)),
    planeado        INTEGER NOT NULL DEFAULT 1 CHECK (planeado IN (0, 1)),
    proyecto        TEXT    NOT NULL DEFAULT '',
    etiquetas       TEXT    NOT NULL DEFAULT '',
    nota            TEXT    NOT NULL DEFAULT '',
    estado          TEXT    NOT NULL DEFAULT 'Confirmado'
                    CHECK (estado IN ('Confirmado', 'Pendiente')),
    creado_en       TEXT    NOT NULL DEFAULT (datetime('now')),
    actualizado_en  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS ix_movimientos_fecha ON movimientos(fecha);
CREATE INDEX IF NOT EXISTS ix_movimientos_categoria ON movimientos(categoria_id);
CREATE INDEX IF NOT EXISTS ix_movimientos_tipo_estado ON movimientos(tipo, estado);

-- ── Presupuesto ──────────────────────────────────────────

CREATE TABLE IF NOT EXISTS presupuestos (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    periodo      TEXT    NOT NULL,                  -- YYYY-MM
    categoria_id INTEGER NOT NULL REFERENCES categorias(id) ON DELETE CASCADE,
    monto_manual REAL    NOT NULL DEFAULT 0 CHECK (monto_manual >= 0),
    pct_recorte  REAL    NOT NULL DEFAULT 0 CHECK (pct_recorte BETWEEN 0 AND 1),
    UNIQUE (periodo, categoria_id)
);

-- ── Metas ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS metas (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    objetivo                 TEXT    NOT NULL,
    tipo                     TEXT    NOT NULL DEFAULT 'Seguridad',
    monto_meta               REAL    NOT NULL CHECK (monto_meta > 0),
    acumulado                REAL    NOT NULL DEFAULT 0 CHECK (acumulado >= 0),
    aporte_mensual_planeado  REAL    NOT NULL DEFAULT 0,
    fecha_limite             TEXT,
    prioridad                TEXT    NOT NULL DEFAULT 'Media'
                             CHECK (prioridad IN ('Alta', 'Media', 'Baja')),
    vehiculo                 TEXT    NOT NULL DEFAULT '',
    notas                    TEXT    NOT NULL DEFAULT ''
);

-- ── Patrimonio ───────────────────────────────────────────

CREATE TABLE IF NOT EXISTS patrimonio (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre      TEXT    NOT NULL,
    tipo        TEXT    NOT NULL CHECK (tipo IN ('Activo', 'Pasivo')),
    subtipo     TEXT    NOT NULL DEFAULT '',
    institucion TEXT    NOT NULL DEFAULT '',
    saldo       REAL    NOT NULL DEFAULT 0,
    liquidez    TEXT    NOT NULL DEFAULT 'No aplica'
                CHECK (liquidez IN ('Alta', 'Media', 'Baja', 'No aplica')),
    tasa_anual  REAL    NOT NULL DEFAULT 0,
    fecha_corte TEXT,
    moneda      TEXT    NOT NULL DEFAULT 'MXN',
    notas       TEXT    NOT NULL DEFAULT ''
);

-- ── Suscripciones ────────────────────────────────────────

CREATE TABLE IF NOT EXISTS suscripciones (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    servicio              TEXT    NOT NULL,
    categoria_id          INTEGER          REFERENCES categorias(id),
    costo_por_cobro       REAL    NOT NULL CHECK (costo_por_cobro >= 0),
    frecuencia            TEXT    NOT NULL DEFAULT 'Mensual'
                          CHECK (frecuencia IN ('Mensual', 'Bimestral',
                                                'Trimestral', 'Semestral',
                                                'Anual')),
    proximo_cobro         TEXT,
    cuenta_id             INTEGER          REFERENCES cuentas(id),
    renovacion_automatica INTEGER NOT NULL DEFAULT 1
                          CHECK (renovacion_automatica IN (0, 1)),
    necesidad             TEXT    NOT NULL DEFAULT 'Deseo'
                          CHECK (necesidad IN ('Esencial', 'Deseo')),
    activa                INTEGER NOT NULL DEFAULT 1 CHECK (activa IN (0, 1)),
    notas                 TEXT    NOT NULL DEFAULT ''
);

-- ── Cierres mensuales ────────────────────────────────────

CREATE TABLE IF NOT EXISTS cierres_mensuales (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    periodo       TEXT    NOT NULL UNIQUE,          -- YYYY-MM
    efectivo      REAL    NOT NULL DEFAULT 0,
    ahorro        REAL    NOT NULL DEFAULT 0,
    inversiones   REAL    NOT NULL DEFAULT 0,
    otros_activos REAL    NOT NULL DEFAULT 0,
    deudas        REAL    NOT NULL DEFAULT 0,
    notas         TEXT    NOT NULL DEFAULT ''
);

-- ── Configuración (reglas editables desde la app) ────────

CREATE TABLE IF NOT EXISTS configuracion (
    clave       TEXT PRIMARY KEY,
    valor       TEXT NOT NULL,
    descripcion TEXT NOT NULL DEFAULT ''
);
"""

# ═══════════════════════════════════════════════════════════
# Vistas: las columnas calculadas del Excel, en un solo lugar
# ═══════════════════════════════════════════════════════════

VIEWS_SQL = """
DROP VIEW IF EXISTS v_movimientos;
CREATE VIEW v_movimientos AS
SELECT
    m.id,
    m.fecha,
    strftime('%Y-%m', m.fecha)          AS periodo,
    CAST(strftime('%Y', m.fecha) AS INTEGER) AS anio,
    CAST(strftime('%m', m.fecha) AS INTEGER) AS mes,
    CAST(strftime('%W', m.fecha) AS INTEGER) AS semana,
    CAST(strftime('%d', m.fecha) AS INTEGER) AS dia,
    m.tipo,
    m.monto,
    m.categoria_id,
    c.nombre                            AS categoria,
    m.subcategoria_id,
    COALESCE(s.nombre, '')              AS subcategoria,
    m.cuenta_id,
    cu.nombre                           AS cuenta,
    m.medio_pago_id,
    COALESCE(mp.nombre, '')             AS medio_pago,
    m.descripcion,
    m.necesidad,
    m.naturaleza,
    m.recurrente,
    m.planeado,
    m.proyecto,
    m.etiquetas,
    m.nota,
    m.estado,
    -- Impacto en caja: el ingreso suma, gasto/ahorro/inversión restan,
    -- la transferencia entre cuentas propias es neutra.
    CASE
        WHEN m.tipo = 'Ingreso'       THEN  m.monto
        WHEN m.tipo = 'Transferencia' THEN  0
        ELSE -m.monto
    END                                 AS impacto_caja,
    -- Gasto real: sólo gasto confirmado consume presupuesto.
    CASE
        WHEN m.tipo = 'Gasto' AND m.estado = 'Confirmado' THEN m.monto
        ELSE 0
    END                                 AS gasto_real,
    -- Patrimonio creado: ahorro e inversión confirmados.
    CASE
        WHEN m.tipo IN ('Ahorro', 'Inversión') AND m.estado = 'Confirmado'
        THEN m.monto ELSE 0
    END                                 AS patrimonio_creado,
    -- Ingreso reconocido: sólo el ingreso confirmado.
    CASE
        WHEN m.tipo = 'Ingreso' AND m.estado = 'Confirmado' THEN m.monto
        ELSE 0
    END                                 AS ingreso_real
FROM movimientos m
JOIN categorias    c  ON c.id  = m.categoria_id
JOIN cuentas       cu ON cu.id = m.cuenta_id
LEFT JOIN subcategorias s  ON s.id  = m.subcategoria_id
LEFT JOIN medios_pago   mp ON mp.id = m.medio_pago_id;

DROP VIEW IF EXISTS v_resumen_mensual;
CREATE VIEW v_resumen_mensual AS
SELECT
    periodo,
    SUM(ingreso_real)      AS ingresos,
    SUM(gasto_real)        AS gastos,
    SUM(patrimonio_creado) AS ahorro_inversion,
    SUM(ingreso_real) - SUM(gasto_real) - SUM(patrimonio_creado) AS disponible,
    COUNT(*)               AS movimientos
FROM v_movimientos
GROUP BY periodo;

DROP VIEW IF EXISTS v_patrimonio_neto;
CREATE VIEW v_patrimonio_neto AS
SELECT
    SUM(CASE WHEN tipo = 'Activo' THEN saldo ELSE 0 END) AS activos,
    SUM(CASE WHEN tipo = 'Pasivo' THEN saldo ELSE 0 END) AS pasivos,
    SUM(CASE WHEN tipo = 'Activo' THEN saldo ELSE -saldo END) AS patrimonio_neto
FROM patrimonio;
"""


# ═══════════════════════════════════════════════════════════
# Conexión
# ═══════════════════════════════════════════════════════════


def _adapt_bool(valor: bool) -> int:
    return int(valor)


sqlite3.register_adapter(bool, _adapt_bool)


@contextmanager
def connect(db_path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    """
    Abre una conexión a SQLite y la cierra al salir del bloque.

    Hace commit si el bloque termina bien y rollback si lanza. Se abre una
    conexión por operación a propósito: Streamlit ejecuta el script en varios
    hilos y SQLite es lo bastante rápido como para no necesitar un pool.

    Parameters
    ----------
    db_path : Path or str, optional
        Ruta al archivo SQLite. Por defecto, la de la configuración.

    Yields
    ------
    sqlite3.Connection
        Conexión con `row_factory` de tipo `sqlite3.Row`.
    """
    ruta = Path(db_path) if db_path is not None else settings.db_path
    ruta.parent.mkdir(parents=True, exist_ok=True)

    conexion = sqlite3.connect(ruta, detect_types=0)
    conexion.row_factory = sqlite3.Row
    conexion.execute("PRAGMA foreign_keys = ON")

    try:
        yield conexion
        conexion.commit()
    except Exception:
        conexion.rollback()
        raise
    finally:
        conexion.close()


def init_database(db_path: Path | str | None = None) -> None:
    """
    Crea tablas, índices y vistas si no existen.

    Es idempotente: puede llamarse en cada arranque de la aplicación.
    """
    with connect(db_path) as conexion:
        conexion.executescript(SCHEMA_SQL)
        conexion.executescript(VIEWS_SQL)

    logger.info("Esquema verificado en %s", db_path or settings.db_path)


def database_exists(db_path: Path | str | None = None) -> bool:
    """Indica si el archivo SQLite ya fue creado."""
    ruta = Path(db_path) if db_path is not None else settings.db_path
    return ruta.exists()


def tabla_vacia(tabla: str, db_path: Path | str | None = None) -> bool:
    """Indica si una tabla existe y no tiene registros."""
    with connect(db_path) as conexion:
        fila = conexion.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (tabla,),
        ).fetchone()
        if fila is None:
            return True
        total = conexion.execute(f"SELECT COUNT(*) AS n FROM {tabla}").fetchone()["n"]

    return total == 0
