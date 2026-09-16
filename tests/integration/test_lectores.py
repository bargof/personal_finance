from __future__ import annotations

from datetime import date

import pytest

from finanzas.application.services.importacion_service import (
    DUPLICADO,
    NUEVO,
    POSIBLE,
    ImportacionService,
)
from finanzas.data.lectores import DocumentoNoReconocidoError, detectar, leer
from finanzas.data.repositories.movimientos_repository import MovimientosRepository
from finanzas.domain.enums import TipoMovimiento

# ═══════════════════════════════════════════════════════════
# Lectura de estados de cuenta
#
# Las muestras son líneas literales de documentos reales. El
# parser trabaja sobre líneas y no sobre el PDF, así que se
# puede fijar aquí el formato exacto de cada banco: cuando uno
# lo cambie, estas pruebas lo dicen antes que un número raro en
# el tablero.
# ═══════════════════════════════════════════════════════════

NU_CLASICO = """Nu México Financiera S.A. de C.V.
TRANSACCIONES DE 23 DIC 2025 A 22 ENE 2026 (31 DÍAS) MONTOS EN PESOS MEXICANOS
Periodo: 23 DIC 2025 - 22 ENE 2026 (31 días)
Saldo inicial del periodo (DIC 2025) $905.17
23 DIC Otros Plan de pagos fijos de julio - 5/6 $634.36
24 DIC Electrónicos Liverpool Perisur $489.00
24 DIC Transporte Dlo*Didi Rides $26.20
31 DIC ¡Muchas gracias! Pago a tu tarjeta de crédito - $905.17
04 ENE Restaurante Rest la Mano Cultural $275.00
15 ENE Servicio Star Cluster Pte. Ltd. $1,026.25
Cambio(USD 1.00 = $17.85) USD 57.48
16 ENE Supermercado Psm*la Tienda de C $24.00"""

NU_REGULADO = """Número de tarjeta: XXXX-XXXX-XXXX-1860
CARGOS, ABONOS Y COMPRAS REGULARES (NO A MESES)
Periodo: 23 MAY 2026 al 21 JUN 2026
22 MAY 2026 23 MAY 2026 Super Rappi | RFC: S.I. +$388.00
Tarjeta virtual **** 9876
27 MAY 2026 27 MAY 2026 ¡Grácias por tu pago! | RFC: S.I. -$1,908.62
Abono (con cuenta Nu)
06 JUN 2026 08 JUN 2026 Apple.Com/Bill | RFC: S.I. +$69.00
Cambio (MXN 1 = $1.00) MXN 69.00
Total de cargos +$457.00
Total de abonos -$1,908.62"""

MP_TARJETA = """Fecha: 22 agosto 2026
mercado pago
Período 22 julio - 21 agosto
Movimientos
22/07 Saldo al corte del periodo anterior (julio) $ 3,519.77
22/07 Compra en LIVERPOOL MITIKAH $ 24.00
23/07 Compra en DIDI $ 37.00
28/07 Pago del resumen del julio/2026 - $ 3,519.77
14/08 Compra en ANTHROPIC* CLAUDE SUB US$ 20.00 $ 341.00"""

MP_CUENTA = """INITIAL_BALANCE;CREDITS;DEBITS;FINAL_BALANCE
0.00;2,410.13;-6,151.50;0.01

RELEASE_DATE;TRANSACTION_TYPE;REFERENCE_ID;TRANSACTION_NET_AMOUNT;PARTIAL_BALANCE
01-06-2026;Monto retirado Ahorro;162018192302;200.00;200.00
01-06-2026;Transferencia enviada Fernando Barrios Gomez;162017205220;-200.00;0.00
03-06-2026;Ganancia ;1744720112053;0.13;906.13
04-06-2026;Transferencia recibida FERNANDO BARRIOS GOMEZ;162553887082;2,210.00;2,210.30
13-06-2026;Monto apartado Ahorro;163143637963;-5,951.50;0.00"""


# ═══════════════════════════════════════════════════════════
# Detección
# ═══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("documento", "esperado"),
    [
        (NU_CLASICO, "Nu (formato con categoría)"),
        (NU_REGULADO, "Nu (formato regulado)"),
        (MP_TARJETA, "Mercado Pago (tarjeta)"),
        (MP_CUENTA, "Mercado Pago (cuenta)"),
    ],
)
def test_cada_documento_va_a_su_lector(documento, esperado):
    """Los cuatro formatos se distinguen sin que haya que elegirlos a mano."""
    assert detectar(documento).nombre == esperado


def test_un_formato_desconocido_se_rechaza_con_un_mensaje_util():
    """Fallar en silencio sería peor que no leer: hay que decir qué sí se lee."""
    with pytest.raises(DocumentoNoReconocidoError, match="Por ahora leo"):
        detectar("Estado de cuenta de un banco que todavía no soporto")


# ═══════════════════════════════════════════════════════════
# Nu, formato con categoría
# ═══════════════════════════════════════════════════════════


def test_nu_clasico_lee_sus_movimientos():
    """Siete filas de importe, ni una más."""
    resultado = leer(NU_CLASICO.splitlines())

    assert len(resultado.movimientos) == 7


