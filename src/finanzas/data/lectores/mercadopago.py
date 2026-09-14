from __future__ import annotations

import csv
import io
import re
from datetime import date

from finanzas.data.lectores.base import (
    MESES_LARGOS,
    MovimientoImportado,
    ResultadoLectura,
    anio_del_periodo,
    es_negativo,
    parsear_fecha_numerica,
    parsear_monto,
)

# ═══════════════════════════════════════════════════════════
# Mercado Pago
#
# Dos documentos muy distintos: el estado de la tarjeta viene
# en PDF con fechas sin año, y el de la cuenta en CSV con
# folio por movimiento y saldo parcial línea a línea.
#
# El CSV es el caso cómodo: con folio, la deduplicación deja de
# ser heurística, y con saldos parciales el cuadre es exacto.
# ═══════════════════════════════════════════════════════════

_PAGOS = ("pago del resumen", "pago a tu tarjeta")


# ═══════════════════════════════════════════════════════════
# Tarjeta de crédito, en PDF
# «22/07 Compra en LIVERPOOL MITIKAH $ 24.00»
# ═══════════════════════════════════════════════════════════


class LectorMercadoPagoTarjeta:
    """Lee el estado de cuenta de la tarjeta de crédito de Mercado Pago."""

    nombre = "Mercado Pago (tarjeta)"

    _FILA = re.compile(r"^\s*(\d{1,2}/\d{1,2})\s+(.*?)\s+(-?\s*\$\s*[\d,]+\.?\d*)\s*$")

    def reconoce(self, texto: str) -> bool:
        """Lo delatan su marca y el encabezado de movimientos."""
        limpia = texto.lower()
        return "mercado" in limpia and "pago" in limpia and "movimientos" in limpia

    def leer(self, lineas: list[str]) -> ResultadoLectura:
        """Extrae los movimientos de la sección «Movimientos»."""
        texto = "\n".join(lineas)
        inicio, fin = self._periodo(texto)
        resultado = ResultadoLectura(
            banco=self.nombre, periodo_inicio=inicio, periodo_fin=fin
        )

        for numero, linea in enumerate(lineas, start=1):
            encontrado = self._FILA.match(linea)
            if encontrado is None:
                continue

            fecha_texto, descripcion, importe = encontrado.groups()
            descripcion = descripcion.strip()

            # El saldo de arranque es contexto, no un movimiento.
            if "saldo al corte" in descripcion.lower():
                resultado.saldo_inicial = parsear_monto(importe)
                continue

            mes = int(fecha_texto.split("/")[1])
            fecha = parsear_fecha_numerica(fecha_texto, anio_del_periodo(inicio, mes))
            monto = parsear_monto(importe)
            if fecha is None or monto is None or monto == 0:
                continue

            # «Compra en OXXO FUENTES» — el prefijo es de la plantilla.
            limpia = re.sub(r"^Compra en\s+", "", descripcion, flags=re.IGNORECASE)
            if not limpia:
                continue

            resultado.movimientos.append(
                MovimientoImportado(
                    fecha=fecha,
                    monto=monto,
                    descripcion_banco=limpia,
                    es_cargo=not es_negativo(importe),
                    es_pago_tarjeta=any(
                        marca in descripcion.lower() for marca in _PAGOS
                    ),
                    linea=linea.strip(),
                    pagina=numero,
                )
            )

        self._totales(lineas, resultado)
        return resultado

    def _periodo(self, texto: str) -> tuple:
        """
        Extrae «22 julio - 21 agosto» y le pone año.

        El periodo no dice el año, así que se toma el de la fecha de
        emisión que encabeza el documento.
        """
        anio = None
        emision = re.search(r"Fecha:\s*\d{1,2}\s+\w+\s+(\d{4})", texto)
        if emision is not None:
            anio = int(emision.group(1))

        rango = re.search(
            r"(\d{1,2})\s+([a-záéíóú]+)\s*[-–]\s*(\d{1,2})\s+([a-záéíóú]+)",
            texto.lower(),
        )
        if rango is None or anio is None:
            return None, None

        dia_i, mes_i, dia_f, mes_f = rango.groups()
        numero_i = MESES_LARGOS.get(mes_i)
        numero_f = MESES_LARGOS.get(mes_f)
        if numero_i is None or numero_f is None:
            return None, None

        try:
            # Un periodo que cruza el año arranca en el anterior.
            anio_inicio = anio - 1 if numero_i > numero_f else anio
            return (
                date(anio_inicio, numero_i, int(dia_i)),
                date(anio, numero_f, int(dia_f)),
            )
        except ValueError:
            return None, None

    def _totales(self, lineas: list[str], resultado: ResultadoLectura) -> None:
        """Lee el saldo declarado, para poder cuadrar."""
        for linea in lineas:
            limpia = linea.lower()
            if "saldo al corte del periodo anterior" in limpia:
                if resultado.saldo_inicial is None:
                    resultado.saldo_inicial = parsear_monto(linea)
            elif "saldo total del periodo" in limpia:
                resultado.saldo_final = parsear_monto(linea)


