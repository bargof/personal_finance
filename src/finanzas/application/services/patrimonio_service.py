from __future__ import annotations

import calendar
from datetime import date

import pandas as pd

from finanzas.analytics.aggregations import periodo_de
from finanzas.data.repositories.patrimonio_repository import PatrimonioRepository
from finanzas.data.schemas import validar_patrimonio
from finanzas.domain.calendario import ciclo_de_pago, siguiente_dia
from finanzas.domain.entities import (
    CierreMensual,
    PosicionPatrimonial,
    SaldoVerificado,
)
from finanzas.domain.enums import (
    EstadoPago,
    Liquidez,
    OrigenSaldo,
    TipoCuenta,
    TipoPatrimonio,
)

# ═══════════════════════════════════════════════════════════
# Patrimonio
#
# El balance ya no se captura: se deduce. Cada cuenta es un
# libro que los movimientos mueven, anclado en los saldos
# verificados que el usuario o el estado de cuenta declaran.
# Lo único que se captura a mano son las posiciones que no
# son cuenta —la casa, el auto— y los propios saldos
# verificados.
# ═══════════════════════════════════════════════════════════


#: De dónde sale lo exigible de una deuda.
FUENTE_CAPTURADO = "Capturado"
FUENTE_CORTE = "Saldo al corte"
FUENTE_PARCIALIDAD = "Parcialidad"
FUENTE_SIN_MONTO = "Falta el monto"
FUENTE_ACUMULADO = "Capturado + parcialidades"