def test_nu_clasico_lee_el_saldo_sin_confundirlo_con_el_ano():
    """
    «Saldo inicial del periodo (DIC 2025) $905.17» tiene dos números.

    El importe es el último y lleva símbolo de moneda; quedarse con el
    primero leería 2025 como si fuera dinero.
    """
    assert leer(NU_CLASICO.splitlines()).saldo_inicial == 905.17


def test_nu_clasico_resuelve_el_ano_de_un_periodo_a_caballo():
    """
    Un periodo de diciembre a enero tiene filas de dos años.

    Las filas sólo dicen el mes, así que diciembre pertenece al año en que
    empezó el periodo y enero al siguiente.
    """
    movimientos = leer(NU_CLASICO.splitlines()).movimientos
    por_descripcion = {m.descripcion_banco: m for m in movimientos}

    assert por_descripcion["Liverpool Perisur"].fecha == date(2025, 12, 24)
    assert por_descripcion["Rest la Mano Cultural"].fecha == date(2026, 1, 4)


def test_nu_clasico_separa_la_categoria_del_comercio():
    """La categoría que Nu asignó se aprovecha en vez de descartarse."""
    movimientos = leer(NU_CLASICO.splitlines()).movimientos
    liverpool = next(m for m in movimientos if "Liverpool" in m.descripcion_banco)

    assert liverpool.categoria_banco == "Electrónicos"
    assert liverpool.descripcion_banco == "Liverpool Perisur"


def test_nu_clasico_ignora_las_lineas_de_continuacion():
    """
    «Cambio(USD 1.00 = $17.85)» detalla la fila anterior, no es un movimiento.

    Sin esta regla se colarían movimientos fantasma con el tipo de cambio
    como importe.
    """
    movimientos = leer(NU_CLASICO.splitlines()).movimientos

    assert not any("Cambio" in m.descripcion_banco for m in movimientos)


def test_nu_clasico_marca_el_pago_de_la_tarjeta():
    """El pago no es gasto: es un traspaso desde otra cuenta propia."""
    movimientos = leer(NU_CLASICO.splitlines()).movimientos
    pago = next(m for m in movimientos if m.es_pago_tarjeta)

    assert pago.monto == 905.17
    assert not pago.es_cargo


# ═══════════════════════════════════════════════════════════
# Nu, formato regulado
# ═══════════════════════════════════════════════════════════


def test_nu_regulado_guarda_las_dos_fechas():
    """
    El formato nuevo distingue cuándo se compró y cuándo se cargó.

    La de operación manda para el presupuesto; la de cargo dice cuándo lo
    aplicó el banco.
    """
    movimientos = leer(NU_REGULADO.splitlines()).movimientos
    apple = next(m for m in movimientos if "Apple" in m.descripcion_banco)

    assert apple.fecha == date(2026, 6, 6)
    assert apple.fecha_cargo == date(2026, 6, 8)


def test_nu_regulado_limpia_el_rfc_de_la_descripcion():
    """«Super Rappi | RFC: S.I.» no aporta nada después de la barra."""
    movimientos = leer(NU_REGULADO.splitlines()).movimientos

    assert movimientos[0].descripcion_banco == "Super Rappi"


def test_nu_regulado_distingue_cargo_de_abono_por_el_signo():
    """El formato nuevo marca el sentido con «+» o «−» explícito."""
    movimientos = leer(NU_REGULADO.splitlines()).movimientos
    por_monto = {m.monto: m for m in movimientos}

    assert por_monto[388.00].es_cargo
    assert not por_monto[1908.62].es_cargo


def test_nu_regulado_cuadra_contra_los_totales_del_documento():
    """
    El cuadre es la red de seguridad contra filas perdidas.

    Si lo leído no reconstruye lo que el documento declara, el parser se
    comió algo, y más vale avisar que importar cifras incompletas.
    """
    resultado = leer(NU_REGULADO.splitlines())

    assert resultado.cargos == 457.00
    assert resultado.abonos == 1_908.62
    assert resultado.cuadra is True


def test_el_cuadre_detecta_una_fila_perdida():
    """Quitar una fila del documento tiene que romper el cuadre."""
    sin_una = [
        linea for linea in NU_REGULADO.splitlines() if "Super Rappi" not in linea
    ]
    resultado = leer(sin_una)

    assert resultado.cuadra is False
    assert resultado.diferencia_de_cuadre == -388.00


# ═══════════════════════════════════════════════════════════
# Mercado Pago
# ═══════════════════════════════════════════════════════════


def test_mp_tarjeta_pone_ano_a_las_fechas_cortas():
    """Las filas dicen «22/07»; el año sale del encabezado del documento."""
    movimientos = leer(MP_TARJETA.splitlines()).movimientos

    assert movimientos[0].fecha == date(2026, 7, 22)


def test_mp_tarjeta_quita_el_prefijo_de_la_plantilla():
    """«Compra en OXXO» es plantilla; el comercio es lo que importa."""
    movimientos = leer(MP_TARJETA.splitlines()).movimientos

    assert movimientos[0].descripcion_banco == "LIVERPOOL MITIKAH"


