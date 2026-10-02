from __future__ import annotations

import calendar
from dataclasses import dataclass, replace
from datetime import date, timedelta

import pandas as pd

from finanzas.analytics.aggregations import MESES_ES
from finanzas.application.services.patrimonio_service import PatrimonioService
from finanzas.data.repositories.ingresos_fijos_repository import (
    IngresosFijosRepository,
)
from finanzas.data.repositories.movimientos_repository import MovimientosRepository
from finanzas.data.repositories.plan_repository import PlanRepository
from finanzas.data.repositories.suscripciones_repository import SuscripcionesRepository
from finanzas.domain.calendario import cobro_en_mes, siguiente_dia
from finanzas.domain.enums import (
    EstadoIngreso,
    EstadoMovimiento,
    EstadoPago,
    TipoCuenta,
    TipoMovimiento,
)
from finanzas.domain.plan import (
    Ajuste,
    Decision,
    EntradaMes,
    MesDelPlan,
    Obligacion,
    faltante_de,
    proyectar,
)

# ═══════════════════════════════════════════════════════════
# Pagos del mes
#
# El balance dice cuánto se debe en total; esta vista dice qué
# hay que pagar ya y si alcanza. Junta tres fuentes que ya
# existen sin duplicar ninguna:
#
# - los cargos fijos del catálogo, con su fecha en el mes y si
#   ya se registró el movimiento que los paga;
# - lo exigible de cada deuda (la parcialidad, el pago de la
#   tarjeta), menos lo abonado desde que se declaró;
# - lo gastado que aún no sale de ninguna cuenta.
#
# Un cargo fijo registrado pero sin pagar ya es un gasto por
# pagar: se cuenta ahí y no otra vez como cargo fijo.
# ═══════════════════════════════════════════════════════════

#: Días alrededor de la fecha de un cargo en que se busca el movimiento
#: que lo paga. La renta del 1 pagada el 28 del mes anterior sigue
#: siendo la renta de este mes.
VENTANA_DIAS = 10

#: Los cargos fijos del mes siguiente que vencen hasta este día se pagan
#: con el dinero del mes anterior: la renta del 1 se paga al cerrar el mes,
#: y es ahí cuando hay que tenerlo. Es el valor por defecto; el usuario lo
#: cambia en la vista.
ANTICIPACION_DIAS = 1

#: Cuánto puede variar el monto de un cargo fijo de un mes a otro: el
#: recibo de luz no cuesta lo mismo cada vez, y una suscripción sube.
TOLERANCIA_MONTO = 0.20

#: El retiro del fondo de ahorro se pide antes de este día del mes y
#: llega el día `DIA_ABONO_RETIRO` de ese mismo mes.
DIA_LIMITE_RETIRO = 22
DIA_ABONO_RETIRO = 30

#: En diciembre el fondo de ahorro entrega lo que no se retiró en el año.
MES_LIQUIDACION_FONDO = 12
DIA_LIQUIDACION_FONDO = 30

#: Los dos tipos de lo que sólo existe en la simulación.
SIMULADO_INGRESO = "ingreso"
SIMULADO_APORTACION = "aportacion"

#: Clave en `configuracion` de la anticipación que fijó el usuario.
CLAVE_ANTICIPACION = "pagos_anticipacion_dias"

#: Cómo se nombra cada fuente en la lista de pagos.
GASTO_SIN_PAGAR = "Gasto sin pagar"
DEUDA_EXIGIBLE = "Deuda exigible"
SALDO_TARJETA = "Saldo de tarjeta"

#: Días alrededor del día de un ingreso fijo en que se busca el
#: movimiento que lo trae: la nómina del 15 que cae el 14 es la del 15.
VENTANA_INGRESO_DIAS = 7

#: Qué papel juega cada ingreso registrado en el ingreso del mes.
COMO_FIJO = "Ingreso fijo"
COMO_OTRO = "Otro ingreso"
COMO_PENDIENTE = "Por recibir"
COMO_RESTRINGIDA = "No cuenta: cuenta restringida"


@dataclass(slots=True)
class ResumenPagos:
    """Las cifras de arriba de la vista: qué entra, qué hay y qué se debe."""

    #: Lo que ya entró y lo que falta, que juntos son el ingreso del mes.
    recibido: float = 0.0
    por_recibir: float = 0.0
    disponible: float = 0.0
    fijos_total: float = 0.0
    fijos_pagados: float = 0.0
    fijos_pendientes: float = 0.0
    exigible: float = 0.0
    #: Lo que deben las tarjetas aparte de lo exigible: lo comprado después
    #: del corte, o todo el saldo si la tarjeta no tiene ciclo.
    saldo_tarjetas: float = 0.0
    sin_pagar: float = 0.0

    @property
    def ingresos(self) -> float:
        """El ingreso del mes: lo recibido más lo que falta por recibir."""
        return round(self.recibido + self.por_recibir, 2)

    @property
    def por_pagar(self) -> float:
        """Todo lo que hay que pagar: cada peso contado una sola vez."""
        return round(
            self.fijos_pendientes
            + self.exigible
            + self.saldo_tarjetas
            + self.sin_pagar,
            2,
        )

    @property
    def queda(self) -> float:
        """Lo disponible hoy menos lo que hay que pagar."""
        return round(self.disponible - self.por_pagar, 2)


