from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from finanzas.application.services.movimientos_service import MovimientosService
from finanzas.application.services.pagos_service import PagosService
from finanzas.application.services.patrimonio_service import PatrimonioService
from finanzas.application.services.suscripciones_service import SuscripcionesService
from finanzas.data.repositories.catalogos_repository import CatalogosRepository
from finanzas.data.repositories.ingresos_fijos_repository import (
    IngresosFijosRepository,
)
from finanzas.data.repositories.movimientos_repository import MovimientosRepository
from finanzas.data.repositories.patrimonio_repository import PatrimonioRepository
from finanzas.data.repositories.plan_repository import PlanRepository
from finanzas.data.repositories.suscripciones_repository import SuscripcionesRepository
from finanzas.domain.enums import ClaseCargo, TipoMovimiento

# ═══════════════════════════════════════════════════════════
# El plan de pagos armado con lo que hay en la base: qué pagar
# primero, cómo quedan los meses hasta diciembre y con cuánto
# se termina el año.
# ═══════════════════════════════════════════════════════════

HOY = date(2026, 9, 20)
SEPTIEMBRE = date(2026, 9, 1)


@pytest.fixture
def patrimonio(db_path: str) -> PatrimonioService:
    return PatrimonioService(PatrimonioRepository(db_path))


@pytest.fixture
def pagos(db_path, patrimonio) -> PagosService:
    return PagosService(
        MovimientosRepository(db_path),
        SuscripcionesRepository(db_path),
        patrimonio,
        IngresosFijosRepository(db_path),
        PlanRepository(db_path),
    )


@pytest.fixture
def escenario(db_path, pagos, patrimonio, ids_catalogo):
    """
    El caso que motivó el plan: no alcanza para todo.

    Hay 6,000 en la cuenta; la renta de 3,000 no se puede posponer, la
    tarjeta debe 4,000 al 65 % y un adeudo de 4,000 se puede dejar hasta
    diciembre. Entran 7,000 de nómina cada mes.
    """
    catalogos = CatalogosRepository(db_path)
    adeudo = catalogos.crear_cuenta("Adeudo escuela", "Préstamo")
    patrimonio.verificar_saldo(ids_catalogo["cuenta"], date(2026, 9, 1), 6_000.0)
    patrimonio.verificar_saldo(ids_catalogo["tarjeta"], date(2026, 9, 1), 4_000.0)
    patrimonio.verificar_saldo(adeudo, date(2026, 9, 1), 20_000.0)
    patrimonio.fijar_reglas_deuda(ids_catalogo["tarjeta"], tasa_anual=0.65)
    patrimonio.fijar_reglas_deuda(adeudo, posponer_hasta=date(2026, 12, 31))
    patrimonio.fijar_exigible(adeudo, 4_000.0, declarado_el=date(2026, 9, 1))

    SuscripcionesService(SuscripcionesRepository(db_path), catalogos).crear(
        servicio="Renta",
        costo_por_cobro=3_000.0,
        categoria_id=ids_catalogo["vivienda"],
        proximo_cobro=date(2026, 9, 25),
        clase=ClaseCargo.GASTO_FIJO,
    )
    pagos.crear_ingreso_fijo(
        concepto="Nómina",
        monto=7_000.0,
        dia=30,
        categoria_id=ids_catalogo["sueldo"],
        cuenta_id=ids_catalogo["cuenta"],
        texto="NOMINA",
    )
    return {"adeudo": adeudo, "tarjeta": ids_catalogo["tarjeta"]}


def _del_mes(plan_mes, clave_empieza):
    return next(
        r for r in plan_mes.resoluciones if r.obligacion.clave.startswith(clave_empieza)
    )


def test_el_primer_mes_trae_lo_mismo_que_por_pagar(pagos, escenario):
    septiembre = pagos.plan(SEPTIEMBRE, HOY)[0]

    assert sum(r.obligacion.monto for r in septiembre.resoluciones) == pytest.approx(
        pagos.resumen(SEPTIEMBRE, HOY).por_pagar
    )