def test_mp_tarjeta_no_toma_el_saldo_inicial_como_movimiento():
    """El saldo de arranque es contexto, y además cuadra el documento."""
    resultado = leer(MP_TARJETA.splitlines())

    assert resultado.saldo_inicial == 3_519.77
    assert not any(
        "Saldo al corte" in m.descripcion_banco for m in resultado.movimientos
    )


def test_mp_cuenta_conserva_el_folio():
    """
    Con folio, la deduplicación deja de ser heurística.

    Es el único de los cuatro formatos que lo trae, y por eso es el más
    confiable para importar sin riesgo de repetir.
    """
    movimientos = leer(MP_CUENTA.splitlines()).movimientos

    assert movimientos[0].referencia == "162018192302"


def test_mp_cuenta_marca_los_traspasos_entre_cuentas_propias():
    """
    Apartar en el ahorro o transferirse a uno mismo no es gasto.

    Contarlo como gasto inflaría el consumo del mes con dinero que nunca
    salió del patrimonio.
    """
    movimientos = leer(MP_CUENTA.splitlines()).movimientos
    ganancia = next(m for m in movimientos if "Ganancia" in m.descripcion_banco)

    assert not ganancia.es_pago_tarjeta
    assert sum(1 for m in movimientos if m.es_pago_tarjeta) == 4


def test_mp_cuenta_lee_los_saldos_de_la_cabecera():
    """El CSV declara totales arriba, con lo que el cuadre es exacto."""
    resultado = leer(MP_CUENTA.splitlines())

    assert resultado.saldo_inicial == 0.0
    assert resultado.total_abonos == 2_410.13
    assert resultado.total_cargos == 6_151.50
    assert resultado.cuadra is True


def test_mp_cuenta_usa_el_signo_del_importe():
    """En el CSV el sentido va en el número, no en una columna aparte."""
    movimientos = leer(MP_CUENTA.splitlines()).movimientos
    por_monto = {m.monto: m for m in movimientos}

    assert por_monto[5_951.50].es_cargo
    assert not por_monto[2_210.00].es_cargo


# ═══════════════════════════════════════════════════════════
# Deduplicación contra lo ya registrado
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def importacion(movimientos: MovimientosRepository, db_path: str) -> ImportacionService:
    """Servicio de importación sobre la base de prueba."""
    return ImportacionService(movimientos, db_path=db_path)


@pytest.fixture
def servicio(movimientos: MovimientosRepository):
    """Servicio de movimientos, para sembrar lo ya registrado."""
    from finanzas.application.services.movimientos_service import MovimientosService

    return MovimientosService(movimientos)


def test_sin_nada_registrado_todo_es_nuevo(importacion):
    """Una base vacía no puede tener duplicados."""
    resultado = importacion.leer_documento(MP_TARJETA.encode())

    assert all(c.estado == NUEVO for c in resultado.candidatos)
    assert all(c.incluir for c in resultado.candidatos)


def test_un_movimiento_ya_capturado_se_marca_y_se_desmarca(
    importacion, servicio, ids_catalogo
):
    """Lo que ya está no se vuelve a importar, pero se dice por qué."""
    servicio.registrar(
        fecha=date(2026, 7, 23),
        tipo=TipoMovimiento.GASTO,
        monto=37.00,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
    )

    resultado = importacion.leer_documento(MP_TARJETA.encode())
    repetido = next(c for c in resultado.candidatos if c.origen.monto == 37.00)

    assert repetido.estado == DUPLICADO
    assert not repetido.incluir
    assert "23/07/2026" in repetido.motivo


def test_una_fecha_corrida_se_marca_como_posible_duplicado(
    importacion, servicio, ids_catalogo
):
    """
    La fecha del banco es la de aplicación y la capturada la de compra.

    Exigir coincidencia exacta dejaría pasar duplicados reales, así que se
    avisa del parecido y decide el usuario.
    """
    servicio.registrar(
        fecha=date(2026, 8, 12),
        tipo=TipoMovimiento.GASTO,
        monto=341.00,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
    )

    resultado = importacion.leer_documento(MP_TARJETA.encode())
    parecido = next(c for c in resultado.candidatos if c.origen.monto == 341.00)

    assert parecido.estado == POSIBLE
    assert "2 días" in parecido.motivo


def test_dos_movimientos_iguales_no_se_colapsan(importacion, servicio, ids_catalogo):
    """
    Dos cafés del mismo monto el mismo día son dos gastos, no uno.

    Si la base tiene uno y el documento trae dos, el segundo es nuevo: la
    deduplicación cuenta ocurrencias en vez de agrupar por clave.
    """
    servicio.registrar(
        fecha=date(2026, 7, 23),
        tipo=TipoMovimiento.GASTO,
        monto=37.00,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
    )

    documento = MP_TARJETA.replace(
        "23/07 Compra en DIDI $ 37.00",
        "23/07 Compra en DIDI $ 37.00\n23/07 Compra en DIDI $ 37.00",
    )
    resultado = importacion.leer_documento(documento.encode())
    didis = [c for c in resultado.candidatos if c.origen.monto == 37.00]

    assert len(didis) == 2
    assert {c.estado for c in didis} == {DUPLICADO, NUEVO}


