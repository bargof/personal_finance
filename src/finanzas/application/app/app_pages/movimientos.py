from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from finanzas.application.app.components import (
    invalidar_datos,
    moneda,
    obtener_servicios,
    reportar_error,
)
from finanzas.domain.enums import (
    EstadoMovimiento,
    Naturaleza,
    Necesidad,
    TipoMovimiento,
)

# ═══════════════════════════════════════════════════════════
# Movimientos: capturar, explorar y corregir
#
# El formulario no usa `st.form` a propósito: categoría y
# subcategoría se encadenan, y eso exige que cada cambio
# vuelva a ejecutar el script.
# ═══════════════════════════════════════════════════════════

servicios = obtener_servicios()
catalogos = servicios.catalogos.opciones_captura()

st.title("Movimientos")
st.caption(
    "Captura el monto siempre en positivo: el tipo define si suma o resta. "
    "Usa **Transferencia** al mover dinero entre tus propias cuentas."
)

if catalogos["cuentas"].empty or catalogos["categorias"].empty:
    st.warning(
        "Primero da de alta al menos una categoría y una cuenta en **Catálogos**.",
        icon=":material/warning:",
    )
    st.stop()


def _opciones(df: pd.DataFrame) -> dict[str, int]:
    """Convierte un catálogo en {nombre: id} para alimentar un selector."""
    return {fila.nombre: int(fila.id) for fila in df.itertuples()}


def _categorias_de(tipo: str) -> pd.DataFrame:
    """
    Devuelve las categorías del tipo elegido.

    Si el tipo aún no tiene categorías propias, se muestran todas para no
    dejar al usuario sin poder capturar.
    """
    df = catalogos["categorias"]
    propias = df[df["tipo"] == tipo]

    return propias if not propias.empty else df


registrar, explorar = st.tabs(["Registrar", "Explorar y editar"])


# ═══════════════════════════════════════════════════════════
# Registrar
# ═══════════════════════════════════════════════════════════

with registrar:
    with st.container(border=True):
        fila_1 = st.columns([1, 2, 1])

        with fila_1[0]:
            fecha = st.date_input("Fecha", value=date.today(), format="DD/MM/YYYY")

        with fila_1[1]:
            tipo = st.segmented_control(
                "Tipo",
                [str(valor) for valor in TipoMovimiento],
                default=str(TipoMovimiento.GASTO),
                key="mov_tipo",
            )

        with fila_1[2]:
            monto = st.number_input(
                "Monto", min_value=0.0, step=50.0, format="%.2f", key="mov_monto"
            )

        tipo = tipo or str(TipoMovimiento.GASTO)
        categorias_tipo = _categorias_de(tipo)
        opciones_categoria = _opciones(categorias_tipo)

        fila_2 = st.columns(3)

        with fila_2[0]:
            categoria = st.selectbox("Categoría", list(opciones_categoria))
            categoria_id = opciones_categoria[categoria]

        with fila_2[1]:
            subcategorias = catalogos["subcategorias"]
            propias = subcategorias[subcategorias["categoria_id"] == categoria_id]
            opciones_sub = {"— sin subcategoría —": None} | _opciones(propias)
            subcategoria = st.selectbox("Subcategoría", list(opciones_sub))
            subcategoria_id = opciones_sub[subcategoria]

        with fila_2[2]:
            opciones_cuenta = _opciones(catalogos["cuentas"])
            cuenta = st.selectbox("Cuenta", list(opciones_cuenta))
            cuenta_id = opciones_cuenta[cuenta]

        descripcion = st.text_input(
            "Descripción", placeholder="Supermercado quincenal", key="mov_desc"
        )

        fila_3 = st.columns(3)

        with fila_3[0]:
            opciones_medio = {"— sin especificar —": None} | _opciones(
                catalogos["medios_pago"]
            )
            medio = st.selectbox("Medio de pago", list(opciones_medio))
            medio_pago_id = opciones_medio[medio]

        with fila_3[1]:
            necesidad = st.segmented_control(
                "Esencial o deseo",
                [str(valor) for valor in Necesidad],
                default=str(Necesidad.ESENCIAL),
            )

        with fila_3[2]:
            naturaleza = st.segmented_control(
                "Fijo o variable",
                [str(valor) for valor in Naturaleza],
                default=str(Naturaleza.VARIABLE),
            )

        with st.expander("Detalle adicional", icon=":material/more_horiz:"):
            fila_4 = st.columns(3)

            with fila_4[0]:
                proyecto = st.text_input("Proyecto o persona", placeholder="Hogar")
            with fila_4[1]:
                etiquetas = st.text_input("Etiquetas", placeholder="despensa, quincena")
            with fila_4[2]:
                estado = st.segmented_control(
                    "Estado",
                    [str(valor) for valor in EstadoMovimiento],
                    default=str(EstadoMovimiento.CONFIRMADO),
                )

            nota = st.text_area("Nota o comprobante", height=80)

            banderas = st.columns(2)
            with banderas[0]:
                recurrente = st.checkbox("Es un gasto recurrente")
            with banderas[1]:
                planeado = st.checkbox("Estaba planeado", value=True)

        if st.button("Registrar movimiento", type="primary", icon=":material/add:"):
            try:
                nuevo_id = servicios.movimientos.registrar(
                    fecha=fecha,
                    tipo=tipo,
                    monto=monto,
                    categoria_id=categoria_id,
                    cuenta_id=cuenta_id,
                    subcategoria_id=subcategoria_id,
                    medio_pago_id=medio_pago_id,
                    descripcion=descripcion,
                    necesidad=necesidad or str(Necesidad.ESENCIAL),
                    naturaleza=naturaleza or str(Naturaleza.VARIABLE),
                    recurrente=recurrente,
                    planeado=planeado,
                    proyecto=proyecto,
                    etiquetas=etiquetas,
                    nota=nota,
                    estado=estado or str(EstadoMovimiento.CONFIRMADO),
                )
            except ValueError as error:
                reportar_error(error)
            else:
                invalidar_datos()
                st.success(
                    f"Movimiento {nuevo_id} registrado por {moneda(monto)}.",
                    icon=":material/check_circle:",
                )


