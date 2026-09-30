from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from finanzas.analytics.aggregations import etiqueta_periodo
from finanzas.application.app.components import (
    grafico_barras,
    grafico_patrimonio,
    invalidar_datos,
    moneda,
    obtener_servicios,
    reportar_error,
    tabla_equivalente,
)
from finanzas.application.services.patrimonio_service import FUENTE_CAPTURADO
from finanzas.domain.enums import Liquidez, OrigenSaldo, TipoCuenta, TipoPatrimonio

# ═══════════════════════════════════════════════════════════
# Patrimonio: lo que tienes y lo que debes, deducido
#
# El saldo de cada cuenta no se captura: se deduce de los
# movimientos a partir de un saldo verificado. Lo que sí se
# captura es ese saldo verificado —lo que dice el estado de
# cuenta o la app del banco un día concreto— y las posiciones
# que no son cuenta, como la casa o el auto.
# ═══════════════════════════════════════════════════════════

servicios = obtener_servicios()

st.title("Patrimonio")
st.caption(
    "Los saldos salen de los movimientos. Verifica un saldo real de vez en "
    "cuando —el de hoy en la app del banco, o el de un estado de cuenta— y "
    "el sistema deduce el resto, hacia adelante y hacia atrás."
)

SUBTIPOS = ("Inmueble", "Vehículo", "Otros bienes", "Préstamo personal", "Otro")


def _entero(valor: object) -> int | None:
    """Un día guardado, o None para dejar el campo vacío."""
    return None if valor is None or pd.isna(valor) else int(valor)


def _numero(valor: object) -> float | None:
    """Una cifra guardada, o None para dejar el campo vacío."""
    return None if valor is None or pd.isna(valor) else float(valor)


# ── La fecha del balance ─────────────────────────────────
#
# Por defecto hoy. Elegir otra responde «¿cómo estaba en ese momento?»,
# que es justo lo que un balance capturado a mano no podía responder.

cabecera = st.columns([1, 3])
with cabecera[0]:
    fecha_balance = st.date_input(
        "Balance al",
        value=date.today(),
        max_value=date.today(),
        format="DD/MM/YYYY",
        help="Cómo estaban las cuentas al cierre de ese día.",
    )

saldos = servicios.patrimonio.saldos(fecha_balance)
resumen = servicios.patrimonio.resumen(fecha_balance)
adeudo = resumen["por_pagar"]

with st.container(horizontal=True):
    st.metric("Activos", moneda(resumen["activos"]), border=True)
    st.metric(
        "Pasivos",
        moneda(resumen["pasivos"]),
        delta=f"incluye {moneda(adeudo)} por pagar" if adeudo else None,
        delta_color="off",
        border=True,
        help=(
            "Deuda de tarjetas y préstamos, más lo gastado que a esa fecha "
            "no se había pagado desde ninguna cuenta."
        ),
    )
    st.metric("Patrimonio neto", moneda(resumen["patrimonio_neto"]), border=True)
    st.metric(
        "Activos líquidos",
        moneda(servicios.patrimonio.activos_liquidos(fecha_balance)),
        help="Efectivo, débito y ahorro: el colchón disponible de inmediato.",
        border=True,
    )

# ── Guardarraíles ────────────────────────────────────────
#
# Un saldo sin verificar se suma desde cero, que casi nunca es verdad; y
# dos saldos verificados que los movimientos no conectan dicen que falta
# algo por registrar. Las dos cosas se avisan antes que nada.

sin_ancla = servicios.patrimonio.cuentas_sin_ancla()
if not sin_ancla.empty:
    nombres = ", ".join(str(nombre) for nombre in sin_ancla["nombre"])
    st.warning(
        f"{len(sin_ancla)} cuentas no tienen ningún saldo verificado, así que su "
        f"saldo se suma desde cero: {nombres}. Verifica el saldo de hoy abajo "
        "y el sistema deduce cómo estaban antes.",
        icon=":material/link_off:",
    )

