from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from finanzas.application.services.movimientos_service import (
    MovimientoInvalidoError,
    MovimientosService,
)
from finanzas.data.database import connect
from finanzas.data.repositories.movimientos_repository import MovimientosRepository
from finanzas.data.repositories.patrimonio_repository import PatrimonioRepository
from finanzas.data.seed import preparar_base
from finanzas.domain.entities import Movimiento
from finanzas.domain.enums import EstadoMovimiento, TipoMovimiento

# ═══════════════════════════════════════════════════════════
# Contabilidad devengada
#
# `fecha` y `fecha_pago` responden preguntas distintas: cuándo
# se incurrió el gasto y cuándo salió el dinero. Lo que estas
# pruebas fijan es que ninguna de las dos invada a la otra.
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def servicio(movimientos: MovimientosRepository) -> MovimientosService:
    """Servicio de movimientos sobre la base de prueba."""
    return MovimientosService(movimientos)


def _gasto(
    servicio: MovimientosService,
    ids: dict[str, int],
    monto: float = 2_500.0,
    fecha: date = date(2026, 8, 5),
    fecha_pago: date | None = None,
) -> int:
    """Registra un gasto, devengado si no se le da fecha de pago."""
    return servicio.registrar(
        fecha=fecha,
        tipo=TipoMovimiento.GASTO,
        monto=monto,
        categoria_id=ids["vivienda"],
        cuenta_id=ids["cuenta"],
        descripcion="Compra a crédito",
        fecha_pago=fecha_pago,
    )


# ═══════════════════════════════════════════════════════════
# La entidad
# ═══════════════════════════════════════════════════════════


def _entidad(**extras) -> Movimiento:
    """Arma un gasto mínimo con los campos que la prueba necesita."""
    base = {
        "fecha": date(2026, 8, 5),
        "tipo": TipoMovimiento.GASTO,
        "monto": 1_000.0,
        "categoria_id": 1,
        "cuenta_id": 1,
    }
    return Movimiento(**{**base, **extras})


def test_un_gasto_devengado_no_mueve_la_caja():
    """Mientras no haya fecha de pago, el dinero sigue en la cuenta."""
    movimiento = _entidad(fecha_pago=None)

    assert movimiento.impacto_caja == 0.0
    assert movimiento.por_pagar == 1_000.0
    assert movimiento.pagado is False


def test_un_gasto_devengado_si_consume_presupuesto():
    """El gasto ocurrió: pesa en el periodo en que se incurrió."""
    movimiento = _entidad(fecha_pago=None)

    assert movimiento.gasto_real == 1_000.0
    assert movimiento.periodo == "2026-08"


def test_al_pagarlo_la_caja_se_mueve_y_el_adeudo_desaparece():
    """Liquidar el adeudo traslada el efecto a la caja."""
    movimiento = _entidad(fecha_pago=date(2026, 9, 15))

    assert movimiento.impacto_caja == -1_000.0
    assert movimiento.por_pagar == 0.0
    assert movimiento.pagado is True


def test_el_pago_no_cambia_el_periodo_del_gasto():
    """
    Una compra de agosto pagada en septiembre pesa en dos meses distintos.

    En el presupuesto de agosto, porque ahí se incurrió; en la caja de
    septiembre, porque ahí salió el dinero.
    """
    movimiento = _entidad(fecha_pago=date(2026, 9, 15))

    assert movimiento.periodo == "2026-08"
    assert movimiento.periodo_pago == "2026-09"


def test_un_movimiento_pendiente_no_cuenta_como_adeudo():
    """
    `estado` y `fecha_pago` son independientes.

    Un gasto que todavía no ocurre no se debe: es una proyección, no un
    adeudo contraído.
    """
    movimiento = _entidad(fecha_pago=None, estado=EstadoMovimiento.PENDIENTE)

    assert movimiento.por_pagar == 0.0
    assert movimiento.gasto_real == 0.0
    assert movimiento.impacto_caja == 0.0


def test_un_ingreso_devengado_tampoco_mueve_la_caja():
    """Facturar no es cobrar: el ingreso por cobrar no suma efectivo."""
    movimiento = _entidad(tipo=TipoMovimiento.INGRESO, fecha_pago=None)

    assert movimiento.impacto_caja == 0.0
    # El ingreso por cobrar no es un adeudo propio: no entra en `por_pagar`.
    assert movimiento.por_pagar == 0.0


# ═══════════════════════════════════════════════════════════
# La vista
# ═══════════════════════════════════════════════════════════