# ═══════════════════════════════════════════════════════════
# Guardado
# ═══════════════════════════════════════════════════════════


def test_el_pago_de_la_tarjeta_se_guarda_como_traspaso(
    importacion, servicio, ids_catalogo
):
    """
    Registrarlo como gasto contaría dos veces el consumo.

    El pago mueve dinero de una cuenta propia a otra; el gasto ya se contó
    cuando se compró.
    """
    resultado = importacion.leer_documento(MP_TARJETA.encode())
    pago = next(c for c in resultado.candidatos if c.origen.es_pago_tarjeta)

    assert pago.tipo == str(TipoMovimiento.TRANSFERENCIA)


def test_guardar_conserva_el_concepto_del_banco(importacion, servicio, ids_catalogo):
    """
    La descripción propia sirve para rastrear y la del banco para auditar.

    Guardar sólo una pierde la otra, así que el original va en su propia
    columna, no dentro de la nota ni pisando la descripción.
    """
    resultado = importacion.leer_documento(MP_TARJETA.encode())
    candidato = next(
        c for c in resultado.candidatos if "LIVERPOOL" in c.origen.descripcion_banco
    )
    candidato.categoria_id = ids_catalogo["vivienda"]
    candidato.cuenta_id = ids_catalogo["cuenta"]
    candidato.descripcion = "Regalo de cumpleaños"

    importacion.guardar([candidato], ids_catalogo["cuenta"], pagado=False)
    guardado = servicio.buscar().iloc[0]

    assert guardado["descripcion"] == "Regalo de cumpleaños"
    assert guardado["descripcion_banco"] == "LIVERPOOL MITIKAH"


def test_lo_importado_de_una_tarjeta_nace_devengado(
    importacion, servicio, ids_catalogo
):
    """
    El consumo ocurrió, pero el dinero sale hasta el corte.

    Marcarlo pagado al importar movería la caja antes de tiempo.
    """
    resultado = importacion.leer_documento(MP_TARJETA.encode())
    candidato = resultado.candidatos[0]
    candidato.categoria_id = ids_catalogo["vivienda"]
    candidato.cuenta_id = ids_catalogo["cuenta"]

    importacion.guardar([candidato], ids_catalogo["cuenta"], pagado=False)

    assert servicio.total_por_pagar() == candidato.origen.monto


def test_un_candidato_sin_categoria_no_se_guarda(importacion, servicio, ids_catalogo):
    """Guardar a medias dejaría movimientos sin clasificar en la base."""
    resultado = importacion.leer_documento(MP_TARJETA.encode())
    candidato = resultado.candidatos[0]
    candidato.cuenta_id = ids_catalogo["cuenta"]

    guardados = importacion.guardar([candidato], ids_catalogo["cuenta"])

    assert guardados == 0
    assert servicio.buscar().empty


# ═══════════════════════════════════════════════════════════
# El concepto del banco, en su propia columna
#
# `descripcion` es cómo lo llama uno y se edita a gusto para
# rastrear; `descripcion_banco` es el original con el que se
# audita contra el documento. Guardarlos juntos —como hacía la
# primera versión, metiendo el concepto dentro de la nota—
# perdía uno de los dos en cuanto se editaba el otro.
# ═══════════════════════════════════════════════════════════


def _importar_uno(importacion, servicio, ids_catalogo, documento=MP_CUENTA):
    """Importa el primer candidato de un documento y devuelve su id."""
    resultado = importacion.leer_documento(documento.encode())
    candidato = resultado.candidatos[0]
    candidato.categoria_id = ids_catalogo["vivienda"]
    candidato.cuenta_id = ids_catalogo["cuenta"]
    importacion.guardar([candidato], ids_catalogo["cuenta"], pagado=True)

    return int(servicio.buscar().iloc[0]["id"]), candidato


def test_el_concepto_del_banco_va_en_su_columna(importacion, servicio, ids_catalogo):
    """No dentro de la nota, que es del usuario."""
    _, candidato = _importar_uno(importacion, servicio, ids_catalogo)
    guardado = servicio.buscar().iloc[0]

    assert guardado["descripcion_banco"] == candidato.origen.descripcion_banco
    assert guardado["referencia_externa"] == candidato.origen.referencia


def test_la_nota_queda_para_el_usuario(importacion, servicio, ids_catalogo):
    """Importar no debe dejar texto del banco dentro de la nota."""
    resultado = importacion.leer_documento(MP_CUENTA.encode())
    candidato = resultado.candidatos[0]
    candidato.categoria_id = ids_catalogo["vivienda"]
    candidato.cuenta_id = ids_catalogo["cuenta"]
    candidato.nota = "revisar con Ana"

    importacion.guardar([candidato], ids_catalogo["cuenta"])
    guardado = servicio.buscar().iloc[0]

    assert guardado["nota"] == "revisar con Ana"


