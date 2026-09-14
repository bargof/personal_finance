from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from finanzas.data.repositories.catalogos_repository import CatalogosRepository
from finanzas.data.repositories.suscripciones_repository import SuscripcionesRepository
from finanzas.domain.entities import Suscripcion
from finanzas.domain.enums import FrecuenciaCobro, Necesidad

# ═══════════════════════════════════════════════════════════
# Suscripciones
# ═══════════════════════════════════════════════════════════


class SuscripcionesService:
    """Casos de uso sobre el gasto recurrente por suscripción."""

    def __init__(
        self,
        repositorio: SuscripcionesRepository | None = None,
        catalogos: CatalogosRepository | None = None,
    ) -> None:
        self._repo = repositorio or SuscripcionesRepository()
        self._catalogos = catalogos or CatalogosRepository()

    def listar(self, solo_activas: bool = False) -> pd.DataFrame:
        """Devuelve las suscripciones con costo mensual y anual calculados."""
        return self._repo.listar(solo_activas=solo_activas)

    def resumen(self) -> dict[str, float]:
        """
        Devuelve las cifras que interesan al revisar suscripciones.

        Returns
        -------
        dict
            Costo mensual y anual de las activas, cuántas hay y cuánto
            representan las candidatas a cancelar.
        """
        df = self._repo.listar(solo_activas=True)
        if df.empty:
            return {
                "costo_mensual": 0.0,
                "costo_anual": 0.0,
                "activas": 0,
                "ahorro_potencial_mensual": 0.0,
                "candidatas": 0,
            }

        candidatas = df[df["candidato_a_cancelar"]]

        return {
            "costo_mensual": float(df["costo_mensual"].sum()),
            "costo_anual": float(df["costo_anual"].sum()),
            "activas": int(len(df)),
            "ahorro_potencial_mensual": float(candidatas["costo_mensual"].sum()),
            "candidatas": int(len(candidatas)),
        }

    def proximos_cobros(self, dias: int = 30) -> pd.DataFrame:
        """Devuelve las suscripciones activas que se cobran en los próximos días."""
        df = self._repo.listar(solo_activas=True)
        if df.empty:
            return df

        hoy = pd.Timestamp(date.today())
        limite = hoy + timedelta(days=dias)
        proximas = df[df["proximo_cobro"].between(hoy, limite)]

        return proximas.sort_values("proximo_cobro").reset_index(drop=True)

    def crear(
        self,
        servicio: str,
        costo_por_cobro: float,
        frecuencia: str = FrecuenciaCobro.MENSUAL,
        categoria_id: int | None = None,
        subcategoria_id: int | None = None,
        cuenta_id: int | None = None,
        proximo_cobro: date | None = None,
        renovacion_automatica: bool = True,
        necesidad: str = Necesidad.DESEO,
        notas: str = "",
    ) -> int:
        """
        Da de alta una suscripción.

        Raises
        ------
        ValueError
            Si la subcategoría no cuelga de la categoría indicada.
        """
        self._validar_clasificacion(categoria_id, subcategoria_id)

        suscripcion = Suscripcion(
            servicio=_validar_servicio(servicio),
            costo_por_cobro=_validar_costo(costo_por_cobro),
            frecuencia=FrecuenciaCobro(frecuencia),
            categoria_id=categoria_id,
            subcategoria_id=subcategoria_id,
            cuenta_id=cuenta_id,
            proximo_cobro=proximo_cobro,
            renovacion_automatica=renovacion_automatica,
            necesidad=Necesidad(necesidad),
            notas=notas,
        )

        return self._repo.crear(suscripcion)

    def actualizar(self, suscripcion_id: int, **campos: object) -> None:
        """Actualiza los campos indicados de una suscripción."""
        df = self._repo.listar()
        fila = df[df["id"] == suscripcion_id]
        if fila.empty:
            raise ValueError(f"No existe la suscripción {suscripcion_id}.")

        actual = fila.iloc[0]
        proximo = actual["proximo_cobro"]

        suscripcion = Suscripcion(
            servicio=actual["servicio"],
            costo_por_cobro=float(actual["costo_por_cobro"]),
            frecuencia=FrecuenciaCobro(actual["frecuencia"]),
            categoria_id=_entero_o_nulo(actual["categoria_id"]),
            subcategoria_id=_entero_o_nulo(actual["subcategoria_id"]),
            cuenta_id=_entero_o_nulo(actual["cuenta_id"]),
            proximo_cobro=proximo.date() if pd.notna(proximo) else None,
            renovacion_automatica=bool(actual["renovacion_automatica"]),
            necesidad=Necesidad(actual["necesidad"]),
            activa=bool(actual["activa"]),
            notas=actual["notas"],
        )

        for campo, valor in campos.items():
            if not hasattr(suscripcion, campo):
                raise ValueError(f"La suscripción no tiene el campo «{campo}».")
            setattr(suscripcion, campo, valor)

        suscripcion.servicio = _validar_servicio(suscripcion.servicio)
        suscripcion.costo_por_cobro = _validar_costo(suscripcion.costo_por_cobro)
        self._validar_clasificacion(
            suscripcion.categoria_id, suscripcion.subcategoria_id
        )

        self._repo.actualizar(suscripcion_id, suscripcion)

    def _validar_clasificacion(
        self, categoria_id: int | None, subcategoria_id: int | None
    ) -> None:
        """Exige que la subcategoría cuelgue de la categoría elegida."""
        if self._catalogos.subcategoria_pertenece_a(subcategoria_id, categoria_id):
            return

        raise ValueError(
            "La subcategoría elegida no pertenece a esa categoría. "
            "Elige una subcategoría de la misma categoría o déjala vacía."
        )

    def cambiar_estado(self, suscripcion_id: int, activa: bool) -> None:
        """Da de alta o de baja una suscripción sin perder su historial."""
        self._repo.cambiar_estado(suscripcion_id, activa)

    def eliminar(self, suscripcion_id: int) -> None:
        """Elimina una suscripción."""
        self._repo.eliminar(suscripcion_id)


def _validar_servicio(servicio: str) -> str:
    """Normaliza y valida el nombre del servicio."""
    limpio = (servicio or "").strip()
    if not limpio:
        raise ValueError("La suscripción necesita un nombre de servicio.")

    return limpio


def _validar_costo(costo: float) -> float:
    """Valida que el costo por cobro no sea negativo."""
    valor = float(costo)
    if valor < 0:
        raise ValueError("El costo por cobro no puede ser negativo.")

    return valor


def _entero_o_nulo(valor: object) -> int | None:
    """Convierte a int cuidando los nulos que llegan desde pandas."""
    if valor is None or pd.isna(valor):
        return None
    return int(valor)
