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

-- El tipo de cuenta decide de qué lado del balance cae y qué significa
-- mover dinero hacia ella: Crédito y Préstamo son deuda, Ahorro e
-- Inversión son patrimonio, Efectivo y Débito son caja. Una base creada
-- antes de este catálogo puede traer «Banco», que la migración traduce.
CREATE TABLE IF NOT EXISTS cuentas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre      TEXT    NOT NULL UNIQUE,
    tipo        TEXT    NOT NULL DEFAULT 'Débito'
                CHECK (tipo IN ('Efectivo', 'Débito', 'Ahorro', 'Inversión',
                                'Vales', 'Crédito', 'Préstamo', 'Otro')),
    institucion TEXT    NOT NULL DEFAULT '',
    activa      INTEGER NOT NULL DEFAULT 1 CHECK (activa IN (0, 1))
);

-- ── Saldos verificados ───────────────────────────────────
--
-- Lo único del balance que se captura. Un saldo verificado dice «esta
-- cuenta cerró el día D con S», y de ahí el saldo a cualquier otra fecha
-- se deduce sumando los movimientos hacia adelante o restándolos hacia
-- atrás. Restar hacia atrás es lo que permite cargar estados de cuenta
-- viejos sin saber cuánto había al principio.
--
-- `saldo` va con el signo del libro: en una tarjeta, deber es negativo.

CREATE TABLE IF NOT EXISTS saldos_verificados (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    cuenta_id INTEGER NOT NULL REFERENCES cuentas(id) ON DELETE CASCADE,
    fecha     TEXT    NOT NULL,
    saldo     REAL    NOT NULL,
    origen    TEXT    NOT NULL DEFAULT 'Manual'
              CHECK (origen IN ('Manual', 'Estado de cuenta')),
    nota      TEXT    NOT NULL DEFAULT '',
    creado_en TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (cuenta_id, fecha)
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
    -- Cuenta a la que entra el dinero en una transferencia. `cuenta_id`
    -- es el origen y ésta el destino; sin ella, un traspaso entre
    -- cuentas propias no dice dónde acabó el dinero. Nula en todo lo
    -- que no es transferencia.
    cuenta_destino_id INTEGER        REFERENCES cuentas(id),
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
    -- Cuándo salió el dinero de una cuenta. Nulo significa devengado: el
    -- gasto ya ocurrió y consume presupuesto, pero todavía no salió de
    -- ninguna cuenta. Sólo un gasto pagado desde una cuenta que no es de
    -- crédito puede quedar nulo: con tarjeta lo paga la tarjeta en el
    -- acto, y un ingreso, un ahorro o un traspaso ocurren o no ocurren.
    -- `estado` responde otra pregunta —si el movimiento ocurrió de
    -- verdad o es una proyección— y las dos son independientes.
    fecha_pago      TEXT,
    -- Concepto tal como lo escribió el banco, cuando el movimiento vino
    -- de un estado de cuenta. Va aparte de `descripcion` porque sirve
    -- para otra cosa: aquélla es cómo lo llama uno —y se edita libremente
    -- para rastrear—, ésta es el texto original con el que se audita
    -- contra el documento y se reconocen comercios que se repiten.
    descripcion_banco TEXT  NOT NULL DEFAULT '',
    -- Folio del banco. Donde existe, dos movimientos con el mismo folio
    -- son el mismo movimiento, sin depender de fecha ni monto.
    referencia_externa TEXT NOT NULL DEFAULT '',
    -- Fecha tal como la reportó el banco. `fecha` es la que uno decide
    -- —la de compra, no la de aplicación— y se edita; ésta es la que el
    -- banco va a repetir en el siguiente estado de cuenta, y con la que
    -- se reconoce un movimiento ya importado.
    fecha_banco     TEXT,
    -- Quién cobró y dónde. `empresa` es el comercio o la marca (Walmart,
    -- DiDi, Oxxo); `lugar` es el sitio físico (Mitikah, Coyoacán). Los
    -- dos son texto libre porque ninguno es un catálogo: se ofrecen los
    -- ya usados para no teclear dos veces distinto. La hora es opcional
    -- porque ningún estado de cuenta la trae.
    empresa         TEXT    NOT NULL DEFAULT '',
    lugar           TEXT    NOT NULL DEFAULT '',
    hora            TEXT,
    creado_en       TEXT    NOT NULL DEFAULT (datetime('now')),
    actualizado_en  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS ix_movimientos_fecha ON movimientos(fecha);
CREATE INDEX IF NOT EXISTS ix_movimientos_categoria ON movimientos(categoria_id);
CREATE INDEX IF NOT EXISTS ix_movimientos_tipo_estado ON movimientos(tipo, estado);

-- ── Productos de un movimiento ───────────────────────────
--
-- El detalle de una compra de varias cosas. No son movimientos: el gasto
-- sigue siendo uno solo, con una categoría, y esto dice qué había dentro.
-- Por eso no llevan categoría propia; si algo necesita la suya, entonces
-- es un movimiento aparte y no un producto.
--
-- El desglose puede ser parcial a propósito: anotar tres artículos de un
-- ticket de veinte es útil, y exigir que cuadre convertiría una ayuda en
-- una tarea.

CREATE TABLE IF NOT EXISTS movimiento_productos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    movimiento_id   INTEGER NOT NULL
                    REFERENCES movimientos(id) ON DELETE CASCADE,
    producto        TEXT    NOT NULL,
    cantidad        REAL    NOT NULL DEFAULT 1 CHECK (cantidad > 0),
    precio_unitario REAL    NOT NULL DEFAULT 0 CHECK (precio_unitario >= 0),
    orden           INTEGER NOT NULL DEFAULT 0,
    nota            TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_productos_movimiento
    ON movimiento_productos(movimiento_id);

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
    notas       TEXT    NOT NULL DEFAULT '',
    -- Heredado: antes el saldo de una cuenta vivía aquí. Ahora se deduce
    -- de los movimientos y de `saldos_verificados`, y la migración
    -- convierte las posiciones ligadas en saldos verificados. Sólo lo
    -- que no es cuenta (la casa, el auto) sigue siendo una posición.
    cuenta_id   INTEGER REFERENCES cuentas(id) ON DELETE SET NULL
);