def test_editar_la_descripcion_no_borra_la_del_banco(
    importacion, servicio, ids_catalogo
):
    """
    El caso que motivó separarlos.

    Cambiar la descripción propia es la operación normal —para eso está—
    y no puede costar el original con el que se audita.
    """
    movimiento_id, candidato = _importar_uno(importacion, servicio, ids_catalogo)

    servicio.actualizar(movimiento_id, descripcion="Audífonos de la oficina")
    guardado = servicio.buscar().set_index("id").loc[movimiento_id]

    assert guardado["descripcion"] == "Audífonos de la oficina"
    assert guardado["descripcion_banco"] == candidato.origen.descripcion_banco
    assert guardado["referencia_externa"] == candidato.origen.referencia


def test_duplicar_no_hereda_el_rastro_del_banco(importacion, servicio, ids_catalogo):
    """Una copia no salió de ningún estado de cuenta."""
    from datetime import date as fecha_tipo

    movimiento_id, _ = _importar_uno(importacion, servicio, ids_catalogo)

    copia_id = servicio.duplicar(movimiento_id, fecha_tipo(2026, 9, 5))
    copia = servicio.buscar().set_index("id").loc[copia_id]

    assert copia["descripcion_banco"] == ""
    assert copia["referencia_externa"] == ""


def test_se_puede_buscar_por_el_texto_del_banco(importacion, servicio, ids_catalogo):
    """Si lo que se recuerda es cómo lo escribió el banco, debe encontrarse."""
    _importar_uno(importacion, servicio, ids_catalogo)

    assert len(servicio.buscar(texto="Ahorro")) == 1


def test_el_folio_deduplica_sin_heuristica(importacion, servicio, ids_catalogo):
    """
    Donde hay folio no hace falta comparar fechas ni montos.

    Reimportar el mismo archivo no debe volver a proponer nada, aunque el
    banco reporte otra fecha de aplicación.
    """
    _importar_uno(importacion, servicio, ids_catalogo)

    segunda = importacion.leer_documento(MP_CUENTA.encode())
    repetido = segunda.candidatos[0]

    assert repetido.estado == DUPLICADO
    assert "folio" in repetido.motivo
    assert not repetido.incluir


def test_sin_folio_la_deduplicacion_sigue_siendo_por_fecha_y_monto(
    importacion, servicio, ids_catalogo
):
    """Los PDF no traen folio, y ahí el criterio anterior sigue aplicando."""
    resultado = importacion.leer_documento(MP_TARJETA.encode())
    candidato = next(c for c in resultado.candidatos if c.origen.monto == 24.00)
    candidato.categoria_id = ids_catalogo["vivienda"]
    candidato.cuenta_id = ids_catalogo["cuenta"]
    importacion.guardar([candidato], ids_catalogo["cuenta"])

    segunda = importacion.leer_documento(MP_TARJETA.encode())
    repetido = next(c for c in segunda.candidatos if c.origen.monto == 24.00)

    assert repetido.estado == DUPLICADO
    assert "folio" not in repetido.motivo


def test_un_movimiento_capturado_a_mano_no_trae_rastro_de_banco(servicio, ids_catalogo):
    """Los campos son opcionales: la captura manual los deja vacíos."""
    from datetime import date as fecha_tipo

    servicio.registrar(
        fecha=fecha_tipo(2026, 8, 5),
        tipo=TipoMovimiento.GASTO,
        monto=100.0,
        categoria_id=ids_catalogo["vivienda"],
        cuenta_id=ids_catalogo["cuenta"],
    )
    guardado = servicio.buscar().iloc[0]

    assert guardado["descripcion_banco"] == ""
    assert guardado["referencia_externa"] == ""


def test_la_migracion_rescata_el_concepto_que_quedo_en_la_nota(tmp_path):
    """
    La primera versión del importador lo metía entre corchetes en la nota.

    Al añadir la columna hay que recuperarlo, o lo ya importado perdería
    el rastro con el que se audita.
    """
    import sqlite3

    from finanzas.data.database import connect
    from finanzas.data.seed import preparar_base

    ruta = tmp_path / "vieja.db"
    conexion = sqlite3.connect(ruta)
    conexion.executescript(
        """
        CREATE TABLE movimientos (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha        TEXT NOT NULL,
            tipo         TEXT NOT NULL,
            monto        REAL NOT NULL,
            categoria_id INTEGER NOT NULL,
            cuenta_id    INTEGER NOT NULL,
            estado       TEXT NOT NULL DEFAULT 'Confirmado',
            nota         TEXT NOT NULL DEFAULT ''
        );
        INSERT INTO movimientos (fecha, tipo, monto, categoria_id, cuenta_id, nota)
        VALUES ('2026-08-14', 'Gasto', 341.0, 1, 1,
                'suscripción mensual [ANTHROPIC* CLAUDE SUB]'),
               ('2026-07-05', 'Gasto', 50.0, 1, 1, 'una nota sin corchetes');
        """
    )
    conexion.commit()
    conexion.close()

    preparar_base(str(ruta))

    with connect(str(ruta)) as conexion:
        importado, normal = conexion.execute(
            "SELECT nota, descripcion_banco FROM movimientos ORDER BY id"
        ).fetchall()

    assert importado["descripcion_banco"] == "ANTHROPIC* CLAUDE SUB"
    assert importado["nota"] == "suscripción mensual"
    # Una nota que nunca tuvo corchetes se queda como está.
    assert normal["descripcion_banco"] == ""
    assert normal["nota"] == "una nota sin corchetes"