def test_llega_hasta_diciembre(pagos, escenario):
    plan = pagos.plan(SEPTIEMBRE, HOY)

    assert [m.mes.month for m in plan] == [9, 10, 11, 12]


def test_renta_y_tarjeta_primero_el_adeudo_se_pospone(pagos, escenario):
    """
    Hay 6,000 y además la nómina del 30: 13,000.

    Renta 3,000 y tarjeta 4,000 se pagan; el adeudo, que se puede dejar
    hasta diciembre, va con lo que sobra.
    """
    septiembre = pagos.plan(SEPTIEMBRE, HOY)[0]

    assert _del_mes(septiembre, "fijo:").pagado == 3_000.0
    assert _del_mes(septiembre, "tarjeta:").pagado == 4_000.0
    assert [r.obligacion.clave.split(":")[0] for r in septiembre.resoluciones] == [
        "fijo",
        "tarjeta",
        "exigible",
    ]


def test_con_poco_dinero_el_adeudo_es_lo_que_se_pospone(
    pagos, patrimonio, escenario, ids_catalogo
):
    patrimonio.verificar_saldo(ids_catalogo["cuenta"], date(2026, 9, 1), 0.0)
    pagos.eliminar_ingreso_fijo(int(pagos.ingresos_fijos().iloc[0]["id"]))
    # 7,600 del mes: renta 3,000 y tarjeta 4,000; al adeudo le quedan 600.
    pagos.crear_ingreso_fijo(concepto="Nómina", monto=7_600.0, dia=30)

    septiembre = pagos.plan(SEPTIEMBRE, HOY)[0]

    assert _del_mes(septiembre, "tarjeta:").pagado == 4_000.0
    assert _del_mes(septiembre, "exigible:").pospuesto > 0


def test_decidir_a_mano_se_guarda_y_manda(pagos, escenario):
    clave = f"tarjeta:{escenario['tarjeta']}"
    pagos.decidir(SEPTIEMBRE, clave, "posponer")

    septiembre = pagos.plan(SEPTIEMBRE, HOY)[0]
    tarjeta = _del_mes(septiembre, "tarjeta:")

    assert tarjeta.pospuesto == 4_000.0
    assert tarjeta.manual

    pagos.decidir(SEPTIEMBRE, clave, None)
    assert not _del_mes(pagos.plan(SEPTIEMBRE, HOY)[0], "tarjeta:").manual


def test_un_pago_parcial_necesita_monto(pagos, escenario):
    with pytest.raises(ValueError, match="cuánto"):
        pagos.decidir(SEPTIEMBRE, "tarjeta:1", "parcial")


def test_posponer_la_tarjeta_cuesta_intereses_en_octubre(pagos, escenario):
    pagos.decidir(SEPTIEMBRE, f"tarjeta:{escenario['tarjeta']}", "posponer")

    _, octubre, *_ = pagos.plan(SEPTIEMBRE, HOY)

    # 65 % al año sobre 4,000, un mes.
    assert octubre.intereses == pytest.approx(4_000.0 * 0.65 / 12, abs=0.01)


def test_las_parcialidades_futuras_no_pasan_de_la_deuda(
    pagos, patrimonio, db_path, ids_catalogo
):
    prestamo = CatalogosRepository(db_path).crear_cuenta("Préstamo corto", "Préstamo")
    patrimonio.verificar_saldo(prestamo, date(2026, 9, 1), 2_500.0)
    patrimonio.fijar_calendario_deuda(prestamo, dia_pago=10, pago_mensual=1_000.0)

    plan = pagos.plan(SEPTIEMBRE, HOY)
    parcialidades = [
        r.obligacion.monto
        for mes in plan[1:]
        for r in mes.resoluciones
        if r.obligacion.clave.startswith("parcialidad:") and not r.obligacion.atrasada
    ]

    # Septiembre ya pide 2,000 —la parcialidad del 10, vencida, y la del
    # 10 de octubre—: de la deuda quedan 500 para los meses siguientes.
    assert parcialidades == [500.0]


