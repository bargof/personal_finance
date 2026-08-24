from __future__ import annotations

import streamlit as st

from finanzas.application.app.components import (
    invalidar_datos,
    obtener_servicios,
    reportar_error,
)
from finanzas.domain.enums import TipoMovimiento

# ═══════════════════════════════════════════════════════════
# Catálogos: las listas maestras que mantienen la captura
# consistente y, por lo tanto, el análisis comparable.
# ═══════════════════════════════════════════════════════════

servicios = obtener_servicios()

st.title("Catálogos")
st.caption(
    "Una categoría con movimientos no se elimina: se desactiva, para no "
    "perder el histórico que ya la usa."
)

categorias_tab, subcategorias_tab, cuentas_tab, medios_tab = st.tabs(
    ["Categorías", "Subcategorías", "Cuentas", "Medios de pago"]
)


# ═══════════════════════════════════════════════════════════
# Categorías
# ═══════════════════════════════════════════════════════════

with categorias_tab:
    categorias = servicios.catalogos.categorias(solo_activas=False)

    with st.container(border=True):
        st.subheader("Categorías registradas")
        st.dataframe(
            categorias,
            hide_index=True,
            column_config={
                "id": st.column_config.NumberColumn("ID", width="small"),
                "nombre": st.column_config.TextColumn("Nombre", pinned=True),
                "tipo": st.column_config.TextColumn("Tipo"),
                "activa": st.column_config.CheckboxColumn("Activa"),
                "orden": st.column_config.NumberColumn("Orden"),
                "movimientos": st.column_config.NumberColumn("Movimientos"),
            },
        )

    izquierda, derecha = st.columns(2)

    with izquierda, st.container(border=True):
        st.subheader("Nueva categoría")

        with st.form("nueva_categoria", clear_on_submit=True, border=False):
            nombre = st.text_input("Nombre", placeholder="Mascotas")
            tipo = st.selectbox("Tipo", [str(valor) for valor in TipoMovimiento])
            orden = st.number_input("Orden", min_value=0, value=99, step=1)

            if st.form_submit_button(
                "Crear categoría", type="primary", icon=":material/add:"
            ):
                try:
                    servicios.catalogos.crear_categoria(nombre, tipo, int(orden))
                except ValueError as error:
                    reportar_error(error)
                else:
                    invalidar_datos()
                    st.success(f"Categoría «{nombre}» creada.", icon=":material/check:")
                    st.rerun()

    with derecha, st.container(border=True):
        st.subheader("Editar categoría")

        if categorias.empty:
            st.caption("Todavía no hay categorías.")
        else:
            opciones = {fila.nombre: int(fila.id) for fila in categorias.itertuples()}
            elegida = st.selectbox("Categoría", list(opciones), key="cat_editar")
            categoria_id = opciones[elegida]
            actual = categorias[categorias["id"] == categoria_id].iloc[0]

            nuevo_nombre = st.text_input(
                "Nombre", value=actual["nombre"], key=f"cat_nombre_{categoria_id}"
            )
            tipos = [str(valor) for valor in TipoMovimiento]
            nuevo_tipo = st.selectbox(
                "Tipo",
                tipos,
                index=tipos.index(actual["tipo"]),
                key=f"cat_tipo_{categoria_id}",
            )
            activa = st.checkbox(
                "Activa",
                value=bool(actual["activa"]),
                key=f"cat_activa_{categoria_id}",
                help="Una categoría inactiva no aparece al capturar, pero "
                "conserva su historial.",
            )
            nuevo_orden = st.number_input(
                "Orden",
                min_value=0,
                value=int(actual["orden"]),
                step=1,
                key=f"cat_orden_{categoria_id}",
            )

            acciones = st.columns(2)

            with acciones[0]:
                if st.button("Guardar", type="primary", icon=":material/save:"):
                    try:
                        servicios.catalogos.actualizar_categoria(
                            categoria_id,
                            nuevo_nombre,
                            nuevo_tipo,
                            activa,
                            int(nuevo_orden),
                        )
                    except ValueError as error:
                        reportar_error(error)
                    else:
                        invalidar_datos()
                        st.success("Categoría actualizada.", icon=":material/check:")
                        st.rerun()

            with acciones[1]:
                if st.button("Eliminar", icon=":material/delete:"):
                    try:
                        servicios.catalogos.eliminar_categoria(categoria_id)
                    except RuntimeError as error:
                        reportar_error(error)
                    else:
                        invalidar_datos()
                        st.success("Categoría eliminada.", icon=":material/check:")
                        st.rerun()


