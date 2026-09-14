from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

# ═══════════════════════════════════════════════════════════
# Tema de la aplicación
#
# El color de la interfaz vive en `.streamlit/config.toml`.
# Aquí está lo que Streamlit no puede resolver solo: los
# colores que el código pasa explícitamente a los gráficos
# —que no heredan el tema— y los pocos ajustes de estilo que
# no son opciones de configuración.
#
# Las dos paletas se mantienen en paralelo porque el tema se
# puede alternar desde el menú: leer la activa en cada rerun
# sale más barato que fijar colores que sólo sirven en uno.
# ═══════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class Paleta:
    """Colores que el código necesita nombrar, por tema."""

    #: Acento: lo accionable y el foco. Es el único color de relleno
    #: fuerte en pantalla, así que cuando aparece significa algo.
    acento: str

    #: Luz que emite el acento. En un neón el tubo es de color profundo
    #: y lo brillante es el resplandor, así que el halo va más encendido
    #: que el relleno en vez de ser el mismo color con opacidad.
    acento_luz: str

    #: Series categóricas, en orden fijo: el color sigue a la serie,
    #: nunca a su tamaño ni a su posición en el ranking.
    serie_1: str
    serie_2: str
    serie_3: str

    #: Trazo de los ejes y de la retícula de los gráficos.
    eje: str
    reticula: str
    texto_tenue: str

    #: Colores de estado; nunca se reutilizan como color de serie.
    exito: str
    atencion: str
    error: str
    neutro: str
    ajuste: str


OSCURA = Paleta(
    acento="#d40b45",
    acento_luz="#ff2e63",
    serie_1="#e8134e",
    serie_2="#f0883e",
    serie_3="#4fd1c5",
    eje="#39424f",
    reticula="#1e242c",
    texto_tenue="#8b949e",
    exito="#3fb950",
    atencion="#e3b341",
    error="#f85149",
    neutro="#8b949e",
    ajuste="#f0883e",
)

CLARA = Paleta(
    acento="#b00840",
    acento_luz="#d4004f",
    serie_1="#b00840",
    serie_2="#eb6834",
    serie_3="#1baf7a",
    eje="#c9c6be",
    reticula="#ebe9e3",
    texto_tenue="#6b6862",
    exito="#0ca30c",
    atencion="#c88f10",
    error="#d03b3b",
    neutro="#898781",
    ajuste="#ec835a",
)


def paleta() -> Paleta:
    """
    Devuelve la paleta del tema activo.

    Cae en la oscura si Streamlit todavía no reporta el tema, que es lo
    que pasa en el primer render y en las pruebas sin navegador.
    """
    try:
        tipo = st.context.theme.type
    except Exception:
        return OSCURA

    return CLARA if tipo == "light" else OSCURA


# ═══════════════════════════════════════════════════════════
# Estilos
#
# Sólo lo que no es una opción de `config.toml`: densidad del
# lienzo, jerarquía de las pestañas y el halo del acento.
#
# El color del acento se sustituye desde la paleta en vez de
# leerse de una variable CSS, para no depender de qué nombres
# publique Streamlit por dentro, que cambian entre versiones.
# Los marcadores son `@@ACENTO@@` y no llaves porque `format`
# chocaría con las llaves de las propias reglas CSS.
# ═══════════════════════════════════════════════════════════