# ═══════════════════════════════════════════════════════════
# Cuenta, en CSV
# «01-06-2026;Monto retirado Ahorro;162018192302;200.00;200.00»
# ═══════════════════════════════════════════════════════════


class LectorMercadoPagoCuenta:
    """
    Lee el estado de cuenta de la cuenta de Mercado Pago, en CSV.

    Es el formato más confiable de los cuatro: trae folio por movimiento
    —con lo que la deduplicación es exacta— y saldos declarados arriba,
    con lo que el cuadre no depende de heurísticas.
    """

    nombre = "Mercado Pago (cuenta)"

    _ENCABEZADO = "RELEASE_DATE;TRANSACTION_TYPE"

    def reconoce(self, texto: str) -> bool:
        """El encabezado del CSV es inconfundible."""
        return self._ENCABEZADO in texto.upper()

    def leer(self, lineas: list[str]) -> ResultadoLectura:
        """Extrae los movimientos de las filas del CSV."""
        resultado = ResultadoLectura(banco=self.nombre)
        self._saldos(lineas, resultado)

        inicio = self._inicio_de_la_tabla(lineas)
        if inicio is None:
            resultado.avisos.append(
                "El archivo no trae la tabla de movimientos esperada."
            )
            return resultado

        cuerpo = "\n".join(lineas[inicio:])
        for numero, fila in enumerate(
            csv.DictReader(io.StringIO(cuerpo), delimiter=";"), start=1
        ):
            movimiento = self._fila(fila, numero)
            if movimiento is not None:
                resultado.movimientos.append(movimiento)

        if resultado.movimientos:
            fechas = [m.fecha for m in resultado.movimientos]
            resultado.periodo_inicio = min(fechas)
            resultado.periodo_fin = max(fechas)

        return resultado

    def _inicio_de_la_tabla(self, lineas: list[str]) -> int | None:
        """Localiza el encabezado real, que no es la primera línea."""
        for indice, linea in enumerate(lineas):
            if self._ENCABEZADO in linea.upper():
                return indice

        return None

    def _fila(self, fila: dict, numero: int) -> MovimientoImportado | None:
        """Convierte una fila del CSV en candidato, o None si no lo es."""
        fecha = parsear_fecha_numerica(fila.get("RELEASE_DATE", "") or "")
        crudo = (fila.get("TRANSACTION_NET_AMOUNT", "") or "").strip()
        monto = parsear_monto(crudo)
        if fecha is None or monto is None or monto == 0:
            return None

        concepto = (fila.get("TRANSACTION_TYPE", "") or "").strip()

        return MovimientoImportado(
            fecha=fecha,
            monto=monto,
            descripcion_banco=concepto,
            # En el CSV el signo va en el importe: negativo es salida.
            es_cargo=crudo.lstrip().startswith("-"),
            referencia=(fila.get("REFERENCE_ID", "") or "").strip(),
            es_pago_tarjeta=self._es_traspaso(concepto),
            linea=";".join(str(valor) for valor in fila.values()),
            pagina=numero,
        )

    def _es_traspaso(self, concepto: str) -> bool:
        """
        Indica si el movimiento sólo mueve dinero entre cuentas propias.

        Apartar o retirar del ahorro y transferirse a uno mismo no es
        gasto ni ingreso: es un traspaso, y contarlo como gasto inflaría
        el consumo del mes.
        """
        limpio = concepto.lower()
        marcas = (
            "monto apartado",
            "monto retirado",
            "transferencia enviada fernando",
            "transferencia recibida fernando",
        )
        return any(marca in limpio for marca in marcas)

    def _saldos(self, lineas: list[str], resultado: ResultadoLectura) -> None:
        """
        Lee la cabecera de saldos que precede a la tabla.

        «INITIAL_BALANCE;CREDITS;DEBITS;FINAL_BALANCE» y su fila de
        valores permiten cuadrar sin ambigüedad.
        """
        for indice, linea in enumerate(lineas):
            if "INITIAL_BALANCE" not in linea.upper():
                continue
            if indice + 1 >= len(lineas):
                return

            valores = lineas[indice + 1].split(";")
            if len(valores) < 4:
                return

            resultado.saldo_inicial = parsear_monto(valores[0])
            resultado.total_abonos = parsear_monto(valores[1])
            resultado.total_cargos = parsear_monto(valores[2])
            resultado.saldo_final = parsear_monto(valores[3])
            return
