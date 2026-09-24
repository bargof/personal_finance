from __future__ import annotations

import streamlit as st

from finanzas.application.app.components import (
    grafico_barras,
    grafico_tendencia,
    insignia_estado,
    moneda,
    obtener_servicios,
    porcentaje,
    selector_periodo,
    sin_datos,
    tabla_equivalente,
    tablero_del_periodo,
)
from finanzas.application.app.theme import rotulo
from finanzas.domain.enums import TipoPatrimonio

# ═══════════════════════════════════════════════════════════
# Dashboard: el estado del periodo en una pantalla
# ═══════════════════════════════════════════════════════════

servicios = obtener_servicios()
periodo = selector_periodo()
tablero = tablero_del_periodo(periodo)
reglas = tablero.reglas
simbolo = "$" if reglas.moneda in ("MXN", "USD") else ""

st.title("Dashboard financiero")
if tablero.es_historico:
    st.caption(
        f"Todo lo registrado · {tablero.meses} meses con datos · moneda "
        f"{reglas.moneda}. El presupuesto se compara contra el gasto mensual "
        "promedio; elige un mes en la barra lateral para filtrar."
    )
else:
    st.caption(f"Periodo analizado: {tablero.etiqueta} · moneda {reglas.moneda}")


# ── Cuentas: cómo está cada una ──────────────────────────
#
# Va antes que el resultado del periodo y antes del corte por
# falta de datos: «cuánto tengo» es de hoy y no depende del mes que se
# esté mirando, ni deja de ser cierto en un mes sin movimientos.
#
# Los saldos no se capturan, se deducen; el detalle de cada uno —de qué
# saldo verificado sale, cuántos movimientos median— vive en Patrimonio.

rotulo("Tus cuentas hoy")

saldos = servicios.patrimonio.saldos()
flujo = servicios.movimientos.flujo_por_cuenta(
    None if tablero.es_historico else periodo
)
movido = (
    dict(zip(flujo["cuenta"], flujo["flujo_neto"], strict=False))
    if not flujo.empty
    else {}
)
cuando = "en total" if tablero.es_historico else f"en {tablero.etiqueta}"

#: Medidas de cada tarjeta de cuenta, en píxeles. Fijas y no repartidas:
#: con una docena de cuentas, repartir el ancho deja cada tarjeta tan
#: estrecha que el importe sale cortado. Así envuelven en varias filas,
#: se leen enteras y todas miden lo mismo, tengan delta o no.
ANCHO_TARJETA = 170
ALTO_TARJETA = 92

st.caption(
    f"Saldos deducidos al día de hoy. Debajo de cada uno, lo que se movió {cuando}."
)

if saldos.empty:
    st.info(
        "Aún no hay cuentas. Da de alta las tuyas en **Catálogos**.",
        icon=":material/info:",
    )
else:
    # El lado lo decide el tipo de cuenta, no el signo: un préstamo ya
    # liquidado sigue siendo una deuda, con cero, y una cuenta de débito
    # en números rojos es una cuenta a la que le falta un saldo
    # verificado, no un préstamo.
    tienes = saldos[saldos["lado"] == str(TipoPatrimonio.ACTIVO)]
    debes = saldos[saldos["lado"] == str(TipoPatrimonio.PASIVO)]
    deuda_en_cuentas = (
        float(debes["saldo_visto"].clip(lower=0).sum()) if not debes.empty else 0.0
    )

    with st.container(horizontal=True):
        st.metric(
            "Disponible",
            moneda(servicios.patrimonio.activos_liquidos(), simbolo),
            border=True,
            height=ALTO_TARJETA,
            help="Efectivo, débito y ahorro: lo que puedes usar hoy mismo.",
        )
        st.metric(
            "Deuda en cuentas",
            moneda(deuda_en_cuentas, simbolo),
            border=True,
            height=ALTO_TARJETA,
            help="Lo que deben tus tarjetas y préstamos.",
        )
        st.metric(
            "Por pagar",
            moneda(servicios.movimientos.total_por_pagar(), simbolo),
            border=True,
            height=ALTO_TARJETA,
            help="Gasto ya hecho que todavía no sale de ninguna cuenta.",
        )

    if not tienes.empty:
        with st.container(horizontal=True):
            for fila in tienes.sort_values("saldo", ascending=False).itertuples():
                neto = round(float(movido.get(fila.cuenta, 0.0)), 2)
                pista = f"{fila.tipo}" + (
                    f" · {fila.institucion}" if fila.institucion else ""
                )
                if not fila.verificado:
                    pista += " · sin saldo verificado: se suma desde cero"
                st.metric(
                    fila.cuenta,
                    moneda(fila.saldo_visto, simbolo),
                    delta=f"{neto:+,.0f}" if neto else None,
                    border=True,
                    width=ANCHO_TARJETA,
                    height=ALTO_TARJETA,
                    help=pista,
                )

    if not debes.empty:
        with st.container(horizontal=True):
            for fila in debes.sort_values("saldo").itertuples():
                # En una deuda la tarjeta enseña lo que debes, así que el
                # delta es cuánto subió o bajó eso: entrar dinero a la
                # cuenta la baja. Con `inverse`, bajar sale en verde.
                neto = round(float(movido.get(fila.cuenta, 0.0)), 2)
                pista = f"{fila.tipo}" + (
                    f" · {fila.institucion}" if fila.institucion else ""
                )
                if not fila.verificado:
                    pista += " · sin saldo verificado: se suma desde cero"
                st.metric(
                    fila.cuenta,
                    moneda(fila.saldo_visto, simbolo),
                    delta=f"{-neto:+,.0f} de deuda" if neto else None,
                    delta_color="inverse",
                    border=True,
                    width=ANCHO_TARJETA,
                    height=ALTO_TARJETA,
                    help=pista,
                )

        st.caption(
            "Una tarjeta o un préstamo es una cuenta de deuda: el gasto cuenta "
            "una sola vez, al comprar, y lo que abonas después es un "
            "**traspaso** hacia ella, no otro gasto."
        )

    # Una cuenta sin saldo verificado se suma desde cero, que casi nunca es
    # verdad. Decir dónde se arregla vale más que marcarlo y callarse.
    sin_verificar = saldos[~saldos["verificado"]]
    if not sin_verificar.empty:
        st.caption(
            f"{len(sin_verificar)} cuentas se suman desde cero porque no tienen "
            "un saldo real capturado. Ve a **Patrimonio → Verificar saldo**, "
            "elige la cuenta y captura lo que dice hoy la app del banco: el "
            "resto lo deduce el sistema, hacia adelante y hacia atrás."
        )

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
                        "Gasto mensual promedio" if tablero.es_historico else "Gasto",
                        format="$%.0f",
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
    st.subheader(
        "Últimos movimientos"
        if tablero.es_historico
        else "Últimos movimientos del periodo"
    )
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
