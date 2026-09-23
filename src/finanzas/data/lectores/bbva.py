from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date

from finanzas.data.lectores.base import (
    MESES,
    MovimientoImportado,
    ResultadoLectura,
    anio_del_periodo,
    es_apartado,
    es_pago_de_tarjeta,
    es_rendimiento,
    es_retiro,
    parsear_fecha_numerica,
)

# ═══════════════════════════════════════════════════════════
# BBVA, cuenta de débito
#
# El documento más completo de los que se leen: fecha de
# operación y de liquidación, folio por movimiento, saldo
# anterior y final, y los totales de cargos y abonos con su
# número de movimientos, así que se puede cuadrar por importe
# y también por conteo.
#
# Y el más incómodo: el signo no está escrito. «PAGO CUENTA DE
# TERCERO» es el mismo concepto para el dinero que entra y para
# el que sale —en un mismo periodo aparece de las dos formas— y
# lo único que los distingue es en qué columna cae el importe,
# CARGOS o ABONOS. Por eso el lector mira la posición de cada
# palabra y no sólo el texto; si el documento llega sin
# posiciones deduce el signo del concepto y lo avisa, que es
# peor pero es algo.
#
# Cada movimiento ocupa varias líneas: la primera trae fechas,
# concepto e importes; las de abajo, el folio y el detalle
# —CLABE, clave de rastreo, nombre del otro extremo—. Esas
# continuaciones empiezan en la columna de la descripción, que
# es como se distinguen del pie de página que se cuela entre
# ellas cada vez que cambia de hoja.
# ═══════════════════════════════════════════════════════════

#: Dónde empieza y dónde termina la tabla de movimientos.
_INICIO_TABLA = "detalle de movimientos realizados"
_FIN_TABLA = "total de movimientos"

#: «08/AGO 10/AGO SPEI ENVIADO Mercado Pago 427.50»: las dos fechas
#: abren fila, y nada más las abre.
_FILA = re.compile(r"^(\d{1,2})/([A-ZÁÉÍÓÚ]{3})\s+(\d{1,2})/([A-ZÁÉÍÓÚ]{3})\s+(\S.*)$")

#: Un importe de la tabla. Siempre lleva los dos decimales, que es lo que
#: lo distingue de los números sueltos del concepto.
_IMPORTE = re.compile(r"^-?\d[\d,]*\.\d{2}$")

#: Concepto e importes cuando hay que separarlos por el texto. Los
#: importes van al final: el primero es el del movimiento y los que
#: siguen, si los hay, son los saldos de la fila.
_FILA_TEXTO = re.compile(r"^(.*?)\s+((?:-?\d[\d,]*\.\d{2})(?:\s+-?\d[\d,]*\.\d{2})*)$")

#: Conceptos con los que BBVA nombra el dinero que entra. Sólo se usan
#: cuando no se ven las columnas: son una aproximación, no la verdad.
_CONCEPTOS_ABONO = (
    "spei recibido",
    "deposito",
    "depósito",
    "pago de nomina",
    "pago de nómina",
    "abono",
    "bonificacion",
    "bonificación",
    "devolucion",
    "devolución",
    "intereses",
    "rendimiento",
)

#: Encabezado y pie de página, que aparecen en medio de la tabla cada vez
#: que el documento cambia de hoja.
_FUERA_DE_TABLA = (
    "estado de cuenta",
    "libretón",
    "libreton",
    "pagina",
    "página",
    "no. de cuenta",
    "no. de cliente",
    "bbva mexico",
    "av. paseo",
    "la gat real",
    "le informamos",
    "detalle de movimientos",
)

#: A partir de cuántos dígitos un dato es CLABE o clave de rastreo y no
#: dice nada: «00722969010664871550», «MBAN01002608100079724199». Se
#: cuentan los dígitos y no el largo porque el concepto que escribe quien
#: envía viene pegado a una fecha —«0260902Colegiatura»— y ahí sí hay algo
#: que leer.
_DIGITOS_DE_RASTREO = 12


