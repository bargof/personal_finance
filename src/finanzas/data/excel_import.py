from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

from finanzas.data.database import connect
from finanzas.data.seed import preparar_base

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# Importación desde el Excel original
#
# Cada hoja del archivo tiene su encabezado en una fila
# distinta y trae columnas con fórmulas que aquí sobran: lo
# derivado se recalcula, sólo se importa lo capturado.
# ═══════════════════════════════════════════════════════════

#: Fila (base 0) en la que empieza el encabezado de cada hoja.
ENCABEZADOS = {
    "Movimientos": 4,
    "Patrimonio": 6,
    "Suscripciones": 4,
    "Metas": 4,
    "Cierres mensuales": 4,
    "Presupuesto": 4,
}

#: Celda (fila, columna) del periodo seleccionado en la hoja «Configuración».
CELDA_PERIODO = (11, 1)

#: Categorías destino cuando el Excel usó «Otros» para un tipo no-gasto.
CATEGORIA_POR_TIPO = {
    "Ingreso": "Otros ingresos",
    "Ahorro": "Ahorro programado",
    "Inversión": "Aportación a inversión",
    "Transferencia": "Traspaso entre cuentas",
}


@dataclass(slots=True)
class ResultadoImportacion:
    """Qué se importó y qué se tuvo que omitir."""

    movimientos: int = 0
    patrimonio: int = 0
    suscripciones: int = 0
    metas: int = 0
    cierres: int = 0
    presupuesto: int = 0
    catalogos_creados: int = 0
    omitidos: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        """Registros importados en todas las hojas."""
        return (
            self.movimientos
            + self.patrimonio
            + self.suscripciones
            + self.metas
            + self.cierres
            + self.presupuesto
        )

    def resumen(self) -> str:
        """Una línea legible con lo que ocurrió."""
        partes = [
            f"{self.movimientos} movimientos",
            f"{self.patrimonio} posiciones",
            f"{self.suscripciones} suscripciones",
            f"{self.metas} metas",
            f"{self.cierres} cierres",
            f"{self.presupuesto} líneas de presupuesto",
        ]
        texto = ", ".join(partes)
        if self.omitidos:
            texto += f" · {len(self.omitidos)} filas omitidas"

        return texto


def importar_excel(
    ruta: Path | str,
    db_path: str | None = None,
    incluir_ejemplos: bool = True,
) -> ResultadoImportacion:
    """
    Carga el Excel del sistema financiero personal en SQLite.

    Parameters
    ----------
    ruta : Path or str
        Ruta al archivo .xlsx.
    db_path : str, optional
        Base de datos destino. Por defecto, la de la configuración.
    incluir_ejemplos : bool
        Si es False, omite las filas marcadas como «Ejemplo» en el Excel.

    Returns
    -------
    ResultadoImportacion
        Conteo por hoja y lista de filas que no se pudieron importar.

    Raises
    ------
    FileNotFoundError
        Si el archivo no existe.
    """
    ruta = Path(ruta)
    if not ruta.exists():
        raise FileNotFoundError(f"No se encontró el Excel en {ruta}")

    preparar_base(db_path)
    resultado = ResultadoImportacion()
    hojas = pd.read_excel(ruta, sheet_name=None, header=None, engine="openpyxl")

    _importar_movimientos(hojas, db_path, incluir_ejemplos, resultado)
    _importar_patrimonio(hojas, db_path, incluir_ejemplos, resultado)
    _importar_suscripciones(hojas, db_path, incluir_ejemplos, resultado)
    _importar_metas(hojas, db_path, incluir_ejemplos, resultado)
    _importar_cierres(hojas, db_path, incluir_ejemplos, resultado)
    _importar_presupuesto(hojas, db_path, resultado)
    _importar_configuracion(hojas, db_path)

    logger.info("Importación terminada: %s", resultado.resumen())
    return resultado


# ═══════════════════════════════════════════════════════════
# Hoja por hoja
# ═══════════════════════════════════════════════════════════


