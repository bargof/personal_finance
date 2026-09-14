from __future__ import annotations

import streamlit as st

from finanzas.application.app.components import (
    invalidar_datos,
    moneda,
    obtener_servicios,
    reportar_error,
)
from finanzas.application.app.theme import rotulo
from finanzas.application.services.importacion_service import (
    ImportacionService,
    Producto,
    tabla_de,
)
from finanzas.data.lectores import REGISTRO, DocumentoNoReconocidoError
from finanzas.data.lectores.extraccion import PdfProtegidoError
from finanzas.domain.enums import (
    EstadoMovimiento,
    Naturaleza,
    Necesidad,
    TipoMovimiento,
)

# ═══════════════════════════════════════════════════════════
# Importar: del estado de cuenta a movimientos
#
# Tres pasos deliberados. Primero leer y enseñar lo leído, que
# es cuando se detecta si el documento se entendió bien.
# Después completar lo que el banco no sabe —categoría, si fue
# esencial, para qué proyecto— uno por uno, porque hacerlo en
# una rejilla de treinta filas es donde la atención se pierde.
# Al final, guardar.
#
# Nada se escribe hasta el último paso.
# ═══════════════════════════════════════════════════════════

servicios = obtener_servicios()
importacion = ImportacionService()
catalogos = servicios.catalogos.opciones_captura()

st.title("Importar estado de cuenta")
st.caption(
    "Sube el archivo del banco, revisa lo que se leyó y completa los datos "
    "que el estado de cuenta no trae."
)


#: Cómo se traducen las categorías que trae el banco a las del catálogo.
#: Nu clasifica sus movimientos y ese trabajo no hay que repetirlo; el
#: resto de los formatos no lo trae, y ahí el selector arranca en blanco.
_EQUIVALENCIAS = {
    "transporte": "Transporte",
    "restaurante": "Restaurantes",
    "supermercado": "Supermercado",
    "electrónicos": "Tecnología",
    "ropa": "Compras",
    "educación": "Educación",
    "servicio": "Servicios",
    "salud": "Salud",
    "entretenimiento": "Entretenimiento",
    "viajes": "Viajes",
    "hogar": "Vivienda",
}


def _sugerir_categoria(categoria_banco: str, disponibles: dict[str, int]) -> str:
    """
    Traduce la categoría del banco a una del catálogo propio.

    Devuelve cadena vacía si no hay equivalencia, para que el selector no
    proponga algo arbitrario: una sugerencia mala cuesta más de corregir
    que una casilla en blanco de llenar.
    """
    equivalente = _EQUIVALENCIAS.get(categoria_banco.strip().lower(), "")

    return equivalente if equivalente in disponibles else ""


SIN_PROYECTO = "— ninguno —"
SIN_SUBCATEGORIA = "— sin subcategoría —"


def _nombre_de(identificador: int | None, opciones: dict[str, int]) -> str:
    """Traduce un id de catálogo a su nombre, o cadena vacía si no está."""
    if identificador is None:
        return ""

    for nombre, valor in opciones.items():
        if valor == identificador:
            return nombre

    return ""


def _selector_subcategoria(
    categoria_id: int | None, actual: int | None, clave: str
) -> int | None:
    """
    Dibuja el selector de subcategoría de una categoría dada.

    Se recalcula cada vez porque las opciones dependen de la categoría
    elegida; el valor previo se conserva sólo si sigue siendo válido.
    """
    subcategorias = catalogos["subcategorias"]
    propias = subcategorias[subcategorias["categoria_id"] == categoria_id]
    opciones = {SIN_SUBCATEGORIA: None} | {
        fila.nombre: int(fila.id) for fila in propias.itertuples()
    }
    nombres = list(opciones)
    previo = _nombre_de(actual, {k: v for k, v in opciones.items() if v is not None})

    elegida = st.selectbox(
        "Subcategoría",
        nombres,
        index=nombres.index(previo) if previo in nombres else 0,
        key=clave,
        persist_state="session",
    )

    return opciones[elegida]


