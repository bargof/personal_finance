from __future__ import annotations

from datetime import date

import streamlit as st

from finanzas.analytics.aggregations import etiqueta_periodo, periodo_de
from finanzas.application.app.components import (
    grafico_barras,
    grafico_patrimonio,
    invalidar_datos,
    moneda,
    obtener_servicios,
    opciones_periodo,
    reportar_error,
    tabla_equivalente,
)
from finanzas.domain.enums import Liquidez, TipoPatrimonio

# ═══════════════════════════════════════════════════════════
# Patrimonio: la foto de lo que tienes y lo que debes
#
# Los pasivos también se capturan en positivo; el signo lo
# pone el cálculo, no la captura.
# ═══════════════════════════════════════════════════════════

servicios = obtener_servicios()
balance = servicios.patrimonio.balance()
resumen = servicios.patrimonio.resumen()

st.title("Patrimonio")
st.caption(
    "Actualiza los saldos al menos una vez al mes. Captura las deudas en "
    "positivo: el patrimonio neto ya las resta."
)

SUBTIPOS = (
    "Efectivo / banco",
    "Ahorro",
    "Inversiones",
    "Otros bienes",
    "Tarjeta",
    "Préstamo",
    "Otro",
)

with st.container(horizontal=True):
    st.metric("Activos", moneda(resumen["activos"]), border=True)
    st.metric("Pasivos", moneda(resumen["pasivos"]), border=True)
    st.metric(
        "Patrimonio neto",
        moneda(resumen["patrimonio_neto"]),
        border=True,
    )
    st.metric(
        "Activos líquidos",
        moneda(servicios.patrimonio.activos_liquidos()),
        help="Activos con liquidez alta: el colchón disponible de inmediato.",
        border=True,
    )

balance_tab, cierres_tab = st.tabs(["Balance actual", "Cierres mensuales"])


# ═══════════════════════════════════════════════════════════
# Balance actual
# ═══════════════════════════════════════════════════════════

with balance_tab:
    if balance.empty:
        st.info(
            "Aún no hay posiciones registradas. Agrega tus cuentas y deudas abajo.",
            icon=":material/info:",
        )
    else:
        with st.container(border=True):
            st.subheader("Posiciones")
            st.dataframe(
                balance,
                hide_index=True,
                column_config={
                    "id": None,
                    "nombre": st.column_config.TextColumn(
                        "Cuenta o activo", pinned=True
                    ),
                    "tipo": st.column_config.TextColumn("Tipo"),
                    "subtipo": st.column_config.TextColumn("Subtipo"),
                    "institucion": st.column_config.TextColumn("Institución"),
                    "saldo": st.column_config.NumberColumn("Saldo", format="$%.2f"),
                    "liquidez": st.column_config.TextColumn("Liquidez"),
                    "tasa_anual": st.column_config.NumberColumn(
                        "Tasa anual", format="percent"
                    ),
                    "fecha_corte": st.column_config.DateColumn(
                        "Fecha de corte", format="DD/MM/YYYY"
                    ),
                    "moneda": st.column_config.TextColumn("Moneda"),
                    "notas": st.column_config.TextColumn("Notas", width="medium"),
                    "aporte_a_patrimonio": st.column_config.NumberColumn(
                        "Aporte al neto", format="$%.2f"
                    ),
                },
            )

        composicion = servicios.patrimonio.por_subtipo()
        activos = composicion[composicion["tipo"] == "Activo"]

        if not activos.empty:
            with st.container(border=True):
                st.subheader("Composición de los activos")
                st.altair_chart(
                    grafico_barras(
                        activos,
                        dimension="subtipo",
                        medida="saldo",
                        titulo_dimension="Subtipo",
                        titulo_medida="Saldo",
                    )
                )
                tabla_equivalente(
                    composicion.rename(
                        columns={
                            "tipo": "Tipo",
                            "subtipo": "Subtipo",
                            "saldo": "Saldo",
                        }
                    )
                )

        with st.container(border=True):
            st.subheader("Actualizar un saldo")
            st.caption("La operación mensual: sólo cambia el número.")

            opciones = {fila.nombre: int(fila.id) for fila in balance.itertuples()}
            columnas = st.columns([2, 1, 1])

            with columnas[0]:
                elegida = st.selectbox("Posición", list(opciones))
            posicion_id = opciones[elegida]
            actual = balance[balance["id"] == posicion_id].iloc[0]

            with columnas[1]:
                nuevo_saldo = st.number_input(
                    "Saldo actual",
                    min_value=0.0,
                    value=float(actual["saldo"]),
                    step=100.0,
                    format="%.2f",
                )

            with columnas[2]:
                st.markdown("&nbsp;")
                if st.button("Guardar saldo", type="primary", icon=":material/save:"):
                    try:
                        servicios.patrimonio.actualizar_saldo(posicion_id, nuevo_saldo)
                    except ValueError as error:
                        reportar_error(error)
                    else:
                        invalidar_datos()
                        st.success("Saldo actualizado.", icon=":material/check:")
                        st.rerun()

            if st.button("Eliminar posición", icon=":material/delete:"):
                servicios.patrimonio.eliminar(posicion_id)
                invalidar_datos()
                st.success(f"«{elegida}» eliminada.", icon=":material/check:")
                st.rerun()

    with st.container(border=True):
        st.subheader("Nueva posición")

        with st.form("nueva_posicion", clear_on_submit=True, border=False):
            fila_1 = st.columns([2, 1, 1, 1])

            with fila_1[0]:
                nombre = st.text_input(
                    "Cuenta o activo", placeholder="Cuenta principal"
                )
            with fila_1[1]:
                tipo = st.selectbox("Tipo", [str(valor) for valor in TipoPatrimonio])
            with fila_1[2]:
                subtipo = st.selectbox("Subtipo", SUBTIPOS)
            with fila_1[3]:
                liquidez = st.selectbox("Liquidez", [str(valor) for valor in Liquidez])

            fila_2 = st.columns(4)

            with fila_2[0]:
                saldo = st.number_input(
                    "Saldo actual", min_value=0.0, step=500.0, format="%.2f"
                )
            with fila_2[1]:
                institucion = st.text_input("Institución", placeholder="Banco")
            with fila_2[2]:
                tasa = st.number_input(
                    "Tasa anual",
                    min_value=0.0,
                    max_value=2.0,
                    step=0.01,
                    format="%.4f",
                    help="En proporción: 0.07 equivale a 7%.",
                )
            with fila_2[3]:
                fecha_corte = st.date_input(
                    "Fecha de corte", value=date.today(), format="DD/MM/YYYY"
                )

            notas = st.text_input("Notas", placeholder="Saldo al corte del mes")

            if st.form_submit_button(
                "Agregar posición", type="primary", icon=":material/add:"
            ):
                try:
                    servicios.patrimonio.crear(
                        nombre=nombre,
                        tipo=tipo,
                        saldo=saldo,
                        subtipo=subtipo,
                        institucion=institucion,
                        liquidez=liquidez,
                        tasa_anual=tasa,
                        fecha_corte=fecha_corte,
                        notas=notas,
                    )
                except ValueError as error:
                    reportar_error(error)
                else:
                    invalidar_datos()
                    st.success(f"«{nombre}» agregada.", icon=":material/check_circle:")
                    st.rerun()


