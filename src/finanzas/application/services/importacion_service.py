from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from finanzas.data.lectores import ResultadoLectura, leer
from finanzas.data.lectores.base import MovimientoImportado
from finanzas.data.lectores.extraccion import extraer_lineas
from finanzas.data.repositories.movimientos_repository import MovimientosRepository
from finanzas.domain.enums import Naturaleza, Necesidad, TipoMovimiento

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# Importación de estados de cuenta
#
# Entre leer el documento y guardar movimientos hay un paso que
# no se puede saltar: decidir cuáles ya están registrados. El
# criterio es fecha y monto, porque la descripción del banco no
# se parece a la que uno escribe.
#
# Nada se guarda aquí. El servicio prepara candidatos; el
# usuario los revisa, completa y confirma.
# ═══════════════════════════════════════════════════════════

#: Margen de días para considerar que dos movimientos son el mismo.
#: La fecha del banco es la de aplicación y la capturada suele ser la de
#: compra, así que exigir coincidencia exacta dejaría pasar duplicados.
TOLERANCIA_DIAS = 3

NUEVO = "Nuevo"
DUPLICADO = "Ya registrado"
POSIBLE = "Posible duplicado"


@dataclass(slots=True)
class Producto:
    """
    Un artículo dentro de una compra de varias cosas.

    No parte el movimiento: el gasto sigue siendo uno, con una categoría,
    y esto dice qué había dentro. El desglose puede quedarse a medias a
    propósito —tres artículos de un ticket de veinte— y por eso no se
    exige que cuadre.
    """

    producto: str = ""
    cantidad: float = 1.0
    precio_unitario: float = 0.0
    nota: str = ""

    @property
    def importe(self) -> float:
        """Lo que costó la partida completa."""
        return round(self.cantidad * self.precio_unitario, 2)


@dataclass(slots=True)
class Candidato:
    """
    Una línea leída, con lo que hace falta para decidir sobre ella.

    `estado` es informativo, no una orden: la app enseña por qué se cree
    que ya existe y deja que el usuario decida.
    """

    origen: MovimientoImportado
    estado: str = NUEVO
    motivo: str = ""
    incluir: bool = True

    #: Lo que el usuario completa después de leer el documento.
    categoria_id: int | None = None
    subcategoria_id: int | None = None
    cuenta_id: int | None = None
    medio_pago_id: int | None = None
    descripcion: str = ""
    necesidad: str = Necesidad.ESENCIAL
    naturaleza: str = Naturaleza.VARIABLE
    proyecto: str = ""
    nota: str = ""
    pagado: bool = False

    #: Artículos que venían en la compra. No parten el movimiento: es un
    #: solo gasto con una categoría, y esto es su detalle.
    productos: list[Producto] = field(default_factory=list)

    #: De lo que trae el documento, sólo el tipo se corrige: el banco no
    #: distingue una devolución de un pago de tarjeta. Fecha y monto no
    #: están aquí a propósito: son con lo que se reconoce el movimiento al
    #: reimportar, y corregidos dejarían de coincidir con lo que el banco
    #: va a repetir.
    tipo_elegido: str = ""
    fecha_pago: date | None = None

    #: Campos que el documento no trae y el movimiento sí admite.
    etiquetas: str = ""
    recurrente: bool = False
    planeado: bool = True
    #: Confirmado o Pendiente. Se llama así y no `estado` porque ése ya
    #: es el de la deduplicación, que responde otra pregunta.
    estado_movimiento: str = "Confirmado"
    cuenta_destino_id: int | None = None

    #: Marca que el usuario ya revisó este movimiento, para saber por
    #: dónde iba si sale del asistente y vuelve.
    revisado: bool = False

    #: Ids de los movimientos ya escritos en la base. Vacío significa que
    #: todavía no se guardó; con contenido, volver a guardarlo reemplaza
    #: en vez de duplicar.
    guardados: list[int] = field(default_factory=list)

    @property
    def ya_guardado(self) -> bool:
        """Indica si el candidato ya se escribió en la base."""
        return bool(self.guardados)

    @property
    def tipo_sugerido(self) -> str:
        """
        Tipo que le corresponde según lo que dice el documento.

        El pago de una tarjeta es un traspaso entre cuentas propias: si se
        registrara como gasto, contaría otra vez el consumo que ya se contó
        al comprar. Un abono que no es pago —una devolución, una
        bonificación— queda como ingreso y el usuario decide.
        """
        if self.origen.es_pago_tarjeta:
            return str(TipoMovimiento.TRANSFERENCIA)
        return str(
            TipoMovimiento.GASTO if self.origen.es_cargo else TipoMovimiento.INGRESO
        )

    @property
    def tipo(self) -> str:
        """El tipo elegido por el usuario, o el sugerido si no lo cambió."""
        return self.tipo_elegido or self.tipo_sugerido

    @property
    def desglosado(self) -> float:
        """Cuánto del monto está detallado en productos."""
        return round(sum(p.importe for p in self.productos), 2)

    @property
    def sin_desglosar(self) -> float:
        """
        Cuánto del monto no está detallado.

        Puede quedarse en positivo sin que sea un error: desglosar de más
        sí lo es, porque inventaría artículos que el cobro no cubre.
        """
        return round(self.origen.monto - self.desglosado, 2)

    @property
    def desglose_excedido(self) -> bool:
        """Indica si los productos suman más de lo que se cobró."""
        return self.sin_desglosar < -0.01

    @property
    def completo(self) -> bool:
        """Indica si ya tiene lo mínimo para poder guardarse."""
        return (
            self.categoria_id is not None
            and self.cuenta_id is not None
            and not self.desglose_excedido
        )


