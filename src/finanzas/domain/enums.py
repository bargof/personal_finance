from __future__ import annotations

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


class TipoPatrimonio(StrEnum):
    """Lado del balance al que pertenece una cuenta o activo."""

    ACTIVO = "Activo"
    PASIVO = "Pasivo"


class Liquidez(StrEnum):
    """Qué tan rápido puede convertirse un activo en efectivo."""

    ALTA = "Alta"
    MEDIA = "Media"
    BAJA = "Baja"
    NO_APLICA = "No aplica"


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
