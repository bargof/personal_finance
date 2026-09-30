from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from finanzas.analytics.aggregations import MESES_ES
from finanzas.application.app.components import moneda, obtener_servicios
from finanzas.application.services.pagos_service import (
    COMO_OTRO,
    COMO_PENDIENTE,
    COMO_RESTRINGIDA,
    ResumenPagos,
)
from finanzas.domain.enums import EstadoPago
from finanzas.domain.plan import MesDelPlan

# ═══════════════════════════════════════════════════════════
# Las cifras del mes: qué entra, qué hay, qué se debe y si alcanza
#
# Las dibujan el Dashboard y Pagos del mes, y tienen que decir lo
# mismo en las dos: por eso viven aquí y no en cada página. Cada
# cifra lleva su desglose a un clic, sacado de lo mismo que la
# calcula, para que siempre cuadren.
# ═══════════════════════════════════════════════════════════


def nombre_mes(mes: date) -> str:
    """«septiembre 2026»."""
    return f"{MESES_ES[mes.month - 1]} {mes.year}"


COLUMNAS_MOVIMIENTO = {
    "id": None,
    "estado": None,
    "cuenta_como": None,
    "ingreso_fijo": None,
    "fecha": st.column_config.DateColumn("Fecha", format="DD/MM"),
    "descripcion": st.column_config.TextColumn("Descripción", width="medium"),
    "categoria": st.column_config.TextColumn("Categoría"),
    "cuenta": st.column_config.TextColumn("Cuenta"),
    "monto": st.column_config.NumberColumn("Monto", format="$%.2f"),
}
COLUMNAS_INGRESO_FIJO = {
    "id": None,
    "movimiento_id": None,
    "concepto": st.column_config.TextColumn("Concepto", width="medium"),
    "fecha": st.column_config.DateColumn("Día", format="DD/MM"),
    "esperado": st.column_config.NumberColumn("Esperado", format="$%.2f"),
    "recibido": st.column_config.NumberColumn("Llegó", format="$%.2f"),
    "monto": st.column_config.NumberColumn("Cuenta", format="$%.2f"),
    "estado": st.column_config.TextColumn("Estado"),
    "cuenta": st.column_config.TextColumn("A la cuenta"),
}


#: Alto de cada cifra, en píxeles. Fijo y el mismo que las tarjetas de
#: cuentas del Dashboard: sin él, una cifra con línea de detalle y otra
#: sin ella miden distinto, y el renglón se ve disparejo.
ALTO_CIFRA = 92


def _desglose(clave: str, etiqueta: str):
    """El botón que abre el desglose de una cifra, a lo ancho de su tarjeta."""
    return st.popover(
        "¿De dónde sale?",
        icon=":material/manage_search:",
        type="tertiary",
        width="stretch",
        key=f"desglose_{clave}_{etiqueta}",
    )


