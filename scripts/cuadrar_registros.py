"""
Deja los registros capturados antes del modelo de cuentas como libros
en el estado que ese modelo espera.

Antes, el saldo de una cuenta se capturaba a mano y la tarjeta de crédito
era un gasto «por pagar». Ahora cada cuenta es un libro, la tarjeta es una
cuenta de deuda y pagarla es un traspaso. Lo capturado con el modelo
anterior tiene tres clases de problema, y este script corrige las tres:

1. Tipos de cuenta mal puestos: las tarjetas de Mercado Pago y Nu como
   «Banco», los apartados como «Banco» u «Otro».
2. Cuentas que faltan: el apartado de cada banco, que se venía registrando
   contra «Otra».
3. Movimientos concretos que el modelo viejo dejaba pasar: un pago de
   tarjeta capturado como gasto, un ahorro sin cuenta de destino, un
   retiro del apartado desde «Otra», y un par de rendimientos de Mercado
   Pago asignados a BBVA.
4. Las ganancias de centavos de Mercado Pago, que se registraban una por
   una: se juntan en un ingreso por mes con la suma y todos los folios,
   igual que hace ahora el importador.
5. Fechas que se alejaron de la del banco por más de un día. Un defecto
   del asistente hacía que la fecha corregida en un documento reapareciera
   en la misma posición del siguiente; donde la fecha propia y la del
   banco difieren más de un día, manda la del banco.

Cada corrección se identifica por folio del banco y por el estado actual
de la fila, así que correr el script dos veces no hace nada la segunda.
Las migraciones genéricas (Banco → Débito, fecha de pago en traspasos)
las hace `init_database` al arrancar y aquí sólo se invocan.

Uso
---
    poetry run python scripts/cuadrar_registros.py --dry-run
    poetry run python scripts/cuadrar_registros.py
    poetry run python scripts/cuadrar_registros.py --db ruta/alterna.db
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from finanzas.config.logging import setup_logging
from finanzas.config.settings import settings
from finanzas.data.database import init_database

# ═══════════════════════════════════════════════════════════
# Qué corregir
# ═══════════════════════════════════════════════════════════

#: (nombre de cuenta, tipo correcto, institución normalizada)
CUENTAS: tuple[tuple[str, str, str], ...] = (
    ("BBVA TDD", "Débito", "BBVA"),
    ("BBVA TDC", "Crédito", "BBVA"),
    ("MP TDD", "Débito", "Mercado Pago"),
    ("MP TDC", "Crédito", "Mercado Pago"),
    ("MP Apartado", "Ahorro", "Mercado Pago"),
    ("NU TDD", "Débito", "Nu"),
    ("NU TDC", "Crédito", "Nu"),
    ("Fondo Ahorro", "Ahorro", "ITAM"),
    ("Edenred", "Vales", "Edenred"),
    ("Inversiones", "Inversión", ""),
    ("Efectivo", "Efectivo", ""),
    ("Otra", "Otro", ""),
)

#: (nombre, tipo, institución, activa) — el apartado de cada banco.
CUENTAS_NUEVAS: tuple[tuple[str, str, str, int], ...] = (
    ("BBVA Apartado", "Ahorro", "BBVA", 1),
    ("NU Apartado", "Ahorro", "Nu", 0),
)

#: Correcciones a movimientos concretos, identificados por folio.
#:
#: Cada entrada: (folio, condición extra en SQL, descripción, cambios),
#: donde los cambios son {columna: valor} y los valores que empiezan con
#: «cuenta:» se resuelven al id de esa cuenta.
MOVIMIENTOS: tuple[tuple[str, str, str, dict[str, object]], ...] = (
    (
        "175326834603",
        "tipo = 'Gasto'",
        "«Pago Tarjeta de crédito» 6,784.48 capturado como gasto desde BBVA "
        "TDD. El folio es de Mercado Pago: es el pago de MP TDC desde MP TDD, "
        "un traspaso.",
        {
            "tipo": "Transferencia",
            "cuenta_id": "cuenta:MP TDD",
            "cuenta_destino_id": "cuenta:MP TDC",
            "categoria_id": "categoria:Traspaso entre cuentas",
            "subcategoria_id": None,
            "necesidad": "Esencial",
            "naturaleza": "Variable",
            "medio_pago_id": None,
        },
    ),
    (
        "176276256156",
        "cuenta_destino_id IS NULL",
        "«Monto apartado Ahorro» 7,000 registrado como Ahorro sin cuenta de "
        "destino: entra a MP Apartado.",
        {"cuenta_destino_id": "cuenta:MP Apartado"},
    ),
    (
        "176278878094",
        "cuenta_id = (SELECT id FROM cuentas WHERE nombre = 'Otra')",
        "«Monto retirado Ahorro» 629 registrado desde «Otra»: sale de MP Apartado.",
        {"cuenta_id": "cuenta:MP Apartado"},
    ),
    (
        "175006820044",
        "tipo = 'Gasto' AND descripcion_banco LIKE 'Transferencia enviada%' "
        "AND cuenta_id = (SELECT id FROM cuentas WHERE nombre = 'MP TDC')",
        "«Pizzas Aline» 100: el pago a Aline salió de la cuenta MP TDD, no de "
        "la tarjeta. La tarjeta ya movió sus 100 en el «Ingreso de dinero» del "
        "mismo folio; dejarlo en MP TDC contaba 200 de deuda por 100.",
        {"cuenta_id": "cuenta:MP TDD"},
    ),
    (
        "1748172013786",
        "cuenta_id = (SELECT id FROM cuentas WHERE nombre = 'BBVA TDD')",
        "«Ganancia» 0.01 con folio de Mercado Pago asignada a BBVA TDD.",
        {"cuenta_id": "cuenta:MP TDD"},
    ),
    (
        "173182037226",
        "cuenta_id = (SELECT id FROM cuentas WHERE nombre = 'BBVA TDD')",
        "«Ganancia de Apartados» 0.02 con folio de Mercado Pago asignada a BBVA TDD.",
        {"cuenta_id": "cuenta:MP TDD"},
    ),
)


# ═══════════════════════════════════════════════════════════
# Ejecución
# ═══════════════════════════════════════════════════════════


def _respaldar(ruta: Path) -> Path:
    """Copia la base junto a la original, con la hora en el nombre."""
    marca = datetime.now().strftime("%Y%m%d-%H%M%S")
    destino = ruta.with_name(f"{ruta.stem}.respaldo-{marca}{ruta.suffix}")
    shutil.copy2(ruta, destino)
    return destino


def _id_de(conexion: sqlite3.Connection, tabla: str, nombre: str) -> int:
    fila = conexion.execute(
        f"SELECT id FROM {tabla} WHERE nombre = ?", (nombre,)
    ).fetchone()
    if fila is None:
        raise SystemExit(f"No existe «{nombre}» en {tabla}.")
    return int(fila["id"])


def _resolver(conexion: sqlite3.Connection, valor: object) -> object:
    """Traduce «cuenta:Nombre» y «categoria:Nombre» a su id."""
    if isinstance(valor, str) and valor.startswith("cuenta:"):
        return _id_de(conexion, "cuentas", valor.split(":", 1)[1])
    if isinstance(valor, str) and valor.startswith("categoria:"):
        return _id_de(conexion, "categorias", valor.split(":", 1)[1])
    return valor


def _corregir_cuentas(conexion: sqlite3.Connection, aplicar: bool) -> list[str]:
    cambios: list[str] = []
    for nombre, tipo, institucion in CUENTAS:
        fila = conexion.execute(
            "SELECT id, tipo, institucion FROM cuentas WHERE nombre = ?", (nombre,)
        ).fetchone()
        if fila is None:
            continue
        if fila["tipo"] == tipo and fila["institucion"] == institucion:
            continue
        cambios.append(
            f"cuenta «{nombre}»: {fila['tipo']}/{fila['institucion'] or '—'} → "
            f"{tipo}/{institucion or '—'}"
        )
        if aplicar:
            conexion.execute(
                "UPDATE cuentas SET tipo = ?, institucion = ? WHERE id = ?",
                (tipo, institucion, fila["id"]),
            )

    for nombre, tipo, institucion, activa in CUENTAS_NUEVAS:
        existe = conexion.execute(
            "SELECT 1 FROM cuentas WHERE nombre = ?", (nombre,)
        ).fetchone()
        if existe:
            continue
        cambios.append(f"cuenta nueva «{nombre}» ({tipo}, {institucion})")
        if aplicar:
            conexion.execute(
                "INSERT INTO cuentas (nombre, tipo, institucion, activa) "
                "VALUES (?, ?, ?, ?)",
                (nombre, tipo, institucion, activa),
            )

    return cambios


def _corregir_movimientos(conexion: sqlite3.Connection, aplicar: bool) -> list[str]:
    cambios: list[str] = []
    for folio, condicion, descripcion, valores in MOVIMIENTOS:
        filas = conexion.execute(
            "SELECT id FROM movimientos "
            f"WHERE referencia_externa = ? AND ({condicion})",
            (folio,),
        ).fetchall()
        if not filas:
            continue

        ids = [int(f["id"]) for f in filas]
        cambios.append(f"movimiento(s) {ids}: {descripcion}")
        if not aplicar:
            continue

        resueltos = {col: _resolver(conexion, val) for col, val in valores.items()}
        asignaciones = ", ".join(f"{col} = ?" for col in resueltos)
        for identificador in ids:
            conexion.execute(
                f"UPDATE movimientos SET {asignaciones}, "
                f"actualizado_en = datetime('now') WHERE id = ?",
                [*resueltos.values(), identificador],
            )

    # El medio de pago de un gasto lo implica la cuenta; lo que quedó
    # vacío se rellena para que el análisis por medio de pago no mienta.
    por_tipo = {"Efectivo": "Efectivo", "Débito": "Débito", "Crédito": "Crédito"}
    for tipo_cuenta, medio in por_tipo.items():
        medio_id = conexion.execute(
            "SELECT id FROM medios_pago WHERE nombre = ?", (medio,)
        ).fetchone()
        if medio_id is None:
            continue
        pendientes = conexion.execute(
            """
            SELECT COUNT(*) AS n FROM movimientos
            WHERE tipo = 'Gasto' AND medio_pago_id IS NULL
              AND cuenta_id IN (SELECT id FROM cuentas WHERE tipo = ?)
            """,
            (tipo_cuenta,),
        ).fetchone()["n"]
        if not pendientes:
            continue
        cambios.append(f"{pendientes} gastos desde {tipo_cuenta} sin medio → {medio}")
        if aplicar:
            conexion.execute(
                """
                UPDATE movimientos SET medio_pago_id = ?
                WHERE tipo = 'Gasto' AND medio_pago_id IS NULL
                  AND cuenta_id IN (SELECT id FROM cuentas WHERE tipo = ?)
                """,
                (int(medio_id["id"]), tipo_cuenta),
            )

    cambios += _consolidar_ganancias(conexion, aplicar)
    cambios += _alinear_fechas(conexion, aplicar)

    # Lo que no es gasto no lleva medio de pago ni banderas de gasto.
    neutros = conexion.execute(
        """
        SELECT COUNT(*) AS n FROM movimientos
        WHERE tipo <> 'Gasto'
          AND (medio_pago_id IS NOT NULL OR recurrente = 1 OR planeado = 0
               OR necesidad <> 'Esencial' OR naturaleza <> 'Variable')
        """
    ).fetchone()["n"]
    if neutros:
        cambios.append(f"{neutros} movimientos que no son gasto con banderas de gasto")
        if aplicar:
            conexion.execute(
                """
                UPDATE movimientos
                   SET medio_pago_id = NULL, recurrente = 0, planeado = 1,
                       necesidad = 'Esencial', naturaleza = 'Variable'
                 WHERE tipo <> 'Gasto'
                """
            )

    return cambios


def _consolidar_ganancias(conexion: sqlite3.Connection, aplicar: bool) -> list[str]:
    """
    Junta las ganancias de intereses de cada mes en un solo ingreso.

    Se reconocen por el concepto del banco («Ganancia…») o por la
    descripción propia, dentro de la cuenta de Mercado Pago. El total
    conserva los folios de todas, separados por coma, que es como el
    importador reconoce después que ya están registradas. Un total ya
    hecho se distingue por su concepto «(N movimientos)» y no se vuelve
    a tocar.
    """
    filas = conexion.execute(
        """
        SELECT m.id, m.fecha, m.monto, m.referencia_externa, m.cuenta_id,
               m.categoria_id, m.subcategoria_id
        FROM movimientos m
        JOIN cuentas cu ON cu.id = m.cuenta_id
        WHERE m.tipo = 'Ingreso'
          AND cu.nombre = 'MP TDD'
          AND (m.descripcion_banco LIKE 'Ganancia%' OR m.descripcion LIKE 'Ganancia%')
          AND m.descripcion_banco NOT LIKE '%movimientos)'
        ORDER BY m.fecha, m.id
        """
    ).fetchall()
    if not filas:
        return []

    por_mes: dict[str, list[sqlite3.Row]] = {}
    for fila in filas:
        por_mes.setdefault(fila["fecha"][:7], []).append(fila)

    cambios: list[str] = []
    for periodo, grupo in sorted(por_mes.items()):
        total = round(sum(float(f["monto"]) for f in grupo), 2)
        ids = [int(f["id"]) for f in grupo]
        cambios.append(
            f"{len(grupo)} ganancias de {periodo} ({ids[0]}…{ids[-1]}) → un "
            f"ingreso por {total:,.2f}"
        )
        if not aplicar:
            continue

        ultimo = grupo[-1]
        folios = ",".join(
            f["referencia_externa"] for f in grupo if f["referencia_externa"]
        )
        conexion.execute(
            """
            INSERT INTO movimientos (
                fecha, tipo, monto, categoria_id, subcategoria_id, cuenta_id,
                descripcion, descripcion_banco, referencia_externa,
                fecha_pago, fecha_banco, estado
            ) VALUES (?, 'Ingreso', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Confirmado')
            """,
            (
                ultimo["fecha"],
                total,
                ultimo["categoria_id"],
                ultimo["subcategoria_id"],
                ultimo["cuenta_id"],
                f"Ganancias Mercado Pago {periodo}",
                f"Ganancias del mes ({len(grupo)} movimientos)",
                folios,
                ultimo["fecha"],
                ultimo["fecha"],
            ),
        )
        marcadores = ", ".join("?" for _ in ids)
        conexion.execute(f"DELETE FROM movimientos WHERE id IN ({marcadores})", ids)

    return cambios


def _alinear_fechas(conexion: sqlite3.Connection, aplicar: bool) -> list[str]:
    """
    Pone la fecha del banco donde la propia se alejó más de un día.

    Un día de diferencia es normal (el banco aplica al siguiente); más,
    casi siempre es un arrastre del asistente. La fecha de pago también
    pasa a ser la del banco: si la fecha propia estaba mal, la de pago
    que salió de ella también.
    """
    filas = conexion.execute(
        """
        SELECT id, fecha, fecha_banco, fecha_pago, descripcion
        FROM movimientos
        WHERE fecha_banco IS NOT NULL
          AND abs(julianday(fecha) - julianday(fecha_banco)) > 1
        ORDER BY id
        """
    ).fetchall()

    cambios: list[str] = []
    for fila in filas:
        cambios.append(
            f"movimiento {fila['id']} «{fila['descripcion'][:28]}»: fecha "
            f"{fila['fecha']} → {fila['fecha_banco']} (la del banco)"
        )
        if not aplicar:
            continue
        conexion.execute(
            """
            UPDATE movimientos
               SET fecha = fecha_banco,
                   fecha_pago = fecha_banco,
                   actualizado_en = datetime('now')
             WHERE id = ?
            """,
            (fila["id"],),
        )

    return cambios


def _libros(conexion: sqlite3.Connection) -> list[str]:
    """Resume el flujo neto de cada cuenta, para ver cómo quedó."""
    filas = conexion.execute(
        """
        SELECT cu.nombre, cu.tipo,
               COALESCE(SUM(f.movimiento), 0) AS neto,
               COUNT(f.movimiento_id) AS patas
        FROM cuentas cu
        LEFT JOIN v_flujo_cuentas f ON f.cuenta_id = cu.id
        WHERE cu.activa = 1
        GROUP BY cu.id
        ORDER BY cu.nombre
        """
    ).fetchall()
    return [
        f"  {f['nombre']:<14} {f['tipo']:<10} {f['neto']:>12,.2f}  ({f['patas']} patas)"
        for f in filas
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", type=Path, default=settings.db_path)
    parser.add_argument("--dry-run", action="store_true", help="Sólo muestra.")
    argumentos = parser.parse_args()

    setup_logging()
    ruta = Path(argumentos.db)
    if not ruta.exists():
        print(f"No existe {ruta}", file=sys.stderr)
        return 1

    aplicar = not argumentos.dry_run
    if aplicar:
        print(f"Respaldo: {_respaldar(ruta)}")

    # Las migraciones genéricas primero: traducen «Banco» y ponen fecha de
    # pago a lo que ya no puede quedar sin ella.
    if aplicar:
        init_database(ruta)

    conexion = sqlite3.connect(ruta)
    conexion.row_factory = sqlite3.Row
    conexion.execute("PRAGMA foreign_keys = ON")

    try:
        cambios = _corregir_cuentas(conexion, aplicar)
        cambios += _corregir_movimientos(conexion, aplicar)

        if aplicar:
            conexion.commit()
        else:
            conexion.rollback()
    except Exception:
        conexion.rollback()
        raise

    titulo = "Cambios aplicados" if aplicar else "Cambios que se aplicarían"
    print(f"\n{titulo}: {len(cambios)}")
    for cambio in cambios:
        print(f"  - {cambio}")

    if aplicar:
        print("\nFlujo neto por cuenta (sin anclas, desde cero):")
        print("\n".join(_libros(conexion)))

    conexion.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
