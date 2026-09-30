from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from finanzas.application.app.cifras_del_mes import ALTO_CIFRA, nombre_mes
from finanzas.application.app.components import (
    grafico_saldo_proyectado,
    moneda,
    obtener_servicios,
    reportar_error,
    tabla_equivalente,
)
from finanzas.application.app.theme import rotulo
from finanzas.application.services.pagos_service import (
    DIA_ABONO_RETIRO,
    DIA_LIMITE_RETIRO,
    DIA_LIQUIDACION_FONDO,
)
from finanzas.domain.plan import Decision, MesDelPlan, Resolucion, fin_de_mes

# ═══════════════════════════════════════════════════════════
# Plan de pagos: qué pagar este mes y cómo se ve el resto del año
#
# Lo primero en la pantalla es la decisión: una fila por cuenta, con
# pagar, pagar una parte o posponer. Cada cambio rehace el plan y dice
# a dónde fue a parar lo pospuesto. Nada de esto se registra: es para
# ver qué pasaría. Lo único que se guarda son esas decisiones, para
# no repetirlas.
# ═══════════════════════════════════════════════════════════

PAGAR = "Pagar"
PARTE = "Pagar una parte"
POSPONER = "Posponer"
DECISION_DE = {
    PAGAR: Decision.PAGAR,
    PARTE: Decision.PARCIAL,
    POSPONER: Decision.POSPONER,
}
ETIQUETA_DE = {decision: etiqueta for etiqueta, decision in DECISION_DE.items()}


def _estado_actual(resolucion: Resolucion) -> str:
    """Lo que el plan está haciendo hoy con un concepto, como opción del selector."""
    if resolucion.pagado <= 0 and resolucion.pospuesto > 0:
        return POSPONER
    if resolucion.pagado + 0.005 < resolucion.obligacion.monto:
        return PARTE
    return PAGAR


def _destino(resolucion: Resolucion, siguiente: str) -> str:
    """Qué pasa con un concepto este mes y, si queda algo, a dónde va."""
    partes = []
    if resolucion.pagado > 0:
        partes.append(f"Pagas {moneda(resolucion.pagado, decimales=2)}")
    if resolucion.pospuesto > 0:
        partes.append(
            f"pasan {moneda(resolucion.pospuesto, decimales=2)} a {siguiente}"
        )
    if resolucion.falta > 0:
        partes.append(
            f"no alcanza: faltan {moneda(resolucion.falta, decimales=2)}, que "
            f"quedan como deuda nueva a pagar en {siguiente}"
        )
    if not partes:
        return "Nada que pagar"
    texto = ", ".join(partes)
    return texto[0].upper() + texto[1:] + "."


def _clave_widget(prefijo: str, mes: date, clave: str) -> str:
    return f"{prefijo}_{mes:%Y-%m}_{clave}"


