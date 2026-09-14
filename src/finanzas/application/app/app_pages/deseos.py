from __future__ import annotations

import streamlit as st

from finanzas.application.app.components import (
    invalidar_datos,
    moneda,
    obtener_servicios,
    porcentaje,
    reportar_error,
)
from finanzas.application.app.theme import paleta, rotulo
from finanzas.domain.enums import EstadoDeseo, Prioridad

# ═══════════════════════════════════════════════════════════
# Lista de deseos
#
# La lista sola no decide nada; lo que decide es ponerla al
# lado del saldo disponible. De ahí que la cuenta se elija
# arriba y todo lo de abajo se recalcule contra ella: la
# pregunta no es «cuánto cuesta» sino «me alcanza».
# ═══════════════════════════════════════════════════════════

servicios = obtener_servicios()
saldos = servicios.deseos.saldos_por_cuenta()
catalogos = servicios.catalogos.opciones_captura()

st.title("Lista de deseos")
st.caption(
    "Lo que quieres comprar, medido contra lo que tienes. Elige la cuenta y "
    "cada deseo dice si alcanza."
)


# ═══════════════════════════════════════════════════════════
# Saldo contra el que se mide
# ═══════════════════════════════════════════════════════════

if saldos.empty:
    st.warning(
        "Ninguna cuenta tiene saldo en el balance, así que no hay contra qué "
        "comparar. Liga tus cuentas en **Patrimonio** para verlo.",
        icon=":material/link_off:",
    )
    saldo_elegido = 0.0
    nombre_cuenta = "sin cuenta"
else:
    rotulo("Saldo disponible")

    with st.container(horizontal=True):
        st.metric(
            "Total en cuentas",
            moneda(float(saldos["saldo"].sum())),
            border=True,
            help="Suma de todas las cuentas activas con saldo en el balance.",
        )
        liquidas = saldos[saldos["liquidez"] == "Alta"]
        if not liquidas.empty:
            st.metric(
                "Disponible de inmediato",
                moneda(float(liquidas["saldo"].sum())),
                border=True,
                help="Sólo las cuentas de liquidez alta.",
            )
        st.metric("Cuentas", len(saldos), border=True)

    opciones = {"Todas las cuentas": float(saldos["saldo"].sum())} | {
        f"{fila.cuenta}": float(fila.saldo) for fila in saldos.itertuples()
    }
    nombre_cuenta = st.selectbox(
        "Medir contra",
        list(opciones),
        help="Los deseos de abajo se comparan contra el saldo de lo que elijas.",
    )
    saldo_elegido = opciones[nombre_cuenta]

    st.caption(
        f"Comparando contra **{moneda(saldo_elegido)}** de {nombre_cuenta.lower()}."
    )


deseos = servicios.deseos.listar(saldo_elegido)
resumen = servicios.deseos.resumen(saldo_elegido)

lista_tab, nuevo_tab, comprados_tab = st.tabs(
    ["Mi lista", "Agregar deseo", "Ya comprados"]
)


# ═══════════════════════════════════════════════════════════
# La lista, con su semáforo
# ═══════════════════════════════════════════════════════════

with lista_tab:
    if deseos.empty:
        st.info(
            "Tu lista está vacía. Agrega lo primero en la pestaña de al lado.",
            icon=":material/info:",
        )
    else:
        rotulo("Cómo va la lista")

        with st.container(horizontal=True):
            st.metric("Deseos", resumen["deseos"], border=True)
            st.metric("Cuestan", moneda(resumen["costo_total"]), border=True)
            st.metric(
                "Ya alcanzan",
                resumen["alcanzan"],
                delta=f"de {resumen['deseos']}",
                delta_color="off",
                border=True,
            )
            st.metric(
                "Falta en total",
                moneda(resumen["falta_total"]),
                border=True,
                help="Para poder comprarlo todo junto.",
            )

        rotulo("Tus deseos")

        colores = paleta()
        for fila in deseos.itertuples():
            alcance = str(fila.alcance)
            color = {
                str(EstadoDeseo.ALCANZA): colores.exito,
                str(EstadoDeseo.CASI): colores.atencion,
                str(EstadoDeseo.LEJOS): colores.error,
            }[alcance]
            icono = {
                str(EstadoDeseo.ALCANZA): ":material/check_circle:",
                str(EstadoDeseo.CASI): ":material/schedule:",
                str(EstadoDeseo.LEJOS): ":material/trending_up:",
            }[alcance]

            with st.container(border=True):
                cabecera = st.columns([3, 1, 1, 1])

                with cabecera[0]:
                    st.markdown(f"**{fila.nombre}**")
                    pie = f"{fila.prioridad}"
                    if fila.categoria:
                        pie += f" · {fila.categoria}"
                    pie += f" · {int(fila.dias_en_lista)} días en la lista"
                    st.caption(pie)

                with cabecera[1]:
                    st.metric("Cuesta", moneda(float(fila.costo)))

                with cabecera[2]:
                    if fila.faltante > 0:
                        st.metric("Falta", moneda(float(fila.faltante)))
                    else:
                        st.metric("Sobra", moneda(saldo_elegido - float(fila.costo)))

                with cabecera[3]:
                    # El color nunca va solo: lo acompaña el icono y el
                    # texto, para que se lea sin depender de distinguirlo.
                    st.markdown(
                        f'<div style="text-align:right;color:{color};'
                        f'font-weight:600;font-size:0.85rem;">{alcance}</div>',
                        unsafe_allow_html=True,
                    )

                st.progress(
                    float(fila.cobertura),
                    text=f"{porcentaje(float(fila.cobertura), 0)} cubierto",
                )

                acciones = st.columns([1, 1, 1, 3])

                with acciones[0]:
                    if st.button(
                        "Ya lo compré",
                        icon=icono,
                        key=f"comprar_{fila.id}",
                        type="primary"
                        if alcance == str(EstadoDeseo.ALCANZA)
                        else "secondary",
                    ):
                        servicios.deseos.marcar_comprado(int(fila.id))
                        invalidar_datos()
                        st.rerun()

                with acciones[1]:
                    if st.button(
                        "Quitar", icon=":material/delete:", key=f"del_{fila.id}"
                    ):
                        servicios.deseos.eliminar(int(fila.id))
                        invalidar_datos()
                        st.rerun()

                with acciones[2]:
                    if fila.enlace:
                        st.link_button(
                            "Ver", fila.enlace, icon=":material/open_in_new:"
                        )

                if fila.notas:
                    st.caption(fila.notas)