def _editar_productos(candidato, indice: int) -> None:
    """
    Dibuja la lista de artículos de la compra.

    Los productos no parten el movimiento: el gasto sigue siendo uno con
    su categoría, y esto es su detalle. Por eso no llevan categoría propia
    y el desglose puede quedarse a medias sin que sea un error.
    """
    st.caption(
        f"Apunta qué venía en los {moneda(candidato.origen.monto, decimales=2)}. "
        "No hace falta listarlo todo."
    )

    for numero, producto in enumerate(candidato.productos):
        columnas = st.columns([3, 1, 1, 1, 1])

        with columnas[0]:
            producto.producto = st.text_input(
                "Producto",
                value=producto.producto,
                key=f"prod_nom_{indice}_{numero}",
                persist_state="session",
                placeholder="Monitor 24 pulgadas",
                label_visibility="collapsed" if numero else "visible",
            )

        with columnas[1]:
            producto.cantidad = st.number_input(
                "Cantidad",
                min_value=0.01,
                value=float(producto.cantidad),
                step=1.0,
                key=f"prod_cant_{indice}_{numero}",
                persist_state="session",
                label_visibility="collapsed" if numero else "visible",
            )

        with columnas[2]:
            producto.precio_unitario = st.number_input(
                "Precio",
                min_value=0.0,
                value=float(producto.precio_unitario),
                step=10.0,
                format="%.2f",
                key=f"prod_precio_{indice}_{numero}",
                persist_state="session",
                label_visibility="collapsed" if numero else "visible",
            )

        with columnas[3]:
            if not numero:
                st.markdown("&nbsp;")
            st.markdown(f"**{moneda(producto.importe, decimales=2)}**")

        with columnas[4]:
            if not numero:
                st.markdown("&nbsp;")
            if st.button(
                "", icon=":material/delete:", key=f"prod_del_{indice}_{numero}"
            ):
                candidato.productos.pop(numero)
                st.rerun()

    acciones = st.columns([1, 3])

    with acciones[0]:
        if st.button(
            "Agregar producto", icon=":material/add:", key=f"prod_mas_{indice}"
        ):
            # El precio nuevo arranca con lo que falta: si es el único
            # artículo, queda cuadrado de entrada.
            candidato.productos.append(
                Producto(precio_unitario=max(candidato.sin_desglosar, 0.0))
            )
            st.rerun()

    with acciones[1]:
        resto = candidato.sin_desglosar
        st.markdown("&nbsp;")
        if candidato.desglose_excedido:
            st.caption(
                f"Los productos suman {moneda(abs(resto), decimales=2)} más de "
                "lo que se cobró."
            )
        elif resto > 0.01:
            st.caption(
                f"Detallado {moneda(candidato.desglosado, decimales=2)} · "
                f"quedan {moneda(resto, decimales=2)} sin apuntar"
            )
        else:
            st.caption("El detalle cubre el movimiento completo.")


def _recordar(paso: str | None = None) -> None:
    """
    Deja el avance en la base.

    Se llama en cada paso del asistente: `session_state` sobrevive los
    reruns pero no una recarga del navegador, y lo capturado no puede
    depender de que nadie toque F5.
    """
    resultado = st.session_state.get("importacion")
    if resultado is None:
        return

    importacion.guardar_avance(
        resultado,
        st.session_state.get("indice", 0),
        paso or st.session_state.get("paso", "revisar"),
    )


def _guardar_actual(candidato) -> None:
    """
    Escribe el candidato antes de moverse de sitio.

    El asistente guarda al avanzar en vez de al final: así lo capturado no
    depende de llegar hasta el último movimiento ni de acordarse de pulsar
    nada. Si el candidato ya se había guardado, se reemplaza, de modo que
    volver atrás a corregir no deja copias.
    """
    if not candidato.completo:
        return

    cuenta_id = candidato.cuenta_id
    if cuenta_id is None:
        return

    try:
        importacion.guardar_uno(candidato, cuenta_id, pagado=candidato.pagado)
    except ValueError as error:
        reportar_error(error)
    else:
        invalidar_datos()
        _recordar()


