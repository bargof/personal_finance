from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from finanzas.application.app.components import (
    grafico_barras,
    invalidar_datos,
    moneda,
    obtener_servicios,
    reportar_error,
    tabla_equivalente,
)
from finanzas.domain.enums import FrecuenciaCobro, Necesidad

# ═══════════════════════════════════════════════════════════
# Suscripciones: el gasto que se renueva solo
#
# Todo se normaliza a costo mensual, que es lo único que
# permite comparar un cobro anual con uno mensual.
# ═══════════════════════════════════════════════════════════

servicios = obtener_servicios()
suscripciones = servicios.suscripciones.listar()
resumen = servicios.suscripciones.resumen()

st.title("Suscripciones")
st.caption(
    "El costo mensual normaliza cualquier frecuencia de cobro, para que un "
    "servicio anual y uno mensual se puedan comparar."
)

with st.container(horizontal=True):
    st.metric("Costo mensual", moneda(resumen["costo_mensual"]), border=True)
    st.metric("Costo anual", moneda(resumen["costo_anual"]), border=True)
    st.metric("Activas", resumen["activas"], border=True)
    st.metric(
        "Ahorro potencial",
        moneda(resumen["ahorro_potencial_mensual"]),
        delta=f"{resumen['candidatas']} candidatas a cancelar",
        delta_color="off",
        border=True,
        help="Suscripciones activas, discrecionales y con renovación automática.",
    )

if suscripciones.empty:
    st.info(
        "Aún no hay suscripciones registradas. Agrega la primera abajo.",
        icon=":material/info:",
    )
else:
    # ── Próximos cobros ──────────────────────────────────

    proximos = servicios.suscripciones.proximos_cobros(dias=30)
    if not proximos.empty:
        st.warning(
            f"{len(proximos)} cobros en los próximos 30 días por "
            f"{moneda(float(proximos['costo_por_cobro'].sum()))}.",
            icon=":material/event_upcoming:",
        )

    with st.container(border=True):
        st.subheader("Tus suscripciones")
        st.dataframe(
            suscripciones,
            hide_index=True,
            column_config={
                "id": None,
                "categoria_id": None,
                "cuenta_id": None,
                "servicio": st.column_config.TextColumn("Servicio", pinned=True),
                "categoria": st.column_config.TextColumn("Categoría"),
                "costo_por_cobro": st.column_config.NumberColumn(
                    "Costo por cobro", format="$%.2f"
                ),
                "frecuencia": st.column_config.TextColumn("Frecuencia"),
                "costo_mensual": st.column_config.NumberColumn(
                    "Costo mensual", format="$%.2f"
                ),
                "costo_anual": st.column_config.NumberColumn(
                    "Costo anual", format="$%.2f"
                ),
                "proximo_cobro": st.column_config.DateColumn(
                    "Próximo cobro", format="DD/MM/YYYY"
                ),
                "cuenta": st.column_config.TextColumn("Cuenta"),
                "renovacion_automatica": st.column_config.CheckboxColumn(
                    "Renueva sola"
                ),
                "necesidad": st.column_config.TextColumn("Necesidad"),
                "activa": st.column_config.CheckboxColumn("Activa"),
                "candidato_a_cancelar": st.column_config.CheckboxColumn("Revisar"),
                "notas": st.column_config.TextColumn("Notas", width="medium"),
            },
        )

    activas = suscripciones[suscripciones["activa"]]
    if not activas.empty:
        with st.container(border=True):
            st.subheader("Costo mensual por servicio")
            st.altair_chart(
                grafico_barras(
                    activas,
                    dimension="servicio",
                    medida="costo_mensual",
                    titulo_dimension="Servicio",
                    titulo_medida="Costo mensual",
                )
            )
            tabla_equivalente(
                activas[["servicio", "costo_mensual", "costo_anual"]].rename(
                    columns={
                        "servicio": "Servicio",
                        "costo_mensual": "Costo mensual",
                        "costo_anual": "Costo anual",
                    }
                )
            )

    # ── Gestionar ────────────────────────────────────────

    with st.container(border=True):
        st.subheader("Gestionar una suscripción")

        opciones = {fila.servicio: int(fila.id) for fila in suscripciones.itertuples()}
        elegida = st.selectbox("Suscripción", list(opciones))
        suscripcion_id = opciones[elegida]
        actual = suscripciones[suscripciones["id"] == suscripcion_id].iloc[0]

        columnas = st.columns([1, 1, 1, 1])

        with columnas[0]:
            nuevo_costo = st.number_input(
                "Costo por cobro",
                min_value=0.0,
                value=float(actual["costo_por_cobro"]),
                step=10.0,
                format="%.2f",
            )

        with columnas[1]:
            frecuencias = [str(valor) for valor in FrecuenciaCobro]
            nueva_frecuencia = st.selectbox(
                "Frecuencia",
                frecuencias,
                index=frecuencias.index(actual["frecuencia"]),
            )

        with columnas[2]:
            proximo = actual["proximo_cobro"]
            nuevo_cobro = st.date_input(
                "Próximo cobro",
                value=proximo.date()
                if pd.notna(proximo)
                else date.today() + timedelta(days=30),
                format="DD/MM/YYYY",
            )

        with columnas[3]:
            sigue_activa = st.checkbox("Activa", value=bool(actual["activa"]))

        acciones = st.columns(3)

        with acciones[0]:
            if st.button("Guardar cambios", type="primary", icon=":material/save:"):
                try:
                    servicios.suscripciones.actualizar(
                        suscripcion_id,
                        costo_por_cobro=nuevo_costo,
                        frecuencia=FrecuenciaCobro(nueva_frecuencia),
                        proximo_cobro=nuevo_cobro,
                        activa=sigue_activa,
                    )
                except ValueError as error:
                    reportar_error(error)
                else:
                    invalidar_datos()
                    st.success("Suscripción actualizada.", icon=":material/check:")
                    st.rerun()

        with acciones[1]:
            etiqueta = "Dar de baja" if actual["activa"] else "Reactivar"
            if st.button(etiqueta, icon=":material/power_settings_new:"):
                servicios.suscripciones.cambiar_estado(
                    suscripcion_id, not bool(actual["activa"])
                )
                invalidar_datos()
                st.success(f"«{elegida}»: {etiqueta.lower()}.", icon=":material/check:")
                st.rerun()

        with acciones[2]:
            if st.button("Eliminar", icon=":material/delete:"):
                servicios.suscripciones.eliminar(suscripcion_id)
                invalidar_datos()
                st.success(f"«{elegida}» eliminada.", icon=":material/check:")
                st.rerun()