descuadres = servicios.patrimonio.descuadres()
if not descuadres.empty:
    with st.container(border=True):
        st.subheader("Tramos que no cuadran")
        st.caption(
            "Entre dos saldos verificados, los movimientos tienen que explicar "
            "la diferencia. Donde no, falta un estado de cuenta por importar o "
            "algo quedó en la cuenta equivocada."
        )
        for fila in descuadres.itertuples():
            signo = "entraron" if fila.diferencia > 0 else "salieron"
            st.error(
                f"**{fila.cuenta}** · del {fila.desde:%d/%m/%Y} al "
                f"{fila.hasta:%d/%m/%Y}: con los {fila.movimientos} movimientos "
                f"registrados debería cerrar en {moneda(fila.esperado, decimales=2)} "
                f"y cerró en {moneda(fila.saldo_final, decimales=2)}. "
                f"Faltan {moneda(abs(fila.diferencia), decimales=2)} que "
                f"{signo} sin registrarse.",
                icon=":material/rule:",
            )

cuentas_tab, verificar_tab, adeudos_tab, otros_tab, evolucion_tab = st.tabs(
    ["Cuentas", "Verificar saldo", "Por pagar", "Otros bienes y deudas", "Evolución"]
)


# ═══════════════════════════════════════════════════════════
# Cuentas
# ═══════════════════════════════════════════════════════════

with cuentas_tab:
    if saldos.empty:
        st.info("Aún no hay cuentas. Da de alta las tuyas en **Catálogos**.")
    else:
        with st.container(border=True):
            st.subheader(f"Saldos al {fecha_balance:%d/%m/%Y}")
            st.dataframe(
                saldos,
                hide_index=True,
                column_config={
                    "cuenta_id": None,
                    "saldo": None,
                    "liquidez": None,
                    "ancla_saldo": None,
                    "cuenta": st.column_config.TextColumn("Cuenta", pinned=True),
                    "tipo": st.column_config.TextColumn("Tipo"),
                    "institucion": st.column_config.TextColumn("Institución"),
                    "lado": st.column_config.TextColumn("Lado"),
                    "saldo_visto": st.column_config.NumberColumn(
                        "Saldo",
                        format="$%.2f",
                        help="Como lo enseña el banco: en una tarjeta, lo que debes.",
                    ),
                    "verificado": st.column_config.CheckboxColumn(
                        "Verificado",
                        help="Si descansa en un saldo real o se suma desde cero.",
                    ),
                    "sentido": st.column_config.TextColumn("Deducido"),
                    "ancla_fecha": st.column_config.DateColumn(
                        "Saldo verificado del", format="DD/MM/YYYY"
                    ),
                    "ancla_origen": st.column_config.TextColumn("Fuente"),
                    "movimientos": st.column_config.NumberColumn(
                        "Movimientos", format="%d", help="Entre el ancla y la fecha."
                    ),
                },
            )

        por_tipo = (
            saldos.groupby("tipo", as_index=False)["saldo"]
            .sum()
            .rename(columns={"saldo": "saldo_libro"})
        )
        activos_por_tipo = por_tipo[por_tipo["saldo_libro"] > 0].rename(
            columns={"saldo_libro": "saldo"}
        )
        if not activos_por_tipo.empty:
            with st.container(border=True):
                st.subheader("Composición de los activos")
                st.altair_chart(
                    grafico_barras(
                        activos_por_tipo,
                        dimension="tipo",
                        medida="saldo",
                        titulo_dimension="Tipo de cuenta",
                        titulo_medida="Saldo",
                    )
                )
                tabla_equivalente(
                    activos_por_tipo.rename(columns={"tipo": "Tipo", "saldo": "Saldo"})
                )


# ═══════════════════════════════════════════════════════════
# Verificar saldo
# ═══════════════════════════════════════════════════════════