# ═══════════════════════════════════════════════════════════
# Desglose y avance guardado
#
# El asistente escribe conforme se avanza en vez de al final,
# y el avance sobrevive a recargar: lo capturado no puede
# depender de llegar al último movimiento ni de que nadie
# toque F5.
# ═══════════════════════════════════════════════════════════


def test_un_movimiento_puede_llevar_su_lista_de_productos(
    importacion, servicio, ids_catalogo, db_path
):
    """
    El detalle no parte el movimiento: sigue siendo uno, con su categoría.

    Es la diferencia con repartir entre categorías, que crearía varios
    gastos donde sólo hubo una compra.
    """
    from finanzas.application.services.importacion_service import Producto
    from finanzas.data.repositories.productos_repository import ProductosRepository

    resultado = importacion.leer_documento(MP_TARJETA.encode())
    candidato = next(c for c in resultado.candidatos if c.origen.monto == 341.00)
    candidato.categoria_id = ids_catalogo["vivienda"]
    candidato.cuenta_id = ids_catalogo["cuenta"]
    candidato.productos = [
        Producto(producto="Café", cantidad=2, precio_unitario=100.0),
        Producto(producto="Pan", cantidad=1, precio_unitario=141.0),
    ]

    importacion.guardar_uno(candidato, ids_catalogo["cuenta"])
    guardados = servicio.buscar()

    assert len(guardados) == 1
    assert guardados.iloc[0]["monto"] == 341.00

    productos = ProductosRepository(db_path).de_movimiento(int(guardados.iloc[0]["id"]))
    assert list(productos["producto"]) == ["Café", "Pan"]
    assert list(productos["importe"]) == [200.0, 141.0]


def test_el_detalle_puede_quedarse_a_medias(importacion, ids_catalogo):
    """
    Apuntar tres artículos de un ticket de veinte es legítimo.

    Exigir que cuadre convertiría una ayuda en una tarea, así que sólo se
    informa cuánto queda sin apuntar.
    """
    from finanzas.application.services.importacion_service import Producto

    resultado = importacion.leer_documento(MP_TARJETA.encode())
    candidato = next(c for c in resultado.candidatos if c.origen.monto == 341.00)
    candidato.categoria_id = ids_catalogo["vivienda"]
    candidato.cuenta_id = ids_catalogo["cuenta"]
    candidato.productos = [Producto(producto="Café", precio_unitario=100.0)]

    assert candidato.sin_desglosar == 241.0
    assert not candidato.desglose_excedido
    assert candidato.completo


def test_un_detalle_que_excede_el_cobro_si_es_un_error(importacion, ids_catalogo):
    """Apuntar más de lo que se cobró inventaría artículos."""
    from finanzas.application.services.importacion_service import Producto

    resultado = importacion.leer_documento(MP_TARJETA.encode())
    candidato = next(c for c in resultado.candidatos if c.origen.monto == 341.00)
    candidato.categoria_id = ids_catalogo["vivienda"]
    candidato.cuenta_id = ids_catalogo["cuenta"]
    candidato.productos = [Producto(producto="Caro", precio_unitario=500.0)]

    assert candidato.desglose_excedido
    assert not candidato.completo
    assert importacion.guardar_uno(candidato, ids_catalogo["cuenta"]) == 0


def test_borrar_el_movimiento_se_lleva_sus_productos(
    importacion, servicio, ids_catalogo, db_path
):
    """Son parte de él, no entidades propias."""
    from finanzas.application.services.importacion_service import Producto
    from finanzas.data.repositories.productos_repository import ProductosRepository

    resultado = importacion.leer_documento(MP_TARJETA.encode())
    candidato = resultado.candidatos[0]
    candidato.categoria_id = ids_catalogo["vivienda"]
    candidato.cuenta_id = ids_catalogo["cuenta"]
    candidato.productos = [Producto(producto="Algo", precio_unitario=10.0)]
    importacion.guardar_uno(candidato, ids_catalogo["cuenta"])

    movimiento_id = int(servicio.buscar().iloc[0]["id"])
    servicio.eliminar(movimiento_id)

    assert ProductosRepository(db_path).de_movimiento(movimiento_id).empty