class PagosService:
    """Casos de uso de la vista de pagos del mes."""

    def __init__(
        self,
        movimientos: MovimientosRepository | None = None,
        suscripciones: SuscripcionesRepository | None = None,
        patrimonio: PatrimonioService | None = None,
        ingresos_fijos: IngresosFijosRepository | None = None,
        plan: PlanRepository | None = None,
    ) -> None:
        self._movimientos = movimientos or MovimientosRepository()
        self._suscripciones = suscripciones or SuscripcionesRepository()
        self._patrimonio = patrimonio or PatrimonioService()
        self._ingresos_fijos = ingresos_fijos or IngresosFijosRepository()
        self._plan = plan or PlanRepository()

    # ── Cargos fijos ─────────────────────────────────────

    def calendario(
        self, mes: date, hoy: date | None = None, en_plan: bool = False
    ) -> pd.DataFrame:
        """
        Devuelve los cargos fijos que tocan en el mes, con su estado.

        Cada cargo busca el movimiento que lo paga cerca de su fecha: un
        gasto confirmado de su categoría (o que lo nombre) por un monto
        parecido. Cada movimiento paga un solo cargo.

        Lo que vence en los primeros días del mes siguiente —hasta el día
        de anticipación, el 1 por defecto: la renta— se paga con el dinero
        de este mes, así que va aquí, marcado «adelantado». También en un
        mes que ya pasó: ahí sigue, pendiente mientras no se registre el
        pago y pagado cuando se registre.

        Parameters
        ----------
        mes : date
            Cualquier día del mes a mostrar.
        en_plan : bool
            Si es un mes que el plan proyecta. Entonces lo de sus primeros
            días no va, porque ya se pidió el mes anterior.

        Returns
        -------
        pandas.DataFrame
            Una fila por cargo que toca en el mes, ordenada por fecha. Los
            cargos sin próximo cobro van al final, con fecha vacía.
        """
        hoy = hoy or date.today()
        columnas = [
            "id",
            "servicio",
            "clase",
            "categoria",
            "cuenta",
            "monto",
            "fecha",
            "estado",
            "movimiento_id",
            "pagado_el",
            "en_sin_pagar",
            "adelantado",
            "posponible",
            "posponer_hasta",
        ]
        cargos = self._suscripciones.listar(solo_activas=True)
        if cargos.empty:
            return pd.DataFrame(columns=columnas)

        inicio, fin = _limites_del_mes(mes)
        dias = self.anticipacion_dias()
        siguiente = fin + timedelta(days=1)
        # Hasta qué día del mes siguiente se paga con el dinero de este.
        horizonte = _dia_del_mes(siguiente, dias) if dias > 0 else fin
        gastos = self._movimientos.listar(
            desde=inicio - timedelta(days=VENTANA_DIAS),
            hasta=max(fin, horizonte) + timedelta(days=VENTANA_DIAS),
            tipos=[str(TipoMovimiento.GASTO)],
            estado=str(EstadoMovimiento.CONFIRMADO),
        )

        filas = []
        for cargo in cargos.itertuples():
            base = {
                "id": int(cargo.id),
                "servicio": cargo.servicio,
                "clase": cargo.clase,
                "categoria": cargo.categoria,
                "cuenta": cargo.cuenta,
                "monto": float(cargo.costo_por_cobro),
                "categoria_id": cargo.categoria_id,
                "subcategoria_id": cargo.subcategoria_id,
                "posponible": bool(cargo.posponible),
                "posponer_hasta": (
                    cargo.posponer_hasta if pd.notna(cargo.posponer_hasta) else None
                ),
            }
            if pd.isna(cargo.proximo_cobro):
                filas.append(base | {"fecha": None, "adelantado": False})
                continue

            referencia = cargo.proximo_cobro.date()
            del_mes = cobro_en_mes(
                referencia, cargo.frecuencia, inicio.year, inicio.month
            )
            # En el plan, lo de los primeros días ya se pidió el mes anterior.
            if del_mes is not None and not (en_plan and del_mes.day <= dias):
                filas.append(base | {"fecha": del_mes, "adelantado": False})

            proximo = cobro_en_mes(
                referencia, cargo.frecuencia, siguiente.year, siguiente.month
            )
            if proximo is not None and proximo <= horizonte:
                filas.append(base | {"fecha": proximo, "adelantado": True})

        # Los de fecha más temprana escogen primero: si dos cargos podrían
        # quedarse con el mismo movimiento, se lo lleva el que vencía antes.
        filas.sort(key=lambda f: (f["fecha"] is None, f["fecha"] or fin, f["servicio"]))
        usados: set[int] = set()
        for fila in filas:
            pago = None
            if fila["fecha"] is not None:
                pago = _buscar_pago(fila, gastos, usados)
            if pago is None:
                fila |= {
                    "estado": str(EstadoPago.segun_fecha(fila["fecha"], hoy))
                    if fila["fecha"]
                    else "Sin fecha",
                    "movimiento_id": None,
                    "pagado_el": None,
                    "en_sin_pagar": False,
                }
                continue

            usados.add(int(pago["id"]))
            pagado = pd.notna(pago["fecha_pago"])
            fila |= {
                "estado": str(
                    EstadoPago.PAGADO
                    if pagado
                    else EstadoPago.segun_fecha(fila["fecha"], hoy)
                ),
                "movimiento_id": int(pago["id"]),
                "pagado_el": pago["fecha_pago"].date() if pagado else None,
                # Registrado pero sin pagar: ya está entre los gastos por
                # pagar, y ahí se cuenta.
                "en_sin_pagar": not pagado,
            }

        return pd.DataFrame(filas, columns=columnas)

    # ── Lo que hay que pagar ─────────────────────────────

    def por_pagar(
        self, mes: date, hoy: date | None = None, detalle: bool = False
    ) -> pd.DataFrame:
        """
        Junta en una lista todo lo que hay que pagar, lo vencido primero.

        Parameters
        ----------
        detalle : bool
            Si es True, deja además las columnas con que el plan de pagos
            identifica cada concepto (`clave`, `cuenta_id`, `posponible`).

        Returns
        -------
        pandas.DataFrame
            `concepto`, `origen`, `cuenta`, `monto`, `fecha_limite`,
            `estado`: los cargos fijos del mes aún sin movimiento, lo
            exigible de cada deuda y los gastos registrados sin pagar.
        """
        hoy = hoy or date.today()
        filas = []

        calendario = self.calendario(mes, hoy)
        if not calendario.empty:
            pendientes = calendario[
                (calendario["estado"] != str(EstadoPago.PAGADO))
                & ~calendario["en_sin_pagar"].astype(bool)
            ]
            for cargo in pendientes.itertuples():
                filas.append(
                    {
                        "concepto": cargo.servicio,
                        "origen": cargo.clase,
                        "cuenta": cargo.cuenta,
                        "monto": float(cargo.monto),
                        "fecha_limite": cargo.fecha,
                        "estado": cargo.estado,
                        "clave": f"fijo:{cargo.id}:{cargo.fecha}",
                        "cuenta_id": None,
                        "posponible": bool(cargo.posponible),
                        "tope": cargo.posponer_hasta,
                    }
                )

        exigibles = self._patrimonio.exigibles(hoy)
        if not exigibles.empty:
            for deuda in exigibles[exigibles["pendiente"] > 0].itertuples():
                filas.append(
                    {
                        "concepto": f"Pago de {deuda.cuenta}"
                        + (f" ({deuda.nota})" if deuda.nota else ""),
                        "origen": DEUDA_EXIGIBLE,
                        "cuenta": deuda.cuenta,
                        "monto": float(deuda.pendiente),
                        "fecha_limite": deuda.fecha_limite,
                        "estado": deuda.estado,
                        "clave": f"exigible:{deuda.cuenta_id}",
                        "cuenta_id": int(deuda.cuenta_id),
                        "posponible": None,
                        "tope": None,
                    }
                )
            for saldo in _saldos_de_tarjeta(exigibles):
                filas.append(
                    {
                        "concepto": f"Resto del saldo de {saldo['cuenta']}",
                        "origen": SALDO_TARJETA,
                        "cuenta": saldo["cuenta"],
                        "monto": saldo["monto"],
                        "fecha_limite": saldo["fecha_limite"],
                        "estado": str(EstadoPago.POR_PAGAR),
                        "clave": f"tarjeta:{saldo['cuenta_id']}",
                        "cuenta_id": saldo["cuenta_id"],
                        "posponible": None,
                        "tope": None,
                    }
                )

        sin_pagar = self._movimientos.por_pagar()
        if not sin_pagar.empty:
            for gasto in sin_pagar.itertuples():
                filas.append(
                    {
                        "concepto": gasto.descripcion or gasto.categoria,
                        "origen": GASTO_SIN_PAGAR,
                        "cuenta": gasto.cuenta,
                        "monto": float(gasto.monto),
                        "fecha_limite": None,
                        "estado": str(EstadoPago.POR_PAGAR),
                        "clave": f"sinpagar:{gasto.id}",
                        "cuenta_id": None,
                        "posponible": True,
                        "tope": None,
                    }
                )

        publicas = ["concepto", "origen", "cuenta", "monto", "fecha_limite", "estado"]
        internas = ["clave", "cuenta_id", "posponible", "tope"]
        df = pd.DataFrame(filas, columns=[*publicas, *internas])
        if not detalle:
            df = df[publicas]
        if df.empty:
            return df

        urgencia = {
            str(EstadoPago.VENCIDO): 0,
            str(EstadoPago.VENCE_HOY): 1,
            str(EstadoPago.POR_PAGAR): 2,
        }
        df["_orden"] = df["estado"].map(urgencia).fillna(3)
        df["_fecha"] = pd.to_datetime(df["fecha_limite"], errors="coerce")
        return (
            df.sort_values(["_orden", "_fecha"], na_position="last")
            .drop(columns=["_orden", "_fecha"])
            .reset_index(drop=True)
        )

    # ── Ingresos ─────────────────────────────────────────
    #
    # El ingreso del mes arranca de los ingresos fijos —la nómina—, que
    # están aunque todavía no se registren; cuando llega el movimiento que
    # trae uno, manda su monto real. Lo demás que se registre se suma
    # encima, salvo lo que entra a una cuenta restringida, que no se
    # puede usar para pagar.

    def ingresos_fijos(self, solo_activos: bool = False) -> pd.DataFrame:
        """Devuelve el catálogo de ingresos fijos."""
        return self._ingresos_fijos.listar(solo_activos=solo_activos)

    def crear_ingreso_fijo(
        self,
        concepto: str,
        monto: float,
        dia: int,
        categoria_id: int | None = None,
        cuenta_id: int | None = None,
        texto: str = "",
    ) -> int:
        """
        Da de alta un ingreso fijo, como una quincena de la nómina.

        `texto` reconoce el movimiento que lo trae por su descripción o su
        concepto del banco —«NOMINA»—; sin él, se reconoce por categoría y
        cuenta.
        """
        return self._ingresos_fijos.crear(
            **_validar_ingreso_fijo(
                concepto, monto, dia, categoria_id, cuenta_id, texto, activo=True
            )
        )

    def actualizar_ingreso_fijo(self, ingreso_id: int, **campos: object) -> None:
        """Cambia los campos indicados de un ingreso fijo."""
        actual = self._ingresos_fijos.listar()
        fila = actual[actual["id"] == ingreso_id]
        if fila.empty:
            raise ValueError(f"No existe el ingreso fijo {ingreso_id}.")

        datos = {
            "concepto": fila.iloc[0]["concepto"],
            "monto": float(fila.iloc[0]["monto"]),
            "dia": int(fila.iloc[0]["dia"]),
            "categoria_id": _entero_o_nulo(fila.iloc[0]["categoria_id"]),
            "cuenta_id": _entero_o_nulo(fila.iloc[0]["cuenta_id"]),
            "texto": fila.iloc[0]["texto"],
            "activo": bool(fila.iloc[0]["activo"]),
        }
        for campo in campos:
            if campo not in datos:
                raise ValueError(f"El ingreso fijo no tiene el campo «{campo}».")
        datos.update(campos)

        self._ingresos_fijos.actualizar(ingreso_id, **_validar_ingreso_fijo(**datos))

    def eliminar_ingreso_fijo(self, ingreso_id: int) -> None:
        """Elimina un ingreso fijo."""
        self._ingresos_fijos.eliminar(ingreso_id)

    def ingresos_fijos_del_mes(
        self, mes: date, hoy: date | None = None
    ) -> pd.DataFrame:
        """
        Devuelve cada ingreso fijo del mes: cuánto se espera y si ya llegó.

        Returns
        -------
        pandas.DataFrame
            `concepto`, `fecha`, `esperado`, `recibido` (el monto real, si
            ya llegó), `monto` (el que cuenta: el real o, si no, el
            esperado), `estado` y `movimiento_id`.
        """
        return self._ingresos_del_mes(mes, hoy or date.today())[0]

    def ingresos(self, mes: date, hoy: date | None = None) -> pd.DataFrame:
        """
        Devuelve los ingresos registrados en el mes y qué papel juega cada uno.

        La columna `cuenta_como` lo dice: trae un ingreso fijo, es otro
        ingreso que se suma, falta por recibir, o no cuenta porque entró a
        una cuenta restringida.
        """
        return self._ingresos_del_mes(mes, hoy or date.today())[1]

    def historial_ingreso_fijo(
        self, ingreso_id: int, meses: int = 6, hoy: date | None = None
    ) -> pd.DataFrame:
        """
        Devuelve lo que trajo un ingreso fijo en los meses anteriores.

        Sirve para ver si el monto esperado sigue siendo el real: una fila
        por mes cerrado en que llegó, con lo que llegó.
        """
        hoy = hoy or date.today()
        filas = []
        for atras in range(1, meses + 1):
            indice = hoy.year * 12 + hoy.month - 1 - atras
            mes = date(indice // 12, indice % 12 + 1, 1)
            fijos = self._ingresos_del_mes(mes, hoy)[0]
            fila = fijos[
                (fijos["id"] == ingreso_id)
                & (fijos["estado"] == str(EstadoIngreso.RECIBIDO))
            ]
            if not fila.empty:
                filas.append(
                    {
                        "fecha": fila.iloc[0]["fecha"],
                        "recibido": float(fila.iloc[0]["recibido"]),
                    }
                )

        return pd.DataFrame(filas, columns=["fecha", "recibido"])

    def _ingresos_del_mes(
        self, mes: date, hoy: date
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Empareja los ingresos fijos del mes con lo registrado."""
        inicio, fin = _limites_del_mes(mes)
        columnas_registrados = [
            "id",
            "fecha",
            "descripcion",
            "categoria",
            "cuenta",
            "monto",
            "estado",
            "cuenta_como",
            "ingreso_fijo",
        ]
        columnas_fijos = [
            "id",
            "concepto",
            "fecha",
            "esperado",
            "recibido",
            "monto",
            "estado",
            "cuenta",
            "movimiento_id",
        ]

        registrados = self._movimientos.listar(
            desde=inicio, hasta=fin, tipos=[str(TipoMovimiento.INGRESO)]
        )
        if registrados.empty:
            registrados = pd.DataFrame(
                columns=[*columnas_registrados, "cuenta_id", "descripcion_banco"]
            )
        registrados = registrados.sort_values("fecha", ascending=False).reset_index(
            drop=True
        )
        restringidas = self._patrimonio.cuentas_restringidas()
        registrados["restringida"] = registrados["cuenta_id"].isin(restringidas)
        confirmado = registrados["estado"] == str(EstadoMovimiento.CONFIRMADO)
        registrados["cuenta_como"] = COMO_OTRO
        registrados.loc[~confirmado, "cuenta_como"] = COMO_PENDIENTE
        registrados.loc[registrados["restringida"], "cuenta_como"] = COMO_RESTRINGIDA
        registrados["ingreso_fijo"] = ""

        filas = []
        for fijo in self._ingresos_fijos.listar(solo_activos=True).itertuples():
            ultimo = calendar.monthrange(inicio.year, inicio.month)[1]
            filas.append(
                {
                    "id": int(fijo.id),
                    "concepto": fijo.concepto,
                    "fecha": date(
                        inicio.year, inicio.month, min(int(fijo.dia), ultimo)
                    ),
                    "esperado": float(fijo.monto),
                    "cuenta": fijo.cuenta,
                    "_fijo": fijo,
                }
            )
        filas.sort(key=lambda f: (f["fecha"], f["concepto"]))

        # Primero contra lo confirmado; lo que quede sin llegar, contra lo
        # registrado como pendiente, para no contar dos veces la nómina que
        # alguien ya apuntó como proyección.
        usados: set[int] = set()
        for pasada in (str(EstadoMovimiento.CONFIRMADO), None):
            for fila in filas:
                if "movimiento_id" in fila:
                    continue
                elegibles = registrados[
                    ~registrados["restringida"] & ~registrados.index.isin(usados)
                ]
                elegibles = elegibles[
                    elegibles["estado"] == pasada
                    if pasada
                    else elegibles["estado"] != str(EstadoMovimiento.CONFIRMADO)
                ]
                indice = _buscar_ingreso(fila["_fijo"], fila["fecha"], elegibles)
                if indice is None:
                    continue
                usados.add(indice)
                movimiento = registrados.loc[indice]
                registrados.loc[indice, "cuenta_como"] = COMO_FIJO
                registrados.loc[indice, "ingreso_fijo"] = fila["concepto"]
                llego = pasada is not None
                fila |= {
                    "movimiento_id": int(movimiento["id"]),
                    "recibido": float(movimiento["monto"]) if llego else None,
                    "monto": float(movimiento["monto"]),
                    "estado": str(
                        EstadoIngreso.RECIBIDO
                        if llego
                        else _estado_ingreso(fila["fecha"], hoy)
                    ),
                }

        for fila in filas:
            fila.pop("_fijo")
            if "movimiento_id" not in fila:
                fila |= {
                    "movimiento_id": None,
                    "recibido": None,
                    "monto": fila["esperado"],
                    "estado": str(_estado_ingreso(fila["fecha"], hoy)),
                }

        return (
            pd.DataFrame(filas, columns=columnas_fijos),
            registrados[columnas_registrados],
        )

    # ── Las cifras de arriba ─────────────────────────────

    def resumen(self, mes: date, hoy: date | None = None) -> ResumenPagos:
        """
        Devuelve lo que entra en el mes, lo que hay hoy y lo que se debe.

        El ingreso del mes son los ingresos fijos —con su monto real si ya
        llegaron, el esperado si no— más lo demás que se registre. Lo que
        entra a una cuenta restringida no cuenta.
        """
        hoy = hoy or date.today()
        resumen = ResumenPagos(disponible=self._patrimonio.activos_liquidos(hoy))

        fijos, registrados = self._ingresos_del_mes(mes, hoy)
        llego = fijos["estado"] == str(EstadoIngreso.RECIBIDO)
        resumen.recibido = round(
            float(fijos.loc[llego, "monto"].sum())
            + float(
                registrados.loc[registrados["cuenta_como"] == COMO_OTRO, "monto"].sum()
            ),
            2,
        )
        resumen.por_recibir = round(
            float(fijos.loc[~llego, "monto"].sum())
            + float(
                registrados.loc[
                    registrados["cuenta_como"] == COMO_PENDIENTE, "monto"
                ].sum()
            ),
            2,
        )

        calendario = self.calendario(mes, hoy)
        if not calendario.empty:
            pagado = calendario["estado"] == str(EstadoPago.PAGADO)
            en_sin_pagar = calendario["en_sin_pagar"].astype(bool)
            resumen.fijos_total = round(float(calendario["monto"].sum()), 2)
            resumen.fijos_pagados = round(
                float(calendario.loc[pagado, "monto"].sum()), 2
            )
            resumen.fijos_pendientes = round(
                float(calendario.loc[~pagado & ~en_sin_pagar, "monto"].sum()), 2
            )

        exigibles = self._patrimonio.exigibles(hoy)
        if not exigibles.empty:
            resumen.exigible = round(float(exigibles["pendiente"].sum()), 2)
            resumen.saldo_tarjetas = round(
                sum(saldo["monto"] for saldo in _saldos_de_tarjeta(exigibles)), 2
            )

        resumen.sin_pagar = round(self._movimientos.total_por_pagar(), 2)

        return resumen

    # ── Plan de pagos ────────────────────────────────────
    #
    # Cuando no alcanza para todo: qué pagar ahora y qué dejar para
    # después, y cómo quedan los meses que vienen con esa decisión. Las
    # obligaciones del primer mes son exactamente las de «Por pagar»; las
    # de los siguientes, los gastos fijos, las parcialidades y el gasto
    # del día a día.

    def anticipacion_dias(self) -> int:
        """Hasta qué día del mes siguiente se paga con el dinero de este."""
        valor = self._plan.valor(CLAVE_ANTICIPACION)
        try:
            return int(valor) if valor is not None else ANTICIPACION_DIAS
        except ValueError:
            return ANTICIPACION_DIAS

    def fijar_anticipacion(self, dias: int) -> None:
        """
        Fija hasta qué día del mes siguiente se paga con el dinero de este.

        Con 1, la renta del día 1; con 0, cada cargo en su propio mes.
        """
        if not 0 <= int(dias) <= 31:
            raise ValueError("La anticipación va de 0 a 31 días.")
        self._plan.guardar_valor(
            CLAVE_ANTICIPACION,
            str(int(dias)),
            "Hasta qué día del mes siguiente se paga con el dinero de este.",
        )

    # ── Retiros simulados del fondo de ahorro ────────────
    #
    # Un ingreso que sólo existe en el plan: sacar dinero de una cuenta
    # restringida para ver cómo se va pagando todo mes a mes. Se pide
    # antes del día 22 y entra el 30; entre todos los meses no pueden
    # pasar de lo que hay en el fondo.

    def fondos(self, hoy: date | None = None) -> pd.DataFrame:
        """Las cuentas restringidas de las que se puede simular un retiro."""
        hoy = hoy or date.today()
        saldos = self._patrimonio.saldos(hoy)
        if saldos.empty:
            return pd.DataFrame(columns=["cuenta_id", "cuenta", "saldo"])
        fondos = saldos[saldos["restringida"]]
        return fondos.assign(saldo=fondos["saldo_visto"].clip(lower=0).round(2))[
            ["cuenta_id", "cuenta", "saldo"]
        ].reset_index(drop=True)

    def retiro_simulado(self, mes: date, cuenta_id: int) -> float:
        """Lo que se simula retirar de un fondo en un mes."""
        return self._plan.retiros().get((f"{mes:%Y-%m}", int(cuenta_id)), 0.0)

    def puede_pedirse_retiro(self, mes: date, hoy: date | None = None) -> bool:
        """
        Si el retiro de ese mes todavía se puede pedir.

        Sólo antes del día 22 del propio mes; un mes que ya pasó, no.
        """
        hoy = hoy or date.today()
        inicio = date(mes.year, mes.month, 1)
        actual = date(hoy.year, hoy.month, 1)
        if inicio < actual:
            return False
        return inicio > actual or hoy.day < DIA_LIMITE_RETIRO

    def fijar_retiro(
        self, mes: date, cuenta_id: int, monto: float, hoy: date | None = None
    ) -> None:
        """
        Simula retirar `monto` de un fondo en un mes; cero lo quita.

        Raises
        ------
        ValueError
            Si la cuenta no es un fondo, el monto es negativo, ya no se
            puede pedir ese mes o, con los demás meses, pasa del saldo.
        """
        hoy = hoy or date.today()
        monto = round(float(monto), 2)
        fondos = self.fondos(hoy).set_index("cuenta_id")
        if int(cuenta_id) not in fondos.index:
            raise ValueError(
                "Sólo se simulan retiros de una cuenta restringida, como el "
                "fondo de ahorro."
            )
        if monto < 0:
            raise ValueError("El retiro va en positivo.")
        if monto > 0 and not self.puede_pedirse_retiro(mes, hoy):
            raise ValueError(
                f"El retiro de ese mes ya no se puede pedir: es antes del día "
                f"{DIA_LIMITE_RETIRO}."
            )

        inicio = date(mes.year, mes.month, 1)
        retiros = {
            m: valor
            for (m, cuenta), valor in self._plan.retiros().items()
            if cuenta == int(cuenta_id)
        }
        retiros[f"{inicio:%Y-%m}"] = monto
        ultimo = max(
            [inicio]
            + [date(int(m[:4]), int(m[5:]), 1) for m, valor in retiros.items() if valor]
        )
        for fila in self._proyeccion_fondo(int(cuenta_id), ultimo, hoy, retiros):
            if fila["retira"] > fila["al_pedir"] + 0.005:
                raise ValueError(
                    f"En {_nombre_de_mes(fila['mes'])} el fondo tendría "
                    f"{fila['al_pedir']:,.2f} y se retirarían {fila['retira']:,.2f}."
                )

        self._plan.guardar_retiro(inicio, int(cuenta_id), monto)

    def fondo_restante(
        self, hasta: date | None = None, hoy: date | None = None
    ) -> float:
        """
        Lo que habría en los fondos al cerrar `hasta`, según la simulación.

        El saldo de hoy, más las aportaciones simuladas, menos los retiros
        simulados y, en diciembre, menos la liquidación del año, de este
        mes a `hasta` (diciembre, si no se dice).
        """
        hoy = hoy or date.today()
        hasta = hasta or date(hoy.year, 12, 1)
        total = 0.0
        for fondo in self.fondos(hoy).itertuples():
            proyeccion = self._proyeccion_fondo(int(fondo.cuenta_id), hasta, hoy)
            total += proyeccion[-1]["final"] if proyeccion else float(fondo.saldo)
        return round(total, 2)

    def liquidaciones_del_mes(
        self, mes: date, hoy: date | None = None
    ) -> list[tuple[str, float]]:
        """
        Lo que un fondo paga en su liquidación, si cae en ese mes.

        En diciembre el fondo de ahorro entrega lo que no se retiró en el
        año: su saldo con las aportaciones y retiros simulados. Entra como
        ingreso el día de liquidación y el fondo queda en cero.
        """
        hoy = hoy or date.today()
        inicio = date(mes.year, mes.month, 1)
        nombres = self._patrimonio.nombres_de_cuentas()
        resultado = []
        for fondo in self.fondos(hoy).itertuples():
            proyeccion = self._proyeccion_fondo(int(fondo.cuenta_id), inicio, hoy)
            if proyeccion and proyeccion[-1]["mes"] == inicio:
                liquida = proyeccion[-1]["liquida"]
                if liquida > 0:
                    resultado.append(
                        (nombres.get(int(fondo.cuenta_id), "fondo"), round(liquida, 2))
                    )
        return resultado

    def _proyeccion_fondo(
        self,
        cuenta_id: int,
        hasta: date,
        hoy: date,
        retiros: dict[str, float] | None = None,
    ) -> list[dict[str, object]]:
        """
        El fondo mes a mes, del mes en curso a `hasta`, según la simulación.

        Cada mes: lo que había al pedir el retiro —antes del 22—, lo que se
        retira, lo que se aporta —el 30, tarde para ese retiro—, lo que se
        liquida en diciembre y con cuánto cierra. Es la única cuenta del
        fondo: el tope de los retiros, lo que queda y la liquidación salen
        de aquí.
        """
        if retiros is None:
            retiros = {
                m: valor
                for (m, cuenta), valor in self._plan.retiros().items()
                if cuenta == cuenta_id
            }
        fondos = self.fondos(hoy).set_index("cuenta_id")
        hay = (
            float(fondos.loc[cuenta_id, "saldo"]) if cuenta_id in fondos.index else 0.0
        )
        fin = date(hasta.year, hasta.month, 1)
        cada = date(hoy.year, hoy.month, 1)
        filas: list[dict[str, object]] = []
        while cada <= fin:
            al_pedir = hay
            retira = float(retiros.get(f"{cada:%Y-%m}", 0.0))
            aporta = self._aportado(cada, cuenta_id, hoy)
            hay = hay - retira + aporta
            liquida = 0.0
            if (
                cada.month == MES_LIQUIDACION_FONDO
                and _dia_del_mes(cada, DIA_LIQUIDACION_FONDO) > hoy
            ):
                liquida, hay = max(hay, 0.0), 0.0
            filas.append(
                {
                    "mes": cada,
                    "al_pedir": round(al_pedir, 2),
                    "retira": retira,
                    "aporta": aporta,
                    "liquida": round(liquida, 2),
                    "final": round(hay, 2),
                }
            )
            cada = _desplazar_mes(cada, 1)
        return filas

    # ── Ingresos y aportaciones simulados ────────────────
    #
    # Lo que el plan supone que pasará y aún no pasa: el aguinaldo, la
    # aportación de cada mes al fondo de ahorro. Viven en su propia tabla
    # y nunca se registran como movimientos. En el mes en curso sólo
    # cuentan los que caen después de hoy: lo de antes ya pasó —o no— y
    # está registrado, o no, de verdad.

    def simulados(self) -> pd.DataFrame:
        """Los ingresos y aportaciones simulados, con el nombre del fondo."""
        nombres = self._patrimonio.nombres_de_cuentas()
        filas = [
            fila
            | {
                "mes": (
                    date(int(fila["mes"][:4]), int(fila["mes"][5:]), 1)
                    if fila["mes"]
                    else None
                ),
                "cuenta": nombres.get(fila["cuenta_id"], "")
                if fila["cuenta_id"]
                else "",
            }
            for fila in self._plan.simulados()
        ]
        return pd.DataFrame(
            filas,
            columns=[
                "id",
                "tipo",
                "concepto",
                "monto",
                "dia",
                "mes",
                "cuenta_id",
                "cuenta",
            ],
        )

    def crear_simulado(
        self,
        tipo: str,
        concepto: str,
        monto: float,
        dia: int,
        mes: date | None = None,
        cuenta_id: int | None = None,
    ) -> int:
        """
        Da de alta un ingreso o una aportación simulados.

        Sin `mes`, se repite cada mes. Una aportación va a un fondo —una
        cuenta restringida—; un ingreso, a lo que hay para pagar.

        Raises
        ------
        ValueError
            Si el tipo no existe, falta el concepto, el monto no es
            positivo, el día no va del 1 al 31 o la aportación no es a una
            cuenta restringida.
        """
        if tipo not in (SIMULADO_INGRESO, SIMULADO_APORTACION):
            raise ValueError("Un simulado es un ingreso o una aportación.")
        concepto = (concepto or "").strip()
        if not concepto:
            raise ValueError("Ponle un concepto.")
        if float(monto) <= 0:
            raise ValueError("El monto va en positivo.")
        if not 1 <= int(dia) <= 31:
            raise ValueError("El día va del 1 al 31.")
        if tipo == SIMULADO_APORTACION:
            if cuenta_id is None or int(cuenta_id) not in (
                self._patrimonio.cuentas_restringidas()
            ):
                raise ValueError(
                    "Una aportación va a una cuenta restringida, como el fondo "
                    "de ahorro."
                )
        else:
            cuenta_id = None

        return self._plan.crear_simulado(
            tipo,
            concepto,
            round(float(monto), 2),
            int(dia),
            date(mes.year, mes.month, 1) if mes else None,
            int(cuenta_id) if cuenta_id is not None else None,
        )

    def eliminar_simulado(self, simulado_id: int) -> None:
        """Quita un ingreso o una aportación simulados."""
        self._plan.eliminar_simulado(simulado_id)

    def simulados_del_mes(
        self, mes: date, tipo: str, hoy: date | None = None
    ) -> list[dict[str, object]]:
        """
        Los simulados de un tipo que caen en un mes, con su fecha.

        En el mes en curso, sólo los que caen después de hoy.
        """
        hoy = hoy or date.today()
        inicio = date(mes.year, mes.month, 1)
        resultado = []
        for fila in self.simulados().itertuples():
            if fila.tipo != tipo or (fila.mes is not None and fila.mes != inicio):
                continue
            fecha = _dia_del_mes(inicio, int(fila.dia))
            if fecha <= hoy:
                continue
            resultado.append(
                {
                    "concepto": fila.concepto,
                    "monto": float(fila.monto),
                    "fecha": fecha,
                    "cuenta_id": fila.cuenta_id,
                    "cuenta": fila.cuenta,
                }
            )
        return resultado

    def _ingreso_simulado(self, mes: date, hoy: date) -> float:
        """Lo que se simula que entra en un mes, para pagar."""
        return sum(
            i["monto"] for i in self.simulados_del_mes(mes, SIMULADO_INGRESO, hoy)
        )

    def _aportado(self, mes: date, cuenta_id: int, hoy: date) -> float:
        """Lo que se simula aportar a un fondo en un mes."""
        return sum(
            a["monto"]
            for a in self.simulados_del_mes(mes, SIMULADO_APORTACION, hoy)
            if a["cuenta_id"] is not None and int(a["cuenta_id"]) == cuenta_id
        )

    def acciones_del_plan(
        self, plan: list[MesDelPlan], hoy: date | None = None
    ) -> pd.DataFrame:
        """
        Lo que se decidió para armar el plan, mes por mes.

        Los retiros simulados del fondo, lo que se decidió a mano —pagar,
        pagar una parte, posponer— y los faltantes que quedaron como deuda
        nueva. Lo que sigue la sugerencia no aparece: no es una acción.
        """
        hoy = hoy or date.today()
        filas: list[dict[str, object]] = []
        for indice, mes in enumerate(plan):
            siguiente = plan[indice + 1].mes if indice + 1 < len(plan) else None
            para = f" en {_nombre_de_mes(siguiente)}" if siguiente else " después"

            for tipo, accion in (
                (SIMULADO_INGRESO, "Ingreso simulado"),
                (SIMULADO_APORTACION, "Aportación simulada al fondo"),
            ):
                for simulado in self.simulados_del_mes(mes.mes, tipo, hoy):
                    filas.append(
                        {
                            "mes": mes.mes,
                            "accion": accion,
                            "concepto": simulado["concepto"],
                            "monto": simulado["monto"],
                            "detalle": f"El {simulado['fecha']:%d/%m}"
                            + (
                                f", a {simulado['cuenta']}"
                                if simulado["cuenta"]
                                else ""
                            ),
                        }
                    )
            liquidacion = _dia_del_mes(mes.mes, DIA_LIQUIDACION_FONDO)
            for cuenta, monto in self.liquidaciones_del_mes(mes.mes, hoy):
                filas.append(
                    {
                        "mes": mes.mes,
                        "accion": "Liquidación del fondo (simulado)",
                        "concepto": cuenta,
                        "monto": monto,
                        "detalle": f"Lo que no retiraste; entra el {liquidacion:%d/%m}",
                    }
                )
            abono = _dia_del_mes(mes.mes, DIA_ABONO_RETIRO)
            for cuenta, monto in self._retiros_del_mes(mes.mes):
                filas.append(
                    {
                        "mes": mes.mes,
                        "accion": "Retirar del fondo (simulado)",
                        "concepto": cuenta,
                        "monto": monto,
                        "detalle": f"Entra el {abono:%d/%m}",
                    }
                )
            decididas = self._plan.ajustes(mes.mes)
            for r in mes.resoluciones:
                if not r.manual or r.obligacion.clave not in decididas:
                    continue
                decision, monto_decidido = decididas[r.obligacion.clave]
                # La acción es lo que se decidió, no lo que resultó: pagar
                # una parte sin dinero sigue siendo pagar una parte.
                if decision == Decision.POSPONER:
                    accion, monto, detalle = "Posponer", r.pospuesto, f"Se paga{para}"
                elif decision == Decision.PARCIAL:
                    accion = "Pagar una parte"
                    monto = float(monto_decidido or 0.0)
                    resto = round(r.obligacion.monto - monto, 2)
                    detalle = f"El resto ({resto:,.2f}) se paga{para}" if resto else ""
                else:
                    accion, monto, detalle = "Pagar", r.obligacion.monto, ""
                if r.falta > 0:
                    detalle = (detalle + " · " if detalle else "") + (
                        f"no alcanzan {r.falta:,.2f}, van a la deuda nueva"
                    )
                filas.append(
                    {
                        "mes": mes.mes,
                        "accion": accion,
                        "concepto": r.obligacion.concepto,
                        "monto": monto,
                        "detalle": detalle,
                    }
                )
            if mes.falta > 0:
                deuda = faltante_de(mes)
                filas.append(
                    {
                        "mes": mes.mes,
                        "accion": "Queda como deuda nueva",
                        "concepto": deuda.concepto,
                        "monto": mes.falta,
                        "detalle": f"Lo que no alcanzó; se paga{para}",
                    }
                )

        return pd.DataFrame(
            filas, columns=["mes", "accion", "concepto", "monto", "detalle"]
        )

    def _retiros_del_mes(self, mes: date) -> list[tuple[str, float]]:
        """Los retiros simulados de un mes, como (cuenta, monto)."""
        clave_mes = f"{mes:%Y-%m}"
        nombres = self._patrimonio.nombres_de_cuentas()
        return [
            (nombres.get(cuenta, "fondo"), monto)
            for (mes_retiro, cuenta), monto in self._plan.retiros().items()
            if mes_retiro == clave_mes
        ]

    def decidir(
        self,
        mes: date,
        clave: str,
        decision: str | None,
        monto: float | None = None,
    ) -> None:
        """
        Guarda qué hacer con un concepto del mes; None vuelve a la sugerencia.

        Raises
        ------
        ValueError
            Si la decisión no existe o un pago parcial no dice cuánto.
        """
        if decision is None:
            self._plan.quitar_ajuste(mes, clave)
            return

        decision = Decision(decision)
        if decision == Decision.PARCIAL and (monto is None or float(monto) < 0):
            raise ValueError("Di cuánto vas a pagar de ese concepto.")
        self._plan.guardar_ajuste(
            mes,
            clave,
            str(decision),
            float(monto) if decision == Decision.PARCIAL else None,
        )

    def decisiones(self, mes: date) -> dict[str, tuple[str, float | None]]:
        """Lo decidido a mano en el mes: {clave: (decisión, monto)}."""
        return self._plan.ajustes(date(mes.year, mes.month, 1))

    def olvidar_decisiones(self, mes: date) -> None:
        """Vuelve todo el mes a lo que sugiera el plan."""
        self._plan.quitar_ajustes(mes)

    def plan(self, mes: date, hoy: date | None = None) -> list[MesDelPlan]:
        """
        Arma el plan de pagos del mes y lo proyecta hasta la fecha tope.

        El primer mes parte de lo disponible hoy más lo que falta por
        recibir, y sus obligaciones son las de «Por pagar». Los siguientes
        suman los ingresos fijos y restan los gastos fijos y las
        parcialidades de los préstamos: sólo lo registrado o configurado,
        sin suponer cuánto se gastará además. Llega hasta la fecha
        tope más lejana de las deudas, o hasta diciembre si es antes, con
        al menos tres meses y a lo más doce.
        """
        hoy = hoy or date.today()
        inicio = date(mes.year, mes.month, 1)
        reglas = self._patrimonio.reglas_de_deudas().set_index("cuenta_id")

        pendientes = self.por_pagar(inicio, hoy, detalle=True)
        primeras = [_obligacion(fila, reglas) for fila in pendientes.to_dict("records")]

        topes = [o.tope for o in primeras if o.tope] + [
            t for t in reglas["posponer_hasta"] if pd.notna(t) and t
        ]
        # Hasta diciembre, que es con lo que se termina el año; más lejos
        # si una deuda se puede posponer más allá, y al menos tres meses.
        ultimo = max(
            [
                date(inicio.year, 12, 1),
                _desplazar_mes(inicio, 2),
                *(date(t.year, t.month, 1) for t in topes),
            ]
        )
        ultimo = min(ultimo, _desplazar_mes(inicio, 11))

        vistas = {o.clave for o in primeras}
        fijos_activos = self._ingresos_fijos.listar(solo_activos=True)
        ingreso_fijo = (
            float(fijos_activos["monto"].sum()) if not fijos_activos.empty else 0.0
        )
        resumen = self.resumen(inicio, hoy)
        entradas = [
            EntradaMes(
                inicio,
                resumen.por_recibir
                + sum(monto for _, monto in self._retiros_del_mes(inicio))
                + self._ingreso_simulado(inicio, hoy)
                + sum(monto for _, monto in self.liquidaciones_del_mes(inicio, hoy)),
                tuple(primeras),
            )
        ]

        # Lo que falta de cada préstamo después de lo que ya se pide este
        # mes: las parcialidades futuras no deben pasarse de ahí.
        exigibles = self._patrimonio.exigibles(hoy).set_index("cuenta_id")
        restante_prestamo = {
            int(cuenta_id): max(
                float(exigibles.loc[cuenta_id, "deuda"])
                - float(exigibles.loc[cuenta_id, "pendiente"]),
                0.0,
            )
            for cuenta_id in exigibles.index
        }

        siguiente = _desplazar_mes(inicio, 1)
        while siguiente <= ultimo:
            obligaciones: list[Obligacion] = []
            calendario = self.calendario(siguiente, hoy, en_plan=True)
            if not calendario.empty:
                for cargo in calendario.itertuples():
                    clave = f"fijo:{cargo.id}:{cargo.fecha}"
                    if (
                        clave in vistas
                        or cargo.estado == str(EstadoPago.PAGADO)
                        or bool(cargo.en_sin_pagar)
                    ):
                        continue
                    vistas.add(clave)
                    obligaciones.append(
                        Obligacion(
                            clave=clave,
                            concepto=cargo.servicio,
                            origen=cargo.clase,
                            monto=float(cargo.monto),
                            vence=cargo.fecha,
                            posponible=bool(cargo.posponible),
                            tope=(
                                cargo.posponer_hasta
                                if pd.notna(cargo.posponer_hasta)
                                else None
                            ),
                            parcial=False,
                        )
                    )

            for cuenta_id, regla in reglas.iterrows():
                if pd.isna(regla["pago_mensual"]) or pd.isna(regla["dia_pago"]):
                    continue
                vence = _dia_del_mes(siguiente, int(regla["dia_pago"]))
                # La que ya cuenta en lo exigible de hoy no se pide otra vez.
                ya_pedida = (
                    exigibles.loc[cuenta_id, "fecha_limite"]
                    if cuenta_id in exigibles.index
                    else None
                )
                if ya_pedida is not None and pd.notna(ya_pedida) and vence <= ya_pedida:
                    continue
                parcialidad = min(
                    float(regla["pago_mensual"]), restante_prestamo.get(cuenta_id, 0.0)
                )
                if parcialidad <= 0:
                    continue
                restante_prestamo[cuenta_id] -= parcialidad
                obligaciones.append(
                    _con_reglas_de_deuda(
                        Obligacion(
                            clave=f"parcialidad:{cuenta_id}:{siguiente:%Y-%m}",
                            concepto=f"Parcialidad de {regla['cuenta']}",
                            origen=DEUDA_EXIGIBLE,
                            monto=round(parcialidad, 2),
                            vence=vence,
                        ),
                        regla,
                    )
                )

            retiros = (
                sum(monto for _, monto in self._retiros_del_mes(siguiente))
                + self._ingreso_simulado(siguiente, hoy)
                + sum(monto for _, monto in self.liquidaciones_del_mes(siguiente, hoy))
            )
            entradas.append(
                EntradaMes(siguiente, ingreso_fijo + retiros, tuple(obligaciones))
            )
            siguiente = _desplazar_mes(siguiente, 1)

        ajustes = {
            entrada.mes: {
                clave: Ajuste(Decision(decision), monto)
                for clave, (decision, monto) in self._plan.ajustes(entrada.mes).items()
            }
            for entrada in entradas
        }
        return proyectar(resumen.disponible, entradas, ajustes)

    def flujo_del_plan(
        self, plan: list[MesDelPlan], hoy: date | None = None
    ) -> pd.DataFrame:
        """
        Pone el plan en el calendario: qué día entra y qué día sale cuánto.

        Cada pago va el día en que vence (o hoy, si ya venció o no tiene
        fecha; o al empezar su mes, en uno futuro). El gasto del día a día
        se reparte por semanas. Los ingresos fijos entran su día; en el
        mes en curso, sólo los que faltan por llegar. El saldo empieza en
        lo disponible hoy y acumula día con día: si baja de cero a mitad
        de mes, es que ese día no alcanza aunque el mes cierre bien.

        Returns
        -------
        pandas.DataFrame
            `fecha`, `concepto`, `origen`, `entra`, `sale` y `saldo`,
            ordenado por fecha, con lo que entra antes de lo que sale en
            un mismo día.
        """
        hoy = hoy or date.today()
        columnas = ["fecha", "concepto", "origen", "entra", "sale", "saldo"]
        if not plan:
            return pd.DataFrame(columns=columnas)

        eventos: list[dict[str, object]] = []

        primero = plan[0].mes
        fijos, registrados = self._ingresos_del_mes(primero, hoy)
        for fila in fijos.itertuples():
            if fila.estado != str(EstadoIngreso.RECIBIDO):
                eventos.append(
                    _entrada(max(fila.fecha, hoy), fila.concepto, float(fila.monto))
                )
        for fila in registrados[
            registrados["cuenta_como"] == COMO_PENDIENTE
        ].itertuples():
            eventos.append(
                _entrada(
                    max(pd.Timestamp(fila.fecha).date(), hoy),
                    fila.descripcion or "Ingreso por recibir",
                    float(fila.monto),
                )
            )

        for mes in plan:
            for simulado in self.simulados_del_mes(mes.mes, SIMULADO_INGRESO, hoy):
                eventos.append(
                    _entrada(
                        simulado["fecha"],
                        f"{simulado['concepto']} (simulado)",
                        simulado["monto"],
                    )
                )
            for cuenta, monto in self.liquidaciones_del_mes(mes.mes, hoy):
                eventos.append(
                    _entrada(
                        _dia_del_mes(mes.mes, DIA_LIQUIDACION_FONDO),
                        f"Liquidación de {cuenta} (simulado)",
                        monto,
                    )
                )
            for cuenta, monto in self._retiros_del_mes(mes.mes):
                eventos.append(
                    _entrada(
                        max(_dia_del_mes(mes.mes, DIA_ABONO_RETIRO), hoy),
                        f"Retiro de {cuenta} (simulado)",
                        monto,
                    )
                )

        catalogo = self._ingresos_fijos.listar(solo_activos=True)
        for mes in plan[1:]:
            for fijo in catalogo.itertuples():
                eventos.append(
                    _entrada(
                        _dia_del_mes(mes.mes, int(fijo.dia)),
                        fijo.concepto,
                        float(fijo.monto),
                    )
                )

        for mes in plan:
            desde = max(mes.mes, hoy)
            for resolucion in mes.resoluciones:
                if resolucion.pagado <= 0:
                    continue
                obligacion = resolucion.obligacion
                cuando = (
                    obligacion.vence
                    if obligacion.vence is not None and obligacion.vence >= desde
                    else desde
                )
                eventos.append(
                    {
                        "fecha": cuando,
                        "concepto": obligacion.concepto,
                        "origen": obligacion.origen,
                        "entra": 0.0,
                        "sale": resolucion.pagado,
                    }
                )

        df = pd.DataFrame(eventos, columns=columnas[:-1])
        if df.empty:
            return pd.DataFrame(columns=columnas)
        df[["entra", "sale"]] = df[["entra", "sale"]].astype(float)
        df["_orden"] = (df["sale"] > 0).astype(int)
        df = df.sort_values(["fecha", "_orden"], kind="stable").drop(columns="_orden")
        df["saldo"] = (
            plan[0].efectivo_inicial + (df["entra"] - df["sale"]).cumsum()
        ).round(2)
        return df.reset_index(drop=True)[columnas]

    def cierre_del_plan(self, plan: list[MesDelPlan]) -> dict[str, float]:
        """
        Con cuánto se termina el último mes del plan.

        `efectivo` es lo que queda en la mano; `deudas`, lo que se dejó sin
        pagar y seguiría debiéndose —pospuesto o que no alcanzó—; `neto`,
        la diferencia: con cuánto terminas de verdad. `intereses` suma lo
        que lo pospuesto generó a lo largo del plan.
        """
        if not plan:
            return {"efectivo": 0.0, "deudas": 0.0, "neto": 0.0, "intereses": 0.0}

        ultimo = plan[-1]
        deudas = round(ultimo.pospuesto + ultimo.falta, 2)
        return {
            "efectivo": ultimo.queda,
            "deudas": deudas,
            "neto": round(ultimo.queda - deudas, 2),
            "intereses": round(sum(m.intereses for m in plan), 2),
        }


def _saldos_de_tarjeta(exigibles: pd.DataFrame) -> list[dict[str, object]]:
    """
    Lo que debe cada tarjeta aparte de lo que ya es exigible.

    Lo exigible es lo del corte; el resto —lo comprado después— también se
    debe y se paga en el ciclo siguiente. Sin ciclo ni exigible, es todo
    el saldo. Juntos suman la deuda de la tarjeta, sin contar nada dos
    veces.
    """
    saldos = []
    tarjetas = exigibles[exigibles["tipo"] == str(TipoCuenta.CREDITO)]
    for tarjeta in tarjetas.itertuples():
        resto = round(float(tarjeta.deuda) - float(tarjeta.pendiente), 2)
        if resto < 0.005:
            continue

        limite = None
        if (
            pd.notna(tarjeta.dia_corte)
            and pd.notna(tarjeta.dia_pago)
            and tarjeta.corte is not None
        ):
            siguiente_corte = siguiente_dia(int(tarjeta.dia_corte), tarjeta.corte)
            limite = siguiente_dia(int(tarjeta.dia_pago), siguiente_corte)
        saldos.append(
            {
                "cuenta": tarjeta.cuenta,
                "cuenta_id": int(tarjeta.cuenta_id),
                "monto": resto,
                "fecha_limite": limite,
            }
        )

    return saldos


def _nombre_de_mes(mes: date) -> str:
    return f"{MESES_ES[mes.month - 1]} {mes.year}"


def _desplazar_mes(mes: date, meses: int) -> date:
    """El primer día del mes que queda `meses` antes o después de `mes`."""
    indice = mes.year * 12 + mes.month - 1 + meses
    return date(indice // 12, indice % 12 + 1, 1)


def _dia_del_mes(mes: date, dia: int) -> date:
    return date(
        mes.year, mes.month, min(dia, calendar.monthrange(mes.year, mes.month)[1])
    )


def _entrada(fecha: date, concepto: str, monto: float) -> dict[str, object]:
    return {
        "fecha": fecha,
        "concepto": concepto,
        "origen": "Ingreso",
        "entra": round(monto, 2),
        "sale": 0.0,
    }


def _con_reglas_de_deuda(obligacion: Obligacion, regla: pd.Series) -> Obligacion:
    """
    Le pone a una obligación de deuda sus reglas de posponer.

    Una tarjeta siempre se puede posponer —a su costo— y genera intereses
    aunque no se haya capturado su tasa. Un préstamo, sólo si tiene tasa
    o fecha tope; si no, se paga.
    """
    tasa = float(regla["tasa_anual"]) if pd.notna(regla["tasa_anual"]) else 0.0
    tope = regla["posponer_hasta"] if pd.notna(regla["posponer_hasta"]) else None
    es_tarjeta = regla["tipo"] == str(TipoCuenta.CREDITO)
    return replace(
        obligacion,
        posponible=es_tarjeta or tasa > 0 or tope is not None,
        tope=tope,
        tasa_anual=tasa,
        genera_interes=es_tarjeta,
        parcial=True,
    )


def _obligacion(fila: dict, reglas: pd.DataFrame) -> Obligacion:
    """
    Convierte una fila de «Por pagar» en una obligación del plan.

    Lo vencido va como atrasado —obligatorio— sólo si no se puede
    posponer: una deuda que puede esperar hasta diciembre sigue pudiendo
    aunque ya haya pasado su fecha límite de este mes.
    """
    base = Obligacion(
        clave=str(fila["clave"]),
        concepto=str(fila["concepto"]),
        origen=str(fila["origen"]),
        monto=float(fila["monto"]),
        vence=fila["fecha_limite"] if pd.notna(fila["fecha_limite"]) else None,
    )
    cuenta_id = fila.get("cuenta_id")
    if cuenta_id is not None and pd.notna(cuenta_id) and int(cuenta_id) in reglas.index:
        obligacion = _con_reglas_de_deuda(base, reglas.loc[int(cuenta_id)])
    else:
        es_fijo = str(fila["clave"]).startswith("fijo:")
        tope = fila.get("tope")
        obligacion = replace(
            base,
            posponible=bool(fila["posponible"]),
            tope=tope if tope is not None and pd.notna(tope) else None,
            parcial=not es_fijo,
        )

    vencida = fila["estado"] == str(EstadoPago.VENCIDO)
    return replace(obligacion, atrasada=vencida and not obligacion.posponible)


def _estado_ingreso(fecha: date, hoy: date) -> EstadoIngreso:
    """Un ingreso fijo que no ha llegado: por recibir, o atrasado si ya pasó."""
    return EstadoIngreso.ATRASADO if fecha < hoy else EstadoIngreso.POR_RECIBIR


def _buscar_ingreso(fijo, fecha: date, registrados: pd.DataFrame) -> int | None:
    """
    Busca, entre lo registrado, el movimiento que trae un ingreso fijo.

    Con `texto`, lo reconoce porque su descripción o su concepto del
    banco lo contienen; sin él, por su categoría. Si el ingreso fijo
    tiene cuenta, además tiene que haber entrado ahí. El monto no se
    exige: una nómina cambia de una quincena a otra. De los que cumplen,
    el más cercano a su día.
    """
    if registrados.empty:
        return None

    objetivo = pd.Timestamp(fecha)
    fechas = pd.to_datetime(registrados["fecha"])
    cerca = registrados[(fechas - objetivo).dt.days.abs() <= VENTANA_INGRESO_DIAS]
    if pd.notna(fijo.cuenta_id):
        cerca = cerca[cerca["cuenta_id"] == int(fijo.cuenta_id)]

    texto = str(fijo.texto or "").strip().lower()
    if texto:
        cerca = cerca[
            cerca["descripcion"].fillna("").str.lower().str.contains(texto, regex=False)
            | cerca["descripcion_banco"]
            .fillna("")
            .str.lower()
            .str.contains(texto, regex=False)
        ]
    elif pd.notna(fijo.categoria_id):
        cerca = cerca[cerca["categoria_id"] == int(fijo.categoria_id)]
    else:
        return None

    if cerca.empty:
        return None
    return (pd.to_datetime(cerca["fecha"]) - objetivo).abs().idxmin()


def _validar_ingreso_fijo(
    concepto: str,
    monto: float,
    dia: int,
    categoria_id: int | None,
    cuenta_id: int | None,
    texto: str,
    activo: bool,
) -> dict[str, object]:
    """Valida y normaliza los campos de un ingreso fijo."""
    concepto = (concepto or "").strip()
    if not concepto:
        raise ValueError("El ingreso fijo necesita un concepto.")
    if float(monto) < 0:
        raise ValueError("El monto de un ingreso fijo va en positivo.")
    if not 1 <= int(dia) <= 31:
        raise ValueError("El día va del 1 al 31.")

    return {
        "concepto": concepto,
        "monto": float(monto),
        "dia": int(dia),
        "categoria_id": categoria_id,
        "cuenta_id": cuenta_id,
        "texto": (texto or "").strip(),
        "activo": int(bool(activo)),
    }


def _entero_o_nulo(valor: object) -> int | None:
    """Convierte a int cuidando los nulos que llegan desde pandas."""
    if valor is None or pd.isna(valor):
        return None
    return int(valor)


def _limites_del_mes(mes: date) -> tuple[date, date]:
    """El primer y el último día del mes de `mes`."""
    ultimo = calendar.monthrange(mes.year, mes.month)[1]
    return date(mes.year, mes.month, 1), date(mes.year, mes.month, ultimo)


def _buscar_pago(
    cargo: dict, gastos: pd.DataFrame, usados: set[int]
) -> pd.Series | None:
    """
    Busca el gasto que paga un cargo fijo, el más cercano a su fecha.

    Se reconoce por su categoría (y subcategoría, si el cargo la tiene) o
    porque su descripción lo nombra, y por un monto parecido.
    """
    if gastos.empty:
        return None

    fecha = pd.Timestamp(cargo["fecha"])
    cerca = gastos[
        ((pd.to_datetime(gastos["fecha"]) - fecha).dt.days.abs() <= VENTANA_DIAS)
        & ~gastos["id"].isin(usados)
        & (
            (gastos["monto"] - cargo["monto"]).abs()
            <= max(cargo["monto"] * TOLERANCIA_MONTO, 1.0)
        )
    ]
    if cerca.empty:
        return None

    nombre = str(cargo["servicio"]).strip().lower()
    lo_nombra = cerca["descripcion"].fillna("").str.lower().str.contains(
        nombre, regex=False
    ) | cerca["descripcion_banco"].fillna("").str.lower().str.contains(
        nombre, regex=False
    )

    misma_categoria = pd.Series(False, index=cerca.index)
    if pd.notna(cargo["categoria_id"]):
        misma_categoria = cerca["categoria_id"] == int(cargo["categoria_id"])
        if pd.notna(cargo["subcategoria_id"]):
            misma_categoria &= cerca["subcategoria_id"] == int(cargo["subcategoria_id"])

    candidatos = cerca[lo_nombra | misma_categoria]
    if candidatos.empty:
        return None

    distancia = (candidatos["fecha"] - fecha).abs()
    return candidatos.loc[distancia.idxmin()]