with verificar_tab:
    cuentas_catalogo = servicios.catalogos.cuentas()

    with st.container(border=True):
        st.subheader("Registrar un saldo real")
        st.caption(
            "Lo que dice la app del banco hoy, o el estado de cuenta al inicio "
            "y al fin de su periodo. En una tarjeta, captura lo que debes en "
            "positivo. Un saldo por cuenta y día: repetirlo lo corrige."
        )

        if cuentas_catalogo.empty:
            st.caption("Todavía no hay cuentas.")
        else:
            opciones_cuenta = {
                fila.nombre: int(fila.id) for fila in cuentas_catalogo.itertuples()
            }
            tipos_cuenta = {
                int(fila.id): str(fila.tipo) for fila in cuentas_catalogo.itertuples()
            }

            columnas = st.columns([2, 1, 1, 2])
            with columnas[0]:
                elegida = st.selectbox("Cuenta", list(opciones_cuenta))
            cuenta_id = opciones_cuenta[elegida]
            es_deuda = TipoCuenta(tipos_cuenta[cuenta_id]).es_pasivo

            with columnas[1]:
                fecha_saldo = st.date_input(
                    "Al cierre del",
                    value=date.today(),
                    max_value=date.today(),
                    format="DD/MM/YYYY",
                )

            with columnas[2]:
                saldo_real = st.number_input(
                    "Lo que debes" if es_deuda else "Saldo",
                    min_value=0.0,
                    step=100.0,
                    format="%.2f",
                    key=f"saldo_real_{cuenta_id}",
                )

            with columnas[3]:
                nota_saldo = st.text_input("Nota", placeholder="App del banco")

            deducido = saldos[saldos["cuenta_id"] == cuenta_id]
            if not deducido.empty and fecha_saldo == fecha_balance:
                actual = deducido.iloc[0]
                visto = moneda(actual["saldo_visto"], decimales=2)
                nota_ancla = "" if actual["verificado"] else " (sin saldo verificado)"
                st.caption(
                    f"Deducido para ese día: **{visto}**{nota_ancla}. Si difiere, "
                    "la diferencia son movimientos que faltan."
                )

            if st.button("Verificar saldo", type="primary", icon=":material/verified:"):
                try:
                    servicios.patrimonio.verificar_saldo(
                        cuenta_id,
                        fecha_saldo,
                        saldo_real,
                        origen=OrigenSaldo.MANUAL,
                        nota=nota_saldo,
                    )
                except ValueError as error:
                    reportar_error(error)
                else:
                    invalidar_datos()
                    st.success(
                        f"Saldo de «{elegida}» al {fecha_saldo:%d/%m/%Y} verificado.",
                        icon=":material/check:",
                    )
                    st.rerun()

    anclas = servicios.patrimonio.anclas()
    with st.container(border=True):
        st.subheader("Saldos verificados")
        if anclas.empty:
            st.caption(
                "Ninguno todavía. Importar un estado de cuenta registra los suyos "
                "solos; el de hoy lo capturas arriba."
            )
        else:
            seleccion = st.dataframe(
                anclas,
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
                column_config={
                    "id": None,
                    "cuenta_id": None,
                    "cuenta_tipo": None,
                    "saldo": None,
                    "creado_en": None,
                    "cuenta": st.column_config.TextColumn("Cuenta", pinned=True),
                    "fecha": st.column_config.DateColumn(
                        "Al cierre del", format="DD/MM/YYYY"
                    ),
                    "saldo_visto": st.column_config.NumberColumn(
                        "Saldo", format="$%.2f"
                    ),
                    "origen": st.column_config.TextColumn("Fuente"),
                    "nota": st.column_config.TextColumn("Nota", width="medium"),
                },
            )
            filas = seleccion.selection.rows
            if filas:
                ancla = anclas.iloc[filas[0]]
                if st.button(
                    f"Olvidar el saldo de «{ancla['cuenta']}» del "
                    f"{ancla['fecha']:%d/%m/%Y}",
                    icon=":material/delete:",
                ):
                    servicios.patrimonio.olvidar_saldo(int(ancla["id"]))
                    invalidar_datos()
                    st.rerun()
            else:
                st.caption("Selecciona uno para olvidarlo.")


