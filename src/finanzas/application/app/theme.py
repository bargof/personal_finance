from __future__ import annotations

from dataclasses import dataclass

import streamlit as st
import streamlit.components.v1 as components

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
    acento="#8ad4bf",
    acento_luz="#a8f0da",
    serie_1="#8ad4bf",
    serie_2="#f0883e",
    serie_3="#7aa2f7",
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
    acento="#1f8a6b",
    acento_luz="#2fb08a",
    serie_1="#1f8a6b",
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
# Cristal: paneles translúcidos que desenfocan manchas de luz
# puestas en el fondo. Sin esas manchas no habría nada que
# desenfocar y el cristal sería sólo un gris. El acento va en
# el botón primario —iluminado desde dentro— y en los halos.
#
# El color del acento se sustituye desde la paleta en vez de
# leerse de una variable CSS, para no depender de qué nombres
# publique Streamlit por dentro, que cambian entre versiones.
# Los marcadores son `@@ACENTO_NN@@` y `@@ACENTO_LUZ_NN@@`, con
# la opacidad en dos cifras, y no llaves porque `format`
# chocaría con las llaves de las propias reglas CSS.
# ═══════════════════════════════════════════════════════════

_ESTILOS = """
<style>
/* ═══════════════════════════════════════════════════════════
   Cristal
   ═══════════════════════════════════════════════════════════ */

/* El cristal necesita algo detrás que difuminar: sobre un fondo plano un
   panel translúcido es sólo un panel gris. Estas manchas de luz —del
   acento y de un azul frío— son lo que los paneles desenfocan. Van
   fijas para que no se muevan con el scroll. */
.stApp, [data-testid="stAppViewContainer"] {
    background:
        radial-gradient(ellipse 55% 45% at 12% 8%,  @@ACENTO_LUZ_14@@, transparent 62%),
        radial-gradient(ellipse 45% 40% at 88% 88%, @@ACENTO_LUZ_09@@, transparent 60%),
        radial-gradient(ellipse 35% 35% at 70% 25%,
                        rgba(122, 162, 247, 0.07), transparent 60%),
        radial-gradient(ellipse 40% 30% at 30% 75%,
                        rgba(255, 255, 255, 0.025), transparent 60%),
        #0f1216;
    background-attachment: fixed;
}

/* Un panel de cristal: fondo apenas lechoso, desenfoque de lo que hay
   detrás, un borde fino que capta luz y una arista superior más clara,
   que es lo que hace que el ojo lea «vidrio» y no «caja gris». La clase
   `cristal` la pone el script a los contenedores con borde. */
.cristal,
[data-testid="stMetric"],
[data-testid="stExpander"] > details {
    background: rgba(255, 255, 255, 0.038) !important;
    -webkit-backdrop-filter: blur(18px) saturate(150%);
    backdrop-filter: blur(18px) saturate(150%);
    border: 1px solid rgba(255, 255, 255, 0.09) !important;
    box-shadow:
        inset 0 1px 0 rgba(255, 255, 255, 0.07),
        0 10px 36px rgba(0, 0, 0, 0.28);
}

[data-testid="stSidebar"] > div:first-child {
    background: rgba(9, 11, 14, 0.55);
    -webkit-backdrop-filter: blur(24px) saturate(140%);
    backdrop-filter: blur(24px) saturate(140%);
    border-right: 1px solid rgba(255, 255, 255, 0.06);
}

/* Los campos también son vidrio, un poco más hundido que los paneles. */
[data-baseweb="input"], [data-baseweb="textarea"], [data-baseweb="select"] > div,
.stNumberInput > div > div, .stDateInput > div > div, .stTimeInput > div > div {
    background: rgba(255, 255, 255, 0.03) !important;
    border-color: rgba(255, 255, 255, 0.10) !important;
}
[data-baseweb="input"]:focus-within, [data-baseweb="textarea"]:focus-within,
[data-baseweb="select"] > div:focus-within {
    border-color: @@ACENTO_55@@ !important;
    box-shadow: 0 0 0 1px @@ACENTO_35@@, 0 0 18px @@ACENTO_LUZ_20@@;
}

/* ═══════════════════════════════════════════════════════════
   Lienzo
   ═══════════════════════════════════════════════════════════ */

header[data-testid="stHeader"] {
    background: transparent;
    height: 2.5rem;
}
.stMainBlockContainer {
    padding-top: 2.5rem;
    padding-bottom: 4rem;
    max-width: 1400px;
}
.stMainBlockContainer h1 {
    letter-spacing: -0.02em;
    margin-bottom: 0.15rem;
}
.stMainBlockContainer h2,
.stMainBlockContainer h3 {
    letter-spacing: -0.01em;
}
.stMainBlockContainer h1 + div [data-testid="stCaptionContainer"] {
    max-width: 62ch;
}

/* ═══════════════════════════════════════════════════════════
   Tarjetas de métrica
   ═══════════════════════════════════════════════════════════ */

[data-testid="stMetric"] {
    padding: 0.9rem 1.1rem;
    transition: border-color 140ms ease, box-shadow 140ms ease;
}
[data-testid="stMetric"]:hover {
    border-color: @@ACENTO_45@@ !important;
    box-shadow:
        inset 0 1px 0 rgba(255, 255, 255, 0.09),
        0 0 22px @@ACENTO_LUZ_14@@;
}
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

/* ═══════════════════════════════════════════════════════════
   Pestañas
   ═══════════════════════════════════════════════════════════ */

.stTabs [data-baseweb="tab-list"] {
    gap: 1.6rem;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}
.stTabs [data-baseweb="tab"] {
    padding: 0.5rem 0;
    font-weight: 500;
}
.stTabs [data-baseweb="tab-highlight"] {
    height: 2px;
    box-shadow: 0 0 10px @@ACENTO_LUZ_70@@;
}

/* ═══════════════════════════════════════════════════════════
   Controles
   ═══════════════════════════════════════════════════════════ */

/* El botón primario es cristal iluminado desde dentro: el acento con
   algo de transparencia, texto oscuro encima, arista clara arriba y el
   halo alrededor. Es el único relleno fuerte de la pantalla. */
.stButton button[kind="primary"],
.stFormSubmitButton button[kind="primary"] {
    background: linear-gradient(135deg, @@ACENTO_92@@, @@ACENTO_78@@);
    color: #0f1216;
    font-weight: 650;
    border: 1px solid @@ACENTO_LUZ_55@@;
    box-shadow:
        inset 0 1px 0 rgba(255, 255, 255, 0.35),
        0 0 0 1px @@ACENTO_LUZ_20@@,
        0 4px 22px @@ACENTO_LUZ_28@@;
    transition: box-shadow 140ms ease, filter 140ms ease, transform 120ms ease;
}
.stButton button[kind="primary"]:hover,
.stFormSubmitButton button[kind="primary"]:hover {
    filter: brightness(1.06);
    box-shadow:
        inset 0 1px 0 rgba(255, 255, 255, 0.4),
        0 0 0 1px @@ACENTO_LUZ_45@@,
        0 6px 30px @@ACENTO_LUZ_45@@;
}
.stButton button[kind="primary"] p,
.stFormSubmitButton button[kind="primary"] p {
    color: #0f1216;
}

/* El secundario es cristal sin luz propia. */
.stButton button[kind="secondary"],
.stFormSubmitButton button[kind="secondary"] {
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(255, 255, 255, 0.12);
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.05);
    -webkit-backdrop-filter: blur(10px);
    backdrop-filter: blur(10px);
}
.stButton button[kind="secondary"]:hover,
.stFormSubmitButton button[kind="secondary"]:hover {
    border-color: @@ACENTO_45@@;
    background: rgba(255, 255, 255, 0.06);
}
.stButton button:active,
.stFormSubmitButton button:active {
    transform: translateY(1px);
}
.stButton button:focus-visible,
.stFormSubmitButton button:focus-visible {
    box-shadow: 0 0 0 2px @@ACENTO_55@@, 0 0 16px @@ACENTO_LUZ_28@@;
}
.stExpander summary:hover {
    color: @@ACENTO@@;
}

/* ═══════════════════════════════════════════════════════════
   Barra lateral
   ═══════════════════════════════════════════════════════════ */

[data-testid="stSidebarNav"] a {
    border-radius: 0.5rem;
}
[data-testid="stSidebarNav"] a[aria-current="page"] span {
    font-weight: 600;
}
[data-testid="stSidebarNav"] a[aria-current="page"] {
    background: @@ACENTO_12@@;
    box-shadow: inset 2px 0 0 @@ACENTO@@;
}

/* ═══════════════════════════════════════════════════════════
   Tablas
   ═══════════════════════════════════════════════════════════ */

.stDataFrame thead th {
    font-weight: 600;
    letter-spacing: 0.01em;
}
[data-testid="stDataFrame"], [data-testid="stDataEditor"] {
    border-radius: 0.7rem;
    overflow: hidden;
}

/* ═══════════════════════════════════════════════════════════
   Botones en grupo
   ═══════════════════════════════════════════════════════════ */

[data-testid="stHorizontalBlock"] .stButton,
[data-testid="stHorizontalBlock"] .stFormSubmitButton {
    flex: 1 1 auto;
}
[data-testid="stHorizontalBlock"] .stButton > button,
[data-testid="stHorizontalBlock"] .stFormSubmitButton > button {
    width: 100%;
}

/* ═══════════════════════════════════════════════════════════
   Teléfono
   ═══════════════════════════════════════════════════════════ */

@media (max-width: 640px) {
    input, select, textarea,
    .stTextInput input, .stNumberInput input, .stTextArea textarea,
    .stSelectbox [data-baseweb="select"] *,
    .stDateInput input, .stTimeInput input,
    [data-baseweb="input"] input, [data-baseweb="textarea"] textarea {
        font-size: 16px !important;
    }
    .stMainBlockContainer {
        padding-left: 0.9rem;
        padding-right: 0.9rem;
        padding-top: 1.2rem;
    }
    .stMainBlockContainer h1 { font-size: 1.45rem; }
    .stMainBlockContainer h2 { font-size: 1.15rem; }
    .stMainBlockContainer h3 { font-size: 1rem; }
    [data-testid="stHorizontalBlock"] > [data-testid="stMetric"],
    [data-testid="stHorizontalBlock"] > div:has(> [data-testid="stMetric"]) {
        flex: 1 1 calc(50% - 0.5rem);
        min-width: calc(50% - 0.5rem);
    }
    [data-testid="stMetricValue"] { font-size: 1.35rem; }
    [data-testid="stMetricLabel"] { font-size: 0.7rem; }
    .stButton > button, .stFormSubmitButton > button {
        min-height: 2.75rem;
        padding-top: 0.55rem;
        padding-bottom: 0.55rem;
    }
    .stButton > button[kind="primary"],
    .stFormSubmitButton > button[kind="primary"] {
        width: 100%;
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 1rem;
        overflow-x: auto;
        -webkit-overflow-scrolling: touch;
    }
    .stTabs [data-baseweb="tab"] { white-space: nowrap; }
    [data-testid="stSegmentedControl"] { overflow-x: auto; }
    [data-testid="stVerticalBlockBorderWrapper"] > div {
        padding: 0.75rem;
    }
    /* El desenfoque es caro en el teléfono: menos radio, mismo efecto. */
    .cristal, [data-testid="stMetric"], [data-testid="stExpander"] > details,
    [data-testid="stSidebar"] > div:first-child {
        -webkit-backdrop-filter: blur(12px) saturate(140%);
        backdrop-filter: blur(12px) saturate(140%);
    }
}
</style>
"""


