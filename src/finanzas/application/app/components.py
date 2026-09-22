from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from finanzas.analytics import aggregations as agg
from finanzas.application.app import theme as tema
from finanzas.application.services.analytics_service import (
    AnalyticsService,
    TableroPeriodo,
)
from finanzas.application.services.catalogos_service import CatalogosService
from finanzas.application.services.deseos_service import DeseosService
from finanzas.application.services.metas_service import MetasService
from finanzas.application.services.movimientos_service import MovimientosService
from finanzas.application.services.patrimonio_service import PatrimonioService
from finanzas.application.services.presupuesto_service import PresupuestoService
from finanzas.application.services.productos_service import ProductosService
from finanzas.application.services.proyectos_service import ProyectosService
from finanzas.application.services.suscripciones_service import SuscripcionesService

# ═══════════════════════════════════════════════════════════
# Piezas compartidas por las páginas
#
# Aquí viven el contenedor de servicios, la invalidación de
# caché tras cada escritura, el formato de moneda y los
# gráficos, para que las páginas queden como guiones cortos.
# ═══════════════════════════════════════════════════════════

# ── Paleta ───────────────────────────────────────────────
#
# Los colores viven en `theme.py` y dependen del tema activo:
# aquí sólo se nombran los estados, que son los mismos en
# ambos y se resuelven a color al dibujar.


def estado_color(estado: str) -> str:
    """Color del estado en el tema activo; nunca es un color de serie."""
    p = tema.paleta()
    colores = {
        "En orden": p.exito,
        "Atención": p.atencion,
        "Excedido": p.error,
        "Sin presupuesto": p.neutro,
        "Configurar": p.ajuste,
        "En ruta": p.exito,
        "Ajustar aportación": p.atencion,
        "Vencida": p.error,
        "Lograda": p.exito,
    }

    return colores.get(estado, p.neutro)


#: Icono de estado; acompaña siempre al color, que nunca va solo.
ESTADO_ICONO = {
    "En orden": ":material/check_circle:",
    "Atención": ":material/warning:",
    "Excedido": ":material/error:",
    "Sin presupuesto": ":material/remove:",
    "Configurar": ":material/tune:",
    "En ruta": ":material/check_circle:",
    "Ajustar aportación": ":material/warning:",
    "Vencida": ":material/error:",
    "Lograda": ":material/verified:",
}

ALTURA_GRAFICO = 280


@dataclass(slots=True)
class Servicios:
    """Contenedor de los servicios que usan las páginas."""

    movimientos: MovimientosService
    catalogos: CatalogosService
    presupuesto: PresupuestoService
    metas: MetasService
    patrimonio: PatrimonioService
    suscripciones: SuscripcionesService
    analytics: AnalyticsService
    proyectos: ProyectosService
    deseos: DeseosService
    productos: ProductosService


@st.cache_resource
def obtener_servicios() -> Servicios:
    """
    Construye los servicios una sola vez por sesión de servidor.

    Son objetos sin estado que abren su propia conexión por operación, así
    que compartirlos entre reruns es seguro y evita rearmarlos en cada uno.
    """
    return Servicios(
        movimientos=MovimientosService(),
        catalogos=CatalogosService(),
        presupuesto=PresupuestoService(),
        metas=MetasService(),
        patrimonio=PatrimonioService(),
        suscripciones=SuscripcionesService(),
        analytics=AnalyticsService(),
        proyectos=ProyectosService(),
        deseos=DeseosService(),
        productos=ProductosService(),
    )


# ── Invalidación de caché ────────────────────────────────


def version_datos() -> int:
    """
    Devuelve la versión actual de los datos.

    Las funciones cacheadas la reciben como argumento: al cambiar, Streamlit
    recalcula en vez de servir un tablero viejo tras una escritura.
    """
    return st.session_state.setdefault("version_datos", 0)


def invalidar_datos() -> None:
    """Marca los datos como cambiados. Llamar tras cada escritura."""
    st.session_state["version_datos"] = version_datos() + 1


@st.cache_data(ttl="10m", max_entries=24, show_spinner="Calculando el periodo…")
def cargar_tablero(periodo: str, version: int) -> TableroPeriodo:
    """Calcula el tablero de un periodo; `version` fuerza el recálculo."""
    return obtener_servicios().analytics.tablero(periodo)


def tablero_del_periodo(periodo: str) -> TableroPeriodo:
    """
    Devuelve el tablero del periodo, cacheado cuando se puede.

    Con el servidor vigilando el código, cambiar un módulo lo recarga; los
    servicios ya construidos siguen apuntando a las clases viejas y lo que
    producen ya no se puede serializar para la caché («no es el mismo
    objeto que…»). Ahí se reconstruyen los servicios y se reintenta; si
    ni así, se calcula sin caché, que es lento pero no deja la página en
    blanco. Reiniciar el servidor lo deja como nuevo.
    """
    from streamlit.runtime.caching.cache_errors import (
        UnserializableReturnValueError,
    )

    try:
        return cargar_tablero(periodo, version_datos())
    except UnserializableReturnValueError:
        logging.getLogger(__name__).warning(
            "Servicios de una versión anterior del código; se reconstruyen. "
            "Reinicia el servidor para recuperar la caché."
        )
        obtener_servicios.clear()
        cargar_tablero.clear()

    try:
        return cargar_tablero(periodo, version_datos())
    except UnserializableReturnValueError:
        return obtener_servicios().analytics.tablero(periodo)


