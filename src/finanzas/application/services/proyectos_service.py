from __future__ import annotations

import sqlite3
from datetime import date

import pandas as pd

from finanzas.data.repositories.proyectos_repository import ProyectosRepository
from finanzas.domain.entities import Proyecto

# ═══════════════════════════════════════════════════════════
# Proyectos
#
# El catálogo existe para poder crear un proyecto antes de
# gastarle: ponerle presupuesto y fechas de antemano es lo que
# permite ver a media obra si va a alcanzar.
# ═══════════════════════════════════════════════════════════


class ProyectoInvalidoError(ValueError):
    """El proyecto no tiene los datos mínimos para guardarse."""


class NombreDeProyectoDuplicadoError(ProyectoInvalidoError):
    """Ya existe un proyecto con ese nombre."""


class ProyectosService:
    """Casos de uso sobre los proyectos."""

    def __init__(self, repositorio: ProyectosRepository | None = None) -> None:
        self._repo = repositorio or ProyectosRepository()

    def listar(self, solo_activos: bool = False) -> pd.DataFrame:
        """Devuelve los proyectos con su gasto acumulado y su avance."""
        df = self._repo.listar(solo_activos=solo_activos)
        if df.empty:
            return df

        df = df.copy()
        # Sin presupuesto no hay proporción que calcular: queda en cero y
        # la interfaz lo muestra como «sin presupuesto» en vez de 0%.
        df["pct_usado"] = df.apply(
            lambda fila: (
                fila["gasto"] / fila["presupuesto"] if fila["presupuesto"] > 0 else 0.0
            ),
            axis=1,
        )

        return df

    def nombres(self, solo_activos: bool = False) -> list[str]:
        """Devuelve los nombres para poblar los selectores de captura."""
        return self._repo.nombres(solo_activos=solo_activos)

    def crear(
        self,
        nombre: str,
        descripcion: str = "",
        presupuesto: float = 0.0,
        fecha_inicio: date | None = None,
        fecha_fin: date | None = None,
        notas: str = "",
    ) -> int:
        """
        Da de alta un proyecto.

        Raises
        ------
        ProyectoInvalidoError
            Si falta el nombre, el presupuesto es negativo o las fechas
            están invertidas.
        NombreDeProyectoDuplicadoError
            Si ya hay uno con ese nombre.
        """
        proyecto = Proyecto(
            nombre=_validar_nombre(nombre),
            descripcion=descripcion,
            presupuesto=_validar_presupuesto(presupuesto),
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
            notas=notas,
        )
        _validar_fechas(proyecto)

        try:
            return self._repo.crear(proyecto)
        except sqlite3.IntegrityError as error:
            raise NombreDeProyectoDuplicadoError(
                f"Ya existe un proyecto llamado «{proyecto.nombre}»."
            ) from error

    def actualizar(self, proyecto_id: int, **campos: object) -> None:
        """
        Actualiza los campos indicados de un proyecto.

        Renombrarlo arrastra sus movimientos: el vínculo es por nombre y
        dejarlos atrás partiría el proyecto en dos.
        """
        df = self._repo.listar()
        fila = df[df["proyecto_id"] == proyecto_id]
        if fila.empty:
            raise ProyectoInvalidoError(f"No existe el proyecto {proyecto_id}.")

        actual = fila.iloc[0]
        proyecto = Proyecto(
            nombre=actual["proyecto"],
            id=proyecto_id,
            descripcion=actual["descripcion"],
            presupuesto=float(actual["presupuesto"]),
            fecha_inicio=_fecha_o_nulo(actual["fecha_inicio"]),
            fecha_fin=_fecha_o_nulo(actual["fecha_fin"]),
            activo=bool(actual["activo"]),
            notas=actual["notas"],
        )

        for campo, valor in campos.items():
            if not hasattr(proyecto, campo):
                raise ProyectoInvalidoError(f"El proyecto no tiene «{campo}».")
            setattr(proyecto, campo, valor)

        proyecto.nombre = _validar_nombre(proyecto.nombre)
        proyecto.presupuesto = _validar_presupuesto(proyecto.presupuesto)
        _validar_fechas(proyecto)

        try:
            self._repo.actualizar(proyecto_id, proyecto)
        except sqlite3.IntegrityError as error:
            raise NombreDeProyectoDuplicadoError(
                f"Ya existe un proyecto llamado «{proyecto.nombre}»."
            ) from error

    def cerrar(self, proyecto_id: int) -> None:
        """Marca un proyecto como terminado sin perder su histórico."""
        self.actualizar(proyecto_id, activo=False)

    def reabrir(self, proyecto_id: int) -> None:
        """Vuelve a poner en curso un proyecto cerrado."""
        self.actualizar(proyecto_id, activo=True)

    def adoptar(self, nombre: str) -> int:
        """
        Le da ficha a un proyecto que sólo existía como texto.

        Los proyectos anteriores al catálogo viven en los movimientos y no
        tienen presupuesto ni fechas; esto se las permite poner.
        """
        try:
            return self._repo.adoptar(_validar_nombre(nombre))
        except sqlite3.IntegrityError as error:
            raise NombreDeProyectoDuplicadoError(
                f"«{nombre}» ya está en el catálogo."
            ) from error

    def eliminar(self, proyecto_id: int, desligar: bool = True) -> None:
        """
        Elimina un proyecto del catálogo.

        Por defecto desliga sus movimientos en vez de borrarlos: el gasto
        ocurrió aunque el proyecto deje de interesar.
        """
        self._repo.eliminar(proyecto_id, desligar=desligar)


def _validar_nombre(nombre: str) -> str:
    """Normaliza y valida el nombre de un proyecto."""
    limpio = (nombre or "").strip()
    if not limpio:
        raise ProyectoInvalidoError("El proyecto necesita un nombre.")
    if len(limpio) > 80:
        raise ProyectoInvalidoError("El nombre no puede exceder 80 caracteres.")

    return limpio


def _validar_presupuesto(presupuesto: float) -> float:
    """Valida el presupuesto del proyecto."""
    valor = float(presupuesto or 0)
    if valor < 0:
        raise ProyectoInvalidoError("El presupuesto no puede ser negativo.")

    return valor


def _validar_fechas(proyecto: Proyecto) -> None:
    """Exige que el proyecto no termine antes de empezar."""
    if proyecto.fecha_inicio and proyecto.fecha_fin:
        if proyecto.fecha_fin < proyecto.fecha_inicio:
            raise ProyectoInvalidoError(
                "La fecha de fin no puede ser anterior a la de inicio."
            )


def _fecha_o_nulo(valor: object) -> date | None:
    """Convierte a `date` cuidando los NaT que llegan desde pandas."""
    if valor is None or pd.isna(valor):
        return None
    if isinstance(valor, date):
        return valor

    return pd.Timestamp(valor).date()