# ── El calendario y el cierre del año ────────────────────


def test_el_calendario_termina_en_lo_mismo_que_el_plan(pagos, escenario):
    plan = pagos.plan(SEPTIEMBRE, HOY)
    flujo = pagos.flujo_del_plan(plan, HOY)

    assert flujo["saldo"].iloc[-1] == pytest.approx(plan[-1].queda, abs=0.05)
    assert flujo["fecha"].is_monotonic_increasing


def test_cada_dia_dice_cuanto_entra_y_cuanto_sale(pagos, escenario):
    flujo = pagos.flujo_del_plan(pagos.plan(SEPTIEMBRE, HOY), HOY)
    rentas = flujo[flujo["concepto"] == "Renta"]

    assert rentas["fecha"].tolist() == [
        date(2026, 9, 25),
        date(2026, 10, 25),
        date(2026, 11, 25),
        date(2026, 12, 25),
    ]
    assert (rentas["sale"] == 3_000.0).all()
    nominas = flujo[flujo["concepto"] == "Nómina"]
    assert nominas["fecha"].tolist() == [
        date(2026, 9, 30),
        date(2026, 10, 30),
        date(2026, 11, 30),
        date(2026, 12, 30),
    ]
    assert (nominas["entra"] == 7_000.0).all()


def test_con_cuanto_se_termina_el_ano(pagos, escenario):
    plan = pagos.plan(SEPTIEMBRE, HOY)
    cierre = pagos.cierre_del_plan(plan)

    assert cierre["efectivo"] == plan[-1].queda
    assert cierre["neto"] == pytest.approx(cierre["efectivo"] - cierre["deudas"])


def test_sin_nada_que_planear_el_calendario_queda_vacio(pagos):
    flujo = pagos.flujo_del_plan(pagos.plan(SEPTIEMBRE, HOY), HOY)

    assert flujo.empty


# ── Un gasto fijo que se puede posponer hasta una fecha ──


@pytest.fixture
def parcialidad_fija(db_path, ids_catalogo):
    """El adeudo como gasto fijo: 4,000 cada día 30, posponible hasta diciembre."""
    cargos = SuscripcionesService(
        SuscripcionesRepository(db_path), CatalogosRepository(db_path)
    )
    return cargos, cargos.crear(
        servicio="Pago adeudo",
        costo_por_cobro=4_000.0,
        categoria_id=ids_catalogo["vivienda"],
        proximo_cobro=date(2026, 9, 30),
        clase=ClaseCargo.GASTO_FIJO,
        posponible=True,
        posponer_hasta=date(2026, 12, 31),
    )


def test_el_tope_de_un_gasto_fijo_se_guarda(parcialidad_fija):
    cargos, cargo = parcialidad_fija

    fila = cargos.listar().set_index("id").loc[cargo]

    assert fila["posponible"]
    assert fila["posponer_hasta"] == date(2026, 12, 31)


def test_si_deja_de_ser_posponible_pierde_su_tope(parcialidad_fija):
    cargos, cargo = parcialidad_fija

    cargos.actualizar(cargo, posponible=False)

    assert pd.isna(cargos.listar().set_index("id").loc[cargo, "posponer_hasta"])


def test_se_pospone_hasta_diciembre_y_ahi_se_paga_todo(pagos, parcialidad_fija):
    """
    Sin dinero: la de cada mes se pospone al siguiente, donde ya toca
    pagarla; como no alcanza, lo que falta se vuelve deuda nueva. En
    diciembre, su tope, se junta todo: las cuatro, 16,000.
    """
    plan = pagos.plan(SEPTIEMBRE, HOY)
    diciembre = plan[-1]

    assert [m.pospuesto for m in plan[:3]] == [4_000.0, 4_000.0, 4_000.0]
    assert [m.falta for m in plan[:3]] == [0.0, 4_000.0, 8_000.0]
    assert diciembre.obligaciones == 16_000.0
    assert diciembre.falta == 16_000.0
    assert any(
        r.obligacion.concepto == "Faltante de noviembre" for r in diciembre.resoluciones
    )