@dataclass(frozen=True, slots=True)
class _Columnas:
    """
    Dónde cae cada columna de la tabla, en puntos de la página.

    Sale del propio encabezado del documento en vez de ir fija: si BBVA
    mueve la tabla, el encabezado se mueve con ella.
    """

    #: Donde empieza la descripción: por ahí empiezan también las líneas
    #: de detalle, y es lo que las separa del pie de página.
    descripcion: float

    #: Donde empieza la referencia, que es donde termina la descripción.
    referencia: float

    #: Donde terminan las columnas de importes. Los números van alineados
    #: a la derecha, así que es su borde derecho el que dice de cuál son.
    cargos: float
    abonos: float

    #: Cuánto puede desviarse un importe del borde de su columna. Lo
    #: visto son siete puntos; veinte deja margen sin llegar a la mitad
    #: de la distancia entre una columna y la otra.
    TOLERANCIA = 20.0

    def es_cargo(self, x1: float) -> bool | None:
        """
        Dice si un importe cayó en CARGOS, en ABONOS o en ninguna.

        Devuelve None para los saldos de operación y liquidación, que
        viven más a la derecha y no son el importe del movimiento.
        """
        a_cargos, a_abonos = abs(x1 - self.cargos), abs(x1 - self.abonos)
        if min(a_cargos, a_abonos) > self.TOLERANCIA:
            return None

        return a_cargos <= a_abonos


class LectorBBVACuenta:
    """Lee el estado de cuenta de la cuenta de débito de BBVA."""

    nombre = "BBVA (cuenta)"

    def reconoce(self, texto: str) -> bool:
        """Lo delatan la marca y el título de su tabla de movimientos."""
        limpia = texto.lower()
        return "bbva" in limpia and _INICIO_TABLA in limpia

    def leer(self, lineas: list[str]) -> ResultadoLectura:
        """Extrae los movimientos del detalle y los saldos del resumen."""
        texto = "\n".join(lineas)
        inicio, fin = _periodo(texto)
        resultado = ResultadoLectura(
            banco=self.nombre, periodo_inicio=inicio, periodo_fin=fin
        )

        columnas = _columnas(lineas)
        if columnas is None:
            resultado.avisos.append(
                "No pude ver las columnas del documento, así que deduje de cada "
                "concepto si era cargo o abono. Revisa el tipo de cada movimiento "
                "antes de guardar."
            )

        for numero, (fila, detalle) in enumerate(_bloques(lineas, columnas), start=1):
            movimiento = _movimiento(fila, detalle, inicio, columnas, numero)
            if movimiento is not None:
                resultado.movimientos.append(movimiento)

        _soltar_folios_repetidos(resultado.movimientos)
        _totales(texto, resultado)
        _contrastar_conteos(texto, resultado)
        _avisar_de_lo_que_no_es_movimiento(texto, resultado)

        return resultado


# ═══════════════════════════════════════════════════════════
# La tabla
# ═══════════════════════════════════════════════════════════


def _columnas(lineas: list[str]) -> _Columnas | None:
    """
    Lee del encabezado dónde cae cada columna.

    Devuelve None si el documento llegó sin posiciones —texto pegado, un
    PDF sin capa de texto— o si no trae el encabezado.
    """
    for linea in lineas:
        palabras = {p.texto.upper(): p for p in getattr(linea, "palabras", ())}
        if not {"DESCRIPCION", "REFERENCIA", "CARGOS", "ABONOS"} <= palabras.keys():
            continue

        return _Columnas(
            descripcion=palabras["DESCRIPCION"].x0,
            referencia=palabras["REFERENCIA"].x0,
            cargos=palabras["CARGOS"].x1,
            abonos=palabras["ABONOS"].x1,
        )

    return None


def _bloques(
    lineas: list[str], columnas: _Columnas | None
) -> Iterator[tuple[str, list[str]]]:
    """
    Agrupa cada movimiento con las líneas de detalle que lo siguen.

    Rinde pares (fila, detalle). Sólo mira entre el título de la tabla y
    el total de movimientos: fuera de ahí hay cifras que no son
    movimientos y que se colarían como tales.
    """
    dentro = False
    actual: tuple[str, list[str]] | None = None

    for linea in lineas:
        limpia = linea.strip()
        baja = limpia.lower()

        if not dentro:
            dentro = _INICIO_TABLA in baja
            continue

        if baja.startswith(_FIN_TABLA):
            break

        if _FILA.match(limpia):
            if actual is not None:
                yield actual
            actual = (linea, [])
        elif actual is not None and _es_detalle(linea, columnas):
            actual[1].append(limpia)

    if actual is not None:
        yield actual