def test_la_importacion_llena_todos_los_campos_del_movimiento(
    importacion, servicio, ids_catalogo, catalogos
):
    """
    Un movimiento importado no debe quedar más pobre que uno capturado.

    Todo lo que el formulario manual permite poner, el asistente también.
    """
    resultado = importacion.leer_documento(MP_TARJETA.encode())
    candidato = resultado.candidatos[0]
    candidato.categoria_id = ids_catalogo["vivienda"]
    candidato.cuenta_id = ids_catalogo["cuenta"]
    candidato.medio_pago_id = catalogos.mapa_nombre_id("medios_pago")["Crédito"]
    candidato.descripcion = "Lo que yo diría"
    candidato.necesidad = "Deseo"
    candidato.naturaleza = "Fijo"
    candidato.etiquetas = "oficina, setup"
    candidato.recurrente = True
    candidato.planeado = False
    candidato.proyecto = "Home office"
    candidato.nota = "pedido 123"
    candidato.estado_movimiento = "Pendiente"

    importacion.guardar_uno(candidato, ids_catalogo["cuenta"])
    guardado = servicio.buscar().iloc[0]

    assert guardado["descripcion"] == "Lo que yo diría"
    assert guardado["medio_pago"] == "Crédito"
    assert guardado["necesidad"] == "Deseo"
    assert guardado["naturaleza"] == "Fijo"
    assert guardado["etiquetas"] == "oficina, setup"
    assert bool(guardado["recurrente"])
    assert not bool(guardado["planeado"])
    assert guardado["proyecto"] == "Home office"
    assert guardado["nota"] == "pedido 123"
    assert guardado["estado"] == "Pendiente"


def test_corregir_un_candidato_ya_guardado_no_duplica(
    importacion, servicio, ids_catalogo
):
    """
    Volver atrás a corregir es una operación normal, no un accidente.

    Como el asistente guarda al avanzar, retroceder y volver a pasar
    tiene que reemplazar en vez de acumular copias.
    """
    resultado = importacion.leer_documento(MP_TARJETA.encode())
    candidato = resultado.candidatos[0]
    candidato.categoria_id = ids_catalogo["vivienda"]
    candidato.cuenta_id = ids_catalogo["cuenta"]

    importacion.guardar_uno(candidato, ids_catalogo["cuenta"])
    candidato.descripcion = "Corregido"
    importacion.guardar_uno(candidato, ids_catalogo["cuenta"])

    guardados = servicio.buscar()
    assert len(guardados) == 1
    assert guardados.iloc[0]["descripcion"] == "Corregido"


def test_descartar_saca_de_la_base_lo_ya_guardado(importacion, servicio, ids_catalogo):
    """Saltar significa lo mismo antes y después de haber avanzado."""
    resultado = importacion.leer_documento(MP_TARJETA.encode())
    candidato = resultado.candidatos[0]
    candidato.categoria_id = ids_catalogo["vivienda"]
    candidato.cuenta_id = ids_catalogo["cuenta"]
    importacion.guardar_uno(candidato, ids_catalogo["cuenta"])

    importacion.descartar(candidato)

    assert servicio.buscar().empty
    assert not candidato.ya_guardado


def test_el_avance_sobrevive_a_recargar(importacion, ids_catalogo):
    """
    `session_state` se pierde al recargar el navegador; esto no.

    Lo capturado no puede depender de que nadie toque F5 a media
    importación.
    """
    from finanzas.application.services.importacion_service import Producto

    resultado = importacion.leer_documento(MP_TARJETA.encode())
    candidato = resultado.candidatos[0]
    candidato.categoria_id = ids_catalogo["vivienda"]
    candidato.cuenta_id = ids_catalogo["cuenta"]
    candidato.descripcion = "Lo que llevaba escrito"
    candidato.proyecto = "Mudanza"
    candidato.etiquetas = "una etiqueta"
    candidato.productos = [Producto(producto="Café", cantidad=2, precio_unitario=50.0)]

    importacion.guardar_avance(resultado, indice=2, paso="completar")
    recuperado = importacion.recuperar_avance()

    assert recuperado is not None
    devuelto, indice, paso = recuperado
    assert indice == 2
    assert paso == "completar"
    assert len(devuelto.candidatos) == len(resultado.candidatos)

    primero = devuelto.candidatos[0]
    assert primero.descripcion == "Lo que llevaba escrito"
    assert primero.proyecto == "Mudanza"
    assert primero.categoria_id == ids_catalogo["vivienda"]
    assert primero.etiquetas == "una etiqueta"
    assert len(primero.productos) == 1
    assert primero.productos[0].importe == 100.0


def test_sin_avance_guardado_no_hay_nada_que_reanudar(importacion):
    """Entrar a la página con la casa limpia no debe inventar una sesión."""
    assert importacion.recuperar_avance() is None


def test_olvidar_el_avance_lo_borra(importacion):
    """Al cerrar la importación, la siguiente empieza de cero."""
    resultado = importacion.leer_documento(MP_TARJETA.encode())
    importacion.guardar_avance(resultado, 0, "revisar")

    importacion.olvidar_avance()

    assert importacion.recuperar_avance() is None


def test_un_avance_ilegible_se_descarta_sin_romper(importacion, db_path):
    """
    Un avance de una versión anterior no debe impedir importar.

    Fallar al leerlo dejaría la página inservible hasta borrar la fila a
    mano, y lo que se pierde es sólo el progreso de una importación.
    """
    from finanzas.data.database import connect

    with connect(db_path) as conexion:
        conexion.execute(
            "INSERT INTO importacion_en_curso (id, banco, estado, indice, paso) "
            "VALUES (1, 'Nu', 'esto no es json', 0, 'completar')"
        )

    assert importacion.recuperar_avance() is None