def _reiniciar() -> None:
    """Deja la página lista para un documento nuevo."""
    for clave in ("importacion", "paso", "indice", "terminado"):
        st.session_state.pop(clave, None)
    importacion.olvidar_avance()


# ═══════════════════════════════════════════════════════════
# Paso 1: leer
# ═══════════════════════════════════════════════════════════

# Una recarga vacía `session_state`, pero el avance sigue en la base.
if "importacion" not in st.session_state:
    guardado = importacion.recuperar_avance()
    if guardado is not None:
        anterior, indice_guardado, paso_guardado = guardado
        st.session_state["importacion"] = anterior
        st.session_state["indice"] = indice_guardado
        st.session_state["paso"] = paso_guardado
        st.rerun()

if "importacion" not in st.session_state:
    with st.container(border=True):
        st.subheader("1 · Elige el archivo")

        formatos = " · ".join(lector.nombre for lector in REGISTRO)
        st.caption(f"Formatos que reconozco: {formatos}")

        archivo = st.file_uploader(
            "Estado de cuenta",
            type=["pdf", "csv", "txt"],
            help="El archivo no sale de tu computadora.",
        )

        contrasena = st.text_input(
            "Contraseña del PDF",
            type="password",
            help="Déjala vacía si el archivo abre sin contraseña.",
        )

        if archivo is not None and st.button(
            "Leer documento", type="primary", icon=":material/upload_file:"
        ):
            try:
                resultado = importacion.leer_documento(
                    archivo.getvalue(), contrasena or None
                )
            except PdfProtegidoError as error:
                st.warning(str(error), icon=":material/lock:")
            except DocumentoNoReconocidoError as error:
                st.error(str(error), icon=":material/help:")
                with st.expander(
                    "Ver el texto que extraje", icon=":material/bug_report:"
                ):
                    st.caption(
                        "Sirve para saber si el PDF trae texto o es una imagen "
                        "escaneada, que necesitaría otro tratamiento."
                    )
                    from finanzas.data.lectores.extraccion import extraer_lineas

                    try:
                        crudo = extraer_lineas(archivo.getvalue(), contrasena or None)
                        st.code("\n".join(crudo[:120]) or "(sin texto)")
                    except Exception as fallo:  # noqa: BLE001 - es diagnóstico
                        st.code(str(fallo))
            except ValueError as error:
                reportar_error(error)
            else:
                if not resultado.candidatos:
                    st.warning(
                        "El documento se reconoció pero no encontré movimientos.",
                        icon=":material/search_off:",
                    )
                else:
                    st.session_state["importacion"] = resultado
                    st.session_state["paso"] = "revisar"
                    st.session_state["indice"] = 0
                    importacion.guardar_avance(resultado, 0, "revisar")
                    st.rerun()

    st.stop()


resultado = st.session_state["importacion"]
lectura = resultado.lectura


# ═══════════════════════════════════════════════════════════
# Paso 2: revisar lo leído
# ═══════════════════════════════════════════════════════════