def _es_detalle(linea: str, columnas: _Columnas | None) -> bool:
    """
    Indica si la línea continúa el movimiento anterior.

    Con posiciones basta mirar dónde empieza: el detalle arranca en la
    columna de la descripción o en la de la referencia. El pie de página
    queda en el margen y el encabezado de la hoja siguiente, pegado a la
    derecha, así que los dos caen fuera de esa banda. Sin posiciones hay
    que nombrar lo que no es tabla.
    """
    palabras = getattr(linea, "palabras", ())
    if columnas is not None and palabras:
        inicio = palabras[0].x0
        return columnas.descripcion - 5 <= inicio <= columnas.referencia + 5

    baja = linea.strip().lower()
    return bool(baja) and not any(baja.startswith(m) for m in _FUERA_DE_TABLA)


def _movimiento(
    fila: str,
    detalle: list[str],
    inicio: date | None,
    columnas: _Columnas | None,
    numero: int,
) -> MovimientoImportado | None:
    """Convierte un bloque del documento en candidato, o None si no lo es."""
    encontrado = _FILA.match(fila.strip())
    if encontrado is None:
        return None

    dia_oper, mes_oper, dia_liq, mes_liq, resto = encontrado.groups()
    fecha = _fecha(dia_oper, mes_oper, inicio)
    if fecha is None:
        return None

    partes = _concepto_e_importe(fila, resto, columnas)
    if partes is None:
        return None

    concepto, monto, es_cargo = partes
    if monto == 0 or not concepto:
        return None

    texto = " ".join([concepto, *detalle])
    extra = _detalle_util(detalle)

    return MovimientoImportado(
        fecha=fecha,
        fecha_cargo=_fecha(dia_liq, mes_liq, inicio),
        monto=monto,
        descripcion_banco=f"{concepto} · {extra}" if extra else concepto,
        es_cargo=es_cargo,
        referencia=_folio(detalle),
        es_pago_tarjeta=es_pago_de_tarjeta(concepto),
        es_retiro_efectivo=es_retiro(concepto),
        es_apartado=es_apartado(concepto),
        es_traspaso_propio=_es_cuenta_propia(texto),
        es_rendimiento=es_rendimiento(concepto),
        linea=fila.strip(),
        pagina=numero,
    )


def _concepto_e_importe(
    fila: str, resto: str, columnas: _Columnas | None
) -> tuple[str, float, bool] | None:
    """
    Separa el concepto del importe y dice si es cargo o abono.

    Con las columnas a la vista el signo es un hecho: el importe está en
    CARGOS o está en ABONOS. Sin ellas hay que deducirlo del concepto, y
    «PAGO CUENTA DE TERCERO» sirve para las dos cosas, así que el lector
    ya avisó de que esto es una aproximación.
    """
    palabras = getattr(fila, "palabras", ())
    if columnas is not None and palabras:
        # Las dos primeras palabras son las fechas; la descripción llega
        # hasta donde empieza la referencia.
        concepto = " ".join(
            p.texto for p in palabras[2:] if p.x1 <= columnas.referencia
        )
        for palabra in palabras:
            if not _IMPORTE.match(palabra.texto):
                continue
            es_cargo = columnas.es_cargo(palabra.x1)
            if es_cargo is not None:
                return _limpiar(concepto), _numero(palabra.texto), es_cargo

        return None

    encontrado = _FILA_TEXTO.match(resto.strip())
    if encontrado is None:
        return None

    concepto = _limpiar(encontrado.group(1))
    # El primero de los importes es el del movimiento; los demás, saldos.
    monto = _numero(encontrado.group(2).split()[0])
    entra = any(marca in concepto.lower() for marca in _CONCEPTOS_ABONO)

    return concepto, monto, not entra