# ── Formato ──────────────────────────────────────────────


def moneda(valor: float, simbolo: str = "$", decimales: int = 0) -> str:
    """Formatea un importe como moneda."""
    return f"{simbolo}{valor:,.{decimales}f}"


def porcentaje(valor: float, decimales: int = 0) -> str:
    """Formatea una proporción (0–1) como porcentaje."""
    return f"{valor * 100:.{decimales}f}%"


def insignia_estado(estado: str) -> str:
    """Devuelve el estado con su icono, para no depender sólo del color."""
    return f"{ESTADO_ICONO.get(estado, '')} {estado}".strip()


# ── Selector de periodo ──────────────────────────────────


def selector_periodo(clave: str = "periodo_activo") -> str:
    """
    Dibuja el selector de periodo en la barra lateral y devuelve el elegido.

    Ofrece los periodos con movimientos más el mes en curso, para poder
    empezar a capturar un mes que aún no existe en la base.
    """
    servicios = obtener_servicios()
    disponibles = servicios.analytics.periodos_disponibles()

    actual = agg.periodo_de(date.today())
    if actual not in disponibles:
        disponibles = [actual, *disponibles]

    # El histórico va primero y es el punto de partida; un mes es un filtro.
    disponibles = [agg.HISTORICO, *disponibles]

    if clave in st.session_state and st.session_state[clave] in disponibles:
        indice = disponibles.index(st.session_state[clave])
    else:
        indice = 0

    with st.sidebar:
        elegido = st.selectbox(
            "Periodo",
            disponibles,
            index=indice,
            format_func=agg.etiqueta_periodo,
            key=clave,
            help="Histórico es todo lo registrado. Elige un mes para filtrar.",
        )

    return elegido


def opciones_periodo(extra: str | None = None) -> list[str]:
    """Lista de periodos para selectores fuera de la barra lateral."""
    servicios = obtener_servicios()
    periodos = servicios.analytics.periodos_disponibles()

    actual = agg.periodo_de(date.today())
    for candidato in (actual, extra):
        if candidato and candidato not in periodos:
            periodos.insert(0, candidato)

    return periodos


# ── Gráficos ─────────────────────────────────────────────


def grafico_tendencia(tendencia: pd.DataFrame) -> alt.Chart:
    """
    Línea de ingresos, gastos y ahorro por mes.

    Tres series sobre un solo eje: comparar magnitudes de la misma unidad
    en un eje compartido es justo lo que un segundo eje arruinaría.
    """
    largo = tendencia.melt(
        id_vars=["periodo", "etiqueta"],
        value_vars=["ingresos", "gastos", "ahorro_inversion"],
        var_name="serie",
        value_name="monto",
    )
    largo["serie"] = largo["serie"].map(
        {
            "ingresos": "Ingresos",
            "gastos": "Gastos",
            "ahorro_inversion": "Ahorro e inversión",
        }
    )
    p = tema.paleta()

    return (
        alt.Chart(largo)
        .mark_line(strokeWidth=2.2, point=alt.OverlayMarkDef(size=42, filled=True))
        .encode(
            x=alt.X("periodo:O", title=None, axis=alt.Axis(labelAngle=-45)),
            y=alt.Y("monto:Q", title=None),
            color=alt.Color(
                "serie:N",
                title=None,
                scale=alt.Scale(
                    domain=["Ingresos", "Gastos", "Ahorro e inversión"],
                    range=[p.serie_1, p.serie_2, p.serie_3],
                ),
                legend=alt.Legend(orient="top"),
            ),
            tooltip=[
                alt.Tooltip("etiqueta:N", title="Mes"),
                alt.Tooltip("serie:N", title="Serie"),
                alt.Tooltip("monto:Q", title="Monto", format=",.0f"),
            ],
        )
        .properties(height=ALTURA_GRAFICO)
    )