if st.session_state.get("paso") == "revisar":
    with st.container(horizontal=True):
        st.metric("Banco", lectura.banco, border=True)
        st.metric("Movimientos", len(resultado.candidatos), border=True)
        st.metric("Cargos", moneda(lectura.cargos), border=True)
        st.metric("Abonos", moneda(lectura.abonos), border=True)

    # El cuadre es la red de seguridad: si lo leído no reconstruye lo que
    # el documento declara, faltan filas y más vale decirlo antes de nada.
    if lectura.cuadra is True:
        st.success(
            "Lo leído cuadra con los totales del documento.",
            icon=":material/verified:",
        )
    elif lectura.cuadra is False:
        st.error(
            f"Lo leído no cuadra con el documento: hay una diferencia de "
            f"{moneda(abs(lectura.diferencia_de_cuadre), decimales=2)}. "
            "Puede que falten filas o que alguna se haya leído de más. "
            "Revisa la tabla antes de continuar.",
            icon=":material/warning:",
        )
    else:
        st.info(
            "El documento no declara totales con los que contrastar, así que "
            "revisa la tabla con calma.",
            icon=":material/info:",
        )

    for aviso in lectura.avisos:
        st.warning(aviso, icon=":material/info:")

    rotulo("Lo que encontré")

    nuevos, repetidos = len(resultado.nuevos), len(resultado.duplicados)
    if repetidos:
        st.caption(
            f"{nuevos} movimientos nuevos y {repetidos} que parecen ya "
            "registrados. Los repetidos vienen desmarcados; puedes incluirlos "
            "si el parecido es casualidad."
        )

    tabla = tabla_de(resultado)
    editada = st.data_editor(
        tabla,
        hide_index=True,
        width="stretch",
        disabled=[
            "fecha",
            "descripcion_banco",
            "monto",
            "tipo",
            "categoria_banco",
            "estado",
            "motivo",
        ],
        column_config={
            "incluir": st.column_config.CheckboxColumn("Importar", width="small"),
            "fecha": st.column_config.DateColumn("Fecha", format="DD/MM/YYYY"),
            "descripcion_banco": st.column_config.TextColumn(
                "Concepto del banco", width="large"
            ),
            "monto": st.column_config.NumberColumn("Monto", format="$%.2f"),
            "tipo": st.column_config.TextColumn("Tipo"),
            "categoria_banco": st.column_config.TextColumn("Categoría del banco"),
            "estado": st.column_config.TextColumn("Estado"),
            "motivo": st.column_config.TextColumn("Por qué", width="medium"),
        },
        key="tabla_importacion",
    )

    for candidato, incluir in zip(resultado.candidatos, editada["incluir"]):
        candidato.incluir = bool(incluir)

    seleccionados = resultado.a_importar
    st.caption(
        f"**{len(seleccionados)} seleccionados** por "
        f"{moneda(sum(c.origen.monto for c in seleccionados))}."
    )

    acciones = st.columns([1, 1, 3])

    with acciones[0]:
        if st.button(
            "Completar información",
            type="primary",
            icon=":material/edit_note:",
            disabled=not seleccionados,
        ):
            st.session_state["paso"] = "completar"
            st.session_state.setdefault("indice", 0)
            _recordar("completar")
            st.rerun()

    with acciones[1]:
        if st.button("Empezar de nuevo", icon=":material/restart_alt:"):
            _reiniciar()
            st.rerun()

    st.stop()


# ═══════════════════════════════════════════════════════════
# Paso 3: completar, uno por uno
#
# Cada widget arranca desde el valor guardado en el candidato y
# `persist_state="session"` evita que Streamlit lo descarte al
# cambiar de página: sin las dos cosas, salir de la vista y
# volver devolvía todo a sus valores iniciales.
# ═══════════════════════════════════════════════════════════

