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
from finanzas.domain.enums import ClaseCargo, FrecuenciaCobro, Necesidad

# ═══════════════════════════════════════════════════════════
# Gastos fijos: lo que se cobra solo o que toca pagar cada mes
#
# Suscripciones y gastos fijos —la renta, el celular— viven en
# el mismo catálogo porque los dos tienen monto y fecha, y los
# dos entran al calendario de «Pagos del mes». Una suscripción
# se puede cancelar; un gasto fijo, no: la clase los separa.
#
# Todo se normaliza a costo mensual, que es lo único que
# permite comparar un cobro anual con uno mensual.
# ═══════════════════════════════════════════════════════════

servicios = obtener_servicios()
suscripciones = servicios.suscripciones.listar()
resumen = servicios.suscripciones.resumen()

st.title("Gastos fijos")
st.caption(
    "Lo que se cobra solo o toca pagar cada tanto: suscripciones y gastos "
    "fijos como la renta o el celular. Con su próximo cobro, cada uno aparece "
    "en **Pagos del mes** con su fecha y si ya lo pagaste. El costo mensual "
    "normaliza cualquier frecuencia, para comparar un cobro anual con uno "
    "mensual."
)

with st.container(horizontal=True):
    st.metric("Costo mensual", moneda(resumen["costo_mensual"]), border=True)
    st.metric("Gastos fijos al mes", moneda(resumen["fijos_mensual"]), border=True)
    st.metric(
        "Suscripciones al mes",
        moneda(resumen["suscripciones_mensual"]),
        border=True,
    )
    st.metric("Activos", resumen["activas"], border=True)
    st.metric(
        "Ahorro potencial",
        moneda(resumen["ahorro_potencial_mensual"]),
        delta=f"{resumen['candidatas']} candidatas a cancelar",
        delta_color="off",
        border=True,
        help=(
            "Suscripciones activas, discrecionales y con renovación "
            "automática. Los gastos fijos no cuentan: no se cancelan."
        ),
    )

if suscripciones.empty:
    st.info(
        "Aún no hay gastos fijos ni suscripciones. Agrega el primero abajo.",
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
        st.subheader("Tus cargos fijos")
        st.dataframe(
            suscripciones,
            hide_index=True,
            column_config={
                "id": None,
                "categoria_id": None,
                "subcategoria_id": None,
                "cuenta_id": None,
                "servicio": st.column_config.TextColumn("Concepto", pinned=True),
                "clase": st.column_config.TextColumn("Clase"),
                "categoria": st.column_config.TextColumn("Categoría"),
                "subcategoria": st.column_config.TextColumn("Subcategoría"),
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
                "posponible": st.column_config.CheckboxColumn("Posponible"),
                "posponer_hasta": st.column_config.DateColumn(
                    "Hasta", format="DD/MM/YYYY"
                ),
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
                    titulo_dimension="Concepto",
                    titulo_medida="Costo mensual",
                )
            )
            tabla_equivalente(
                activas[["servicio", "costo_mensual", "costo_anual"]].rename(
                    columns={
                        "servicio": "Concepto",
                        "costo_mensual": "Costo mensual",
                        "costo_anual": "Costo anual",
                    }
                )
            )

    # ── Gestionar ────────────────────────────────────────

    with st.container(border=True):
        st.subheader("Gestionar un cargo")

        opciones = {fila.servicio: int(fila.id) for fila in suscripciones.itertuples()}
        elegida = st.selectbox("Cargo", list(opciones))
        suscripcion_id = opciones[elegida]
        actual = suscripciones[suscripciones["id"] == suscripcion_id].iloc[0]

        clases = [str(valor) for valor in ClaseCargo]
        nueva_clase = st.segmented_control(
            "Clase",
            clases,
            default=actual["clase"],
            key=f"clase_{suscripcion_id}",
        )

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
            sigue_posponible = st.checkbox(
                "Se puede posponer",
                value=bool(actual["posponible"]),
                key=f"posponible_{suscripcion_id}",
                help="Si no alcanza, el plan de pagos lo deja para el mes siguiente.",
            )
            tope_actual = actual["posponer_hasta"]
            nuevo_tope = (
                st.date_input(
                    "Hasta",
                    value=tope_actual if pd.notna(tope_actual) else None,
                    format="DD/MM/YYYY",
                    key=f"posponer_hasta_{suscripcion_id}",
                    help=(
                        "En ese mes ya hay que pagar todo lo que se haya ido "
                        "dejando. Vacío: sin límite."
                    ),
                )
                if sigue_posponible
                else None
            )

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
                        posponible=sigue_posponible,
                        posponer_hasta=nuevo_tope,
                        clase=ClaseCargo(nueva_clase or actual["clase"]),
                    )
                except ValueError as error:
                    reportar_error(error)
                else:
                    invalidar_datos()
                    st.success("Cargo actualizado.", icon=":material/check:")
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