# ═══════════════════════════════════════════════════════════
# Adeudos generados
#
# Lo que ya se gastó y no se ha pagado desde ninguna cuenta. No
# es una posición capturada sino la suma de los movimientos
# devengados, así que se lee aquí y se liquida en Movimientos.
# ═══════════════════════════════════════════════════════════

with adeudos_tab:
    # ── Lo exigible de cada deuda ────────────────────────
    #
    # El saldo de un préstamo o una tarjeta es todo lo que se debe; aquí
    # se dice cuánto de eso ya hay que pagar. Los abonos que registres
    # después lo van descontando solos.

    exigibles = servicios.patrimonio.exigibles()
    with st.container(border=True):
        st.subheader("Lo que ya es exigible de tus deudas")
        st.caption(
            "De lo que debe cada tarjeta o préstamo, la parte que ya toca pagar. "
            "Con sus días fijos sale sola cada ciclo: en una tarjeta, lo que "
            "debía al corte; en un préstamo, su parcialidad. Los traspasos que "
            "hagas a esa cuenta dentro del ciclo la van descontando."
        )

        if exigibles.empty:
            st.caption("No tienes cuentas de deuda en el catálogo.")
        else:
            st.dataframe(
                exigibles,
                hide_index=True,
                column_config={
                    "cuenta_id": None,
                    "institucion": None,
                    "declarado_el": None,
                    "dia_corte": None,
                    "dia_pago": None,
                    "pago_mensual": None,
                    "cuenta": st.column_config.TextColumn("Cuenta", pinned=True),
                    "tipo": st.column_config.TextColumn("Tipo"),
                    "deuda": st.column_config.NumberColumn(
                        "Deuda total", format="$%.2f"
                    ),
                    "exigible": st.column_config.NumberColumn(
                        "Exigible", format="$%.2f"
                    ),
                    "fuente": st.column_config.TextColumn(
                        "De dónde sale",
                        help=(
                            "Capturado: lo que pusiste a mano para este ciclo. "
                            "Saldo al corte: lo que debía la tarjeta el día de "
                            "corte. Parcialidad: el pago fijo del préstamo."
                        ),
                    ),
                    "corte": st.column_config.DateColumn(
                        "Ciclo desde", format="DD/MM/YYYY"
                    ),
                    "abonado": st.column_config.NumberColumn("Abonado", format="$%.2f"),
                    "pendiente": st.column_config.NumberColumn(
                        "Falta pagar", format="$%.2f"
                    ),
                    "fecha_limite": st.column_config.DateColumn(
                        "Fecha límite", format="DD/MM/YYYY"
                    ),
                    "dias": st.column_config.NumberColumn(
                        "Días", format="%d", help="Negativo: ya venció."
                    ),
                    "estado": st.column_config.TextColumn("Estado"),
                    "nota": st.column_config.TextColumn("Nota", width="medium"),
                },
            )

            opciones_deuda = {
                fila.cuenta: int(fila.cuenta_id) for fila in exigibles.itertuples()
            }
            deuda_elegida = st.selectbox(
                "Cuenta de deuda", list(opciones_deuda), key="exigible_cuenta"
            )
            deuda_id = opciones_deuda[deuda_elegida]
            actual_deuda = exigibles[exigibles["cuenta_id"] == deuda_id].iloc[0]
            es_tarjeta = actual_deuda["tipo"] == str(TipoCuenta.CREDITO)

            # ── Días fijos ───────────────────────────────
            st.markdown("**Días fijos**")
            dias = st.columns(3)
            with dias[0]:
                dia_pago = st.number_input(
                    "Día de pago",
                    min_value=1,
                    max_value=31,
                    value=_entero(actual_deuda["dia_pago"]),
                    step=1,
                    key=f"dia_pago_{deuda_id}",
                    help="El día del mes en que vence el pago.",
                )
            with dias[1]:
                if es_tarjeta:
                    dia_corte = st.number_input(
                        "Día de corte",
                        min_value=1,
                        max_value=31,
                        value=_entero(actual_deuda["dia_corte"]),
                        step=1,
                        key=f"dia_corte_{deuda_id}",
                        help="Lo que debe la tarjeta ese día es lo que hay que pagar.",
                    )
                    pago_mensual = None
                else:
                    dia_corte = None
                    pago_mensual = st.number_input(
                        "Parcialidad",
                        min_value=0.0,
                        value=_numero(actual_deuda["pago_mensual"]),
                        step=100.0,
                        format="%.2f",
                        key=f"parcialidad_{deuda_id}",
                        help="Lo que pagas cada mes. Vacío: lo capturas tú.",
                    )

            with st.container(horizontal=True):
                if st.button(
                    "Guardar días", type="primary", icon=":material/event_repeat:"
                ):
                    if dia_pago is None or (es_tarjeta and dia_corte is None):
                        st.warning(
                            "Pon el día de pago"
                            + (" y el de corte." if es_tarjeta else "."),
                            icon=":material/info:",
                        )
                    else:
                        try:
                            servicios.patrimonio.fijar_calendario_deuda(
                                deuda_id,
                                dia_pago=dia_pago,
                                dia_corte=dia_corte,
                                pago_mensual=pago_mensual,
                            )
                        except ValueError as error:
                            reportar_error(error)
                        else:
                            invalidar_datos()
                            st.rerun()

                if pd.notna(actual_deuda["dia_pago"]) and st.button(
                    "Quitar días", icon=":material/event_busy:"
                ):
                    servicios.patrimonio.fijar_calendario_deuda(deuda_id, dia_pago=None)
                    invalidar_datos()
                    st.rerun()

            if es_tarjeta:
                st.caption(
                    "Con compras a meses, lo que debía al corte incluye todo lo "
                    "que falta de ellas y exagera el pago. Captura abajo el «pago "
                    "para no generar intereses» del estado de cuenta: manda "
                    "durante ese ciclo."
                )

            # ── Para el plan de pagos ────────────────────
            st.markdown("**Si no alcanza para pagarla**")
            regla = (
                servicios.patrimonio.reglas_de_deudas()
                .set_index("cuenta_id")
                .loc[deuda_id]
            )
            reglas_columnas = st.columns(3)
            with reglas_columnas[0]:
                tasa_pct = st.number_input(
                    "Tasa anual (%)",
                    min_value=0.0,
                    max_value=500.0,
                    value=(
                        float(regla["tasa_anual"]) * 100
                        if pd.notna(regla["tasa_anual"])
                        else 0.0
                    ),
                    step=1.0,
                    format="%.2f",
                    key=f"tasa_{deuda_id}",
                    help=(
                        "Lo que cobra al año por lo que dejas sin pagar; viene "
                        "en el estado de cuenta. El plan paga primero la deuda "
                        "más cara."
                    ),
                )
            with reglas_columnas[1]:
                tope = st.date_input(
                    "Se puede posponer hasta",
                    value=regla["posponer_hasta"]
                    if pd.notna(regla["posponer_hasta"])
                    else None,
                    format="DD/MM/YYYY",
                    key=f"tope_{deuda_id}",
                    help=(
                        "Hasta cuándo puedes dejarla sin pagar sin problema, "
                        "como un adeudo que se liquida a fin de semestre. "
                        "Vacío: una tarjeta se puede posponer a su costo; un "
                        "préstamo sin tasa ni fecha, no."
                    ),
                )
            with reglas_columnas[2]:
                st.markdown("&nbsp;")
                if st.button("Guardar reglas", icon=":material/rule:"):
                    try:
                        servicios.patrimonio.fijar_reglas_deuda(
                            deuda_id,
                            tasa_anual=tasa_pct / 100 if tasa_pct else None,
                            posponer_hasta=tope,
                        )
                    except ValueError as error:
                        reportar_error(error)
                    else:
                        invalidar_datos()
                        st.rerun()

            # ── Captura a mano ───────────────────────────
            st.markdown("**Capturar a mano**")
            st.caption(
                "Manda sobre la estimación mientras siga siendo el mismo ciclo. "
                "Sin días fijos, vale hasta que lo cambies."
            )
            columnas = st.columns(3)
            capturado = actual_deuda["fuente"] == FUENTE_CAPTURADO
            with columnas[0]:
                monto_exigible = st.number_input(
                    "Exigible",
                    min_value=0.0,
                    value=float(actual_deuda["exigible"]),
                    step=100.0,
                    format="%.2f",
                    key=f"exigible_monto_{deuda_id}",
                    help="Lo que ya hay que pagar, no la deuda total.",
                )
            with columnas[1]:
                limite_exigible = st.date_input(
                    "Fecha límite",
                    value=actual_deuda["fecha_limite"],
                    format="DD/MM/YYYY",
                    key=f"exigible_limite_{deuda_id}",
                )
            with columnas[2]:
                declarado_exigible = st.date_input(
                    "Al día",
                    value=(
                        actual_deuda["corte"]
                        if pd.notna(actual_deuda["corte"])
                        else date.today()
                    ),
                    max_value=date.today(),
                    format="DD/MM/YYYY",
                    key=f"exigible_declarado_{deuda_id}",
                    help=(
                        "Desde cuándo vale esta cifra: los abonos de ese día en "
                        "adelante la descuentan. Si la tomas del estado de "
                        "cuenta, pon su fecha de corte."
                    ),
                )
            nota_exigible = st.text_input(
                "Nota",
                value=str(actual_deuda["nota"] or ""),
                placeholder="Parcialidad de octubre",
                key=f"exigible_nota_{deuda_id}",
            )

            if monto_exigible > float(actual_deuda["deuda"]) + 0.005:
                deuda_deducida = moneda(actual_deuda["deuda"], decimales=2)
                st.caption(
                    f"Es más que la deuda deducida ({deuda_deducida}). Puede "
                    "faltar un saldo verificado; lo pendiente no pasará de la deuda."
                )

            with st.container(horizontal=True):
                if st.button("Guardar exigible", icon=":material/save:"):
                    try:
                        servicios.patrimonio.fijar_exigible(
                            deuda_id,
                            monto_exigible,
                            fecha_limite=limite_exigible,
                            declarado_el=declarado_exigible,
                            nota=nota_exigible,
                        )
                    except ValueError as error:
                        reportar_error(error)
                    else:
                        invalidar_datos()
                        st.success(
                            f"Exigible de «{deuda_elegida}» guardado.",
                            icon=":material/check:",
                        )
                        st.rerun()

                if capturado and st.button(
                    "Quitar lo capturado", icon=":material/delete:"
                ):
                    servicios.patrimonio.quitar_exigible(deuda_id)
                    invalidar_datos()
                    st.rerun()

    por_pagar = servicios.movimientos.por_pagar()

    if por_pagar.empty:
        st.success(
            "No debes nada fuera de las tarjetas: todo lo que gastaste ya salió "
            "de una cuenta.",
            icon=":material/check_circle:",
        )
    else:
        with st.container(horizontal=True):
            st.metric("Total por pagar", moneda(por_pagar["monto"].sum()), border=True)
            st.metric("Movimientos", len(por_pagar), border=True)
            st.metric(
                "El más antiguo",
                f"{int(por_pagar['dias_pendiente'].max())} días",
                border=True,
            )

        with st.container(border=True):
            st.subheader("Lo que debes")
            st.caption(
                "Estos gastos ya pesan en el presupuesto de su mes, pero no "
                "han salido de ninguna cuenta. Márcalos como pagados desde "
                "**Movimientos** cuando los liquides. La deuda de la tarjeta "
                "no está aquí: es el saldo de la tarjeta."
            )
            st.dataframe(
                por_pagar,
                hide_index=True,
                column_config={
                    "id": st.column_config.NumberColumn("ID", width="small"),
                    "fecha": st.column_config.DateColumn(
                        "Fecha del gasto", format="DD/MM/YYYY"
                    ),
                    "periodo": None,
                    "cuenta_id": None,
                    "monto": st.column_config.NumberColumn("Monto", format="$%.2f"),
                    "descripcion": st.column_config.TextColumn(
                        "Descripción", width="medium"
                    ),
                    "categoria": st.column_config.TextColumn("Categoría"),
                    "cuenta": st.column_config.TextColumn("Cuenta"),
                    "dias_pendiente": st.column_config.NumberColumn(
                        "Días pendiente", format="%d"
                    ),
                },
            )

        por_categoria = (
            por_pagar.groupby("categoria", as_index=False)["monto"]
            .sum()
            .sort_values("monto", ascending=False)
        )
        if len(por_categoria) > 1:
            with st.container(border=True):
                st.subheader("Adeudo por categoría")
                st.altair_chart(
                    grafico_barras(
                        por_categoria,
                        dimension="categoria",
                        medida="monto",
                        titulo_dimension="Categoría",
                        titulo_medida="Por pagar",
                    )
                )