def test_lo_vencido_que_se_puede_posponer_sigue_pudiendo(pagos, parcialidad_fija):
    """Pasado el día 30, la de septiembre sigue pudiendo esperar a diciembre."""
    octubre = pagos.plan(date(2026, 10, 1), date(2026, 10, 5))[0]
    adeudo = [r for r in octubre.resoluciones if r.obligacion.concepto == "Pago adeudo"]

    assert adeudo
    assert all(not r.obligacion.atrasada for r in adeudo)
    assert all(r.falta == 0 and r.pospuesto > 0 for r in adeudo)


# ── Un adeudo que se va acumulando hasta diciembre ───────


@pytest.fixture
def adeudo_acumulado(db_path, patrimonio, ids_catalogo):
    """
    El caso real: 4,000 cada día 30, dos meses ya exigibles al 30/09 y
    se puede dejar todo para pagarlo en diciembre.
    """
    adeudo = CatalogosRepository(db_path).crear_cuenta("Adeudo", "Préstamo")
    patrimonio.verificar_saldo(adeudo, date(2026, 9, 1), 57_978.45)
    patrimonio.fijar_calendario_deuda(adeudo, dia_pago=30, pago_mensual=4_000.0)
    patrimonio.fijar_reglas_deuda(adeudo, posponer_hasta=date(2026, 12, 31))
    patrimonio.fijar_exigible(
        adeudo,
        8_000.0,
        fecha_limite=date(2026, 9, 30),
        declarado_el=date(2026, 9, 30),
        nota="Agosto y septiembre",
    )
    return adeudo


def _exigible(patrimonio, cuenta, hoy):
    return patrimonio.exigibles(hoy).set_index("cuenta_id").loc[cuenta]


def test_hoy_son_los_dos_meses_capturados(patrimonio, adeudo_acumulado):
    fila = _exigible(patrimonio, adeudo_acumulado, date(2026, 9, 30))

    assert fila["exigible"] == 8_000.0
    assert fila["fecha_limite"] == date(2026, 9, 30)


def test_en_octubre_se_suma_la_de_octubre(patrimonio, adeudo_acumulado):
    fila = _exigible(patrimonio, adeudo_acumulado, date(2026, 10, 5))

    assert fila["exigible"] == 12_000.0
    assert fila["fecha_limite"] == date(2026, 10, 30)
    # Lo de agosto y septiembre ya venció.
    assert fila["estado"] == "Vencido"


def test_los_abonos_lo_descuentan(patrimonio, adeudo_acumulado, db_path, ids_catalogo):
    MovimientosService(MovimientosRepository(db_path)).registrar(
        fecha=date(2026, 10, 15),
        tipo=TipoMovimiento.TRANSFERENCIA,
        monto=3_000.0,
        categoria_id=ids_catalogo["ahorro"],
        cuenta_id=ids_catalogo["cuenta"],
        cuenta_destino_id=adeudo_acumulado,
    )

    fila = _exigible(patrimonio, adeudo_acumulado, date(2026, 11, 5))

    assert fila["exigible"] == 16_000.0
    assert fila["abonado"] == 3_000.0
    assert fila["pendiente"] == 13_000.0


def _del_adeudo(mes_plan, cuenta):
    return [
        r
        for r in mes_plan.resoluciones
        if r.obligacion.clave.split(":")[1] == str(cuenta)
        and r.obligacion.clave.split(":")[0] in ("exigible", "parcialidad")
    ]


def test_pospuesto_todo_se_junta_en_diciembre(pagos, adeudo_acumulado):
    """Sin dinero: se pospone mes con mes y en diciembre son 20,000."""
    plan = pagos.plan(SEPTIEMBRE, date(2026, 9, 30))

    # Lo del adeudo y los faltantes que lo fueron cubriendo.
    assert plan[-1].obligaciones == 20_000.0
    assert plan[-1].falta == 20_000.0
    # Lo pospuesto en un mes llega al siguiente para pagarse: sin dinero,
    # falta; sólo lo nuevo de cada mes se pospone solo.
    assert [m.falta for m in plan[:3]] == [0.0, 8_000.0, 12_000.0]