-- ── Suscripciones ────────────────────────────────────────

CREATE TABLE IF NOT EXISTS suscripciones (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    servicio              TEXT    NOT NULL,
    categoria_id          INTEGER          REFERENCES categorias(id),
    subcategoria_id       INTEGER          REFERENCES subcategorias(id)
                          ON DELETE SET NULL,
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

-- ── Proyectos ────────────────────────────────────────────
--
-- Agrupan movimientos de cualquier categoría bajo un esfuerzo común: un
-- viaje, una obra, una mudanza. El movimiento guarda el nombre y no un
-- id porque la columna ya existía como texto libre; el nombre es único y
-- renombrar actualiza en cascada, que para un catálogo de este tamaño
-- sale más barato que migrar a clave foránea.

CREATE TABLE IF NOT EXISTS proyectos (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre       TEXT    NOT NULL UNIQUE,
    descripcion  TEXT    NOT NULL DEFAULT '',
    presupuesto  REAL    NOT NULL DEFAULT 0 CHECK (presupuesto >= 0),
    fecha_inicio TEXT,
    fecha_fin    TEXT,
    activo       INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
    notas        TEXT    NOT NULL DEFAULT ''
);

-- ── Lista de deseos ──────────────────────────────────────
--
-- Lo que se quiere comprar y todavía no. Sirve para decidir con números
-- en vez de con ganas: cada deseo se compara contra el saldo disponible
-- y se ve cuánto falta.

CREATE TABLE IF NOT EXISTS deseos (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre       TEXT    NOT NULL,
    costo        REAL    NOT NULL CHECK (costo >= 0),
    categoria_id INTEGER          REFERENCES categorias(id) ON DELETE SET NULL,
    prioridad    TEXT    NOT NULL DEFAULT 'Media'
                 CHECK (prioridad IN ('Alta', 'Media', 'Baja')),
    enlace       TEXT    NOT NULL DEFAULT '',
    notas        TEXT    NOT NULL DEFAULT '',
    -- Cuándo se compró. Nulo significa que sigue en la lista.
    comprado_en  TEXT,
    creado_en    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS ix_deseos_pendientes
    ON deseos(comprado_en) WHERE comprado_en IS NULL;

-- ── Importación en curso ─────────────────────────────────
--
-- El avance del asistente de importación, para que recargar el navegador
-- no cueste el trabajo hecho. `session_state` de Streamlit sobrevive los
-- reruns pero no una recarga, así que lo que no puede perderse vive aquí.
-- Es una sola fila: se importa un documento a la vez.

CREATE TABLE IF NOT EXISTS importacion_en_curso (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    banco          TEXT    NOT NULL,
    estado         TEXT    NOT NULL,
    indice         INTEGER NOT NULL DEFAULT 0,
    paso           TEXT    NOT NULL DEFAULT 'revisar',
    actualizado_en TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Configuración (reglas editables desde la app) ────────

CREATE TABLE IF NOT EXISTS configuracion (
    clave       TEXT PRIMARY KEY,
    valor       TEXT NOT NULL,
    descripcion TEXT NOT NULL DEFAULT ''
);
"""

# ═══════════════════════════════════════════════════════════
# Índices sobre columnas añadidas por migración
#
# Van aparte porque en una base creada con el esquema anterior
# la columna aún no existe cuando corre `SCHEMA_SQL`.
# ═══════════════════════════════════════════════════════════

INDICES_MIGRADOS_SQL = """
-- Una cuenta se refleja en una sola línea del balance.
CREATE UNIQUE INDEX IF NOT EXISTS ux_patrimonio_cuenta
    ON patrimonio(cuenta_id) WHERE cuenta_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_suscripciones_subcategoria
    ON suscripciones(subcategoria_id);

-- Los pendientes de pago se consultan como grupo en cada cierre.
CREATE INDEX IF NOT EXISTS ix_movimientos_por_pagar
    ON movimientos(fecha_pago) WHERE fecha_pago IS NULL;

CREATE INDEX IF NOT EXISTS ix_saldos_verificados_cuenta
    ON saldos_verificados(cuenta_id, fecha);

CREATE INDEX IF NOT EXISTS ix_movimientos_cuenta_destino
    ON movimientos(cuenta_destino_id) WHERE cuenta_destino_id IS NOT NULL;
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
    cu.tipo                             AS cuenta_tipo,
    m.cuenta_destino_id,
    COALESCE(cd.nombre, '')             AS cuenta_destino,
    cd.tipo                             AS cuenta_destino_tipo,
    m.medio_pago_id,
    COALESCE(mp.nombre, '')             AS medio_pago,
    m.descripcion,
    m.empresa,
    m.lugar,
    m.hora,
    m.descripcion_banco,
    m.referencia_externa,
    m.fecha_banco,
    m.necesidad,
    m.naturaleza,
    m.recurrente,
    m.planeado,
    m.proyecto,
    m.etiquetas,
    m.nota,
    m.estado,
    m.fecha_pago,
    CASE WHEN m.fecha_pago IS NULL THEN 0 ELSE 1 END AS pagado,
    -- Periodo en que el dinero se movió, que no tiene por qué ser aquel
    -- en que se incurrió el gasto: una compra de agosto pagada en
    -- septiembre pesa en el presupuesto de agosto y en la caja de
    -- septiembre.
    strftime('%Y-%m', m.fecha_pago)     AS periodo_pago,
    -- Impacto en caja: el ingreso suma, gasto/ahorro/inversión restan,
    -- la transferencia entre cuentas propias es neutra. Lo devengado
    -- —sin fecha de pago— no mueve caja todavía.
    CASE
        WHEN m.fecha_pago IS NULL     THEN  0
        WHEN m.tipo = 'Ingreso'       THEN  m.monto
        WHEN m.tipo = 'Transferencia' THEN  0
        ELSE -m.monto
    END                                 AS impacto_caja,
    -- Lo que se debe: gasto ya incurrido que aún no se ha pagado desde
    -- ninguna cuenta. Sólo un gasto puede quedar así.
    CASE
        WHEN m.tipo = 'Gasto'
         AND m.estado = 'Confirmado'
         AND m.fecha_pago IS NULL
        THEN m.monto ELSE 0
    END                                 AS por_pagar,
    -- Gasto real: sólo gasto confirmado consume presupuesto.
    CASE
        WHEN m.tipo = 'Gasto' AND m.estado = 'Confirmado' THEN m.monto
        ELSE 0
    END                                 AS gasto_real,
    -- Patrimonio creado: dinero que entró a una cuenta de ahorro o
    -- inversión desde una que no lo es. Lo dicen las cuentas, no el
    -- tipo: un traspaso al apartado también es ahorro, y así aportación
    -- y retiro se miden con la misma vara.
    CASE
        WHEN m.estado = 'Confirmado'
         AND m.fecha_pago IS NOT NULL
         AND m.tipo IN ('Ahorro', 'Inversión', 'Transferencia')
         AND cd.tipo IN ('Ahorro', 'Inversión')
         AND cu.tipo NOT IN ('Ahorro', 'Inversión')
        THEN m.monto ELSE 0
    END                                 AS patrimonio_creado,
    -- Ahorro retirado: dinero que salió de una cuenta de ahorro o
    -- inversión hacia una que no lo es, o que se gastó desde ahí.
    CASE
        WHEN m.estado = 'Confirmado'
         AND m.fecha_pago IS NOT NULL
         AND m.tipo IN ('Gasto', 'Transferencia')
         AND cu.tipo IN ('Ahorro', 'Inversión')
         AND (cd.tipo IS NULL OR cd.tipo NOT IN ('Ahorro', 'Inversión'))
        THEN m.monto ELSE 0
    END                                 AS ahorro_retirado,
    -- Ingreso reconocido: sólo el ingreso confirmado.
    CASE
        WHEN m.tipo = 'Ingreso' AND m.estado = 'Confirmado' THEN m.monto
        ELSE 0
    END                                 AS ingreso_real
FROM movimientos m
JOIN categorias    c  ON c.id  = m.categoria_id
JOIN cuentas       cu ON cu.id = m.cuenta_id
LEFT JOIN cuentas       cd ON cd.id = m.cuenta_destino_id
LEFT JOIN subcategorias s  ON s.id  = m.subcategoria_id
LEFT JOIN medios_pago   mp ON mp.id = m.medio_pago_id;

-- El ahorro del mes es neto: lo que entró al ahorro menos lo que salió.
-- Meter 7,000 y sacar 3,000 el mismo mes es haber ahorrado 4,000.
DROP VIEW IF EXISTS v_resumen_mensual;
CREATE VIEW v_resumen_mensual AS
SELECT
    periodo,
    SUM(ingreso_real)      AS ingresos,
    SUM(gasto_real)        AS gastos,
    SUM(patrimonio_creado) AS aportaciones,
    SUM(ahorro_retirado)   AS retiros,
    SUM(patrimonio_creado) - SUM(ahorro_retirado) AS ahorro_inversion,
    SUM(ingreso_real) - SUM(gasto_real)
        - (SUM(patrimonio_creado) - SUM(ahorro_retirado)) AS disponible,
    COUNT(*)               AS movimientos
FROM v_movimientos
GROUP BY periodo;

-- Posiciones que no son cuenta: la casa, el auto, un préstamo entre
-- personas. Las cuentas del catálogo no están aquí; su saldo se deduce.
DROP VIEW IF EXISTS v_patrimonio;
CREATE VIEW v_patrimonio AS
SELECT
    p.*,
    COALESCE(cu.nombre, '')      AS cuenta,
    COALESCE(cu.tipo, '')        AS cuenta_tipo,
    -- Contribución con signo al patrimonio neto: el pasivo se captura
    -- en positivo y aquí se resta.
    CASE WHEN p.tipo = 'Activo' THEN p.saldo ELSE -p.saldo END
                                 AS aporte_a_patrimonio
FROM patrimonio p
LEFT JOIN cuentas cu ON cu.id = p.cuenta_id;

-- Saldos verificados con el nombre y el tipo de su cuenta.
DROP VIEW IF EXISTS v_saldos_verificados;
CREATE VIEW v_saldos_verificados AS
SELECT
    sv.id,
    sv.cuenta_id,
    cu.nombre   AS cuenta,
    cu.tipo     AS cuenta_tipo,
    sv.fecha,
    sv.saldo,
    -- Como lo enseña el banco: la deuda de una tarjeta, en positivo.
    CASE WHEN cu.tipo IN ('Crédito', 'Préstamo') THEN -sv.saldo ELSE sv.saldo END
                AS saldo_visto,
    sv.origen,
    sv.nota,
    sv.creado_en
FROM saldos_verificados sv
JOIN cuentas cu ON cu.id = sv.cuenta_id;

-- Proyectos: agrupan movimientos de cualquier categoría bajo un
-- esfuerzo común —una mudanza, un viaje, una obra— para poder preguntar
-- «¿cuánto llevo gastado en esto?» sin que la categoría deje de decir
-- qué clase de gasto fue.
DROP VIEW IF EXISTS v_proyectos;
CREATE VIEW v_proyectos AS
SELECT
    nombres.proyecto,
    COALESCE(p.id, 0)                AS proyecto_id,
    COALESCE(p.descripcion, '')      AS descripcion,
    COALESCE(p.presupuesto, 0)       AS presupuesto,
    p.fecha_inicio,
    p.fecha_fin,
    COALESCE(p.activo, 1)            AS activo,
    COALESCE(p.notas, '')            AS notas,
    -- Un proyecto declarado que aún no tiene movimientos también existe.
    CASE WHEN p.id IS NULL THEN 0 ELSE 1 END AS declarado,
    COALESCE(totales.movimientos, 0) AS movimientos,
    totales.desde,
    totales.hasta,
    COALESCE(totales.gasto, 0)             AS gasto,
    COALESCE(totales.ingreso, 0)           AS ingreso,
    COALESCE(totales.ahorro_inversion, 0)  AS ahorro_inversion,
    COALESCE(totales.por_pagar, 0)         AS por_pagar,
    COALESCE(totales.ingreso, 0) - COALESCE(totales.gasto, 0) AS neto,
    -- Lo que queda del presupuesto del proyecto, si se le puso uno.
    CASE
        WHEN COALESCE(p.presupuesto, 0) > 0
        THEN p.presupuesto - COALESCE(totales.gasto, 0)
    END                              AS disponible
FROM (
    SELECT nombre AS proyecto FROM proyectos
    UNION
    SELECT DISTINCT TRIM(proyecto) FROM movimientos WHERE TRIM(proyecto) <> ''
) AS nombres
LEFT JOIN proyectos p ON p.nombre = nombres.proyecto
LEFT JOIN (
    SELECT
        TRIM(m.proyecto)                      AS proyecto,
        COUNT(*)                              AS movimientos,
        MIN(m.fecha)                          AS desde,
        MAX(m.fecha)                          AS hasta,
        COALESCE(SUM(v.gasto_real), 0)        AS gasto,
        COALESCE(SUM(v.ingreso_real), 0)      AS ingreso,
        COALESCE(SUM(v.patrimonio_creado), 0) AS ahorro_inversion,
        COALESCE(SUM(v.por_pagar), 0)         AS por_pagar
    FROM movimientos m
    JOIN v_movimientos v ON v.id = m.id
    WHERE TRIM(m.proyecto) <> ''
    GROUP BY TRIM(m.proyecto)
) AS totales ON totales.proyecto = nombres.proyecto;

-- Flujo por cuenta, con una fila por pata del movimiento: el libro.
--
-- `impacto_caja` mira la caja como un todo, así que una transferencia
-- entre cuentas propias vale cero. Por cuenta no es neutra: sale de una
-- y entra en otra, y esa es justo la pregunta de «¿de dónde salió el
-- dinero con que pagué la tarjeta?». Un ahorro o una inversión también
-- tienen dos patas: el dinero no se fue, cambió de cuenta. Sólo entra lo
-- confirmado y ya pagado, que es lo único que movió dinero de verdad.
DROP VIEW IF EXISTS v_flujo_cuentas;
CREATE VIEW v_flujo_cuentas AS
SELECT
    m.id                            AS movimiento_id,
    m.fecha_pago                    AS fecha,
    strftime('%Y-%m', m.fecha_pago) AS periodo,
    m.tipo,
    m.cuenta_id,
    cu.nombre                       AS cuenta,
    'Origen'                        AS pata,
    CASE WHEN m.tipo = 'Ingreso' THEN m.monto ELSE -m.monto END AS movimiento
FROM movimientos m
JOIN cuentas cu ON cu.id = m.cuenta_id
WHERE m.fecha_pago IS NOT NULL
  AND m.estado = 'Confirmado'

UNION ALL

SELECT
    m.id,
    m.fecha_pago,
    strftime('%Y-%m', m.fecha_pago),
    m.tipo,
    m.cuenta_destino_id,
    cd.nombre,
    'Destino',
    m.monto
FROM movimientos m
JOIN cuentas cd ON cd.id = m.cuenta_destino_id
WHERE m.fecha_pago IS NOT NULL
  AND m.estado = 'Confirmado'
  AND m.tipo IN ('Transferencia', 'Ahorro', 'Inversión')
  AND m.cuenta_destino_id IS NOT NULL;

-- Adeudos generados: lo que ya se gastó y todavía no se paga. Es el
-- saldo de cuentas por pagar, y entra al balance como pasivo.
DROP VIEW IF EXISTS v_por_pagar;
CREATE VIEW v_por_pagar AS
SELECT
    m.id,
    m.fecha,
    strftime('%Y-%m', m.fecha)  AS periodo,
    m.monto,
    m.descripcion,
    c.nombre                    AS categoria,
    cu.id                       AS cuenta_id,
    cu.nombre                   AS cuenta,
    CAST(julianday('now') - julianday(m.fecha) AS INTEGER) AS dias_pendiente
FROM movimientos m
JOIN categorias c  ON c.id  = m.categoria_id
JOIN cuentas    cu ON cu.id = m.cuenta_id
WHERE m.estado = 'Confirmado'
  AND m.fecha_pago IS NULL
  AND m.tipo = 'Gasto';

-- Productos con su importe ya calculado y el movimiento al que cuelgan.
DROP VIEW IF EXISTS v_movimiento_productos;
CREATE VIEW v_movimiento_productos AS
SELECT
    pr.id,
    pr.movimiento_id,
    pr.producto,
    pr.cantidad,
    pr.precio_unitario,
    ROUND(pr.cantidad * pr.precio_unitario, 2) AS importe,
    pr.orden,
    pr.nota,
    m.fecha,
    m.monto                     AS monto_movimiento,
    c.nombre                    AS categoria
FROM movimiento_productos pr
JOIN movimientos m ON m.id = pr.movimiento_id
JOIN categorias   c ON c.id = m.categoria_id;

-- Cuánto del movimiento está desglosado en productos, para poder avisar
-- cuando el detalle no llega a cubrirlo.
DROP VIEW IF EXISTS v_movimientos_desglose;
CREATE VIEW v_movimientos_desglose AS
SELECT
    m.id                                   AS movimiento_id,
    m.monto,
    COUNT(pr.id)                           AS productos,
    COALESCE(SUM(ROUND(pr.cantidad * pr.precio_unitario, 2)), 0) AS desglosado,
    m.monto - COALESCE(SUM(ROUND(pr.cantidad * pr.precio_unitario, 2)), 0)
                                           AS sin_desglosar
FROM movimientos m
LEFT JOIN movimiento_productos pr ON pr.movimiento_id = m.id
GROUP BY m.id, m.monto;

-- Los deseos pendientes, con su costo y su antigüedad.
DROP VIEW IF EXISTS v_deseos;
CREATE VIEW v_deseos AS
SELECT
    d.id,
    d.nombre,
    d.costo,
    d.categoria_id,
    COALESCE(c.nombre, '')   AS categoria,
    d.prioridad,
    d.enlace,
    d.notas,
    d.comprado_en,
    CASE WHEN d.comprado_en IS NULL THEN 0 ELSE 1 END AS comprado,
    d.creado_en,
    CAST(julianday('now') - julianday(d.creado_en) AS INTEGER) AS dias_en_lista
FROM deseos d
LEFT JOIN categorias c ON c.id = d.categoria_id;

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


# ═══════════════════════════════════════════════════════════
# Migraciones
#
# `CREATE TABLE IF NOT EXISTS` no toca una tabla que ya existe,
# así que las columnas nuevas hay que añadirlas aparte. Cada
# entrada es (tabla, columna, definición) y se aplica sólo si
# la columna falta, de modo que el arranque sea idempotente.
# ═══════════════════════════════════════════════════════════

_COLUMNAS_NUEVAS: tuple[tuple[str, str, str], ...] = (
    (
        "patrimonio",
        "cuenta_id",
        "INTEGER REFERENCES cuentas(id) ON DELETE SET NULL",
    ),
    (
        "suscripciones",
        "subcategoria_id",
        "INTEGER REFERENCES subcategorias(id) ON DELETE SET NULL",
    ),
    ("movimientos", "fecha_pago", "TEXT"),
    ("movimientos", "cuenta_destino_id", "INTEGER REFERENCES cuentas(id)"),
    ("movimientos", "descripcion_banco", "TEXT NOT NULL DEFAULT ''"),
    ("movimientos", "referencia_externa", "TEXT NOT NULL DEFAULT ''"),
    ("movimientos", "empresa", "TEXT NOT NULL DEFAULT ''"),
    ("movimientos", "lugar", "TEXT NOT NULL DEFAULT ''"),
    ("movimientos", "hora", "TEXT"),
    ("movimientos", "fecha_banco", "TEXT"),
)

#: Columnas que cambiaron de nombre porque cambiaron de significado.
#:
#: `lugar` nació para el comercio —«Walmart Universidad»— y eso es la
#: empresa; el lugar es dónde está. Renombrar conserva lo capturado bajo
#: el nombre que le corresponde, y `_COLUMNAS_NUEVAS` añade después el
#: `lugar` nuevo, vacío. Cada entrada es (tabla, nombre viejo, nombre
#: nuevo) y sólo aplica si el viejo existe y el nuevo no.
_RENOMBRES: tuple[tuple[str, str, str], ...] = (("movimientos", "lugar", "empresa"),)

#: Relleno que corre una sola vez, justo tras añadir una columna.
#:
#: Una columna nueva nace nula para todas las filas, y a veces el nulo
#: significa algo distinto de lo que esas filas significaban antes. Es el
#: caso de `fecha_pago`: hasta ahora un movimiento confirmado era, por
#: definición, un movimiento pagado, así que dejarlo nulo convertiría todo
#: el histórico en adeudos que nadie contrajo.
#: Cada relleno declara de qué columnas depende además de la recién
#: añadida. Una base vieja puede no tenerlas, y entonces el relleno se
#: omite en vez de tumbar el arranque: perder el relleno es un dato menos,
#: pero fallar aquí deja la aplicación sin abrir.
_RELLENOS: dict[str, tuple[str, tuple[str, ...]]] = {
    "movimientos.fecha_pago": (
        """
        UPDATE movimientos
           SET fecha_pago = fecha
         WHERE fecha_pago IS NULL AND estado = 'Confirmado'
        """,
        ("fecha", "estado"),
    ),
    # Una primera versión del importador metía el concepto del banco
    # dentro de la nota, entre corchetes. Rescatarlo evita perderlo en lo
    # ya importado, y deja la nota con lo que de verdad escribió el
    # usuario.
    "movimientos.descripcion_banco": (
        """
        UPDATE movimientos
           SET descripcion_banco = TRIM(
                   SUBSTR(nota,
                          INSTR(nota, '[') + 1,
                          INSTR(nota, ']') - INSTR(nota, '[') - 1)
               ),
               nota = TRIM(SUBSTR(nota, 1, INSTR(nota, '[') - 1))
         WHERE descripcion_banco = ''
           AND INSTR(nota, '[') > 0
           AND INSTR(nota, ']') > INSTR(nota, '[')
        """,
        ("nota",),
    ),
}


#: Correcciones de datos que corren en cada arranque.
#:
#: Son idempotentes a propósito: cada una deja la base como si el modelo
#: actual hubiera existido siempre, y no hace nada si ya está así. Van en
#: orden, porque las de movimientos dependen de que el tipo de cuenta ya
#: esté traducido.
#: Cada entrada es (nombre, sentencia, columnas que necesita) con las
#: columnas como `tabla.columna`; si una base vieja no las tiene, la
#: corrección se omite en vez de tumbar el arranque.
_MIGRACIONES_DATOS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    # «Banco» era el tipo genérico antes de que existiera el catálogo de
    # tipos de cuenta; en él, una cuenta de banco es de débito.
    (
        "cuentas.tipo: Banco → Débito",
        "UPDATE cuentas SET tipo = 'Débito' WHERE tipo = 'Banco'",
        ("cuentas.tipo",),
    ),
    (
        "cuentas.tipo: desconocido → Otro",
        """
        UPDATE cuentas SET tipo = 'Otro'
         WHERE tipo NOT IN ('Efectivo', 'Débito', 'Ahorro', 'Inversión',
                            'Vales', 'Crédito', 'Préstamo', 'Otro')
        """,
        ("cuentas.tipo",),
    ),
    # El saldo de una cuenta ya no se captura como posición: se deduce. Lo
    # que se había capturado se conserva como saldo verificado a su fecha
    # de corte, que es exactamente lo que era.
    (
        "patrimonio ligado → saldos_verificados",
        """
        INSERT OR IGNORE INTO saldos_verificados (cuenta_id, fecha, saldo, origen, nota)
        SELECT
            p.cuenta_id,
            COALESCE(p.fecha_corte, date('now')),
            CASE WHEN p.tipo = 'Pasivo' THEN -p.saldo ELSE p.saldo END,
            'Manual',
            'Migrado del balance capturado'
        FROM patrimonio p
        WHERE p.cuenta_id IS NOT NULL
        """,
        ("patrimonio.cuenta_id", "patrimonio.fecha_corte", "patrimonio.saldo"),
    ),
    (
        "patrimonio ligado: eliminar",
        "DELETE FROM patrimonio WHERE cuenta_id IS NOT NULL",
        ("patrimonio.cuenta_id",),
    ),
    # Un ingreso, un ahorro o un traspaso ocurren o no ocurren: no quedan
    # «por pagar». Lo que estaba sin fecha de pago se paga en su fecha.
    (
        "movimientos.fecha_pago: no-gasto",
        """
        UPDATE movimientos SET fecha_pago = fecha
         WHERE fecha_pago IS NULL AND tipo <> 'Gasto'
        """,
        ("movimientos.fecha_pago", "movimientos.tipo"),
    ),
    # Con tarjeta de crédito el gasto lo paga la tarjeta en el acto; lo
    # que se debe es la tarjeta, y eso lo dice su saldo.
    (
        "movimientos.fecha_pago: crédito",
        """
        UPDATE movimientos SET fecha_pago = fecha
         WHERE fecha_pago IS NULL
           AND tipo = 'Gasto'
           AND cuenta_id IN (
               SELECT id FROM cuentas WHERE tipo IN ('Crédito', 'Préstamo')
           )
        """,
        ("movimientos.fecha_pago", "movimientos.cuenta_id", "cuentas.tipo"),
    ),
)


def _columnas_de(conexion: sqlite3.Connection, tabla: str) -> set[str]:
    """Devuelve los nombres de columna de una tabla existente."""
    filas = conexion.execute(f"PRAGMA table_info({tabla})").fetchall()
    return {fila["name"] for fila in filas}


def aplicar_migraciones(db_path: Path | str | None = None) -> list[str]:
    """
    Añade a una base existente las columnas que el esquema ya declara.

    Returns
    -------
    list of str
        Las columnas efectivamente añadidas, como `tabla.columna`.
    """
    aplicadas: list[str] = []

    with connect(db_path) as conexion:
        for tabla, vieja, nueva in _RENOMBRES:
            presentes = _columnas_de(conexion, tabla)
            if vieja in presentes and nueva not in presentes:
                conexion.execute(
                    f"ALTER TABLE {tabla} RENAME COLUMN {vieja} TO {nueva}"
                )
                aplicadas.append(f"{tabla}.{vieja} → {nueva}")

        for tabla, columna, definicion in _COLUMNAS_NUEVAS:
            if columna in _columnas_de(conexion, tabla):
                continue

            conexion.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {definicion}")

            relleno = _RELLENOS.get(f"{tabla}.{columna}")
            if relleno is not None:
                sentencia, requiere = relleno
                presentes = _columnas_de(conexion, tabla)
                if set(requiere) <= presentes:
                    conexion.execute(sentencia)
                else:
                    faltan = ", ".join(sorted(set(requiere) - presentes))
                    logger.warning(
                        "Relleno de %s.%s omitido: falta %s", tabla, columna, faltan
                    )

            aplicadas.append(f"{tabla}.{columna}")

        for nombre, sentencia, requiere in _MIGRACIONES_DATOS:
            faltan = [
                columna
                for columna in requiere
                if columna.split(".")[1]
                not in _columnas_de(conexion, columna.split(".")[0])
            ]
            if faltan:
                logger.warning("Corrección «%s» omitida: falta %s", nombre, faltan)
                continue

            cursor = conexion.execute(sentencia)
            if cursor.rowcount > 0:
                aplicadas.append(f"{nombre} ({cursor.rowcount})")

    if aplicadas:
        logger.info("Migraciones aplicadas: %s", ", ".join(aplicadas))

    return aplicadas


def _retirar_vistas(db_path: Path | str | None) -> None:
    """
    Borra todas las vistas antes de migrar.

    Se recrean completas justo después, así que no se pierde nada; y
    quitarlas antes evita dos problemas: que una vista retirada siga viva
    apuntando a columnas que ya no significan lo mismo, y que un `RENAME
    COLUMN` —que reescribe las vistas que la nombran— tropiece con una
    vista vieja que ya no compila.
    """
    with connect(db_path) as conexion:
        vistas = conexion.execute(
            "SELECT name FROM sqlite_master WHERE type = 'view'"
        ).fetchall()
        for fila in vistas:
            conexion.execute(f"DROP VIEW IF EXISTS {fila['name']}")


def init_database(db_path: Path | str | None = None) -> None:
    """
    Crea tablas, índices y vistas si no existen, y migra las que ya están.

    Es idempotente: puede llamarse en cada arranque de la aplicación.
    """
    with connect(db_path) as conexion:
        conexion.executescript(SCHEMA_SQL)

    # Antes de las vistas: `v_patrimonio` lee `cuenta_id`, que en una base
    # creada con el esquema anterior sólo existe después de migrar.
    _retirar_vistas(db_path)
    aplicar_migraciones(db_path)

    with connect(db_path) as conexion:
        conexion.executescript(INDICES_MIGRADOS_SQL)
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