# ═══════════════════════════════════════════════════════════
# Otros bienes y deudas: lo que no es cuenta
# ═══════════════════════════════════════════════════════════

with otros_tab:
    balance = servicios.patrimonio.balance()

    if balance.empty:
        st.info(
            "Nada registrado. Aquí van los bienes y deudas que no son una cuenta: "
            "la casa, el auto, lo que le debes a alguien.",
            icon=":material/info:",
        )
    else:
        with st.container(border=True):
            st.subheader("Posiciones")
            st.dataframe(
                balance,
                hide_index=True,
                column_config={
                    "id": None,
                    "cuenta_id": None,
                    "cuenta": None,
                    "cuenta_tipo": None,
                    "nombre": st.column_config.TextColumn("Bien o deuda", pinned=True),
                    "tipo": st.column_config.TextColumn("Tipo"),
                    "subtipo": st.column_config.TextColumn("Subtipo"),
                    "institucion": st.column_config.TextColumn("Institución"),
                    "saldo": st.column_config.NumberColumn("Valor", format="$%.2f"),
                    "liquidez": st.column_config.TextColumn("Liquidez"),
                    "tasa_anual": st.column_config.NumberColumn(
                        "Tasa anual", format="percent"
                    ),
                    "fecha_corte": st.column_config.DateColumn(
                        "Actualizado el", format="DD/MM/YYYY"
                    ),
                    "moneda": st.column_config.TextColumn("Moneda"),
                    "notas": st.column_config.TextColumn("Notas", width="medium"),
                    "aporte_a_patrimonio": st.column_config.NumberColumn(
                        "Aporte al neto", format="$%.2f"
                    ),
                },
            )

        with st.container(border=True):
            st.subheader("Actualizar un valor")

            opciones = {fila.nombre: int(fila.id) for fila in balance.itertuples()}
            columnas = st.columns([2, 1, 1])

            with columnas[0]:
                elegida = st.selectbox("Posición", list(opciones))
            posicion_id = opciones[elegida]
            actual = balance[balance["id"] == posicion_id].iloc[0]

            with columnas[1]:
                nuevo_saldo = st.number_input(
                    "Valor actual",
                    min_value=0.0,
                    value=float(actual["saldo"]),
                    step=100.0,
                    format="%.2f",
                )

            with columnas[2]:
                st.markdown("&nbsp;")
                if st.button("Guardar valor", type="primary", icon=":material/save:"):
                    try:
                        servicios.patrimonio.actualizar_saldo(posicion_id, nuevo_saldo)
                    except ValueError as error:
                        reportar_error(error)
                    else:
                        invalidar_datos()
                        st.success("Valor actualizado.", icon=":material/check:")
                        st.rerun()

            if st.button("Eliminar posición", icon=":material/delete:"):
                servicios.patrimonio.eliminar(posicion_id)
                invalidar_datos()
                st.success(f"«{elegida}» eliminada.", icon=":material/check:")
                st.rerun()

    with st.container(border=True):
        st.subheader("Nuevo bien o deuda")
        st.caption(
            "Sólo lo que no es una cuenta del catálogo: las cuentas ya están "
            "arriba con su saldo deducido."
        )

        with st.form("nueva_posicion", clear_on_submit=True, border=False):
            fila_1 = st.columns([2, 1, 1])

            with fila_1[0]:
                nombre = st.text_input("Nombre", placeholder="Departamento")
            with fila_1[1]:
                tipo = st.selectbox("Tipo", [str(valor) for valor in TipoPatrimonio])
            with fila_1[2]:
                subtipo = st.selectbox("Subtipo", SUBTIPOS)

            fila_2 = st.columns(4)

            with fila_2[0]:
                saldo = st.number_input(
                    "Valor", min_value=0.0, step=500.0, format="%.2f"
                )
            with fila_2[1]:
                liquidez = st.selectbox(
                    "Liquidez",
                    [str(valor) for valor in Liquidez],
                    index=2,
                )
            with fila_2[2]:
                institucion = st.text_input("Institución", placeholder="Opcional")
            with fila_2[3]:
                tasa = st.number_input(
                    "Tasa anual",
                    min_value=0.0,
                    max_value=2.0,
                    step=0.01,
                    format="%.4f",
                    help="En proporción: 0.07 equivale a 7%.",
                )

            notas = st.text_input("Notas", placeholder="Avalúo de enero")

            if st.form_submit_button("Agregar", type="primary", icon=":material/add:"):
                try:
                    servicios.patrimonio.crear(
                        nombre=nombre,
                        tipo=tipo,
                        saldo=saldo,
                        subtipo=subtipo,
                        institucion=institucion,
                        liquidez=liquidez,
                        tasa_anual=tasa,
                        fecha_corte=date.today(),
                        notas=notas,
                    )
                except ValueError as error:
                    reportar_error(error)
                else:
                    invalidar_datos()
                    st.success(f"«{nombre}» agregada.", icon=":material/check_circle:")
                    st.rerun()