# ═══════════════════════════════════════════════════════════
# Agregar
# ═══════════════════════════════════════════════════════════

with nuevo_tab:
    with st.container(border=True):
        st.subheader("Agregar a la lista")

        opciones_categoria = {"— sin categoría —": None} | {
            fila.nombre: int(fila.id) for fila in catalogos["categorias"].itertuples()
        }

        with st.form("nuevo_deseo", clear_on_submit=True, border=False):
            fila_1 = st.columns([2, 1, 1])

            with fila_1[0]:
                nombre = st.text_input("Qué quieres", placeholder="Audífonos")
            with fila_1[1]:
                costo = st.number_input(
                    "Cuánto cuesta", min_value=0.0, step=100.0, format="%.2f"
                )
            with fila_1[2]:
                prioridad = st.selectbox(
                    "Prioridad", [str(valor) for valor in Prioridad], index=1
                )

            fila_2 = st.columns(2)
            with fila_2[0]:
                categoria = st.selectbox("Categoría", list(opciones_categoria))
            with fila_2[1]:
                enlace = st.text_input("Enlace", placeholder="https://…")

            notas = st.text_input("Notas", placeholder="Esperar a que baje")

            if st.form_submit_button(
                "Agregar a la lista", type="primary", icon=":material/add:"
            ):
                try:
                    servicios.deseos.agregar(
                        nombre=nombre,
                        costo=costo,
                        categoria_id=opciones_categoria[categoria],
                        prioridad=prioridad,
                        enlace=enlace,
                        notas=notas,
                    )
                except ValueError as error:
                    reportar_error(error)
                else:
                    invalidar_datos()
                    st.success(f"«{nombre}» agregado.", icon=":material/check_circle:")
                    st.rerun()


# ═══════════════════════════════════════════════════════════
# Historial
# ═══════════════════════════════════════════════════════════

with comprados_tab:
    todos = servicios.deseos.listar(saldo_elegido, incluir_comprados=True)
    comprados = todos[todos["comprado"]] if not todos.empty else todos

    if comprados.empty:
        st.info(
            "Aquí quedan los deseos que ya compraste, para ver con el tiempo "
            "qué quisiste y qué terminaste comprando.",
            icon=":material/info:",
        )
    else:
        with st.container(horizontal=True):
            st.metric("Comprados", len(comprados), border=True)
            st.metric(
                "Gastado en deseos",
                moneda(float(comprados["costo"].sum())),
                border=True,
            )

        with st.container(border=True):
            st.dataframe(
                comprados[["nombre", "costo", "categoria", "prioridad", "comprado_en"]],
                hide_index=True,
                column_config={
                    "nombre": st.column_config.TextColumn("Deseo", pinned=True),
                    "costo": st.column_config.NumberColumn("Costo", format="$%.2f"),
                    "categoria": st.column_config.TextColumn("Categoría"),
                    "prioridad": st.column_config.TextColumn("Prioridad"),
                    "comprado_en": st.column_config.DateColumn(
                        "Comprado el", format="DD/MM/YYYY"
                    ),
                },
            )

        devolver = st.selectbox("Devolver a la lista", comprados["nombre"].tolist())
        if st.button("Devolver", icon=":material/undo:"):
            deseo_id = int(comprados[comprados["nombre"] == devolver].iloc[0]["id"])
            servicios.deseos.devolver_a_la_lista(deseo_id)
            invalidar_datos()
            st.rerun()