# ── Nuevo cargo ──────────────────────────────────────────

with st.container(border=True):
    st.subheader("Nuevo gasto fijo o suscripción")
    st.caption(
        "El próximo cobro con su frecuencia dice cuándo toca cada vez. La "
        "categoría ayuda a reconocer el movimiento que lo paga."
    )

    catalogos = servicios.catalogos.opciones_captura()
    opciones_categoria = {"— sin categoría —": None} | {
        fila.nombre: int(fila.id) for fila in catalogos["categorias"].itertuples()
    }
    opciones_cuenta = {"— sin cuenta —": None} | {
        fila.nombre: int(fila.id) for fila in catalogos["cuentas"].itertuples()
    }

    # La categoría vive fuera del formulario para que el selector de
    # subcategoría se repueble al cambiarla: dentro de un `st.form` el
    # rerun se posterga hasta el submit.
    fila_clasificacion = st.columns(2)

    with fila_clasificacion[0]:
        categoria = st.selectbox("Categoría", list(opciones_categoria))
    categoria_id = opciones_categoria[categoria]

    with fila_clasificacion[1]:
        subcategorias = catalogos["subcategorias"]
        propias = subcategorias[subcategorias["categoria_id"] == categoria_id]
        opciones_sub = {"— sin subcategoría —": None} | {
            fila.nombre: int(fila.id) for fila in propias.itertuples()
        }
        subcategoria = st.selectbox("Subcategoría", list(opciones_sub))
    subcategoria_id = opciones_sub[subcategoria]

    with st.form("nueva_suscripcion", clear_on_submit=True, border=False):
        clase = st.segmented_control(
            "Clase",
            [str(valor) for valor in ClaseCargo],
            default=str(ClaseCargo.GASTO_FIJO),
        )
        fila_1 = st.columns([2, 1, 1, 1])

        with fila_1[0]:
            servicio = st.text_input("Concepto", placeholder="Renta, celular, Netflix")
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

        fila_2 = st.columns(3)

        with fila_2[0]:
            cuenta = st.selectbox("Cuenta de cobro", list(opciones_cuenta))
        with fila_2[1]:
            proximo_cobro = st.date_input(
                "Próximo cobro",
                value=date.today() + timedelta(days=30),
                format="DD/MM/YYYY",
            )
        with fila_2[2]:
            renovacion = st.checkbox("Renovación automática", value=True)
            posponible = st.checkbox(
                "Se puede posponer",
                value=False,
                help="La renta, no. Si no alcanza, el plan la paga primero.",
            )
            posponer_hasta = st.date_input(
                "Hasta",
                value=None,
                format="DD/MM/YYYY",
                help="Sólo si se puede posponer. Vacío: sin límite.",
            )

        notas = st.text_input("Notas")

        if st.form_submit_button("Agregar", type="primary", icon=":material/add:"):
            try:
                servicios.suscripciones.crear(
                    servicio=servicio,
                    costo_por_cobro=costo,
                    frecuencia=frecuencia,
                    categoria_id=categoria_id,
                    subcategoria_id=subcategoria_id,
                    cuenta_id=opciones_cuenta[cuenta],
                    proximo_cobro=proximo_cobro,
                    renovacion_automatica=renovacion,
                    necesidad=necesidad,
                    notas=notas,
                    clase=clase or ClaseCargo.GASTO_FIJO,
                    posponible=posponible,
                    posponer_hasta=posponer_hasta,
                )
            except ValueError as error:
                reportar_error(error)
            else:
                invalidar_datos()
                st.success(f"«{servicio}» agregada.", icon=":material/check_circle:")
                st.rerun()
