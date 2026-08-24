from __future__ import annotations

from datetime import date

import pandas as pd

from finanzas.data.repositories.metas_repository import MetasRepository
from finanzas.domain.entities import Meta
from finanzas.domain.enums import Prioridad

# ═══════════════════════════════════════════════════════════
# Metas financieras
#
# El repositorio guarda lo capturado; el servicio devuelve la
# tabla con avance, aporte necesario y estado ya resueltos
# por la entidad.
# ═══════════════════════════════════════════════════════════


class MetasService:
    """Casos de uso sobre las metas financieras."""

    def __init__(self, repositorio: MetasRepository | None = None) -> None:
        self._repo = repositorio or MetasRepository()

    def tablero(self, hoy: date | None = None) -> pd.DataFrame:
        """
        Devuelve las metas con su avance y su estado calculados.

        Returns
        -------
        pandas.DataFrame
            Una fila por meta, con meses restantes, aporte necesario,
            % de avance y estado.
        """
        metas = self._repo.entidades()
        if not metas:
            return pd.DataFrame(
                columns=[
                    "id",
                    "objetivo",
                    "tipo",
                    "monto_meta",
                    "acumulado",
                    "aporte_mensual_planeado",
                    "fecha_limite",
                    "meses_restantes",
                    "aporte_necesario",
                    "pct_avance",
                    "estado",
                    "prioridad",
                    "vehiculo",
                    "notas",
                ]
            )

        return pd.DataFrame(
            [
                {
                    "id": meta.id,
                    "objetivo": meta.objetivo,
                    "tipo": meta.tipo,
                    "monto_meta": meta.monto_meta,
                    "acumulado": meta.acumulado,
                    "aporte_mensual_planeado": meta.aporte_mensual_planeado,
                    "fecha_limite": meta.fecha_limite,
                    "meses_restantes": meta.meses_restantes(hoy),
                    "aporte_necesario": meta.aporte_necesario(hoy),
                    "pct_avance": meta.pct_avance,
                    "estado": str(meta.estado(hoy)),
                    "prioridad": str(meta.prioridad),
                    "vehiculo": meta.vehiculo,
                    "notas": meta.notas,
                }
                for meta in metas
            ]
        )

    def aporte_mensual_comprometido(self) -> float:
        """Suma de las aportaciones mensuales planeadas de metas abiertas."""
        return sum(
            meta.aporte_mensual_planeado
            for meta in self._repo.entidades()
            if meta.acumulado < meta.monto_meta
        )

    def crear(
        self,
        objetivo: str,
        monto_meta: float,
        tipo: str = "Seguridad",
        acumulado: float = 0.0,
        aporte_mensual_planeado: float = 0.0,
        fecha_limite: date | None = None,
        prioridad: str = Prioridad.MEDIA,
        vehiculo: str = "",
        notas: str = "",
    ) -> int:
        """Da de alta una meta y devuelve su id."""
        meta = Meta(
            objetivo=_validar_objetivo(objetivo),
            monto_meta=_validar_monto(monto_meta),
            tipo=tipo,
            acumulado=max(0.0, float(acumulado)),
            aporte_mensual_planeado=max(0.0, float(aporte_mensual_planeado)),
            fecha_limite=fecha_limite,
            prioridad=Prioridad(prioridad),
            vehiculo=vehiculo,
            notas=notas,
        )

        return self._repo.crear(meta)

    def actualizar(self, meta_id: int, **campos: object) -> None:
        """Actualiza los campos indicados de una meta existente."""
        metas = {meta.id: meta for meta in self._repo.entidades()}
        meta = metas.get(meta_id)
        if meta is None:
            raise ValueError(f"No existe la meta {meta_id}.")

        for campo, valor in campos.items():
            if not hasattr(meta, campo):
                raise ValueError(f"La meta no tiene el campo «{campo}».")
            setattr(meta, campo, valor)

        meta.objetivo = _validar_objetivo(meta.objetivo)
        meta.monto_meta = _validar_monto(meta.monto_meta)

        self._repo.actualizar(meta_id, meta)

    def abonar(self, meta_id: int, monto: float) -> None:
        """Suma una aportación al acumulado de la meta."""
        if monto <= 0:
            raise ValueError("La aportación debe ser mayor que cero.")

        self._repo.abonar(meta_id, monto)

    def eliminar(self, meta_id: int) -> None:
        """Elimina una meta."""
        self._repo.eliminar(meta_id)


def _validar_objetivo(objetivo: str) -> str:
    """Normaliza y valida el nombre del objetivo."""
    limpio = (objetivo or "").strip()
    if not limpio:
        raise ValueError("La meta necesita un nombre.")

    return limpio


def _validar_monto(monto: float) -> float:
    """Valida que la meta tenga un monto alcanzable."""
    valor = float(monto)
    if valor <= 0:
        raise ValueError("El monto de la meta debe ser mayor que cero.")

    return valor
