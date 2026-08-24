from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from finanzas.analytics import aggregations as agg
from finanzas.application.services.analytics_service import (
    AnalyticsService,
    TableroPeriodo,
)
from finanzas.application.services.catalogos_service import CatalogosService
from finanzas.application.services.metas_service import MetasService
from finanzas.application.services.movimientos_service import MovimientosService
from finanzas.application.services.patrimonio_service import PatrimonioService
from finanzas.application.services.presupuesto_service import PresupuestoService
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
# Slots categóricos en orden fijo: el color sigue a la serie,
# nunca a su tamaño ni a su posición en el ranking.

SERIE_1 = "#2a78d6"  # azul
SERIE_2 = "#eb6834"  # naranja
SERIE_3 = "#1baf7a"  # aqua

#: Colores de estado; nunca se reutilizan como color de serie.
ESTADO_COLOR = {
    "En orden": "#0ca30c",
    "Atención": "#fab219",
    "Excedido": "#d03b3b",
    "Sin presupuesto": "#898781",
    "Configurar": "#ec835a",
    "En ruta": "#0ca30c",
    "Ajustar aportación": "#fab219",
    "Vencida": "#d03b3b",
    "Lograda": "#0ca30c",
}

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

    return (
        alt.Chart(largo)
        .mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=45, filled=True))
        .encode(
            x=alt.X("periodo:O", title=None, axis=alt.Axis(labelAngle=-45)),
            y=alt.Y("monto:Q", title="Monto"),
            color=alt.Color(
                "serie:N",
                title=None,
                scale=alt.Scale(
                    domain=["Ingresos", "Gastos", "Ahorro e inversión"],
                    range=[SERIE_1, SERIE_2, SERIE_3],
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
    eje_categoria = alt.Axis(labelLimit=180)

    if horizontal:
        codificacion = {
            "y": alt.Y(
                f"{dimension}:N",
                title=titulo_dimension,
                sort="-x",
                axis=eje_categoria,
            ),
            "x": alt.X(f"{medida}:Q", title=titulo_medida),
        }
    else:
        codificacion = {
            "x": alt.X(
                f"{dimension}:N",
                title=titulo_dimension,
                sort="-y",
                axis=eje_categoria,
            ),
            "y": alt.Y(f"{medida}:Q", title=titulo_medida),
        }

    return (
        alt.Chart(datos)
        .mark_bar(color=SERIE_1, cornerRadius=4, size=18)
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

    return (
        alt.Chart(largo)
        .mark_bar(cornerRadius=4, size=12)
        .encode(
            y=alt.Y(
                "categoria:N", title=None, sort="-x", axis=alt.Axis(labelLimit=180)
            ),
            x=alt.X("monto:Q", title="Monto"),
            yOffset=alt.YOffset("serie:N"),
            color=alt.Color(
                "serie:N",
                title=None,
                scale=alt.Scale(
                    domain=["Presupuesto", "Gasto"],
                    range=[SERIE_1, SERIE_2],
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
        .mark_bar(color=SERIE_1, cornerRadius=3, size=12)
        .encode(
            x=alt.X("dia:O", title="Día del mes"),
            y=alt.Y("gasto:Q", title="Gasto"),
            tooltip=[
                alt.Tooltip("dia:O", title="Día"),
                alt.Tooltip("gasto:Q", title="Gasto", format=",.0f"),
            ],
        )
        .properties(height=220)
    )


def grafico_patrimonio(cierres: pd.DataFrame) -> alt.Chart:
    """Evolución del patrimonio neto: una sola serie, sin leyenda."""
    return (
        alt.Chart(cierres)
        .mark_line(
            color=SERIE_1,
            strokeWidth=2,
            point=alt.OverlayMarkDef(size=50, filled=True, color=SERIE_1),
        )
        .encode(
            x=alt.X("periodo:O", title=None, axis=alt.Axis(labelAngle=-45)),
            y=alt.Y("patrimonio_neto:Q", title="Patrimonio neto"),
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
