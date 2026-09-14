from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from finanzas.data.repositories.movimientos_repository import MovimientosRepository
from finanzas.domain.entities import Movimiento
from finanzas.domain.enums import (
    EstadoMovimiento,
    Naturaleza,
    Necesidad,
    TipoMovimiento,
)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# Movimientos: captura, edición y consulta.
#
# La regla que más se rompe al capturar es el signo: aquí el
# monto siempre entra positivo y el tipo define el efecto.
# ═══════════════════════════════════════════════════════════

#: Tope defensivo para un solo movimiento; atrapa ceros de más al teclear.
MONTO_MAXIMO = 100_000_000.0


class MovimientoInvalidoError(ValueError):
    """El movimiento no cumple las reglas mínimas de captura."""


class _SinEspecificar:
    """
    Centinela para distinguir «no me lo dijiste» de «me dijiste None».

    Importa en la fecha de pago: omitirla registra un gasto ya pagado,
    que es el caso común, mientras que pasar None a propósito registra un
    devengado. Sin el centinela, un llamador que olvidara el campo
    —el importador de Excel, la futura API— inventaría deudas en silencio.
    """


_SIN_FECHA_PAGO = _SinEspecificar()


class MovimientosService:
    """Casos de uso sobre los movimientos del usuario."""

    def __init__(self, repositorio: MovimientosRepository | None = None) -> None:
        self._repo = repositorio or MovimientosRepository()

    # ── Consulta ─────────────────────────────────────────

    def buscar(
        self,
        desde: date | None = None,
        hasta: date | None = None,
        tipos: list[str] | None = None,
        categorias: list[str] | None = None,
        cuentas: list[str] | None = None,
        estado: str | None = None,
        texto: str | None = None,
        limite: int | None = None,
        solo_por_pagar: bool = False,
        proyectos: list[str] | None = None,
    ) -> pd.DataFrame:
        """Devuelve los movimientos que cumplen los filtros dados."""
        return self._repo.listar(
            desde=desde,
            hasta=hasta,
            tipos=tipos,
            categorias=categorias,
            cuentas=cuentas,
            estado=estado,
            texto=texto,
            limite=limite,
            solo_por_pagar=solo_por_pagar,
            proyectos=proyectos,
        )

    def proyectos(self) -> pd.DataFrame:
        """Devuelve el resumen de gasto e ingreso por proyecto."""
        return self._repo.proyectos()

    def nombres_de_proyecto(self) -> list[str]:
        """Devuelve los proyectos ya usados, para reutilizarlos al capturar."""
        return self._repo.nombres_de_proyecto()

    def referencias_externas(self, referencias: list[str]) -> set[str]:
        """Devuelve cuáles de esos folios de banco ya están registrados."""
        return self._repo.referencias_externas(referencias)

    def por_pagar(self) -> pd.DataFrame:
        """Devuelve los adeudos generados, del más viejo al más reciente."""
        return self._repo.por_pagar()

    def flujo_por_cuenta(self, periodo: str | None = None) -> pd.DataFrame:
        """Devuelve entradas, salidas y flujo neto por cuenta."""
        return self._repo.flujo_por_cuenta(periodo)

    def total_por_pagar(self) -> float:
        """Devuelve el saldo total de lo gastado que aún no se paga."""
        return self._repo.total_por_pagar()

    def del_periodo(self, periodo: str) -> pd.DataFrame:
        """Devuelve los movimientos de un periodo YYYY-MM."""
        return self._repo.del_periodo(periodo)

    def resumen_mensual(self) -> pd.DataFrame:
        """Devuelve el agregado de ingresos, gastos y ahorro por periodo."""
        return self._repo.resumen_mensual()

    def periodos_disponibles(self) -> list[str]:
        """Devuelve los periodos con movimientos, del más reciente al más viejo."""
        return self._repo.periodos_disponibles()

    def obtener(self, movimiento_id: int) -> pd.Series | None:
        """Devuelve un movimiento por id, o None si no existe."""
        return self._repo.obtener(movimiento_id)

    # ── Escritura ────────────────────────────────────────

    def registrar(
        self,
        fecha: date,
        tipo: str,
        monto: float,
        categoria_id: int,
        cuenta_id: int,
        subcategoria_id: int | None = None,
        cuenta_destino_id: int | None = None,
        medio_pago_id: int | None = None,
        descripcion: str = "",
        necesidad: str = Necesidad.ESENCIAL,
        naturaleza: str = Naturaleza.VARIABLE,
        recurrente: bool = False,
        planeado: bool = True,
        proyecto: str = "",
        etiquetas: str = "",
        nota: str = "",
        estado: str = EstadoMovimiento.CONFIRMADO,
        fecha_pago: date | None | _SinEspecificar = _SIN_FECHA_PAGO,
        descripcion_banco: str = "",
        referencia_externa: str = "",
    ) -> int:
        """
        Registra un movimiento nuevo.

        Parameters
        ----------
        fecha_pago : date or None, optional
            Cuándo salió el dinero. Si se omite, se asume pagado el día
            del gasto. Pasar None explícitamente registra un devengado:
            el gasto pesa en el presupuesto de `fecha` pero no toca la
            caja hasta que se pague.

        Returns
        -------
        int
            Identificador del movimiento creado.

        Raises
        ------
        MovimientoInvalidoError
            Si el monto no es positivo o falta un dato obligatorio.
        """
        if isinstance(fecha_pago, _SinEspecificar):
            fecha_pago = fecha

        movimiento = _construir(
            fecha=fecha,
            tipo=tipo,
            monto=monto,
            categoria_id=categoria_id,
            cuenta_id=cuenta_id,
            subcategoria_id=subcategoria_id,
            cuenta_destino_id=cuenta_destino_id,
            medio_pago_id=medio_pago_id,
            descripcion=descripcion,
            necesidad=necesidad,
            naturaleza=naturaleza,
            recurrente=recurrente,
            planeado=planeado,
            proyecto=proyecto,
            etiquetas=etiquetas,
            nota=nota,
            estado=estado,
            fecha_pago=fecha_pago,
            descripcion_banco=descripcion_banco,
            referencia_externa=referencia_externa,
        )

        movimiento_id = self._repo.crear(movimiento)
        logger.info(
            "Movimiento %s registrado: %s %s por %.2f",
            movimiento_id,
            movimiento.tipo,
            movimiento.descripcion or "sin descripción",
            movimiento.monto,
        )

        return movimiento_id

    def registrar_muchos(self, movimientos: list[Movimiento]) -> int:
        """Registra varios movimientos ya validados en una transacción."""
        for movimiento in movimientos:
            _validar(movimiento)

        return self._repo.crear_muchos(movimientos)

    def actualizar(self, movimiento_id: int, **campos: object) -> None:
        """
        Actualiza un movimiento existente.

        Recibe los mismos campos que `registrar`; los que no se pasen
        conservan su valor actual.
        """
        actual = self._repo.obtener(movimiento_id)
        if actual is None:
            raise MovimientoInvalidoError(f"No existe el movimiento {movimiento_id}.")

        datos: dict[str, object] = {
            "fecha": actual["fecha"].date(),
            "tipo": actual["tipo"],
            "monto": float(actual["monto"]),
            "categoria_id": int(actual["categoria_id"]),
            "cuenta_id": int(actual["cuenta_id"]),
            "subcategoria_id": _entero_o_nulo(actual["subcategoria_id"]),
            "cuenta_destino_id": _entero_o_nulo(actual["cuenta_destino_id"]),
            "medio_pago_id": _entero_o_nulo(actual["medio_pago_id"]),
            "descripcion": actual["descripcion"],
            "necesidad": actual["necesidad"],
            "naturaleza": actual["naturaleza"],
            "recurrente": bool(actual["recurrente"]),
            "planeado": bool(actual["planeado"]),
            "proyecto": actual["proyecto"],
            "etiquetas": actual["etiquetas"],
            "nota": actual["nota"],
            "estado": actual["estado"],
            "fecha_pago": _fecha_o_nulo(actual["fecha_pago"]),
            # Editar la descripción propia no debe borrar el original del
            # banco: son dos campos con dos propósitos.
            "descripcion_banco": actual["descripcion_banco"],
            "referencia_externa": actual["referencia_externa"],
        }
        datos.update(campos)

        self._repo.actualizar(movimiento_id, _construir(**datos))
        logger.info("Movimiento %s actualizado", movimiento_id)

    def duplicar(self, movimiento_id: int, nueva_fecha: date) -> int:
        """
        Copia un movimiento a otra fecha.

        Es el atajo para los gastos que se repiten cada mes sin cambiar.
        """
        actual = self._repo.obtener(movimiento_id)
        if actual is None:
            raise MovimientoInvalidoError(f"No existe el movimiento {movimiento_id}.")

        return self.registrar(
            fecha=nueva_fecha,
            tipo=actual["tipo"],
            monto=float(actual["monto"]),
            categoria_id=int(actual["categoria_id"]),
            cuenta_id=int(actual["cuenta_id"]),
            subcategoria_id=_entero_o_nulo(actual["subcategoria_id"]),
            cuenta_destino_id=_entero_o_nulo(actual["cuenta_destino_id"]),
            medio_pago_id=_entero_o_nulo(actual["medio_pago_id"]),
            descripcion=actual["descripcion"],
            necesidad=actual["necesidad"],
            naturaleza=actual["naturaleza"],
            recurrente=bool(actual["recurrente"]),
            planeado=bool(actual["planeado"]),
            proyecto=actual["proyecto"],
            etiquetas=actual["etiquetas"],
            nota=actual["nota"],
            estado=actual["estado"],
            # La copia nace sin pagar: repetir el gasto no repite su pago.
            fecha_pago=None,
            # Y sin rastro del banco: la copia no salió de ningún estado
            # de cuenta, así que heredarlo la haría parecer importada.
        )

    def eliminar(self, movimiento_id: int) -> None:
        """Elimina un movimiento."""
        self._repo.eliminar(movimiento_id)
        logger.info("Movimiento %s eliminado", movimiento_id)

    def eliminar_muchos(self, movimiento_ids: list[int]) -> int:
        """Elimina varios movimientos y devuelve cuántos se borraron."""
        borrados = self._repo.eliminar_muchos(movimiento_ids)
        logger.info("%s movimientos eliminados", borrados)
        return borrados

    def confirmar(self, movimiento_id: int) -> None:
        """Marca como confirmado un movimiento pendiente."""
        self.actualizar(movimiento_id, estado=EstadoMovimiento.CONFIRMADO)

    def marcar_pagado(self, movimiento_id: int, fecha_pago: date | None = None) -> None:
        """
        Liquida un adeudo generado: fija la fecha en que salió el dinero.

        A partir de ese momento el movimiento pesa en la caja del periodo
        de pago, sin cambiar el periodo en que consumió presupuesto.
        """
        self._repo.marcar_pagado(movimiento_id, fecha_pago or date.today())
        logger.info("Movimiento %s marcado como pagado", movimiento_id)

    def marcar_por_pagar(self, movimiento_id: int) -> None:
        """Devuelve un movimiento a devengado, si se marcó pagado por error."""
        self._repo.marcar_pagado(movimiento_id, None)
        logger.info("Movimiento %s devuelto a por pagar", movimiento_id)


