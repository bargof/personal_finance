from __future__ import annotations

import sqlite3

import pandas as pd

from finanzas.data.repositories.catalogos_repository import CatalogosRepository
from finanzas.domain.entities import ReglasFinancieras
from finanzas.domain.enums import TipoCuenta

# ═══════════════════════════════════════════════════════════
# Catálogos: alta, baja y consulta de las listas maestras.
#
# El servicio es quien decide qué es un error del usuario
# (nombre duplicado, catálogo en uso) y lo traduce a un
# mensaje accionable en vez de dejar salir el error de SQLite.
# ═══════════════════════════════════════════════════════════


class CatalogoEnUsoError(RuntimeError):
    """El elemento no puede eliminarse porque tiene movimientos asociados."""


class NombreDuplicadoError(ValueError):
    """Ya existe un elemento con ese nombre en el catálogo."""


class CatalogosService:
    """Casos de uso sobre categorías, subcategorías, cuentas y medios de pago."""

    def __init__(self, repositorio: CatalogosRepository | None = None) -> None:
        self._repo = repositorio or CatalogosRepository()

    # ── Consulta ─────────────────────────────────────────

    def categorias(self, solo_activas: bool = True) -> pd.DataFrame:
        """Devuelve las categorías con su conteo de movimientos."""
        return self._repo.listar_categorias(solo_activas=solo_activas)

    def subcategorias(self, categoria_id: int | None = None) -> pd.DataFrame:
        """Devuelve las subcategorías, opcionalmente de una sola categoría."""
        return self._repo.listar_subcategorias(categoria_id)

    def cuentas(self, solo_activas: bool = True) -> pd.DataFrame:
        """Devuelve las cuentas registradas, con el lado del balance que les toca."""
        df = self._repo.listar_cuentas(solo_activas=solo_activas)
        if not df.empty:
            df["lado"] = df["tipo"].map(lambda tipo: str(TipoCuenta(tipo).lado))

        return df

    def tipos_de_cuentas(self) -> dict[int, str]:
        """Devuelve {id: tipo} de todas las cuentas."""
        return self._repo.tipos_de_cuentas()

    def medios_pago(self, solo_activos: bool = True) -> pd.DataFrame:
        """Devuelve los medios de pago registrados."""
        return self._repo.listar_medios_pago(solo_activos=solo_activos)

    def opciones_captura(self) -> dict[str, pd.DataFrame]:
        """
        Devuelve de una sola vez los catálogos que necesita el formulario
        de captura, para no dispersar consultas en la página.
        """
        return {
            "categorias": self.categorias(),
            "subcategorias": self.subcategorias(),
            "cuentas": self.cuentas(),
            "medios_pago": self.medios_pago(),
        }

    # ── Categorías ───────────────────────────────────────

    def crear_categoria(self, nombre: str, tipo: str, orden: int = 0) -> int:
        """Da de alta una categoría validando que el nombre sea único."""
        nombre = _validar_nombre(nombre, "categoría")

        try:
            return self._repo.crear_categoria(nombre, tipo, orden)
        except sqlite3.IntegrityError as error:
            raise NombreDuplicadoError(
                f"Ya existe una categoría llamada «{nombre}»."
            ) from error

    def actualizar_categoria(
        self,
        categoria_id: int,
        nombre: str,
        tipo: str,
        activa: bool,
        orden: int,
    ) -> None:
        """Actualiza una categoría existente."""
        nombre = _validar_nombre(nombre, "categoría")

        try:
            self._repo.actualizar_categoria(categoria_id, nombre, tipo, activa, orden)
        except sqlite3.IntegrityError as error:
            raise NombreDuplicadoError(
                f"Ya existe una categoría llamada «{nombre}»."
            ) from error

    def eliminar_categoria(self, categoria_id: int) -> None:
        """Elimina una categoría que no tenga movimientos."""
        try:
            self._repo.eliminar_categoria(categoria_id)
        except ValueError as error:
            raise CatalogoEnUsoError(str(error)) from error

    # ── Subcategorías ────────────────────────────────────

    def crear_subcategoria(self, categoria_id: int, nombre: str) -> int:
        """Da de alta una subcategoría dentro de una categoría."""
        nombre = _validar_nombre(nombre, "subcategoría")

        try:
            return self._repo.crear_subcategoria(categoria_id, nombre)
        except sqlite3.IntegrityError as error:
            raise NombreDuplicadoError(
                f"Esa categoría ya tiene una subcategoría «{nombre}»."
            ) from error

    def eliminar_subcategoria(self, subcategoria_id: int) -> None:
        """Elimina una subcategoría y desliga sus movimientos."""
        self._repo.eliminar_subcategoria(subcategoria_id)

    # ── Cuentas ──────────────────────────────────────────

    def crear_cuenta(self, nombre: str, tipo: str, institucion: str = "") -> int:
        """Da de alta una cuenta; el nombre debe ser único y el tipo, del catálogo."""
        nombre = _validar_nombre(nombre, "cuenta")
        tipo = _validar_tipo_cuenta(tipo)

        try:
            return self._repo.crear_cuenta(nombre, tipo, institucion)
        except sqlite3.IntegrityError as error:
            raise NombreDuplicadoError(
                f"Ya existe una cuenta llamada «{nombre}»."
            ) from error

    def actualizar_cuenta(
        self,
        cuenta_id: int,
        nombre: str,
        tipo: str,
        institucion: str,
        activa: bool,
    ) -> None:
        """Actualiza una cuenta existente."""
        nombre = _validar_nombre(nombre, "cuenta")
        tipo = _validar_tipo_cuenta(tipo)

        try:
            self._repo.actualizar_cuenta(cuenta_id, nombre, tipo, institucion, activa)
        except sqlite3.IntegrityError as error:
            raise NombreDuplicadoError(
                f"Ya existe una cuenta llamada «{nombre}»."
            ) from error

    def eliminar_cuenta(self, cuenta_id: int) -> None:
        """Elimina una cuenta que no tenga movimientos."""
        try:
            self._repo.eliminar_cuenta(cuenta_id)
        except ValueError as error:
            raise CatalogoEnUsoError(str(error)) from error

    # ── Medios de pago ───────────────────────────────────

    def crear_medio_pago(self, nombre: str) -> int:
        """Da de alta un medio de pago."""
        nombre = _validar_nombre(nombre, "medio de pago")

        try:
            return self._repo.crear_medio_pago(nombre)
        except sqlite3.IntegrityError as error:
            raise NombreDuplicadoError(
                f"Ya existe un medio de pago llamado «{nombre}»."
            ) from error

    def eliminar_medio_pago(self, medio_pago_id: int) -> None:
        """Elimina un medio de pago y desliga sus movimientos."""
        self._repo.eliminar_medio_pago(medio_pago_id)

    # ── Reglas financieras ───────────────────────────────

    def reglas(self) -> ReglasFinancieras:
        """Devuelve las reglas financieras vigentes."""
        return self._repo.leer_reglas()

    def guardar_reglas(self, reglas: ReglasFinancieras) -> None:
        """Persiste las reglas financieras."""
        self._repo.guardar_reglas(reglas)


def _validar_tipo_cuenta(tipo: str) -> str:
    """Comprueba que el tipo de cuenta sea uno del catálogo."""
    try:
        return str(TipoCuenta(tipo))
    except ValueError as error:
        opciones = ", ".join(str(valor) for valor in TipoCuenta)
        raise ValueError(
            f"«{tipo}» no es un tipo de cuenta. Elige uno de: {opciones}."
        ) from error


def _validar_nombre(nombre: str, que: str) -> str:
    """Normaliza y valida el nombre de un elemento de catálogo."""
    limpio = (nombre or "").strip()
    if not limpio:
        raise ValueError(f"El nombre de la {que} no puede estar vacío.")
    if len(limpio) > 60:
        raise ValueError(f"El nombre de la {que} no puede exceder 60 caracteres.")

    return limpio