if st.session_state.get("paso") == "completar":
    pendientes = resultado.a_importar
    indice = min(st.session_state.get("indice", 0), max(len(pendientes) - 1, 0))
    total = len(pendientes)

    if not pendientes:
        st.warning("No hay movimientos seleccionados.", icon=":material/info:")
        if st.button("Volver a la tabla", icon=":material/arrow_back:"):
            st.session_state["paso"] = "revisar"
            st.rerun()
        st.stop()

    candidato = pendientes[indice]
    origen = candidato.origen
    listos = sum(1 for c in pendientes if c.completo)

    st.progress(
        listos / total,
        text=f"Movimiento {indice + 1} de {total} · {listos} completos",
    )

    with st.container(border=True):
        cabecera = st.columns([3, 1, 1])
        with cabecera[0]:
            st.markdown(f"### {origen.descripcion_banco}")
            detalle = f"{origen.fecha:%d/%m/%Y} · {candidato.tipo}"
            if origen.categoria_banco:
                detalle += f" · el banco lo clasificó como {origen.categoria_banco}"
            st.caption(detalle)
        with cabecera[1]:
            st.metric("Monto", moneda(origen.monto, decimales=2), border=True)
        with cabecera[2]:
            if origen.referencia:
                st.caption(f"Folio\n\n`{origen.referencia}`")

        st.divider()

        opciones_categoria = {
            fila.nombre: int(fila.id) for fila in catalogos["categorias"].itertuples()
        }
        nombres = list(opciones_categoria)
        opciones_cuenta = {
            fila.nombre: int(fila.id) for fila in catalogos["cuentas"].itertuples()
        }
        nombres_cuenta = list(opciones_cuenta)

        fila_1 = st.columns(2)

        with fila_1[0]:
            sugerida = _sugerir_categoria(origen.categoria_banco, opciones_categoria)
            actual = _nombre_de(candidato.categoria_id, opciones_categoria)
            inicial = actual or sugerida
            categoria = st.selectbox(
                "Categoría",
                nombres,
                index=nombres.index(inicial) if inicial in nombres else 0,
                key=f"cat_{indice}",
                persist_state="session",
            )
            candidato.categoria_id = opciones_categoria[categoria]

        with fila_1[1]:
            candidato.subcategoria_id = _selector_subcategoria(
                candidato.categoria_id,
                candidato.subcategoria_id,
                clave=f"sub_{indice}",
            )

        cuenta_previa = _nombre_de(candidato.cuenta_id, opciones_cuenta)
        inicial_cuenta = cuenta_previa or st.session_state.get("cuenta_documento", "")
        cuenta = st.selectbox(
            "Cuenta",
            nombres_cuenta,
            index=nombres_cuenta.index(inicial_cuenta)
            if inicial_cuenta in nombres_cuenta
            else 0,
            key=f"cta_{indice}",
            persist_state="session",
        )
        candidato.cuenta_id = opciones_cuenta[cuenta]
        st.session_state["cuenta_documento"] = cuenta

        candidato.descripcion = st.text_input(
            "Descripción",
            value=candidato.descripcion or origen.descripcion_banco,
            help=(
                "Cómo lo describirías tú. Puedes cambiarla sin perder nada: "
                "el concepto del banco se guarda en su propia columna."
            ),
            key=f"desc_{indice}",
            persist_state="session",
        )
        st.caption(f"Se conserva del banco: **{origen.descripcion_banco}**")

        fila_2 = st.columns(4)

        with fila_2[0]:
            candidato.necesidad = st.segmented_control(
                "Esencial o deseo",
                [str(valor) for valor in Necesidad],
                default=candidato.necesidad,
                key=f"nec_{indice}",
                persist_state="session",
            ) or str(Necesidad.ESENCIAL)

        with fila_2[1]:
            candidato.naturaleza = st.segmented_control(
                "Fijo o variable",
                [str(valor) for valor in Naturaleza],
                default=candidato.naturaleza,
                key=f"nat_{indice}",
                persist_state="session",
            ) or str(Naturaleza.VARIABLE)

        with fila_2[2]:
            previos = servicios.proyectos.nombres()
            opciones_proyecto = [SIN_PROYECTO, *previos]
            elegido = candidato.proyecto or SIN_PROYECTO
            proyecto = st.selectbox(
                "Proyecto",
                opciones_proyecto,
                index=opciones_proyecto.index(elegido)
                if elegido in opciones_proyecto
                else 0,
                accept_new_options=True,
                key=f"proy_{indice}",
                persist_state="session",
            )
            candidato.proyecto = "" if proyecto == SIN_PROYECTO else proyecto

        with fila_2[3]:
            candidato.pagado = st.checkbox(
                "Ya se pagó",
                value=candidato.pagado,
                help=(
                    "En una tarjeta de crédito déjalo sin marcar: el gasto ya "
                    "ocurrió pero el dinero sale hasta que pagues el corte."
                ),
                key=f"pag_{indice}",
                persist_state="session",
            )

        # Lo que el documento no trae y el movimiento sí admite: sin esto,
        # importar dejaría movimientos a medio llenar frente a los que se
        # capturan a mano.
        with st.expander("Más detalle", icon=":material/more_horiz:"):
            fila_3 = st.columns(3)

            with fila_3[0]:
                opciones_medio = {"— sin especificar —": None} | {
                    fila.nombre: int(fila.id)
                    for fila in catalogos["medios_pago"].itertuples()
                }
                nombres_medio = list(opciones_medio)
                previo_medio = _nombre_de(
                    candidato.medio_pago_id,
                    {k: v for k, v in opciones_medio.items() if v is not None},
                )
                medio = st.selectbox(
                    "Medio de pago",
                    nombres_medio,
                    index=nombres_medio.index(previo_medio)
                    if previo_medio in nombres_medio
                    else 0,
                    key=f"medio_{indice}",
                    persist_state="session",
                )
                candidato.medio_pago_id = opciones_medio[medio]

            with fila_3[1]:
                estados = [str(valor) for valor in EstadoMovimiento]
                candidato.estado_movimiento = st.selectbox(
                    "Estado",
                    estados,
                    index=estados.index(candidato.estado_movimiento)
                    if candidato.estado_movimiento in estados
                    else 0,
                    key=f"estado_{indice}",
                    persist_state="session",
                    help="Pendiente es una proyección; confirmado ya ocurrió.",
                )

            with fila_3[2]:
                # Sólo un traspaso tiene dos patas.
                if candidato.tipo == str(TipoMovimiento.TRANSFERENCIA):
                    otras = {
                        nombre: valor
                        for nombre, valor in opciones_cuenta.items()
                        if valor != candidato.cuenta_id
                    }
                    opciones_destino = {"— sin especificar —": None} | otras
                    nombres_destino = list(opciones_destino)
                    previo_destino = _nombre_de(candidato.cuenta_destino_id, otras)
                    destino = st.selectbox(
                        "Cuenta de destino",
                        nombres_destino,
                        index=nombres_destino.index(previo_destino)
                        if previo_destino in nombres_destino
                        else 0,
                        key=f"dest_{indice}",
                        persist_state="session",
                        help="A dónde llegó el dinero del traspaso.",
                    )
                    candidato.cuenta_destino_id = opciones_destino[destino]
                else:
                    candidato.cuenta_destino_id = None

            candidato.etiquetas = st.text_input(
                "Etiquetas",
                value=candidato.etiquetas,
                placeholder="despensa, quincena",
                key=f"etq_{indice}",
                persist_state="session",
            )

            banderas = st.columns(2)
            with banderas[0]:
                candidato.recurrente = st.checkbox(
                    "Es un gasto recurrente",
                    value=candidato.recurrente,
                    key=f"rec_{indice}",
                    persist_state="session",
                )
            with banderas[1]:
                candidato.planeado = st.checkbox(
                    "Estaba planeado",
                    value=candidato.planeado,
                    key=f"plan_{indice}",
                    persist_state="session",
                )

        candidato.nota = st.text_input(
            "Nota",
            value=candidato.nota,
            key=f"nota_{indice}",
            persist_state="session",
        )

        # ── Qué venía en la compra ───────────────────────

        detallar = st.toggle(
            "Apuntar los productos",
            value=bool(candidato.productos),
            key=f"detalle_{indice}",
            persist_state="session",
            help=(
                "Para una compra de varias cosas: el gasto sigue siendo uno "
                "con su categoría, y esto guarda qué había dentro."
            ),
        )

        if detallar and not candidato.productos:
            candidato.productos = [Producto(precio_unitario=origen.monto)]
        elif not detallar and candidato.productos:
            candidato.productos = []

        if detallar:
            _editar_productos(candidato, indice)

    candidato.revisado = True

    if candidato.desglose_excedido:
        st.warning(
            f"Los productos suman "
            f"{moneda(abs(candidato.sin_desglosar), decimales=2)} más de lo "
            "que se cobró. Ajústalos antes de continuar.",
            icon=":material/balance:",
        )

    navegacion = st.columns([1, 1, 1, 1, 2])

    with navegacion[0]:
        if st.button("Anterior", icon=":material/arrow_back:", disabled=indice == 0):
            st.session_state["indice"] = indice - 1
            st.rerun()

    with navegacion[1]:
        ultimo = indice + 1 >= total
        if st.button(
            "Revisar y guardar" if ultimo else "Siguiente",
            type="primary",
            icon=":material/arrow_forward:",
        ):
            st.session_state["indice"] = indice + 1
            st.rerun()

    with navegacion[2]:
        # Guardar sin llegar al final: lo completo entra, lo demás sigue
        # esperando en la lista.
        if st.button(
            "Guardar lo listo",
            icon=":material/save:",
            disabled=listos == 0,
            help=f"Guarda ahora los {listos} movimientos ya completos.",
        ):
            st.session_state["paso"] = "guardar"
            st.rerun()

    with navegacion[3]:
        if st.button("Saltar éste", icon=":material/skip_next:"):
            candidato.incluir = False
            st.session_state["indice"] = indice
            st.rerun()

    with navegacion[4]:
        if st.button("Salir del detalle", icon=":material/close:"):
            st.session_state["paso"] = "revisar"
            st.rerun()

    st.caption(
        "Lo que llevas capturado se conserva aunque cambies de página y vuelvas."
    )

    st.stop()


