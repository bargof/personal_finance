"""
Monta una deuda que se paga en abonos como su propia cuenta.

Una colegiatura a plazos, un préstamo familiar o cualquier cosa que se
deba y se vaya pagando en partes no cabe como gasto devengado: un gasto
se marca pagado una sola vez y no admite abonos. Cabe como cuenta de
tipo Préstamo, igual que una tarjeta: el gasto cuenta una sola vez —al
contraer la deuda— y cada abono es un traspaso hacia esa cuenta, que no
vuelve a contar como gasto. El saldo de la cuenta es lo que falta por
pagar, y se lee en el dashboard junto a las demás.

Este script hace ese montaje de una vez: da de alta la cuenta, deja
registrado de dónde viene la deuda y convierte en abonos los gastos que
ya estaban capturados como si lo fueran.

De dónde sale el saldo inicial, según lo que se sepa:

`--total` con `--cargo` registra el cargo que creó la deuda como un
gasto pagado con esa cuenta. Es lo correcto cuando la deuda es de este
año y el gasto debe pesar en el presupuesto de su mes.

`--debes` con `--al` ancla el saldo sin registrar ningún gasto. Es para
una deuda vieja, de antes de llevar cuentas, que no tiene por qué entrar
al presupuesto de ningún mes.

Uso
---
    poetry run python scripts/registrar_adeudo.py --cuenta ITAM \
        --total 25000 --cargo 2026-06-01 --categoria Educación \
        --abonos 555,104 --dry-run

    poetry run python scripts/registrar_adeudo.py --cuenta ITAM \
        --debes 18000 --al 2026-08-31 --abonos 555,104
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

from finanzas.application.services.catalogos_service import CatalogosService
from finanzas.application.services.movimientos_service import MovimientosService
from finanzas.application.services.patrimonio_service import PatrimonioService
from finanzas.config.logging import setup_logging
from finanzas.config.settings import settings
from finanzas.data.repositories.catalogos_repository import CatalogosRepository
from finanzas.data.repositories.movimientos_repository import MovimientosRepository
from finanzas.data.repositories.patrimonio_repository import PatrimonioRepository
from finanzas.domain.enums import TipoCuenta, TipoMovimiento


def _respaldar(ruta: Path) -> Path:
    """Copia el archivo SQLite junto al original, con marca de tiempo."""
    sello = datetime.now().strftime("%Y%m%d-%H%M%S")
    destino = ruta.with_name(f"{ruta.stem}.respaldo-{sello}{ruta.suffix}")
    shutil.copy2(ruta, destino)

    return destino


def _fecha(texto: str) -> date:
    """Convierte AAAA-MM-DD en fecha, para argparse."""
    return date.fromisoformat(texto)


def _ids(texto: str) -> list[int]:
    """Convierte «555,104» en ids, para argparse."""
    return [int(parte) for parte in texto.split(",") if parte.strip()]


def main() -> int:
    """Monta la cuenta de la deuda y convierte sus abonos."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cuenta", required=True, help="Nombre de la deuda.")
    parser.add_argument("--institucion", default="", help="A quién se le debe.")
    parser.add_argument(
        "--total", type=float, default=None, help="Cuánto era la deuda al contraerla."
    )
    parser.add_argument(
        "--cargo", type=_fecha, default=None, help="Fecha del cargo (AAAA-MM-DD)."
    )
    parser.add_argument(
        "--categoria",
        default="Educación",
        help="Categoría del gasto que creó la deuda.",
    )
    parser.add_argument(
        "--descripcion", default="", help="Descripción del cargo que creó la deuda."
    )
    parser.add_argument(
        "--debes",
        type=float,
        default=None,
        help="Saldo pendiente a una fecha, si prefieres anclarlo sin gasto.",
    )
    parser.add_argument(
        "--al", type=_fecha, default=None, help="Fecha de ese saldo (AAAA-MM-DD)."
    )
    parser.add_argument(
        "--abonos",
        type=_ids,
        default=[],
        help="Ids de los gastos ya registrados que en realidad son abonos.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Muestra lo que haría sin escribir nada."
    )
    parser.add_argument("--db", type=Path, default=None, help="Ruta alterna al SQLite.")
    argumentos = parser.parse_args()

    logger = setup_logging(level=settings.log_level)
    ruta = argumentos.db or settings.db_path

    if not ruta.exists():
        logger.error("No existe la base %s", ruta)
        return 1

    if (argumentos.total is None) != (argumentos.cargo is None):
        logger.error("--total y --cargo van juntos.")
        return 1

    if (argumentos.debes is None) != (argumentos.al is None):
        logger.error("--debes y --al van juntos.")
        return 1

    if argumentos.total is not None and argumentos.debes is not None:
        logger.error(
            "Elige una de las dos: registrar el cargo (--total) o anclar el "
            "saldo (--debes). Las dos juntas contarían la deuda dos veces."
        )
        return 1

    catalogos = CatalogosService(CatalogosRepository(str(ruta)))
    movimientos = MovimientosService(MovimientosRepository(str(ruta)))
    patrimonio = PatrimonioService(PatrimonioRepository(str(ruta)))

    # ── Qué hay ya en la base ────────────────────────────

    cuentas = catalogos.cuentas(solo_activas=False)
    iguales = cuentas[cuentas["nombre"].str.lower() == argumentos.cuenta.lower()]
    deuda_id: int | None = None

    if not iguales.empty:
        existente = iguales.iloc[0]
        if not TipoCuenta(existente["tipo"]).es_pasivo:
            logger.error(
                "Ya hay una cuenta «%s» y es de tipo %s, no una deuda. Cámbiale "
                "el tipo en Catálogos o usa otro nombre.",
                existente["nombre"],
                existente["tipo"],
            )
            return 1
        deuda_id = int(existente["id"])

    categorias = catalogos.categorias(solo_activas=False)
    del_cargo = categorias[categorias["nombre"] == argumentos.categoria]
    if argumentos.total is not None and del_cargo.empty:
        logger.error("No existe la categoría «%s».", argumentos.categoria)
        return 1

    traspasos = categorias[categorias["tipo"] == str(TipoMovimiento.TRANSFERENCIA)]
    if argumentos.abonos and traspasos.empty:
        logger.error(
            "No hay ninguna categoría de tipo Transferencia con la que dejar "
            "los abonos. Da de alta una en Catálogos."
        )
        return 1

    # Los abonos se leen antes de tocar nada: así el plan dice qué se va a
    # convertir y, si alguno no existe, se sabe antes del respaldo.
    por_convertir = []
    for identificador in argumentos.abonos:
        fila = movimientos.obtener(identificador)
        if fila is None:
            logger.error("No existe el movimiento %s.", identificador)
            return 1
        por_convertir.append(fila)

    # ── El plan, que es lo único que se imprime en seco ──

    plan: list[str] = []
    if deuda_id is None:
        plan.append(
            f"Dar de alta la cuenta «{argumentos.cuenta}» de tipo "
            f"{TipoCuenta.PRESTAMO}"
            + (f" ({argumentos.institucion})" if argumentos.institucion else "")
        )
    else:
        plan.append(f"Usar la cuenta «{argumentos.cuenta}» que ya existe ({deuda_id})")

    if argumentos.total is not None:
        plan.append(
            f"Registrar el cargo del {argumentos.cargo:%d/%m/%Y} por "
            f"{argumentos.total:,.2f} como gasto de «{argumentos.categoria}» "
            f"pagado con «{argumentos.cuenta}»"
        )

    if argumentos.debes is not None:
        plan.append(
            f"Anclar el saldo al {argumentos.al:%d/%m/%Y}: debes "
            f"{argumentos.debes:,.2f}"
        )

    for fila in por_convertir:
        plan.append(
            f"Convertir el movimiento {int(fila['id'])} "
            f"({fila['fecha']:%d/%m/%Y} · {float(fila['monto']):,.2f} · "
            f"{fila['tipo']} · {fila['cuenta']}) en un traspaso de "
            f"«{fila['cuenta']}» a «{argumentos.cuenta}»"
        )

    for paso in plan:
        logger.info("· %s", paso)

    if argumentos.dry_run:
        logger.info("Nada escrito: es --dry-run.")
        return 0

    respaldo = _respaldar(ruta)
    logger.info("Respaldo creado en %s", respaldo)

    # ── Aplicar ──────────────────────────────────────────

    if deuda_id is None:
        deuda_id = catalogos.crear_cuenta(
            argumentos.cuenta, TipoCuenta.PRESTAMO, argumentos.institucion
        )

    if argumentos.total is not None:
        # Pagado con la cuenta de la deuda: es lo que la crea, igual que
        # una compra con tarjeta. El gasto cuenta una vez, aquí.
        movimientos.registrar(
            fecha=argumentos.cargo,
            tipo=TipoMovimiento.GASTO,
            monto=argumentos.total,
            categoria_id=int(del_cargo.iloc[0]["id"]),
            cuenta_id=deuda_id,
            descripcion=argumentos.descripcion or f"Adeudo {argumentos.cuenta}",
            nota="Cargo que creó la deuda; los abonos son traspasos a esta cuenta.",
        )

    if argumentos.debes is not None:
        patrimonio.verificar_saldo(
            deuda_id,
            argumentos.al,
            argumentos.debes,
            nota=f"Saldo inicial de «{argumentos.cuenta}»",
        )

    for fila in por_convertir:
        movimientos.actualizar(
            int(fila["id"]),
            tipo=TipoMovimiento.TRANSFERENCIA,
            categoria_id=int(traspasos.iloc[0]["id"]),
            cuenta_destino_id=deuda_id,
        )

    saldo = patrimonio.saldos()
    fila = saldo[saldo["cuenta_id"] == deuda_id]
    if not fila.empty:
        logger.info(
            "Listo. Hoy debes %.2f en «%s».",
            float(fila.iloc[0]["saldo_visto"]),
            argumentos.cuenta,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
