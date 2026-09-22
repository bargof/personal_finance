from __future__ import annotations

import calendar
from datetime import date

import pandas as pd

from finanzas.analytics.aggregations import periodo_de
from finanzas.data.repositories.patrimonio_repository import PatrimonioRepository
from finanzas.data.schemas import validar_patrimonio
from finanzas.domain.entities import (
    CierreMensual,
    PosicionPatrimonial,
    SaldoVerificado,
)
from finanzas.domain.enums import Liquidez, OrigenSaldo, TipoCuenta, TipoPatrimonio

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
        saldos = self.saldos(fecha)
        total = 0.0
        if not saldos.empty:
            liquidas = saldos[saldos["tipo"].map(lambda t: TipoCuenta(t).es_liquida)]
            total += float(liquidas["saldo"].clip(lower=0).sum())

        posiciones = self._repo.listar()
        if not posiciones.empty:
            manuales = posiciones[
                (posiciones["tipo"] == "Activo") & (posiciones["liquidez"] == "Alta")
            ]
            total += float(manuales["saldo"].sum())

        return round(total, 2)

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