# ═══════════════════════════════════════════════════════════
# Subcategorías
# ═══════════════════════════════════════════════════════════

with subcategorias_tab:
    categorias = servicios.catalogos.categorias(solo_activas=False)

    if categorias.empty:
        st.info("Crea primero una categoría.", icon=":material/info:")
    else:
        opciones = {fila.nombre: int(fila.id) for fila in categorias.itertuples()}
        padre = st.selectbox("Categoría", list(opciones), key="sub_padre")
        categoria_id = opciones[padre]
        subcategorias = servicios.catalogos.subcategorias(categoria_id)

        izquierda, derecha = st.columns(2)

        with izquierda, st.container(border=True):
            st.subheader(f"Subcategorías de {padre}")

            if subcategorias.empty:
                st.caption("Esta categoría aún no tiene subcategorías.")
            else:
                st.dataframe(
                    subcategorias[["id", "nombre", "activa"]],
                    hide_index=True,
                    column_config={
                        "id": st.column_config.NumberColumn("ID", width="small"),
                        "nombre": st.column_config.TextColumn("Nombre"),
                        "activa": st.column_config.CheckboxColumn("Activa"),
                    },
                )

        with derecha, st.container(border=True):
            st.subheader("Agregar y quitar")

            nueva = st.text_input("Nueva subcategoría", placeholder="Veterinario")
            if st.button("Agregar", type="primary", icon=":material/add:"):
                try:
                    servicios.catalogos.crear_subcategoria(categoria_id, nueva)
                except ValueError as error:
                    reportar_error(error)
                else:
                    invalidar_datos()
                    st.success(f"«{nueva}» agregada.", icon=":material/check:")
                    st.rerun()

            if not subcategorias.empty:
                st.divider()
                a_borrar = {
                    fila.nombre: int(fila.id) for fila in subcategorias.itertuples()
                }
                elegida = st.selectbox(
                    "Eliminar subcategoría", list(a_borrar), key="sub_borrar"
                )
                st.caption(
                    "Los movimientos que la usaban conservan su categoría y "
                    "quedan sin subcategoría."
                )
                if st.button("Eliminar subcategoría", icon=":material/delete:"):
                    servicios.catalogos.eliminar_subcategoria(a_borrar[elegida])
                    invalidar_datos()
                    st.success(f"«{elegida}» eliminada.", icon=":material/check:")
                    st.rerun()


# ═══════════════════════════════════════════════════════════
# Cuentas
# ═══════════════════════════════════════════════════════════

