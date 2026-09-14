from __future__ import annotations

import streamlit as st

from finanzas.application.app.components import (
    cargar_tablero,
    grafico_barras,
    grafico_tendencia,
    insignia_estado,
    moneda,
    obtener_servicios,
    porcentaje,
    selector_periodo,
    sin_datos,
    tabla_equivalente,
    version_datos,
)
from finanzas.application.app.theme import rotulo

# ═══════════════════════════════════════════════════════════
# Dashboard: el estado del periodo en una pantalla
# ═══════════════════════════════════════════════════════════

periodo = selector_periodo()
tablero = cargar_tablero(periodo, version_datos())
reglas = tablero.reglas
simbolo = "$" if reglas.moneda in ("MXN", "USD") else ""

st.title("Dashboard financiero")
st.caption(f"Periodo analizado: {tablero.etiqueta} · moneda {reglas.moneda}")

if not tablero.hay_datos:
    sin_datos()
    st.stop()

resumen = tablero.resumen

# ── Primera fila de indicadores ──────────────────────────

rotulo("Resultado del periodo")

tendencia = tablero.tendencia
serie_ingresos = tendencia["ingresos"].tolist()
serie_gastos = tendencia["gastos"].tolist()
serie_ahorro = tendencia["ahorro_inversion"].tolist()

with st.container(horizontal=True):
    st.metric(
        "Ingresos",
        moneda(resumen.ingresos, simbolo),
        border=True,
        chart_data=serie_ingresos,
        chart_type="line",
    )
    st.metric(
        "Gastos",
        moneda(resumen.gastos, simbolo),
        border=True,
        chart_data=serie_gastos,
        chart_type="line",
    )
    st.metric(
        "Ahorro e inversión",
        moneda(resumen.ahorro_inversion, simbolo),
        border=True,
        chart_data=serie_ahorro,
        chart_type="line",
    )
    st.metric(
        "Disponible tras metas",
        moneda(resumen.disponible, simbolo),
        delta=f"{porcentaje(resumen.tasa_ahorro, 1)} de tasa de ahorro",
        border=True,
    )
    st.metric(
        "Score financiero",
        f"{tablero.score.total} / 100",
        border=True,
        help="35 ahorro · 25 presupuesto · 25 fondo de emergencia · 15 deseos",
    )

# ── Segunda fila ─────────────────────────────────────────

rotulo("Salud financiera")

meta_meses = reglas.meses_fondo_emergencia

with st.container(horizontal=True):
    st.metric(
        "Presupuesto utilizado",
        porcentaje(resumen.presupuesto_utilizado, 1),
        delta=f"{tablero.categorias_excedidas} categorías excedidas",
        delta_color="inverse",
        border=True,
    )
    st.metric(
        "Gasto esencial",
        porcentaje(resumen.pct_esencial, 1),
        delta=(
            f"deseos: {porcentaje(resumen.pct_deseos, 1)} · "
            f"techo {porcentaje(reglas.max_deseos, 0)}"
        ),
        delta_color="off",
        border=True,
    )
    st.metric(
        "Suscripciones al mes",
        moneda(resumen.suscripciones_mensuales, simbolo),
        border=True,
    )
    st.metric(
        "Patrimonio neto",
        moneda(resumen.patrimonio_neto, simbolo),
        border=True,
    )
    st.metric(
        "Fondo de emergencia",
        f"{resumen.meses_fondo_emergencia:.1f} meses",
        delta=f"objetivo: {meta_meses} meses",
        delta_color="off",
        border=True,
    )

# ── Tendencia y composición del gasto ────────────────────

rotulo("Evolución")

izquierda, derecha = st.columns([3, 2])

with izquierda:
    with st.container(border=True):
        st.subheader("Tendencia de 12 meses")
        st.altair_chart(grafico_tendencia(tendencia))
        tabla_equivalente(
            tendencia[
                ["etiqueta", "ingresos", "gastos", "ahorro_inversion", "disponible"]
            ].rename(
                columns={
                    "etiqueta": "Mes",
                    "ingresos": "Ingresos",
                    "gastos": "Gastos",
                    "ahorro_inversion": "Ahorro e inversión",
                    "disponible": "Disponible",
                }
            )
        )

