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
from finanzas.domain import captura
from finanzas.domain.enums import (
    EstadoMovimiento,
    Naturaleza,
    Necesidad,
    TipoCuenta,
    TipoMovimiento,
)

# ═══════════════════════════════════════════════════════════
# Movimientos: capturar, explorar y corregir
#
# Capturar y editar dibujan el mismo formulario (`_formulario`):
# las mismas dos cajas, los mismos campos según el tipo y las
# mismas reglas. Sólo cambia de dónde salen los valores
# iniciales: vacíos al capturar, los del movimiento al editar.
#
# No usa `st.form` a propósito: tipo, categoría y cuenta se
# encadenan, y eso exige que cada cambio vuelva a ejecutar el
# script.
#
# Qué campos se piden lo decide el tipo (`domain.captura`):
# un ingreso no es esencial ni deseo, y un traspaso no se
# «paga». Lo que la cuenta decide —con tarjeta el gasto lo paga
# la tarjeta— tampoco se pregunta: se explica.
# ═══════════════════════════════════════════════════════════

servicios = obtener_servicios()
catalogos = servicios.catalogos.opciones_captura()

st.title("Movimientos")
st.caption(
    "Captura el monto siempre en positivo: el tipo define si suma o resta. "
    "Con tarjeta de crédito el gasto queda pagado por la tarjeta; pagarla "
    "después es un **traspaso**, no otro gasto."
)

if catalogos["cuentas"].empty or catalogos["categorias"].empty:
    st.warning(
        "Primero da de alta al menos una categoría y una cuenta en **Catálogos**.",
        icon=":material/warning:",
    )
    st.stop()

#: {id: tipo} de las cuentas activas, para aplicar las reglas por cuenta.
TIPO_DE_CUENTA: dict[int, str] = {
    int(fila.id): str(fila.tipo) for fila in catalogos["cuentas"].itertuples()
}

SIN_SUBCATEGORIA = "— sin subcategoría —"
SIN_MEDIO = "— sin especificar —"
SIN_PROYECTO = "— ninguno —"
SIN_DESTINO = "— elige una cuenta —"

COLUMNAS_PRODUCTOS = ["producto", "cantidad", "precio_unitario", "nota"]
CONFIG_PRODUCTOS = {
    "producto": st.column_config.TextColumn("Producto", width="large", required=True),
    "cantidad": st.column_config.NumberColumn(
        "Cantidad", min_value=0.01, default=1.0, format="%.2f"
    ),
    "precio_unitario": st.column_config.NumberColumn(
        "Precio unitario", min_value=0.0, default=0.0, format="$%.2f"
    ),
    "nota": st.column_config.TextColumn("Nota"),
}


# ═══════════════════════════════════════════════════════════
# Ayudas
# ═══════════════════════════════════════════════════════════


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


def _cuentas_destino(tipo: str, origen_id: int) -> dict[str, int]:
    """
    Cuentas a las que puede llegar el dinero, según el tipo.

    Un ahorro o una inversión sólo pueden entrar a una cuenta de ahorro o
    inversión; un traspaso, a cualquiera que no sea el origen.
    """
    cuentas = catalogos["cuentas"]
    if tipo in (str(TipoMovimiento.AHORRO), str(TipoMovimiento.INVERSION)):
        cuentas = cuentas[cuentas["tipo"].map(lambda t: TipoCuenta(t).guarda_ahorro)]

    return {
        nombre: identificador
        for nombre, identificador in _opciones(cuentas).items()
        if identificador != origen_id
    }


def _mostrar_pista(pista: tuple[str, str] | None) -> None:
    """Dibuja el aviso que explica qué significa el movimiento entre cuentas."""
    if pista is None:
        return

    nivel, mensaje = pista
    if nivel == "error":
        st.warning(mensaje, icon=":material/help:")
    elif nivel == "aviso":
        st.warning(mensaje, icon=":material/savings:")
    else:
        st.info(mensaje, icon=":material/swap_horiz:")