def test_la_vista_calcula_lo_mismo_que_la_entidad(servicio, ids_catalogo, db_path):
    """La regla vive en dos lados y tiene que dar el mismo número."""
    _gasto(servicio, ids_catalogo, monto=2_500.0, fecha_pago=None)
    _gasto(servicio, ids_catalogo, monto=800.0, fecha_pago=date(2026, 8, 10))

    df = servicio.buscar()

    assert df["gasto_real"].sum() == 3_300.0
    assert df["impacto_caja"].sum() == -800.0
    assert df["por_pagar"].sum() == 2_500.0


def test_marcar_pagado_liquida_el_adeudo(servicio, ids_catalogo):
    """El atajo de la interfaz deja el movimiento en el estado correcto."""
    movimiento_id = _gasto(servicio, ids_catalogo, fecha_pago=None)
    assert servicio.total_por_pagar() == 2_500.0

    servicio.marcar_pagado(movimiento_id, date(2026, 9, 15))
    fila = servicio.buscar().set_index("id").loc[movimiento_id]

    assert servicio.total_por_pagar() == 0.0
    assert fila["impacto_caja"] == -2_500.0
    assert fila["periodo"] == "2026-08"
    assert fila["periodo_pago"] == "2026-09"


def test_marcar_por_pagar_revierte_el_pago(servicio, ids_catalogo):
    """Marcar pagado por error tiene vuelta atrás."""
    movimiento_id = _gasto(servicio, ids_catalogo, fecha_pago=date(2026, 8, 5))
    assert servicio.total_por_pagar() == 0.0

    servicio.marcar_por_pagar(movimiento_id)

    assert servicio.total_por_pagar() == 2_500.0


def test_editar_un_movimiento_conserva_su_fecha_de_pago(servicio, ids_catalogo):
    """Cambiar el monto no debe resucitar un adeudo ya liquidado."""
    movimiento_id = _gasto(servicio, ids_catalogo, fecha_pago=date(2026, 8, 5))

    servicio.actualizar(movimiento_id, monto=3_000.0)
    fila = servicio.buscar().set_index("id").loc[movimiento_id]

    assert fila["monto"] == 3_000.0
    assert fila["pagado"]
    assert servicio.total_por_pagar() == 0.0


def test_duplicar_un_movimiento_lo_deja_sin_pagar(servicio, ids_catalogo):
    """Repetir el gasto no repite su pago: volver a incurrirlo no es pagarlo."""
    movimiento_id = _gasto(servicio, ids_catalogo, fecha_pago=date(2026, 8, 5))

    copia_id = servicio.duplicar(movimiento_id, date(2026, 9, 5))
    copia = servicio.buscar().set_index("id").loc[copia_id]

    assert not copia["pagado"]
    assert copia["por_pagar"] == 2_500.0


def test_el_filtro_de_adeudos_deja_solo_lo_que_se_debe(servicio, ids_catalogo):
    """El filtro «sólo lo que debo» de la página de movimientos."""
    _gasto(servicio, ids_catalogo, monto=2_500.0, fecha_pago=None)
    _gasto(servicio, ids_catalogo, monto=800.0, fecha_pago=date(2026, 8, 10))

    deudas = servicio.buscar(solo_por_pagar=True)

    assert len(deudas) == 1
    assert deudas.iloc[0]["monto"] == 2_500.0


def test_los_adeudos_reportan_su_antiguedad(servicio, ids_catalogo):
    """Saber cuánto lleva pendiente es la mitad de la utilidad de la lista."""
    _gasto(servicio, ids_catalogo, fecha=date(2026, 8, 5), fecha_pago=None)

    adeudos = servicio.por_pagar()

    assert len(adeudos) == 1
    assert adeudos.iloc[0]["dias_pendiente"] >= 0
    assert adeudos.iloc[0]["cuenta"] == "Cuenta principal"


def test_omitir_la_fecha_de_pago_registra_un_gasto_pagado(servicio, ids_catalogo):
    """
    El caso común no debe requerir ceremonia.

    Omitir la fecha de pago asume que se pagó el día del gasto. Si el
    default fuera devengado, cualquier llamador que olvidara el campo
    —el importador, la futura API— inventaría deudas en silencio.
    """
    servicio.registrar(
        fecha=date(2026, 8, 5),
        tipo=TipoMovimiento.GASTO,
        monto=1_000.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
    )

    fila = servicio.buscar().iloc[0]

    assert fila["pagado"]
    assert fila["impacto_caja"] == -1_000.0
    assert servicio.total_por_pagar() == 0.0