with derecha:
    with st.container(border=True):
        st.subheader("Gasto por categoría")
        if tablero.por_categoria.empty:
            st.caption("Sin gasto registrado en el periodo.")
        else:
            st.altair_chart(
                grafico_barras(
                    tablero.por_categoria.head(8),
                    dimension="categoria",
                    medida="gasto",
                    titulo_dimension="Categoría",
                    titulo_medida="Gasto",
                )
            )
            tabla_equivalente(
                tablero.por_categoria.rename(
                    columns={
                        "categoria": "Categoría",
                        "gasto": "Gasto",
                        "pct_del_total": "% del total",
                    }
                )
            )

# ── Presupuesto y lectura rápida ─────────────────────────

rotulo("Control")

izquierda, derecha = st.columns([3, 2])

with izquierda:
    with st.container(border=True):
        st.subheader("Semáforo del presupuesto")
        presupuesto = tablero.presupuesto
        activo = presupuesto[
            (presupuesto["presupuesto_activo"] > 0) | (presupuesto["gasto_del_mes"] > 0)
        ]

        if activo.empty:
            st.caption("Aún no hay presupuesto configurado para este periodo.")
        else:
            st.dataframe(
                activo[
                    [
                        "categoria",
                        "presupuesto_activo",
                        "gasto_del_mes",
                        "disponible",
                        "pct_usado",
                        "estado",
                    ]
                ],
                hide_index=True,
                column_config={
                    "categoria": st.column_config.TextColumn("Categoría"),
                    "presupuesto_activo": st.column_config.NumberColumn(
                        "Presupuesto", format="$%.0f"
                    ),
                    "gasto_del_mes": st.column_config.NumberColumn(
                        "Gasto", format="$%.0f"
                    ),
                    "disponible": st.column_config.NumberColumn(
                        "Disponible", format="$%.0f"
                    ),
                    "pct_usado": st.column_config.ProgressColumn(
                        "% usado", format="percent", min_value=0, max_value=1.5
                    ),
                    "estado": st.column_config.TextColumn("Estado"),
                },
            )

with derecha:
    with st.container(border=True):
        st.subheader("Lectura rápida")

        mayor = tablero.categoria_mayor_gasto
        if mayor is not None:
            st.markdown(
                f"**Mayor categoría de gasto** — {mayor[0]}: "
                f"{moneda(mayor[1], simbolo)}"
            )

        fugas = tablero.fugas
        st.markdown(
            f"**Oportunidad inmediata** — deseos no planeados: "
            f"{moneda(fugas['deseos_no_planeados'], simbolo)} · "
            f"microgastos: {moneda(fugas['microgastos_monto'], simbolo)} "
            f"({fugas['microgastos_cantidad']})"
        )

        estado_presupuesto = "Excedido" if tablero.categorias_excedidas else "En orden"
        st.markdown(
            f"**Presupuesto** — {insignia_estado(estado_presupuesto)}: "
            f"{tablero.categorias_excedidas} categorías excedidas"
        )

        st.divider()
        st.markdown(f"**Siguiente mejor acción**  \n{tablero.accion_sugerida}")

        with st.expander("Cómo se compone el score", icon=":material/scoreboard:"):
            st.dataframe(
                tablero.score.desglose,
                hide_index=True,
                column_config={
                    "componente": st.column_config.TextColumn("Componente"),
                    "puntos": st.column_config.NumberColumn("Puntos", format="%.1f"),
                    "maximo": st.column_config.NumberColumn("Máximo"),
                },
            )

# ── Últimos movimientos ──────────────────────────────────

rotulo("Actividad reciente")

with st.container(border=True):
    st.subheader("Últimos movimientos del periodo")
    servicios = obtener_servicios()
    ultimos = tablero.movimientos.sort_values("fecha", ascending=False).head(10)

    st.dataframe(
        ultimos[
            ["fecha", "tipo", "categoria", "descripcion", "cuenta", "monto", "estado"]
        ],
        hide_index=True,
        column_config={
            "fecha": st.column_config.DateColumn("Fecha", format="DD MMM YYYY"),
            "tipo": st.column_config.TextColumn("Tipo"),
            "categoria": st.column_config.TextColumn("Categoría"),
            "descripcion": st.column_config.TextColumn("Descripción", width="medium"),
            "cuenta": st.column_config.TextColumn("Cuenta"),
            "monto": st.column_config.NumberColumn("Monto", format="$%.2f"),
            "estado": st.column_config.TextColumn("Estado"),
        },
    )
