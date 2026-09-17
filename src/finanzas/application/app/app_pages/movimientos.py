from __future__ import annotations

from datetime import date, time, timedelta

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
        fila_1 = st.columns([1, 1, 2, 1])

        with fila_1[0]:
            fecha = st.date_input(
                "Fecha",
                value=date.today(),
                format="DD/MM/YYYY",
                help="Cuándo ocurrió el gasto. Es la que manda en el presupuesto.",
            )

        with fila_1[1]:
            hora = st.time_input(
                "Hora",
                value=None,
                step=300,
                help="Opcional. Ningún estado de cuenta la trae.",
            )

        with fila_1[2]:
            tipo = st.segmented_control(
                "Tipo",
                [str(valor) for valor in TipoMovimiento],
                default=str(TipoMovimiento.GASTO),
                key="mov_tipo",
            )

        with fila_1[3]:
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

        es_traspaso = tipo == str(TipoMovimiento.TRANSFERENCIA)

        with fila_2[2]:
            opciones_cuenta = _opciones(catalogos["cuentas"])
            cuenta = st.selectbox(
                "Cuenta de origen" if es_traspaso else "Cuenta",
                list(opciones_cuenta),
            )
            cuenta_id = opciones_cuenta[cuenta]

        # Sólo un traspaso tiene dos patas; en lo demás el dinero entra o
        # sale, no se mueve de un bolsillo propio a otro.
        cuenta_destino_id = None
        if es_traspaso:
            destino = st.columns([1, 2])

            with destino[0]:
                otras = {
                    nombre: identificador
                    for nombre, identificador in opciones_cuenta.items()
                    if identificador != cuenta_id
                }
                opciones_destino = {"— sin especificar —": None} | otras
                cuenta_destino = st.selectbox(
                    "Cuenta de destino",
                    list(opciones_destino),
                    help="A dónde llega el dinero que sale de la cuenta de origen.",
                )
                cuenta_destino_id = opciones_destino[cuenta_destino]

            with destino[1]:
                st.markdown("&nbsp;")
                if cuenta_destino_id is None:
                    st.warning(
                        "Sin destino, el traspaso no dice dónde acabó el dinero.",
                        icon=":material/help:",
                    )
                else:
                    st.info(
                        f"Sale de **{cuenta}** y entra a **{cuenta_destino}**. "
                        "El traspaso no es gasto ni ingreso.",
                        icon=":material/swap_horiz:",
                    )

        texto = st.columns([3, 2])

        with texto[0]:
            descripcion = st.text_input(
                "Descripción", placeholder="Supermercado quincenal", key="mov_desc"
            )

        with texto[1]:
            # Los lugares ya usados se ofrecen primero, por frecuencia: el
            # súper de siempre no se teclea dos veces distinto.
            lugares_previos = servicios.movimientos.lugares()
            lugar = st.selectbox(
                "Lugar",
                ["", *lugares_previos],
                accept_new_options=True,
                placeholder="Walmart Universidad",
                key="mov_lugar",
            )
            lugar = lugar or ""

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
                # Un proyecto escrito a mano cada vez se convierte en varios
                # proyectos por culpa de un acento o una mayúscula, así que
                # los ya usados se ofrecen para reutilizar.
                previos = servicios.movimientos.nombres_de_proyecto()
                proyecto = st.selectbox(
                    "Proyecto o persona",
                    ["— ninguno —", *previos],
                    accept_new_options=True,
                    help=(
                        "Agrupa movimientos de distintas categorías bajo un "
                        "mismo esfuerzo: un viaje, una mudanza, una obra. "
                        "Escribe uno nuevo para crearlo."
                    ),
                )
                if proyecto == "— ninguno —":
                    proyecto = ""
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

        # Fuera del expander de detalle: decidir si ya se pagó es parte de
        # la captura normal, no un ajuste fino.
        pago = st.columns([1, 1, 2])

        with pago[0]:
            ya_pagado = st.checkbox(
                "Ya se pagó",
                value=True,
                key="mov_pagado",
                help=(
                    "Desmárcalo para registrar un adeudo generado: el gasto "
                    "cuenta en el presupuesto de su fecha, pero no sale de la "
                    "caja hasta que lo pagues."
                ),
            )

        with pago[1]:
            if ya_pagado:
                fecha_pago = st.date_input(
                    "Fecha de pago",
                    value=fecha,
                    format="DD/MM/YYYY",
                    key="mov_fecha_pago",
                )
            else:
                fecha_pago = None
                st.markdown("&nbsp;")

        with pago[2]:
            if not ya_pagado:
                st.info(
                    "Queda como pendiente de pago y suma a tus adeudos.",
                    icon=":material/schedule:",
                )

        # ── Qué venía en la compra ───────────────────────
        #
        # El detalle de una compra de varias cosas. No parte el
        # movimiento: sigue siendo un gasto con su categoría, y esto
        # guarda qué había dentro. Se captura aquí, en memoria, y se
        # escribe después de crear el movimiento, que es cuando existe el
        # id del que cuelgan.

        detallar = st.toggle(
            "Apuntar los productos",
            key="mov_detallar",
            help=(
                "Para una compra de varias cosas. El gasto sigue siendo uno "
                "con su categoría; esto guarda qué venía dentro, y no hace "
                "falta listarlo todo."
            ),
        )

        productos_nuevos = None
        if detallar:
            with st.container(border=True):
                productos_nuevos = st.data_editor(
                    pd.DataFrame(
                        {
                            "producto": pd.Series(dtype="str"),
                            "cantidad": pd.Series(dtype="float"),
                            "precio_unitario": pd.Series(dtype="float"),
                            "nota": pd.Series(dtype="str"),
                        }
                    ),
                    num_rows="dynamic",
                    hide_index=True,
                    width="stretch",
                    key="mov_productos",
                    column_config={
                        "producto": st.column_config.TextColumn(
                            "Producto", width="large", required=True
                        ),
                        "cantidad": st.column_config.NumberColumn(
                            "Cantidad", min_value=0.01, default=1.0, format="%.2f"
                        ),
                        "precio_unitario": st.column_config.NumberColumn(
                            "Precio unitario",
                            min_value=0.0,
                            default=0.0,
                            format="$%.2f",
                        ),
                        "nota": st.column_config.TextColumn("Nota"),
                    },
                )

                suma_productos = float(
                    (
                        productos_nuevos["cantidad"].fillna(1)
                        * productos_nuevos["precio_unitario"].fillna(0)
                    ).sum()
                )
                resto_productos = round(float(monto) - suma_productos, 2)

                if resto_productos < -0.01:
                    st.warning(
                        f"Los productos suman {moneda(abs(resto_productos))} más "
                        "que el monto del movimiento.",
                        icon=":material/balance:",
                    )
                elif suma_productos and resto_productos > 0.01:
                    st.caption(
                        f"Detallado {moneda(suma_productos)} de {moneda(monto)} "
                        f"· quedan {moneda(resto_productos)} sin apuntar"
                    )
                elif suma_productos:
                    st.caption("El detalle cubre el movimiento completo.")

        if st.button("Registrar movimiento", type="primary", icon=":material/add:"):
            try:
                nuevo_id = servicios.movimientos.registrar(
                    fecha=fecha,
                    tipo=tipo,
                    monto=monto,
                    categoria_id=categoria_id,
                    cuenta_id=cuenta_id,
                    subcategoria_id=subcategoria_id,
                    cuenta_destino_id=cuenta_destino_id,
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
                    fecha_pago=fecha_pago,
                    lugar=lugar,
                    hora=hora,
                )
            except ValueError as error:
                reportar_error(error)
            else:
                cuantos = 0
                if productos_nuevos is not None:
                    cuantos = servicios.productos.reemplazar(nuevo_id, productos_nuevos)

                invalidar_datos()
                sufijo = "" if fecha_pago else " · pendiente de pago"
                if cuantos:
                    sufijo += f" · {cuantos} productos"
                st.success(
                    f"Movimiento {nuevo_id} registrado por {moneda(monto)}{sufijo}.",
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

        filtros_3 = st.columns([3, 2])

        with filtros_3[0]:
            proyectos_filtro = st.multiselect(
                "Proyecto", servicios.movimientos.nombres_de_proyecto()
            )

        with filtros_3[1]:
            st.markdown("&nbsp;")
            solo_por_pagar = st.checkbox(
                "Sólo lo que debo",
                help="Gastos ya incurridos que todavía no se han pagado.",
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
        solo_por_pagar=solo_por_pagar,
        proyectos=proyectos_filtro,
    )

    if movimientos.empty:
        st.info("Ningún movimiento coincide con los filtros.", icon=":material/info:")
        st.stop()

    totales = st.columns(5)
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
    adeudo = float(movimientos["por_pagar"].sum())
    totales[4].metric(
        "Por pagar",
        moneda(adeudo),
        delta=f"{int((movimientos['por_pagar'] > 0).sum())} movimientos",
        delta_color="off",
        border=True,
        help="Gasto ya incurrido que todavía no sale de la caja.",
    )

    seleccion = st.dataframe(
        movimientos[
            [
                "id",
                "fecha",
                "hora",
                "tipo",
                "categoria",
                "subcategoria",
                "descripcion",
                "lugar",
                "cuenta",
                "cuenta_destino",
                "medio_pago",
                "monto",
                "necesidad",
                "estado",
                "fecha_pago",
                "proyecto",
                "descripcion_banco",
            ]
        ],
        hide_index=True,
        on_select="rerun",
        selection_mode="multi-row",
        column_config={
            "id": st.column_config.NumberColumn("ID", width="small"),
            "fecha": st.column_config.DateColumn("Fecha", format="DD/MM/YYYY"),
            "hora": st.column_config.TextColumn("Hora", width="small"),
            "tipo": st.column_config.TextColumn("Tipo"),
            "categoria": st.column_config.TextColumn("Categoría"),
            "subcategoria": st.column_config.TextColumn("Subcategoría"),
            "descripcion": st.column_config.TextColumn("Descripción", width="medium"),
            "lugar": st.column_config.TextColumn("Lugar"),
            "cuenta": st.column_config.TextColumn("Cuenta"),
            "cuenta_destino": st.column_config.TextColumn(
                "Destino", help="Sólo en traspasos: a dónde llegó el dinero."
            ),
            "medio_pago": st.column_config.TextColumn("Medio de pago"),
            "monto": st.column_config.NumberColumn("Monto", format="$%.2f"),
            "necesidad": st.column_config.TextColumn("Necesidad"),
            "estado": st.column_config.TextColumn("Estado"),
            "fecha_pago": st.column_config.DateColumn(
                "Pagado el",
                format="DD/MM/YYYY",
                help="Vacío significa que el gasto sigue pendiente de pago.",
            ),
            "proyecto": st.column_config.TextColumn("Proyecto"),
            "descripcion_banco": st.column_config.TextColumn(
                "Concepto del banco",
                width="medium",
                help=(
                    "Texto original del estado de cuenta. No se edita: "
                    "sirve para casar el movimiento con su línea del "
                    "documento."
                ),
            ),
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

        deben = elegidos[elegidos["por_pagar"] > 0]
        if not deben.empty:
            st.caption(
                f"{len(deben)} están pendientes de pago por "
                f"{moneda(float(deben['por_pagar'].sum()))}."
            )

        with st.container(horizontal=True):
            if not deben.empty and st.button(
                "Marcar pagados hoy", type="primary", icon=":material/payments:"
            ):
                for identificador in deben["id"]:
                    servicios.movimientos.marcar_pagado(int(identificador))
                invalidar_datos()
                st.success(
                    f"{len(deben)} movimientos marcados como pagados.",
                    icon=":material/check:",
                )
                st.rerun()

            if st.button(
                "Eliminar seleccionados", type="secondary", icon=":material/delete:"
            ):
                borrados = servicios.movimientos.eliminar_muchos(
                    [int(valor) for valor in elegidos["id"]]
                )
                invalidar_datos()
                st.success(
                    f"{borrados} movimientos eliminados.", icon=":material/check:"
                )
                st.rerun()

        st.stop()

    actual = elegidos.iloc[0]
    movimiento_id = int(actual["id"])

    with st.container(border=True):
        st.markdown(f"**Editar movimiento {movimiento_id}**")

        edicion_1 = st.columns([1, 1, 1, 1])

        with edicion_1[0]:
            nueva_fecha = st.date_input(
                "Fecha",
                value=actual["fecha"].date(),
                format="DD/MM/YYYY",
                key=f"edit_fecha_{movimiento_id}",
            )

        with edicion_1[1]:
            hora_actual = actual["hora"]
            nueva_hora = st.time_input(
                "Hora",
                value=time.fromisoformat(hora_actual)
                if isinstance(hora_actual, str) and hora_actual
                else None,
                step=300,
                key=f"edit_hora_{movimiento_id}",
            )

        with edicion_1[2]:
            nuevo_monto = st.number_input(
                "Monto",
                min_value=0.0,
                value=float(actual["monto"]),
                step=50.0,
                format="%.2f",
                key=f"edit_monto_{movimiento_id}",
            )

        with edicion_1[3]:
            nuevo_estado = st.selectbox(
                "Estado",
                [str(valor) for valor in EstadoMovimiento],
                index=[str(valor) for valor in EstadoMovimiento].index(
                    actual["estado"]
                ),
                key=f"edit_estado_{movimiento_id}",
            )

        pagado_actual = pd.notna(actual["fecha_pago"])
        edicion_pago = st.columns([1, 1, 1])

        with edicion_pago[0]:
            sigue_pagado = st.checkbox(
                "Ya se pagó",
                value=bool(pagado_actual),
                key=f"edit_pagado_{movimiento_id}",
            )

        with edicion_pago[1]:
            if sigue_pagado:
                nueva_fecha_pago = st.date_input(
                    "Fecha de pago",
                    value=actual["fecha_pago"].date()
                    if pagado_actual
                    else date.today(),
                    format="DD/MM/YYYY",
                    key=f"edit_fpago_{movimiento_id}",
                )
            else:
                nueva_fecha_pago = None

        with edicion_pago[2]:
            if not sigue_pagado:
                st.markdown("&nbsp;")
                st.caption("Queda como adeudo generado.")

        texto_edicion = st.columns([3, 2])

        with texto_edicion[0]:
            nueva_descripcion = st.text_input(
                "Descripción",
                value=actual["descripcion"],
                key=f"edit_desc_{movimiento_id}",
            )

        with texto_edicion[1]:
            lugares_edicion = servicios.movimientos.lugares()
            lugar_actual = actual["lugar"] or ""
            opciones_lugar = ["", *lugares_edicion]
            if lugar_actual and lugar_actual not in opciones_lugar:
                opciones_lugar.insert(1, lugar_actual)
            nuevo_lugar = st.selectbox(
                "Lugar",
                opciones_lugar,
                index=opciones_lugar.index(lugar_actual)
                if lugar_actual in opciones_lugar
                else 0,
                accept_new_options=True,
                key=f"edit_lugar_{movimiento_id}",
            )
            nuevo_lugar = nuevo_lugar or ""

        if actual["descripcion_banco"]:
            pie = f"Del banco: **{actual['descripcion_banco']}**"
            if actual["referencia_externa"]:
                pie += f" · folio `{actual['referencia_externa']}`"
            st.caption(pie)

        previos_edicion = servicios.movimientos.nombres_de_proyecto()
        opciones_proyecto = ["— ninguno —", *previos_edicion]
        nuevo_proyecto = st.selectbox(
            "Proyecto",
            opciones_proyecto,
            index=opciones_proyecto.index(actual["proyecto"])
            if actual["proyecto"] in opciones_proyecto
            else 0,
            accept_new_options=True,
            key=f"edit_proy_{movimiento_id}",
        )
        if nuevo_proyecto == "— ninguno —":
            nuevo_proyecto = ""

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

        with st.container(horizontal=True):
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
                        fecha_pago=nueva_fecha_pago,
                        proyecto=nuevo_proyecto,
                        lugar=nuevo_lugar,
                        hora=nueva_hora,
                    )
                except ValueError as error:
                    reportar_error(error)
                else:
                    invalidar_datos()
                    st.success("Movimiento actualizado.", icon=":material/check:")
                    st.rerun()

            if st.button("Duplicar hoy", icon=":material/content_copy:"):
                servicios.movimientos.duplicar(movimiento_id, date.today())
                invalidar_datos()
                st.success("Movimiento duplicado con la fecha de hoy.")
                st.rerun()

            if st.button("Eliminar", icon=":material/delete:"):
                servicios.movimientos.eliminar(movimiento_id)
                invalidar_datos()
                st.success("Movimiento eliminado.", icon=":material/check:")
                st.rerun()

        # El detalle de una compra de varias cosas. Vive aparte del
        # movimiento y no lo parte: sigue siendo un gasto con su categoría.
        with st.expander("Productos de esta compra", icon=":material/list_alt:"):
            productos = servicios.productos.de_movimiento(movimiento_id)
            base = (
                productos[["producto", "cantidad", "precio_unitario", "nota"]]
                if not productos.empty
                else pd.DataFrame(
                    columns=["producto", "cantidad", "precio_unitario", "nota"]
                )
            )

            st.caption(
                "Apunta qué venía dentro. No hace falta listarlo todo: el "
                "detalle puede quedarse a medias."
            )

            editados = st.data_editor(
                base,
                num_rows="dynamic",
                hide_index=True,
                width="stretch",
                key=f"prods_{movimiento_id}",
                column_config={
                    "producto": st.column_config.TextColumn(
                        "Producto", width="large", required=True
                    ),
                    "cantidad": st.column_config.NumberColumn(
                        "Cantidad", min_value=0.01, default=1.0, format="%.2f"
                    ),
                    "precio_unitario": st.column_config.NumberColumn(
                        "Precio unitario", min_value=0.0, default=0.0, format="$%.2f"
                    ),
                    "nota": st.column_config.TextColumn("Nota"),
                },
            )

            suma = float(
                (
                    editados["cantidad"].fillna(1)
                    * editados["precio_unitario"].fillna(0)
                ).sum()
            )
            resto = round(float(actual["monto"]) - suma, 2)

            if resto < -0.01:
                st.warning(
                    f"Los productos suman {moneda(abs(resto))} más de lo que "
                    "costó el movimiento.",
                    icon=":material/balance:",
                )
            elif resto > 0.01:
                st.caption(
                    f"Detallado {moneda(suma)} de {moneda(float(actual['monto']))} "
                    f"· quedan {moneda(resto)} sin apuntar"
                )
            elif suma:
                st.caption("El detalle cubre el movimiento completo.")

            if st.button("Guardar productos", icon=":material/save:"):
                servicios.productos.reemplazar(movimiento_id, editados)
                invalidar_datos()
                st.success("Productos guardados.", icon=":material/check:")
                st.rerun()

        if float(actual["por_pagar"]) > 0:
            st.info(
                f"Este gasto lleva pendiente de pago desde el "
                f"{actual['fecha'].strftime('%d/%m/%Y')}.",
                icon=":material/schedule:",
            )
            if st.button(
                "Marcar como pagado hoy", type="primary", icon=":material/payments:"
            ):
                servicios.movimientos.marcar_pagado(movimiento_id)
                invalidar_datos()
                st.success("Movimiento marcado como pagado.", icon=":material/check:")
                st.rerun()