# ═══════════════════════════════════════════════════════════
# Homologación con la captura manual
#
# Lo que el documento trae también se puede corregir: el banco
# sólo sabe si entró o salió dinero, y un abono en la tarjeta
# puede ser el pago del corte o una devolución, que no es lo
# mismo. El original queda en `origen` para el cuadre.
# ═══════════════════════════════════════════════════════════


def test_un_abono_que_no_es_pago_llega_como_ingreso(importacion):
    """Una devolución entra dinero, pero no es un traspaso propio."""
    documento = MP_TARJETA.replace(
        "14/08 Compra en ANTHROPIC* CLAUDE SUB US$ 20.00 $ 341.00",
        "14/08 Devolucion LIVERPOOL - $ 459.00",
    )
    resultado = importacion.leer_documento(documento.encode())
    devolucion = next(c for c in resultado.candidatos if c.origen.monto == 459.00)
    pago = next(c for c in resultado.candidatos if c.origen.es_pago_tarjeta)

    assert devolucion.tipo_sugerido == str(TipoMovimiento.INGRESO)
    assert pago.tipo_sugerido == str(TipoMovimiento.TRANSFERENCIA)


def test_el_usuario_puede_cambiar_el_tipo_sugerido(importacion, servicio, ids_catalogo):
    """Lo sugerido es un punto de partida, no una decisión."""
    resultado = importacion.leer_documento(MP_TARJETA.encode())
    pago = next(c for c in resultado.candidatos if c.origen.es_pago_tarjeta)
    pago.tipo_elegido = str(TipoMovimiento.GASTO)
    pago.categoria_id = ids_catalogo["vivienda"]
    pago.cuenta_id = ids_catalogo["cuenta"]

    importacion.guardar_uno(pago, ids_catalogo["cuenta"])

    assert servicio.buscar().iloc[0]["tipo"] == str(TipoMovimiento.GASTO)


def test_fecha_y_monto_siempre_son_los_del_banco(importacion, servicio, ids_catalogo):
    """
    A diferencia del tipo, ni la fecha ni el monto se corrigen.

    Son con lo que se reconoce el movimiento al reimportar, y corregidos
    dejarían de coincidir con lo que el banco va a repetir.
    """
    from datetime import date as fecha_tipo

    resultado = importacion.leer_documento(MP_TARJETA.encode())
    candidato = resultado.candidatos[0]
    candidato.categoria_id = ids_catalogo["vivienda"]
    candidato.cuenta_id = ids_catalogo["cuenta"]

    importacion.guardar_uno(candidato, ids_catalogo["cuenta"])

    guardado = servicio.buscar().iloc[0]
    assert guardado["fecha"].date() == fecha_tipo(2026, 7, 22)
    assert guardado["monto"] == 24.00
    assert not hasattr(candidato, "fecha")
    assert not hasattr(candidato, "monto")


def test_sin_corregir_nada_se_guarda_lo_que_dijo_el_banco(
    importacion, servicio, ids_catalogo
):
    """Los campos editables arrancan en el valor del documento."""
    resultado = importacion.leer_documento(MP_TARJETA.encode())
    candidato = resultado.candidatos[0]
    candidato.categoria_id = ids_catalogo["vivienda"]
    candidato.cuenta_id = ids_catalogo["cuenta"]

    assert candidato.tipo == candidato.tipo_sugerido

    importacion.guardar_uno(candidato, ids_catalogo["cuenta"])
    guardado = servicio.buscar().iloc[0]

    assert guardado["monto"] == candidato.origen.monto
    assert guardado["fecha"].date() == candidato.origen.fecha


def test_la_fecha_de_pago_explicita_manda_sobre_la_del_cargo(
    importacion, servicio, ids_catalogo
):
    """Si el usuario dice cuándo pagó, eso es lo que vale."""
    from datetime import date as fecha_tipo

    resultado = importacion.leer_documento(NU_REGULADO.encode())
    candidato = next(
        c for c in resultado.candidatos if "Apple" in c.origen.descripcion_banco
    )
    candidato.categoria_id = ids_catalogo["vivienda"]
    candidato.cuenta_id = ids_catalogo["cuenta"]
    candidato.pagado = True
    candidato.fecha_pago = fecha_tipo(2026, 7, 2)

    importacion.guardar_uno(candidato, ids_catalogo["cuenta"])

    assert servicio.buscar().iloc[0]["fecha_pago"].date() == fecha_tipo(2026, 7, 2)


def test_las_correcciones_sobreviven_a_recargar(importacion, ids_catalogo):
    """Tipo y fecha de pago corregidos van en el avance guardado."""
    from datetime import date as fecha_tipo

    resultado = importacion.leer_documento(MP_TARJETA.encode())
    candidato = resultado.candidatos[0]
    candidato.tipo_elegido = str(TipoMovimiento.INGRESO)
    candidato.fecha_pago = fecha_tipo(2026, 7, 21)

    importacion.guardar_avance(resultado, 0, "completar")
    devuelto, _, _ = importacion.recuperar_avance()
    primero = devuelto.candidatos[0]

    assert primero.tipo == str(TipoMovimiento.INGRESO)
    assert primero.fecha_pago == fecha_tipo(2026, 7, 21)