def _decidir(
    mes: date,
    resolucion: Resolucion,
    destino: str,
    decidido: tuple[str, float | None] | None,
) -> None:
    """
    Una fila: el concepto, su prioridad, el selector y lo que pasa después.

    El selector enseña lo que el usuario decidió, si decidió algo, y no
    lo que resulta de ello: pagar «una parte» de 4,000 por 4,000 sigue
    siendo lo que eligió, y deducirlo del pago lo devolvería a «Pagar» y
    le escondería el campo del monto.
    """
    servicios = obtener_servicios()
    obligacion = resolucion.obligacion
    se_puede = obligacion.puede_posponerse(fin_de_mes(mes))
    opciones = [PAGAR, PARTE] + ([POSPONER] if se_puede else [])
    if decidido is not None:
        actual = ETIQUETA_DE[Decision(decidido[0])]
    else:
        actual = _estado_actual(resolucion)
    if actual not in opciones:
        actual = PAGAR
    monto_decidido = (
        float(decidido[1])
        if decidido is not None and decidido[1] is not None
        else float(resolucion.pagado)
    )

    with st.container(border=True):
        columnas = st.columns([3, 3, 2])
        with columnas[0]:
            st.markdown(
                f"**{obligacion.concepto}** · {moneda(obligacion.monto, decimales=2)}"
            )
            detalles = [obligacion.origen]
            if obligacion.vence is not None:
                detalles.append(f"vence {obligacion.vence:%d/%m}")
            if obligacion.arrastrada:
                detalles.append("viene del mes anterior")
            detalles.append(
                "decidiste tú" if resolucion.manual else f"sugerido: {actual.lower()}"
            )
            st.caption(" · ".join(detalles))
        with columnas[1]:
            elegido = st.segmented_control(
                "Qué hacer",
                opciones,
                default=actual,
                key=_clave_widget("decision", mes, obligacion.clave),
                label_visibility="collapsed",
            )
            monto_parte = None
            if elegido == PARTE:
                monto_parte = st.number_input(
                    "Cuánto pagas ahora",
                    min_value=0.0,
                    max_value=float(obligacion.monto),
                    value=min(monto_decidido, float(obligacion.monto)),
                    step=100.0,
                    format="%.2f",
                    key=_clave_widget("parte", mes, obligacion.clave),
                )
        with columnas[2]:
            st.caption(resolucion.razon)
            if elegido == PARTE and resolucion.pagado + 0.005 < monto_decidido:
                st.caption(
                    f"Sólo alcanza para {moneda(resolucion.pagado, decimales=2)}."
                )
            st.markdown(destino)

    if elegido is None:
        return
    cambio_decision = elegido != actual
    cambio_monto = (
        elegido == PARTE
        and monto_parte is not None
        and abs(monto_parte - monto_decidido) > 0.005
    )
    if not (cambio_decision or cambio_monto):
        return

    try:
        servicios.pagos.decidir(
            mes,
            obligacion.clave,
            str(DECISION_DE[elegido]),
            monto_parte if elegido == PARTE else None,
        )
    except ValueError as error:
        reportar_error(error)
        return
    for prefijo in ("decision", "parte"):
        st.session_state.pop(_clave_widget(prefijo, mes, obligacion.clave), None)
    st.rerun()


def _simulados() -> None:
    """
    Los ingresos y aportaciones que sólo existen en la simulación.

    El aguinaldo de diciembre, la aportación de cada mes al fondo: cosas
    que todavía no pasan y que el plan da por hechas. Se dan de alta y se
    quitan aquí; nunca se registran como movimientos.
    """
    servicios = obtener_servicios()
    simulados = servicios.pagos.simulados()
    st.caption(
        "Un **ingreso** suma a lo que tienes para pagar ese mes, el día que "
        "digas. Una **aportación** entra al fondo de ahorro y hace que tenga "
        "más para retirar después; no es dinero para pagar. Sin mes, se repite "
        "cada mes. Nada de esto se registra."
    )
    if simulados.empty:
        st.caption("Todavía no hay nada simulado.")
    else:
        for fila in simulados.itertuples():
            columnas = st.columns([4, 1])
            with columnas[0]:
                cuando = (
                    f"el {fila.dia} de {nombre_mes(fila.mes)}"
                    if fila.mes is not None
                    else f"cada mes, el día {fila.dia}"
                )
                destino = f" · al {fila.cuenta}" if fila.cuenta else ""
                tipo = "Aportación" if fila.tipo == "aportacion" else "Ingreso"
                st.markdown(
                    f"**{fila.concepto}** · {moneda(fila.monto, decimales=2)} · "
                    f"{tipo} {cuando}{destino}"
                )
            with columnas[1]:
                if st.button(
                    "Quitar", icon=":material/delete:", key=f"quitar_sim_{fila.id}"
                ):
                    servicios.pagos.eliminar_simulado(int(fila.id))
                    st.rerun()

    fondos = servicios.pagos.fondos()
    with st.form("nuevo_simulado", clear_on_submit=True, border=False):
        fila_1 = st.columns([1, 2, 1, 1])
        with fila_1[0]:
            tipo = st.selectbox("Tipo", ["Ingreso", "Aportación al fondo"])
        with fila_1[1]:
            concepto = st.text_input("Concepto", placeholder="Aguinaldo")
        with fila_1[2]:
            monto = st.number_input("Monto", min_value=0.0, step=100.0, format="%.2f")
        with fila_1[3]:
            dia = st.number_input("Día", min_value=1, max_value=31, value=30)
        fila_2 = st.columns(2)
        with fila_2[0]:
            mes = st.date_input(
                "Mes (vacío: cada mes)",
                value=None,
                format="DD/MM/YYYY",
                help="Cualquier día de ese mes. Vacío: se repite cada mes.",
            )
        with fila_2[1]:
            opciones_fondo = {f.cuenta: int(f.cuenta_id) for f in fondos.itertuples()}
            fondo = (
                st.selectbox("Fondo", list(opciones_fondo)) if opciones_fondo else None
            )
        if st.form_submit_button("Agregar", icon=":material/add:"):
            try:
                servicios.pagos.crear_simulado(
                    "aportacion" if tipo.startswith("Aportación") else "ingreso",
                    concepto,
                    monto,
                    int(dia),
                    mes,
                    opciones_fondo.get(fondo) if fondo else None,
                )
            except ValueError as error:
                reportar_error(error)
            else:
                st.rerun()