def _indice(opciones: list[str], valor: str | None, por_defecto: int = 0) -> int:
    """Índice de `valor` en la lista, o el de respaldo si no está."""
    return opciones.index(valor) if valor in opciones else por_defecto


def _cuenta_habitual(opciones: dict[str, int]) -> int:
    """
    Índice de la cuenta con la que se arranca: la primera de débito.

    El orden alfabético pondría primero un apartado, y ahí casi nada sale.
    """
    for posicion, identificador in enumerate(opciones.values()):
        if TIPO_DE_CUENTA.get(identificador) == str(TipoCuenta.DEBITO):
            return posicion
    return 0


def _selector_texto_libre(
    etiqueta: str, actual: str, previos: list[str], key: str, ayuda: str = ""
) -> str:
    """
    Selector de texto libre con lo ya usado como opciones.

    El valor actual se conserva aunque no esté entre los previos, para que
    editar otro campo no borre éste.
    """
    opciones = ["", *previos]
    if actual and actual not in opciones:
        opciones.insert(1, actual)
    elegido = st.selectbox(
        etiqueta,
        opciones,
        index=_indice(opciones, actual),
        accept_new_options=True,
        key=key,
        help=ayuda or None,
    )
    return elegido or ""


def _texto(actual: pd.Series | None, campo: str) -> str:
    """Valor de texto del movimiento en edición, o vacío al capturar."""
    if actual is None:
        return ""
    valor = actual.get(campo)
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return ""
    return str(valor)


def _fecha(actual: pd.Series | None, campo: str) -> date | None:
    """Fecha del movimiento en edición, o None si no la tiene."""
    if actual is None:
        return None
    valor = actual.get(campo)
    if valor is None or pd.isna(valor):
        return None
    return pd.Timestamp(valor).date()


def _hora(actual: pd.Series | None) -> time | None:
    """Hora del movimiento en edición, o None."""
    texto = _texto(actual, "hora")
    return time.fromisoformat(texto) if texto else None


# ═══════════════════════════════════════════════════════════
# El formulario, único para capturar y editar
# ═══════════════════════════════════════════════════════════