# ── Nueva suscripción ────────────────────────────────────

with st.container(border=True):
    st.subheader("Nueva suscripción")

    catalogos = servicios.catalogos.opciones_captura()
    opciones_categoria = {"— sin categoría —": None} | {
        fila.nombre: int(fila.id) for fila in catalogos["categorias"].itertuples()
    }
    opciones_cuenta = {"— sin cuenta —": None} | {
        fila.nombre: int(fila.id) for fila in catalogos["cuentas"].itertuples()
    }

    with st.form("nueva_suscripcion", clear_on_submit=True, border=False):
        fila_1 = st.columns([2, 1, 1, 1])

        with fila_1[0]:
            servicio = st.text_input("Servicio", placeholder="Streaming")
        with fila_1[1]:
            costo = st.number_input(
                "Costo por cobro", min_value=0.0, step=10.0, format="%.2f"
            )
        with fila_1[2]:
            frecuencia = st.selectbox(
                "Frecuencia", [str(valor) for valor in FrecuenciaCobro]
            )
        with fila_1[3]:
            necesidad = st.selectbox(
                "Necesidad", [str(valor) for valor in Necesidad], index=1
            )

        fila_2 = st.columns(4)

        with fila_2[0]:
            categoria = st.selectbox("Categoría", list(opciones_categoria))
        with fila_2[1]:
            cuenta = st.selectbox("Cuenta de cobro", list(opciones_cuenta))
        with fila_2[2]:
            proximo_cobro = st.date_input(
                "Próximo cobro",
                value=date.today() + timedelta(days=30),
                format="DD/MM/YYYY",
            )
        with fila_2[3]:
            renovacion = st.checkbox("Renovación automática", value=True)

        notas = st.text_input("Notas")

        if st.form_submit_button(
            "Agregar suscripción", type="primary", icon=":material/add:"
        ):
            try:
                servicios.suscripciones.crear(
                    servicio=servicio,
                    costo_por_cobro=costo,
                    frecuencia=frecuencia,
                    categoria_id=opciones_categoria[categoria],
                    cuenta_id=opciones_cuenta[cuenta],
                    proximo_cobro=proximo_cobro,
                    renovacion_automatica=renovacion,
                    necesidad=necesidad,
                    notas=notas,
                )
            except ValueError as error:
                reportar_error(error)
            else:
                invalidar_datos()
                st.success(f"«{servicio}» agregada.", icon=":material/check_circle:")
                st.rerun()
