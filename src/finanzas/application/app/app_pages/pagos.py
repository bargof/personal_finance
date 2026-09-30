from __future__ import annotations

from datetime import date

import streamlit as st

from finanzas.application.app.cifras_del_mes import cifras_del_mes, nombre_mes
from finanzas.application.app.components import (
    insignia_estado,
    invalidar_datos,
    moneda,
    obtener_servicios,
    reportar_error,
)
from finanzas.application.app.plan_de_pagos import dibujar_plan
from finanzas.application.app.theme import rotulo
from finanzas.application.services.pagos_service import DEUDA_EXIGIBLE
from finanzas.domain.enums import EstadoPago

# ═══════════════════════════════════════════════════════════
# Pagos del mes: qué hay que pagar ya, y si alcanza
#
# Patrimonio dice cuánto se debe en total; aquí sólo lo que ya
# toca pagar —cargos fijos del mes, lo exigible de cada deuda,
# lo gastado sin pagar— junto a lo que entra y lo que hay para
# cubrirlo. Cada peso se cuenta una vez: un cargo fijo ya
# registrado y sin pagar está entre los gastos por pagar.
# ═══════════════════════════════════════════════════════════

servicios = obtener_servicios()
hoy = date.today()

st.title("Pagos del mes")


def _desplazar(base: date, meses: int) -> date:
    """El primer día del mes que queda `meses` antes o después de `base`."""
    indice = base.year * 12 + base.month - 1 + meses
    return date(indice // 12, indice % 12 + 1, 1)


opciones_mes = {nombre_mes(m): m for m in (_desplazar(hoy, d) for d in (-1, 0, 1, 2))}
cabecera = st.columns([1, 3])
with cabecera[0]:
    elegido = st.selectbox(
        "Mes",
        list(opciones_mes),
        index=1,
        help=(
            "Cambia los cargos fijos y el ingreso. Lo exigible y lo sin pagar "
            "son de hoy."
        ),
    )
mes = opciones_mes[elegido]

calendario = servicios.pagos.calendario(mes)
exigibles = servicios.patrimonio.exigibles()

st.caption(
    f"Cargos fijos e ingreso de {elegido}; lo exigible de tus deudas, lo "
    "gastado sin pagar y lo disponible, al día de hoy."
)

# El plan se arma una vez: las cifras de arriba siguen sus decisiones del
# mes, y la pestaña del plan lo dibuja completo.
plan = servicios.pagos.plan(mes, hoy)
resumen, pendientes = cifras_del_mes(mes, plan_mes=plan[0] if plan else None)

pestana_debes, pestana_plan, pestana_ingresos = st.tabs(
    ["Lo que debes", "Plan hasta diciembre", "Ingresos fijos"]
)

with pestana_debes:
    # ── Lo que ya debes pagar ────────────────────────────────

    rotulo("Lo que ya debes pagar")

    with st.container(border=True):
        if pendientes.empty:
            st.success(
                "Nada por pagar: los cargos del mes ya están pagados y no hay nada "
                "exigible ni gastos sin pagar.",
                icon=":material/check_circle:",
            )
        else:
            st.dataframe(
                pendientes,
                hide_index=True,
                column_config={
                    "concepto": st.column_config.TextColumn("Concepto", width="medium"),
                    "origen": st.column_config.TextColumn("De dónde viene"),
                    "cuenta": st.column_config.TextColumn("Cuenta"),
                    "monto": st.column_config.NumberColumn("Monto", format="$%.2f"),
                    "fecha_limite": st.column_config.DateColumn(
                        "Fecha límite", format="DD/MM/YYYY"
                    ),
                    "estado": st.column_config.TextColumn("Estado"),
                },
            )
            st.caption(
                "Lo vencido va primero. Un gasto sin pagar se liquida marcándolo como "
                "pagado en **Movimientos**; lo exigible de una deuda baja solo al "
                "registrar el traspaso a esa cuenta."
            )

    # ── Gastos fijos y deudas ────────────────────────────────

    izquierda, derecha = st.columns([3, 2])

    with izquierda:
        rotulo(f"Gastos fijos de {elegido}")
        with st.container(border=True):
            anticipacion = servicios.pagos.anticipacion_dias()
            ajuste = st.columns([2, 1])
            with ajuste[0]:
                nueva_anticipacion = st.number_input(
                    "Pagar con este mes lo que vence hasta el día (del mes siguiente)",
                    min_value=0,
                    max_value=31,
                    value=anticipacion,
                    step=1,
                    key="anticipacion_dias",
                    help=(
                        "Los gastos fijos del mes siguiente que vencen hasta este "
                        "día se pagan con el dinero de este mes: con 1, la renta "
                        "del día 1. Vale también para cada mes del plan: en "
                        "octubre sale la renta del 1 de noviembre. Con 0, cada "
                        "cargo en su propio mes."
                    ),
                )
            with ajuste[1]:
                st.markdown("&nbsp;")
                if st.button(
                    "Aplicar",
                    icon=":material/event_upcoming:",
                    disabled=nueva_anticipacion == anticipacion,
                    key="aplicar_anticipacion",
                ):
                    servicios.pagos.fijar_anticipacion(int(nueva_anticipacion))
                    invalidar_datos()
                    st.rerun()
            if calendario.empty:
                st.info(
                    "No hay gastos fijos que toquen este mes. Da de alta la renta, el "
                    "celular y tus suscripciones en **Gastos fijos**, con su próximo "
                    "cobro.",
                    icon=":material/event_repeat:",
                )
            else:
                pagados = calendario[calendario["estado"] == str(EstadoPago.PAGADO)]
                st.progress(
                    min(resumen.fijos_pagados / resumen.fijos_total, 1.0)
                    if resumen.fijos_total
                    else 0.0,
                    text=(
                        f"{len(pagados)} de {len(calendario)} pagados · "
                        f"{moneda(resumen.fijos_pagados)} de "
                        f"{moneda(resumen.fijos_total)}"
                    ),
                )
                st.dataframe(
                    calendario,
                    hide_index=True,
                    column_config={
                        "id": None,
                        "movimiento_id": None,
                        "en_sin_pagar": None,
                        "fecha": st.column_config.DateColumn("Toca el", format="DD/MM"),
                        "servicio": st.column_config.TextColumn(
                            "Concepto", pinned=True
                        ),
                        "clase": st.column_config.TextColumn("Clase"),
                        "categoria": st.column_config.TextColumn("Categoría"),
                        "cuenta": st.column_config.TextColumn("Cuenta"),
                        "monto": st.column_config.NumberColumn("Monto", format="$%.2f"),
                        "estado": st.column_config.TextColumn("Estado"),
                        "pagado_el": st.column_config.DateColumn(
                            "Pagado el", format="DD/MM/YYYY"
                        ),
                        "adelantado": st.column_config.CheckboxColumn(
                            "Mes siguiente",
                            help="Vence en los primeros días del mes que viene.",
                        ),
                    },
                    column_order=[
                        "fecha",
                        "adelantado",
                        "servicio",
                        "monto",
                        "estado",
                        "pagado_el",
                        "clase",
                        "categoria",
                        "cuenta",
                    ],
                )
                registrados_sin_pagar = int(
                    calendario["en_sin_pagar"].astype(bool).sum()
                )
                if registrados_sin_pagar:
                    st.caption(
                        f"{registrados_sin_pagar} ya los registraste pero siguen sin "
                        "pagar: cuentan como gasto sin pagar, no dos veces."
                    )
                if calendario["adelantado"].astype(bool).any():
                    st.caption(
                        "Los marcados «Mes siguiente» vencen hasta el día "
                        f"{servicios.pagos.anticipacion_dias()} del mes que viene: se "
                        "pagan con el dinero de este mes, como la renta del 1."
                    )
                st.caption(
                    "Un cargo cuenta como pagado cuando hay un gasto confirmado de su "
                    "categoría —o que lo nombra— por un monto parecido, cerca de su "
                    "fecha."
                )

    with derecha:
        rotulo("Deudas")
        with st.container(border=True):
            con_exigible = (
                exigibles[exigibles["estado"] != ""]
                if not exigibles.empty
                else exigibles
            )
            if exigibles.empty:
                st.caption("No tienes cuentas de deuda.")
            else:
                for deuda in exigibles.itertuples():
                    limite = (
                        f" · límite {deuda.fecha_limite:%d/%m}"
                        if deuda.fecha_limite
                        else ""
                    )
                    if deuda.estado:
                        detalle = (
                            f"{insignia_estado(deuda.estado)} · falta "
                            f"{moneda(deuda.pendiente, decimales=2)} de "
                            f"{moneda(deuda.exigible, decimales=2)}{limite}  \n"
                            f"{deuda.fuente}"
                        )
                    elif deuda.fuente:
                        detalle = f"{deuda.fuente}{limite}"
                    else:
                        detalle = "Sin días fijos ni exigible capturado"
                    st.markdown(
                        f"**{deuda.cuenta}** — debe {moneda(deuda.deuda)}  \n{detalle}"
                    )
                if con_exigible.empty:
                    st.caption(
                        f"Para que una deuda aparezca como {DEUDA_EXIGIBLE.lower()}, "
                        "pon sus días fijos o captura cuánto ya toca pagar en "
                        "**Patrimonio → Por pagar**."
                    )
                else:
                    st.caption("Se captura y corrige en **Patrimonio → Por pagar**.")

with pestana_plan:
    dibujar_plan(mes, hoy, plan)

with pestana_ingresos:
    # ── Ingresos fijos ───────────────────────────────────────
    #
    # La nómina y lo que llegue cada mes igual. Cuentan en el ingreso del mes
    # aunque todavía no se registren; al llegar, manda lo que llegó.

    rotulo("Ingresos fijos")

    with st.container(border=True):
        catalogo_fijos = servicios.pagos.ingresos_fijos()
        st.caption(
            "Lo que entra cada mes aunque aún no lo registres, como la nómina en sus "
            "quincenas. «Reconocer por» es un texto de la descripción o del concepto "
            "del banco —«NOMINA»— con el que se identifica el movimiento que lo trae."
        )

        if not catalogo_fijos.empty:
            st.dataframe(
                catalogo_fijos,
                hide_index=True,
                column_config={
                    "id": None,
                    "categoria_id": None,
                    "cuenta_id": None,
                    "concepto": st.column_config.TextColumn("Concepto", pinned=True),
                    "monto": st.column_config.NumberColumn("Esperado", format="$%.2f"),
                    "dia": st.column_config.NumberColumn("Día", format="%d"),
                    "categoria": st.column_config.TextColumn("Categoría"),
                    "cuenta": st.column_config.TextColumn("Cuenta"),
                    "texto": st.column_config.TextColumn("Reconocer por"),
                    "activo": st.column_config.CheckboxColumn("Activo"),
                },
            )

            opciones_fijo = {
                fila.concepto: int(fila.id) for fila in catalogo_fijos.itertuples()
            }
            elegido_fijo = st.selectbox(
                "Ingreso fijo", list(opciones_fijo), key="ingreso_fijo_elegido"
            )
            fijo_id = opciones_fijo[elegido_fijo]
            actual_fijo = catalogo_fijos[catalogo_fijos["id"] == fijo_id].iloc[0]

            historial = servicios.pagos.historial_ingreso_fijo(fijo_id)
            if historial.empty:
                st.caption("Todavía no hay meses anteriores en que haya llegado.")
            else:
                promedio = round(float(historial["recibido"].mean()), 2)
                detalle_historial = " · ".join(
                    f"{fila.fecha:%d/%m}: {moneda(fila.recibido, decimales=2)}"
                    for fila in historial.itertuples()
                )
                st.caption(
                    f"En los últimos {len(historial)} meses llegó en promedio "
                    f"**{moneda(promedio, decimales=2)}** ({detalle_historial})."
                )

            edicion = st.columns([1, 1, 2, 1])
            with edicion[0]:
                nuevo_monto = st.number_input(
                    "Esperado",
                    min_value=0.0,
                    value=float(actual_fijo["monto"]),
                    step=100.0,
                    format="%.2f",
                    key=f"ingreso_fijo_monto_{fijo_id}",
                )
            with edicion[1]:
                nuevo_dia = st.number_input(
                    "Día",
                    min_value=1,
                    max_value=31,
                    value=int(actual_fijo["dia"]),
                    step=1,
                    key=f"ingreso_fijo_dia_{fijo_id}",
                )
            with edicion[2]:
                nuevo_texto = st.text_input(
                    "Reconocer por",
                    value=str(actual_fijo["texto"]),
                    key=f"ingreso_fijo_texto_{fijo_id}",
                )
            with edicion[3]:
                sigue_activo = st.checkbox(
                    "Activo",
                    value=bool(actual_fijo["activo"]),
                    key=f"ingreso_fijo_activo_{fijo_id}",
                )

            with st.container(horizontal=True):
                if st.button(
                    "Guardar", type="primary", icon=":material/save:", key="if_g"
                ):
                    try:
                        servicios.pagos.actualizar_ingreso_fijo(
                            fijo_id,
                            monto=nuevo_monto,
                            dia=nuevo_dia,
                            texto=nuevo_texto,
                            activo=sigue_activo,
                        )
                    except ValueError as error:
                        reportar_error(error)
                    else:
                        invalidar_datos()
                        st.rerun()
                if not historial.empty and st.button(
                    f"Usar el promedio ({moneda(promedio, decimales=2)})",
                    icon=":material/functions:",
                    key="if_promedio",
                ):
                    servicios.pagos.actualizar_ingreso_fijo(fijo_id, monto=promedio)
                    invalidar_datos()
                    st.rerun()
                if st.button("Eliminar", icon=":material/delete:", key="if_e"):
                    servicios.pagos.eliminar_ingreso_fijo(fijo_id)
                    invalidar_datos()
                    st.rerun()

        with st.expander("Agregar un ingreso fijo", icon=":material/add:"):
            opciones_captura = servicios.catalogos.opciones_captura()
            categorias_ingreso = {"— sin categoría —": None} | {
                fila.nombre: int(fila.id)
                for fila in opciones_captura["categorias"].itertuples()
            }
            cuentas_ingreso = {"— cualquiera —": None} | {
                fila.nombre: int(fila.id)
                for fila in opciones_captura["cuentas"].itertuples()
            }
            with st.form("nuevo_ingreso_fijo", clear_on_submit=True, border=False):
                fila_1 = st.columns([2, 1, 1])
                with fila_1[0]:
                    concepto = st.text_input(
                        "Concepto", placeholder="Nómina · 1ª quincena"
                    )
                with fila_1[1]:
                    monto = st.number_input(
                        "Esperado", min_value=0.0, step=100.0, format="%.2f"
                    )
                with fila_1[2]:
                    dia = st.number_input("Día", min_value=1, max_value=31, value=15)
                fila_2 = st.columns(3)
                with fila_2[0]:
                    categoria = st.selectbox("Categoría", list(categorias_ingreso))
                with fila_2[1]:
                    cuenta = st.selectbox("Llega a", list(cuentas_ingreso))
                with fila_2[2]:
                    texto = st.text_input("Reconocer por", placeholder="NOMINA")

                if st.form_submit_button(
                    "Agregar", type="primary", icon=":material/add:"
                ):
                    try:
                        servicios.pagos.crear_ingreso_fijo(
                            concepto=concepto,
                            monto=monto,
                            dia=int(dia),
                            categoria_id=categorias_ingreso[categoria],
                            cuenta_id=cuentas_ingreso[cuenta],
                            texto=texto,
                        )
                    except ValueError as error:
                        reportar_error(error)
                    else:
                        invalidar_datos()
                        st.rerun()