def test_desde_octubre_no_se_pide_dos_veces_la_de_octubre(pagos, adeudo_acumulado):
    plan = pagos.plan(date(2026, 10, 1), date(2026, 10, 5))
    claves = [r.obligacion.clave for m in plan for r in m.resoluciones]

    assert f"parcialidad:{adeudo_acumulado}:2026-10" not in claves
    octubre = _del_adeudo(plan[0], adeudo_acumulado)
    assert sum(r.obligacion.monto for r in octubre) == 12_000.0
    assert octubre[0].obligacion.concepto == "Pago de Adeudo (Agosto y septiembre)"


# ── Retiros simulados del fondo de ahorro ────────────────


@pytest.fixture
def fondo(db_path, patrimonio):
    """Un fondo de ahorro restringido con 10,000."""
    catalogos = CatalogosRepository(db_path)
    cuenta = catalogos.crear_cuenta("Fondo", "Ahorro")
    catalogos.actualizar_cuenta(cuenta, "Fondo", "Ahorro", "", True, True)
    patrimonio.verificar_saldo(cuenta, date(2026, 9, 1), 10_000.0)
    return cuenta


@pytest.mark.parametrize(
    ("hoy", "mes", "se_puede"),
    [
        (date(2026, 9, 20), date(2026, 9, 1), True),
        (date(2026, 9, 21), date(2026, 9, 1), True),
        (date(2026, 9, 22), date(2026, 9, 1), False),
        (date(2026, 9, 30), date(2026, 10, 1), True),
        (date(2026, 9, 30), date(2026, 8, 1), False),
    ],
)
def test_el_retiro_se_pide_antes_del_22(pagos, hoy, mes, se_puede):
    assert pagos.puede_pedirse_retiro(mes, hoy) is se_puede


def test_el_retiro_entra_el_30_del_mes(pagos, fondo):
    octubre = date(2026, 10, 1)
    pagos.fijar_retiro(octubre, fondo, 3_000.0, date(2026, 9, 30))

    plan = pagos.plan(SEPTIEMBRE, date(2026, 9, 30))
    flujo = pagos.flujo_del_plan(plan, date(2026, 9, 30))
    retiro = flujo[flujo["concepto"].str.startswith("Retiro de Fondo")]

    assert plan[1].ingresos == 3_000.0
    assert retiro["fecha"].tolist() == [date(2026, 10, 30)]
    assert retiro["entra"].tolist() == [3_000.0]
    assert flujo["saldo"].iloc[-1] == pytest.approx(plan[-1].queda, abs=0.05)


def test_pasado_el_22_ya_no_se_puede_pedir(pagos, fondo):
    with pytest.raises(ValueError, match="22"):
        pagos.fijar_retiro(SEPTIEMBRE, fondo, 1_000.0, date(2026, 9, 25))


def test_entre_todos_los_meses_no_pasan_del_fondo(pagos, fondo):
    hoy = date(2026, 9, 30)
    pagos.fijar_retiro(date(2026, 10, 1), fondo, 6_000.0, hoy)

    with pytest.raises(ValueError, match="el fondo tendría 4,000.00"):
        pagos.fijar_retiro(date(2026, 11, 1), fondo, 5_000.0, hoy)
    pagos.fijar_retiro(date(2026, 11, 1), fondo, 4_000.0, hoy)


def test_solo_de_una_cuenta_restringida(pagos, fondo, ids_catalogo):
    with pytest.raises(ValueError, match="restringida"):
        pagos.fijar_retiro(date(2026, 10, 1), ids_catalogo["cuenta"], 100.0, HOY)


def test_cero_quita_el_retiro(pagos, fondo):
    octubre = date(2026, 10, 1)
    pagos.fijar_retiro(octubre, fondo, 3_000.0, HOY)
    pagos.fijar_retiro(octubre, fondo, 0.0, HOY)

    assert pagos.retiro_simulado(octubre, fondo) == 0.0


