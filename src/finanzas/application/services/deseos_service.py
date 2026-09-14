from __future__ import annotations

from datetime import date

import pandas as pd

from finanzas.data.repositories.deseos_repository import DeseosRepository
from finanzas.domain.entities import Deseo
from finanzas.domain.enums import EstadoDeseo, Prioridad

# ═══════════════════════════════════════════════════════════
# Lista de deseos
#
# La lista sola no decide nada; lo que decide es ponerla al
# lado del saldo disponible. Por eso el servicio no devuelve
# deseos a secas sino deseos ya contrastados contra un saldo.
# ═══════════════════════════════════════════════════════════

#: Desde qué proporción del costo se considera que ya casi alcanza.
UMBRAL_CERCA = 0.75


class DeseoInvalidoError(ValueError):
    """El deseo no tiene los datos mínimos para guardarse."""


class DeseosService:
    """Casos de uso sobre la lista de deseos."""

    def __init__(self, repositorio: DeseosRepository | None = None) -> None:
        self._repo = repositorio or DeseosRepository()

    def listar(
        self, saldo: float = 0.0, incluir_comprados: bool = False
    ) -> pd.DataFrame:
        """
        Devuelve los deseos contrastados contra un saldo disponible.

        Parameters
        ----------
        saldo : float
            Contra qué se compara cada costo. Cero deja todo en «falta
            mucho», que es lo correcto cuando no hay con qué comprar.
        incluir_comprados : bool
            Si es True, incluye los que ya salieron de la lista.

        Returns
        -------
        pandas.DataFrame
            Los deseos con `cobertura`, `faltante` y `alcance`.
        """
        df = self._repo.listar(incluir_comprados=incluir_comprados)
        if df.empty:
            return df

        df = df.copy()
        df["cobertura"] = df["costo"].apply(
            lambda costo: min(saldo / costo, 1.0) if costo > 0 else 1.0
        )
        df["faltante"] = (df["costo"] - saldo).clip(lower=0.0)
        df["alcance"] = df["cobertura"].apply(_alcance)

        return df

    def saldos_por_cuenta(self) -> pd.DataFrame:
        """Devuelve el saldo disponible en cada cuenta del balance."""
        return self._repo.saldos_por_cuenta()

    def resumen(self, saldo: float = 0.0) -> dict[str, float]:
        """
        Devuelve las cifras de la lista frente a un saldo.

        Returns
        -------
        dict
            Cuántos deseos hay, cuánto cuestan en total, cuántos alcanzan
            con el saldo dado y cuánto falta para cubrirlos todos.
        """
        df = self.listar(saldo)
        if df.empty:
            return {
                "deseos": 0,
                "costo_total": 0.0,
                "alcanzan": 0,
                "falta_total": 0.0,
            }

        return {
            "deseos": int(len(df)),
            "costo_total": float(df["costo"].sum()),
            "alcanzan": int((df["alcance"] == EstadoDeseo.ALCANZA).sum()),
            "falta_total": float(max(df["costo"].sum() - saldo, 0.0)),
        }

    def agregar(
        self,
        nombre: str,
        costo: float,
        categoria_id: int | None = None,
        prioridad: str = Prioridad.MEDIA,
        enlace: str = "",
        notas: str = "",
    ) -> int:
        """
        Agrega un deseo a la lista.

        Raises
        ------
        DeseoInvalidoError
            Si falta el nombre o el costo no es válido.
        """
        limpio = (nombre or "").strip()
        if not limpio:
            raise DeseoInvalidoError("El deseo necesita un nombre.")
        if costo < 0:
            raise DeseoInvalidoError("El costo no puede ser negativo.")

        return self._repo.crear(
            Deseo(
                nombre=limpio,
                costo=float(costo),
                categoria_id=categoria_id,
                prioridad=Prioridad(prioridad),
                enlace=enlace,
                notas=notas,
            )
        )

    def actualizar(self, deseo_id: int, **campos: object) -> None:
        """Actualiza los campos indicados de un deseo."""
        df = self._repo.listar(incluir_comprados=True)
        fila = df[df["id"] == deseo_id]
        if fila.empty:
            raise DeseoInvalidoError(f"No existe el deseo {deseo_id}.")

        actual = fila.iloc[0]
        comprado = actual["comprado_en"]
        deseo = Deseo(
            nombre=actual["nombre"],
            costo=float(actual["costo"]),
            categoria_id=_entero_o_nulo(actual["categoria_id"]),
            prioridad=Prioridad(actual["prioridad"]),
            enlace=actual["enlace"],
            notas=actual["notas"],
            comprado_en=comprado.date() if pd.notna(comprado) else None,
        )

        for campo, valor in campos.items():
            if not hasattr(deseo, campo):
                raise DeseoInvalidoError(f"El deseo no tiene el campo «{campo}».")
            setattr(deseo, campo, valor)

        if not deseo.nombre.strip():
            raise DeseoInvalidoError("El deseo necesita un nombre.")

        self._repo.actualizar(deseo_id, deseo)

    def marcar_comprado(self, deseo_id: int, cuando: date | None = None) -> None:
        """Saca un deseo de la lista, conservándolo como histórico."""
        self._repo.marcar_comprado(deseo_id, cuando)

    def devolver_a_la_lista(self, deseo_id: int) -> None:
        """Deshace la compra de un deseo."""
        self._repo.devolver_a_la_lista(deseo_id)

    def eliminar(self, deseo_id: int) -> None:
        """Borra un deseo de la lista."""
        self._repo.eliminar(deseo_id)


def _alcance(cobertura: float) -> str:
    """Traduce la cobertura al semáforo de la lista."""
    if cobertura >= 1.0:
        return str(EstadoDeseo.ALCANZA)
    if cobertura >= UMBRAL_CERCA:
        return str(EstadoDeseo.CASI)

    return str(EstadoDeseo.LEJOS)


def _entero_o_nulo(valor: object) -> int | None:
    """Convierte a int cuidando los nulos que llegan desde pandas."""
    if valor is None or pd.isna(valor):
        return None
    return int(valor)
