from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from finanzas.domain.enums import (
    COBROS_POR_ANIO,
    EstadoMeta,
    EstadoMovimiento,
    EstadoPresupuesto,
    FrecuenciaCobro,
    Liquidez,
    Naturaleza,
    Necesidad,
    Prioridad,
    TipoMovimiento,
    TipoPatrimonio,
)

# ═══════════════════════════════════════════════════════════
# Entidades del dominio
#
# Cada entidad concentra las reglas que en el Excel vivían
# como columnas calculadas. Los repositorios sólo persisten
# los campos capturados; lo derivado se calcula aquí.
# ═══════════════════════════════════════════════════════════


@dataclass(slots=True)
class Categoria:
    """Categoría de gasto o ingreso; raíz del árbol de clasificación."""

    nombre: str
    id: int | None = None
    tipo: str = TipoMovimiento.GASTO
    activa: bool = True
    orden: int = 0


@dataclass(slots=True)
class Subcategoria:
    """Detalle de una categoría (por ejemplo, Vivienda → Renta/Hipoteca)."""

    nombre: str
    categoria_id: int
    id: int | None = None
    activa: bool = True


@dataclass(slots=True)
class Cuenta:
    """Cuenta o instrumento por el que entra y sale el dinero."""

    nombre: str
    id: int | None = None
    tipo: str = "Banco"
    institucion: str = ""
    activa: bool = True


@dataclass(slots=True)
class MedioPago:
    """Forma en que se liquidó un movimiento."""

    nombre: str
    id: int | None = None
    activo: bool = True


@dataclass(slots=True)
class Movimiento:
    """
    Un movimiento de dinero.

    El monto siempre se captura positivo; `tipo` define su efecto sobre
    la caja y sobre el patrimonio, igual que en el Excel de origen.
    """

    fecha: date
    tipo: TipoMovimiento
    monto: float
    categoria_id: int
    cuenta_id: int
    id: int | None = None
    subcategoria_id: int | None = None
    medio_pago_id: int | None = None
    descripcion: str = ""
    necesidad: Necesidad = Necesidad.ESENCIAL
    naturaleza: Naturaleza = Naturaleza.VARIABLE
    recurrente: bool = False
    planeado: bool = True
    proyecto: str = ""
    etiquetas: str = ""
    nota: str = ""
    estado: EstadoMovimiento = EstadoMovimiento.CONFIRMADO

    # ── Columnas derivadas (equivalentes a V:Y del Excel) ──

    @property
    def impacto_caja(self) -> float:
        """Efecto neto sobre el efectivo disponible."""
        if self.tipo == TipoMovimiento.INGRESO:
            return self.monto
        if self.tipo == TipoMovimiento.TRANSFERENCIA:
            return 0.0
        return -self.monto

    @property
    def gasto_real(self) -> float:
        """Monto que cuenta como gasto consumido del periodo."""
        es_gasto = self.tipo == TipoMovimiento.GASTO
        return self.monto if es_gasto and self._confirmado else 0.0

    @property
    def patrimonio_creado(self) -> float:
        """Monto que se convirtió en ahorro o inversión."""
        construye = self.tipo in (TipoMovimiento.AHORRO, TipoMovimiento.INVERSION)
        return self.monto if construye and self._confirmado else 0.0

    @property
    def periodo(self) -> str:
        """Periodo mensual al que pertenece el movimiento (YYYY-MM)."""
        return self.fecha.strftime("%Y-%m")

    @property
    def _confirmado(self) -> bool:
        return self.estado == EstadoMovimiento.CONFIRMADO


@dataclass(slots=True)
class LineaPresupuesto:
    """
    Presupuesto de una categoría para un periodo.

    El presupuesto activo es el monto manual cuando existe; si no,
    el promedio de los últimos 3 meses menos el recorte objetivo.
    """

    periodo: str
    categoria_id: int
    categoria: str = ""
    id: int | None = None
    monto_manual: float = 0.0
    pct_recorte: float = 0.0
    promedio_3m: float = 0.0
    gasto_del_mes: float = 0.0

    @property
    def presupuesto_activo(self) -> float:
        """Monto contra el que se compara el gasto del periodo."""
        if self.monto_manual > 0:
            return self.monto_manual
        return self.promedio_3m * (1 - self.pct_recorte)

    @property
    def disponible(self) -> float:
        """Saldo que queda por gastar en la categoría."""
        return self.presupuesto_activo - self.gasto_del_mes

    @property
    def pct_usado(self) -> float:
        """Proporción del presupuesto ya consumida."""
        if self.presupuesto_activo == 0:
            return 0.0
        return self.gasto_del_mes / self.presupuesto_activo

    def estado(self, umbral_alerta: float) -> EstadoPresupuesto:
        """Semáforo de la categoría dado el umbral de alerta configurado."""
        if self.presupuesto_activo == 0:
            if self.gasto_del_mes == 0:
                return EstadoPresupuesto.SIN_PRESUPUESTO
            return EstadoPresupuesto.CONFIGURAR
        if self.gasto_del_mes > self.presupuesto_activo:
            return EstadoPresupuesto.EXCEDIDO
        if self.pct_usado >= umbral_alerta:
            return EstadoPresupuesto.ATENCION
        return EstadoPresupuesto.EN_ORDEN