def test_pasar_none_a_proposito_si_registra_un_devengado(servicio, ids_catalogo):
    """Pedir el devengado explícitamente sigue funcionando."""
    servicio.registrar(
        fecha=date(2026, 8, 5),
        tipo=TipoMovimiento.GASTO,
        monto=1_000.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
        fecha_pago=None,
    )

    assert servicio.total_por_pagar() == 1_000.0


# ═══════════════════════════════════════════════════════════
# El balance
# ═══════════════════════════════════════════════════════════


def test_el_adeudo_entra_al_balance_como_pasivo(servicio, ids_catalogo, db_path):
    """Deber la tarjeta empobrece igual que un préstamo capturado."""
    patrimonio = PatrimonioRepository(db_path)
    assert patrimonio.resumen()["patrimonio_neto"] == 0.0

    _gasto(servicio, ids_catalogo, monto=2_500.0, fecha_pago=None)
    resumen = patrimonio.resumen()

    assert resumen["por_pagar"] == 2_500.0
    assert resumen["pasivos"] == 2_500.0
    assert resumen["patrimonio_neto"] == -2_500.0


def test_pagar_el_adeudo_lo_saca_del_balance(servicio, ids_catalogo, db_path):
    """Liquidar la deuda la quita de los pasivos."""
    patrimonio = PatrimonioRepository(db_path)
    movimiento_id = _gasto(servicio, ids_catalogo, fecha_pago=None)

    servicio.marcar_pagado(movimiento_id, date(2026, 9, 15))

    assert patrimonio.resumen()["por_pagar"] == 0.0
    assert patrimonio.resumen()["patrimonio_neto"] == 0.0


def test_los_adeudos_se_suman_a_los_pasivos_capturados(servicio, ids_catalogo, db_path):
    """Las dos clases de pasivo conviven sin pisarse."""
    patrimonio = PatrimonioRepository(db_path)
    with connect(db_path) as conexion:
        conexion.execute(
            "INSERT INTO patrimonio (nombre, tipo, saldo) VALUES (?, ?, ?)",
            ("Hipoteca", "Pasivo", 900_000.0),
        )

    _gasto(servicio, ids_catalogo, monto=2_500.0, fecha_pago=None)
    resumen = patrimonio.resumen()

    assert resumen["pasivos"] == 902_500.0
    assert resumen["por_pagar"] == 2_500.0
    assert resumen["patrimonio_neto"] == -902_500.0


# ═══════════════════════════════════════════════════════════
# Migración
# ═══════════════════════════════════════════════════════════