def _limpiar(concepto: str) -> str:
    """
    Despega lo que el PDF dejó pegado.

    «SPEI RECIBIDOBANORTE» y «SPEI RECIBIDOMercado Pago» salen así del
    documento; separarlos importa porque la descripción del banco es con
    lo que después se reconocen los comercios que se repiten.
    """
    return re.sub(r"\bRECIBIDO(?=\S)", "RECIBIDO ", concepto.strip())


# ═══════════════════════════════════════════════════════════
# El detalle: folio, contraparte y ruido
# ═══════════════════════════════════════════════════════════

#: «Referencia 0079724199 722»: el folio es lo que sigue a la palabra.
_REFERENCIA = re.compile(r"\breferencia\b\s*(\S+)?", re.IGNORECASE)


def _folio(detalle: list[str]) -> str:
    """
    Devuelve el folio del banco, si la línea de referencia trae uno.

    Se queda con lo que sigue a «Referencia» y nada más: en la nómina
    viene «Referencia BC 4201118692», donde «BC» no es folio, y el
    retiro trae la tarjeta enmascarada, que se repite en todos. Un folio
    que no identifica el movimiento es peor que ninguno: haría que el
    siguiente igual pareciera repetido.
    """
    for linea in detalle:
        encontrado = _REFERENCIA.search(linea)
        if encontrado is None:
            continue

        folio = (encontrado.group(1) or "").strip()
        if len(folio) >= 6 and "*" not in folio and any(c.isdigit() for c in folio):
            return folio

    return ""


def _detalle_util(detalle: list[str]) -> str:
    """
    Junta lo que el detalle dice y se puede leer.

    De cada línea se queda con lo anterior a «Referencia» —ahí va el
    concepto que escribió quien envió y el nombre del otro extremo— y
    tira las CLABE y las claves de rastreo, que ocupan tres renglones y
    no dicen nada a quien revisa.
    """
    palabras: list[str] = []
    for linea in detalle:
        antes = _REFERENCIA.split(linea)[0]
        palabras.extend(
            palabra
            for palabra in antes.split()
            if sum(c.isdigit() for c in palabra) < _DIGITOS_DE_RASTREO
        )

    return " ".join(palabras)


def _es_cuenta_propia(texto: str) -> bool:
    """
    Indica si el otro extremo de la transferencia es uno mismo.

    BBVA pone el nombre de la contraparte al pie del movimiento; cuando
    es el del titular, el dinero sólo cambió de banco. Viene de la
    configuración (`TITULAR`) para no atar el lector a una persona.
    """
    from finanzas.config.settings import settings

    titular = settings.titular.strip().lower()
    return bool(titular) and titular in texto.lower()


def _soltar_folios_repetidos(movimientos: list[MovimientoImportado]) -> None:
    """
    Quita los folios que el documento repite.

    Dos movimientos distintos no pueden compartir folio: si lo comparten
    —la nómina quincenal trae la misma referencia del patrón— es que ese
    número no identifica al movimiento, y dejarlo haría que el segundo
    pareciera el primero ya registrado.
    """
    vistos: dict[str, int] = {}
    for movimiento in movimientos:
        if movimiento.referencia:
            vistos[movimiento.referencia] = vistos.get(movimiento.referencia, 0) + 1

    for movimiento in movimientos:
        if vistos.get(movimiento.referencia, 0) > 1:
            movimiento.referencia = ""


# ═══════════════════════════════════════════════════════════
# El resumen: periodo, saldos y totales
# ═══════════════════════════════════════════════════════════

_PERIODO = re.compile(
    r"periodo\s+del\s+(\d{1,2}/\d{1,2}/\d{4})\s+al\s+(\d{1,2}/\d{1,2}/\d{4})",
    re.IGNORECASE,
)


def _periodo(texto: str) -> tuple[date | None, date | None]:
    """Extrae «Periodo DEL 07/08/2026 AL 06/09/2026»."""
    encontrado = _PERIODO.search(texto)
    if encontrado is None:
        return None, None

    return (
        parsear_fecha_numerica(encontrado.group(1)),
        parsear_fecha_numerica(encontrado.group(2)),
    )