def _retiro_simulado(mes: date, hoy: date, final: date) -> None:
    """
    El fondo de ahorro en el mes: su aportación y el retiro que se simula.

    Sólo existe en el plan. La aportación del mes entra al fondo el día
    que toca y no es dinero para pagar; el retiro suma a lo que entra ese
    mes, el día 30. Se pide antes del 22; pasado, el campo se bloquea.
    """
    servicios = obtener_servicios()
    fondos = servicios.pagos.fondos(hoy)
    if fondos.empty:
        return

    se_puede = servicios.pagos.puede_pedirse_retiro(mes, hoy)
    with st.container(border=True):
        st.markdown("**Fondo de ahorro este mes (simulado)**")
        st.caption(
            f"El retiro se pide antes del día {DIA_LIMITE_RETIRO} y entra el "
            f"{DIA_ABONO_RETIRO} de ese mes; sale de lo que el fondo tenga "
            "entonces. No registra nada: es para ver cómo se pagaría todo mes a mes."
        )
        aportaciones = servicios.pagos.simulados_del_mes(mes, "aportacion", hoy)
        for aportacion in aportaciones:
            st.caption(
                f"Entra al fondo: {aportacion['concepto']}, "
                f"{moneda(aportacion['monto'], decimales=2)} el "
                f"{aportacion['fecha']:%d/%m} (simulado)."
            )
        for cuenta, monto in servicios.pagos.liquidaciones_del_mes(mes, hoy):
            st.success(
                f"En diciembre se liquida {cuenta}: lo que no retiraste, "
                f"{moneda(monto, decimales=2)}, entra como ingreso el "
                f"{min(DIA_LIQUIDACION_FONDO, fin_de_mes(mes).day)}/{mes:%m} y el "
                "fondo queda en cero (simulado).",
                icon=":material/savings:",
            )
        for fondo in fondos.itertuples():
            actual = servicios.pagos.retiro_simulado(mes, int(fondo.cuenta_id))
            columnas = st.columns([2, 1, 2])
            with columnas[0]:
                monto = st.number_input(
                    f"Retirar de {fondo.cuenta}",
                    min_value=0.0,
                    value=float(actual),
                    step=500.0,
                    format="%.2f",
                    disabled=not se_puede,
                    key=f"retiro_{mes:%Y-%m}_{fondo.cuenta_id}",
                    help=(
                        f"Hoy tiene {moneda(fondo.saldo, decimales=2)}; con las "
                        "aportaciones simuladas va creciendo mes a mes."
                    ),
                )
            with columnas[1]:
                st.markdown("&nbsp;")
                if st.button(
                    "Aplicar",
                    icon=":material/savings:",
                    disabled=not se_puede or round(monto, 2) == round(actual, 2),
                    key=f"aplicar_retiro_{mes:%Y-%m}_{fondo.cuenta_id}",
                ):
                    try:
                        servicios.pagos.fijar_retiro(
                            mes, int(fondo.cuenta_id), monto, hoy
                        )
                    except ValueError as error:
                        reportar_error(error)
                    else:
                        st.rerun()
            with columnas[2]:
                if not se_puede:
                    st.caption(
                        f"Ya pasó el {DIA_LIMITE_RETIRO}: este mes no se puede "
                        "pedir. Elige un mes siguiente."
                        if not actual
                        else f"Pedido a tiempo: entran {moneda(actual)} el "
                        f"{DIA_ABONO_RETIRO}."
                    )
                elif actual:
                    st.caption(
                        f"Entran {moneda(actual, decimales=2)} el "
                        f"{min(DIA_ABONO_RETIRO, fin_de_mes(mes).day)}/"
                        f"{mes:%m}."
                    )
        restante = servicios.pagos.fondo_restante(hasta=mes, hoy=hoy)
        total = servicios.pagos.fondo_restante(hasta=final, hoy=hoy)
        st.metric(
            "Queda en el fondo al cerrar este mes",
            moneda(restante, decimales=2),
            delta=f"{moneda(total, decimales=2)} al cierre de {nombre_mes(final)}",
            delta_color="off",
            delta_arrow="off",
            help="Lo de hoy, más las aportaciones simuladas, menos los retiros.",
        )


