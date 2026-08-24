from __future__ import annotations

from datetime import date

import pandas as pd

from finanzas.data.repositories.patrimonio_repository import PatrimonioRepository
from finanzas.data.schemas import validar_patrimonio
from finanzas.domain.entities import CierreMensual, PosicionPatrimonial
from finanzas.domain.enums import Liquidez, TipoPatrimonio

# ═══════════════════════════════════════════════════════════
# Patrimonio y cierres mensuales
# ═══════════════════════════════════════════════════════════


class PatrimonioService:
    """Casos de uso sobre el balance personal y su histórico."""

    def __init__(self, repositorio: PatrimonioRepository | None = None) -> None:
        self._repo = repositorio or PatrimonioRepository()

    # ── Balance actual ───────────────────────────────────

    def balance(self) -> pd.DataFrame:
        """
        Devuelve las posiciones con su aporte con signo al patrimonio.

        Raises
        ------
        pandera.errors.SchemaError
            Si el balance rompe su contrato; en particular, si algún saldo
            quedó capturado en negativo.
        """
        df = self._repo.listar()
        if df.empty:
            return df

        df = validar_patrimonio(df)
        df["aporte_a_patrimonio"] = df.apply(
            lambda fila: (
                fila["saldo"]
                if fila["tipo"] == TipoPatrimonio.ACTIVO
                else -fila["saldo"]
            ),
            axis=1,
        )

        return df

    def resumen(self) -> dict[str, float]:
        """Devuelve activos, pasivos y patrimonio neto."""
        return self._repo.resumen()

    def activos_liquidos(self) -> float:
        """Suma de los activos de liquidez alta."""
        return self._repo.activos_liquidos()

    def por_subtipo(self) -> pd.DataFrame:
        """Agrupa el balance por lado y subtipo, para ver la composición."""
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
        """Da de alta una posición patrimonial."""
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
        """
        Actualiza sólo el saldo de una posición.

        Es la operación mensual habitual: los demás datos rara vez cambian.
        """
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

    # ── Cierres mensuales ────────────────────────────────

    def cierres(self) -> pd.DataFrame:
        """Devuelve el histórico de cierres con patrimonio neto y cambio."""
        return self._repo.listar_cierres()

    def guardar_cierre(
        self,
        periodo: str,
        efectivo: float,
        ahorro: float,
        inversiones: float,
        otros_activos: float,
        deudas: float,
        notas: str = "",
    ) -> None:
        """Crea o actualiza el cierre de un periodo."""
        self._repo.guardar_cierre(
            CierreMensual(
                periodo=periodo,
                efectivo=float(efectivo),
                ahorro=float(ahorro),
                inversiones=float(inversiones),
                otros_activos=float(otros_activos),
                deudas=float(deudas),
                notas=notas,
            )
        )

    def cierre_sugerido(self, periodo: str) -> CierreMensual:
        """Prellena el cierre del periodo con los saldos actuales."""
        return self._repo.cierre_desde_patrimonio(periodo)

    def eliminar_cierre(self, periodo: str) -> None:
        """Elimina el cierre de un periodo."""
        self._repo.eliminar_cierre(periodo)


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