@dataclass(slots=True)
class ResultadoImportacion:
    """Lo leído de un documento, ya contrastado con lo registrado."""

    lectura: ResultadoLectura
    candidatos: list[Candidato] = field(default_factory=list)

    @property
    def nuevos(self) -> list[Candidato]:
        """Los que no parecen estar ya registrados."""
        return [c for c in self.candidatos if c.estado == NUEVO]

    @property
    def duplicados(self) -> list[Candidato]:
        """Los que ya parecen estar registrados."""
        return [c for c in self.candidatos if c.estado != NUEVO]

    @property
    def a_importar(self) -> list[Candidato]:
        """Los marcados para guardar."""
        return [c for c in self.candidatos if c.incluir]


class ImportacionService:
    """Lee estados de cuenta y prepara sus movimientos para revisión."""

    def __init__(
        self,
        repositorio: MovimientosRepository | None = None,
        db_path: str | None = None,
    ) -> None:
        self._repo = repositorio or MovimientosRepository(db_path)
        self._db_path = db_path

    def leer_documento(
        self, contenido: bytes, contrasena: str | None = None
    ) -> ResultadoImportacion:
        """
        Lee un estado de cuenta y marca qué movimientos ya están registrados.

        Parameters
        ----------
        contenido : bytes
            El archivo tal cual, PDF o CSV.
        contrasena : str, optional
            Para los PDF cifrados.

        Raises
        ------
        DocumentoNoReconocidoError
            Si ningún lector reconoce el formato.
        PdfProtegidoError
            Si el PDF pide contraseña y no se dio la correcta.
        """
        lineas = extraer_lineas(contenido, contrasena)
        lectura = leer(lineas)
        logger.info(
            "Leídos %s movimientos de %s", len(lectura.movimientos), lectura.banco
        )

        candidatos = [Candidato(origen=m) for m in lectura.movimientos]
        self._marcar_duplicados(candidatos, lectura)

        return ResultadoImportacion(lectura=lectura, candidatos=candidatos)

    def _marcar_duplicados(
        self, candidatos: list[Candidato], lectura: ResultadoLectura
    ) -> None:
        """
        Compara contra lo registrado por fecha y monto.

        Cuenta ocurrencias en vez de colapsarlas: dos cafés de cincuenta
        pesos el mismo día son dos movimientos legítimos, así que si la
        base tiene uno y el documento trae dos, el segundo es nuevo.
        """
        if not candidatos:
            return

        # Donde el banco da folio no hace falta adivinar: el mismo folio
        # es el mismo movimiento, aunque la fecha o el monto difieran.
        conocidas = self._repo.referencias_externas(
            [c.origen.referencia for c in candidatos]
        )
        for candidato in candidatos:
            referencia = candidato.origen.referencia
            if referencia and referencia in conocidas:
                candidato.estado = DUPLICADO
                candidato.incluir = False
                candidato.motivo = f"Ya se importó el folio {referencia}."

        registrados = self._registrados_del_rango(lectura)
        if registrados.empty:
            return

        usados: set[int] = set()

        for candidato in candidatos:
            if candidato.estado != NUEVO:
                continue

            indice = self._buscar(candidato, registrados, usados, exacto=True)
            if indice is not None:
                usados.add(indice)
                candidato.estado = DUPLICADO
                candidato.incluir = False
                candidato.motivo = (
                    f"Ya hay un movimiento del {candidato.origen.fecha:%d/%m/%Y} "
                    f"por {candidato.origen.monto:,.2f}."
                )
                continue

            indice = self._buscar(candidato, registrados, usados, exacto=False)
            if indice is not None:
                usados.add(indice)
                fila = registrados.loc[indice]
                candidato.estado = POSIBLE
                candidato.incluir = False
                candidato.motivo = (
                    f"Hay uno por el mismo monto el "
                    f"{fila['fecha']:%d/%m/%Y}, a "
                    f"{abs((fila['fecha'].date() - candidato.origen.fecha).days)} "
                    f"días de diferencia."
                )

    def _registrados_del_rango(self, lectura: ResultadoLectura) -> pd.DataFrame:
        """Trae sólo los movimientos que podrían chocar con lo leído."""
        if lectura.periodo_inicio is None or lectura.periodo_fin is None:
            fechas = [m.fecha for m in lectura.movimientos]
            if not fechas:
                return pd.DataFrame()
            desde, hasta = min(fechas), max(fechas)
        else:
            desde, hasta = lectura.periodo_inicio, lectura.periodo_fin

        margen = timedelta(days=TOLERANCIA_DIAS)
        return self._repo.listar(desde=desde - margen, hasta=hasta + margen)

    def _buscar(
        self,
        candidato: Candidato,
        registrados: pd.DataFrame,
        usados: set[int],
        exacto: bool,
    ) -> int | None:
        """
        Busca en lo registrado una coincidencia todavía sin emparejar.

        Devuelve el índice de la fila, o None si no hay ninguna libre.
        """
        monto = round(candidato.origen.monto, 2)
        iguales = registrados[(registrados["monto"].round(2) - monto).abs() < 0.005]
        if iguales.empty:
            return None

        fecha = candidato.origen.fecha
        for indice, fila in iguales.iterrows():
            if indice in usados:
                continue

            distancia = abs((fila["fecha"].date() - fecha).days)
            if exacto and distancia == 0:
                return int(indice)
            if not exacto and distancia <= TOLERANCIA_DIAS:
                return int(indice)

        return None

    # ── Avance guardado ──────────────────────────────────

    def guardar_avance(
        self, resultado: ResultadoImportacion, indice: int, paso: str
    ) -> None:
        """Deja el avance en la base, para poder reanudarlo tras recargar."""
        from finanzas.data.database import connect

        with connect(self._db_path) as conexion:
            conexion.execute(
                """
                INSERT INTO importacion_en_curso
                    (id, banco, estado, indice, paso, actualizado_en)
                VALUES (1, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(id) DO UPDATE SET
                    banco = excluded.banco,
                    estado = excluded.estado,
                    indice = excluded.indice,
                    paso = excluded.paso,
                    actualizado_en = excluded.actualizado_en
                """,
                (resultado.lectura.banco, serializar(resultado), indice, paso),
            )

    def recuperar_avance(self) -> tuple | None:
        """
        Devuelve el avance guardado, o None si no hay ninguno.

        Returns
        -------
        tuple or None
            `(resultado, indice, paso)` listo para continuar.
        """
        from finanzas.data.database import connect

        with connect(self._db_path) as conexion:
            fila = conexion.execute(
                "SELECT estado, indice, paso FROM importacion_en_curso WHERE id = 1"
            ).fetchone()

        if fila is None:
            return None

        try:
            resultado = deserializar(fila["estado"])
        except (ValueError, KeyError, TypeError):
            # Un avance de una versión anterior no se puede reanudar, pero
            # tampoco debe impedir importar: se descarta y se sigue.
            logger.warning("El avance guardado no se pudo leer; se descarta")
            self.olvidar_avance()
            return None

        return resultado, int(fila["indice"]), fila["paso"]

    def olvidar_avance(self) -> None:
        """Borra el avance guardado, al terminar o al empezar de nuevo."""
        from finanzas.data.database import connect

        with connect(self._db_path) as conexion:
            conexion.execute("DELETE FROM importacion_en_curso WHERE id = 1")

    def guardar_uno(
        self, candidato: Candidato, cuenta_id: int, pagado: bool = False
    ) -> int:
        """
        Guarda o actualiza un solo candidato.

        Se llama al avanzar en el asistente, de modo que lo capturado no
        dependa de llegar al final. Si el candidato ya se había guardado,
        reemplaza sus movimientos en vez de duplicarlos: volver atrás a
        corregir es una operación normal, no un accidente.

        Returns
        -------
        int
            Cuántos movimientos quedaron escritos para este candidato.
        """
        if candidato.ya_guardado:
            self._repo.eliminar_muchos(candidato.guardados)
            candidato.guardados = []

        if not (candidato.completo and candidato.incluir):
            return 0

        candidato.guardados = self._escribir(candidato, cuenta_id, pagado)
        return len(candidato.guardados)

    def descartar(self, candidato: Candidato) -> None:
        """
        Quita de la base lo que se hubiera guardado de este candidato.

        Es lo que hace falta al desmarcarlo después de haberlo guardado,
        para que saltar un movimiento signifique lo mismo antes y después
        de haber avanzado.
        """
        if candidato.ya_guardado:
            self._repo.eliminar_muchos(candidato.guardados)
            candidato.guardados = []

    def guardar(
        self, candidatos: list[Candidato], cuenta_id: int, pagado: bool = True
    ) -> int:
        """
        Registra los candidatos ya completados.

        Parameters
        ----------
        cuenta_id : int
            Cuenta del estado de cuenta, que se usa para los candidatos
            que no traigan una propia.
        pagado : bool
            Si el movimiento ya salió de la caja. En una tarjeta de
            crédito conviene dejarlo en False: el consumo se incurrió pero
            se paga hasta el corte.

        Returns
        -------
        int
            Cuántos se guardaron.
        """
        guardados = 0
        for candidato in candidatos:
            guardados += self.guardar_uno(candidato, cuenta_id, pagado)

        logger.info("Importados %s movimientos", guardados)
        return guardados

    def _escribir(
        self, candidato: Candidato, cuenta_id: int, pagado: bool
    ) -> list[int]:
        """
        Escribe el candidato en la base y devuelve los ids creados.

        Un candidato desglosado produce un movimiento por parte; sin
        desglose, uno solo.
        """
        from finanzas.application.services.movimientos_service import (
            MovimientosService,
        )
        from finanzas.data.repositories.productos_repository import (
            ProductosRepository,
        )
        from finanzas.domain.entities import ProductoDeMovimiento

        servicio = MovimientosService(self._repo)

        movimiento_id = servicio.registrar(
            fecha=candidato.origen.fecha,
            tipo=candidato.tipo,
            monto=candidato.origen.monto,
            categoria_id=candidato.categoria_id,
            cuenta_id=candidato.cuenta_id or cuenta_id,
            subcategoria_id=candidato.subcategoria_id,
            cuenta_destino_id=candidato.cuenta_destino_id,
            medio_pago_id=candidato.medio_pago_id,
            descripcion=candidato.descripcion or candidato.origen.descripcion_banco,
            necesidad=candidato.necesidad,
            naturaleza=candidato.naturaleza,
            recurrente=candidato.recurrente,
            planeado=candidato.planeado,
            proyecto=candidato.proyecto,
            etiquetas=candidato.etiquetas,
            nota=candidato.nota,
            estado=candidato.estado_movimiento,
            fecha_pago=self._fecha_pago(candidato, pagado),
            # El concepto del banco va en su propia columna, no dentro de
            # la nota: la descripción se edita para rastrear y ésta se
            # conserva intacta para auditar contra el documento.
            descripcion_banco=candidato.origen.descripcion_banco,
            referencia_externa=candidato.origen.referencia,
        )

        # Los productos son detalle del movimiento, no movimientos: cuelgan
        # de él y se van con él si se borra.
        if candidato.productos:
            ProductosRepository(self._db_path).reemplazar(
                movimiento_id,
                [
                    ProductoDeMovimiento(
                        producto=p.producto,
                        cantidad=p.cantidad,
                        precio_unitario=p.precio_unitario,
                        nota=p.nota,
                    )
                    for p in candidato.productos
                    if p.producto.strip()
                ],
            )

        return [movimiento_id]

    def _fecha_pago(self, candidato: Candidato, pagado: bool) -> date | None:
        """Resuelve cuándo salió el dinero, si es que ya salió."""
        if not (candidato.pagado or pagado):
            return None

        return (
            candidato.fecha_pago
            or candidato.origen.fecha_cargo
            or candidato.origen.fecha
        )


