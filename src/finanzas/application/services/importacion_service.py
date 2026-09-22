from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, time, timedelta

import pandas as pd

from finanzas.data.lectores import ResultadoLectura, leer
from finanzas.data.lectores.base import MovimientoImportado, consolidar_rendimientos
from finanzas.data.lectores.extraccion import extraer_lineas
from finanzas.data.repositories.movimientos_repository import MovimientosRepository
from finanzas.domain.enums import (
    Naturaleza,
    Necesidad,
    OrigenSaldo,
    TipoCuenta,
    TipoMovimiento,
)

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

#: Cuánto se abre el rango de fechas al buscar lo ya registrado. Es más
#: ancho que la tolerancia porque filtra por la fecha editable, que puede
#: estar corrida respecto a la del banco.
MARGEN_RANGO_DIAS = 45

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
    #: Si el dinero ya salió. Lo normal es que sí: el estado de cuenta
    #: es la prueba. Sólo un gasto desde débito puede desmarcarse, y con
    #: tarjeta ni se pregunta, porque lo pagó la tarjeta.
    pagado: bool = True

    #: Artículos que venían en la compra. No parten el movimiento: es un
    #: solo gasto con una categoría, y esto es su detalle.
    productos: list[Producto] = field(default_factory=list)

    #: De lo que trae el documento se corrigen el tipo —el banco no
    #: distingue una devolución de un pago de tarjeta— y la fecha —el
    #: banco pone la de aplicación y a veces se quiere la de compra—. El
    #: monto no: es lo que se cobró. La fecha original queda en `origen` y
    #: se guarda aparte como `fecha_banco`, que es con la que se reconoce
    #: el movimiento al reimportar.
    tipo_elegido: str = ""
    fecha: date | None = None
    fecha_pago: date | None = None

    #: Campos que el documento no trae y el movimiento sí admite.
    empresa: str = ""
    lugar: str = ""
    hora: str = ""
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
        if self.origen.es_traspaso:
            return str(TipoMovimiento.TRANSFERENCIA)
        return str(
            TipoMovimiento.GASTO if self.origen.es_cargo else TipoMovimiento.INGRESO
        )

    @property
    def tipo(self) -> str:
        """El tipo elegido por el usuario, o el sugerido si no lo cambió."""
        return self.tipo_elegido or self.tipo_sugerido

    @property
    def fecha_final(self) -> date:
        """La fecha corregida por el usuario, o la del documento."""
        return self.fecha or self.origen.fecha

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

    #: La cuenta de la que es el documento. Es el origen por defecto de
    #: cada candidato y la cuenta a la que se anclan los saldos que el
    #: documento declara.
    cuenta_id: int | None = None

    @property
    def anclas(self) -> list[tuple[date, float]]:
        """
        Los saldos que el documento declara, como (fecha, saldo visto).

        El saldo inicial es el cierre del día anterior al periodo; el
        final, el cierre del último día. Son la verdad del banco, y con
        ellos el sistema puede deducir el saldo de cualquier otro día y
        avisar si entre dos documentos falta algo.
        """
        lectura = self.lectura
        puntos: list[tuple[date, float]] = []
        if lectura.periodo_inicio and lectura.saldo_inicial is not None:
            puntos.append(
                (lectura.periodo_inicio - timedelta(days=1), lectura.saldo_inicial)
            )
        if lectura.periodo_fin and lectura.saldo_final is not None:
            puntos.append((lectura.periodo_fin, lectura.saldo_final))

        return puntos

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

        # Las ganancias de centavos se juntan en un total por mes antes de
        # proponer nada: nadie quiere revisar veinte líneas de un centavo.
        lectura.movimientos = consolidar_rendimientos(lectura.movimientos)

        candidatos = [Candidato(origen=m) for m in lectura.movimientos]
        resultado = ResultadoImportacion(lectura=lectura, candidatos=candidatos)
        resultado.cuenta_id = self.cuenta_sugerida(resultado)
        self.contrastar(resultado)

        return resultado

    def contrastar(self, resultado: ResultadoImportacion) -> None:
        """
        Marca qué candidatos ya están registrados, sabiendo de qué cuenta
        es el documento.

        Se vuelve a llamar si el usuario cambia la cuenta del documento:
        la misma cifra en otra cuenta no es el mismo movimiento, y un
        traspaso ya registrado sólo es «la otra pata» si esta cuenta es
        uno de sus dos extremos. Lo ya guardado no se toca.
        """
        for candidato in resultado.candidatos:
            if candidato.ya_guardado:
                continue
            candidato.estado = NUEVO
            candidato.motivo = ""
            candidato.incluir = True

        self._marcar_duplicados(
            [c for c in resultado.candidatos if not c.ya_guardado],
            resultado.lectura,
            resultado.cuenta_id,
        )

    def _marcar_duplicados(
        self,
        candidatos: list[Candidato],
        lectura: ResultadoLectura,
        cuenta_id: int | None = None,
    ) -> None:
        """
        Compara contra lo registrado por fecha, monto y cuenta.

        Cuenta ocurrencias en vez de colapsarlas: dos cafés de cincuenta
        pesos el mismo día son dos movimientos legítimos, así que si la
        base tiene uno y el documento trae dos, el segundo es nuevo.
        """
        if not candidatos:
            return

        # Donde el banco da folio no hace falta adivinar: el mismo folio
        # es el mismo movimiento, aunque la fecha o el monto difieran. Un
        # folio puede estar solo o dentro de un total de ganancias; en los
        # dos casos ya está registrado.
        conocidas = self._repo.referencias_externas(
            [r for c in candidatos for r in c.origen.referencias]
        )
        for candidato in candidatos:
            folios = candidato.origen.referencias
            vistos = [f for f in folios if f in conocidas]
            if not vistos:
                continue

            if candidato.origen.es_total and len(vistos) < len(folios):
                candidato.estado = POSIBLE
                candidato.incluir = False
                candidato.motivo = (
                    f"{len(vistos)} de las {len(folios)} ganancias ya están en "
                    "un total registrado; las demás son nuevas."
                )
                continue

            candidato.estado = DUPLICADO
            candidato.incluir = False
            if candidato.origen.es_total:
                candidato.motivo = (
                    f"Las {len(folios)} ganancias ya están en un total registrado."
                )
            else:
                candidato.motivo = f"Ya se importó el folio {folios[0]}."

        registrados = self._registrados_del_rango(lectura)
        if registrados.empty:
            return

        usados: set[int] = set()

        for candidato in candidatos:
            if candidato.estado != NUEVO:
                continue

            indice = self._buscar(candidato, registrados, usados, cuenta_id, True)
            if indice is not None:
                usados.add(indice)
                candidato.estado = DUPLICADO
                candidato.incluir = False
                candidato.motivo = _motivo_duplicado(
                    candidato, registrados.loc[indice], exacto=True
                )
                continue

            indice = self._buscar(candidato, registrados, usados, cuenta_id, False)
            if indice is not None:
                usados.add(indice)
                candidato.estado = POSIBLE
                candidato.incluir = False
                candidato.motivo = _motivo_duplicado(
                    candidato, registrados.loc[indice], exacto=False
                )
                continue

            # La misma cifra en la misma fecha pero en otra cuenta: casi
            # siempre es otro movimiento, pero a veces es éste registrado
            # con la cuenta equivocada. Se avisa sin desmarcar.
            indice = self._buscar(candidato, registrados, usados, None, True)
            if indice is not None and cuenta_id is not None:
                fila = registrados.loc[indice]
                candidato.motivo = (
                    f"Hay uno igual el mismo día pero en {fila['cuenta']}"
                    + (f" → {fila['cuenta_destino']}" if fila["cuenta_destino"] else "")
                    + "; si es éste, la cuenta de aquél está mal."
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

        # El rango filtra por `fecha`, que el usuario puede haber corrido
        # respecto a la del banco; se abre lo suficiente para que una
        # corrección razonable no lo saque del rango.
        margen = timedelta(days=MARGEN_RANGO_DIAS)
        return self._repo.listar(desde=desde - margen, hasta=hasta + margen)

    def _buscar(
        self,
        candidato: Candidato,
        registrados: pd.DataFrame,
        usados: set[int],
        cuenta_id: int | None,
        exacto: bool,
    ) -> int | None:
        """
        Busca en lo registrado una coincidencia todavía sin emparejar.

        Con `cuenta_id`, sólo cuentan las filas que tocan esa cuenta del
        lado correcto: un cargo del documento es un movimiento que salió
        de esa cuenta; un abono, uno que entró. Un traspaso registrado
        desde la otra cuenta cumple eso por su otra pata, y así el pago de
        la tarjeta visto desde los dos estados es un solo movimiento.

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
            if cuenta_id is not None and not _toca_la_cuenta(
                fila, cuenta_id, candidato.origen.es_cargo
            ):
                continue

            # Se compara contra lo que el banco dijo la vez anterior, no
            # contra la fecha corregida: es lo que va a repetir.
            registrada = fila.get("fecha_banco")
            if registrada is None or pd.isna(registrada):
                registrada = fila["fecha"]
            distancia = abs((registrada.date() - fecha).days)
            if exacto and distancia == 0:
                return int(indice)
            if not exacto and distancia <= TOLERANCIA_DIAS:
                return int(indice)

        return None

    # ── Saldos del documento ─────────────────────────────

    def anclar(self, resultado: ResultadoImportacion) -> int:
        """
        Registra como saldos verificados los que el documento declara.

        Un estado de cuenta es la mejor fuente de verdad que hay sobre el
        saldo: dice cuánto había al empezar y cuánto al terminar. Con eso
        el sistema deduce el resto y detecta lo que falta.

        Returns
        -------
        int
            Cuántos saldos quedaron registrados.
        """
        if resultado.cuenta_id is None:
            return 0

        from finanzas.application.services.patrimonio_service import (
            PatrimonioService,
        )
        from finanzas.data.repositories.patrimonio_repository import (
            PatrimonioRepository,
        )

        servicio = PatrimonioService(PatrimonioRepository(self._db_path))
        registrados = 0
        for fecha, saldo in resultado.anclas:
            servicio.verificar_saldo(
                resultado.cuenta_id,
                fecha,
                saldo,
                origen=OrigenSaldo.ESTADO_DE_CUENTA,
                nota=f"{resultado.lectura.banco}",
            )
            registrados += 1

        if registrados:
            logger.info(
                "Anclados %s saldos de %s", registrados, resultado.lectura.banco
            )

        return registrados

    def cuenta_sugerida(self, resultado: ResultadoImportacion) -> int | None:
        """
        Adivina de qué cuenta es el documento, por su banco y su clase.

        Un estado de tarjeta busca una cuenta de crédito de esa
        institución; uno de cuenta, una de débito. Si hay varias o
        ninguna, no adivina: el usuario elige.
        """
        from finanzas.data.repositories.catalogos_repository import (
            CatalogosRepository,
        )

        cuentas = CatalogosRepository(self._db_path).listar_cuentas()
        if cuentas.empty:
            return None

        banco = resultado.lectura.banco.lower()
        es_tarjeta = "tarjeta" in banco
        institucion = banco.split("(")[0].strip().replace(" ", "")

        # Un estado de tarjeta es de una cuenta de crédito; uno de cuenta,
        # de la de débito (no del apartado, que no emite estados).
        def encaja(tipo: str) -> bool:
            clase = TipoCuenta(tipo)
            return clase.es_pasivo if es_tarjeta else clase == TipoCuenta.DEBITO

        misma_institucion = (
            cuentas["institucion"]
            .str.lower()
            .str.replace(" ", "")
            .str.contains(institucion, regex=False)
        )
        candidatas = cuentas[misma_institucion & cuentas["tipo"].map(encaja)]
        if len(candidatas) == 1:
            return int(candidatas.iloc[0]["id"])

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
            fecha=candidato.fecha_final,
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
            empresa=candidato.empresa,
            lugar=candidato.lugar,
            hora=time.fromisoformat(candidato.hora) if candidato.hora else None,
            # La del banco, aparte: es la que va a repetir el siguiente
            # estado de cuenta, y con la que se reconoce al reimportar.
            fecha_banco=candidato.origen.fecha,
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
        """
        Resuelve cuándo salió el dinero, si es que ya salió.

        Sólo un gasto desde débito puede quedar sin pagar; en todo lo demás
        el servicio de movimientos pone la fecha aunque aquí llegue None.
        """
        if not (candidato.pagado or pagado):
            return None

        return (
            candidato.fecha_pago
            or candidato.origen.fecha_cargo
            or candidato.fecha_final
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
            "cuenta_id": resultado.cuenta_id,
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
                            "es_retiro_efectivo",
                            "es_rendimiento",
                            "agrupa",
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
                    "fecha": _a_json(c.fecha),
                    "fecha_pago": _a_json(c.fecha_pago),
                    "empresa": c.empresa,
                    "lugar": c.lugar,
                    "hora": c.hora,
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
                fecha=_fecha_de(crudo_candidato.get("fecha")),
                fecha_pago=_fecha_de(crudo_candidato.get("fecha_pago")),
                empresa=crudo_candidato.get("empresa", ""),
                lugar=crudo_candidato.get("lugar", ""),
                hora=crudo_candidato.get("hora", ""),
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

    return ResultadoImportacion(
        lectura=lectura, candidatos=candidatos, cuenta_id=datos.get("cuenta_id")
    )


def _toca_la_cuenta(fila: pd.Series, cuenta_id: int, es_cargo: bool) -> bool:
    """
    Indica si el movimiento registrado sale de (o entra a) esa cuenta.

    Un cargo del documento sale de la cuenta: coincide con un gasto, un
    ahorro o un traspaso cuyo origen es ella. Un abono entra: coincide con
    un ingreso a ella o con un traspaso cuyo destino es ella.
    """
    origen = int(fila["cuenta_id"])
    destino = fila["cuenta_destino_id"]
    destino = None if destino is None or pd.isna(destino) else int(destino)
    es_ingreso = fila["tipo"] == str(TipoMovimiento.INGRESO)

    if es_cargo:
        return origen == cuenta_id and not es_ingreso
    if es_ingreso:
        return origen == cuenta_id
    return destino == cuenta_id


def _motivo_duplicado(candidato: Candidato, fila: pd.Series, exacto: bool) -> str:
    """
    Explica por qué un candidato parece ya registrado.

    El caso que más confunde es el pago de la tarjeta: aparece en el
    estado de la tarjeta como abono y en el de la cuenta como cargo, y
    es un solo traspaso. Cuando lo registrado es un traspaso, se dice
    así, para que no se importe la otra pata como si fuera otro dinero.
    """
    if fila["tipo"] in (
        str(TipoMovimiento.TRANSFERENCIA),
        str(TipoMovimiento.AHORRO),
        str(TipoMovimiento.INVERSION),
    ):
        destino = fila.get("cuenta_destino") or "otra cuenta"
        return (
            f"Ya está como traspaso {int(fila['id'])}: sale de "
            f"{fila['cuenta']} y entra a {destino} el {fila['fecha']:%d/%m/%Y}. "
            "Ésta es su otra pata."
        )

    if exacto:
        return (
            f"Ya hay un movimiento del {candidato.origen.fecha:%d/%m/%Y} "
            f"por {candidato.origen.monto:,.2f}."
        )

    return (
        f"Hay uno por el mismo monto el {fila['fecha']:%d/%m/%Y}, a "
        f"{abs((fila['fecha'].date() - candidato.origen.fecha).days)} "
        f"días de diferencia."
    )


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
