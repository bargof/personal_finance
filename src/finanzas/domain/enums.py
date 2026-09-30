from __future__ import annotations

from datetime import date
from enum import StrEnum

# ═══════════════════════════════════════════════════
# Vocabulario controlado heredado del Excel original
# ═══════════════════════════════════════════════════


class TipoMovimiento(StrEnum):
    """Tipo de movimiento; define su efecto sobre caja y patrimonio."""

    INGRESO = "Ingreso"
    GASTO = "Gasto"
    AHORRO = "Ahorro"
    INVERSION = "Inversión"
    TRANSFERENCIA = "Transferencia"


class Necesidad(StrEnum):
    """Distingue gasto indispensable de gasto discrecional."""

    ESENCIAL = "Esencial"
    DESEO = "Deseo"


class Naturaleza(StrEnum):
    """Distingue el gasto comprometido del que varía mes a mes."""

    FIJO = "Fijo"
    VARIABLE = "Variable"


class EstadoMovimiento(StrEnum):
    """Solo los movimientos confirmados alimentan los cálculos."""

    CONFIRMADO = "Confirmado"
    PENDIENTE = "Pendiente"


class Liquidez(StrEnum):
    """Qué tan rápido puede convertirse un activo en efectivo."""

    ALTA = "Alta"
    MEDIA = "Media"
    BAJA = "Baja"
    NO_APLICA = "No aplica"


class TipoPatrimonio(StrEnum):
    """Lado del balance al que pertenece una cuenta o activo."""

    ACTIVO = "Activo"
    PASIVO = "Pasivo"


class TipoCuenta(StrEnum):
    """
    Qué clase de cuenta es, y con ello de qué lado del balance cae.

    El tipo decide cosas que antes había que capturar a mano: una tarjeta
    de crédito es deuda, así que su saldo va en negativo y comprar con
    ella no toca la caja; un apartado es ahorro, así que mover dinero
    hacia él cuenta como patrimonio creado y sacarlo como retiro.
    """

    EFECTIVO = "Efectivo"
    DEBITO = "Débito"
    AHORRO = "Ahorro"
    INVERSION = "Inversión"
    VALES = "Vales"
    CREDITO = "Crédito"
    PRESTAMO = "Préstamo"
    OTRO = "Otro"

    @property
    def es_pasivo(self) -> bool:
        """Indica si el saldo de la cuenta es dinero que se debe."""
        return self in (TipoCuenta.CREDITO, TipoCuenta.PRESTAMO)

    @property
    def lado(self) -> TipoPatrimonio:
        """Lado del balance en el que entra la cuenta."""
        return TipoPatrimonio.PASIVO if self.es_pasivo else TipoPatrimonio.ACTIVO

    @property
    def guarda_ahorro(self) -> bool:
        """Indica si meter dinero aquí cuenta como ahorro o inversión."""
        return self in (TipoCuenta.AHORRO, TipoCuenta.INVERSION)

    @property
    def es_liquida(self) -> bool:
        """Indica si el saldo se puede usar de inmediato."""
        return self in (TipoCuenta.EFECTIVO, TipoCuenta.DEBITO, TipoCuenta.AHORRO)

    @property
    def liquidez(self) -> Liquidez:
        """Liquidez que se deriva del tipo, sin capturarla aparte."""
        if self.es_pasivo:
            return Liquidez.NO_APLICA
        if self.es_liquida:
            return Liquidez.ALTA
        if self == TipoCuenta.INVERSION:
            return Liquidez.MEDIA
        return Liquidez.BAJA


#: Equivalencias de tipos capturados antes de que existiera el catálogo.
TIPOS_CUENTA_HEREDADOS: dict[str, str] = {
    "Banco": TipoCuenta.DEBITO,
}


class OrigenSaldo(StrEnum):
    """De dónde salió un saldo verificado."""

    MANUAL = "Manual"
    ESTADO_DE_CUENTA = "Estado de cuenta"


class Prioridad(StrEnum):
    """Prioridad relativa de una meta financiera."""

    ALTA = "Alta"
    MEDIA = "Media"
    BAJA = "Baja"


class FrecuenciaCobro(StrEnum):
    """Periodicidad de cobro de una suscripción."""

    MENSUAL = "Mensual"
    BIMESTRAL = "Bimestral"
    TRIMESTRAL = "Trimestral"
    SEMESTRAL = "Semestral"
    ANUAL = "Anual"


class ClaseCargo(StrEnum):
    """
    Qué tipo de cargo fijo es.

    Una suscripción es discrecional y se puede cancelar; un gasto fijo
    —la renta, el celular— es un compromiso. Los dos tienen monto y
    fecha, y los dos entran al calendario de pagos.
    """

    SUSCRIPCION = "Suscripción"
    GASTO_FIJO = "Gasto fijo"


class EstadoPago(StrEnum):
    """Dónde está un pago que toca hacer: un cargo fijo o algo exigible."""

    PAGADO = "Pagado"
    POR_PAGAR = "Por pagar"
    VENCE_HOY = "Vence hoy"
    VENCIDO = "Vencido"

    @classmethod
    def segun_fecha(cls, limite: date | None, hoy: date) -> EstadoPago:
        """El estado de algo aún sin pagar, según su fecha límite."""
        if limite is None or limite > hoy:
            return cls.POR_PAGAR
        if limite == hoy:
            return cls.VENCE_HOY
        return cls.VENCIDO


class EstadoIngreso(StrEnum):
    """Dónde está un ingreso fijo del mes, como la nómina de una quincena."""

    RECIBIDO = "Recibido"
    POR_RECIBIR = "Por recibir"
    ATRASADO = "Atrasado"


#: Cobros al año por frecuencia, para normalizar suscripciones a costo mensual.
COBROS_POR_ANIO: dict[str, int] = {
    FrecuenciaCobro.MENSUAL: 12,
    FrecuenciaCobro.BIMESTRAL: 6,
    FrecuenciaCobro.TRIMESTRAL: 4,
    FrecuenciaCobro.SEMESTRAL: 2,
    FrecuenciaCobro.ANUAL: 1,
}


class EstadoPresupuesto(StrEnum):
    """Semáforo de una categoría frente a su presupuesto activo."""

    EN_ORDEN = "En orden"
    ATENCION = "Atención"
    EXCEDIDO = "Excedido"
    SIN_PRESUPUESTO = "Sin presupuesto"
    CONFIGURAR = "Configurar"


class EstadoDeseo(StrEnum):
    """Situación de un deseo frente al saldo disponible."""

    ALCANZA = "Alcanza"
    CASI = "Casi"
    LEJOS = "Falta mucho"


class EstadoMeta(StrEnum):
    """Situación de una meta frente a su fecha límite y aportación."""

    EN_RUTA = "En ruta"
    AJUSTAR = "Ajustar aportación"
    VENCIDA = "Vencida"
    LOGRADA = "Lograda"