with cuentas_tab:
    cuentas = servicios.catalogos.cuentas(solo_activas=False)

    with st.container(border=True):
        st.subheader("Cuentas registradas")
        st.dataframe(
            cuentas,
            hide_index=True,
            column_config={
                "id": st.column_config.NumberColumn("ID", width="small"),
                "nombre": st.column_config.TextColumn("Nombre", pinned=True),
                "tipo": st.column_config.TextColumn("Tipo"),
                "institucion": st.column_config.TextColumn("Institución"),
                "activa": st.column_config.CheckboxColumn("Activa"),
            },
        )

    izquierda, derecha = st.columns(2)

    with izquierda, st.container(border=True):
        st.subheader("Nueva cuenta")

        with st.form("nueva_cuenta", clear_on_submit=True, border=False):
            nombre = st.text_input("Nombre", placeholder="Cuenta nómina")
            tipo = st.selectbox(
                "Tipo", ("Banco", "Efectivo", "Crédito", "Inversión", "Otro")
            )
            institucion = st.text_input("Institución", placeholder="Banco")

            if st.form_submit_button(
                "Crear cuenta", type="primary", icon=":material/add:"
            ):
                try:
                    servicios.catalogos.crear_cuenta(nombre, tipo, institucion)
                except ValueError as error:
                    reportar_error(error)
                else:
                    invalidar_datos()
                    st.success(f"Cuenta «{nombre}» creada.", icon=":material/check:")
                    st.rerun()

    with derecha, st.container(border=True):
        st.subheader("Editar cuenta")

        if cuentas.empty:
            st.caption("Todavía no hay cuentas.")
        else:
            opciones = {fila.nombre: int(fila.id) for fila in cuentas.itertuples()}
            elegida = st.selectbox("Cuenta", list(opciones), key="cuenta_editar")
            cuenta_id = opciones[elegida]
            actual = cuentas[cuentas["id"] == cuenta_id].iloc[0]

            nuevo_nombre = st.text_input(
                "Nombre", value=actual["nombre"], key=f"cuenta_nombre_{cuenta_id}"
            )
            tipos = ["Banco", "Efectivo", "Crédito", "Inversión", "Otro"]
            nuevo_tipo = st.selectbox(
                "Tipo",
                tipos,
                index=tipos.index(actual["tipo"]) if actual["tipo"] in tipos else 4,
                key=f"cuenta_tipo_{cuenta_id}",
            )
            nueva_institucion = st.text_input(
                "Institución",
                value=actual["institucion"],
                key=f"cuenta_inst_{cuenta_id}",
            )
            activa = st.checkbox(
                "Activa", value=bool(actual["activa"]), key=f"cuenta_activa_{cuenta_id}"
            )

            acciones = st.columns(2)

            with acciones[0]:
                if st.button(
                    "Guardar", type="primary", icon=":material/save:", key="cuenta_save"
                ):
                    try:
                        servicios.catalogos.actualizar_cuenta(
                            cuenta_id,
                            nuevo_nombre,
                            nuevo_tipo,
                            nueva_institucion,
                            activa,
                        )
                    except ValueError as error:
                        reportar_error(error)
                    else:
                        invalidar_datos()
                        st.success("Cuenta actualizada.", icon=":material/check:")
                        st.rerun()

            with acciones[1]:
                if st.button("Eliminar", icon=":material/delete:", key="cuenta_delete"):
                    try:
                        servicios.catalogos.eliminar_cuenta(cuenta_id)
                    except RuntimeError as error:
                        reportar_error(error)
                    else:
                        invalidar_datos()
                        st.success("Cuenta eliminada.", icon=":material/check:")
                        st.rerun()


# ═══════════════════════════════════════════════════════════
# Medios de pago
# ═══════════════════════════════════════════════════════════

with medios_tab:
    medios = servicios.catalogos.medios_pago(solo_activos=False)

    izquierda, derecha = st.columns(2)

    with izquierda, st.container(border=True):
        st.subheader("Medios de pago")
        st.dataframe(
            medios,
            hide_index=True,
            column_config={
                "id": st.column_config.NumberColumn("ID", width="small"),
                "nombre": st.column_config.TextColumn("Nombre"),
                "activo": st.column_config.CheckboxColumn("Activo"),
            },
        )

    with derecha, st.container(border=True):
        st.subheader("Agregar y quitar")

        nuevo = st.text_input("Nuevo medio de pago", placeholder="Vales")
        if st.button("Agregar medio", type="primary", icon=":material/add:"):
            try:
                servicios.catalogos.crear_medio_pago(nuevo)
            except ValueError as error:
                reportar_error(error)
            else:
                invalidar_datos()
                st.success(f"«{nuevo}» agregado.", icon=":material/check:")
                st.rerun()

        if not medios.empty:
            st.divider()
            opciones = {fila.nombre: int(fila.id) for fila in medios.itertuples()}
            elegido = st.selectbox("Eliminar medio", list(opciones), key="medio_borrar")
            st.caption(
                "Los movimientos que lo usaban quedan sin medio de pago, pero "
                "conservan todo lo demás."
            )
            if st.button("Eliminar medio", icon=":material/delete:"):
                servicios.catalogos.eliminar_medio_pago(opciones[elegido])
                invalidar_datos()
                st.success(f"«{elegido}» eliminado.", icon=":material/check:")
                st.rerun()