def test_la_migracion_no_convierte_el_historico_en_adeudos(tmp_path):
    """
    Los movimientos anteriores a la columna se dan por pagados.

    Hasta ahora un movimiento confirmado era, por definición, uno pagado.
    Dejar `fecha_pago` nula en el histórico inventaría deudas que nadie
    contrajo, así que la migración la rellena con la fecha del gasto.
    """
    ruta = tmp_path / "vieja.db"
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
        INSERT INTO movimientos (fecha, tipo, monto, categoria_id, cuenta_id, estado)
        VALUES ('2026-07-01', 'Gasto', 3000, 1, 1, 'Confirmado'),
               ('2026-07-20', 'Gasto', 500, 1, 1, 'Pendiente');
        """
    )
    conexion.commit()
    conexion.close()

    preparar_base(str(ruta))

    with connect(str(ruta)) as conexion:
        filas = conexion.execute(
            "SELECT estado, fecha, fecha_pago FROM movimientos ORDER BY id"
        ).fetchall()

    confirmado, pendiente = filas
    assert confirmado["fecha_pago"] == confirmado["fecha"]
    # Lo que no había ocurrido tampoco se había pagado.
    assert pendiente["fecha_pago"] is None


def test_el_relleno_no_se_repite_en_arranques_posteriores(
    db_path, servicio, ids_catalogo
):
    """
    Un devengado nuevo sobrevive al siguiente arranque.

    El relleno corre sólo al añadir la columna; si corriera en cada
    arranque, marcaría como pagado todo lo que se debe.
    """
    _gasto(servicio, ids_catalogo, fecha_pago=None)

    preparar_base(db_path)

    assert servicio.total_por_pagar() == 2_500.0


# ═══════════════════════════════════════════════════════════
# Traspasos entre cuentas propias
#
# Una transferencia es neutra para la caja completa pero no
# para cada cuenta: sale de una y entra en otra. Es lo que
# permite registrar el pago de una tarjeta sin inventar un
# gasto que ya estaba contado.
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def cuentas(catalogos) -> dict[str, int]:
    """Las dos cuentas que usan las pruebas de traspaso."""
    mapa = catalogos.mapa_nombre_id("cuentas")
    return {"banco": mapa["Cuenta principal"], "tarjeta": mapa["Tarjeta crédito"]}


def _traspaso(servicio, ids, cuentas, monto: float = 3_200.0) -> int:
    """Registra un pago de tarjeta: del banco a la tarjeta."""
    return servicio.registrar(
        fecha=date(2026, 9, 15),
        tipo=TipoMovimiento.TRANSFERENCIA,
        monto=monto,
        categoria_id=ids["vivienda"],
        cuenta_id=cuentas["banco"],
        cuenta_destino_id=cuentas["tarjeta"],
        descripcion="Pago tarjeta",
        fecha_pago=date(2026, 9, 15),
    )


def test_un_traspaso_es_neutro_para_la_caja(servicio, ids_catalogo, cuentas):
    """Mover dinero entre cuentas propias no crea ni consume nada."""
    _traspaso(servicio, ids_catalogo, cuentas)
    fila = servicio.buscar().iloc[0]

    assert fila["impacto_caja"] == 0.0
    assert fila["gasto_real"] == 0.0


def test_un_traspaso_no_es_neutro_por_cuenta(servicio, ids_catalogo, cuentas):
    """Sale del origen y entra al destino, aunque sume cero en total."""
    _traspaso(servicio, ids_catalogo, cuentas)
    flujo = servicio.flujo_por_cuenta().set_index("cuenta")

    assert flujo.loc["Cuenta principal", "flujo_neto"] == -3_200.0
    assert flujo.loc["Tarjeta crédito", "flujo_neto"] == 3_200.0
    assert flujo["flujo_neto"].sum() == 0.0


def test_pagar_la_tarjeta_no_cuenta_el_gasto_dos_veces(servicio, ids_catalogo, cuentas):
    """
    El escenario completo: comprar con tarjeta y luego pagarla.

    El consumo se cuenta una vez, en el mes de la compra. El traspaso
    mueve el dinero entre cuentas sin volver a ser gasto, y el flujo por
    cuenta deja la tarjeta en cero y el banco en números rojos.
    """
    compra_id = servicio.registrar(
        fecha=date(2026, 8, 5),
        tipo=TipoMovimiento.GASTO,
        monto=3_200.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=cuentas["tarjeta"],
        descripcion="Compra con tarjeta",
        fecha_pago=None,
    )
    assert servicio.total_por_pagar() == 3_200.0

    servicio.marcar_pagado(compra_id, date(2026, 9, 15))
    _traspaso(servicio, ids_catalogo, cuentas)

    df = servicio.buscar()
    flujo = servicio.flujo_por_cuenta().set_index("cuenta")

    assert servicio.total_por_pagar() == 0.0
    # El gasto se contó una sola vez, y sólo la compra fue gasto.
    assert df["gasto_real"].sum() == 3_200.0
    assert df["impacto_caja"].sum() == -3_200.0
    # La tarjeta se liquidó; el dinero salió del banco.
    assert flujo.loc["Tarjeta crédito", "flujo_neto"] == 0.0
    assert flujo.loc["Cuenta principal", "flujo_neto"] == -3_200.0


def test_el_origen_y_el_destino_no_pueden_coincidir(servicio, ids_catalogo, cuentas):
    """Un traspaso a la misma cuenta no mueve nada: es un error de captura."""
    with pytest.raises(MovimientoInvalidoError, match="misma cuenta"):
        servicio.registrar(
            fecha=date(2026, 9, 15),
            tipo=TipoMovimiento.TRANSFERENCIA,
            monto=100.0,
            categoria_id=ids_catalogo["vivienda"],
            cuenta_id=cuentas["banco"],
            cuenta_destino_id=cuentas["banco"],
        )


def test_solo_una_transferencia_admite_cuenta_destino(servicio, ids_catalogo, cuentas):
    """Un gasto sale de una cuenta y no entra a ninguna otra."""
    with pytest.raises(MovimientoInvalidoError, match="sólo aplica"):
        servicio.registrar(
            fecha=date(2026, 9, 15),
            tipo=TipoMovimiento.GASTO,
            monto=100.0,
            categoria_id=ids_catalogo["vivienda"],
            cuenta_id=cuentas["banco"],
            cuenta_destino_id=cuentas["tarjeta"],
        )


def test_un_traspaso_sin_destino_sigue_siendo_valido(servicio, ids_catalogo, cuentas):
    """El destino es opcional: no todo traspaso capturado sabe a dónde fue."""
    servicio.registrar(
        fecha=date(2026, 9, 15),
        tipo=TipoMovimiento.TRANSFERENCIA,
        monto=500.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=cuentas["banco"],
        descripcion="Traspaso sin destino",
    )

    flujo = servicio.flujo_por_cuenta().set_index("cuenta")

    # Sin destino sólo hay pata de origen, así que el flujo no cuadra a cero.
    assert flujo.loc["Cuenta principal", "flujo_neto"] == -500.0
    assert "Tarjeta crédito" not in flujo.index


def test_un_traspaso_devengado_no_mueve_ninguna_cuenta(servicio, ids_catalogo, cuentas):
    """El flujo por cuenta sólo cuenta lo que ya se pagó."""
    servicio.registrar(
        fecha=date(2026, 9, 15),
        tipo=TipoMovimiento.TRANSFERENCIA,
        monto=3_200.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=cuentas["banco"],
        cuenta_destino_id=cuentas["tarjeta"],
        fecha_pago=None,
    )

    assert servicio.flujo_por_cuenta().empty


def test_editar_un_traspaso_conserva_su_destino(servicio, ids_catalogo, cuentas):
    """Cambiar el monto no debe desligar las dos cuentas."""
    traspaso_id = _traspaso(servicio, ids_catalogo, cuentas)

    servicio.actualizar(traspaso_id, monto=4_000.0)
    flujo = servicio.flujo_por_cuenta().set_index("cuenta")

    assert flujo.loc["Tarjeta crédito", "flujo_neto"] == 4_000.0
    assert flujo.loc["Cuenta principal", "flujo_neto"] == -4_000.0


# ═══════════════════════════════════════════════════════════
# Lugar y hora
# ═══════════════════════════════════════════════════════════


def test_un_movimiento_guarda_lugar_y_hora(servicio, ids_catalogo):
    """Los dos campos opcionales viajan enteros hasta la base y de vuelta."""
    from datetime import time as hora_tipo

    servicio.registrar(
        fecha=date(2026, 9, 17),
        tipo=TipoMovimiento.GASTO,
        monto=180.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
        lugar="Walmart Universidad",
        hora=hora_tipo(19, 30),
    )
    guardado = servicio.buscar().iloc[0]

    assert guardado["lugar"] == "Walmart Universidad"
    assert guardado["hora"] == "19:30"


def test_editar_conserva_lugar_y_hora(servicio, ids_catalogo):
    """Cambiar el monto no debe borrar dónde ni a qué hora fue."""
    from datetime import time as hora_tipo

    movimiento_id = servicio.registrar(
        fecha=date(2026, 9, 17),
        tipo=TipoMovimiento.GASTO,
        monto=180.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
        lugar="Oxxo",
        hora=hora_tipo(8, 15),
    )

    servicio.actualizar(movimiento_id, monto=200.0)
    guardado = servicio.buscar().iloc[0]

    assert guardado["lugar"] == "Oxxo"
    assert guardado["hora"] == "08:15"


def test_los_lugares_se_ofrecen_por_frecuencia(servicio, ids_catalogo):
    """El súper de siempre aparece primero en el autocompletado."""
    for lugar in ("Oxxo", "Walmart", "Walmart", "Walmart", "Oxxo"):
        servicio.registrar(
            fecha=date(2026, 9, 17),
            tipo=TipoMovimiento.GASTO,
            monto=50.0,
            categoria_id=ids_catalogo["vivienda"],
            cuenta_id=ids_catalogo["cuenta"],
            lugar=lugar,
        )

    assert servicio.lugares() == ["Walmart", "Oxxo"]


def test_la_busqueda_de_texto_tambien_mira_el_lugar(servicio, ids_catalogo):
    """Si lo que recuerdas es dónde fue, buscarlo debe encontrarlo."""
    servicio.registrar(
        fecha=date(2026, 9, 17),
        tipo=TipoMovimiento.GASTO,
        monto=50.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
        descripcion="Café",
        lugar="Starbucks Perisur",
    )

    assert len(servicio.buscar(texto="Perisur")) == 1
