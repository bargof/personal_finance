from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, runtime_checkable

# ═══════════════════════════════════════════════════════════
# Lectura de estados de cuenta
#
# El parser trabaja sobre líneas de texto, no sobre el PDF.
# Separarlos es lo que permite probarlo con las líneas reales
# de un estado de cuenta sin arrastrar el archivo, y deja la
# extracción —que es la parte frágil— en un solo lugar.
#
# Nada de esto toca la base: un lector devuelve candidatos que
# el usuario revisa y completa antes de que se guarden.
# ═══════════════════════════════════════════════════════════

MESES = {
    "ENE": 1,
    "FEB": 2,
    "MAR": 3,
    "ABR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AGO": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DIC": 12,
}

MESES_LARGOS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}


@dataclass(slots=True)
class MovimientoImportado:
    """
    Una línea de estado de cuenta, antes de volverse movimiento.

    Trae lo que el banco sabe —fecha, monto, su propia descripción— y deja
    vacío lo que sólo el usuario puede decir: categoría, necesidad, nota.
    `descripcion_banco` se conserva aparte de la descripción propia porque
    sirve para otra cosa: casar el registro con su línea del documento y
    aprender a clasificar comercios que se repiten.
    """

    fecha: date
    monto: float
    descripcion_banco: str
    es_cargo: bool = True

    #: Fecha en que el banco aplicó el cargo, cuando la reporta aparte.
    fecha_cargo: date | None = None

    #: Folio del banco. Cuando existe, la deduplicación deja de ser
    #: heurística: el mismo folio es el mismo movimiento.
    referencia: str = ""

    #: Categoría que el propio banco asignó, si la trae.
    categoria_banco: str = ""

    #: Marca los movimientos que no son gasto ni ingreso: el pago de la
    #: tarjeta es un traspaso desde otra cuenta propia, y registrarlo como
    #: gasto contaría dos veces lo que ya se contó al comprar.
    es_pago_tarjeta: bool = False

    pagina: int = 0
    linea: str = ""

    @property
    def fecha_operacion(self) -> date:
        """Fecha en que ocurrió el movimiento."""
        return self.fecha

    @property
    def monto_con_signo(self) -> float:
        """Monto positivo si entra dinero, negativo si sale."""
        return -self.monto if self.es_cargo else self.monto


@dataclass(slots=True)
class ResultadoLectura:
    """
    Lo extraído de un documento, con lo necesario para confiar en ello.

    El cuadre es la red de seguridad: si lo leído no reconstruye el saldo
    que el propio documento declara, el parser perdió o duplicó filas, y
    más vale decirlo que dejar pasar cifras incompletas.
    """

    banco: str
    movimientos: list[MovimientoImportado] = field(default_factory=list)
    periodo_inicio: date | None = None
    periodo_fin: date | None = None
    saldo_inicial: float | None = None
    saldo_final: float | None = None
    total_cargos: float | None = None
    total_abonos: float | None = None
    avisos: list[str] = field(default_factory=list)

    @property
    def cargos(self) -> float:
        """Suma de lo que salió."""
        return sum(m.monto for m in self.movimientos if m.es_cargo)

    @property
    def abonos(self) -> float:
        """Suma de lo que entró."""
        return sum(m.monto for m in self.movimientos if not m.es_cargo)

    @property
    def cuadra(self) -> bool | None:
        """
        Indica si lo leído reconstruye los totales del documento.

        Devuelve None cuando el documento no declara con qué comparar.
        """
        diferencia = self.diferencia_de_cuadre
        if diferencia is None:
            return None

        return abs(diferencia) < 0.02

    @property
    def diferencia_de_cuadre(self) -> float | None:
        """
        Cuánto falta o sobra frente a lo que declara el documento.

        Prefiere los totales de cargos y abonos si existen; si no, usa los
        saldos inicial y final.
        """
        if self.total_cargos is not None and self.total_abonos is not None:
            leido = self.cargos - self.abonos
            declarado = self.total_cargos - self.total_abonos
            return round(leido - declarado, 2)

        if self.saldo_inicial is not None and self.saldo_final is not None:
            esperado = self.saldo_inicial + self.cargos - self.abonos
            return round(esperado - self.saldo_final, 2)

        return None