def grafico_barras(
    datos: pd.DataFrame,
    dimension: str,
    medida: str,
    titulo_dimension: str,
    titulo_medida: str,
    horizontal: bool = True,
) -> alt.Chart:
    """
    Barras de una sola serie: todas del mismo color.

    Teñir cada barra según su tamaño duplicaría lo que ya dice su longitud
    y gastaría el único canal libre que queda.
    """
    eje_categoria = alt.Axis(labelLimit=180, domain=False, ticks=False)

    if horizontal:
        codificacion = {
            "y": alt.Y(
                f"{dimension}:N",
                title=titulo_dimension,
                sort="-x",
                axis=eje_categoria,
            ),
            "x": alt.X(f"{medida}:Q", title=None),
        }
    else:
        codificacion = {
            "x": alt.X(
                f"{dimension}:N",
                title=titulo_dimension,
                sort="-y",
                axis=eje_categoria,
            ),
            "y": alt.Y(f"{medida}:Q", title=None),
        }

    return (
        alt.Chart(datos)
        .mark_bar(color=tema.paleta().serie_1, cornerRadius=5, size=17)
        .encode(
            **codificacion,
            tooltip=[
                alt.Tooltip(f"{dimension}:N", title=titulo_dimension),
                alt.Tooltip(f"{medida}:Q", title=titulo_medida, format=",.0f"),
            ],
        )
        .properties(height=ALTURA_GRAFICO)
    )


def grafico_presupuesto(presupuesto: pd.DataFrame) -> alt.Chart:
    """
    Presupuesto activo contra gasto real, por categoría.

    Dos medidas de la misma unidad, en barras agrupadas sobre un solo eje.
    """
    largo = presupuesto.melt(
        id_vars=["categoria"],
        value_vars=["presupuesto_activo", "gasto_del_mes"],
        var_name="serie",
        value_name="monto",
    )
    largo["serie"] = largo["serie"].map(
        {"presupuesto_activo": "Presupuesto", "gasto_del_mes": "Gasto"}
    )
    p = tema.paleta()

    return (
        alt.Chart(largo)
        .mark_bar(cornerRadius=3, size=11)
        .encode(
            y=alt.Y(
                "categoria:N",
                title=None,
                sort="-x",
                axis=alt.Axis(labelLimit=180, domain=False, ticks=False),
            ),
            x=alt.X("monto:Q", title=None),
            yOffset=alt.YOffset("serie:N"),
            color=alt.Color(
                "serie:N",
                title=None,
                scale=alt.Scale(
                    domain=["Presupuesto", "Gasto"],
                    range=[p.serie_1, p.serie_2],
                ),
                legend=alt.Legend(orient="top"),
            ),
            tooltip=[
                alt.Tooltip("categoria:N", title="Categoría"),
                alt.Tooltip("serie:N", title=None),
                alt.Tooltip("monto:Q", title="Monto", format=",.0f"),
            ],
        )
        .properties(height=max(ALTURA_GRAFICO, 26 * len(presupuesto)))
    )


def grafico_calendario(calendario: pd.DataFrame) -> alt.Chart:
    """Gasto por día del mes; los días en cero se ven como huecos."""
    return (
        alt.Chart(calendario)
        .mark_bar(color=tema.paleta().serie_1, cornerRadius=3, size=11)
        .encode(
            x=alt.X(
                "dia:O",
                title="Día del mes",
                axis=alt.Axis(domain=False, ticks=False, labelOverlap=True),
            ),
            y=alt.Y("gasto:Q", title=None),
            tooltip=[
                alt.Tooltip("dia:O", title="Día"),
                alt.Tooltip("gasto:Q", title="Gasto", format=",.0f"),
            ],
        )
        .properties(height=220)
    )


def grafico_patrimonio(cierres: pd.DataFrame) -> alt.Chart:
    """Evolución del patrimonio neto: una sola serie, sin leyenda."""
    p = tema.paleta()

    return (
        alt.Chart(cierres)
        .mark_line(
            color=p.serie_1,
            strokeWidth=2.2,
            point=alt.OverlayMarkDef(size=48, filled=True, color=p.serie_1),
        )
        .encode(
            x=alt.X("periodo:O", title=None, axis=alt.Axis(labelAngle=-45)),
            y=alt.Y("patrimonio_neto:Q", title=None),
            tooltip=[
                alt.Tooltip("periodo:N", title="Periodo"),
                alt.Tooltip("patrimonio_neto:Q", title="Patrimonio", format=",.0f"),
                alt.Tooltip("cambio_mensual:Q", title="Cambio", format=",.0f"),
            ],
        )
        .properties(height=ALTURA_GRAFICO)
    )


def tabla_equivalente(datos: pd.DataFrame, etiqueta: str = "Ver los datos") -> None:
    """
    Muestra la tabla que acompaña a cada gráfico.

    El color y la posición nunca son la única vía para leer un valor: aquí
    están los mismos números en texto.
    """
    with st.expander(etiqueta, icon=":material/table_rows:"):
        st.dataframe(datos, hide_index=True)


# ── Mensajes ─────────────────────────────────────────────


def sin_datos(mensaje: str = "No hay movimientos en este periodo.") -> None:
    """Estado vacío uniforme, con la salida a la mano."""
    st.info(
        f"{mensaje} Captura uno en **Movimientos** para empezar.",
        icon=":material/info:",
    )


def reportar_error(error: Exception) -> None:
    """Muestra un error de validación como mensaje, no como traza."""
    st.error(str(error), icon=":material/error:")