# ═══════════════════════════════════════════════════════════
# Persistencia del avance
#
# `session_state` de Streamlit sobrevive los reruns pero no una
# recarga del navegador. Lo capturado en el asistente no puede
# depender de que nadie toque F5, así que el avance se guarda
# en la base y se reanuda al volver.
# ═══════════════════════════════════════════════════════════


def _a_json(valor: object) -> object:
    """Convierte fechas a texto para poder serializar el avance."""
    if isinstance(valor, date):
        return valor.isoformat()

    return valor


def serializar(resultado: ResultadoImportacion) -> str:
    """Convierte el avance en texto guardable."""
    return json.dumps(
        {
            "banco": resultado.lectura.banco,
            "periodo_inicio": _a_json(resultado.lectura.periodo_inicio),
            "periodo_fin": _a_json(resultado.lectura.periodo_fin),
            "saldo_inicial": resultado.lectura.saldo_inicial,
            "saldo_final": resultado.lectura.saldo_final,
            "total_cargos": resultado.lectura.total_cargos,
            "total_abonos": resultado.lectura.total_abonos,
            "candidatos": [
                {
                    "origen": {
                        campo: _a_json(getattr(c.origen, campo))
                        for campo in (
                            "fecha",
                            "monto",
                            "descripcion_banco",
                            "es_cargo",
                            "fecha_cargo",
                            "referencia",
                            "categoria_banco",
                            "es_pago_tarjeta",
                        )
                    },
                    "estado": c.estado,
                    "motivo": c.motivo,
                    "incluir": c.incluir,
                    "categoria_id": c.categoria_id,
                    "subcategoria_id": c.subcategoria_id,
                    "cuenta_id": c.cuenta_id,
                    "medio_pago_id": c.medio_pago_id,
                    "descripcion": c.descripcion,
                    "necesidad": str(c.necesidad),
                    "naturaleza": str(c.naturaleza),
                    "proyecto": c.proyecto,
                    "nota": c.nota,
                    "pagado": c.pagado,
                    "revisado": c.revisado,
                    "guardados": c.guardados,
                    "tipo_elegido": c.tipo_elegido,
                    "fecha_pago": _a_json(c.fecha_pago),
                    "etiquetas": c.etiquetas,
                    "recurrente": c.recurrente,
                    "planeado": c.planeado,
                    "estado_movimiento": c.estado_movimiento,
                    "cuenta_destino_id": c.cuenta_destino_id,
                    "productos": [
                        {
                            "producto": p.producto,
                            "cantidad": p.cantidad,
                            "precio_unitario": p.precio_unitario,
                            "nota": p.nota,
                        }
                        for p in c.productos
                    ],
                }
                for c in resultado.candidatos
            ],
        }
    )


