from __future__ import annotations

import hashlib
from datetime import time

import streamlit as st

from finanzas.application.app.components import (
    avisar_parecidos,
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
from finanzas.domain import captura
from finanzas.domain.enums import (
    EstadoMovimiento,
    Naturaleza,
    Necesidad,
    TipoCuenta,
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
    "rendimientos": "Rendimientos",
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
SIN_DESTINO = "— elige una cuenta —"


def _opciones(df) -> dict[str, int]:
    """Convierte un catálogo en {nombre: id} para alimentar un selector."""
    return {fila.nombre: int(fila.id) for fila in df.itertuples()}


#: {id: tipo} de las cuentas activas, para aplicar las reglas por cuenta.
TIPO_DE_CUENTA: dict[int, str] = {
    int(fila.id): str(fila.tipo) for fila in catalogos["cuentas"].itertuples()
}
INSTITUCION_DE_CUENTA: dict[int, str] = {
    int(fila.id): str(fila.institucion).lower()
    for fila in catalogos["cuentas"].itertuples()
}


def _cuentas_destino(tipo: str, origen_id: int | None) -> dict[str, int]:
    """Cuentas a las que puede llegar el dinero, según el tipo."""
    cuentas = catalogos["cuentas"]
    if tipo in (str(TipoMovimiento.AHORRO), str(TipoMovimiento.INVERSION)):
        cuentas = cuentas[cuentas["tipo"].map(lambda t: TipoCuenta(t).guarda_ahorro)]

    return {
        nombre: identificador
        for nombre, identificador in _opciones(cuentas).items()
        if identificador != origen_id
    }


def _cuentas_activas() -> list[int]:
    """Ids de las cuentas activas, en el orden del catálogo."""
    return list(TIPO_DE_CUENTA)


def _hermana(cuenta_id: int, condicion) -> int | None:
    """
    Busca la cuenta de la misma institución que cumple la condición.

    Es lo que un pago de tarjeta o un apartado necesitan: visto desde la
    cuenta, la tarjeta de ese banco; visto desde la tarjeta, la cuenta.
    Si hay varias o ninguna, no adivina.
    """
    institucion = INSTITUCION_DE_CUENTA.get(cuenta_id, "").replace(" ", "")
    if not institucion:
        return None
    candidatas = [
        otra
        for otra in _cuentas_activas()
        if otra != cuenta_id
        and INSTITUCION_DE_CUENTA.get(otra, "").replace(" ", "") == institucion
        and condicion(TipoCuenta(TIPO_DE_CUENTA[otra]))
    ]
    return candidatas[0] if len(candidatas) == 1 else None


def _de_otro_banco(cuenta_id: int, condicion) -> int | None:
    """La única cuenta de otra institución que cumple la condición, si es una."""
    institucion = INSTITUCION_DE_CUENTA.get(cuenta_id, "").replace(" ", "")
    candidatas = [
        otra
        for otra in _cuentas_activas()
        if otra != cuenta_id
        and INSTITUCION_DE_CUENTA.get(otra, "").replace(" ", "") != institucion
        and condicion(TipoCuenta(TIPO_DE_CUENTA[otra]))
    ]
    return candidatas[0] if len(candidatas) == 1 else None


def _cuenta_efectivo() -> int | None:
    """La cuenta de efectivo, si hay una sola."""
    efectivos = [
        otra for otra in _cuentas_activas() if TIPO_DE_CUENTA[otra] == "Efectivo"
    ]
    return efectivos[0] if len(efectivos) == 1 else None


def _sugerir_patas(
    candidato, cuenta_documento: int | None
) -> tuple[int | None, int | None]:
    """
    Propone origen y destino según la clase de movimiento y su dirección.

    La cuenta del documento es una de las dos patas: el origen si es un
    cargo, el destino si es un abono. La otra la dice la clase: la
    tarjeta hermana en un pago, efectivo en un retiro, el apartado del
    mismo banco, o —en una transferencia a uno mismo— la cuenta de otro
    banco. Una transferencia *recibida* de uno mismo es un traspaso que
    entra aquí y salió de otra cuenta propia, nunca un ingreso.
    """
    origen = candidato.origen
    if cuenta_documento is None:
        return None, None
    if not origen.es_traspaso:
        return cuenta_documento, None

    documento = TipoCuenta(TIPO_DE_CUENTA[cuenta_documento])
    if origen.es_pago_tarjeta:
        otra = _hermana(cuenta_documento, lambda t: t.es_pasivo != documento.es_pasivo)
    elif origen.es_retiro_efectivo:
        otra = _cuenta_efectivo()
    elif origen.es_apartado:
        otra = _hermana(
            cuenta_documento, lambda t: t.guarda_ahorro != documento.guarda_ahorro
        )
    else:
        otra = _de_otro_banco(cuenta_documento, lambda t: t == TipoCuenta.DEBITO)

    if otra is None:
        # Sin una candidata clara, cualquier otra cuenta: el usuario corrige.
        otra = next((c for c in _cuentas_activas() if c != cuenta_documento), None)

    if origen.es_cargo:
        return cuenta_documento, otra
    return otra, cuenta_documento


def _selector_texto_libre(
    etiqueta: str, actual: str, previos: list[str], key: str, placeholder: str
) -> str:
    """
    Selector de texto libre con lo ya usado como opciones.

    El valor actual se conserva aunque no esté entre los previos, para que
    volver a un movimiento no borre lo que se le había puesto.
    """
    opciones = ["", *previos]
    if actual and actual not in opciones:
        opciones.insert(1, actual)
    elegido = st.selectbox(
        etiqueta,
        opciones,
        index=opciones.index(actual) if actual in opciones else 0,
        accept_new_options=True,
        placeholder=placeholder,
        key=key,
        persist_state="session",
    )
    return elegido or ""


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


def _categorias_de(tipo: str):
    """
    Devuelve las categorías del tipo elegido.

    Si el tipo aún no tiene categorías propias, se muestran todas para no
    dejar al usuario sin poder capturar.
    """
    df = catalogos["categorias"]
    propias = df[df["tipo"] == tipo]

    return propias if not propias.empty else df


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


def _editar_productos(candidato, sello: str) -> None:
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
                key=f"prod_nom_{sello}_{numero}",
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
                key=f"prod_cant_{sello}_{numero}",
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
                key=f"prod_precio_{sello}_{numero}",
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
                "", icon=":material/delete:", key=f"prod_del_{sello}_{numero}"
            ):
                candidato.productos.pop(numero)
                st.rerun()

    acciones = st.columns([1, 3])

    with acciones[0]:
        if st.button(
            "Agregar producto", icon=":material/add:", key=f"prod_mas_{sello}"
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


def _firma(resultado, candidato) -> str:
    """Identidad corta de una línea dentro de su documento, para las claves."""
    origen = candidato.origen
    crudo = "|".join(
        str(parte)
        for parte in (
            resultado.lectura.banco,
            resultado.lectura.periodo_inicio,
            origen.fecha,
            origen.monto,
            origen.descripcion_banco,
            origen.referencia,
        )
    )
    return hashlib.md5(crudo.encode()).hexdigest()[:10]  # noqa: S324 - no es seguridad


_PREFIJOS_DE_LINEA = (
    "prod_",
    "fecha_",
    "tipo_",
    "hora_",
    "monto_",
    "cat_",
    "sub_",
    "cta_",
    "dest_",
    "desc_",
    "empresa_",
    "lugar_",
    "pag_",
    "fpago_",
    "medio_",
    "nec_",
    "nat_",
    "proy_",
    "etq_",
    "estado_",
    "nota_",
    "rec_",
    "plan_",
    "detalle_",
)


def _reiniciar() -> None:
    """Deja la página lista para un documento nuevo, sin rastro del anterior."""
    for clave in ("importacion", "paso", "indice", "terminado", "anclados"):
        st.session_state.pop(clave, None)
    # Lo que los widgets de cada línea dejaron persistido tampoco se queda:
    # no debe reaparecer en el documento siguiente.
    for clave in list(st.session_state):
        if isinstance(clave, str) and clave.startswith(_PREFIJOS_DE_LINEA):
            st.session_state.pop(clave, None)
    importacion.olvidar_avance()


# ═══════════════════════════════════════════════════════════
# Paso 1: leer
# ═══════════════════════════════════════════════════════════


# Tras un cambio de código, `session_state` puede traer candidatos de una
# versión anterior de la clase —sin algún campo nuevo— y con `slots` no
# se les puede añadir. Se descartan y se retoma del avance en la base,
# que se reconstruye con la clase actual.
def _sesion_compatible() -> bool:
    resultado_previo = st.session_state.get("importacion")
    if resultado_previo is None:
        return True
    muestra = resultado_previo.candidatos[:1]
    return (
        hasattr(resultado_previo, "cuenta_id")
        and all(
            hasattr(c, campo)
            for c in muestra
            for campo in ("empresa", "lugar", "hora", "productos", "guardados")
        )
        and all(hasattr(c.origen, "es_traspaso_propio") for c in muestra)
    )


if not _sesion_compatible():
    for clave in ("importacion", "paso", "indice"):
        st.session_state.pop(clave, None)

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

    # De qué cuenta es el documento. Es el origen por defecto de cada
    # movimiento y la cuenta a la que se anclan los saldos que declara.
    with st.container(border=True):
        columnas_cuenta = st.columns([1, 2])
        opciones_documento = _opciones(catalogos["cuentas"])
        nombres_documento = list(opciones_documento)
        actual_documento = next(
            (n for n, i in opciones_documento.items() if i == resultado.cuenta_id),
            None,
        )
        with columnas_cuenta[0]:
            elegida_documento = st.selectbox(
                "Cuenta de este estado de cuenta",
                nombres_documento,
                index=nombres_documento.index(actual_documento)
                if actual_documento in nombres_documento
                else 0,
                key="cuenta_documento_sel",
                help="La cuenta o tarjeta de la que es el documento.",
            )
        elegida_id = opciones_documento[elegida_documento]
        if elegida_id != resultado.cuenta_id:
            # Otra cuenta cambia qué cuenta como ya registrado: la misma
            # cifra en otra cuenta no es el mismo movimiento.
            resultado.cuenta_id = elegida_id
            importacion.contrastar(resultado)
            _recordar()
        st.session_state["cuenta_documento"] = elegida_documento

        with columnas_cuenta[1]:
            anclas_documento = resultado.anclas
            if anclas_documento:
                partes = " · ".join(
                    f"{moneda(saldo, decimales=2)} al {fecha:%d/%m/%Y}"
                    for fecha, saldo in anclas_documento
                )
                st.info(
                    f"El documento declara saldos: {partes}. Al cerrar la "
                    "importación quedan como saldos verificados de "
                    f"**{elegida_documento}**, y el sistema deduce el resto.",
                    icon=":material/verified:",
                )
            else:
                st.caption(
                    "El documento no declara saldos, así que no anclará ninguno."
                )

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

    # Desmarcar es no guardarlo. Si ya se había guardado en una pasada
    # anterior, desmarcarlo lo borra de la base: la casilla manda.
    descartados = 0
    for candidato, incluir in zip(resultado.candidatos, editada["incluir"]):
        candidato.incluir = bool(incluir)
        if not candidato.incluir and candidato.ya_guardado:
            importacion.descartar(candidato)
            descartados += 1
    if descartados:
        invalidar_datos()
        _recordar()

    seleccionados = resultado.a_importar
    st.caption(
        f"**{len(seleccionados)} seleccionados** por "
        f"{moneda(sum(c.origen.monto for c in seleccionados))}. Lo que "
        "desmarques no se guarda, y si ya se había guardado, se borra."
    )

    totales = [c for c in resultado.candidatos if c.origen.es_total]
    if totales:
        juntadas = sum(c.origen.agrupa for c in totales)
        st.caption(
            f"{juntadas} ganancias de centavos se juntaron en "
            f"{len(totales)} total(es) por mes, al final de la tabla."
        )

    with st.container(horizontal=True):
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

    # Las claves de los widgets llevan la firma de la línea, no sólo su
    # posición: con `persist_state="session"`, un `fecha_3` a secas
    # sobreviviría al siguiente documento y su tercera línea heredaría la
    # fecha que se corrigió en éste, pisando la del banco sin avisar.
    sello = f"{indice}_{_firma(resultado, candidato)}"

    st.progress(
        listos / total,
        text=f"Movimiento {indice + 1} de {total} · {listos} completos",
    )

    # Lo que dijo el banco, como contexto: el formulario de abajo es el
    # mismo de Movimientos, con los valores del documento ya puestos.
    with st.container(border=True):
        contexto = st.columns([3, 1])
        with contexto[0]:
            st.markdown(f"**Del banco:** {origen.descripcion_banco}")
            pista = f"{origen.fecha:%d/%m/%Y} · sugerido como {candidato.tipo_sugerido}"
            if origen.categoria_banco:
                pista += f" · lo clasificó como {origen.categoria_banco}"
            if origen.es_total:
                pista += f" · junta {origen.agrupa} líneas del documento"
            elif origen.referencia:
                pista += f" · folio `{origen.referencia}`"
            st.caption(pista)
        with contexto[1]:
            st.metric("Cobro", moneda(origen.monto, decimales=2))

    with st.container(border=True):
        fila_1 = st.columns([1, 1, 2, 1])

        with fila_1[0]:
            # La fecha sí se corrige: el banco pone la de aplicación y a
            # veces se quiere la de compra. La del banco se guarda aparte,
            # así que corregirla no rompe el reconocimiento al reimportar.
            candidato.fecha = st.date_input(
                "Fecha",
                value=candidato.fecha_final,
                format="DD/MM/YYYY",
                help=(
                    f"El banco la reporta el {origen.fecha:%d/%m/%Y}. Puedes "
                    "poner la de compra; la del banco se conserva aparte."
                ),
                key=f"fecha_{sello}",
                persist_state="session",
            )

        with fila_1[2]:
            tipos = [str(valor) for valor in TipoMovimiento]
            tipo = st.segmented_control(
                "Tipo",
                tipos,
                default=candidato.tipo,
                key=f"tipo_{sello}",
                persist_state="session",
                help=(
                    "El banco sólo dice si entró o salió dinero. Un abono en "
                    "la tarjeta puede ser el pago del corte —un traspaso— o "
                    "una devolución, que sí es ingreso. Un retiro en cajero "
                    "también es traspaso: a efectivo."
                ),
            )
            candidato.tipo_elegido = tipo or candidato.tipo_sugerido

        with fila_1[1]:
            # La hora sí se captura: ningún banco la trae, así que si la
            # sabes, aquí va. Sólo en gastos: a un traspaso no le hace falta.
            if captura.admite(candidato.tipo, "hora"):
                hora = st.time_input(
                    "Hora",
                    value=time.fromisoformat(candidato.hora)
                    if candidato.hora
                    else None,
                    step=300,
                    help="Opcional. El estado de cuenta no la trae.",
                    key=f"hora_{sello}",
                    persist_state="session",
                )
                candidato.hora = hora.strftime("%H:%M") if hora else ""
            else:
                candidato.hora = ""
                st.markdown("&nbsp;")

        with fila_1[3]:
            # El monto no se edita: es lo que se cobró.
            st.number_input(
                "Monto",
                value=float(origen.monto),
                format="%.2f",
                disabled=True,
                help=(
                    "Viene del estado de cuenta y no se cambia: es con lo "
                    "que se reconoce el movimiento si vuelves a importar."
                ),
                key=f"monto_{sello}",
            )

        tipo = candidato.tipo
        categorias_tipo = _categorias_de(tipo)
        opciones_categoria = _opciones(categorias_tipo)
        nombres = list(opciones_categoria)
        pedir_categoria = captura.admite(tipo, "categoria") or len(nombres) > 1

        # Origen y destino propuestos según el documento y la clase de
        # movimiento; el usuario los puede cambiar.
        origen_sugerido, destino_sugerido = _sugerir_patas(
            candidato, resultado.cuenta_id
        )

        fila_2 = st.columns(3)

        if pedir_categoria:
            with fila_2[0]:
                sugerida = _sugerir_categoria(
                    origen.categoria_banco, opciones_categoria
                )
                actual = _nombre_de(candidato.categoria_id, opciones_categoria)
                inicial = actual or sugerida
                categoria = st.selectbox(
                    "Categoría",
                    nombres,
                    index=nombres.index(inicial) if inicial in nombres else 0,
                    key=f"cat_{sello}_{tipo}",
                    persist_state="session",
                )
                candidato.categoria_id = opciones_categoria[categoria]

            with fila_2[1]:
                candidato.subcategoria_id = _selector_subcategoria(
                    candidato.categoria_id,
                    candidato.subcategoria_id,
                    clave=f"sub_{sello}",
                )
            columna_cuenta = fila_2[2]
        else:
            candidato.categoria_id = next(iter(opciones_categoria.values()))
            candidato.subcategoria_id = None
            columna_cuenta = fila_2[0]

        with columna_cuenta:
            opciones_cuenta = _opciones(catalogos["cuentas"])
            nombres_cuenta = list(opciones_cuenta)
            inicial_cuenta = _nombre_de(
                candidato.cuenta_id or origen_sugerido, opciones_cuenta
            ) or st.session_state.get("cuenta_documento", "")
            cuenta = st.selectbox(
                captura.etiqueta_cuenta(tipo),
                nombres_cuenta,
                index=nombres_cuenta.index(inicial_cuenta)
                if inicial_cuenta in nombres_cuenta
                else 0,
                key=f"cta_{sello}",
                persist_state="session",
            )
            candidato.cuenta_id = opciones_cuenta[cuenta]

        tipo_cuenta = TIPO_DE_CUENTA.get(candidato.cuenta_id)

        # Lo que mueve dinero entre cuentas propias necesita las dos.
        if captura.con_destino(tipo):
            destino = st.columns([1, 2])

            with destino[0]:
                otras = _cuentas_destino(tipo, candidato.cuenta_id)
                opciones_destino = {SIN_DESTINO: None} | otras
                nombres_destino = list(opciones_destino)
                previo_destino = _nombre_de(
                    candidato.cuenta_destino_id or destino_sugerido, otras
                )
                cuenta_destino = st.selectbox(
                    "Cuenta de destino",
                    nombres_destino,
                    index=nombres_destino.index(previo_destino)
                    if previo_destino in nombres_destino
                    else 0,
                    key=f"dest_{sello}_{tipo}",
                    persist_state="session",
                    help="A dónde llega el dinero que sale de la cuenta de origen.",
                )
                candidato.cuenta_destino_id = opciones_destino[cuenta_destino]

            with destino[1]:
                st.markdown("&nbsp;")
                _mostrar_pista(
                    captura.describir_traspaso(
                        tipo,
                        tipo_cuenta,
                        TIPO_DE_CUENTA.get(candidato.cuenta_destino_id)
                        if candidato.cuenta_destino_id
                        else None,
                    )
                )
        else:
            candidato.cuenta_destino_id = None

        texto = st.columns([3, 2, 2] if captura.admite(tipo, "empresa") else [1])

        with texto[0]:
            candidato.descripcion = st.text_input(
                "Descripción",
                value=candidato.descripcion or origen.descripcion_banco,
                placeholder="Supermercado quincenal",
                help=(
                    "Cómo lo describirías tú. Puedes cambiarla sin perder nada: "
                    "el concepto del banco se guarda en su propia columna."
                ),
                key=f"desc_{sello}",
                persist_state="session",
            )

        if captura.admite(tipo, "empresa"):
            with texto[1]:
                candidato.empresa = _selector_texto_libre(
                    "Empresa",
                    candidato.empresa,
                    servicios.movimientos.empresas(),
                    key=f"empresa_{sello}",
                    placeholder="Walmart",
                )

            with texto[2]:
                candidato.lugar = _selector_texto_libre(
                    "Lugar",
                    candidato.lugar,
                    servicios.movimientos.lugares(),
                    key=f"lugar_{sello}",
                    placeholder="Mitikah",
                )
        else:
            candidato.empresa = ""
            candidato.lugar = ""

        # ── Pago: sólo un gasto desde caja puede quedar a deber ──
        es_gasto = tipo == str(TipoMovimiento.GASTO)
        if captura.admite(tipo, "pago"):
            if captura.pago_lo_decide_la_cuenta(tipo, tipo_cuenta):
                candidato.pagado = True
                candidato.fecha_pago = None
                st.info(
                    f"Lo paga **{cuenta}**: queda como deuda de la tarjeta hasta "
                    "que la pagues con un traspaso. El gasto cuenta una sola vez.",
                    icon=":material/credit_card:",
                )
            else:
                pago = st.columns([1, 1, 2])

                with pago[0]:
                    candidato.pagado = st.checkbox(
                        "Ya se pagó",
                        value=candidato.pagado,
                        key=f"pag_{sello}",
                        persist_state="session",
                        help=(
                            "Desmárcalo sólo si el gasto sigue sin salir de la "
                            "cuenta; lo normal en un estado de cuenta es que ya "
                            "salió."
                        ),
                    )

                with pago[1]:
                    if candidato.pagado:
                        candidato.fecha_pago = st.date_input(
                            "Fecha de pago",
                            value=candidato.fecha_pago
                            or origen.fecha_cargo
                            or origen.fecha,
                            format="DD/MM/YYYY",
                            key=f"fpago_{sello}",
                            persist_state="session",
                        )
                    else:
                        candidato.fecha_pago = None
                        st.markdown("&nbsp;")

                with pago[2]:
                    if not candidato.pagado:
                        st.info(
                            "Queda como pendiente de pago y suma a tus adeudos.",
                            icon=":material/schedule:",
                        )
        else:
            candidato.pagado = True
            candidato.fecha_pago = None

    # ── Caja 2: el detalle, aparte y a la vista ──────────

    with st.container(border=True):
        st.markdown("**Detalle**")

        if es_gasto:
            fila_3 = st.columns(3)

            with fila_3[0]:
                opciones_medio = {"— sin especificar —": None} | _opciones(
                    catalogos["medios_pago"]
                )
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
                    key=f"medio_{sello}",
                    persist_state="session",
                    help="Si lo dejas vacío, se toma el que implica la cuenta.",
                )
                candidato.medio_pago_id = opciones_medio[medio]

            with fila_3[1]:
                candidato.necesidad = st.segmented_control(
                    "Esencial o deseo",
                    [str(valor) for valor in Necesidad],
                    default=candidato.necesidad,
                    key=f"nec_{sello}",
                    persist_state="session",
                ) or str(Necesidad.ESENCIAL)

            with fila_3[2]:
                candidato.naturaleza = st.segmented_control(
                    "Fijo o variable",
                    [str(valor) for valor in Naturaleza],
                    default=candidato.naturaleza,
                    key=f"nat_{sello}",
                    persist_state="session",
                ) or str(Naturaleza.VARIABLE)
        else:
            candidato.medio_pago_id = None
            candidato.necesidad = str(Necesidad.ESENCIAL)
            candidato.naturaleza = str(Naturaleza.VARIABLE)

        fila_4 = st.columns(3)

        with fila_4[0]:
            previos = servicios.proyectos.nombres()
            opciones_proyecto = [SIN_PROYECTO, *previos]
            elegido = candidato.proyecto or SIN_PROYECTO
            proyecto = st.selectbox(
                "Proyecto o persona",
                opciones_proyecto,
                index=opciones_proyecto.index(elegido)
                if elegido in opciones_proyecto
                else 0,
                accept_new_options=True,
                help=(
                    "Agrupa movimientos de distintas categorías bajo un "
                    "mismo esfuerzo: un viaje, una mudanza, una obra. "
                    "Escribe uno nuevo para crearlo."
                ),
                key=f"proy_{sello}",
                persist_state="session",
            )
            candidato.proyecto = "" if proyecto == SIN_PROYECTO else proyecto

        with fila_4[1]:
            candidato.etiquetas = st.text_input(
                "Etiquetas",
                value=candidato.etiquetas,
                placeholder="despensa, quincena",
                key=f"etq_{sello}",
                persist_state="session",
            )

        with fila_4[2]:
            if captura.admite(tipo, "estado"):
                candidato.estado_movimiento = st.segmented_control(
                    "Estado",
                    [str(valor) for valor in EstadoMovimiento],
                    default=candidato.estado_movimiento,
                    key=f"estado_{sello}",
                    persist_state="session",
                ) or str(EstadoMovimiento.CONFIRMADO)
            else:
                candidato.estado_movimiento = str(EstadoMovimiento.CONFIRMADO)

        candidato.nota = st.text_area(
            "Nota o comprobante",
            value=candidato.nota,
            height=80,
            key=f"nota_{sello}",
            persist_state="session",
        )

        if es_gasto:
            banderas = st.columns(2)
            with banderas[0]:
                candidato.recurrente = st.checkbox(
                    "Es un gasto recurrente",
                    value=candidato.recurrente,
                    key=f"rec_{sello}",
                    persist_state="session",
                )
            with banderas[1]:
                candidato.planeado = st.checkbox(
                    "Estaba planeado",
                    value=candidato.planeado,
                    key=f"plan_{sello}",
                    persist_state="session",
                )
        else:
            candidato.recurrente = False
            candidato.planeado = True

    # ── Qué venía en la compra ───────────────────────────

    if captura.admite(tipo, "productos"):
        detallar = st.toggle(
            "Apuntar los productos",
            value=bool(candidato.productos),
            key=f"detalle_{sello}",
            persist_state="session",
            help=(
                "Para una compra de varias cosas. El gasto sigue siendo uno "
                "con su categoría; esto guarda qué venía dentro, y no hace "
                "falta listarlo todo."
            ),
        )

        if detallar and not candidato.productos:
            candidato.productos = [Producto(precio_unitario=origen.monto)]
        elif not detallar and candidato.productos:
            candidato.productos = []

        if detallar:
            with st.container(border=True):
                _editar_productos(candidato, sello)
    else:
        candidato.productos = []

    candidato.revisado = True

    if candidato.desglose_excedido:
        st.warning(
            f"Los productos suman "
            f"{moneda(abs(candidato.sin_desglosar), decimales=2)} más de lo "
            "que se cobró. Ajústalos antes de continuar.",
            icon=":material/balance:",
        )

    # Un contenedor horizontal hace wrap en el teléfono en vez de apilar
    # las columnas una debajo de otra. Siguiente va primero: es lo que el
    # pulgar busca.
    with st.container(horizontal=True):
        # Avanzar más allá del último no cierra la edición: dar la vuelta
        # entera es normal cuando se corrige algo, y sólo «Terminar»
        # decide que ya está.
        if st.button("Siguiente", type="primary", icon=":material/arrow_forward:"):
            _guardar_actual(candidato)
            st.session_state["indice"] = (indice + 1) % total
            _recordar()
            st.rerun()

        if st.button("Anterior", icon=":material/arrow_back:", disabled=indice == 0):
            _guardar_actual(candidato)
            st.session_state["indice"] = indice - 1
            _recordar()
            st.rerun()

        if st.button("Saltar", icon=":material/skip_next:"):
            # Saltar significa lo mismo antes y después de haber guardado:
            # si ya estaba en la base, sale de ella.
            candidato.incluir = False
            importacion.descartar(candidato)
            st.session_state["indice"] = indice % max(total - 1, 1)
            _recordar()
            st.rerun()

        if st.button("Terminar", icon=":material/task_alt:"):
            _guardar_actual(candidato)
            st.session_state["paso"] = "guardar"
            _recordar("guardar")
            st.rerun()

    escritos = sum(len(c.guardados) for c in resultado.candidatos)
    pie = (
        "Se guarda solo al avanzar, saltar o terminar, y el avance "
        "sobrevive a recargar la página."
    )
    if escritos:
        pie += f" Llevas {escritos} movimientos guardados."
    st.caption(pie)

    # El documento puede traer un cobro que ya está registrado con otro
    # folio —capturado a mano, o importado del banco que lo liquida— y
    # entonces la deduplicación por folio no lo cazó. Fecha y monto sí.
    # Va al final, fuera del camino de la captura.
    avisar_parecidos(
        candidato.fecha_final,
        float(origen.monto),
        clave=sello,
        excluir=candidato.guardados,
        al_salir=_recordar,
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

    with st.container(horizontal=True):
        if st.button(
            "Cerrar importación", type="primary", icon=":material/check_circle:"
        ):
            # Los saldos que declara el documento son la mejor verdad que hay
            # sobre la cuenta: se anclan al cerrar, cuando ya está todo.
            anclados = importacion.anclar(resultado)
            st.session_state["terminado"] = escritos
            st.session_state["anclados"] = anclados
            importacion.olvidar_avance()
            invalidar_datos()
            st.rerun()

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

    anclados = st.session_state.get("anclados", 0)
    if anclados:
        st.info(
            f"Quedaron {anclados} saldos verificados de la cuenta del documento. "
            "En **Patrimonio** puedes ver si los movimientos cuadran con ellos.",
            icon=":material/verified:",
        )

    st.caption(
        "El concepto original del banco quedó en su propia columna de cada "
        "movimiento, por si necesitas rastrearlo contra el estado de cuenta."
    )

    if st.button("Importar otro documento", type="primary", icon=":material/add:"):
        _reiniciar()
        st.rerun()