def test_lo_que_queda_en_el_fondo(pagos, fondo):
    hoy = date(2026, 9, 30)
    pagos.fijar_retiro(date(2026, 10, 1), fondo, 3_000.0, hoy)
    pagos.fijar_retiro(date(2026, 11, 1), fondo, 2_000.0, hoy)

    assert pagos.fondo_restante(hasta=date(2026, 10, 1), hoy=hoy) == 7_000.0
    assert pagos.fondo_restante(hasta=date(2026, 11, 1), hoy=hoy) == 5_000.0
    # En diciembre se liquida: el fondo queda en cero.
    assert pagos.fondo_restante(hoy=hoy) == 0.0


# ── El resumen de acciones del plan ──────────────────────


def test_el_resumen_junta_lo_que_se_decidio(pagos, escenario, fondo):
    hoy = date(2026, 9, 20)
    octubre = date(2026, 10, 1)
    pagos.decidir(SEPTIEMBRE, f"tarjeta:{escenario['tarjeta']}", "posponer")
    pagos.fijar_retiro(octubre, fondo, 1_500.0, hoy)

    acciones = pagos.acciones_del_plan(pagos.plan(SEPTIEMBRE, hoy))
    por_accion = acciones.set_index("accion")

    assert por_accion.loc["Posponer", "mes"] == SEPTIEMBRE
    assert por_accion.loc["Posponer", "detalle"] == "Se paga en octubre 2026"
    assert por_accion.loc["Retirar del fondo (simulado)", "monto"] == 1_500.0
    assert por_accion.loc["Retirar del fondo (simulado)", "detalle"] == "Entra el 30/10"


def test_el_faltante_aparece_como_deuda_nueva_en_el_resumen(pagos, parcialidad_fija):
    acciones = pagos.acciones_del_plan(pagos.plan(SEPTIEMBRE, HOY))
    deudas = acciones[acciones["accion"] == "Queda como deuda nueva"]

    assert deudas.iloc[0]["concepto"] == "Faltante de octubre"
    assert deudas.iloc[0]["monto"] == 4_000.0


def test_sin_decisiones_no_hay_acciones(pagos, escenario):
    assert pagos.acciones_del_plan(pagos.plan(SEPTIEMBRE, HOY)).empty


def test_el_resumen_dice_lo_que_se_decidio_aunque_no_alcance(
    pagos, patrimonio, escenario, ids_catalogo
):
    """Pagar una parte sin dinero sigue siendo «pagar una parte»."""
    patrimonio.verificar_saldo(ids_catalogo["cuenta"], date(2026, 9, 1), 0.0)
    pagos.eliminar_ingreso_fijo(int(pagos.ingresos_fijos().iloc[0]["id"]))
    pagos.decidir(SEPTIEMBRE, f"tarjeta:{escenario['tarjeta']}", "parcial", 1_500.0)

    acciones = pagos.acciones_del_plan(pagos.plan(SEPTIEMBRE, HOY))
    parte = acciones[acciones["accion"] == "Pagar una parte"].iloc[0]

    assert parte["monto"] == 1_500.0
    assert "no alcanzan" in parte["detalle"]


# ── La renta del 1 se paga con el mes anterior, también en el plan ──


@pytest.fixture
def renta_del_1(db_path, ids_catalogo):
    cargos = SuscripcionesService(
        SuscripcionesRepository(db_path), CatalogosRepository(db_path)
    )
    cargos.crear(
        servicio="Renta",
        costo_por_cobro=3_000.0,
        categoria_id=ids_catalogo["vivienda"],
        proximo_cobro=date(2026, 10, 1),
        clase=ClaseCargo.GASTO_FIJO,
    )