def _fecha_de(texto: str | None) -> date | None:
    """Reconstruye una fecha desde el avance guardado."""
    return date.fromisoformat(texto) if texto else None


def deserializar(crudo: str) -> ResultadoImportacion:
    """Reconstruye el avance guardado."""
    datos = json.loads(crudo)
    lectura = ResultadoLectura(
        banco=datos["banco"],
        periodo_inicio=_fecha_de(datos.get("periodo_inicio")),
        periodo_fin=_fecha_de(datos.get("periodo_fin")),
        saldo_inicial=datos.get("saldo_inicial"),
        saldo_final=datos.get("saldo_final"),
        total_cargos=datos.get("total_cargos"),
        total_abonos=datos.get("total_abonos"),
    )

    candidatos = []
    for crudo_candidato in datos["candidatos"]:
        origen_datos = dict(crudo_candidato["origen"])
        origen_datos["fecha"] = _fecha_de(origen_datos["fecha"])
        origen_datos["fecha_cargo"] = _fecha_de(origen_datos.get("fecha_cargo"))
        origen = MovimientoImportado(**origen_datos)
        lectura.movimientos.append(origen)

        candidatos.append(
            Candidato(
                origen=origen,
                estado=crudo_candidato["estado"],
                motivo=crudo_candidato["motivo"],
                incluir=crudo_candidato["incluir"],
                categoria_id=crudo_candidato["categoria_id"],
                subcategoria_id=crudo_candidato["subcategoria_id"],
                cuenta_id=crudo_candidato["cuenta_id"],
                medio_pago_id=crudo_candidato["medio_pago_id"],
                descripcion=crudo_candidato["descripcion"],
                necesidad=crudo_candidato["necesidad"],
                naturaleza=crudo_candidato["naturaleza"],
                proyecto=crudo_candidato["proyecto"],
                nota=crudo_candidato["nota"],
                pagado=crudo_candidato["pagado"],
                revisado=crudo_candidato["revisado"],
                guardados=list(crudo_candidato["guardados"]),
                tipo_elegido=crudo_candidato.get("tipo_elegido", ""),
                fecha_pago=_fecha_de(crudo_candidato.get("fecha_pago")),
                etiquetas=crudo_candidato.get("etiquetas", ""),
                recurrente=crudo_candidato.get("recurrente", False),
                planeado=crudo_candidato.get("planeado", True),
                estado_movimiento=crudo_candidato.get(
                    "estado_movimiento", "Confirmado"
                ),
                cuenta_destino_id=crudo_candidato.get("cuenta_destino_id"),
                productos=[Producto(**p) for p in crudo_candidato.get("productos", [])],
            )
        )

    return ResultadoImportacion(lectura=lectura, candidatos=candidatos)


def tabla_de(resultado: ResultadoImportacion) -> pd.DataFrame:
    """Arma la tabla que la página muestra tras leer el documento."""
    if not resultado.candidatos:
        return pd.DataFrame()

    return pd.DataFrame(
        [
            {
                "incluir": c.incluir,
                "fecha": c.origen.fecha,
                "descripcion_banco": c.origen.descripcion_banco,
                "monto": c.origen.monto,
                "tipo": c.tipo,
                "categoria_banco": c.origen.categoria_banco,
                "estado": c.estado,
                "motivo": c.motivo,
            }
            for c in resultado.candidatos
        ]
    )