_ESTILOS = """
<style>
/* El encabezado flotante sólo estorba: la navegación está en la barra
   lateral y el título, en la página. */
header[data-testid="stHeader"] {
    background: transparent;
    height: 2.5rem;
}

/* Streamlit reserva arriba un espacio pensado para una portada. Esto es
   un tablero: ese aire se gana devolviéndolo al contenido. */
.stMainBlockContainer {
    padding-top: 2.5rem;
    padding-bottom: 4rem;
    max-width: 1400px;
}

/* Un título de página no necesita competir con los datos. */
.stMainBlockContainer h1 {
    letter-spacing: -0.02em;
    margin-bottom: 0.15rem;
}
.stMainBlockContainer h2,
.stMainBlockContainer h3 {
    letter-spacing: -0.01em;
}

/* El pie de cada título: el texto explicativo se lee como nota, no como
   contenido de primer nivel. */
.stMainBlockContainer h1 + div [data-testid="stCaptionContainer"] {
    max-width: 62ch;
}

/* ── Tarjetas ──────────────────────────────────────────── */

/* Contenedores y métricas con borde comparten el mismo lenguaje: una
   superficie apenas elevada, sin sombra dura. */
[data-testid="stMetric"] {
    padding: 0.9rem 1.1rem;
    transition: border-color 140ms ease, box-shadow 140ms ease;
}
[data-testid="stMetric"]:hover {
    border-color: @@ACENTO_45@@;
    box-shadow: 0 0 18px @@ACENTO_12@@;
}

/* La etiqueta de la métrica, en versalita tenue: el número manda. */
[data-testid="stMetricLabel"] {
    opacity: 0.72;
    font-size: 0.78rem;
    font-weight: 500;
    letter-spacing: 0.02em;
    text-transform: uppercase;
}
[data-testid="stMetricValue"] {
    letter-spacing: -0.03em;
    line-height: 1.2;
}
[data-testid="stMetricDelta"] {
    font-size: 0.8rem;
}

/* ── Pestañas ──────────────────────────────────────────── */

/* Subrayado fino en vez del bloque de color por defecto. */
.stTabs [data-baseweb="tab-list"] {
    gap: 1.6rem;
    border-bottom: 1px solid var(--border-color);
}
.stTabs [data-baseweb="tab"] {
    padding: 0.5rem 0;
    font-weight: 500;
}
.stTabs [data-baseweb="tab-highlight"] {
    height: 2px;
    box-shadow: 0 0 10px @@ACENTO_70@@;
}

/* ── Controles ─────────────────────────────────────────── */

/* El botón primario es el único elemento con relleno de acento en
   pantalla, así que se nota sin necesitar tamaño. El halo es lo que lo
   hace leer como neón: un color saturado sin luz alrededor sólo se ve
   chillón. */
.stButton button[kind="primary"],
.stFormSubmitButton button[kind="primary"] {
    font-weight: 600;
    box-shadow: 0 0 0 1px @@ACENTO_45@@, 0 2px 16px @@ACENTO_28@@;
    transition: box-shadow 140ms ease, filter 140ms ease, transform 120ms ease;
}
.stButton button[kind="primary"]:hover,
.stFormSubmitButton button[kind="primary"]:hover {
    filter: brightness(1.06);
    box-shadow: 0 0 0 1px @@ACENTO_70@@, 0 3px 24px @@ACENTO_45@@;
}

/* El foco se marca con el mismo halo: el teclado merece la misma pista
   visual que el ratón. */
.stButton button:focus-visible,
.stFormSubmitButton button:focus-visible,
.stSelectbox [data-baseweb="select"]:focus-within,
.stTextInput input:focus,
.stNumberInput input:focus {
    box-shadow: 0 0 0 2px @@ACENTO_55@@, 0 0 16px @@ACENTO_28@@;
}
.stButton button:active,
.stFormSubmitButton button:active {
    transform: translateY(1px);
}

/* Un expander cerrado es mobiliario; sólo se define al abrirse. */
.stExpander summary:hover {
    color: @@ACENTO@@;
}

/* ── Barra lateral ─────────────────────────────────────── */

/* La navegación se lee como índice: entradas compactas y el activo
   marcado por peso, no sólo por fondo. */
[data-testid="stSidebarNav"] a {
    border-radius: 0.5rem;
}
[data-testid="stSidebarNav"] a[aria-current="page"] span {
    font-weight: 600;
}

/* Una barra de acento en la página activa: el neón marca dónde estás. */
[data-testid="stSidebarNav"] a[aria-current="page"] {
    box-shadow: inset 2px 0 0 @@ACENTO@@;
}

/* ── Tablas ────────────────────────────────────────────── */

/* El encabezado se separa del cuerpo por peso y caja, no por una regla
   más: la retícula ya tiene suficientes líneas. */
.stDataFrame thead th {
    font-weight: 600;
    letter-spacing: 0.01em;
}
</style>
"""


def _con_alfa(color: str, porcentaje: int) -> str:
    """Devuelve el color en `rgba()` con la opacidad dada."""
    crudo = color.lstrip("#")
    r, g, b = (int(crudo[i : i + 2], 16) for i in (0, 2, 4))

    return f"rgba({r}, {g}, {b}, {porcentaje / 100:.2f})"


def aplicar_estilos() -> None:
    """
    Inyecta los estilos de la aplicación.

    Se llama una vez por rerun, antes de dibujar nada. El acento se
    sustituye aquí para que el halo siga al tema activo.
    """
    acento = paleta().acento
    hoja = _ESTILOS.replace("@@ACENTO@@", acento)
    for porcentaje in (12, 28, 45, 55, 70):
        hoja = hoja.replace(f"@@ACENTO_{porcentaje}@@", _con_alfa(acento, porcentaje))

    st.html(hoja)


def rotulo(texto: str) -> None:
    """
    Dibuja un rótulo de sección.

    Separa bloques que sin él se leerían como una sola lista de tarjetas
    iguales. Es jerarquía por tamaño y peso, no por color ni por regla.
    """
    st.html(
        f'<div style="font-size:0.74rem;font-weight:600;letter-spacing:0.08em;'
        f"text-transform:uppercase;opacity:0.55;margin:1.6rem 0 0.6rem;"
        f'">{texto}</div>'
    )


def firma_lateral(titulo: str, subtitulo: str) -> None:
    """
    Encabezado de la barra lateral.

    Da a la navegación un lugar del que colgar, que es lo que separa una
    aplicación de un cuaderno con páginas.
    """
    with st.sidebar:
        st.html(
            f'<div style="padding:0.35rem 0 1rem;">'
            f'<div style="font-size:1.02rem;font-weight:650;letter-spacing:-0.01em;">'
            f"{titulo}</div>"
            f'<div style="font-size:0.78rem;opacity:0.55;margin-top:0.1rem;">'
            f"{subtitulo}</div>"
            f"</div>"
        )