def test_cada_mes_del_plan_paga_la_renta_del_siguiente(
    pagos, renta_del_1, db_path, ids_catalogo
):
    # La de septiembre ya se pagó; aquí sólo importan las que vienen.
    MovimientosService(MovimientosRepository(db_path)).registrar(
        fecha=date(2026, 9, 1),
        tipo=TipoMovimiento.GASTO,
        monto=3_000.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
        descripcion="Renta",
    )
    plan = pagos.plan(SEPTIEMBRE, date(2026, 9, 30))
    rentas = {
        m.mes.month: [
            r.obligacion.vence
            for r in m.resoluciones
            if r.obligacion.concepto == "Renta"
        ]
        for m in plan
    }

    assert rentas == {
        9: [date(2026, 10, 1)],
        10: [date(2026, 11, 1)],
        11: [date(2026, 12, 1)],
        12: [date(2027, 1, 1)],
    }


# ── Ingresos y aportaciones simulados ────────────────────


def test_la_aportacion_hace_crecer_el_fondo(pagos, fondo):
    hoy = date(2026, 9, 30)
    pagos.crear_simulado("aportacion", "Aportación ITAM", 2_491.58, 30, cuenta_id=fondo)

    # La de hoy, 30 de septiembre, ya pasó: está registrada o no, de verdad.
    assert pagos.fondo_restante(hasta=SEPTIEMBRE, hoy=hoy) == 10_000.0
    assert pagos.fondo_restante(hasta=date(2026, 11, 1), hoy=hoy) == pytest.approx(
        10_000.0 + 2 * 2_491.58
    )


def test_con_las_aportaciones_se_puede_retirar_mas(pagos, fondo):
    hoy = date(2026, 9, 30)
    pagos.crear_simulado("aportacion", "Aportación ITAM", 2_491.58, 30, cuenta_id=fondo)
    pagos.fijar_retiro(date(2026, 10, 1), fondo, 10_000.0, hoy)

    # En noviembre ya está la aportación de octubre, no la de noviembre.
    pagos.fijar_retiro(date(2026, 11, 1), fondo, 2_491.58, hoy)
    with pytest.raises(ValueError, match="noviembre"):
        pagos.fijar_retiro(date(2026, 11, 1), fondo, 3_000.0, hoy)


def test_la_aportacion_no_es_dinero_para_pagar(pagos, fondo):
    hoy = date(2026, 9, 30)
    antes = pagos.plan(SEPTIEMBRE, hoy)[1].ingresos
    pagos.crear_simulado("aportacion", "Aportación ITAM", 2_491.58, 30, cuenta_id=fondo)

    assert pagos.plan(SEPTIEMBRE, hoy)[1].ingresos == antes


def test_el_aguinaldo_entra_en_diciembre(pagos):
    hoy = date(2026, 9, 30)
    pagos.crear_simulado("ingreso", "Aguinaldo", 7_300.0, 20, mes=date(2026, 12, 1))

    plan = pagos.plan(SEPTIEMBRE, hoy)
    flujo = pagos.flujo_del_plan(plan, hoy)
    aguinaldo = flujo[flujo["concepto"] == "Aguinaldo (simulado)"]

    assert [m.ingresos for m in plan] == [0.0, 0.0, 0.0, 7_300.0]
    assert aguinaldo["fecha"].tolist() == [date(2026, 12, 20)]
    assert flujo["saldo"].iloc[-1] == pytest.approx(plan[-1].queda, abs=0.05)


def test_lo_simulado_aparece_en_las_acciones(pagos, fondo):
    hoy = date(2026, 9, 30)
    pagos.crear_simulado("ingreso", "Aguinaldo", 7_300.0, 20, mes=date(2026, 12, 1))
    pagos.crear_simulado("aportacion", "Aportación ITAM", 2_491.58, 30, cuenta_id=fondo)

    acciones = pagos.acciones_del_plan(pagos.plan(SEPTIEMBRE, hoy))

    assert (acciones["accion"] == "Aportación simulada al fondo").sum() == 3
    assert acciones[acciones["accion"] == "Ingreso simulado"]["concepto"].tolist() == [
        "Aguinaldo"
    ]


