from __future__ import annotations

import streamlit as st

from finanzas.application.app.components import (
    grafico_barras,
    grafico_calendario,
    moneda,
    obtener_servicios,
    porcentaje,
    selector_periodo,
    sin_datos,
    tabla_equivalente,
    tablero_del_periodo,
)

# ═══════════════════════════════════════════════════════════
# Análisis: los cortes que explican el número del dashboard
# ═══════════════════════════════════════════════════════════

servicios = obtener_servicios()
periodo = selector_periodo()
tablero = tablero_del_periodo(periodo)

st.title("Análisis")
st.caption(
    f"Todo lo registrado · {tablero.meses} meses con datos · desglose del gasto"
    if tablero.es_historico
    else f"{tablero.etiqueta} · desglose del gasto del periodo"
)

if not tablero.hay_datos:
    sin_datos()
    st.stop()

movimientos = tablero.movimientos
reglas = tablero.reglas

mezcla_tab, detalle_tab, proyectos_tab, calendario_tab, fugas_tab = st.tabs(
    ["Mezcla del gasto", "Detalle", "Proyectos", "Calendario", "Fugas"]
)


# ═══════════════════════════════════════════════════════════
# Mezcla del gasto
# ═══════════════════════════════════════════════════════════

with mezcla_tab:
    columnas = st.columns(3)

    cortes = (
        ("necesidad", "Esencial contra deseo", "Necesidad"),
        ("naturaleza", "Fijo contra variable", "Naturaleza"),
        ("medio_pago", "Por medio de pago", "Medio de pago"),
    )

    for columna, (dimension, titulo, etiqueta) in zip(columnas, cortes, strict=True):
        datos = servicios.analytics.mezcla(movimientos, dimension)

        with columna, st.container(border=True):
            st.subheader(titulo)

            if datos.empty:
                st.caption("Sin gasto que repartir.")
                continue

            st.altair_chart(
                grafico_barras(
                    datos,
                    dimension=dimension,
                    medida="gasto",
                    titulo_dimension=etiqueta,
                    titulo_medida="Gasto",
                )
            )
            tabla_equivalente(
                datos.rename(
                    columns={
                        dimension: etiqueta,
                        "gasto": "Gasto",
                        "proporcion": "Proporción",
                    }
                )
            )

    esencial = tablero.resumen.pct_esencial
    deseos = tablero.resumen.pct_deseos

    if deseos > reglas.max_deseos:
        st.warning(
            f"El gasto discrecional fue {porcentaje(deseos, 1)}, por encima de tu "
            f"techo de {porcentaje(reglas.max_deseos, 0)}.",
            icon=":material/warning:",
        )
    else:
        st.success(
            f"El gasto discrecional fue {porcentaje(deseos, 1)}, dentro de tu "
            f"techo de {porcentaje(reglas.max_deseos, 0)}. "
            f"El esencial cubrió {porcentaje(esencial, 1)}.",
            icon=":material/check_circle:",
        )


# ═══════════════════════════════════════════════════════════
# Detalle
# ═══════════════════════════════════════════════════════════

with detalle_tab:
    izquierda, derecha = st.columns(2)

    with izquierda, st.container(border=True):
        st.subheader("Subcategorías con más gasto")
        subcategorias = servicios.analytics.por_subcategoria(movimientos, top=12)

        if subcategorias.empty:
            st.caption("Sin gasto registrado.")
        else:
            st.altair_chart(
                grafico_barras(
                    subcategorias,
                    dimension="subcategoria",
                    medida="gasto",
                    titulo_dimension="Subcategoría",
                    titulo_medida="Gasto",
                )
            )
            tabla_equivalente(
                subcategorias.rename(
                    columns={
                        "categoria": "Categoría",
                        "subcategoria": "Subcategoría",
                        "gasto": "Gasto",
                    }
                )
            )

    with derecha, st.container(border=True):
        st.subheader("Flujo por cuenta")
        cuentas = servicios.analytics.por_cuenta(movimientos)

        if cuentas.empty:
            st.caption("Sin movimientos por cuenta.")
        else:
            st.dataframe(
                cuentas,
                hide_index=True,
                column_config={
                    "cuenta": st.column_config.TextColumn("Cuenta"),
                    "entradas": st.column_config.NumberColumn(
                        "Entradas", format="$%.2f"
                    ),
                    "salidas": st.column_config.NumberColumn("Salidas", format="$%.2f"),
                    "flujo_neto": st.column_config.NumberColumn(
                        "Flujo neto", format="$%.2f"
                    ),
                },
            )