def _fecha(dia: str, mes_texto: str, inicio: date | None) -> date | None:
    """Convierte «08/AGO» resolviendo el año, que la fila no trae."""
    mes = MESES.get(mes_texto.upper())
    if mes is None:
        return None

    try:
        return date(anio_del_periodo(inicio, mes), mes, int(dia))
    except ValueError:
        return None


def _numero(texto: str) -> float:
    """Convierte «16,357.57» en número."""
    return abs(float(texto.replace(",", "")))


def _numero_tras(texto: str, etiqueta: str) -> float | None:
    """
    Devuelve la cifra que sigue a una etiqueta del resumen.

    Hace falta apuntar a la etiqueta y no a la línea: el resumen va en
    dos columnas y al extraerlo quedan pegadas —«Saldo Promedio 3,530.94
    Saldo Anterior 7.50»—, así que leer «el número de la línea» daría el
    de la otra columna.
    """
    encontrado = re.search(
        rf"{re.escape(etiqueta)}\s*:?\s*\$?\s*(-?\d[\d,]*\.\d{{2}})",
        texto,
        re.IGNORECASE,
    )
    return None if encontrado is None else float(encontrado.group(1).replace(",", ""))


def _entero_tras(texto: str, etiqueta: str) -> int | None:
    """Devuelve el conteo que sigue a una etiqueta del resumen."""
    encontrado = re.search(rf"{re.escape(etiqueta)}\s+(\d+)", texto, re.IGNORECASE)
    return None if encontrado is None else int(encontrado.group(1))


def _totales(texto: str, resultado: ResultadoLectura) -> None:
    """Lee los saldos y los totales declarados, para poder cuadrar."""
    resultado.saldo_inicial = _numero_tras(texto, "Saldo Anterior")
    resultado.saldo_final = _numero_tras(texto, "Saldo Final")
    resultado.total_cargos = _numero_tras(texto, "TOTAL IMPORTE CARGOS")
    resultado.total_abonos = _numero_tras(texto, "TOTAL IMPORTE ABONOS")


def _contrastar_conteos(texto: str, resultado: ResultadoLectura) -> None:
    """
    Compara cuántos movimientos se leyeron contra los que declara.

    El cuadre por importe no lo ve todo: dos filas perdidas que se
    compensan cuadran igual. El conteo sí las ve.
    """
    leidos = {
        "CARGOS": sum(1 for m in resultado.movimientos if m.es_cargo),
        "ABONOS": sum(1 for m in resultado.movimientos if not m.es_cargo),
    }
    for columna, leido in leidos.items():
        declarado = _entero_tras(texto, f"TOTAL MOVIMIENTOS {columna}")
        if declarado is not None and declarado != leido:
            resultado.avisos.append(
                f"El documento declara {declarado} {columna.lower()} y leí {leido}. "
                "Revisa la tabla antes de continuar."
            )


def _avisar_de_lo_que_no_es_movimiento(texto: str, resultado: ResultadoLectura) -> None:
    """
    Señala lo que el resumen declara y la tabla no siempre trae.

    Comisiones e intereses del periodo salen en el resumen aunque no
    aparezcan como fila; el saldo de los apartados es de otra cuenta, no
    de ésta. Nada de esto se inventa como movimiento: se dice, y el
    usuario decide.
    """
    comisiones = _numero_tras(texto, "Total Comisiones")
    if comisiones:
        resultado.avisos.append(
            f"El documento declara ${comisiones:,.2f} de comisiones en el periodo; "
            "revisa que estén entre los movimientos."
        )

    intereses = _numero_tras(texto, "Intereses a Favor (+)")
    if intereses:
        resultado.avisos.append(
            f"El documento declara ${intereses:,.2f} de intereses a favor; "
            "revisa que estén entre los movimientos."
        )

    apartados = _numero_tras(texto, "Saldo Global")
    if apartados:
        cuantos = _entero_tras(texto, "Total de Apartados") or 0
        resultado.avisos.append(
            f"Los {cuantos} apartados cerraron con ${apartados:,.2f}. Ese dinero "
            "no está en el saldo de esta cuenta: va en la cuenta del apartado."
        )