# ═══════════════════════════════════════════════════════════
# Evolución: el cierre de cada mes, deducido
# ═══════════════════════════════════════════════════════════

with evolucion_tab:
    cierres = servicios.patrimonio.evolucion()

    if cierres.empty:
        st.info(
            "Sin movimientos ni saldos verificados todavía, no hay nada que dibujar.",
            icon=":material/info:",
        )
    else:
        with st.container(border=True):
            st.subheader("Patrimonio neto al cierre de cada mes")
            st.caption(
                "Deducido de los saldos de las cuentas al último día de cada "
                "mes. Los meses anteriores al primer saldo verificado se "
                "deducen hacia atrás."
            )
            st.altair_chart(grafico_patrimonio(cierres))
            tabla_equivalente(
                cierres.rename(
                    columns={
                        "periodo": "Periodo",
                        "efectivo": "Efectivo y débito",
                        "ahorro": "Ahorro",
                        "inversiones": "Inversiones",
                        "otros_activos": "Otros bienes",
                        "deudas": "Deudas",
                        "patrimonio_neto": "Patrimonio neto",
                        "cambio_mensual": "Cambio",
                    }
                )
            )

        ultimo = cierres.iloc[-1]
        with st.container(horizontal=True):
            st.metric(
                "Último cierre",
                etiqueta_periodo(str(ultimo["periodo"])),
                border=True,
            )
            st.metric(
                "Patrimonio neto",
                moneda(float(ultimo["patrimonio_neto"])),
                delta=moneda(float(ultimo["cambio_mensual"])),
                border=True,
            )

        if isinstance(cierres, pd.DataFrame) and len(cierres) > 1:
            st.caption(
                "El cambio de cada mes es la diferencia con el anterior. Un "
                "mes con descuadre pendiente puede verse raro hasta que se "
                "registre lo que falta."
            )
