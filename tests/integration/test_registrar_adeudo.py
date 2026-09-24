from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

from finanzas.application.services.catalogos_service import CatalogosService
from finanzas.application.services.movimientos_service import MovimientosService
from finanzas.application.services.patrimonio_service import PatrimonioService
from finanzas.data.repositories.catalogos_repository import CatalogosRepository
from finanzas.data.repositories.movimientos_repository import MovimientosRepository
from finanzas.data.repositories.patrimonio_repository import PatrimonioRepository
from finanzas.domain.enums import TipoCuenta, TipoMovimiento

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "scripts"))

from registrar_adeudo import main  # noqa: E402 - el script vive fuera del paquete

# ═══════════════════════════════════════════════════════════
# El montaje de una deuda que se paga en abonos
#
# El script escribe sobre la base de verdad, así que lo que
# hace se fija aquí: qué cuenta crea, qué deja anclado y en
# qué convierte los gastos que en realidad eran abonos.
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def abonos(db_path: str, ids_catalogo: dict[str, int]) -> list[int]:
    """Dos gastos ya capturados que en realidad son abonos a una deuda."""
    servicio = MovimientosService(MovimientosRepository(db_path))

    return [
        servicio.registrar(
            fecha=fecha,
            tipo=TipoMovimiento.GASTO,
            monto=monto,
            categoria_id=ids_catalogo["vivienda"],
            cuenta_id=ids_catalogo["cuenta"],
            descripcion="Abono",
        )
        for fecha, monto in ((date(2026, 6, 18), 3_000.0), (date(2026, 8, 1), 4_000.0))
    ]


def _correr(db_path: str, *argumentos: str) -> int:
    """Llama al script como lo llamaría la terminal."""
    sys.argv = ["registrar_adeudo.py", "--db", db_path, *argumentos]

    return main()


def test_monta_la_cuenta_y_convierte_los_abonos(db_path, abonos):
    """
    El caso completo: la deuda queda como cuenta y los gastos como abonos.

    El saldo anclado es el de hoy, así que hacia atrás el sistema deduce
    que antes de los abonos se debía más.
    """
    codigo = _correr(
        db_path,
        "--cuenta",
        "Adeudo",
        "--debes",
        "57978.45",
        "--al",
        "2026-09-23",
        "--abonos",
        ",".join(str(identificador) for identificador in abonos),
    )
    assert codigo == 0

    cuentas = CatalogosService(CatalogosRepository(db_path)).cuentas()
    deuda = cuentas[cuentas["nombre"] == "Adeudo"].iloc[0]
    assert deuda["tipo"] == str(TipoCuenta.PRESTAMO)

    patrimonio = PatrimonioService(PatrimonioRepository(db_path))
    saldos = patrimonio.saldos(date(2026, 9, 23))
    fila = saldos[saldos["cuenta"] == "Adeudo"].iloc[0]
    assert fila["saldo_visto"] == 57_978.45
    assert fila["lado"] == "Pasivo"

    # Antes de los dos abonos se debían 7.000 más.
    antes = patrimonio.saldos(date(2026, 5, 31))
    assert antes[antes["cuenta"] == "Adeudo"].iloc[0]["saldo_visto"] == 64_978.45


def test_los_abonos_dejan_de_contar_como_gasto(db_path, abonos):
    """
    Lo que se abona a una deuda no es gasto nuevo: el gasto ya se contó
    cuando se contrajo. Quedan como traspasos hacia la cuenta.
    """
    _correr(
        db_path,
        "--cuenta",
        "Adeudo",
        "--debes",
        "57978.45",
        "--al",
        "2026-09-23",
        "--abonos",
        ",".join(str(identificador) for identificador in abonos),
    )

    servicio = MovimientosService(MovimientosRepository(db_path))
    for identificador in abonos:
        fila = servicio.obtener(identificador)
        assert fila["tipo"] == str(TipoMovimiento.TRANSFERENCIA)
        assert fila["cuenta_destino"] == "Adeudo"
        assert fila["gasto_real"] == 0.0


def test_el_cargo_hace_que_el_gasto_cuente_una_vez(db_path, ids_catalogo):
    """
    Con `--total` la deuda nace de un gasto, como una compra a crédito.

    Pesa en el presupuesto de su mes y deja la cuenta debiendo esa cifra.
    """
    codigo = _correr(
        db_path,
        "--cuenta",
        "Adeudo",
        "--total",
        "20000",
        "--cargo",
        "2026-06-01",
        "--categoria",
        "Educación",
    )
    assert codigo == 0

    servicio = MovimientosService(MovimientosRepository(db_path))
    gastos = servicio.buscar(categorias=["Educación"])
    assert len(gastos) == 1
    assert gastos.iloc[0]["gasto_real"] == 20_000.0
    assert gastos.iloc[0]["cuenta"] == "Adeudo"

    saldos = PatrimonioService(PatrimonioRepository(db_path)).saldos(date(2026, 6, 30))
    assert saldos[saldos["cuenta"] == "Adeudo"].iloc[0]["saldo_visto"] == 20_000.0


def test_en_seco_no_escribe_nada(db_path, abonos):
    """`--dry-run` enseña el plan y deja la base como estaba."""
    codigo = _correr(
        db_path,
        "--cuenta",
        "Adeudo",
        "--debes",
        "57978.45",
        "--al",
        "2026-09-23",
        "--abonos",
        str(abonos[0]),
        "--dry-run",
    )
    assert codigo == 0

    cuentas = CatalogosService(CatalogosRepository(db_path)).cuentas()
    assert "Adeudo" not in set(cuentas["nombre"])

    servicio = MovimientosService(MovimientosRepository(db_path))
    assert servicio.obtener(abonos[0])["tipo"] == str(TipoMovimiento.GASTO)


def test_las_dos_formas_de_empezar_se_excluyen(db_path):
    """
    Registrar el cargo y anclar el saldo a la vez contaría la deuda dos
    veces, así que el script se niega.
    """
    codigo = _correr(
        db_path,
        "--cuenta",
        "Adeudo",
        "--total",
        "20000",
        "--cargo",
        "2026-06-01",
        "--debes",
        "18000",
        "--al",
        "2026-08-31",
    )

    assert codigo == 1
    cuentas = CatalogosService(CatalogosRepository(db_path)).cuentas()
    assert "Adeudo" not in set(cuentas["nombre"])


def test_no_pisa_una_cuenta_que_no_es_deuda(db_path):
    """Un nombre ya usado por una cuenta de activo detiene el montaje."""
    CatalogosService(CatalogosRepository(db_path)).crear_cuenta(
        "Adeudo", TipoCuenta.DEBITO
    )

    codigo = _correr(
        db_path, "--cuenta", "Adeudo", "--debes", "100", "--al", "2026-09-23"
    )

    assert codigo == 1
    assert PatrimonioService(PatrimonioRepository(db_path)).anclas().empty