def dibujar_plan(mes: date, hoy: date, plan: list[MesDelPlan] | None = None) -> None:
    """La pestaña del plan: decidir, ver las acciones y cómo termina el año."""
    servicios = obtener_servicios()
    if plan is None:
        plan = servicios.pagos.plan(mes, hoy)
    if not plan:
        st.info("No hay nada que planear todavía.", icon=":material/info:")
        return

    flujo = servicios.pagos.flujo_del_plan(plan, hoy)
    cierre = servicios.pagos.cierre_del_plan(plan)
    final = nombre_mes(plan[-1].mes)

    # ── 1. Decidir, mes por mes ─────────────────────────
    rotulo("Decide qué pagar cada mes")
    st.caption(
        "Elige un mes y decide en cada cuenta si la pagas, pagas una parte o la "
        "pospones. Posponer es dejarlo para el mes siguiente: ahí aparece como "
        "algo que toca pagar, y si quieres volver a posponerlo lo decides en ese "
        "mes. Viene con una sugerencia —primero lo que no se puede posponer, "
        "luego las tarjetas, al final lo que puede esperar— y cada cambio "
        "recalcula todo lo que sigue. Es sólo un plan: no registra ningún pago."
    )

    with st.expander(
        "Lo que la simulación supone: ingresos y aportaciones",
        icon=":material/science:",
    ):
        _simulados()

    nombres = [nombre_mes(m.mes) for m in plan]
    elegido_nombre = (
        st.segmented_control(
            "Mes",
            nombres,
            default=nombres[0],
            key=f"plan_mes_{mes:%Y-%m}",
        )
        or nombres[0]
    )
    indice = nombres.index(elegido_nombre)
    del_mes = plan[indice]
    siguiente = nombres[indice + 1] if indice + 1 < len(nombres) else "después"

    _retiro_simulado(del_mes.mes, hoy, plan[-1].mes)

    # Con qué se cuenta ese mes, para decidir sabiendo cuánto hay.
    tarjetas = st.columns(4)
    with tarjetas[0], st.container(border=True):
        st.metric(
            "Tienes para pagar",
            moneda(del_mes.disponible),
            delta=(
                f"{moneda(del_mes.efectivo_inicial)} + "
                f"{moneda(del_mes.ingresos)} que entran"
            ),
            help=(
                "Con lo que empiezas el mes más lo que entra: tus ingresos fijos "
                "y lo simulado —un retiro del fondo, el aguinaldo, la "
                "liquidación del fondo en diciembre—."
            ),
            delta_color="off",
            delta_arrow="off",
            height=ALTO_CIFRA,
        )
    # «Vas a pagar» es lo que se decidió o toca pagar, alcance o no: si
    # fuera sólo lo que alcanza, nunca pasaría de lo que tienes y el
    # faltante quedaría escondido.
    with tarjetas[1], st.container(border=True):
        st.metric(
            "Vas a pagar",
            moneda(del_mes.a_pagar),
            delta="lo que decidiste y lo que no se pospone",
            delta_color="off",
            delta_arrow="off",
            height=ALTO_CIFRA,
            help=(
                "Todo lo que en este mes pagas o toca pagar. No cuenta lo que pospones."
            ),
        )
    with tarjetas[2], st.container(border=True):
        alcanza = del_mes.balance >= 0
        st.metric(
            "Te queda" if alcanza else "Te falta",
            moneda(del_mes.balance),
            delta="alcanza" if alcanza else "no alcanza",
            delta_color="normal" if alcanza else "inverse",
            delta_arrow="off",
            height=ALTO_CIFRA,
            help="Lo que tienes menos lo que vas a pagar.",
        )
    with tarjetas[3], st.container(border=True):
        faltante_que_pasa = del_mes.falta
        st.metric(
            f"Pospones a {siguiente}",
            moneda(del_mes.pospuesto),
            delta=(
                f"y pasan {moneda(faltante_que_pasa)} que no alcanzaron"
                if faltante_que_pasa
                else "a pagar ese mes"
            ),
            delta_color="off",
            delta_arrow="off",
            height=ALTO_CIFRA,
        )

    filas = del_mes.resoluciones

    decisiones = servicios.pagos.decisiones(del_mes.mes)
    if not filas:
        st.success(
            f"No hay nada por pagar en {elegido_nombre}.",
            icon=":material/check_circle:",
        )
    for resolucion in filas:
        _decidir(
            del_mes.mes,
            resolucion,
            _destino(resolucion, siguiente),
            decisiones.get(resolucion.obligacion.clave),
        )

    if decisiones:
        st.caption("Las filas que dicen «decidiste tú» ya no siguen la sugerencia.")
    if decisiones and st.button(
        f"Volver {elegido_nombre} a lo sugerido", icon=":material/restart_alt:"
    ):
        servicios.pagos.olvidar_decisiones(del_mes.mes)
        for resolucion in filas:
            for prefijo in ("decision", "parte"):
                st.session_state.pop(
                    _clave_widget(prefijo, del_mes.mes, resolucion.obligacion.clave),
                    None,
                )
        st.rerun()

    # ── Tu plan, en acciones ─────────────────────────────
    rotulo("Tu plan, en acciones")
    acciones = servicios.pagos.acciones_del_plan(plan, hoy)
    with st.container(border=True):
        st.caption(
            "Lo que decidiste para armar este plan, mes por mes: los retiros que "
            "simulaste, lo que elegiste pagar o posponer y lo que no alcanzó y "
            "quedó como deuda nueva. Es una simulación: nada de esto está "
            "registrado."
        )
        if acciones.empty:
            st.caption("Todavía no tomas ninguna acción: todo sigue la sugerencia.")
        else:
            st.dataframe(
                acciones.assign(mes=acciones["mes"].map(nombre_mes)),
                hide_index=True,
                column_config={
                    "mes": st.column_config.TextColumn("Mes"),
                    "accion": st.column_config.TextColumn("Acción"),
                    "concepto": st.column_config.TextColumn("Concepto", width="medium"),
                    "monto": st.column_config.NumberColumn("Monto", format="$%.2f"),
                    "detalle": st.column_config.TextColumn("Detalle", width="medium"),
                },
            )

    # ── 2. Cómo termina el año ───────────────────────────
    rotulo(f"Con este plan, así terminas {final}")
    tarjetas = st.columns(5)
    with tarjetas[0], st.container(border=True):
        st.metric(
            f"Terminas {final} con",
            moneda(cierre["neto"]),
            delta="en positivo" if cierre["neto"] >= 0 else "en negativo",
            delta_color="normal" if cierre["neto"] >= 0 else "inverse",
            delta_arrow="off",
            height=ALTO_CIFRA,
            help="Efectivo al cierre menos lo que seguirías debiendo.",
        )
    with tarjetas[1], st.container(border=True):
        st.metric(
            "Efectivo al cierre",
            moneda(cierre["efectivo"]),
            delta=f"al {flujo['fecha'].max():%d/%m}" if not flujo.empty else "—",
            delta_color="off",
            delta_arrow="off",
            height=ALTO_CIFRA,
        )
    with tarjetas[2], st.container(border=True):
        st.metric(
            "Seguirías debiendo",
            moneda(cierre["deudas"]),
            delta="lo pospuesto y lo que no alcanzó",
            delta_color="off",
            delta_arrow="off",
            height=ALTO_CIFRA,
        )
    with tarjetas[3], st.container(border=True):
        st.metric(
            "Intereses por posponer",
            moneda(cierre["intereses"]),
            delta="en todo el plan",
            delta_color="off",
            delta_arrow="off",
            height=ALTO_CIFRA,
        )
    with tarjetas[4], st.container(border=True):
        st.metric(
            "Queda en el fondo",
            moneda(servicios.pagos.fondo_restante(hasta=plan[-1].mes, hoy=hoy)),
            delta="tras la liquidación de diciembre",
            delta_color="off",
            delta_arrow="off",
            height=ALTO_CIFRA,
            help=(
                "Lo que tiene hoy el fondo, más las aportaciones simuladas, menos "
                "lo que simulaste retirar."
            ),
        )

    en_rojo = flujo[flujo["saldo"] < 0] if not flujo.empty else flujo
    if not en_rojo.empty:
        dia = en_rojo.iloc[0]
        st.warning(
            f"El {dia['fecha']:%d/%m} tu saldo quedaría en "
            f"{moneda(float(dia['saldo']), decimales=2)}: ese día sale más de lo "
            "que ha entrado. Pospón algo o muévelo después de tu nómina.",
            icon=":material/trending_down:",
        )

    with st.container(border=True):
        st.subheader("Tu saldo, día por día")
        st.caption(
            "Sube el día que entra tu nómina y baja el día que pagas. Pasa el "
            "cursor para ver qué se mueve cada día."
        )
        if flujo.empty:
            st.caption("Sin movimientos proyectados.")
        else:
            st.altair_chart(grafico_saldo_proyectado(flujo))
            tabla_equivalente(
                flujo.rename(
                    columns={
                        "fecha": "Fecha",
                        "concepto": "Concepto",
                        "origen": "De dónde",
                        "entra": "Entra",
                        "sale": "Sale",
                        "saldo": "Saldo",
                    }
                ),
                etiqueta="Ver el calendario completo: qué día entra y sale cuánto",
            )

    st.caption(
        "El plan sólo cuenta lo que tienes registrado o configurado: tus "
        "ingresos fijos, tus gastos fijos, las parcialidades y lo que debes. No "
        "supone cuánto más vas a gastar. La tasa de cada tarjeta y hasta cuándo "
        "se puede posponer cada deuda se ponen en **Patrimonio → Por pagar**; si "
        "un gasto fijo se puede posponer, en **Gastos fijos**; lo que entra cada "
        "mes, en la pestaña **Ingresos fijos**."
    )

    # ── 3. Mes por mes, debajo de la gráfica ─────────────
    rotulo(f"Mes por mes hasta {final}")
    with st.container(border=True):
        st.caption(
            "Cada mes: lo que tienes, lo que vas a pagar y lo que te queda o te "
            "falta. Lo que pospones o no alcanza llega al mes siguiente, con sus "
            "intereses, como algo que toca pagar. Abre un mes para ver el detalle."
        )
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "mes": nombre_mes(m.mes),
                        "empiezas": m.efectivo_inicial,
                        "entra": m.ingresos,
                        "tienes": m.disponible,
                        "a_pagar": m.a_pagar,
                        "balance": m.balance,
                        "pospones": m.pospuesto,
                        "intereses": m.intereses,
                    }
                    for m in plan
                ]
            ),
            hide_index=True,
            column_config={
                "mes": st.column_config.TextColumn("Mes", pinned=True),
                "empiezas": st.column_config.NumberColumn(
                    "Empiezas con", format="$%.0f"
                ),
                "entra": st.column_config.NumberColumn("Entra", format="$%.0f"),
                "tienes": st.column_config.NumberColumn(
                    "Tienes para pagar", format="$%.0f"
                ),
                "a_pagar": st.column_config.NumberColumn(
                    "Vas a pagar",
                    format="$%.0f",
                    help="Lo que decidiste y lo que no se pospone, alcance o no.",
                ),
                "balance": st.column_config.NumberColumn(
                    "Te queda / falta",
                    format="$%.0f",
                    help="Tienes menos lo que vas a pagar. Negativo: lo que falta.",
                ),
                "pospones": st.column_config.NumberColumn("Pospones", format="$%.0f"),
                "intereses": st.column_config.NumberColumn("Intereses", format="$%.0f"),
            },
        )
        for mes_plan in plan:
            with st.expander(f"Qué pagas en {nombre_mes(mes_plan.mes)}"):
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "concepto": r.obligacion.concepto
                                + (" (atrasado)" if r.obligacion.atrasada else ""),
                                "monto": r.obligacion.monto,
                                "pagas": r.pagado,
                                "pospones": r.pospuesto,
                                "falta": r.falta,
                                "por_que": r.razon,
                            }
                            for r in mes_plan.resoluciones
                        ],
                        columns=[
                            "concepto",
                            "monto",
                            "pagas",
                            "pospones",
                            "falta",
                            "por_que",
                        ],
                    ),
                    hide_index=True,
                    column_config={
                        "concepto": st.column_config.TextColumn(
                            "Concepto", width="medium"
                        ),
                        "monto": st.column_config.NumberColumn("Monto", format="$%.2f"),
                        "pagas": st.column_config.NumberColumn("Pagas", format="$%.2f"),
                        "pospones": st.column_config.NumberColumn(
                            "Pospones", format="$%.2f"
                        ),
                        "falta": st.column_config.NumberColumn("Falta", format="$%.2f"),
                        "por_que": st.column_config.TextColumn(
                            "Por qué", width="medium"
                        ),
                    },
                )
