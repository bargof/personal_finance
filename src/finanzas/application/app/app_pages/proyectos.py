from __future__ import annotations

from datetime import date

import streamlit as st

from finanzas.application.app.components import (
    grafico_barras,
    invalidar_datos,
    moneda,
    obtener_servicios,
    porcentaje,
    reportar_error,
)
from finanzas.application.app.theme import rotulo

# ═══════════════════════════════════════════════════════════
# Proyectos: lo que cruza categorías
#
# La categoría dice qué clase de gasto fue; el proyecto, para
# qué esfuerzo se hizo. Un viaje cruza transporte, comida y
# hospedaje, y sólo agrupándolo se sabe cuánto costó.
#
# Crearlo de antemano con presupuesto es lo que permite ver a
# media obra si va a alcanzar, en vez de enterarse al final.
# ═══════════════════════════════════════════════════════════

servicios = obtener_servicios()
proyectos = servicios.proyectos.listar()

st.title("Proyectos")
st.caption(
    "Agrupa movimientos de distintas categorías bajo un mismo esfuerzo y "
    "ponles presupuesto para saber cómo van."
)

lista_tab, nuevo_tab = st.tabs(["Mis proyectos", "Crear proyecto"])


# ═══════════════════════════════════════════════════════════
# Lista
# ═══════════════════════════════════════════════════════════

