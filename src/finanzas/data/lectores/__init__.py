from __future__ import annotations

import logging

from finanzas.data.lectores.base import (
    LectorBanco,
    MovimientoImportado,
    ResultadoLectura,
)
from finanzas.data.lectores.mercadopago import (
    LectorMercadoPagoCuenta,
    LectorMercadoPagoTarjeta,
)
from finanzas.data.lectores.nu import LectorNuClasico, LectorNuRegulado

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# Registro de lectores
#
# El orden importa: el primero que reconozca el documento se
# lo queda, así que los formatos más específicos van antes.
# Nu regulado va delante del clásico porque ambos dicen «Nu» y
# sólo el nuevo tiene su tabla característica.
# ═══════════════════════════════════════════════════════════

REGISTRO: tuple[LectorBanco, ...] = (
    LectorMercadoPagoCuenta(),
    LectorNuRegulado(),
    LectorNuClasico(),
    LectorMercadoPagoTarjeta(),
)


class DocumentoNoReconocidoError(ValueError):
    """Ningún lector reconoció el formato del documento."""


def detectar(texto: str) -> LectorBanco:
    """
    Devuelve el lector que reconoce el documento.

    Raises
    ------
    DocumentoNoReconocidoError
        Si ninguno lo reconoce. El mensaje nombra los formatos que sí se
        soportan, que es lo accionable para quien sube el archivo.
    """
    for lector in REGISTRO:
        if lector.reconoce(texto):
            logger.info("Documento reconocido como %s", lector.nombre)
            return lector

    soportados = ", ".join(lector.nombre for lector in REGISTRO)
    raise DocumentoNoReconocidoError(
        f"No reconozco el formato de este documento. Por ahora leo: {soportados}."
    )


def leer(lineas: list[str]) -> ResultadoLectura:
    """Detecta el formato y extrae los movimientos."""
    return detectar("\n".join(lineas)).leer(lineas)


__all__ = [
    "REGISTRO",
    "DocumentoNoReconocidoError",
    "LectorBanco",
    "MovimientoImportado",
    "ResultadoLectura",
    "detectar",
    "leer",
]