# ═══════════════════════════════════════════════════════════
# Proyectos
#
# La categoría dice qué clase de gasto fue; el proyecto, para
# qué esfuerzo se hizo. Un viaje cruza transporte, comida y
# hospedaje, y sólo agrupándolo se sabe cuánto costó.
#
# A diferencia del resto de la página, no se limita al periodo
# elegido: un proyecto dura lo que dura.
# ═══════════════════════════════════════════════════════════

with proyectos_tab:
    proyectos = servicios.movimientos.proyectos()

    if proyectos.empty:
        st.info(
            "Todavía no hay movimientos con proyecto. Asigna uno al capturar "
            "en **Movimientos**, o desde el detalle de cualquier movimiento "
            "ya registrado.",
            icon=":material/info:",
        )
    else:
        st.caption(
            "Los proyectos cruzan categorías y periodos: aquí se suma todo su "
            "historial, no sólo el mes seleccionado."
        )

        with st.container(horizontal=True):
            st.metric("Proyectos", len(proyectos), border=True)
            st.metric(
                "Gasto acumulado",
                moneda(float(proyectos["gasto"].sum())),
                border=True,
            )
            deuda = float(proyectos["por_pagar"].sum())
            if deuda:
                st.metric(
                    "Por pagar",
                    moneda(deuda),
                    border=True,
                    help="Gasto de proyectos que aún no sale de la caja.",
                )

        with st.container(border=True):
            st.subheader("Resumen por proyecto")
            st.dataframe(
                proyectos,
                hide_index=True,
                column_config={
                    "proyecto": st.column_config.TextColumn("Proyecto", pinned=True),
                    "movimientos": st.column_config.NumberColumn("Movs."),
                    "desde": st.column_config.DateColumn("Desde", format="DD/MM/YYYY"),
                    "hasta": st.column_config.DateColumn("Hasta", format="DD/MM/YYYY"),
                    "gasto": st.column_config.NumberColumn("Gasto", format="$%.2f"),
                    "ingreso": st.column_config.NumberColumn("Ingreso", format="$%.2f"),
                    "ahorro_inversion": st.column_config.NumberColumn(
                        "Ahorro e inversión", format="$%.2f"
                    ),
                    "por_pagar": st.column_config.NumberColumn(
                        "Por pagar", format="$%.2f"
                    ),
                    "neto": st.column_config.NumberColumn(
                        "Neto",
                        format="$%.2f",
                        help="Ingreso menos gasto del proyecto.",
                    ),
                },
            )

        con_gasto = proyectos[proyectos["gasto"] > 0]
        if len(con_gasto) > 1:
            with st.container(border=True):
                st.subheader("Gasto por proyecto")
                st.altair_chart(
                    grafico_barras(
                        con_gasto,
                        dimension="proyecto",
                        medida="gasto",
                        titulo_dimension="Proyecto",
                        titulo_medida="Gasto",
                    )
                )

        with st.container(border=True):
            st.subheader("Movimientos de un proyecto")
            elegido = st.selectbox("Proyecto", proyectos["proyecto"].tolist())
            detalle = servicios.movimientos.buscar(proyectos=[elegido])

            st.dataframe(
                detalle[
                    [
                        "fecha",
                        "tipo",
                        "categoria",
                        "descripcion",
                        "cuenta",
                        "monto",
                        "fecha_pago",
                    ]
                ],
                hide_index=True,
                column_config={
                    "fecha": st.column_config.DateColumn("Fecha", format="DD/MM/YYYY"),
                    "tipo": st.column_config.TextColumn("Tipo"),
                    "categoria": st.column_config.TextColumn("Categoría"),
                    "descripcion": st.column_config.TextColumn(
                        "Descripción", width="medium"
                    ),
                    "cuenta": st.column_config.TextColumn("Cuenta"),
                    "monto": st.column_config.NumberColumn("Monto", format="$%.2f"),
                    "fecha_pago": st.column_config.DateColumn(
                        "Pagado el", format="DD/MM/YYYY"
                    ),
                },
            )


