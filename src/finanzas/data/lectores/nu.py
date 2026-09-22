from __future__ import annotations

import re

from finanzas.data.lectores.base import (
    MovimientoImportado,
    ResultadoLectura,
    anio_del_periodo,
    es_apartado,
    es_negativo,
    es_rendimiento,
    es_retiro,
    parsear_fecha_larga,
    parsear_monto,
)

# ═══════════════════════════════════════════════════════════
# Nu
#
# Hay dos formatos y conviven: el viejo trae una sola fecha y
# la categoría que Nu asignó; el nuevo, reformateado por
# regulación, trae fecha de operación y fecha de cargo pero
# perdió la categoría.
#
# Ambos parten el movimiento en varias líneas: la de importe y
# una o más de detalle («Tarjeta virtual ****», «Cambio (USD
# 1.00 = ...)», «Abono (con cuenta Nu)»). Esas continuaciones
# hay que saltarlas o se cuelan como movimientos fantasma.
# ═══════════════════════════════════════════════════════════

#: Conceptos que no son gasto: son el pago de la tarjeta desde otra cuenta
#: propia. Registrarlos como gasto contaría dos veces el consumo que ya se
#: contó al comprar.
_PAGOS = (
    "pago a tu tarjeta",
    "gracias por tu pago",
    "grácias por tu pago",
    "muchas gracias",
)

#: Líneas que continúan la anterior y no abren un movimiento.
_CONTINUACIONES = (
    "tarjeta virtual",
    "abono (con cuenta",
    "cambio (",
    "cambio(",
    "↳",
)

_CATEGORIAS_NU = (
    "Otros",
    "Electrónicos",
    "Ropa",
    "Transporte",
    "Restaurante",
    "Supermercado",
    "Educación",
    "Servicio",
    "Salud",
    "Entretenimiento",
    "Viajes",
    "Hogar",
    "Ajuste",
    "Devolución",
    "¡Muchas gracias!",
)


def _es_continuacion(linea: str) -> bool:
    """Indica si la línea sólo agrega detalle al movimiento anterior."""
    limpia = linea.strip().lower()
    return any(limpia.startswith(marca) for marca in _CONTINUACIONES)


def _es_pago(descripcion: str) -> bool:
    """Indica si el concepto es un pago a la tarjeta, no un consumo."""
    limpia = descripcion.lower()
    return any(marca in limpia for marca in _PAGOS)


def _periodo(texto: str) -> tuple:
    """Extrae el rango del periodo del encabezado del documento."""
    # El formato viejo separa con guión y el nuevo con «al».
    encontrado = re.search(
        r"(\d{1,2}\s+[A-ZÁÉÍÓÚ]{3}\s+\d{4})\s*(?:-|–|al)\s*"
        r"(\d{1,2}\s+[A-ZÁÉÍÓÚ]{3}\s+\d{4})",
        texto,
        re.IGNORECASE,
    )
    if encontrado is None:
        return None, None

    return (
        parsear_fecha_larga(encontrado.group(1)),
        parsear_fecha_larga(encontrado.group(2)),
    )


# ═══════════════════════════════════════════════════════════
# Formato anterior: «23 DIC Transporte Dlo*Didi Rides $26.20»
# ═══════════════════════════════════════════════════════════