@runtime_checkable
class LectorBanco(Protocol):
    """
    Contrato de un lector de estados de cuenta.

    Agregar un banco es una clase nueva en el registro, sin tocar nada
    más: el importador prueba cuál reconoce el documento.
    """

    nombre: str

    def reconoce(self, texto: str) -> bool:
        """Indica si el documento es de este banco y formato."""
        ...

    def leer(self, lineas: list[str]) -> ResultadoLectura:
        """Extrae los movimientos de las líneas del documento."""
        ...


# ═══════════════════════════════════════════════════════════
# Normalización
#
# Cada banco escribe fechas y montos a su manera, así que la
# conversión vive aquí y no repetida en cada lector.
# ═══════════════════════════════════════════════════════════

_CON_SIGNO = re.compile(r"\$\s*-?\d[\d,]*(?:\.\d+)?")
_SIN_SIGNO = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def parsear_monto(texto: str) -> float | None:
    """
    Convierte «$1,234.56», «- $459.00» o «+$388.00» en un número.

    Se queda con el último importe de la línea, y prefiere los que llevan
    símbolo de moneda. Tomar el primer número sería más simple y estaría
    mal: «Saldo inicial del periodo (DIC 2025) $905.17» leería el año, y
    «ANTHROPIC CLAUDE SUB US$ 20.00 $341.00» leería el precio en dólares
    en vez del cargo en pesos.

    Devuelve siempre el valor absoluto: el signo lo decide el lector según
    la columna o el prefijo, que es información que aquí ya se perdió.
    """
    if not texto:
        return None

    candidatos = _CON_SIGNO.findall(texto) or _SIN_SIGNO.findall(texto)
    if not candidatos:
        return None

    crudo = candidatos[-1].replace("$", "").replace(",", "").replace(" ", "")
    crudo = crudo.lstrip("-").lstrip("+")
    if not crudo or crudo == ".":
        return None

    try:
        return abs(float(crudo))
    except ValueError:
        return None


def es_negativo(texto: str) -> bool:
    """Indica si el importe viene marcado como abono."""
    limpio = texto.strip()
    return limpio.startswith("-") or "- $" in limpio or "-$" in limpio


def parsear_fecha_corta(texto: str, anio: int) -> date | None:
    """
    Convierte «23 DIC» o «04 ENE» usando el año que se le indique.

    El día y el mes vienen en la fila; el año, sólo en el encabezado del
    documento, así que tiene que llegar desde fuera.
    """
    encontrado = re.search(r"(\d{1,2})\s+([A-ZÁÉÍÓÚ]{3})", texto.upper())
    if encontrado is None:
        return None

    dia, mes = int(encontrado.group(1)), MESES.get(encontrado.group(2))
    if mes is None:
        return None

    try:
        return date(anio, mes, dia)
    except ValueError:
        return None


def parsear_fecha_larga(texto: str) -> date | None:
    """Convierte «22 MAY 2026» en fecha."""
    encontrado = re.search(r"(\d{1,2})\s+([A-ZÁÉÍÓÚ]{3})\s+(\d{4})", texto.upper())
    if encontrado is None:
        return None

    dia = int(encontrado.group(1))
    mes = MESES.get(encontrado.group(2))
    anio = int(encontrado.group(3))
    if mes is None:
        return None

    try:
        return date(anio, mes, dia)
    except ValueError:
        return None


def parsear_fecha_numerica(texto: str, anio: int | None = None) -> date | None:
    """
    Convierte «22/07», «01-06-2026» o «22/07/2026».

    Sin año en la cadena usa el que se le pase, que sale del periodo del
    documento.
    """
    completo = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", texto)
    if completo is not None:
        dia, mes, anio_texto = (int(g) for g in completo.groups())
        try:
            return date(anio_texto, mes, dia)
        except ValueError:
            return None

    corto = re.search(r"\b(\d{1,2})[/-](\d{1,2})\b", texto)
    if corto is None or anio is None:
        return None

    dia, mes = int(corto.group(1)), int(corto.group(2))
    try:
        return date(anio, mes, dia)
    except ValueError:
        return None


def anio_del_periodo(fecha_inicio: date | None, mes: int) -> int:
    """
    Resuelve a qué año pertenece un mes dentro de un periodo a caballo.

    Un periodo que va del 23 de diciembre al 22 de enero tiene filas de
    dos años distintos y las filas sólo dicen el mes, así que diciembre
    pertenece al año en que empezó y enero al siguiente.
    """
    if fecha_inicio is None:
        return date.today().year

    if mes < fecha_inicio.month:
        return fecha_inicio.year + 1

    return fecha_inicio.year