# ═══════════════════════════════════════════════════════════
# Construcción y validación
# ═══════════════════════════════════════════════════════════


def _construir(**datos: object) -> Movimiento:
    """Arma un `Movimiento` desde datos sueltos y lo valida."""
    movimiento = Movimiento(
        fecha=datos["fecha"],  # type: ignore[arg-type]
        tipo=TipoMovimiento(datos["tipo"]),
        monto=float(datos["monto"]),  # type: ignore[arg-type]
        categoria_id=int(datos["categoria_id"]),  # type: ignore[arg-type]
        cuenta_id=int(datos["cuenta_id"]),  # type: ignore[arg-type]
        subcategoria_id=_entero_o_nulo(datos.get("subcategoria_id")),
        cuenta_destino_id=_entero_o_nulo(datos.get("cuenta_destino_id")),
        medio_pago_id=_entero_o_nulo(datos.get("medio_pago_id")),
        descripcion=str(datos.get("descripcion") or ""),
        necesidad=Necesidad(datos.get("necesidad") or Necesidad.ESENCIAL),
        naturaleza=Naturaleza(datos.get("naturaleza") or Naturaleza.VARIABLE),
        recurrente=bool(datos.get("recurrente", False)),
        planeado=bool(datos.get("planeado", True)),
        proyecto=str(datos.get("proyecto") or ""),
        etiquetas=str(datos.get("etiquetas") or ""),
        nota=str(datos.get("nota") or ""),
        estado=EstadoMovimiento(datos.get("estado") or EstadoMovimiento.CONFIRMADO),
        fecha_pago=datos.get("fecha_pago"),  # type: ignore[arg-type]
        descripcion_banco=str(datos.get("descripcion_banco") or ""),
        referencia_externa=str(datos.get("referencia_externa") or ""),
    )
    _validar(movimiento)

    return movimiento


