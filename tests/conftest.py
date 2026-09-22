from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from finanzas.data.repositories.catalogos_repository import CatalogosRepository
from finanzas.data.repositories.movimientos_repository import MovimientosRepository
from finanzas.data.seed import preparar_base
from finanzas.domain.entities import Movimiento
from finanzas.domain.enums import Naturaleza, Necesidad, TipoMovimiento


@pytest.fixture
def db_path(tmp_path: Path) -> Iterator[str]:
    """Base SQLite limpia, con esquema y catálogos base, por prueba."""
    ruta = tmp_path / "prueba.db"
    preparar_base(str(ruta))

    yield str(ruta)


@pytest.fixture
def catalogos(db_path: str) -> CatalogosRepository:
    """Repositorio de catálogos apuntando a la base de prueba."""
    return CatalogosRepository(db_path)


@pytest.fixture
def movimientos(db_path: str) -> MovimientosRepository:
    """Repositorio de movimientos apuntando a la base de prueba."""
    return MovimientosRepository(db_path)


@pytest.fixture
def ids_catalogo(catalogos: CatalogosRepository) -> dict[str, int]:
    """Ids de los catálogos base que usan las pruebas."""
    return {
        "vivienda": catalogos.mapa_nombre_id("categorias")["Vivienda"],
        "restaurantes": catalogos.mapa_nombre_id("categorias")["Restaurantes"],
        "sueldo": catalogos.mapa_nombre_id("categorias")["Sueldo"],
        "ahorro": catalogos.mapa_nombre_id("categorias")["Ahorro programado"],
        "cuenta": catalogos.mapa_nombre_id("cuentas")["Cuenta principal"],
        "efectivo": catalogos.mapa_nombre_id("cuentas")["Efectivo"],
        "tarjeta": catalogos.mapa_nombre_id("cuentas")["Tarjeta crédito"],
        "ahorro_cuenta": catalogos.mapa_nombre_id("cuentas")["Cuenta ahorro"],
    }


@pytest.fixture
def movimiento_gasto(ids_catalogo: dict[str, int]) -> Movimiento:
    """Un gasto confirmado de renta."""
    return Movimiento(
        fecha=date(2026, 8, 2),
        tipo=TipoMovimiento.GASTO,
        monto=6500.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
        descripcion="Renta mensual",
        necesidad=Necesidad.ESENCIAL,
        naturaleza=Naturaleza.FIJO,
    )
