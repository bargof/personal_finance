from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time

from finanzas.domain.enums import (
    COBROS_POR_ANIO,
    EstadoDeseo,
    EstadoMeta,
    EstadoMovimiento,
    EstadoPresupuesto,
    FrecuenciaCobro,
    Liquidez,
    Naturaleza,
    Necesidad,
    OrigenSaldo,
    Prioridad,
    TipoCuenta,
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
    """
    Cuenta o instrumento por el que entra y sale el dinero.

    Cada cuenta es un libro: todo movimiento confirmado y pagado la mueve,
    y su saldo a cualquier fecha se deduce de esos movimientos a partir
    de un saldo verificado. El tipo decide de qué lado del balance cae y
    qué significa mover dinero hacia ella o desde ella.
    """

    nombre: str
    id: int | None = None
    tipo: TipoCuenta = TipoCuenta.DEBITO
    institucion: str = ""
    activa: bool = True

    @property
    def lado(self) -> TipoPatrimonio:
        """Lado del balance en el que entra la cuenta."""
        return self.tipo.lado

    @property
    def es_pasivo(self) -> bool:
        """Indica si su saldo es deuda: tarjeta de crédito o préstamo."""
        return self.tipo.es_pasivo

    def saldo_visto(self, saldo_libro: float) -> float:
        """
        Traduce el saldo del libro al número que enseña el banco.

        En el libro una deuda es negativa; el banco la reporta en positivo
        como «lo que debes». Aquí se hace la conversión para que el usuario
        capture y lea siempre como en su estado de cuenta.
        """
        return -saldo_libro if self.es_pasivo else saldo_libro

    def saldo_libro(self, saldo_visto: float) -> float:
        """Inversa de `saldo_visto`: del número del banco al signo del libro."""
        return -saldo_visto if self.es_pasivo else saldo_visto


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

    `fecha` y `fecha_pago` responden preguntas distintas: cuándo se
    incurrió el gasto y cuándo salió el dinero. Separarlas es lo que
    permite registrar un devengado —una compra con tarjeta que aún no
    se paga— sin que consuma caja antes de tiempo ni deje de pesar en
    el presupuesto del mes en que ocurrió.
    """

    fecha: date
    tipo: TipoMovimiento
    monto: float
    categoria_id: int
    cuenta_id: int
    id: int | None = None
    subcategoria_id: int | None = None
    cuenta_destino_id: int | None = None
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
    fecha_pago: date | None = None

    #: Concepto tal como lo escribió el banco, si vino de un estado de
    #: cuenta. `descripcion` es cómo lo llama uno y se edita a gusto; esto
    #: es el original con el que se audita contra el documento.
    descripcion_banco: str = ""

    #: Folio del banco, donde el documento lo trae.
    referencia_externa: str = ""

    #: Fecha tal como la reportó el banco, si vino de un estado de cuenta.
    #: `fecha` se edita; ésta es con la que se reconoce al reimportar.
    fecha_banco: date | None = None

    #: Quién cobró: el comercio o la marca (Walmart, DiDi, Oxxo). Texto
    #: libre: un comercio no es un catálogo.
    empresa: str = ""

    #: Dónde ocurrió: la plaza, la colonia, la ciudad. Aparte de la
    #: empresa porque la misma empresa cobra en muchos sitios.
    lugar: str = ""

    #: A qué hora, si se sabe. Ningún estado de cuenta la trae.
    hora: time | None = None

    # ── Columnas derivadas (equivalentes a V:Y del Excel) ──

    @property
    def pagado(self) -> bool:
        """Indica si el dinero ya salió o entró."""
        return self.fecha_pago is not None

    @property
    def por_pagar(self) -> float:
        """
        Monto devengado que sigue sin pagarse.

        Es un adeudo generado: el gasto ya ocurrió, así que pesa en el
        presupuesto, pero el dinero no ha salido de ninguna cuenta. Sólo
        un gasto puede quedar así; un ingreso, un ahorro o un traspaso
        ocurren o no ocurren.
        """
        es_gasto = self.tipo == TipoMovimiento.GASTO
        if es_gasto and self._confirmado and not self.pagado:
            return self.monto
        return 0.0

    @property
    def impacto_caja(self) -> float:
        """
        Efecto neto sobre el efectivo disponible.

        Lo devengado no mueve caja: mientras no haya fecha de pago, el
        dinero sigue en la cuenta.
        """
        if not self.pagado:
            return 0.0
        if self.tipo == TipoMovimiento.INGRESO:
            return self.monto
        if self.tipo == TipoMovimiento.TRANSFERENCIA:
            return 0.0
        return -self.monto

    @property
    def importado(self) -> bool:
        """Indica si el movimiento vino de un estado de cuenta."""
        return bool(self.descripcion_banco or self.referencia_externa)

    @property
    def mueve_entre_cuentas(self) -> bool:
        """
        Indica si el dinero sale de una cuenta propia y entra en otra.

        Lo hace un traspaso, y también un ahorro o una inversión: el dinero
        no se va, cambia de cuenta. Lo que los distingue es la intención,
        no la mecánica.
        """
        return self.tipo in (
            TipoMovimiento.TRANSFERENCIA,
            TipoMovimiento.AHORRO,
            TipoMovimiento.INVERSION,
        )

    @property
    def es_traspaso(self) -> bool:
        """Indica si el movimiento mueve dinero entre dos cuentas propias."""
        return self.mueve_entre_cuentas and self.cuenta_destino_id is not None

    def flujo_de(self, cuenta_id: int) -> float:
        """
        Efecto sobre una cuenta concreta.

        `impacto_caja` mira la caja completa, donde una transferencia vale
        cero. Vista cuenta por cuenta no es neutra: sale de una y entra en
        otra. Sólo lo confirmado y pagado mueve un libro.
        """
        if not self.pagado or not self._confirmado:
            return 0.0
        if self.es_traspaso and cuenta_id == self.cuenta_destino_id:
            return self.monto
        if cuenta_id != self.cuenta_id:
            return 0.0
        if self.tipo == TipoMovimiento.INGRESO:
            return self.monto
        return -self.monto

    @property
    def periodo_pago(self) -> str | None:
        """Periodo en que el dinero se movió, o None si aún no se paga."""
        if self.fecha_pago is None:
            return None
        return self.fecha_pago.strftime("%Y-%m")

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
class ProductoDeMovimiento:
    """
    Un artículo dentro de una compra de varias cosas.

    No es un movimiento: el gasto sigue siendo uno, con una categoría, y
    esto dice qué había dentro. Por eso no lleva categoría propia; si algo
    necesita la suya, es un movimiento aparte.
    """

    producto: str
    cantidad: float = 1.0
    precio_unitario: float = 0.0
    id: int | None = None
    movimiento_id: int | None = None
    orden: int = 0
    nota: str = ""

    @property
    def importe(self) -> float:
        """Lo que costó la partida completa."""
        return round(self.cantidad * self.precio_unitario, 2)


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
    """
    Una línea del balance que no es una cuenta: la casa, el auto, un
    préstamo entre personas.

    Las cuentas del catálogo no van aquí: su saldo se deduce de los
    movimientos y de sus saldos verificados. `cuenta_id` sobrevive sólo
    para reconocer posiciones capturadas antes de ese cambio.
    """

    nombre: str
    tipo: TipoPatrimonio
    saldo: float
    id: int | None = None
    cuenta_id: int | None = None
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

    @property
    def es_cuenta(self) -> bool:
        """Indica si la posición refleja el saldo de una cuenta del catálogo."""
        return self.cuenta_id is not None


@dataclass(slots=True)
class SaldoVerificado:
    """
    Un saldo real de una cuenta al cierre de un día.

    Es lo único del balance que se captura: lo que dice el estado de
    cuenta al inicio y al fin de su periodo, o lo que enseña la app del
    banco hoy. Todo lo demás se deduce de los movimientos.

    `saldo` va con el signo del libro: en una tarjeta, deber es negativo.
    """

    cuenta_id: int
    fecha: date
    saldo: float
    id: int | None = None
    origen: OrigenSaldo = OrigenSaldo.MANUAL
    nota: str = ""


@dataclass(slots=True)
class Suscripcion:
    """Servicio de cobro recurrente."""

    servicio: str
    costo_por_cobro: float
    id: int | None = None
    categoria_id: int | None = None
    subcategoria_id: int | None = None
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
class Deseo:
    """
    Algo que se quiere comprar y todavía no.

    Existe para decidir con números en vez de con ganas: puesto al lado
    del saldo disponible, se ve de un vistazo qué alcanza y qué no.
    """

    nombre: str
    costo: float
    id: int | None = None
    categoria_id: int | None = None
    prioridad: Prioridad = Prioridad.MEDIA
    enlace: str = ""
    notas: str = ""
    comprado_en: date | None = None

    @property
    def comprado(self) -> bool:
        """Indica si ya salió de la lista."""
        return self.comprado_en is not None

    def cobertura(self, saldo: float) -> float:
        """
        Qué proporción del costo cubre el saldo disponible.

        Se limita a uno: pasado el costo, lo que sobra ya no dice nada
        sobre este deseo en particular.
        """
        if self.costo <= 0:
            return 1.0

        return min(saldo / self.costo, 1.0)

    def faltante(self, saldo: float) -> float:
        """Cuánto falta para poder comprarlo."""
        return max(self.costo - saldo, 0.0)

    def alcance(self, saldo: float, umbral_cerca: float = 0.75) -> EstadoDeseo:
        """
        Situación del deseo frente al saldo disponible.

        `umbral_cerca` marca desde dónde se considera que ya casi alcanza;
        por debajo de eso falta demasiado como para contarlo.
        """
        if saldo >= self.costo:
            return EstadoDeseo.ALCANZA
        if self.cobertura(saldo) >= umbral_cerca:
            return EstadoDeseo.CASI
        return EstadoDeseo.LEJOS


@dataclass(slots=True)
class Proyecto:
    """
    Un esfuerzo que agrupa movimientos de varias categorías.

    Un viaje cruza transporte, comida y hospedaje; la categoría dice qué
    clase de gasto fue y el proyecto para qué se hizo.
    """

    nombre: str
    id: int | None = None
    descripcion: str = ""
    presupuesto: float = 0.0
    fecha_inicio: date | None = None
    fecha_fin: date | None = None
    activo: bool = True
    notas: str = ""

    def disponible(self, gastado: float) -> float | None:
        """Cuánto queda del presupuesto, o None si no se le puso uno."""
        if self.presupuesto <= 0:
            return None

        return self.presupuesto - gastado

    def pct_usado(self, gastado: float) -> float:
        """Proporción del presupuesto ya consumida."""
        if self.presupuesto <= 0:
            return 0.0

        return gastado / self.presupuesto


@dataclass(slots=True)
class CierreMensual:
    """
    Fotografía del balance al cierre de un mes.

    Ya no se captura: se deduce de los saldos de las cuentas al último día
    del periodo, más las posiciones que no son cuenta.
    """

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