def _formulario(prefijo: str, actual: pd.Series | None) -> dict[str, object]:
    """
    Dibuja las dos cajas del movimiento y devuelve lo capturado.

    `actual` es la fila que se edita, o None al capturar. Las claves de
    los widgets llevan `prefijo` para que editar otro movimiento arranque
    de sus propios valores y no arrastre los del anterior.

    Devuelve exactamente los argumentos de `MovimientosService.registrar`,
    que son los mismos de `actualizar`.
    """
    editando = actual is not None
    hoy = date.today()

    def k(nombre: str) -> str:
        return f"{prefijo}_{nombre}"

    # ── Caja 1: lo que define el movimiento ──────────────

    with st.container(border=True):
        fila_1 = st.columns([1, 1, 2, 1])

        tipos = [str(valor) for valor in TipoMovimiento]
        tipo_inicial = _texto(actual, "tipo") or str(TipoMovimiento.GASTO)
        with fila_1[2]:
            tipo = st.segmented_control(
                "Tipo",
                tipos,
                default=tipo_inicial,
                key=k("tipo"),
                help=(
                    "Cambiar el tipo corrige, por ejemplo, un pago de tarjeta "
                    "capturado como gasto."
                ),
            )
        tipo = tipo or tipo_inicial
        es_gasto = tipo == str(TipoMovimiento.GASTO)

        with fila_1[0]:
            fecha = st.date_input(
                "Fecha",
                value=_fecha(actual, "fecha") or hoy,
                format="DD/MM/YYYY",
                help="Cuándo ocurrió. En un gasto es la que manda en el presupuesto.",
                key=k("fecha"),
            )

        with fila_1[1]:
            hora = None
            if captura.admite(tipo, "hora"):
                hora = st.time_input(
                    "Hora",
                    value=_hora(actual),
                    step=300,
                    help="Opcional. Ningún estado de cuenta la trae.",
                    key=k("hora"),
                )
            else:
                st.markdown("&nbsp;")

        with fila_1[3]:
            monto = st.number_input(
                "Monto",
                min_value=0.0,
                value=float(actual["monto"]) if editando else 0.0,
                step=50.0,
                format="%.2f",
                key=k("monto"),
            )

        opciones_categoria = _opciones(_categorias_de(tipo))
        opciones_cuenta = _opciones(catalogos["cuentas"])
        nombres_cuenta = list(opciones_cuenta)

        # Un traspaso no se clasifica: su categoría es la única de su tipo
        # y se pone sola. Si el catálogo tuviera varias, se pregunta.
        pedir_categoria = (
            captura.admite(tipo, "categoria") or len(opciones_categoria) > 1
        )

        fila_2 = st.columns(3)
        subcategoria_id = None

        if pedir_categoria:
            with fila_2[0]:
                nombres = list(opciones_categoria)
                categoria = st.selectbox(
                    "Categoría",
                    nombres,
                    index=_indice(nombres, _texto(actual, "categoria")),
                    key=k(f"cat_{tipo}"),
                )
                categoria_id = opciones_categoria[categoria]

            with fila_2[1]:
                subcategorias = catalogos["subcategorias"]
                propias = subcategorias[subcategorias["categoria_id"] == categoria_id]
                opciones_sub = {SIN_SUBCATEGORIA: None} | _opciones(propias)
                nombres_sub = list(opciones_sub)
                subcategoria = st.selectbox(
                    "Subcategoría",
                    nombres_sub,
                    index=_indice(nombres_sub, _texto(actual, "subcategoria")),
                    key=k(f"sub_{categoria_id}"),
                )
                subcategoria_id = opciones_sub[subcategoria]
            columna_cuenta = fila_2[2]
        else:
            categoria_id = next(iter(opciones_categoria.values()))
            columna_cuenta = fila_2[0]

        with columna_cuenta:
            cuenta = st.selectbox(
                captura.etiqueta_cuenta(tipo),
                nombres_cuenta,
                index=_indice(
                    nombres_cuenta,
                    _texto(actual, "cuenta"),
                    _cuenta_habitual(opciones_cuenta),
                ),
                key=k("cuenta"),
            )
            cuenta_id = opciones_cuenta[cuenta]

        tipo_cuenta = TIPO_DE_CUENTA.get(cuenta_id)

        # ── Destino, en lo que mueve dinero entre cuentas ──
        cuenta_destino_id = None
        if captura.con_destino(tipo):
            destino = st.columns([1, 2])

            with destino[0]:
                opciones_destino = {SIN_DESTINO: None} | _cuentas_destino(
                    tipo, cuenta_id
                )
                nombres_destino = list(opciones_destino)
                cuenta_destino = st.selectbox(
                    "Cuenta de destino",
                    nombres_destino,
                    index=_indice(nombres_destino, _texto(actual, "cuenta_destino")),
                    help="A dónde llega el dinero que sale de la cuenta de origen.",
                    key=k(f"destino_{tipo}"),
                )
                cuenta_destino_id = opciones_destino[cuenta_destino]

            with destino[1]:
                st.markdown("&nbsp;")
                if len(opciones_destino) == 1:
                    st.warning(
                        "No hay ninguna cuenta de ahorro o inversión. Da de alta "
                        "un apartado en **Catálogos** (por ejemplo «BBVA Apartado»).",
                        icon=":material/savings:",
                    )
                else:
                    _mostrar_pista(
                        captura.describir_traspaso(
                            tipo,
                            tipo_cuenta,
                            TIPO_DE_CUENTA.get(cuenta_destino_id)
                            if cuenta_destino_id
                            else None,
                        )
                    )

        con_empresa = captura.admite(tipo, "empresa")
        texto = st.columns([3, 2, 2] if con_empresa else [1])

        with texto[0]:
            descripcion = st.text_input(
                "Descripción",
                value=_texto(actual, "descripcion"),
                placeholder="Supermercado quincenal",
                key=k("desc"),
            )

        empresa = ""
        lugar = ""
        if con_empresa:
            # Lo ya usado se ofrece primero, por frecuencia: el súper de
            # siempre no se teclea dos veces distinto.
            with texto[1]:
                empresa = _selector_texto_libre(
                    "Empresa",
                    _texto(actual, "empresa"),
                    servicios.movimientos.empresas(),
                    key=k("empresa"),
                    ayuda="Quién cobró: el comercio o la marca.",
                )
            with texto[2]:
                lugar = _selector_texto_libre(
                    "Lugar",
                    _texto(actual, "lugar"),
                    servicios.movimientos.lugares(),
                    key=k("lugar"),
                    ayuda="Dónde: la plaza, la colonia, la ciudad.",
                )

        if editando and _texto(actual, "descripcion_banco"):
            pie = f"Del banco: **{_texto(actual, 'descripcion_banco')}**"
            if _texto(actual, "referencia_externa"):
                pie += f" · folio `{_texto(actual, 'referencia_externa')}`"
            st.caption(pie)

        # ── Pago: sólo un gasto desde caja puede quedar a deber ──
        fecha_pago: date | None = fecha
        if captura.admite(tipo, "pago"):
            if captura.pago_lo_decide_la_cuenta(tipo, tipo_cuenta):
                fecha_pago = _fecha(actual, "fecha_pago") or fecha
                st.info(
                    f"Lo paga **{cuenta}**: queda como deuda de la tarjeta hasta "
                    "que la pagues con un traspaso. El gasto cuenta una sola vez.",
                    icon=":material/credit_card:",
                )
            else:
                pago = st.columns([1, 1, 2])
                pagado_actual = _fecha(actual, "fecha_pago")

                with pago[0]:
                    ya_pagado = st.checkbox(
                        "Ya se pagó",
                        value=(pagado_actual is not None) if editando else True,
                        key=k("pagado"),
                        help=(
                            "Desmárcalo para registrar un adeudo: el gasto "
                            "cuenta en el presupuesto de su fecha, pero no sale "
                            "de la cuenta hasta que lo pagues."
                        ),
                    )

                with pago[1]:
                    if ya_pagado:
                        fecha_pago = st.date_input(
                            "Fecha de pago",
                            value=pagado_actual or fecha,
                            format="DD/MM/YYYY",
                            key=k("fecha_pago"),
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
                    elif tipo_cuenta and TipoCuenta(tipo_cuenta).guarda_ahorro:
                        st.warning(
                            f"Sale directo de **{cuenta}**: cuenta como gasto y "
                            "como retiro de ahorro.",
                            icon=":material/savings:",
                        )

    # ── Caja 2: el detalle, aparte y a la vista ──────────

    medio_pago_id = None
    necesidad = str(Necesidad.ESENCIAL)
    naturaleza = str(Naturaleza.VARIABLE)
    recurrente = False
    planeado = True
    estado = str(EstadoMovimiento.CONFIRMADO)

    with st.container(border=True):
        st.markdown("**Detalle**")

        if es_gasto:
            fila_3 = st.columns(3)

            with fila_3[0]:
                opciones_medio = {SIN_MEDIO: None} | _opciones(catalogos["medios_pago"])
                nombres_medio = list(opciones_medio)
                medio = st.selectbox(
                    "Medio de pago",
                    nombres_medio,
                    index=_indice(nombres_medio, _texto(actual, "medio_pago")),
                    help="Si lo dejas vacío, se toma el que implica la cuenta.",
                    key=k("medio"),
                )
                medio_pago_id = opciones_medio[medio]

            with fila_3[1]:
                necesidad = st.segmented_control(
                    "Esencial o deseo",
                    [str(valor) for valor in Necesidad],
                    default=_texto(actual, "necesidad") or str(Necesidad.ESENCIAL),
                    key=k("necesidad"),
                ) or str(Necesidad.ESENCIAL)

            with fila_3[2]:
                naturaleza = st.segmented_control(
                    "Fijo o variable",
                    [str(valor) for valor in Naturaleza],
                    default=_texto(actual, "naturaleza") or str(Naturaleza.VARIABLE),
                    key=k("naturaleza"),
                ) or str(Naturaleza.VARIABLE)

        fila_4 = st.columns(3)

        with fila_4[0]:
            # Un proyecto escrito a mano cada vez se convierte en varios
            # proyectos por culpa de un acento o una mayúscula, así que
            # los ya usados se ofrecen para reutilizar.
            previos = servicios.movimientos.nombres_de_proyecto()
            opciones_proyecto = [SIN_PROYECTO, *previos]
            proyecto_actual = _texto(actual, "proyecto") or SIN_PROYECTO
            if proyecto_actual not in opciones_proyecto:
                opciones_proyecto.insert(1, proyecto_actual)
            proyecto = st.selectbox(
                "Proyecto o persona",
                opciones_proyecto,
                index=_indice(opciones_proyecto, proyecto_actual),
                accept_new_options=True,
                help=(
                    "Agrupa movimientos de distintas categorías bajo un "
                    "mismo esfuerzo: un viaje, una mudanza, una obra. "
                    "Escribe uno nuevo para crearlo."
                ),
                key=k("proyecto"),
            )
            if proyecto == SIN_PROYECTO:
                proyecto = ""

        with fila_4[1]:
            etiquetas = st.text_input(
                "Etiquetas",
                value=_texto(actual, "etiquetas"),
                placeholder="despensa, quincena",
                key=k("etiquetas"),
            )

        with fila_4[2]:
            if captura.admite(tipo, "estado"):
                estado_inicial = _texto(actual, "estado") or str(
                    EstadoMovimiento.CONFIRMADO
                )
                estado = st.segmented_control(
                    "Estado",
                    [str(valor) for valor in EstadoMovimiento],
                    default=estado_inicial,
                    help="Pendiente es una proyección: no mueve ninguna cuenta.",
                    key=k("estado"),
                ) or str(EstadoMovimiento.CONFIRMADO)
            else:
                st.markdown("&nbsp;")

        banderas = st.columns(3)
        with banderas[0]:
            if es_gasto:
                recurrente = st.checkbox(
                    "Es un gasto recurrente",
                    value=bool(actual["recurrente"]) if editando else False,
                    key=k("recurrente"),
                )
        with banderas[1]:
            if es_gasto:
                planeado = st.checkbox(
                    "Estaba planeado",
                    value=bool(actual["planeado"]) if editando else True,
                    key=k("planeado"),
                )
        with banderas[2]:
            # El banco suele aplicar al día siguiente. Con esto, al
            # importar el estado de cuenta se reconoce a la primera.
            fecha_banco = st.date_input(
                "Fecha en el banco",
                value=_fecha(actual, "fecha_banco") or fecha + timedelta(days=1),
                format="DD/MM/YYYY",
                help=(
                    "Cuándo lo reporta el banco. Por defecto, un día después: "
                    "es con lo que se reconoce al importar el estado de cuenta."
                ),
                key=k("fbanco"),
            )

        nota = st.text_area(
            "Nota o comprobante",
            value=_texto(actual, "nota"),
            height=80,
            key=k("nota"),
        )

    return {
        "fecha": fecha,
        "tipo": tipo,
        "monto": monto,
        "categoria_id": categoria_id,
        "cuenta_id": cuenta_id,
        "subcategoria_id": subcategoria_id,
        "cuenta_destino_id": cuenta_destino_id,
        "medio_pago_id": medio_pago_id,
        "descripcion": descripcion,
        "necesidad": necesidad,
        "naturaleza": naturaleza,
        "recurrente": recurrente,
        "planeado": planeado,
        "proyecto": proyecto,
        "etiquetas": etiquetas,
        "nota": nota,
        "estado": estado,
        "fecha_pago": fecha_pago,
        "empresa": empresa,
        "lugar": lugar,
        "hora": hora,
        "fecha_banco": fecha_banco,
    }


def _editor_productos(base: pd.DataFrame, monto: float, key: str) -> pd.DataFrame:
    """
    Dibuja la lista de artículos de la compra y dice cuánto cubre.

    No parte el movimiento: sigue siendo un gasto con su categoría, y
    esto guarda qué venía dentro. Puede quedarse a medias.
    """
    editados = st.data_editor(
        base,
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        key=key,
        column_config=CONFIG_PRODUCTOS,
    )

    suma = float(
        (editados["cantidad"].fillna(1) * editados["precio_unitario"].fillna(0)).sum()
    )
    resto = round(float(monto) - suma, 2)

    if resto < -0.01:
        st.warning(
            f"Los productos suman {moneda(abs(resto))} más que el monto del "
            "movimiento.",
            icon=":material/balance:",
        )
    elif suma and resto > 0.01:
        st.caption(
            f"Detallado {moneda(suma)} de {moneda(monto)} · quedan "
            f"{moneda(resto)} sin apuntar"
        )
    elif suma:
        st.caption("El detalle cubre el movimiento completo.")

    return editados


registrar, explorar = st.tabs(["Registrar", "Explorar y editar"])


# ═══════════════════════════════════════════════════════════
# Registrar
# ═══════════════════════════════════════════════════════════

with registrar:
    valores = _formulario("mov", None)

    # El detalle de una compra de varias cosas se captura aquí, en
    # memoria, y se escribe después de crear el movimiento, que es cuando
    # existe el id del que cuelgan.
    productos_nuevos = None
    if captura.admite(str(valores["tipo"]), "productos"):
        detallar = st.toggle(
            "Apuntar los productos",
            key="mov_detallar",
            help=(
                "Para una compra de varias cosas. El gasto sigue siendo uno "
                "con su categoría; esto guarda qué venía dentro, y no hace "
                "falta listarlo todo."
            ),
        )
        if detallar:
            with st.container(border=True):
                productos_nuevos = _editor_productos(
                    pd.DataFrame(
                        {
                            "producto": pd.Series(dtype="str"),
                            "cantidad": pd.Series(dtype="float"),
                            "precio_unitario": pd.Series(dtype="float"),
                            "nota": pd.Series(dtype="str"),
                        }
                    ),
                    float(valores["monto"]),
                    key="mov_productos",
                )

    if st.button("Registrar movimiento", type="primary", icon=":material/add:"):
        try:
            nuevo_id = servicios.movimientos.registrar(**valores)
        except ValueError as error:
            reportar_error(error)
        else:
            cuantos = 0
            if productos_nuevos is not None:
                cuantos = servicios.productos.reemplazar(nuevo_id, productos_nuevos)

            invalidar_datos()
            sufijo = "" if valores["fecha_pago"] else " · pendiente de pago"
            if cuantos:
                sufijo += f" · {cuantos} productos"
            st.success(
                f"Movimiento {nuevo_id} registrado por "
                f"{moneda(float(valores['monto']))}{sufijo}.",
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
                "Buscar en descripción, empresa, lugar, etiquetas y notas",
                placeholder="café",
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
    ahorro_neto = float(
        movimientos["patrimonio_creado"].sum() - movimientos["ahorro_retirado"].sum()
    )
    totales[3].metric(
        "Ahorro neto",
        moneda(ahorro_neto),
        delta=f"retiros {moneda(movimientos['ahorro_retirado'].sum())}"
        if movimientos["ahorro_retirado"].sum()
        else None,
        delta_color="off",
        border=True,
        help="Lo que entró a cuentas de ahorro o inversión menos lo que salió.",
    )
    adeudo = float(movimientos["por_pagar"].sum())
    totales[4].metric(
        "Por pagar",
        moneda(adeudo),
        delta=f"{int((movimientos['por_pagar'] > 0).sum())} movimientos",
        delta_color="off",
        border=True,
        help="Gasto ya incurrido que todavía no sale de ninguna cuenta.",
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
                "empresa",
                "lugar",
                "cuenta",
                "cuenta_destino",
                "medio_pago",
                "monto",
                "necesidad",
                "naturaleza",
                "recurrente",
                "planeado",
                "estado",
                "fecha_pago",
                "proyecto",
                "etiquetas",
                "fecha_banco",
                "descripcion_banco",
                "nota",
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
            "empresa": st.column_config.TextColumn("Empresa"),
            "lugar": st.column_config.TextColumn("Lugar"),
            "cuenta": st.column_config.TextColumn("Cuenta"),
            "cuenta_destino": st.column_config.TextColumn(
                "Destino", help="En traspasos y ahorro: a dónde llegó el dinero."
            ),
            "medio_pago": st.column_config.TextColumn("Medio de pago"),
            "monto": st.column_config.NumberColumn("Monto", format="$%.2f"),
            "necesidad": st.column_config.TextColumn("Necesidad"),
            "naturaleza": st.column_config.TextColumn("Naturaleza"),
            "recurrente": st.column_config.CheckboxColumn("Recurrente"),
            "planeado": st.column_config.CheckboxColumn("Planeado"),
            "estado": st.column_config.TextColumn("Estado"),
            "fecha_pago": st.column_config.DateColumn(
                "Pagado el",
                format="DD/MM/YYYY",
                help="Vacío significa que el gasto sigue pendiente de pago.",
            ),
            "proyecto": st.column_config.TextColumn("Proyecto"),
            "etiquetas": st.column_config.TextColumn("Etiquetas"),
            "fecha_banco": st.column_config.DateColumn(
                "En el banco", format="DD/MM/YYYY"
            ),
            "descripcion_banco": st.column_config.TextColumn(
                "Concepto del banco",
                width="medium",
                help=(
                    "Texto original del estado de cuenta. No se edita: "
                    "sirve para casar el movimiento con su línea del "
                    "documento."
                ),
            ),
            "nota": st.column_config.TextColumn("Nota", width="medium"),
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

    # ── Editor: el mismo formulario, con los valores del movimiento ──

    actual = elegidos.iloc[0]
    movimiento_id = int(actual["id"])

    st.markdown(f"**Editar movimiento {movimiento_id}**")
    valores = _formulario(f"edit_{movimiento_id}", actual)

    with st.container(horizontal=True):
        if st.button("Guardar cambios", type="primary", icon=":material/save:"):
            try:
                servicios.movimientos.actualizar(movimiento_id, **valores)
            except ValueError as error:
                reportar_error(error)
            else:
                invalidar_datos()
                st.success("Movimiento actualizado.", icon=":material/check:")
                st.rerun()

        if st.button("Duplicar hoy", icon=":material/content_copy:"):
            try:
                servicios.movimientos.duplicar(movimiento_id, date.today())
            except ValueError as error:
                reportar_error(error)
            else:
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
    if captura.admite(str(valores["tipo"]), "productos"):
        with st.container(border=True):
            st.markdown("**Productos de esta compra**")
            st.caption(
                "Apunta qué venía dentro. No hace falta listarlo todo: el "
                "detalle puede quedarse a medias."
            )
            productos = servicios.productos.de_movimiento(movimiento_id)
            base = (
                productos[COLUMNAS_PRODUCTOS]
                if not productos.empty
                else pd.DataFrame(columns=COLUMNAS_PRODUCTOS)
            )
            editados = _editor_productos(
                base, float(actual["monto"]), key=f"prods_{movimiento_id}"
            )

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
