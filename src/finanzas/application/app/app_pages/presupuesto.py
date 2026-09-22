from __future__ import annotations

from datetime import date

import streamlit as st

from finanzas.analytics.aggregations import es_historico, etiqueta_periodo, periodo_de
from finanzas.application.app.components import (
    grafico_presupuesto,
    invalidar_datos,
    moneda,
    obtener_servicios,
    reportar_error,
    selector_periodo,
    version_datos,
)

# ═══════════════════════════════════════════════════════════
# Presupuesto: promedio real como punto de partida
#
# Sólo dos columnas son editables. El resto se recalcula
# desde los movimientos, así que no puede quedar desfasado.
# ═══════════════════════════════════════════════════════════

servicios = obtener_servicios()
elegido = selector_periodo()
reglas = servicios.catalogos.reglas()

# El presupuesto es mensual por naturaleza. En el histórico se edita el
# del mes en curso y se compara contra el gasto mensual promedio de todos
# los meses con datos; en un mes concreto, contra el gasto de ese mes.
historico = es_historico(elegido)
periodo = periodo_de(date.today()) if historico else elegido

st.title("Presupuesto")
if historico:
    st.caption(
        f"Presupuesto de {etiqueta_periodo(periodo)} frente al gasto mensual "
        "promedio de todo lo registrado. El presupuesto activo es tu monto "
        "manual; si lo dejas en cero, el promedio de los últimos tres meses "
        "menos el recorte."
    )
else:
    st.caption(
        f"{etiqueta_periodo(periodo)} · el presupuesto activo es tu monto manual "
        "cuando lo capturas; si lo dejas en cero, se usa el promedio de los "
        "últimos tres meses menos el recorte."
    )

if historico:
    todos = servicios.movimientos.buscar()
    meses = int(todos["periodo"].nunique()) if not todos.empty else 1
    tablero = servicios.presupuesto.tablero_historico(
        periodo, todos, meses, reglas.alerta_presupuesto
    )
else:
    tablero = servicios.presupuesto.tablero(periodo, reglas.alerta_presupuesto)

if tablero.empty:
    st.info(
        "No hay categorías de gasto activas. Revisa **Catálogos**.",
        icon=":material/info:",
    )
    st.stop()

# ── Encabezado ───────────────────────────────────────────

presupuesto_total = float(tablero["presupuesto_activo"].sum())
gasto_total = float(tablero["gasto_del_mes"].sum())
excedidas = int((tablero["estado"] == "Excedido").sum())
en_alerta = int((tablero["estado"] == "Atención").sum())

ETIQUETA_GASTO = "Gasto mensual promedio" if historico else "Gasto del mes"

with st.container(horizontal=True):
    st.metric("Presupuesto activo", moneda(presupuesto_total), border=True)
    st.metric(ETIQUETA_GASTO, moneda(gasto_total), border=True)
    st.metric(
        "Disponible",
        moneda(presupuesto_total - gasto_total),
        border=True,
    )
    st.metric(
        "Categorías excedidas",
        excedidas,
        delta=f"{en_alerta} en alerta",
        delta_color="inverse" if excedidas else "off",
        border=True,
    )

# ── Edición ──────────────────────────────────────────────

with st.container(border=True):
    st.subheader("Ajusta tu plan")
    st.caption(
        "Edita el **monto manual** para fijar un presupuesto, o el "
        "**% de recorte** para bajar el promedio histórico. Guarda al terminar."
    )

    editado = st.data_editor(
        tablero,
        hide_index=True,
        key=f"editor_presupuesto_{periodo}",
        disabled=[
            "categoria_id",
            "categoria",
            "promedio_3m",
            "presupuesto_activo",
            "gasto_del_mes",
            "disponible",
            "pct_usado",
            "estado",
        ],
        column_config={
            "categoria_id": None,
            "categoria": st.column_config.TextColumn("Categoría", pinned=True),
            "monto_manual": st.column_config.NumberColumn(
                "Monto manual",
                format="$%.0f",
                min_value=0.0,
                step=100.0,
                help="Cero significa: usa el promedio con recorte.",
            ),
            "pct_recorte": st.column_config.NumberColumn(
                "% recorte",
                format="percent",
                min_value=0.0,
                max_value=1.0,
                step=0.05,
            ),
            "promedio_3m": st.column_config.NumberColumn(
                "Promedio 3 meses", format="$%.0f"
            ),
            "presupuesto_activo": st.column_config.NumberColumn(
                "Presupuesto activo", format="$%.0f"
            ),
            "gasto_del_mes": st.column_config.NumberColumn(
                ETIQUETA_GASTO, format="$%.0f"
            ),
            "disponible": st.column_config.NumberColumn("Disponible", format="$%.0f"),
            "pct_usado": st.column_config.ProgressColumn(
                "% usado", format="percent", min_value=0.0, max_value=1.5
            ),
            "estado": st.column_config.TextColumn("Estado"),
        },
    )

    acciones = st.columns([1, 1, 2])

    with acciones[0]:
        if st.button("Guardar presupuesto", type="primary", icon=":material/save:"):
            try:
                guardadas = servicios.presupuesto.guardar(periodo, editado)
            except ValueError as error:
                reportar_error(error)
            else:
                invalidar_datos()
                st.success(
                    f"{guardadas} categorías guardadas para "
                    f"{etiqueta_periodo(periodo)}.",
                    icon=":material/check_circle:",
                )
                st.rerun()

    with acciones[1]:
        origenes = [
            candidato
            for candidato in servicios.presupuesto.periodos_con_presupuesto()
            if candidato != periodo
        ]
        if origenes:
            origen = st.selectbox(
                "Copiar desde",
                origenes,
                format_func=etiqueta_periodo,
                label_visibility="collapsed",
                key="origen_copia",
            )
            if st.button("Copiar plan", icon=":material/content_copy:"):
                try:
                    copiadas = servicios.presupuesto.copiar_desde(origen, periodo)
                except ValueError as error:
                    reportar_error(error)
                else:
                    invalidar_datos()
                    st.success(f"{copiadas} líneas copiadas.", icon=":material/check:")
                    st.rerun()

# ── Comparación ──────────────────────────────────────────

con_actividad = tablero[
    (tablero["presupuesto_activo"] > 0) | (tablero["gasto_del_mes"] > 0)
]

if not con_actividad.empty:
    with st.container(border=True):
        st.subheader("Presupuesto contra gasto real")
        st.altair_chart(grafico_presupuesto(con_actividad))

# ── Recordatorio del umbral ──────────────────────────────

st.caption(
    f"Una categoría pasa a **Atención** al llegar al "
    f"{reglas.alerta_presupuesto:.0%} de su presupuesto. "
    "Cambia el umbral en **Configuración**."
)

# `version_datos` se consulta para que la página se recalcule tras editar
# desde otra pantalla.
_ = version_datos()
