from __future__ import annotations

import re

import pytest

from finanzas.application.app import theme

# ═══════════════════════════════════════════════════════════
# Tema
#
# El CSS se arma sustituyendo marcadores por el acento del
# tema activo. Si un marcador se queda sin sustituir, la regla
# sale con basura y el navegador la descarta sin avisar: no se
# rompe nada, sólo deja de verse. De ahí que valga fijarlo.
# ═══════════════════════════════════════════════════════════


def _hoja(paleta: theme.Paleta) -> str:
    """Devuelve la hoja de estilos ya resuelta para una paleta."""
    hoja = theme._ESTILOS.replace("@@ACENTO@@", paleta.acento)
    for porcentaje in range(100, 0, -1):
        hoja = hoja.replace(
            f"@@ACENTO_LUZ_{porcentaje:02d}@@",
            theme._con_alfa(paleta.acento_luz, porcentaje),
        )
        hoja = hoja.replace(
            f"@@ACENTO_{porcentaje:02d}@@", theme._con_alfa(paleta.acento, porcentaje)
        )

    return hoja


@pytest.mark.parametrize("paleta", [theme.OSCURA, theme.CLARA])
def test_no_queda_ningun_marcador_sin_sustituir(paleta):
    """Cada marcador del CSS tiene su valor en la sustitución."""
    assert "@@" not in _hoja(paleta)


def test_los_marcadores_del_css_llevan_dos_cifras():
    """
    «ACENTO_14» no debe poder confundirse con «ACENTO_1».

    Con opacidades de una cifra, sustituir «@@ACENTO_1@@» dentro de
    «@@ACENTO_14@@» dejaría basura; con dos cifras fijas no hay prefijo
    común.
    """
    usados = re.findall(r"@@ACENTO(?:_LUZ)?_(\d+)@@", theme._ESTILOS)

    assert usados, "el CSS no usa el acento"
    assert all(len(cifras) == 2 for cifras in usados)


def test_el_cristal_desenfoca_algo():
    """
    Sin manchas de luz en el fondo, el cristal es sólo un panel gris.

    El desenfoque necesita algo detrás; lo que hay detrás son los
    degradados del fondo, que usan la luz del acento.
    """
    assert "backdrop-filter" in theme._ESTILOS
    assert "radial-gradient" in theme._ESTILOS
    assert "@@ACENTO_LUZ_" in theme._ESTILOS


def test_el_acento_se_traduce_a_rgba():
    """El halo necesita opacidad, y un hexadecimal de seis cifras no la tiene."""
    assert theme._con_alfa("#8ad4bf", 45) == "rgba(138, 212, 191, 0.45)"
    assert theme._con_alfa("#8ad4bf", 100) == "rgba(138, 212, 191, 1.00)"


def test_cada_tema_trae_su_acento():
    """Las dos paletas definen el acento; ninguna hereda el de la otra."""
    assert theme.OSCURA.acento != theme.CLARA.acento
    for paleta in (theme.OSCURA, theme.CLARA):
        assert re.fullmatch(r"#[0-9a-f]{6}", paleta.acento)


def test_la_paleta_por_defecto_es_la_oscura():
    """
    Sin contexto de navegador, se asume oscuro.

    Es lo que ocurre en el primer render y en las pruebas, y el tema
    arranca en oscuro: equivocarse hacia el claro daría un destello.
    """
    assert theme.paleta() is theme.OSCURA


def test_el_acento_contrasta_contra_su_fondo():
    """
    Un neón sólo se lee como neón si tiene oscuridad alrededor.

    Se exige el mínimo de WCAG para componentes de interfaz (3:1), que es
    lo que aplica a un borde, un halo o el relleno de un botón.
    """

    def luminancia(color: str) -> float:
        crudo = color.lstrip("#")
        canales = [int(crudo[i : i + 2], 16) / 255 for i in (0, 2, 4)]
        lineal = [
            canal / 12.92 if canal <= 0.03928 else ((canal + 0.055) / 1.055) ** 2.4
            for canal in canales
        ]
        return 0.2126 * lineal[0] + 0.7152 * lineal[1] + 0.0722 * lineal[2]

    def contraste(uno: str, otro: str) -> float:
        claro, oscuro = sorted((luminancia(uno), luminancia(otro)), reverse=True)
        return (claro + 0.05) / (oscuro + 0.05)

    assert contraste(theme.OSCURA.acento, "#0f1216") >= 3.0
    assert contraste(theme.CLARA.acento, "#fcfcfb") >= 3.0