def cifras_del_mes(
    mes: date, clave: str = "pagos", plan_mes: MesDelPlan | None = None
) -> tuple[ResumenPagos, pd.DataFrame]:
    """
    Dibuja las cuatro cifras del mes con su desglose, y los avisos.

    Parameters
    ----------
    mes : date
        Cualquier día del mes: decide los cargos fijos y el ingreso. Lo
        disponible, lo exigible y lo sin pagar son siempre de hoy.
    clave : str
        Distingue los widgets si la misma página las dibujara dos veces.
    plan_mes : MesDelPlan, optional
        El mes del plan de pagos. Con él, «Por pagar» y «Queda después de
        pagar» siguen las decisiones del plan —lo pospuesto sale de ahí— y
        lo dicen; sin él, son las cifras reales. El Dashboard no lo pasa:
        allí sólo va lo real.

    Returns
    -------
    tuple
        El resumen y la lista de lo que hay que pagar, por si la página
        los necesita para algo más.
    """
    servicios = obtener_servicios()
    nombre = nombre_mes(mes)
    resumen = servicios.pagos.resumen(mes)
    pendientes = servicios.pagos.por_pagar(mes)
    ingresos = servicios.pagos.ingresos(mes)
    ingresos_fijos = servicios.pagos.ingresos_fijos_del_mes(mes)
    liquidos = servicios.patrimonio.activos_liquidos_detalle()

    tarjetas = st.columns(4)

    with tarjetas[0], st.container(border=True):
        st.metric(
            "Ingreso del mes",
            moneda(resumen.ingresos),
            delta=(
                f"{moneda(resumen.por_recibir)} aún por recibir"
                if resumen.por_recibir
                else "todo recibido"
            ),
            delta_color="off",
            delta_arrow="off",
            height=ALTO_CIFRA,
        )
        with _desglose(clave, "ingreso"):
            st.markdown(f"**Ingreso de {nombre}**")

            st.markdown("**Ingresos fijos**")
            if ingresos_fijos.empty:
                st.caption(
                    "No tienes ingresos fijos. Da de alta tu nómina en **Pagos del "
                    "mes → Ingresos fijos** para que cuente aunque no la hayas "
                    "registrado."
                )
            else:
                st.dataframe(
                    ingresos_fijos, hide_index=True, column_config=COLUMNAS_INGRESO_FIJO
                )
                st.caption(
                    "Cuentan siempre: con lo que llegó si ya lo registraste, con lo "
                    "esperado si no."
                )

            otros = ingresos[ingresos["cuenta_como"] == COMO_OTRO]
            if not otros.empty:
                st.markdown("**Otros ingresos registrados**")
                st.dataframe(otros, hide_index=True, column_config=COLUMNAS_MOVIMIENTO)

            pendientes_registrados = ingresos[ingresos["cuenta_como"] == COMO_PENDIENTE]
            if not pendientes_registrados.empty:
                st.markdown("**Registrados como pendientes**")
                st.dataframe(
                    pendientes_registrados,
                    hide_index=True,
                    column_config=COLUMNAS_MOVIMIENTO,
                )

            st.caption(
                f"Llegó **{moneda(resumen.recibido, decimales=2)}** y falta "
                f"**{moneda(resumen.por_recibir, decimales=2)}**: "
                f"**{moneda(resumen.ingresos, decimales=2)}** en el mes."
            )

            excluidos = ingresos[ingresos["cuenta_como"] == COMO_RESTRINGIDA]
            if not excluidos.empty:
                st.markdown("**No cuentan**")
                st.dataframe(
                    excluidos, hide_index=True, column_config=COLUMNAS_MOVIMIENTO
                )
                st.caption(
                    "Entraron a una cuenta restringida: son tuyos, pero no se pueden "
                    "usar para pagar hasta retirarlos."
                )

    with tarjetas[1], st.container(border=True):
        con_saldo = int((liquidos["monto"] > 0).sum()) if not liquidos.empty else 0
        st.metric(
            "Disponible hoy",
            moneda(resumen.disponible),
            delta=f"en {con_saldo} cuenta{'s' if con_saldo != 1 else ''}",
            delta_color="off",
            delta_arrow="off",
            height=ALTO_CIFRA,
        )
        with _desglose(clave, "disponible"):
            st.markdown("**Lo que puedes usar hoy para pagar**")
            st.caption("Efectivo, débito y ahorro, más los bienes de liquidez alta.")
            if liquidos.empty:
                st.caption("No hay cuentas líquidas.")
            else:
                st.dataframe(
                    liquidos,
                    hide_index=True,
                    column_config={
                        "concepto": st.column_config.TextColumn("Cuenta"),
                        "tipo": st.column_config.TextColumn("Tipo"),
                        "monto": st.column_config.NumberColumn("Saldo", format="$%.2f"),
                        "nota": st.column_config.TextColumn("Nota", width="medium"),
                    },
                )
                st.caption(f"Suman **{moneda(resumen.disponible, decimales=2)}**.")

    with tarjetas[2], st.container(border=True):
        vencido = (
            float(
                pendientes.loc[
                    pendientes["estado"] == str(EstadoPago.VENCIDO), "monto"
                ].sum()
            )
            if not pendientes.empty
            else 0.0
        )
        if plan_mes is not None:
            st.metric(
                "Por pagar",
                moneda(plan_mes.a_pagar),
                delta=f"con tu plan · sin él {moneda(resumen.por_pagar)}",
                delta_color="off",
                delta_arrow="off",
                height=ALTO_CIFRA,
                help=(
                    "Lo que vas a pagar este mes según tus decisiones del plan: "
                    "lo que pospones no cuenta."
                ),
            )
        else:
            st.metric(
                "Por pagar",
                moneda(resumen.por_pagar),
                # Lo vencido en rojo; si no hay, cuántos pagos son, en gris.
                delta=(
                    f"{moneda(vencido)} vencido"
                    if vencido
                    else f"{len(pendientes)} pago{'s' if len(pendientes) != 1 else ''}"
                ),
                delta_color="inverse" if vencido else "off",
                delta_arrow="off",
                height=ALTO_CIFRA,
            )
        with _desglose(clave, "por_pagar"):
            if plan_mes is not None:
                st.markdown("**Con tu plan**")
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "concepto": r.obligacion.concepto,
                                "monto": r.obligacion.monto,
                                "pagas": round(r.pagado + r.falta, 2),
                                "pospones": r.pospuesto,
                            }
                            for r in plan_mes.resoluciones
                        ],
                        columns=["concepto", "monto", "pagas", "pospones"],
                    ),
                    hide_index=True,
                    column_config={
                        "concepto": st.column_config.TextColumn(
                            "Concepto", width="medium"
                        ),
                        "monto": st.column_config.NumberColumn("Monto", format="$%.2f"),
                        "pagas": st.column_config.NumberColumn(
                            "Vas a pagar", format="$%.2f"
                        ),
                        "pospones": st.column_config.NumberColumn(
                            "Pospones", format="$%.2f"
                        ),
                    },
                )
                st.caption(
                    f"Vas a pagar **{moneda(plan_mes.a_pagar, decimales=2)}** y "
                    f"pospones {moneda(plan_mes.pospuesto, decimales=2)}. Se "
                    "cambia en **Plan hasta diciembre**."
                )
                st.markdown("**Sin plan: todo lo que toca pagar**")
            else:
                st.markdown("**Todo lo que ya toca pagar**")
            st.markdown(
                f"- Gastos fijos del mes sin pagar: "
                f"**{moneda(resumen.fijos_pendientes, decimales=2)}**\n"
                f"- Exigible de tus deudas: "
                f"**{moneda(resumen.exigible, decimales=2)}**\n"
                f"- Resto del saldo de tus tarjetas: "
                f"**{moneda(resumen.saldo_tarjetas, decimales=2)}**\n"
                f"- Gastos registrados sin pagar: "
                f"**{moneda(resumen.sin_pagar, decimales=2)}**"
            )
            if not pendientes.empty:
                st.dataframe(
                    pendientes,
                    hide_index=True,
                    column_config={
                        "concepto": st.column_config.TextColumn(
                            "Concepto", width="medium"
                        ),
                        "origen": st.column_config.TextColumn("De dónde viene"),
                        "cuenta": st.column_config.TextColumn("Cuenta"),
                        "monto": st.column_config.NumberColumn("Monto", format="$%.2f"),
                        "fecha_limite": st.column_config.DateColumn(
                            "Límite", format="DD/MM"
                        ),
                        "estado": st.column_config.TextColumn("Estado"),
                    },
                )
            st.caption(
                f"Suman **{moneda(resumen.por_pagar, decimales=2)}**. Nada se cuenta "
                "dos veces: de una tarjeta, lo exigible y el resto suman su saldo; "
                "un gasto fijo registrado y sin pagar va sólo como gasto sin pagar."
            )

    queda = plan_mes.balance if plan_mes is not None else resumen.queda
    with tarjetas[3], st.container(border=True):
        st.metric(
            "Queda después de pagar",
            moneda(queda),
            delta=(
                ("con tu plan · " if plan_mes is not None else "")
                + ("no alcanza" if queda < 0 else "alcanza")
            ),
            delta_color="normal" if queda >= 0 else "inverse",
            delta_arrow="off",
            height=ALTO_CIFRA,
        )
        with _desglose(clave, "queda"):
            if plan_mes is not None:
                st.markdown("**Con tu plan**")
                calculo = [
                    ("Disponible hoy", plan_mes.efectivo_inicial),
                    ("+ Entra este mes", plan_mes.ingresos),
                    ("− Vas a pagar", plan_mes.a_pagar),
                ]
                st.markdown(
                    "| | |\n|---|---:|\n"
                    + "".join(
                        f"| {concepto} | {moneda(valor, decimales=2)} |\n"
                        for concepto, valor in calculo
                    )
                    + f"| **= Queda** | **{moneda(plan_mes.balance, decimales=2)}** |"
                )
                st.caption(
                    "Cuenta lo que entra este mes —la nómina que falta y los "
                    "retiros que simulaste— y deja fuera lo que pospones."
                )
                st.markdown("**Sin plan**")
            filas_calculo = [
                ("Disponible hoy", resumen.disponible),
                ("− Gastos fijos sin pagar", resumen.fijos_pendientes),
                ("− Exigible de deudas", resumen.exigible),
                ("− Resto del saldo de tarjetas", resumen.saldo_tarjetas),
                ("− Gastos sin pagar", resumen.sin_pagar),
            ]
            st.markdown(
                "| | |\n|---|---:|\n"
                + "".join(
                    f"| {concepto} | {moneda(valor, decimales=2)} |\n"
                    for concepto, valor in filas_calculo
                )
                + f"| **= Queda** | **{moneda(resumen.queda, decimales=2)}** |"
            )
            st.caption(
                "No cuenta el ingreso que falta por recibir"
                + (
                    f" ({moneda(resumen.por_recibir, decimales=2)}); con él "
                    "quedarían "
                    f"{moneda(resumen.queda + resumen.por_recibir, decimales=2)}."
                    if resumen.por_recibir
                    else "."
                )
            )

    vencidos = (
        pendientes[pendientes["estado"] == str(EstadoPago.VENCIDO)]
        if not pendientes.empty
        else pendientes
    )
    if not vencidos.empty:
        st.error(
            f"{len(vencidos)} pagos vencidos por "
            f"{moneda(float(vencidos['monto'].sum()))}.",
            icon=":material/error:",
        )
    if plan_mes is not None:
        if plan_mes.balance < 0:
            st.warning(
                f"Con tu plan te faltan {moneda(-plan_mes.balance)} este mes: "
                "quedan como deuda nueva para el mes siguiente. Cámbialo en "
                "**Plan hasta diciembre**.",
                icon=":material/account_balance_wallet:",
            )
    elif resumen.queda < 0:
        faltan = -resumen.queda
        alcance = (
            f" Con lo que falta por recibir ({moneda(resumen.por_recibir)}) "
            "sí alcanzaría."
            if resumen.por_recibir >= faltan
            else ""
        )
        st.warning(
            f"Con lo disponible hoy te faltan {moneda(faltan)} para cubrir todo lo "
            f"por pagar.{alcance}",
            icon=":material/account_balance_wallet:",
        )

    return resumen, pendientes