class PatrimonioService:
    """Casos de uso sobre el balance personal y su histórico."""

    def __init__(self, repositorio: PatrimonioRepository | None = None) -> None:
        self._repo = repositorio or PatrimonioRepository()

    # ── Saldos deducidos ─────────────────────────────────

    def saldos(self, fecha: date | None = None) -> pd.DataFrame:
        """
        Devuelve el saldo de cada cuenta activa al cierre de `fecha`.

        Por defecto, hoy. Cada fila dice además de dónde salió el número:
        desde qué saldo verificado y en qué sentido se dedujo, o si se
        está sumando desde cero porque la cuenta no tiene ninguno.
        """
        return self._repo.saldos_a(fecha or date.today())

    def resumen(self, fecha: date | None = None) -> dict[str, float]:
        """
        Devuelve activos, pasivos, adeudos y patrimonio neto a una fecha.

        Los activos suman las cuentas con saldo a favor y las posiciones
        capturadas como activo. Los pasivos suman la deuda de las cuentas
        de crédito, las posiciones capturadas como pasivo y lo gastado
        que a esa fecha no se había pagado desde ninguna cuenta.
        """
        fecha = fecha or date.today()
        saldos = self.saldos(fecha)
        posiciones = self._repo.listar()

        activos = 0.0
        pasivos = 0.0
        if not saldos.empty:
            # Una tarjeta con saldo a favor es un activo; una con deuda, un
            # pasivo. Lo decide el signo, no el tipo.
            activos = float(saldos["saldo"].clip(lower=0).sum())
            pasivos = float((-saldos["saldo"]).clip(lower=0).sum())

        if not posiciones.empty:
            por_lado = posiciones.groupby("tipo")["saldo"].sum()
            activos += float(por_lado.get("Activo", 0.0))
            pasivos += float(por_lado.get("Pasivo", 0.0))

        por_pagar = self._repo.por_pagar_a(fecha)
        pasivos += por_pagar

        return {
            "activos": round(activos, 2),
            "pasivos": round(pasivos, 2),
            "por_pagar": round(por_pagar, 2),
            "patrimonio_neto": round(activos - pasivos, 2),
        }

    def activos_liquidos(self, fecha: date | None = None) -> float:
        """
        Suma lo que se puede usar de inmediato: efectivo, débito y ahorro.

        Es el colchón del fondo de emergencia. Una tarjeta con saldo a
        favor también cuenta; una con deuda, no resta aquí porque no es
        liquidez negativa sino pasivo, y el pasivo se mide aparte.
        """
        return round(float(self.activos_liquidos_detalle(fecha)["monto"].sum()), 2)

    def activos_liquidos_detalle(self, fecha: date | None = None) -> pd.DataFrame:
        """
        Devuelve de dónde sale cada peso de los activos líquidos.

        Una fila por cuenta líquida y por posición de liquidez alta. Una
        cuenta en negativo aparece con cero y dice por qué: le falta un
        saldo verificado o un movimiento, y restarla escondería el hueco.
        Una cuenta restringida —un fondo de ahorro que no se puede tocar—
        aparece también con cero: es tuya, pero no está disponible.
        """
        filas = []
        saldos = self.saldos(fecha)
        if not saldos.empty:
            liquidas = saldos[saldos["tipo"].map(lambda t: TipoCuenta(t).es_liquida)]
            for cuenta in liquidas.sort_values("saldo", ascending=False).itertuples():
                saldo = float(cuenta.saldo)
                if cuenta.restringida:
                    monto = 0.0
                    nota = f"Restringida ({saldo:,.2f}): no se puede usar aún"
                elif saldo < 0:
                    monto = 0.0
                    nota = f"En negativo ({saldo:,.2f}): no suma"
                else:
                    monto = saldo
                    nota = (
                        ""
                        if cuenta.verificado
                        else "Sin saldo verificado: se suma desde cero"
                    )
                filas.append(
                    {
                        "concepto": cuenta.cuenta,
                        "tipo": cuenta.tipo,
                        "monto": round(monto, 2),
                        "nota": nota,
                    }
                )

        posiciones = self._repo.listar()
        if not posiciones.empty:
            manuales = posiciones[
                (posiciones["tipo"] == "Activo") & (posiciones["liquidez"] == "Alta")
            ]
            for posicion in manuales.itertuples():
                filas.append(
                    {
                        "concepto": posicion.nombre,
                        "tipo": "Bien de liquidez alta",
                        "monto": round(float(posicion.saldo), 2),
                        "nota": "",
                    }
                )

        return pd.DataFrame(filas, columns=["concepto", "tipo", "monto", "nota"])

    def reglas_de_deudas(self) -> pd.DataFrame:
        """
        Devuelve, por cuenta de deuda, lo que el plan de pagos necesita.

        Cuánto cuesta posponerla (`tasa_anual`, en proporción) y hasta
        cuándo se puede (`posponer_hasta`), más su parcialidad y su día
        de pago para proyectar los meses que vienen.
        """
        cuentas = self._repo.cuentas()
        deudas = cuentas[cuentas["tipo"].map(lambda t: TipoCuenta(t).es_pasivo)].copy()
        deudas["posponer_hasta"] = pd.to_datetime(
            deudas["posponer_hasta"], errors="coerce"
        ).dt.date
        return deudas.rename(columns={"id": "cuenta_id", "nombre": "cuenta"})[
            [
                "cuenta_id",
                "cuenta",
                "tipo",
                "tasa_anual",
                "posponer_hasta",
                "dia_pago",
                "pago_mensual",
            ]
        ].reset_index(drop=True)

    def fijar_reglas_deuda(
        self,
        cuenta_id: int,
        tasa_anual: float | None = None,
        posponer_hasta: date | None = None,
    ) -> None:
        """
        Dice cuánto cuesta posponer una deuda y hasta cuándo se puede.

        `tasa_anual` va en proporción (0.65 = 65 %). Una tarjeta se puede
        posponer siempre, a su costo; un préstamo, sólo si tiene tasa o
        fecha tope.

        Raises
        ------
        ValueError
            Si la cuenta no es de deuda o la tasa es negativa.
        """
        cuentas = self._repo.cuentas(solo_activas=False)
        fila = cuentas[cuentas["id"] == cuenta_id]
        if fila.empty:
            raise ValueError(f"No existe la cuenta {cuenta_id}.")
        if not TipoCuenta(fila.iloc[0]["tipo"]).es_pasivo:
            raise ValueError("Sólo una cuenta de deuda tiene tasa o fecha tope.")
        if tasa_anual is not None and float(tasa_anual) < 0:
            raise ValueError("La tasa se captura en positivo.")

        self._repo.guardar_reglas_deuda(
            cuenta_id,
            float(tasa_anual) if tasa_anual else None,
            posponer_hasta,
        )

    def nombres_de_cuentas(self) -> dict[int, str]:
        """{id: nombre} de todas las cuentas, activas o no."""
        cuentas = self._repo.cuentas(solo_activas=False)
        return {int(f.id): f.nombre for f in cuentas.itertuples()}

    def cuentas_restringidas(self) -> set[int]:
        """Ids de las cuentas cuyo dinero no se puede usar hasta retirarlo."""
        cuentas = self._repo.cuentas(solo_activas=False)
        return {int(i) for i in cuentas.loc[cuentas["restringida"] == 1, "id"]}

    def cuentas_sin_ancla(self) -> pd.DataFrame:
        """Devuelve las cuentas activas cuyo saldo se suma desde cero."""
        return self._repo.cuentas_sin_ancla()

    def descuadres(self) -> pd.DataFrame:
        """
        Devuelve los tramos entre saldos verificados que no cuadran.

        Entre dos saldos verificados los movimientos tienen que explicar
        la diferencia. Donde no, falta un estado de cuenta por importar o
        algo se registró en la cuenta equivocada, y la diferencia dice
        cuánto.
        """
        cuentas = self._repo.cuentas(solo_activas=False).set_index("id")
        filas = []
        for cuenta_id, descuadre in self._repo.descuadres():
            tipo = TipoCuenta(cuentas.loc[cuenta_id, "tipo"])
            signo = -1 if tipo.es_pasivo else 1
            filas.append(
                {
                    "cuenta_id": cuenta_id,
                    "cuenta": cuentas.loc[cuenta_id, "nombre"],
                    "desde": descuadre.desde.fecha,
                    "hasta": descuadre.hasta.fecha,
                    "saldo_inicial": descuadre.desde.saldo * signo,
                    "saldo_final": descuadre.hasta.saldo * signo,
                    "esperado": descuadre.esperado * signo,
                    "diferencia": descuadre.diferencia * signo,
                    "movimientos": descuadre.movimientos,
                }
            )

        return pd.DataFrame(
            filas,
            columns=[
                "cuenta_id",
                "cuenta",
                "desde",
                "hasta",
                "saldo_inicial",
                "saldo_final",
                "esperado",
                "diferencia",
                "movimientos",
            ],
        )

    # ── Saldos verificados ───────────────────────────────

    def anclas(self) -> pd.DataFrame:
        """Devuelve los saldos verificados con el nombre de su cuenta."""
        return self._repo.listar_anclas()

    def verificar_saldo(
        self,
        cuenta_id: int,
        fecha: date,
        saldo_visto: float,
        origen: str = OrigenSaldo.MANUAL,
        nota: str = "",
    ) -> int:
        """
        Registra el saldo real de una cuenta al cierre de un día.

        `saldo_visto` es el número tal como lo enseña el banco: en una
        tarjeta, lo que se debe, en positivo. Aquí se traduce al signo del
        libro para que la deducción hacia adelante y hacia atrás sume sin
        casos especiales.
        """
        cuentas = self._repo.cuentas(solo_activas=False)
        fila = cuentas[cuentas["id"] == cuenta_id]
        if fila.empty:
            raise ValueError(f"No existe la cuenta {cuenta_id}.")

        tipo = TipoCuenta(fila.iloc[0]["tipo"])
        saldo = -float(saldo_visto) if tipo.es_pasivo else float(saldo_visto)

        return self._repo.guardar_ancla(
            SaldoVerificado(
                cuenta_id=cuenta_id,
                fecha=fecha,
                saldo=saldo,
                origen=OrigenSaldo(origen),
                nota=nota,
            )
        )

    def olvidar_saldo(self, ancla_id: int) -> None:
        """Elimina un saldo verificado."""
        self._repo.eliminar_ancla(ancla_id)

    # ── Lo exigible de cada deuda ────────────────────────

    def exigibles(self, hoy: date | None = None) -> pd.DataFrame:
        """
        Devuelve, por cada cuenta de deuda, cuánto de ella ya hay que pagar.

        El saldo de un préstamo o una tarjeta es todo lo que se debe; lo
        exigible es la parte que ya toca pagar. Sale de una de tres
        fuentes, en este orden:

        1. Lo capturado a mano, mientras siga siendo del ciclo en curso: es
           el pago para no generar intereses que dice el estado de cuenta,
           y manda sobre cualquier estimación (con compras a meses, el
           saldo al corte lo exageraría).
        2. En una tarjeta con día de corte, lo que debía al último corte.
        3. En un préstamo con parcialidad fija, esa parcialidad, más lo
           que haya quedado sin pagar del ciclo anterior.

        Los abonos a la cuenta dentro del ciclo lo descuentan, y lo
        pendiente nunca rebasa la deuda total.

        Returns
        -------
        pandas.DataFrame
            Una fila por cuenta de deuda activa, tenga o no exigible.
        """
        hoy = hoy or date.today()
        columnas = [
            "cuenta_id",
            "cuenta",
            "tipo",
            "institucion",
            "deuda",
            "exigible",
            "abonado",
            "pendiente",
            "fuente",
            "corte",
            "declarado_el",
            "fecha_limite",
            "dias",
            "estado",
            "nota",
            "dia_corte",
            "dia_pago",
            "pago_mensual",
        ]
        saldos = self.saldos(hoy)
        if saldos.empty:
            return pd.DataFrame(columns=columnas)

        deudas = saldos[saldos["tipo"].map(lambda t: TipoCuenta(t).es_pasivo)]
        declarados = self._repo.exigibles().set_index("cuenta_id")
        configuracion = self._repo.cuentas(solo_activas=False).set_index("id")
        saldos_al_corte: dict[date, pd.DataFrame] = {}

        filas = []
        for fila in deudas.itertuples():
            cuenta_id = int(fila.cuenta_id)
            config = configuracion.loc[cuenta_id]
            dia_corte = _entero_o_nulo(config["dia_corte"])
            dia_pago = _entero_o_nulo(config["dia_pago"])
            pago_mensual = (
                float(config["pago_mensual"])
                if pd.notna(config["pago_mensual"])
                else None
            )
            es_tarjeta = TipoCuenta(fila.tipo) == TipoCuenta.CREDITO
            ciclo = (
                ciclo_de_pago(hoy, dia_pago, dia_corte if es_tarjeta else None)
                if dia_pago
                else None
            )

            deuda = max(0.0, float(fila.saldo_visto))
            registro: dict[str, object] = {
                "cuenta_id": cuenta_id,
                "cuenta": fila.cuenta,
                "tipo": fila.tipo,
                "institucion": fila.institucion,
                "deuda": round(deuda, 2),
                "exigible": 0.0,
                "abonado": 0.0,
                "pendiente": 0.0,
                "fuente": "",
                "corte": ciclo.corte if ciclo else None,
                "declarado_el": None,
                "fecha_limite": ciclo.limite if ciclo else None,
                "estado": "",
                "nota": "",
                "dia_corte": dia_corte,
                "dia_pago": dia_pago,
                "pago_mensual": pago_mensual,
            }

            declarado = (
                declarados.loc[cuenta_id] if cuenta_id in declarados.index else None
            )
            vigente = declarado is not None and (
                ciclo is None or declarado["declarado_el"].date() >= ciclo.corte
            )
            atraso = 0.0
            # Un préstamo con parcialidad fija y algo capturado: lo capturado
            # es el punto de partida y cada día de pago desde entonces suma
            # una parcialidad, hasta el que vence en este ciclo. Así lo que
            # se va dejando se acumula mes con mes —hasta que se pague o
            # llegue su fecha tope— en vez de olvidarse al cambiar de ciclo.
            acumula = (
                not es_tarjeta
                and ciclo is not None
                and bool(pago_mensual)
                and declarado is not None
            )

            if acumula:
                desde = declarado["declarado_el"].date()
                limite_capturado = (
                    declarado["fecha_limite"].date()
                    if pd.notna(declarado["fecha_limite"])
                    else None
                )
                vencimientos = []
                cuando = siguiente_dia(dia_pago, desde)
                while cuando <= ciclo.limite:
                    vencimientos.append(cuando)
                    cuando = siguiente_dia(dia_pago, cuando)

                exigible = float(declarado["monto"]) + pago_mensual * len(vencimientos)
                atraso = (
                    float(declarado["monto"])
                    if limite_capturado is not None and limite_capturado < hoy
                    else 0.0
                ) + pago_mensual * sum(1 for v in vencimientos if v < hoy)
                abonado = float(declarado["abonado"])
                registro |= {
                    "fuente": FUENTE_ACUMULADO,
                    "declarado_el": desde,
                    "fecha_limite": (
                        ciclo.limite
                        if vencimientos or limite_capturado is None
                        else limite_capturado
                    ),
                    "nota": declarado["nota"],
                }
            elif vigente:
                limite = (
                    declarado["fecha_limite"].date()
                    if pd.notna(declarado["fecha_limite"])
                    else registro["fecha_limite"]
                )
                exigible = float(declarado["monto"])
                abonado = float(declarado["abonado"])
                registro |= {
                    "fuente": FUENTE_CAPTURADO,
                    "declarado_el": declarado["declarado_el"].date(),
                    "fecha_limite": limite,
                    "nota": declarado["nota"],
                }
            elif ciclo is not None and es_tarjeta and dia_corte:
                if ciclo.corte not in saldos_al_corte:
                    saldos_al_corte[ciclo.corte] = self.saldos(ciclo.corte).set_index(
                        "cuenta_id"
                    )
                al_corte = saldos_al_corte[ciclo.corte]
                exigible = (
                    max(float(al_corte.loc[cuenta_id, "saldo_visto"]), 0.0)
                    if cuenta_id in al_corte.index
                    else 0.0
                )
                abonado = self._repo.abonos(cuenta_id, despues_de=ciclo.corte)
                registro["fuente"] = FUENTE_CORTE
            elif ciclo is not None and pago_mensual:
                # Lo que quedó sin pagar del ciclo anterior sigue debiéndose
                # y ya está vencido: se paga antes que lo de este ciclo.
                atraso = max(
                    pago_mensual
                    - self._repo.abonos(
                        cuenta_id, despues_de=ciclo.corte_anterior, hasta=ciclo.corte
                    ),
                    0.0,
                )
                exigible = pago_mensual + atraso
                abonado = self._repo.abonos(cuenta_id, despues_de=ciclo.corte)
                registro["fuente"] = FUENTE_PARCIALIDAD
            else:
                if ciclo is not None:
                    registro["fuente"] = FUENTE_SIN_MONTO
                registro["dias"] = _dias(registro["fecha_limite"], hoy)
                filas.append(registro)
                continue

            pendiente = round(min(max(exigible - abonado, 0.0), deuda), 2)
            if pendiente < 0.005:
                estado = EstadoPago.PAGADO
            elif atraso > abonado + 0.005:
                estado = EstadoPago.VENCIDO
            else:
                estado = EstadoPago.segun_fecha(registro["fecha_limite"], hoy)

            registro |= {
                "exigible": round(exigible, 2),
                "abonado": round(abonado, 2),
                "pendiente": pendiente,
                "estado": str(estado),
                "dias": _dias(registro["fecha_limite"], hoy),
            }
            filas.append(registro)

        return pd.DataFrame(filas, columns=columnas)

    def fijar_calendario_deuda(
        self,
        cuenta_id: int,
        dia_pago: int | None,
        dia_corte: int | None = None,
        pago_mensual: float | None = None,
    ) -> None:
        """
        Fija los días de corte y de pago de una deuda, y su parcialidad.

        Con ellos lo exigible sale solo cada ciclo: en una tarjeta, lo que
        debía al corte; en un préstamo, la parcialidad. Pasar None en
        `dia_pago` los quita.

        Raises
        ------
        ValueError
            Si la cuenta no es de deuda, un día no está entre 1 y 31, se da
            día de corte a algo que no es tarjeta o la parcialidad es
            negativa.
        """
        cuentas = self._repo.cuentas(solo_activas=False)
        fila = cuentas[cuentas["id"] == cuenta_id]
        if fila.empty:
            raise ValueError(f"No existe la cuenta {cuenta_id}.")
        tipo = TipoCuenta(fila.iloc[0]["tipo"])
        if not tipo.es_pasivo:
            raise ValueError(
                "Sólo una cuenta de deuda —tarjeta o préstamo— tiene días de pago."
            )
        for dia in (dia_pago, dia_corte):
            if dia is not None and not 1 <= int(dia) <= 31:
                raise ValueError("Los días van del 1 al 31.")
        if dia_corte is not None and tipo != TipoCuenta.CREDITO:
            raise ValueError("Sólo una tarjeta de crédito tiene día de corte.")
        if pago_mensual is not None and float(pago_mensual) < 0:
            raise ValueError("La parcialidad se captura en positivo.")

        if dia_pago is None:
            dia_corte, pago_mensual = None, None

        self._repo.guardar_calendario_deuda(
            cuenta_id,
            int(dia_corte) if dia_corte is not None else None,
            int(dia_pago) if dia_pago is not None else None,
            float(pago_mensual) if pago_mensual else None,
        )

    def fijar_exigible(
        self,
        cuenta_id: int,
        monto: float,
        fecha_limite: date | None = None,
        declarado_el: date | None = None,
        nota: str = "",
    ) -> None:
        """
        Declara cuánto de la deuda de una cuenta ya hay que pagar.

        Capturarlo otra vez lo corrige. Los abonos a la cuenta desde
        `declarado_el` (hoy, si no se dice) lo van descontando.

        Raises
        ------
        ValueError
            Si la cuenta no existe, no es de deuda o el monto es negativo.
        """
        cuentas = self._repo.cuentas(solo_activas=False)
        fila = cuentas[cuentas["id"] == cuenta_id]
        if fila.empty:
            raise ValueError(f"No existe la cuenta {cuenta_id}.")
        if not TipoCuenta(fila.iloc[0]["tipo"]).es_pasivo:
            raise ValueError(
                "Sólo una cuenta de deuda —tarjeta o préstamo— tiene algo exigible."
            )
        if float(monto) < 0:
            raise ValueError("Lo exigible se captura en positivo.")

        self._repo.guardar_exigible(
            cuenta_id,
            float(monto),
            declarado_el or date.today(),
            fecha_limite,
            nota,
        )

    def quitar_exigible(self, cuenta_id: int) -> None:
        """Deja una cuenta de deuda sin nada exigible declarado."""
        self._repo.eliminar_exigible(cuenta_id)

    # ── Evolución mensual ────────────────────────────────

    def evolucion(self, hasta: date | None = None) -> pd.DataFrame:
        """
        Devuelve el cierre de cada mes, deducido, desde el primer dato.

        Es lo que antes se capturaba como «cierre mensual»: ahora sale
        solo, del saldo de cada cuenta al último día del mes.
        """
        hasta = hasta or date.today()
        inicio = self._repo.primera_fecha()
        if inicio is None:
            return pd.DataFrame(
                columns=[
                    "periodo",
                    "efectivo",
                    "ahorro",
                    "inversiones",
                    "otros_activos",
                    "deudas",
                    "patrimonio_neto",
                    "cambio_mensual",
                ]
            )

        posiciones = self._repo.listar()
        otros_activos = 0.0
        pasivos_manuales = 0.0
        if not posiciones.empty:
            otros_activos = float(
                posiciones.loc[posiciones["tipo"] == "Activo", "saldo"].sum()
            )
            pasivos_manuales = float(
                posiciones.loc[posiciones["tipo"] == "Pasivo", "saldo"].sum()
            )

        # Los libros se leen una vez y se deducen tantas fechas como meses.
        libros = self._repo.libros()

        filas = []
        anio, mes = inicio.year, inicio.month
        while (anio, mes) <= (hasta.year, hasta.month):
            fin_de_mes = date(anio, mes, calendar.monthrange(anio, mes)[1])
            corte = min(fin_de_mes, hasta)
            filas.append(self._cierre(corte, otros_activos, pasivos_manuales, libros))
            anio, mes = (anio + 1, 1) if mes == 12 else (anio, mes + 1)

        df = pd.DataFrame(
            [
                {
                    "periodo": c.periodo,
                    "efectivo": c.efectivo,
                    "ahorro": c.ahorro,
                    "inversiones": c.inversiones,
                    "otros_activos": c.otros_activos,
                    "deudas": c.deudas,
                    "patrimonio_neto": c.patrimonio_neto,
                }
                for c in filas
            ]
        )
        df["cambio_mensual"] = df["patrimonio_neto"].diff().fillna(0.0)

        return df

    def _cierre(
        self,
        corte: date,
        otros_activos: float,
        pasivos_manuales: float,
        libros: dict,
    ) -> CierreMensual:
        """Arma la foto del balance al cierre de `corte`."""
        saldos = self._repo.saldos_a(corte, libros=libros)
        cierre = CierreMensual(periodo=periodo_de(corte), otros_activos=otros_activos)
        cierre.deudas = pasivos_manuales + self._repo.por_pagar_a(corte)

        for fila in saldos.itertuples():
            tipo = TipoCuenta(fila.tipo)
            saldo = float(fila.saldo)
            if tipo.es_pasivo:
                cierre.deudas += -saldo
            elif tipo == TipoCuenta.AHORRO:
                cierre.ahorro += saldo
            elif tipo == TipoCuenta.INVERSION:
                cierre.inversiones += saldo
            else:
                cierre.efectivo += saldo

        return cierre

    # ── Posiciones que no son cuenta ─────────────────────

    def balance(self) -> pd.DataFrame:
        """
        Devuelve las posiciones capturadas con su aporte al patrimonio.

        Raises
        ------
        pandera.errors.SchemaError
            Si el balance rompe su contrato; en particular, si algún saldo
            quedó capturado en negativo.
        """
        df = self._repo.listar()
        if df.empty:
            return df

        return validar_patrimonio(df)

    def por_subtipo(self) -> pd.DataFrame:
        """Agrupa las posiciones por lado y subtipo, para ver la composición."""
        df = self._repo.listar()
        if df.empty:
            return pd.DataFrame(columns=["tipo", "subtipo", "saldo"])

        df = df.copy()
        df["subtipo"] = df["subtipo"].replace("", "Sin clasificar")

        return (
            df.groupby(["tipo", "subtipo"], as_index=False)["saldo"]
            .sum()
            .sort_values(["tipo", "saldo"], ascending=[True, False])
            .reset_index(drop=True)
        )

    def crear(
        self,
        nombre: str,
        tipo: str,
        saldo: float,
        subtipo: str = "",
        institucion: str = "",
        liquidez: str = Liquidez.NO_APLICA,
        tasa_anual: float = 0.0,
        fecha_corte: date | None = None,
        moneda: str = "MXN",
        notas: str = "",
    ) -> int:
        """Da de alta una posición que no es cuenta: la casa, el auto."""
        posicion = PosicionPatrimonial(
            nombre=_validar_nombre(nombre),
            tipo=TipoPatrimonio(tipo),
            saldo=_validar_saldo(saldo),
            subtipo=subtipo,
            institucion=institucion,
            liquidez=Liquidez(liquidez),
            tasa_anual=float(tasa_anual),
            fecha_corte=fecha_corte or date.today(),
            moneda=moneda,
            notas=notas,
        )

        return self._repo.crear(posicion)

    def actualizar_saldo(self, posicion_id: int, saldo: float) -> None:
        """Actualiza sólo el valor de una posición."""
        df = self._repo.listar()
        fila = df[df["id"] == posicion_id]
        if fila.empty:
            raise ValueError(f"No existe la posición {posicion_id}.")

        actual = fila.iloc[0]
        posicion = PosicionPatrimonial(
            nombre=actual["nombre"],
            tipo=TipoPatrimonio(actual["tipo"]),
            saldo=_validar_saldo(saldo),
            subtipo=actual["subtipo"],
            institucion=actual["institucion"],
            liquidez=Liquidez(actual["liquidez"]),
            tasa_anual=float(actual["tasa_anual"]),
            fecha_corte=date.today(),
            moneda=actual["moneda"],
            notas=actual["notas"],
        )

        self._repo.actualizar(posicion_id, posicion)

    def eliminar(self, posicion_id: int) -> None:
        """Elimina una posición patrimonial."""
        self._repo.eliminar(posicion_id)


def _dias(limite: object, hoy: date) -> int | None:
    """Días que faltan para `limite`; negativo si ya pasó."""
    return (limite - hoy).days if isinstance(limite, date) else None


def _entero_o_nulo(valor: object) -> int | None:
    """Convierte a int cuidando los nulos que llegan desde pandas."""
    if valor is None or pd.isna(valor):
        return None
    return int(valor)


def _validar_nombre(nombre: str) -> str:
    """Normaliza y valida el nombre de una posición."""
    limpio = (nombre or "").strip()
    if not limpio:
        raise ValueError("La posición necesita un nombre.")

    return limpio


def _validar_saldo(saldo: float) -> float:
    """Valida que el saldo se capture en positivo."""
    valor = float(saldo)
    if valor < 0:
        raise ValueError(
            "Captura el saldo en positivo, incluso para pasivos: "
            "el signo lo pone el cálculo del patrimonio neto."
        )

    return valor