@dataclass(slots=True)
class Meta:
    """Objetivo financiero con monto, fecha y aportación mensual."""

    objetivo: str
    monto_meta: float
    id: int | None = None
    tipo: str = "Seguridad"
    acumulado: float = 0.0
    aporte_mensual_planeado: float = 0.0
    fecha_limite: date | None = None
    prioridad: Prioridad = Prioridad.MEDIA
    vehiculo: str = ""
    notas: str = ""

    def meses_restantes(self, hoy: date | None = None) -> int:
        """Meses completos que faltan para la fecha límite."""
        if self.fecha_limite is None:
            return 0
        hoy = hoy or date.today()
        meses = (self.fecha_limite.year - hoy.year) * 12
        meses += self.fecha_limite.month - hoy.month
        return max(0, meses)

    def aporte_necesario(self, hoy: date | None = None) -> float:
        """Aportación mensual requerida para llegar a tiempo."""
        meses = self.meses_restantes(hoy)
        if self.monto_meta == 0 or meses == 0:
            return 0.0
        return max(0.0, (self.monto_meta - self.acumulado) / meses)

    @property
    def pct_avance(self) -> float:
        """Proporción de la meta ya cubierta."""
        if self.monto_meta == 0:
            return 0.0
        return self.acumulado / self.monto_meta

    def estado(self, hoy: date | None = None) -> EstadoMeta:
        """Situación de la meta frente a su plan."""
        hoy = hoy or date.today()
        if self.acumulado >= self.monto_meta:
            return EstadoMeta.LOGRADA
        if self.fecha_limite is not None and self.fecha_limite < hoy:
            return EstadoMeta.VENCIDA
        if self.aporte_necesario(hoy) > self.aporte_mensual_planeado:
            return EstadoMeta.AJUSTAR
        return EstadoMeta.EN_RUTA


@dataclass(slots=True)
class PosicionPatrimonial:
    """Una línea del balance personal: un activo o un pasivo."""

    nombre: str
    tipo: TipoPatrimonio
    saldo: float
    id: int | None = None
    subtipo: str = ""
    institucion: str = ""
    liquidez: Liquidez = Liquidez.NO_APLICA
    tasa_anual: float = 0.0
    fecha_corte: date | None = None
    moneda: str = "MXN"
    notas: str = ""

    @property
    def aporte_a_patrimonio(self) -> float:
        """Contribución con signo al patrimonio neto."""
        if self.tipo == TipoPatrimonio.ACTIVO:
            return self.saldo
        return -self.saldo


@dataclass(slots=True)
class Suscripcion:
    """Servicio de cobro recurrente."""

    servicio: str
    costo_por_cobro: float
    id: int | None = None
    categoria_id: int | None = None
    frecuencia: FrecuenciaCobro = FrecuenciaCobro.MENSUAL
    proximo_cobro: date | None = None
    cuenta_id: int | None = None
    renovacion_automatica: bool = True
    necesidad: Necesidad = Necesidad.DESEO
    activa: bool = True
    notas: str = ""

    @property
    def costo_anual(self) -> float:
        """Costo acumulado en doce meses."""
        return self.costo_por_cobro * COBROS_POR_ANIO[self.frecuencia]

    @property
    def costo_mensual(self) -> float:
        """Costo normalizado a un mes, comparable entre frecuencias."""
        return self.costo_anual / 12

    @property
    def candidato_a_cancelar(self) -> bool:
        """Suscripción activa, discrecional y con renovación automática."""
        return (
            self.activa
            and self.necesidad == Necesidad.DESEO
            and self.renovacion_automatica
        )


@dataclass(slots=True)
class CierreMensual:
    """Fotografía del balance al cierre de un mes."""

    periodo: str
    id: int | None = None
    efectivo: float = 0.0
    ahorro: float = 0.0
    inversiones: float = 0.0
    otros_activos: float = 0.0
    deudas: float = 0.0
    notas: str = ""

    @property
    def patrimonio_neto(self) -> float:
        """Activos menos deudas al cierre del periodo."""
        activos = self.efectivo + self.ahorro + self.inversiones + self.otros_activos
        return activos - self.deudas


@dataclass(slots=True)
class ReglasFinancieras:
    """Parámetros configurables que alimentan presupuesto, score y alertas."""

    moneda: str = "MXN"
    meta_ahorro_inversion: float = 0.20
    meses_fondo_emergencia: int = 6
    max_deseos: float = 0.30
    umbral_gasto_pequeno: float = 200.0
    alerta_presupuesto: float = 0.90
    dia_inicio_ciclo: int = 1
    campos_extra: dict[str, str] = field(default_factory=dict)