def _importar_movimientos(
    hojas: dict[str, pd.DataFrame],
    db_path: str | None,
    incluir_ejemplos: bool,
    resultado: ResultadoImportacion,
) -> None:
    """Importa la hoja «Movimientos»."""
    df = _hoja(hojas, "Movimientos", incluir_ejemplos)
    if df is None:
        return

    df = df[df["Fecha"].notna() & df["Monto"].notna()]
    if df.empty:
        return

    with connect(db_path) as conexion:
        catalogo = _CatalogoVivo(conexion)

        registros = []
        for indice, fila in df.iterrows():
            tipo = _texto(fila.get("Tipo")) or "Gasto"
            fecha = _fecha(fila.get("Fecha"))
            monto = _numero(fila.get("Monto"))

            if fecha is None or monto <= 0:
                resultado.omitidos.append(
                    f"Movimientos fila {indice + 2}: fecha o monto inválidos"
                )
                continue

            categoria = _categoria_destino(_texto(fila.get("Categoría")), tipo)
            categoria_id = catalogo.categoria(categoria, tipo)
            subcategoria = _texto(fila.get("Subcategoría"))

            registros.append(
                (
                    fecha.isoformat(),
                    tipo,
                    monto,
                    categoria_id,
                    catalogo.subcategoria(categoria_id, subcategoria),
                    catalogo.cuenta(_texto(fila.get("Cuenta")) or "Otra"),
                    catalogo.medio_pago(_texto(fila.get("Medio de pago"))),
                    _texto(fila.get("Descripción")),
                    _texto(fila.get("Esencial / deseo")) or "Esencial",
                    _texto(fila.get("Fijo / variable")) or "Variable",
                    _bandera(fila.get("Recurrente")),
                    _bandera(fila.get("Planeado"), por_defecto=True),
                    _texto(fila.get("Proyecto / persona")),
                    _texto(fila.get("Etiquetas")),
                    _texto(fila.get("Comprobante / nota")),
                    _texto(fila.get("Estado")) or "Confirmado",
                )
            )

        resultado.catalogos_creados += catalogo.creados

        if registros:
            conexion.executemany(
                """
                INSERT INTO movimientos (
                    fecha, tipo, monto, categoria_id, subcategoria_id, cuenta_id,
                    medio_pago_id, descripcion, necesidad, naturaleza, recurrente,
                    planeado, proyecto, etiquetas, nota, estado
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                registros,
            )
            resultado.movimientos = len(registros)


def _importar_patrimonio(
    hojas: dict[str, pd.DataFrame],
    db_path: str | None,
    incluir_ejemplos: bool,
    resultado: ResultadoImportacion,
) -> None:
    """Importa la hoja «Patrimonio»."""
    df = _hoja(hojas, "Patrimonio", incluir_ejemplos)
    if df is None:
        return

    df = df[df["Cuenta / activo"].notna()]
    if df.empty:
        return

    registros = [
        (
            _texto(fila.get("Cuenta / activo")),
            _texto(fila.get("Tipo")) or "Activo",
            _texto(fila.get("Subtipo")),
            _texto(fila.get("Institución")),
            abs(_numero(fila.get("Saldo actual"))),
            _texto(fila.get("Liquidez")) or "No aplica",
            _numero(fila.get("Tasa anual")),
            _iso(_fecha(fila.get("Fecha de corte"))),
            _texto(fila.get("Moneda")) or "MXN",
            _texto(fila.get("Notas")),
        )
        for _, fila in df.iterrows()
    ]

    with connect(db_path) as conexion:
        conexion.executemany(
            """
            INSERT INTO patrimonio (
                nombre, tipo, subtipo, institucion, saldo, liquidez,
                tasa_anual, fecha_corte, moneda, notas
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            registros,
        )

    resultado.patrimonio = len(registros)


def _importar_suscripciones(
    hojas: dict[str, pd.DataFrame],
    db_path: str | None,
    incluir_ejemplos: bool,
    resultado: ResultadoImportacion,
) -> None:
    """Importa la hoja «Suscripciones»."""
    df = _hoja(hojas, "Suscripciones", incluir_ejemplos)
    if df is None:
        return

    df = df[df["Servicio"].notna()]
    if df.empty:
        return

    with connect(db_path) as conexion:
        catalogo = _CatalogoVivo(conexion)

        registros = []
        for _, fila in df.iterrows():
            categoria = _texto(fila.get("Categoría"))
            cuenta = _texto(fila.get("Cuenta"))
            estado = _texto(fila.get("Estado")) or "Activa"

            registros.append(
                (
                    _texto(fila.get("Servicio")),
                    catalogo.categoria(categoria, "Gasto") if categoria else None,
                    _numero(fila.get("Costo por cobro")),
                    _frecuencia(_texto(fila.get("Frecuencia"))),
                    _iso(_fecha(fila.get("Próximo cobro"))),
                    catalogo.cuenta(cuenta) if cuenta else None,
                    _bandera(fila.get("Renovación automática"), por_defecto=True),
                    _texto(fila.get("Esencial / deseo")) or "Deseo",
                    int(estado.lower().startswith("activa")),
                    _texto(fila.get("Notas")),
                )
            )

        resultado.catalogos_creados += catalogo.creados

        conexion.executemany(
            """
            INSERT INTO suscripciones (
                servicio, categoria_id, costo_por_cobro, frecuencia,
                proximo_cobro, cuenta_id, renovacion_automatica, necesidad,
                activa, notas
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            registros,
        )

    resultado.suscripciones = len(registros)


def _importar_metas(
    hojas: dict[str, pd.DataFrame],
    db_path: str | None,
    incluir_ejemplos: bool,
    resultado: ResultadoImportacion,
) -> None:
    """Importa la hoja «Metas»."""
    df = _hoja(hojas, "Metas", incluir_ejemplos)
    if df is None:
        return

    df = df[df["Objetivo"].notna()]
    if df.empty:
        return

    registros = []
    for indice, fila in df.iterrows():
        monto = _numero(fila.get("Meta"))
        if monto <= 0:
            resultado.omitidos.append(f"Metas fila {indice + 2}: monto inválido")
            continue

        registros.append(
            (
                _texto(fila.get("Objetivo")),
                _texto(fila.get("Tipo")) or "Seguridad",
                monto,
                max(0.0, _numero(fila.get("Acumulado"))),
                _numero(fila.get("Aporte mensual planeado")),
                _iso(_fecha(fila.get("Fecha límite"))),
                _texto(fila.get("Prioridad")) or "Media",
                _texto(fila.get("Vehículo / cuenta")),
                _texto(fila.get("Notas")),
            )
        )

    if not registros:
        return

    with connect(db_path) as conexion:
        conexion.executemany(
            """
            INSERT INTO metas (
                objetivo, tipo, monto_meta, acumulado, aporte_mensual_planeado,
                fecha_limite, prioridad, vehiculo, notas
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            registros,
        )

    resultado.metas = len(registros)


def _importar_cierres(
    hojas: dict[str, pd.DataFrame],
    db_path: str | None,
    incluir_ejemplos: bool,
    resultado: ResultadoImportacion,
) -> None:
    """Importa la hoja «Cierres mensuales»."""
    df = _hoja(hojas, "Cierres mensuales", incluir_ejemplos)
    if df is None:
        return

    df = df[df["Mes"].notna()]
    if df.empty:
        return

    registros = []
    for _, fila in df.iterrows():
        mes = _fecha(fila.get("Mes"))
        if mes is None:
            continue

        registros.append(
            (
                mes.strftime("%Y-%m"),
                _numero(fila.get("Efectivo / bancos")),
                _numero(fila.get("Ahorro")),
                _numero(fila.get("Inversiones")),
                _numero(fila.get("Otros activos")),
                _numero(fila.get("Deudas")),
                _texto(fila.get("Notas")),
            )
        )

    if not registros:
        return

    with connect(db_path) as conexion:
        conexion.executemany(
            """
            INSERT INTO cierres_mensuales (
                periodo, efectivo, ahorro, inversiones, otros_activos,
                deudas, notas
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(periodo) DO UPDATE SET
                efectivo      = excluded.efectivo,
                ahorro        = excluded.ahorro,
                inversiones   = excluded.inversiones,
                otros_activos = excluded.otros_activos,
                deudas        = excluded.deudas,
                notas         = excluded.notas
            """,
            registros,
        )

    resultado.cierres = len(registros)


def _importar_presupuesto(
    hojas: dict[str, pd.DataFrame],
    db_path: str | None,
    resultado: ResultadoImportacion,
) -> None:
    """
    Importa la hoja «Presupuesto» al periodo que el Excel tenía seleccionado.

    Sólo se traen las dos decisiones del usuario: el monto manual y el % de
    recorte. El promedio de 3 meses y el gasto del mes se recalculan desde
    los movimientos, así que importarlos sería duplicar la verdad.
    """
    df = _hoja(hojas, "Presupuesto", incluir_ejemplos=True)
    if df is None:
        return

    periodo = _periodo_seleccionado(hojas)
    if periodo is None:
        logger.warning(
            "No se pudo leer el periodo seleccionado; se omite el presupuesto"
        )
        return

    df = df[df["Categoría"].notna()]
    if df.empty:
        return

    with connect(db_path) as conexion:
        registros = []
        for _, fila in df.iterrows():
            categoria = _texto(fila.get("Categoría"))
            manual = _numero(fila.get("Presupuesto manual"))
            recorte = _numero(fila.get("% recorte meta"))

            if not categoria or (manual == 0 and recorte == 0):
                continue

            existente = conexion.execute(
                "SELECT id FROM categorias WHERE nombre = ?", (categoria,)
            ).fetchone()
            if existente is None:
                resultado.omitidos.append(
                    f"Presupuesto: la categoría «{categoria}» no existe"
                )
                continue

            registros.append(
                (
                    periodo,
                    int(existente["id"]),
                    max(0.0, manual),
                    min(1.0, max(0.0, recorte)),
                )
            )

        if registros:
            conexion.executemany(
                """
                INSERT INTO presupuestos
                       (periodo, categoria_id, monto_manual, pct_recorte)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(periodo, categoria_id) DO UPDATE SET
                    monto_manual = excluded.monto_manual,
                    pct_recorte  = excluded.pct_recorte
                """,
                registros,
            )

    resultado.presupuesto = len(registros)


def _periodo_seleccionado(hojas: dict[str, pd.DataFrame]) -> str | None:
    """Lee el periodo activo de la hoja «Configuración»."""
    hoja = hojas.get("Configuración")
    if hoja is None:
        return None

    fila, columna = CELDA_PERIODO
    try:
        valor = hoja.iat[fila, columna]
    except IndexError:
        return None

    momento = _fecha(valor)
    return momento.strftime("%Y-%m") if momento else None


def _importar_configuracion(
    hojas: dict[str, pd.DataFrame], db_path: str | None
) -> None:
    """Trae los parámetros de la hoja «Configuración» (bloque A5:B13)."""
    hoja = hojas.get("Configuración")
    if hoja is None:
        return

    equivalencias = {
        "Moneda": "moneda",
        "Meta de ahorro + inversión": "meta_ahorro_inversion",
        "Meta de fondo de emergencia (meses)": "meses_fondo_emergencia",
        "Máximo deseable en gustos": "max_deseos",
        "Umbral de gasto pequeño": "umbral_gasto_pequeno",
        "Alerta de presupuesto desde": "alerta_presupuesto",
        "Día de inicio del ciclo": "dia_inicio_ciclo",
    }

    registros = []
    for _, fila in hoja.iterrows():
        etiqueta = _texto(fila.get(0))
        clave = equivalencias.get(etiqueta)
        if clave is None:
            continue

        valor = fila.get(1)
        if pd.isna(valor):
            continue

        registros.append((clave, str(valor)))

    if not registros:
        return

    with connect(db_path) as conexion:
        conexion.executemany(
            """
            INSERT INTO configuracion (clave, valor) VALUES (?, ?)
            ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor
            """,
            registros,
        )


# ═══════════════════════════════════════════════════════════
# Catálogo que crece durante la importación
# ═══════════════════════════════════════════════════════════


class _CatalogoVivo:
    """
    Resuelve nombres a ids y da de alta lo que falte.

    El Excel permite escribir cualquier texto en las columnas de catálogo;
    aquí cada valor nuevo se convierte en un registro en vez de perderse.
    """

    def __init__(self, conexion) -> None:
        self._conexion = conexion
        self.creados = 0

    def categoria(self, nombre: str, tipo: str) -> int:
        """Devuelve el id de una categoría, creándola si hace falta."""
        nombre = nombre or "Otros"
        fila = self._conexion.execute(
            "SELECT id FROM categorias WHERE nombre = ?", (nombre,)
        ).fetchone()

        if fila is not None:
            return int(fila["id"])

        cursor = self._conexion.execute(
            "INSERT INTO categorias (nombre, tipo, orden) VALUES (?, ?, 99)",
            (
                nombre,
                tipo if tipo in CATEGORIA_POR_TIPO or tipo == "Gasto" else "Gasto",
            ),
        )
        self.creados += 1

        return int(cursor.lastrowid)

    def subcategoria(self, categoria_id: int, nombre: str) -> int | None:
        """Devuelve el id de una subcategoría, creándola si hace falta."""
        if not nombre:
            return None

        fila = self._conexion.execute(
            "SELECT id FROM subcategorias WHERE categoria_id = ? AND nombre = ?",
            (categoria_id, nombre),
        ).fetchone()

        if fila is not None:
            return int(fila["id"])

        cursor = self._conexion.execute(
            "INSERT INTO subcategorias (categoria_id, nombre) VALUES (?, ?)",
            (categoria_id, nombre),
        )
        self.creados += 1

        return int(cursor.lastrowid)

    def cuenta(self, nombre: str) -> int:
        """Devuelve el id de una cuenta, creándola si hace falta."""
        nombre = nombre or "Otra"
        fila = self._conexion.execute(
            "SELECT id FROM cuentas WHERE nombre = ?", (nombre,)
        ).fetchone()

        if fila is not None:
            return int(fila["id"])

        cursor = self._conexion.execute(
            "INSERT INTO cuentas (nombre, tipo) VALUES (?, 'Otro')", (nombre,)
        )
        self.creados += 1

        return int(cursor.lastrowid)

    def medio_pago(self, nombre: str) -> int | None:
        """Devuelve el id de un medio de pago, creándolo si hace falta."""
        if not nombre:
            return None

        fila = self._conexion.execute(
            "SELECT id FROM medios_pago WHERE nombre = ?", (nombre,)
        ).fetchone()

        if fila is not None:
            return int(fila["id"])

        cursor = self._conexion.execute(
            "INSERT INTO medios_pago (nombre) VALUES (?)", (nombre,)
        )
        self.creados += 1

        return int(cursor.lastrowid)


# ═══════════════════════════════════════════════════════════
# Utilidades de lectura
# ═══════════════════════════════════════════════════════════


def _hoja(
    hojas: dict[str, pd.DataFrame], nombre: str, incluir_ejemplos: bool
) -> pd.DataFrame | None:
    """Recorta una hoja a partir de su fila de encabezado y filtra ejemplos."""
    cruda = hojas.get(nombre)
    if cruda is None:
        logger.warning("El Excel no tiene la hoja «%s»", nombre)
        return None

    fila_encabezado = ENCABEZADOS[nombre]
    if len(cruda) <= fila_encabezado:
        return None

    encabezado = cruda.iloc[fila_encabezado].tolist()
    df = cruda.iloc[fila_encabezado + 1 :].copy()
    df.columns = [str(columna).strip() for columna in encabezado]
    df = df.dropna(how="all")

    if not incluir_ejemplos and "Ejemplo" in df.columns:
        df = df[df["Ejemplo"].astype(str).str.strip().str.lower() != "sí"]

    return df


def _texto(valor: object) -> str:
    """Normaliza una celda a texto, tratando NaN como cadena vacía."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return ""
    if pd.isna(valor):
        return ""

    return str(valor).strip()


def _numero(valor: object) -> float:
    """Normaliza una celda a float, tratando NaN como cero."""
    if valor is None or pd.isna(valor):
        return 0.0
    try:
        return float(valor)
    except (TypeError, ValueError):
        return 0.0


def _fecha(valor: object) -> date | None:
    """Normaliza una celda a fecha, aceptando el serial de Excel."""
    if valor is None or pd.isna(valor):
        return None

    if isinstance(valor, (int, float)):
        # Serial de Excel: días desde 1899-12-30.
        return (pd.Timestamp("1899-12-30") + pd.Timedelta(days=int(valor))).date()

    convertido = pd.to_datetime(valor, errors="coerce")
    if pd.isna(convertido):
        return None

    return convertido.date()


def _iso(momento: date | None) -> str | None:
    """Convierte una fecha a ISO, dejando pasar los nulos."""
    return momento.isoformat() if momento else None


def _bandera(valor: object, por_defecto: bool = False) -> int:
    """Convierte el «Sí»/«No» del Excel a 0/1."""
    texto = _texto(valor).lower()
    if not texto:
        return int(por_defecto)

    return int(texto in {"sí", "si", "true", "1", "verdadero", "x"})


def _frecuencia(valor: str) -> str:
    """Normaliza la frecuencia de cobro al vocabulario de la aplicación."""
    validas = {"Mensual", "Bimestral", "Trimestral", "Semestral", "Anual"}
    if valor in validas:
        return valor

    # El Excel admitía «Semanal», que aquí se aproxima al cobro mensual.
    return "Mensual"


def _categoria_destino(categoria: str, tipo: str) -> str:
    """
    Elige la categoría correcta para un movimiento que no es gasto.

    En el Excel, ingresos, ahorro e inversión usaban la categoría «Otros»
    porque el catálogo era único. Aquí cada tipo tiene la suya.
    """
    if tipo in CATEGORIA_POR_TIPO and categoria in ("", "Otros"):
        return CATEGORIA_POR_TIPO[tipo]

    return categoria or "Otros"