with lista_tab:
    if proyectos.empty:
        st.info(
            "Todavía no hay proyectos. Crea el primero en la pestaña de al "
            "lado, o asigna uno al capturar un movimiento.",
            icon=":material/info:",
        )
    else:
        activos = proyectos[proyectos["activo"]]

        with st.container(horizontal=True):
            st.metric("Proyectos", len(proyectos), border=True)
            st.metric("En curso", len(activos), border=True)
            st.metric(
                "Gasto acumulado",
                moneda(float(proyectos["gasto"].sum())),
                border=True,
            )
            comprometido = float(proyectos["presupuesto"].sum())
            if comprometido:
                st.metric(
                    "Presupuestado",
                    moneda(comprometido),
                    border=True,
                    help="Suma de los presupuestos que les pusiste.",
                )

        rotulo("Resumen")

        with st.container(border=True):
            st.dataframe(
                proyectos,
                hide_index=True,
                width="stretch",
                column_config={
                    "proyecto": st.column_config.TextColumn("Proyecto", pinned=True),
                    "proyecto_id": None,
                    "declarado": None,
                    "notas": None,
                    "descripcion": st.column_config.TextColumn(
                        "Descripción", width="medium"
                    ),
                    "presupuesto": st.column_config.NumberColumn(
                        "Presupuesto", format="$%.2f"
                    ),
                    "gasto": st.column_config.NumberColumn("Gasto", format="$%.2f"),
                    "disponible": st.column_config.NumberColumn(
                        "Disponible", format="$%.2f"
                    ),
                    "pct_usado": st.column_config.ProgressColumn(
                        "% usado", format="percent", min_value=0, max_value=1.5
                    ),
                    "ingreso": st.column_config.NumberColumn("Ingreso", format="$%.2f"),
                    "ahorro_inversion": None,
                    "por_pagar": st.column_config.NumberColumn(
                        "Por pagar", format="$%.2f"
                    ),
                    "neto": st.column_config.NumberColumn("Neto", format="$%.2f"),
                    "movimientos": st.column_config.NumberColumn("Movs."),
                    "desde": st.column_config.DateColumn(
                        "Primer gasto", format="DD/MM/YYYY"
                    ),
                    "hasta": st.column_config.DateColumn(
                        "Último gasto", format="DD/MM/YYYY"
                    ),
                    "fecha_inicio": st.column_config.DateColumn(
                        "Inicio", format="DD/MM/YYYY"
                    ),
                    "fecha_fin": st.column_config.DateColumn(
                        "Fin", format="DD/MM/YYYY"
                    ),
                    "activo": st.column_config.CheckboxColumn("En curso"),
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

        # ── Gestionar uno ────────────────────────────────

        rotulo("Gestionar")

        with st.container(border=True):
            elegido = st.selectbox("Proyecto", proyectos["proyecto"].tolist())
            fila = proyectos[proyectos["proyecto"] == elegido].iloc[0]

            if not fila["declarado"]:
                # Los proyectos anteriores al catálogo viven sólo como
                # texto en los movimientos y no tienen ficha que editar.
                st.info(
                    f"«{elegido}» existe sólo como etiqueta en "
                    f"{int(fila['movimientos'])} movimientos. Dale una ficha "
                    "para poder ponerle presupuesto y fechas.",
                    icon=":material/label:",
                )
                if st.button("Crear su ficha", type="primary", icon=":material/add:"):
                    try:
                        servicios.proyectos.adoptar(elegido)
                    except ValueError as error:
                        reportar_error(error)
                    else:
                        invalidar_datos()
                        st.rerun()
            else:
                proyecto_id = int(fila["proyecto_id"])

                with st.form(f"editar_{proyecto_id}", border=False):
                    fila_1 = st.columns([2, 1, 1])

                    with fila_1[0]:
                        nombre = st.text_input("Nombre", value=fila["proyecto"])
                    with fila_1[1]:
                        presupuesto = st.number_input(
                            "Presupuesto",
                            min_value=0.0,
                            value=float(fila["presupuesto"]),
                            step=500.0,
                            format="%.2f",
                            help="Déjalo en cero si el proyecto no tiene tope.",
                        )
                    with fila_1[2]:
                        en_curso = st.checkbox("En curso", value=bool(fila["activo"]))

                    descripcion = st.text_input(
                        "Descripción", value=fila["descripcion"]
                    )

                    fila_2 = st.columns(2)
                    with fila_2[0]:
                        inicio = st.date_input(
                            "Inicio",
                            value=fila["fecha_inicio"]
                            if fila["fecha_inicio"] == fila["fecha_inicio"]
                            else None,
                            format="DD/MM/YYYY",
                        )
                    with fila_2[1]:
                        fin = st.date_input(
                            "Fin",
                            value=fila["fecha_fin"]
                            if fila["fecha_fin"] == fila["fecha_fin"]
                            else None,
                            format="DD/MM/YYYY",
                        )

                    notas = st.text_area("Notas", value=fila["notas"], height=70)

                    if st.form_submit_button(
                        "Guardar cambios", type="primary", icon=":material/save:"
                    ):
                        try:
                            servicios.proyectos.actualizar(
                                proyecto_id,
                                nombre=nombre,
                                descripcion=descripcion,
                                presupuesto=presupuesto,
                                fecha_inicio=inicio,
                                fecha_fin=fin,
                                activo=en_curso,
                                notas=notas,
                            )
                        except ValueError as error:
                            reportar_error(error)
                        else:
                            invalidar_datos()
                            st.success("Proyecto actualizado.", icon=":material/check:")
                            st.rerun()

                if fila["presupuesto"] > 0:
                    usado = float(fila["pct_usado"])
                    st.progress(
                        min(usado, 1.0),
                        text=(
                            f"{porcentaje(usado, 1)} del presupuesto · "
                            f"quedan {moneda(float(fila['disponible']))}"
                        ),
                    )
                    if usado > 1:
                        st.error(
                            f"El proyecto excede su presupuesto en "
                            f"{moneda(abs(float(fila['disponible'])))}.",
                            icon=":material/error:",
                        )

                if st.button("Eliminar proyecto", icon=":material/delete:"):
                    servicios.proyectos.eliminar(proyecto_id)
                    invalidar_datos()
                    st.success(
                        f"«{elegido}» eliminado; sus movimientos quedaron sin "
                        "proyecto.",
                        icon=":material/check:",
                    )
                    st.rerun()

        # ── Sus movimientos ──────────────────────────────

        rotulo("Movimientos del proyecto")

        detalle = servicios.movimientos.buscar(proyectos=[elegido])
        if detalle.empty:
            st.caption("Todavía no hay movimientos asignados a este proyecto.")
        else:
            with st.container(border=True):
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
                        "fecha": st.column_config.DateColumn(
                            "Fecha", format="DD/MM/YYYY"
                        ),
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
# Crear
# ═══════════════════════════════════════════════════════════

with nuevo_tab:
    with st.container(border=True):
        st.subheader("Nuevo proyecto")
        st.caption(
            "Créalo antes de empezar a gastarle: con presupuesto y fechas se "
            "puede ver a media obra si va a alcanzar."
        )

        with st.form("nuevo_proyecto", clear_on_submit=True, border=False):
            fila_1 = st.columns([2, 1])

            with fila_1[0]:
                nombre_nuevo = st.text_input(
                    "Nombre", placeholder="Remodelación de la cocina"
                )
            with fila_1[1]:
                presupuesto_nuevo = st.number_input(
                    "Presupuesto",
                    min_value=0.0,
                    step=500.0,
                    format="%.2f",
                    help="Opcional. Déjalo en cero si no hay tope.",
                )

            descripcion_nueva = st.text_input(
                "Descripción", placeholder="Cambio de muebles y electrodomésticos"
            )

            fila_2 = st.columns(2)
            with fila_2[0]:
                inicio_nuevo = st.date_input(
                    "Inicio", value=date.today(), format="DD/MM/YYYY"
                )
            with fila_2[1]:
                fin_nuevo = st.date_input(
                    "Fin estimado", value=None, format="DD/MM/YYYY"
                )

            notas_nuevas = st.text_area("Notas", height=70)

            if st.form_submit_button(
                "Crear proyecto", type="primary", icon=":material/add:"
            ):
                try:
                    servicios.proyectos.crear(
                        nombre=nombre_nuevo,
                        descripcion=descripcion_nueva,
                        presupuesto=presupuesto_nuevo,
                        fecha_inicio=inicio_nuevo,
                        fecha_fin=fin_nuevo,
                        notas=notas_nuevas,
                    )
                except ValueError as error:
                    reportar_error(error)
                else:
                    invalidar_datos()
                    st.success(
                        f"«{nombre_nuevo}» creado. Ya puedes asignarle "
                        "movimientos al capturarlos.",
                        icon=":material/check_circle:",
                    )
                    st.rerun()
