from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from finanzas.application.app.components import (
    insignia_estado,
    invalidar_datos,
    moneda,
    obtener_servicios,
    reportar_error,
)
from finanzas.domain.enums import Prioridad

# ═══════════════════════════════════════════════════════════
# Metas: convertir deseos en montos, fechas y aportaciones
# ═══════════════════════════════════════════════════════════

servicios = obtener_servicios()
tablero = servicios.metas.tablero()

st.title("Metas financieras")
st.caption(
    "Cada meta traduce un objetivo en una aportación mensual concreta. "
    "El estado compara lo que aportas con lo que necesitarías aportar."
)

TIPOS_META = ("Seguridad", "Compra / experiencia", "Inversión", "Deuda", "Otro")

# ── Resumen ──────────────────────────────────────────────

if not tablero.empty:
    abiertas = tablero[tablero["estado"] != "Lograda"]

    with st.container(horizontal=True):
        st.metric("Metas activas", len(abiertas), border=True)
        st.metric(
            "Meta acumulada",
            moneda(float(tablero["monto_meta"].sum())),
            border=True,
        )
        st.metric(
            "Avance total",
            moneda(float(tablero["acumulado"].sum())),
            delta=(
                f"{tablero['acumulado'].sum() / tablero['monto_meta'].sum():.0%} "
                "del objetivo"
                if tablero["monto_meta"].sum()
                else None
            ),
            border=True,
        )
        st.metric(
            "Aporte mensual planeado",
            moneda(float(abiertas["aporte_mensual_planeado"].sum())),
            delta=f"necesario: {moneda(float(abiertas['aporte_necesario'].sum()))}",
            delta_color="off",
            border=True,
        )

    with st.container(border=True):
        st.subheader("Tus metas")
        st.dataframe(
            tablero.assign(estado=tablero["estado"].map(insignia_estado)),
            hide_index=True,
            column_config={
                "id": None,
                "objetivo": st.column_config.TextColumn("Objetivo", pinned=True),
                "tipo": st.column_config.TextColumn("Tipo"),
                "monto_meta": st.column_config.NumberColumn("Meta", format="$%.0f"),
                "acumulado": st.column_config.NumberColumn("Acumulado", format="$%.0f"),
                "aporte_mensual_planeado": st.column_config.NumberColumn(
                    "Aporte planeado", format="$%.0f"
                ),
                "fecha_limite": st.column_config.DateColumn(
                    "Fecha límite", format="DD/MM/YYYY"
                ),
                "meses_restantes": st.column_config.NumberColumn("Meses"),
                "aporte_necesario": st.column_config.NumberColumn(
                    "Aporte necesario", format="$%.0f"
                ),
                "pct_avance": st.column_config.ProgressColumn(
                    "Avance", format="percent", min_value=0.0, max_value=1.0
                ),
                "estado": st.column_config.TextColumn("Estado"),
                "prioridad": st.column_config.TextColumn("Prioridad"),
                "vehiculo": st.column_config.TextColumn("Vehículo"),
                "notas": st.column_config.TextColumn("Notas", width="medium"),
            },
        )
else:
    st.info(
        "Aún no tienes metas. Crea la primera abajo: el fondo de emergencia "
        "suele ser la que más rinde.",
        icon=":material/flag:",
    )

# ── Gestionar una meta ───────────────────────────────────