# ═══════════════════════════════════════════════════════════
# Cierres mensuales
# ═══════════════════════════════════════════════════════════

with cierres_tab:
    cierres = servicios.patrimonio.cierres()

    if cierres.empty:
        st.info(
            "Sin cierres registrados. Guarda el primero abajo para empezar a "
            "ver la evolución de tu patrimonio.",
            icon=":material/info:",
        )
    else:
        with st.container(border=True):
            st.subheader("Evolución del patrimonio neto")
            st.altair_chart(grafico_patrimonio(cierres))
            tabla_equivalente(
                cierres[
                    [
                        "periodo",
                        "efectivo",
                        "ahorro",
                        "inversiones",
                        "otros_activos",
                        "deudas",
                        "patrimonio_neto",
                        "cambio_mensual",
                    ]
                ].rename(
                    columns={
                        "periodo": "Periodo",
                        "efectivo": "Efectivo",
                        "ahorro": "Ahorro",
                        "inversiones": "Inversiones",
                        "otros_activos": "Otros activos",
                        "deudas": "Deudas",
                        "patrimonio_neto": "Patrimonio neto",
                        "cambio_mensual": "Cambio",
                    }
                )
            )

        ultimo = cierres.iloc[-1]
        with st.container(horizontal=True):
            st.metric(
                "Último cierre",
                etiqueta_periodo(str(ultimo["periodo"])),
                border=True,
            )
            st.metric(
                "Patrimonio neto",
                moneda(float(ultimo["patrimonio_neto"])),
                delta=moneda(float(ultimo["cambio_mensual"])),
                border=True,
            )

    with st.container(border=True):
        st.subheader("Registrar un cierre")
        st.caption(
            "Los campos se prellenan con los saldos actuales del balance: "
            "revisa y guarda."
        )

        periodo_cierre = st.selectbox(
            "Periodo",
            opciones_periodo(periodo_de(date.today())),
            format_func=etiqueta_periodo,
        )
        sugerido = servicios.patrimonio.cierre_sugerido(periodo_cierre)

        with st.form(f"cierre_{periodo_cierre}", border=False):
            fila = st.columns(5)

            with fila[0]:
                efectivo = st.number_input(
                    "Efectivo y bancos", value=sugerido.efectivo, step=500.0
                )
            with fila[1]:
                ahorro = st.number_input("Ahorro", value=sugerido.ahorro, step=500.0)
            with fila[2]:
                inversiones = st.number_input(
                    "Inversiones", value=sugerido.inversiones, step=500.0
                )
            with fila[3]:
                otros = st.number_input(
                    "Otros activos", value=sugerido.otros_activos, step=500.0
                )
            with fila[4]:
                deudas = st.number_input("Deudas", value=sugerido.deudas, step=500.0)

            notas_cierre = st.text_input("Notas del cierre")

            if st.form_submit_button(
                "Guardar cierre", type="primary", icon=":material/save:"
            ):
                servicios.patrimonio.guardar_cierre(
                    periodo=periodo_cierre,
                    efectivo=efectivo,
                    ahorro=ahorro,
                    inversiones=inversiones,
                    otros_activos=otros,
                    deudas=deudas,
                    notas=notas_cierre,
                )
                invalidar_datos()
                st.success(
                    f"Cierre de {etiqueta_periodo(periodo_cierre)} guardado.",
                    icon=":material/check_circle:",
                )
                st.rerun()