# ═══════════════════════════════════════════════════════════
# Calendario
# ═══════════════════════════════════════════════════════════

with calendario_tab:
    calendario = servicios.analytics.calendario(movimientos, periodo)
    dias_sin_gasto = int(calendario["sin_gasto"].sum())
    gasto_maximo = calendario.loc[calendario["gasto"].idxmax()]

    with st.container(horizontal=True):
        st.metric("Días sin gasto", dias_sin_gasto, border=True)
        st.metric(
            "Día de mayor gasto",
            f"día {int(gasto_maximo['dia'])}",
            delta=moneda(float(gasto_maximo["gasto"])),
            delta_color="off",
            border=True,
        )
        st.metric(
            "Gasto promedio diario",
            moneda(float(calendario["gasto"].mean())),
            border=True,
        )

    with st.container(border=True):
        st.subheader(
            "Gasto promedio por día del mes"
            if tablero.es_historico
            else "Gasto por día del mes"
        )
        st.altair_chart(grafico_calendario(calendario))
        tabla_equivalente(
            calendario[["dia", "gasto", "sin_gasto"]].rename(
                columns={
                    "dia": "Día",
                    "gasto": "Gasto",
                    "sin_gasto": "Sin gasto",
                }
            )
        )


# ═══════════════════════════════════════════════════════════
# Fugas
# ═══════════════════════════════════════════════════════════

with fugas_tab:
    fugas = tablero.fugas

    with st.container(horizontal=True):
        st.metric(
            "Microgastos",
            moneda(fugas["microgastos_monto"]),
            delta=f"{fugas['microgastos_cantidad']} movimientos",
            delta_color="off",
            border=True,
            help=f"Gastos de hasta {moneda(reglas.umbral_gasto_pequeno)}.",
        )
        st.metric(
            "Gasto no planeado",
            moneda(fugas["gasto_no_planeado"]),
            border=True,
        )
        st.metric(
            "Deseos no planeados",
            moneda(fugas["deseos_no_planeados"]),
            border=True,
            help="La bolsa más fácil de recortar sin afectar lo indispensable.",
        )
        st.metric(
            "Suscripciones al mes",
            moneda(tablero.resumen.suscripciones_mensuales),
            border=True,
        )

    with st.container(border=True):
        st.subheader("Movimientos detrás de las fugas")

        candidatos = movimientos[
            (movimientos["gasto_real"] > 0)
            & (
                (~movimientos["planeado"])
                | (movimientos["monto"] <= reglas.umbral_gasto_pequeno)
            )
        ].sort_values("monto", ascending=False)

        if candidatos.empty:
            st.success(
                "Todo el gasto del periodo estaba planeado y por encima del "
                "umbral de microgasto.",
                icon=":material/check_circle:",
            )
        else:
            st.dataframe(
                candidatos[
                    [
                        "fecha",
                        "categoria",
                        "subcategoria",
                        "descripcion",
                        "monto",
                        "necesidad",
                        "planeado",
                    ]
                ],
                hide_index=True,
                column_config={
                    "fecha": st.column_config.DateColumn("Fecha", format="DD/MM/YYYY"),
                    "categoria": st.column_config.TextColumn("Categoría"),
                    "subcategoria": st.column_config.TextColumn("Subcategoría"),
                    "descripcion": st.column_config.TextColumn(
                        "Descripción", width="medium"
                    ),
                    "monto": st.column_config.NumberColumn("Monto", format="$%.2f"),
                    "necesidad": st.column_config.TextColumn("Necesidad"),
                    "planeado": st.column_config.CheckboxColumn("Planeado"),
                },
            )