class LectorNuClasico:
    """Lee el estado de cuenta de Nu con categoría y una sola fecha."""

    nombre = "Nu (formato con categoría)"

    #: Día, mes, y el resto de la fila. La categoría se separa después,
    #: porque no siempre está y a veces trae espacios.
    _FILA = re.compile(
        r"^\s*(\d{1,2})\s+([A-ZÁÉÍÓÚ]{3})\s+(.*?)\s+(-?\s*\$[\d,]+\.?\d*)\s*$"
    )

    def reconoce(self, texto: str) -> bool:
        """Reconoce el formato por su encabezado y la ausencia del nuevo."""
        tiene_nu = "nu méxico" in texto.lower() or "nu.com.mx" in texto.lower()
        tiene_tabla = "transacciones de" in texto.lower()
        return tiene_nu and tiene_tabla

    def leer(self, lineas: list[str]) -> ResultadoLectura:
        """Extrae los movimientos de la sección de transacciones."""
        texto = "\n".join(lineas)
        inicio, fin = _periodo(texto)
        resultado = ResultadoLectura(
            banco=self.nombre, periodo_inicio=inicio, periodo_fin=fin
        )

        for numero, linea in enumerate(lineas, start=1):
            if _es_continuacion(linea):
                continue

            encontrado = self._FILA.match(linea)
            if encontrado is None:
                continue

            dia, mes_texto, resto, importe = encontrado.groups()
            fecha = self._fecha(dia, mes_texto, inicio)
            monto = parsear_monto(importe)
            if fecha is None or monto is None or monto == 0:
                continue

            categoria, descripcion = self._partir(resto)
            if not descripcion:
                continue

            resultado.movimientos.append(
                MovimientoImportado(
                    fecha=fecha,
                    monto=monto,
                    descripcion_banco=descripcion,
                    es_cargo=not es_negativo(importe),
                    categoria_banco=categoria,
                    es_pago_tarjeta=_es_pago(descripcion) or _es_pago(categoria),
                    es_retiro_efectivo=es_retiro(descripcion),
                    es_apartado=es_apartado(descripcion),
                    es_rendimiento=es_rendimiento(descripcion),
                    linea=linea.strip(),
                    pagina=numero,
                )
            )

        self._totales(lineas, resultado)
        return resultado

    def _fecha(self, dia: str, mes_texto: str, inicio):
        """Resuelve el año, que la fila no trae."""
        from finanzas.data.lectores.base import MESES

        mes = MESES.get(mes_texto.upper())
        if mes is None:
            return None

        from datetime import date

        try:
            return date(anio_del_periodo(inicio, mes), mes, int(dia))
        except ValueError:
            return None

    def _partir(self, resto: str) -> tuple[str, str]:
        """
        Separa la categoría de Nu de la descripción del comercio.

        La categoría es un valor de un catálogo corto y conocido, así que
        se reconoce por prefijo en vez de adivinar dónde corta la columna.
        """
        limpio = resto.strip()
        for categoria in _CATEGORIAS_NU:
            if limpio.startswith(categoria):
                return categoria, limpio[len(categoria) :].strip()

        return "", limpio

    def _totales(self, lineas: list[str], resultado: ResultadoLectura) -> None:
        """Busca el saldo final declarado, para poder cuadrar."""
        for linea in lineas:
            limpia = linea.lower()
            if "saldo inicial del periodo" in limpia:
                resultado.saldo_inicial = parsear_monto(linea)
            elif "saldo total del periodo" in limpia and resultado.saldo_final is None:
                resultado.saldo_final = parsear_monto(linea)


# ═══════════════════════════════════════════════════════════
# Formato nuevo: dos fechas y signo explícito
# «22 MAY 2026 23 MAY 2026 Super Rappi | RFC: S.I. +$388.00»
# ═══════════════════════════════════════════════════════════


class LectorNuRegulado:
    """Lee el estado de cuenta de Nu con fecha de operación y de cargo."""

    nombre = "Nu (formato regulado)"

    _FILA = re.compile(
        r"^\s*(\d{1,2}\s+[A-ZÁÉÍÓÚ]{3}\s+\d{4})\s+"
        r"(\d{1,2}\s+[A-ZÁÉÍÓÚ]{3}\s+\d{4})\s+"
        r"(.*?)\s+([+-]\s*\$[\d,]+\.?\d*)\s*$",
        re.IGNORECASE,
    )

    def reconoce(self, texto: str) -> bool:
        """Lo delata su tabla, que el formato anterior no tenía."""
        limpia = texto.lower()
        return "cargos, abonos y compras regulares" in limpia

    def leer(self, lineas: list[str]) -> ResultadoLectura:
        """Extrae los movimientos de la tabla de cargos y abonos."""
        texto = "\n".join(lineas)
        inicio, fin = _periodo(texto)
        resultado = ResultadoLectura(
            banco=self.nombre, periodo_inicio=inicio, periodo_fin=fin
        )

        for numero, linea in enumerate(lineas, start=1):
            if _es_continuacion(linea):
                continue

            encontrado = self._FILA.match(linea)
            if encontrado is None:
                continue

            operacion, cargo, descripcion, importe = encontrado.groups()
            fecha = parsear_fecha_larga(operacion)
            monto = parsear_monto(importe)
            if fecha is None or monto is None or monto == 0:
                continue

            # «Super Rappi | RFC: S.I.» — el RFC no aporta y estorba al leer.
            descripcion = descripcion.split("|")[0].strip()
            if not descripcion:
                continue

            resultado.movimientos.append(
                MovimientoImportado(
                    fecha=fecha,
                    fecha_cargo=parsear_fecha_larga(cargo),
                    monto=monto,
                    descripcion_banco=descripcion,
                    es_cargo=not importe.strip().startswith("-"),
                    es_pago_tarjeta=_es_pago(descripcion),
                    es_retiro_efectivo=es_retiro(descripcion),
                    es_apartado=es_apartado(descripcion),
                    es_rendimiento=es_rendimiento(descripcion),
                    linea=linea.strip(),
                    pagina=numero,
                )
            )

        self._totales(lineas, resultado)
        return resultado

    def _totales(self, lineas: list[str], resultado: ResultadoLectura) -> None:
        """Lee los totales declarados al pie de la tabla."""
        for linea in lineas:
            limpia = linea.lower()
            if "total de cargos" in limpia:
                resultado.total_cargos = parsear_monto(linea)
            elif "total de abonos" in limpia:
                resultado.total_abonos = parsear_monto(linea)