def _validar(movimiento: Movimiento) -> None:
    """
    Comprueba las reglas mínimas de un movimiento.

    Raises
    ------
    MovimientoInvalidoError
        Con un mensaje dirigido al usuario, no al desarrollador.
    """
    if movimiento.monto <= 0:
        raise MovimientoInvalidoError(
            "El monto debe ser mayor que cero. Captúralo siempre en positivo: "
            "el tipo de movimiento define si suma o resta."
        )

    if movimiento.monto > MONTO_MAXIMO:
        raise MovimientoInvalidoError(
            f"El monto supera el máximo permitido ({MONTO_MAXIMO:,.0f}). "
            "Revisa si sobra algún cero."
        )

    if not movimiento.categoria_id:
        raise MovimientoInvalidoError("Selecciona una categoría.")

    if not movimiento.cuenta_id:
        raise MovimientoInvalidoError("Selecciona una cuenta.")

    if movimiento.cuenta_destino_id is not None:
        if movimiento.tipo != TipoMovimiento.TRANSFERENCIA:
            raise MovimientoInvalidoError(
                "La cuenta destino sólo aplica a una transferencia: es a "
                "dónde llega el dinero que sale de la cuenta de origen."
            )
        if movimiento.cuenta_destino_id == movimiento.cuenta_id:
            raise MovimientoInvalidoError(
                "El origen y el destino de una transferencia no pueden ser "
                "la misma cuenta."
            )


def _fecha_o_nulo(valor: object) -> date | None:
    """Convierte a `date` cuidando los NaT que llegan desde pandas."""
    if valor is None or pd.isna(valor):
        return None
    if isinstance(valor, date):
        return valor
    return pd.Timestamp(valor).date()


def _entero_o_nulo(valor: object) -> int | None:
    """Convierte a int cuidando los nulos que llegan desde pandas."""
    if valor is None or pd.isna(valor):
        return None
    return int(valor)