@pytest.mark.parametrize(
    ("campos", "mensaje"),
    [
        ({"tipo": "otro"}, "ingreso o una aportación"),
        ({"concepto": " "}, "concepto"),
        ({"monto": 0.0}, "positivo"),
        ({"dia": 32}, "del 1 al 31"),
        ({"tipo": "aportacion", "cuenta_id": None}, "restringida"),
    ],
)
def test_un_simulado_se_valida(pagos, campos, mensaje):
    datos = {"tipo": "ingreso", "concepto": "Algo", "monto": 100.0, "dia": 15} | campos
    with pytest.raises(ValueError, match=mensaje):
        pagos.crear_simulado(**datos)


def test_quitar_un_simulado(pagos):
    simulado = pagos.crear_simulado("ingreso", "Aguinaldo", 7_300.0, 20)
    pagos.eliminar_simulado(simulado)

    assert pagos.simulados().empty


# ── La liquidación del fondo en diciembre ────────────────


def test_en_diciembre_el_fondo_entrega_lo_que_no_se_retiro(pagos, fondo):
    """10,000 de hoy, más tres aportaciones, menos 3,000 retirados en octubre."""
    hoy = date(2026, 9, 30)
    pagos.crear_simulado("aportacion", "Aportación ITAM", 2_491.58, 30, cuenta_id=fondo)
    pagos.fijar_retiro(date(2026, 10, 1), fondo, 3_000.0, hoy)

    liquidacion = pagos.liquidaciones_del_mes(date(2026, 12, 1), hoy)
    plan = pagos.plan(SEPTIEMBRE, hoy)
    flujo = pagos.flujo_del_plan(plan, hoy)
    evento = flujo[flujo["concepto"] == "Liquidación de Fondo (simulado)"]

    esperado = round(10_000.0 + 3 * 2_491.58 - 3_000.0, 2)
    assert liquidacion == [("Fondo", esperado)]
    assert plan[-1].ingresos == pytest.approx(esperado)
    assert evento["fecha"].tolist() == [date(2026, 12, 30)]
    assert flujo["saldo"].iloc[-1] == pytest.approx(plan[-1].queda, abs=0.05)
    assert pagos.fondo_restante(hoy=hoy) == 0.0


def test_la_liquidacion_solo_cae_en_diciembre(pagos, fondo):
    assert pagos.liquidaciones_del_mes(date(2026, 11, 1), date(2026, 9, 30)) == []


def test_despues_de_liquidar_ya_no_se_puede_retirar_lo_de_antes(pagos, fondo):
    """El plan puede llegar a enero: el fondo empieza de cero."""
    hoy = date(2026, 9, 30)
    with pytest.raises(ValueError, match="enero 2027"):
        pagos.fijar_retiro(date(2027, 1, 1), fondo, 1_000.0, hoy)


def test_la_liquidacion_aparece_en_las_acciones(pagos, fondo):
    hoy = date(2026, 9, 30)
    acciones = pagos.acciones_del_plan(pagos.plan(SEPTIEMBRE, hoy), hoy)

    liquidacion = acciones[acciones["accion"] == "Liquidación del fondo (simulado)"]
    assert liquidacion["monto"].tolist() == [10_000.0]


def test_desde_un_mes_que_ya_paso_la_renta_del_1_no_se_pierde(
    pagos, renta_del_1, db_path, ids_catalogo
):
    """El 1 de octubre, sin pagar aún, el plan desde septiembre la pide."""
    MovimientosService(MovimientosRepository(db_path)).registrar(
        fecha=date(2026, 9, 1),
        tipo=TipoMovimiento.GASTO,
        monto=3_000.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
        descripcion="Renta",
    )
    plan = pagos.plan(SEPTIEMBRE, date(2026, 10, 1))
    rentas = [
        r.obligacion.vence
        for m in plan
        for r in m.resoluciones
        if r.obligacion.concepto == "Renta"
    ]

    assert date(2026, 10, 1) in rentas
    assert rentas.count(date(2026, 10, 1)) == 1