# ═══════════════════════════════════════════════════════════
# Explorar y editar
# ═══════════════════════════════════════════════════════════

with explorar:
    with st.container(border=True):
        st.markdown("**Filtros**")
        filtros_1 = st.columns([2, 2, 2])

        with filtros_1[0]:
            rango = st.date_input(
                "Rango de fechas",
                value=(date.today() - timedelta(days=90), date.today()),
                format="DD/MM/YYYY",
            )

        with filtros_1[1]:
            tipos_filtro = st.multiselect(
                "Tipo", [str(valor) for valor in TipoMovimiento]
            )

        with filtros_1[2]:
            categorias_filtro = st.multiselect(
                "Categoría", catalogos["categorias"]["nombre"].tolist()
            )

        filtros_2 = st.columns([2, 2, 3])

        with filtros_2[0]:
            cuentas_filtro = st.multiselect(
                "Cuenta", catalogos["cuentas"]["nombre"].tolist()
            )

        with filtros_2[1]:
            estado_filtro = st.selectbox(
                "Estado", ["Todos", *[str(valor) for valor in EstadoMovimiento]]
            )

        with filtros_2[2]:
            texto = st.text_input(
                "Buscar en descripción, etiquetas y notas", placeholder="café"
            )

    desde, hasta = (
        rango if isinstance(rango, tuple) and len(rango) == 2 else (None, None)
    )

    movimientos = servicios.movimientos.buscar(
        desde=desde,
        hasta=hasta,
        tipos=tipos_filtro,
        categorias=categorias_filtro,
        cuentas=cuentas_filtro,
        estado=None if estado_filtro == "Todos" else estado_filtro,
        texto=texto or None,
        limite=500,
    )

    if movimientos.empty:
        st.info("Ningún movimiento coincide con los filtros.", icon=":material/info:")
        st.stop()

    totales = st.columns(4)
    totales[0].metric("Movimientos", len(movimientos), border=True)
    totales[1].metric(
        "Ingresos", moneda(movimientos["ingreso_real"].sum()), border=True
    )
    totales[2].metric("Gastos", moneda(movimientos["gasto_real"].sum()), border=True)
    totales[3].metric(
        "Ahorro e inversión",
        moneda(movimientos["patrimonio_creado"].sum()),
        border=True,
    )

    seleccion = st.dataframe(
        movimientos[
            [
                "id",
                "fecha",
                "tipo",
                "categoria",
                "subcategoria",
                "descripcion",
                "cuenta",
                "medio_pago",
                "monto",
                "necesidad",
                "estado",
            ]
        ],
        hide_index=True,
        on_select="rerun",
        selection_mode="multi-row",
        column_config={
            "id": st.column_config.NumberColumn("ID", width="small"),
            "fecha": st.column_config.DateColumn("Fecha", format="DD/MM/YYYY"),
            "tipo": st.column_config.TextColumn("Tipo"),
            "categoria": st.column_config.TextColumn("Categoría"),
            "subcategoria": st.column_config.TextColumn("Subcategoría"),
            "descripcion": st.column_config.TextColumn("Descripción", width="medium"),
            "cuenta": st.column_config.TextColumn("Cuenta"),
            "medio_pago": st.column_config.TextColumn("Medio de pago"),
            "monto": st.column_config.NumberColumn("Monto", format="$%.2f"),
            "necesidad": st.column_config.TextColumn("Necesidad"),
            "estado": st.column_config.TextColumn("Estado"),
        },
    )

    filas = seleccion.selection.rows
    if not filas:
        st.caption("Selecciona una fila para editarla, duplicarla o eliminarla.")
        st.stop()

    elegidos = movimientos.iloc[filas]

    st.divider()

    if len(elegidos) > 1:
        st.markdown(f"**{len(elegidos)} movimientos seleccionados**")
        st.caption(f"Suman {moneda(elegidos['monto'].sum())}.")

        if st.button(
            "Eliminar seleccionados", type="secondary", icon=":material/delete:"
        ):
            borrados = servicios.movimientos.eliminar_muchos(
                [int(valor) for valor in elegidos["id"]]
            )
            invalidar_datos()
            st.success(f"{borrados} movimientos eliminados.", icon=":material/check:")
            st.rerun()

        st.stop()

    actual = elegidos.iloc[0]
    movimiento_id = int(actual["id"])

    with st.container(border=True):
        st.markdown(f"**Editar movimiento {movimiento_id}**")

        edicion_1 = st.columns([1, 1, 1])

        with edicion_1[0]:
            nueva_fecha = st.date_input(
                "Fecha",
                value=actual["fecha"].date(),
                format="DD/MM/YYYY",
                key=f"edit_fecha_{movimiento_id}",
            )

        with edicion_1[1]:
            nuevo_monto = st.number_input(
                "Monto",
                min_value=0.0,
                value=float(actual["monto"]),
                step=50.0,
                format="%.2f",
                key=f"edit_monto_{movimiento_id}",
            )

        with edicion_1[2]:
            nuevo_estado = st.selectbox(
                "Estado",
                [str(valor) for valor in EstadoMovimiento],
                index=[str(valor) for valor in EstadoMovimiento].index(
                    actual["estado"]
                ),
                key=f"edit_estado_{movimiento_id}",
            )

        nueva_descripcion = st.text_input(
            "Descripción",
            value=actual["descripcion"],
            key=f"edit_desc_{movimiento_id}",
        )

        edicion_2 = st.columns(2)

        with edicion_2[0]:
            todas = catalogos["categorias"]
            opciones_edicion = _opciones(todas)
            nombres = list(opciones_edicion)
            nueva_categoria = st.selectbox(
                "Categoría",
                nombres,
                index=nombres.index(actual["categoria"])
                if actual["categoria"] in nombres
                else 0,
                key=f"edit_cat_{movimiento_id}",
            )

        with edicion_2[1]:
            opciones_cuenta_edicion = _opciones(catalogos["cuentas"])
            nombres_cuenta = list(opciones_cuenta_edicion)
            nueva_cuenta = st.selectbox(
                "Cuenta",
                nombres_cuenta,
                index=nombres_cuenta.index(actual["cuenta"])
                if actual["cuenta"] in nombres_cuenta
                else 0,
                key=f"edit_cuenta_{movimiento_id}",
            )

        acciones = st.columns(3)

        with acciones[0]:
            if st.button("Guardar cambios", type="primary", icon=":material/save:"):
                try:
                    servicios.movimientos.actualizar(
                        movimiento_id,
                        fecha=nueva_fecha,
                        monto=nuevo_monto,
                        estado=nuevo_estado,
                        descripcion=nueva_descripcion,
                        categoria_id=opciones_edicion[nueva_categoria],
                        cuenta_id=opciones_cuenta_edicion[nueva_cuenta],
                    )
                except ValueError as error:
                    reportar_error(error)
                else:
                    invalidar_datos()
                    st.success("Movimiento actualizado.", icon=":material/check:")
                    st.rerun()

        with acciones[1]:
            if st.button("Duplicar hoy", icon=":material/content_copy:"):
                servicios.movimientos.duplicar(movimiento_id, date.today())
                invalidar_datos()
                st.success("Movimiento duplicado con la fecha de hoy.")
                st.rerun()

        with acciones[2]:
            if st.button("Eliminar", icon=":material/delete:"):
                servicios.movimientos.eliminar(movimiento_id)
                invalidar_datos()
                st.success("Movimiento eliminado.", icon=":material/check:")
                st.rerun()
