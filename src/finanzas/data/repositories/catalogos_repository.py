from __future__ import annotations

import sqlite3

import pandas as pd

from finanzas.data.database import connect
from finanzas.domain.entities import ReglasFinancieras

# ═══════════════════════════════════════════════════════════
# Catálogos: categorías, subcategorías, cuentas, medios de
# pago y las reglas financieras configurables.
# ═══════════════════════════════════════════════════════════


class CatalogosRepository:
    """Acceso a las listas maestras que dan consistencia a la captura."""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path

    # ── Categorías ───────────────────────────────────────

    def listar_categorias(self, solo_activas: bool = True) -> pd.DataFrame:
        """Devuelve las categorías con su número de movimientos asociados."""
        filtro = "WHERE c.activa = 1" if solo_activas else ""
        consulta = f"""
            SELECT
                c.id, c.nombre, c.tipo, c.activa, c.orden,
                COUNT(m.id) AS movimientos
            FROM categorias c
            LEFT JOIN movimientos m ON m.categoria_id = c.id
            {filtro}
            GROUP BY c.id, c.nombre, c.tipo, c.activa, c.orden
            ORDER BY c.orden, c.nombre
        """
        with connect(self._db_path) as conexion:
            return pd.read_sql_query(consulta, conexion)

    def crear_categoria(self, nombre: str, tipo: str, orden: int = 0) -> int:
        """Da de alta una categoría y devuelve su id."""
        with connect(self._db_path) as conexion:
            cursor = conexion.execute(
                "INSERT INTO categorias (nombre, tipo, orden) VALUES (?, ?, ?)",
                (nombre.strip(), tipo, orden),
            )
            return int(cursor.lastrowid)

    def actualizar_categoria(
        self,
        categoria_id: int,
        nombre: str,
        tipo: str,
        activa: bool,
        orden: int,
    ) -> None:
        """Actualiza los datos de una categoría existente."""
        with connect(self._db_path) as conexion:
            conexion.execute(
                """
                UPDATE categorias
                   SET nombre = ?, tipo = ?, activa = ?, orden = ?
                 WHERE id = ?
                """,
                (nombre.strip(), tipo, int(activa), orden, categoria_id),
            )

    def eliminar_categoria(self, categoria_id: int) -> None:
        """
        Elimina una categoría sin movimientos.

        Raises
        ------
        ValueError
            Si la categoría tiene movimientos asociados; en ese caso conviene
            desactivarla para no perder el histórico.
        """
        with connect(self._db_path) as conexion:
            usados = conexion.execute(
                "SELECT COUNT(*) AS n FROM movimientos WHERE categoria_id = ?",
                (categoria_id,),
            ).fetchone()["n"]

            if usados:
                raise ValueError(
                    f"La categoría tiene {usados} movimientos. "
                    "Desactívala en lugar de eliminarla."
                )

            conexion.execute("DELETE FROM categorias WHERE id = ?", (categoria_id,))

    # ── Subcategorías ────────────────────────────────────

    def listar_subcategorias(self, categoria_id: int | None = None) -> pd.DataFrame:
        """Devuelve las subcategorías, opcionalmente de una sola categoría."""
        consulta = """
            SELECT s.id, s.categoria_id, c.nombre AS categoria,
                   s.nombre, s.activa
            FROM subcategorias s
            JOIN categorias c ON c.id = s.categoria_id
        """
        parametros: tuple[object, ...] = ()
        if categoria_id is not None:
            consulta += " WHERE s.categoria_id = ?"
            parametros = (categoria_id,)
        consulta += " ORDER BY c.nombre, s.nombre"

        with connect(self._db_path) as conexion:
            return pd.read_sql_query(consulta, conexion, params=parametros)

    def crear_subcategoria(self, categoria_id: int, nombre: str) -> int:
        """Da de alta una subcategoría dentro de una categoría."""
        with connect(self._db_path) as conexion:
            cursor = conexion.execute(
                "INSERT INTO subcategorias (categoria_id, nombre) VALUES (?, ?)",
                (categoria_id, nombre.strip()),
            )
            return int(cursor.lastrowid)

    def subcategoria_pertenece_a(
        self, subcategoria_id: int | None, categoria_id: int | None
    ) -> bool:
        """
        Indica si la subcategoría cuelga de esa categoría.

        El esquema no lo puede garantizar: `movimientos` y `suscripciones`
        guardan las dos claves por separado, y SQLite no tiene forma de
        exigir que concuerden. La coherencia se valida aquí.
        """
        if subcategoria_id is None:
            return True
        if categoria_id is None:
            return False

        with connect(self._db_path) as conexion:
            fila = conexion.execute(
                "SELECT 1 FROM subcategorias WHERE id = ? AND categoria_id = ?",
                (subcategoria_id, categoria_id),
            ).fetchone()

        return fila is not None

    def eliminar_subcategoria(self, subcategoria_id: int) -> None:
        """Elimina una subcategoría y desliga los movimientos que la usaban."""
        with connect(self._db_path) as conexion:
            conexion.execute(
                "UPDATE movimientos SET subcategoria_id = NULL "
                "WHERE subcategoria_id = ?",
                (subcategoria_id,),
            )
            conexion.execute(
                "DELETE FROM subcategorias WHERE id = ?", (subcategoria_id,)
            )

    # ── Cuentas ──────────────────────────────────────────

    def listar_cuentas(self, solo_activas: bool = True) -> pd.DataFrame:
        """Devuelve las cuentas registradas."""
        filtro = "WHERE activa = 1" if solo_activas else ""
        with connect(self._db_path) as conexion:
            return pd.read_sql_query(
                f"SELECT id, nombre, tipo, institucion, activa "
                f"FROM cuentas {filtro} ORDER BY nombre",
                conexion,
            )

    def tipos_de_cuentas(self) -> dict[int, str]:
        """
        Devuelve {id: tipo} de todas las cuentas, activas o no.

        Es lo que las reglas de captura necesitan para saber si una cuenta
        es de crédito, de ahorro o de caja sin cargar el catálogo entero.
        """
        with connect(self._db_path) as conexion:
            filas = conexion.execute("SELECT id, tipo FROM cuentas").fetchall()

        return {int(fila["id"]): fila["tipo"] for fila in filas}

    def crear_cuenta(self, nombre: str, tipo: str, institucion: str = "") -> int:
        """Da de alta una cuenta y devuelve su id."""
        with connect(self._db_path) as conexion:
            cursor = conexion.execute(
                "INSERT INTO cuentas (nombre, tipo, institucion) VALUES (?, ?, ?)",
                (nombre.strip(), tipo, institucion.strip()),
            )
            return int(cursor.lastrowid)

    def actualizar_cuenta(
        self,
        cuenta_id: int,
        nombre: str,
        tipo: str,
        institucion: str,
        activa: bool,
    ) -> None:
        """Actualiza los datos de una cuenta."""
        with connect(self._db_path) as conexion:
            conexion.execute(
                """
                UPDATE cuentas
                   SET nombre = ?, tipo = ?, institucion = ?, activa = ?
                 WHERE id = ?
                """,
                (nombre.strip(), tipo, institucion.strip(), int(activa), cuenta_id),
            )

    def eliminar_cuenta(self, cuenta_id: int) -> None:
        """
        Elimina una cuenta sin movimientos.

        Raises
        ------
        ValueError
            Si la cuenta tiene movimientos asociados.
        """
        with connect(self._db_path) as conexion:
            usados = conexion.execute(
                "SELECT COUNT(*) AS n FROM movimientos WHERE cuenta_id = ?",
                (cuenta_id,),
            ).fetchone()["n"]

            if usados:
                raise ValueError(
                    f"La cuenta tiene {usados} movimientos. "
                    "Desactívala en lugar de eliminarla."
                )

            conexion.execute("DELETE FROM cuentas WHERE id = ?", (cuenta_id,))

    # ── Medios de pago ───────────────────────────────────

    def medio_pago_llamado(self, nombre: str) -> int | None:
        """Devuelve el id del medio de pago con ese nombre, si existe y está activo."""
        with connect(self._db_path) as conexion:
            fila = conexion.execute(
                "SELECT id FROM medios_pago WHERE nombre = ? AND activo = 1",
                (nombre,),
            ).fetchone()

        return int(fila["id"]) if fila is not None else None

    def listar_medios_pago(self, solo_activos: bool = True) -> pd.DataFrame:
        """Devuelve los medios de pago registrados."""
        filtro = "WHERE activo = 1" if solo_activos else ""
        with connect(self._db_path) as conexion:
            return pd.read_sql_query(
                f"SELECT id, nombre, activo FROM medios_pago {filtro} ORDER BY nombre",
                conexion,
            )

    def crear_medio_pago(self, nombre: str) -> int:
        """Da de alta un medio de pago."""
        with connect(self._db_path) as conexion:
            cursor = conexion.execute(
                "INSERT INTO medios_pago (nombre) VALUES (?)", (nombre.strip(),)
            )
            return int(cursor.lastrowid)

    def eliminar_medio_pago(self, medio_pago_id: int) -> None:
        """Elimina un medio de pago y desliga los movimientos que lo usaban."""
        with connect(self._db_path) as conexion:
            conexion.execute(
                "UPDATE movimientos SET medio_pago_id = NULL WHERE medio_pago_id = ?",
                (medio_pago_id,),
            )
            conexion.execute("DELETE FROM medios_pago WHERE id = ?", (medio_pago_id,))

    # ── Reglas financieras ───────────────────────────────

    def leer_reglas(self) -> ReglasFinancieras:
        """Lee la tabla `configuracion` y la convierte en reglas tipadas."""
        with connect(self._db_path) as conexion:
            filas = conexion.execute(
                "SELECT clave, valor FROM configuracion"
            ).fetchall()

        crudo = {fila["clave"]: fila["valor"] for fila in filas}
        reglas = ReglasFinancieras()

        for campo, conversor in _CONVERSORES.items():
            if campo in crudo:
                try:
                    setattr(reglas, campo, conversor(crudo.pop(campo)))
                except (TypeError, ValueError):
                    # Un valor corrupto no debe tumbar la app: se ignora y
                    # queda el valor por defecto de la entidad.
                    crudo.pop(campo, None)

        reglas.campos_extra = crudo
        return reglas

    def guardar_reglas(self, reglas: ReglasFinancieras) -> None:
        """Persiste las reglas financieras como pares clave/valor."""
        registros = [
            (campo, str(getattr(reglas, campo)), _DESCRIPCIONES[campo])
            for campo in _CONVERSORES
        ]
        with connect(self._db_path) as conexion:
            conexion.executemany(
                """
                INSERT INTO configuracion (clave, valor, descripcion)
                VALUES (?, ?, ?)
                ON CONFLICT(clave) DO UPDATE SET
                    valor = excluded.valor,
                    descripcion = excluded.descripcion
                """,
                registros,
            )

    # ── Utilidades ───────────────────────────────────────

    def mapa_nombre_id(self, tabla: str) -> dict[str, int]:
        """Devuelve un diccionario {nombre: id} para poblar selectores."""
        if tabla not in _TABLAS_CATALOGO:
            raise ValueError(f"Tabla de catálogo no reconocida: {tabla}")

        with connect(self._db_path) as conexion:
            filas = conexion.execute(f"SELECT id, nombre FROM {tabla}").fetchall()

        return {fila["nombre"]: int(fila["id"]) for fila in filas}

    def existe(self, tabla: str, nombre: str) -> bool:
        """Indica si ya existe un registro con ese nombre en el catálogo."""
        if tabla not in _TABLAS_CATALOGO:
            raise ValueError(f"Tabla de catálogo no reconocida: {tabla}")

        with connect(self._db_path) as conexion:
            try:
                fila = conexion.execute(
                    f"SELECT 1 FROM {tabla} WHERE nombre = ?", (nombre.strip(),)
                ).fetchone()
            except sqlite3.Error:
                return False

        return fila is not None


#: Tablas de catálogo con columna `nombre`, para consultas parametrizadas.
_TABLAS_CATALOGO = frozenset({"categorias", "subcategorias", "cuentas", "medios_pago"})

#: Conversores desde el texto guardado en SQLite al tipo de la regla.
_CONVERSORES = {
    "moneda": str,
    "meta_ahorro_inversion": float,
    "meses_fondo_emergencia": int,
    "max_deseos": float,
    "umbral_gasto_pequeno": float,
    "alerta_presupuesto": float,
    "dia_inicio_ciclo": int,
}

_DESCRIPCIONES = {
    "moneda": "Moneda de trabajo.",
    "meta_ahorro_inversion": "Proporción del ingreso destinada a ahorro e inversión.",
    "meses_fondo_emergencia": "Meses de gasto esencial que cubre el fondo.",
    "max_deseos": "Proporción máxima deseable del gasto en 'Deseo'.",
    "umbral_gasto_pequeno": "Monto máximo para considerar un gasto microgasto.",
    "alerta_presupuesto": "% de presupuesto usado que dispara la alerta.",
    "dia_inicio_ciclo": "Día en que arranca el ciclo mensual.",
}