# ═══════════════════════════════════════════════════════════
# Paso 4: cerrar
#
# Ya no hay nada que guardar: el asistente escribe conforme se
# avanza. Este paso sólo cierra la importación y dice qué pasó.
# ═══════════════════════════════════════════════════════════

if st.session_state.get("paso") == "guardar" and not st.session_state.get("terminado"):
    escritos = sum(len(c.guardados) for c in resultado.candidatos)
    incompletos = [c for c in resultado.a_importar if not c.completo]

    st.success(
        f"Llevas {escritos} movimientos guardados de {lectura.banco}.",
        icon=":material/task_alt:",
    )

    rotulo("Resumen")

    with st.container(horizontal=True):
        st.metric("Guardados", escritos, border=True)
        st.metric(
            "Suma",
            moneda(sum(c.origen.monto for c in resultado.candidatos if c.ya_guardado)),
            border=True,
        )
        if incompletos:
            st.metric("Sin completar", len(incompletos), border=True)

    if incompletos:
        st.warning(
            f"{len(incompletos)} movimientos siguen sin categoría o cuenta y "
            "no se guardaron. Vuelve al detalle para completarlos.",
            icon=":material/info:",
        )

    cierre = st.columns([1, 1, 3])

    with cierre[0]:
        if st.button(
            "Cerrar importación", type="primary", icon=":material/check_circle:"
        ):
            st.session_state["terminado"] = escritos
            importacion.olvidar_avance()
            invalidar_datos()
            st.rerun()

    with cierre[1]:
        if st.button("Volver al detalle", icon=":material/arrow_back:"):
            st.session_state["paso"] = "completar"
            _recordar("completar")
            st.rerun()

    st.stop()


# ═══════════════════════════════════════════════════════════
# Terminado
# ═══════════════════════════════════════════════════════════

if st.session_state.get("terminado"):
    guardados = st.session_state["terminado"]

    st.success(
        f"Listo: {guardados} movimientos importados de {lectura.banco}.",
        icon=":material/check_circle:",
    )
    st.balloons()

    st.caption(
        "El concepto original del banco quedó en su propia columna de cada "
        "movimiento, por si necesitas rastrearlo contra el estado de cuenta."
    )

    if st.button("Importar otro documento", type="primary", icon=":material/add:"):
        _reiniciar()
        st.rerun()