def _con_alfa(color: str, porcentaje: int) -> str:
    """Devuelve el color en `rgba()` con la opacidad dada."""
    crudo = color.lstrip("#")
    r, g, b = (int(crudo[i : i + 2], 16) for i in (0, 2, 4))

    return f"rgba({r}, {g}, {b}, {porcentaje / 100:.2f})"


#: Reescribe la etiqueta viewport, que Streamlit pone y no expone.
#:
#: `maximum-scale=1` es lo que quita el zoom con los dedos. Corre en un
#: iframe del mismo origen, así que puede tocar el documento padre; se
#: anota en `dataset` para no repetirlo en cada rerun.
_VIEWPORT = """
<script>
(function () {
    try {
        var doc = window.parent.document;

        // Viewport sin zoom de dedos. Streamlit pone la etiqueta y no la
        // expone, así que se reescribe una sola vez.
        if (!doc.documentElement.dataset.viewportFijo) {
            var meta = doc.querySelector('meta[name="viewport"]');
            if (!meta) {
                meta = doc.createElement('meta');
                meta.name = 'viewport';
                doc.head.appendChild(meta);
            }
            meta.content = 'width=device-width, initial-scale=1, ' +
                           'maximum-scale=1, user-scalable=no, viewport-fit=cover';
            doc.documentElement.dataset.viewportFijo = '1';
        }

        // `st.container(border=True)` y el contenedor sin borde son el
        // mismo elemento en el HTML; sólo los distingue el estilo. Se
        // marcan como cristal los que tienen borde, y se vuelve a mirar
        // en cada cambio porque Streamlit reconstruye el árbol al rerun.
        function marcar() {
            var nodos = doc.querySelectorAll(
                '[data-testid="stVerticalBlockBorderWrapper"]:not(.cristal)'
            );
            for (var i = 0; i < nodos.length; i++) {
                var estilo = doc.defaultView.getComputedStyle(nodos[i]);
                if (parseFloat(estilo.borderTopWidth) > 0) {
                    nodos[i].classList.add('cristal');
                }
            }
        }
        marcar();
        if (!doc.documentElement.dataset.cristalObservado) {
            new doc.defaultView.MutationObserver(marcar)
                .observe(doc.body, { childList: true, subtree: true });
            doc.documentElement.dataset.cristalObservado = '1';
        }
    } catch (e) { /* fuera de un navegador no hay documento padre */ }
})();
</script>
"""


def aplicar_estilos() -> None:
    """
    Inyecta los estilos de la aplicación.

    Se llama una vez por rerun, antes de dibujar nada. El acento se
    sustituye aquí para que el halo siga al tema activo.
    """
    activa = paleta()
    hoja = _ESTILOS.replace("@@ACENTO@@", activa.acento)
    # Dos familias de marcador: el acento —relleno, bordes, texto— y su
    # luz —halos y manchas de fondo—, cada una con la opacidad que el CSS
    # pida. Los marcadores llevan dos cifras para que «ACENTO_14» no se
    # confunda con «ACENTO_1».
    for porcentaje in range(100, 0, -1):
        hoja = hoja.replace(
            f"@@ACENTO_LUZ_{porcentaje:02d}@@",
            _con_alfa(activa.acento_luz, porcentaje),
        )
        hoja = hoja.replace(
            f"@@ACENTO_{porcentaje:02d}@@", _con_alfa(activa.acento, porcentaje)
        )

    st.html(hoja)

    # `st.html` descarta los scripts; el viewport necesita uno, y el
    # componente sí lo ejecuta. Altura cero para que no deje hueco.
    components.html(_VIEWPORT, height=0)


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
