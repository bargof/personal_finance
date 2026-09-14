"""
Genera el favicon de la aplicación.

Se ejecuta a mano cuando cambia la identidad visual; el PNG resultante se
versiona junto al código para que arrancar la app no dependa de generarlo.

    poetry run python scripts/generar_favicon.py

Lleva el acento vino de la aplicación, pero invertido respecto a la
interfaz: aquí el vino es el fondo y el signo va en blanco. A dieciséis
píxeles gana la masa de color, y un símbolo fino sobre carbón se pierde
—más aún en una pestaña de navegador oscura, donde el fondo del icono se
confunde con el del navegador—. Por lo mismo no lleva halo ni borde: a
ese tamaño sólo restarían píxeles al signo.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

LIENZO = 512
DESTINO = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "finanzas"
    / "application"
    / "app"
    / "assets"
    / "favicon.png"
)

#: El acento de la aplicación, que aquí hace de fondo.
VINO = (212, 11, 69, 255)
BLANCO = (255, 255, 255, 255)

#: Fuentes con las que el signo de peso se ve bien de bold. Se prueba en
#: orden y se usa la primera que exista; si no hay ninguna, el signo se
#: dibuja a mano.
FUENTES = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/SFNSRounded.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


def _fuente(tamano: int) -> ImageFont.FreeTypeFont | None:
    """Devuelve la primera fuente disponible, o None si no hay ninguna."""
    for ruta in FUENTES:
        if Path(ruta).exists():
            try:
                return ImageFont.truetype(ruta, tamano)
            except OSError:
                continue

    return None


def dibujar() -> Image.Image:
    """Compone el favicon."""
    fondo = Image.new("RGBA", (LIENZO, LIENZO), (0, 0, 0, 0))
    pincel = ImageDraw.Draw(fondo)

    # Un cuadrado de esquinas suaves, como los contenedores de la interfaz,
    # con el margen justo para que se note el redondeo y nada más.
    margen = LIENZO // 28
    pincel.rounded_rectangle(
        [margen, margen, LIENZO - margen, LIENZO - margen],
        radius=LIENZO // 6,
        fill=VINO,
    )

    simbolo = Image.new("RGBA", (LIENZO, LIENZO), (0, 0, 0, 0))
    trazo = ImageDraw.Draw(simbolo)

    fuente = _fuente(int(LIENZO * 0.72))
    if fuente is not None:
        # `anchor="mm"` centra por la caja real del glifo, que no coincide
        # con el centro geométrico por culpa de las astas del signo.
        trazo.text(
            (LIENZO // 2, LIENZO // 2 - LIENZO // 40),
            "$",
            font=fuente,
            fill=BLANCO,
            anchor="mm",
        )
    else:
        _signo_a_mano(trazo)

    return Image.alpha_composite(fondo, simbolo)


def _signo_a_mano(trazo: ImageDraw.ImageDraw) -> None:
    """
    Dibuja el signo de peso sin depender de una fuente.

    Es el respaldo para cuando el script corre donde no hay las fuentes
    del sistema; queda más geométrico pero igual de legible en pequeño.
    """
    centro = LIENZO // 2
    grosor = LIENZO // 12
    alto = LIENZO // 3
    ancho = LIENZO // 5

    # Las dos curvas de la S, como arcos gruesos enfrentados.
    trazo.arc(
        [centro - ancho, centro - alto, centro + ancho, centro],
        start=20,
        end=200,
        fill=BLANCO,
        width=grosor,
    )
    trazo.arc(
        [centro - ancho, centro, centro + ancho, centro + alto],
        start=200,
        end=20,
        fill=BLANCO,
        width=grosor,
    )
    # El asta vertical que lo convierte en signo de moneda.
    trazo.line(
        [(centro, centro - alto - grosor), (centro, centro + alto + grosor)],
        fill=BLANCO,
        width=grosor // 2,
    )


def main() -> int:
    """Genera el favicon y lo deja en el paquete de la aplicación."""
    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    imagen = dibujar()
    imagen.save(DESTINO, "PNG")

    print(f"Favicon generado en {DESTINO}")
    print(f"  {imagen.size[0]}×{imagen.size[1]} px · {DESTINO.stat().st_size} bytes")

    return 0


if __name__ == "__main__":
    sys.exit(main())
