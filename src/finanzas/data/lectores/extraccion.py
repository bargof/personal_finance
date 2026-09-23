from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# Extracción de texto
#
# La parte frágil del asunto, aislada en un solo lugar.
#
# Las filas se reconstruyen agrupando palabras por su posición
# vertical en vez de confiar en el texto plano del PDF: una
# descripción con números o una celda que ocupa dos renglones
# rompen cualquier expresión regular sobre el texto corrido,
# pero no rompen la geometría de la página.
# ═══════════════════════════════════════════════════════════

#: Cuánto pueden separarse verticalmente dos palabras y seguir siendo la
#: misma fila. Tres puntos cubren el desalineado de las fuentes sin unir
#: renglones contiguos.
_TOLERANCIA_FILA = 3.0


@dataclass(frozen=True, slots=True)
class Palabra:
    """Una palabra del documento y el tramo horizontal que ocupa."""

    texto: str
    x0: float
    x1: float


class Fila(str):
    """
    Una fila del documento: su texto, con la posición de cada palabra.

    Es un `str` a propósito. Los lectores trabajan sobre texto y no tienen
    por qué enterarse de nada más, pero hay tablas —la de BBVA— donde el
    signo del importe no está escrito: lo dice la columna en que cae. Sin
    saber dónde cae cada número no se distingue un cargo de un abono, así
    que la posición viaja colgada de la línea para el que la necesite.
    """

    palabras: tuple[Palabra, ...]

    def __new__(cls, palabras: list[Palabra]) -> Fila:
        """Construye la fila con el texto que forman sus palabras."""
        fila = super().__new__(cls, " ".join(p.texto for p in palabras))
        fila.palabras = tuple(palabras)
        return fila


class PdfProtegidoError(ValueError):
    """El PDF pide contraseña."""


def extraer_lineas(
    origen: Path | str | bytes, contrasena: str | None = None
) -> list[str]:
    """
    Devuelve las líneas de un documento, sea PDF o texto.

    Parameters
    ----------
    origen : Path, str or bytes
        Ruta al archivo o su contenido en memoria.
    contrasena : str, optional
        Para los estados de cuenta que vienen cifrados.

    Returns
    -------
    list of str
        Una entrada por fila visual del documento.

    Raises
    ------
    PdfProtegidoError
        Si el PDF pide contraseña y no se dio la correcta.
    """
    datos = _bytes_de(origen)

    if not datos.lstrip().startswith(b"%PDF"):
        # Un CSV o un texto plano ya viene en líneas.
        return datos.decode("utf-8-sig", errors="replace").splitlines()

    return _lineas_del_pdf(datos, contrasena)


def _bytes_de(origen: Path | str | bytes) -> bytes:
    """Normaliza el origen a bytes."""
    if isinstance(origen, bytes):
        return origen

    return Path(origen).read_bytes()


def _lineas_del_pdf(datos: bytes, contrasena: str | None) -> list[str]:
    """Reconstruye las filas de un PDF a partir de la posición de cada palabra."""
    import io

    try:
        documento = pdfplumber.open(io.BytesIO(datos), password=contrasena or "")
    except Exception as error:  # pdfplumber envuelve el error de cifrado
        if "password" in str(error).lower() or "encrypt" in str(error).lower():
            raise PdfProtegidoError(
                "El PDF está protegido. Escribe la contraseña para abrirlo."
            ) from error
        raise

    lineas: list[str] = []
    with documento:
        for pagina in documento.pages:
            lineas.extend(_filas_de(pagina))

    logger.info("Extraídas %s líneas de %s páginas", len(lineas), len(documento.pages))
    return lineas


def _filas_de(pagina) -> list[str]:
    """
    Agrupa las palabras de una página en filas por su posición vertical.

    Dentro de cada fila las palabras van de izquierda a derecha, que es el
    orden en que se leen las columnas de la tabla.
    """
    palabras = pagina.extract_words(use_text_flow=False, keep_blank_chars=False)
    if not palabras:
        return []

    filas: list[list[dict]] = []
    for palabra in sorted(palabras, key=lambda p: (round(p["top"], 1), p["x0"])):
        if filas and abs(filas[-1][0]["top"] - palabra["top"]) <= _TOLERANCIA_FILA:
            filas[-1].append(palabra)
        else:
            filas.append([palabra])

    return [
        Fila(
            [
                Palabra(p["text"], p["x0"], p["x1"])
                for p in sorted(fila, key=lambda p: p["x0"])
            ]
        )
        for fila in filas
    ]