if not tablero.empty:
    with st.container(border=True):
        st.subheader("Actualizar una meta")

        opciones = {fila.objetivo: int(fila.id) for fila in tablero.itertuples()}
        elegida = st.selectbox("Meta", list(opciones))
        meta_id = opciones[elegida]
        actual = tablero[tablero["id"] == meta_id].iloc[0]

        columnas = st.columns([1, 1, 1])

        with columnas[0]:
            abono = st.number_input(
                "Registrar aportación",
                min_value=0.0,
                step=500.0,
                format="%.2f",
                help="Se suma al acumulado de la meta.",
            )
            if st.button("Abonar", type="primary", icon=":material/add:"):
                try:
                    servicios.metas.abonar(meta_id, abono)
                except ValueError as error:
                    reportar_error(error)
                else:
                    invalidar_datos()
                    st.success(
                        f"{moneda(abono)} abonados a «{elegida}».",
                        icon=":material/check_circle:",
                    )
                    st.rerun()

        with columnas[1]:
            nuevo_aporte = st.number_input(
                "Aporte mensual planeado",
                min_value=0.0,
                value=float(actual["aporte_mensual_planeado"]),
                step=250.0,
                format="%.2f",
            )
            if st.button("Guardar aporte", icon=":material/save:"):
                try:
                    servicios.metas.actualizar(
                        meta_id, aporte_mensual_planeado=nuevo_aporte
                    )
                except ValueError as error:
                    reportar_error(error)
                else:
                    invalidar_datos()
                    st.success("Aporte actualizado.", icon=":material/check:")
                    st.rerun()

        with columnas[2]:
            st.markdown("&nbsp;", unsafe_allow_html=False)
            if st.button("Eliminar meta", icon=":material/delete:"):
                servicios.metas.eliminar(meta_id)
                invalidar_datos()
                st.success(f"Meta «{elegida}» eliminada.", icon=":material/check:")
                st.rerun()

        if actual["estado"] == "Ajustar aportación":
            brecha = actual["aporte_necesario"] - actual["aporte_mensual_planeado"]
            st.warning(
                f"Para llegar a tiempo necesitas aportar "
                f"{moneda(float(actual['aporte_necesario']))} al mes, "
                f"{moneda(float(brecha))} más de lo planeado.",
                icon=":material/warning:",
            )

# ── Crear una meta ───────────────────────────────────────

with st.container(border=True):
    st.subheader("Nueva meta")

    with st.form("nueva_meta", clear_on_submit=True, border=False):
        fila_1 = st.columns([2, 1, 1])

        with fila_1[0]:
            objetivo = st.text_input("Objetivo", placeholder="Fondo de emergencia")
        with fila_1[1]:
            tipo = st.selectbox("Tipo", TIPOS_META)
        with fila_1[2]:
            prioridad = st.selectbox(
                "Prioridad",
                [str(valor) for valor in Prioridad],
                index=1,
            )

        fila_2 = st.columns(4)

        with fila_2[0]:
            monto_meta = st.number_input(
                "Monto meta", min_value=0.0, step=1000.0, format="%.2f"
            )
        with fila_2[1]:
            acumulado = st.number_input(
                "Ya acumulado", min_value=0.0, step=500.0, format="%.2f"
            )
        with fila_2[2]:
            aporte = st.number_input(
                "Aporte mensual", min_value=0.0, step=250.0, format="%.2f"
            )
        with fila_2[3]:
            fecha_limite = st.date_input(
                "Fecha límite",
                value=date.today() + timedelta(days=365),
                format="DD/MM/YYYY",
            )

        fila_3 = st.columns([1, 2])

        with fila_3[0]:
            vehiculo = st.text_input("Vehículo o cuenta", placeholder="Cuenta ahorro")
        with fila_3[1]:
            notas = st.text_input("Notas", placeholder="3 a 6 meses de gasto esencial")

        if st.form_submit_button("Crear meta", type="primary", icon=":material/add:"):
            try:
                servicios.metas.crear(
                    objetivo=objetivo,
                    monto_meta=monto_meta,
                    tipo=tipo,
                    acumulado=acumulado,
                    aporte_mensual_planeado=aporte,
                    fecha_limite=fecha_limite,
                    prioridad=prioridad,
                    vehiculo=vehiculo,
                    notas=notas,
                )
            except ValueError as error:
                reportar_error(error)
            else:
                invalidar_datos()
                st.success(f"Meta «{objetivo}» creada.", icon=":material/check_circle:")
                st.rerun()
