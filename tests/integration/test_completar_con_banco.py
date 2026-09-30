from __future__ import annotations

from datetime import date

import pytest

from finanzas.application.services.importacion_service import (
    DUPLICADO,
    NUEVO,
    POSIBLE,
    ImportacionService,
    deserializar,
    serializar,
)
from finanzas.application.services.movimientos_service import MovimientosService
from finanzas.data.repositories.movimientos_repository import MovimientosRepository
from finanzas.domain.enums import TipoMovimiento
from tests.integration.test_lectores import MP_CUENTA, MP_TARJETA

# ═══════════════════════════════════════════════════════════
# Completar lo capturado a mano con los datos del banco
#
# Una línea del estado de cuenta que ya se había capturado a
# mano no se importa otra vez: se le pasan al movimiento propio
# el concepto, el folio y la fecha del banco. Sólo cuando no hay
# duda de cuál es, y sin tocar lo que escribió el usuario.
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def importacion(movimientos: MovimientosRepository, db_path: str) -> ImportacionService:
    """Servicio de importación sobre la base de prueba."""
    return ImportacionService(movimientos, db_path=db_path)


@pytest.fixture
def servicio(movimientos: MovimientosRepository) -> MovimientosService:
    """Servicio de movimientos, para sembrar lo ya registrado."""
    return MovimientosService(movimientos)


def _capturar(servicio, ids_catalogo, **campos) -> int:
    """Registra un movimiento como lo haría el formulario de captura."""
    datos = {
        "tipo": TipoMovimiento.GASTO,
        "categoria_id": ids_catalogo["vivienda"],
        "cuenta_id": ids_catalogo["cuenta"],
        "descripcion": "Didi a casa",
    }
    datos.update(campos)
    return servicio.registrar(**datos)


def _leer(importacion, ids_catalogo, documento=MP_TARJETA):
    """Lee el documento como si fuera de la cuenta principal."""
    resultado = importacion.leer_documento(documento.encode())
    resultado.cuenta_id = ids_catalogo["cuenta"]
    importacion.contrastar(resultado)
    return resultado


def _del_monto(resultado, monto: float):
    return next(c for c in resultado.candidatos if c.origen.monto == monto)


def _fila(servicio, movimiento_id: int):
    df = servicio.buscar()
    return df[df["id"] == movimiento_id].iloc[0]


def test_una_coincidencia_exacta_se_propone_marcada(
    importacion, servicio, ids_catalogo
):
    movimiento_id = _capturar(
        servicio, ids_catalogo, fecha=date(2026, 7, 22), monto=37.0
    )

    candidato = _del_monto(_leer(importacion, ids_catalogo), 37.0)

    assert candidato.estado == DUPLICADO
    assert not candidato.incluir
    assert candidato.vinculo_id == movimiento_id
    assert candidato.completar
    assert "Didi a casa" in candidato.vinculo_resumen


def test_un_posible_duplicado_tambien_viene_marcado(
    importacion, servicio, ids_catalogo
):
    """Se revisa en su propia sección; desmarcarlo lo deja ignorado."""
    _capturar(servicio, ids_catalogo, fecha=date(2026, 8, 12), monto=341.0)

    candidato = _del_monto(_leer(importacion, ids_catalogo), 341.0)

    assert candidato.estado == POSIBLE
    assert candidato.vinculo_id is not None
    assert candidato.completar


def test_lo_que_ya_tiene_datos_del_banco_es_duplicado_de_verdad(
    importacion, servicio, ids_catalogo
):
    """Si ya vino de un estado de cuenta, no hay nada que completar."""
    _capturar(
        servicio,
        ids_catalogo,
        fecha=date(2026, 7, 22),
        monto=37.0,
        descripcion_banco="DIDI",
    )

    candidato = _del_monto(_leer(importacion, ids_catalogo), 37.0)

    assert candidato.estado != NUEVO
    assert not candidato.incluir
    assert candidato.vinculo_id is None


def test_un_traspaso_no_se_propone(importacion, servicio, ids_catalogo):
    """Sale en dos estados de cuenta y tiene un solo folio."""
    _capturar(
        servicio,
        ids_catalogo,
        fecha=date(2026, 7, 22),
        monto=37.0,
        tipo=TipoMovimiento.TRANSFERENCIA,
        cuenta_destino_id=ids_catalogo["ahorro_cuenta"],
        categoria_id=ids_catalogo["ahorro"],
    )

    candidato = _del_monto(_leer(importacion, ids_catalogo), 37.0)

    assert candidato.vinculo_id is None


def test_sin_cuenta_del_documento_no_se_propone(importacion, servicio, ids_catalogo):
    """Sin saber de qué cuenta es, podría completar el de otra."""
    _capturar(servicio, ids_catalogo, fecha=date(2026, 7, 22), monto=37.0)

    resultado = importacion.leer_documento(MP_TARJETA.encode())
    resultado.cuenta_id = None
    importacion.contrastar(resultado)

    assert _del_monto(resultado, 37.0).vinculo_id is None


def test_con_dos_candidatos_distintos_no_adivina(importacion, servicio, ids_catalogo):
    _capturar(servicio, ids_catalogo, fecha=date(2026, 8, 13), monto=341.0)
    _capturar(
        servicio,
        ids_catalogo,
        fecha=date(2026, 8, 13),
        monto=341.0,
        descripcion="Otra cosa",
    )

    candidato = _del_monto(_leer(importacion, ids_catalogo), 341.0)

    assert candidato.vinculo_id is None
    assert "no adivino" in candidato.motivo


def test_dos_iguales_se_emparejan_uno_a_uno(importacion, servicio, ids_catalogo):
    """Dos cafés iguales son intercambiables: cada línea completa uno."""
    primero = _capturar(servicio, ids_catalogo, fecha=date(2026, 7, 22), monto=37.0)
    segundo = _capturar(servicio, ids_catalogo, fecha=date(2026, 7, 22), monto=37.0)

    documento = MP_TARJETA.replace(
        "23/07 Compra en DIDI $ 37.00",
        "23/07 Compra en DIDI $ 37.00\n23/07 Compra en DIDI $ 37.00",
    )
    resultado = _leer(importacion, ids_catalogo, documento)
    didis = [c for c in resultado.candidatos if c.origen.monto == 37.0]

    assert {c.vinculo_id for c in didis} == {primero, segundo}


def test_completar_solo_escribe_los_datos_del_banco(
    importacion, servicio, ids_catalogo
):
    movimiento_id = _capturar(
        servicio, ids_catalogo, fecha=date(2026, 7, 22), monto=37.0
    )
    antes = _fila(servicio, movimiento_id)

    resultado = _leer(importacion, ids_catalogo)
    completados = importacion.aplicar_vinculos(resultado)
    despues = _fila(servicio, movimiento_id)

    assert completados == 1
    assert despues["descripcion_banco"] == "DIDI"
    assert despues["fecha_banco"].date() == date(2026, 7, 23)
    for campo in ("fecha", "descripcion", "categoria_id", "monto", "cuenta_id"):
        assert despues[campo] == antes[campo]
    # Y no se importó nada nuevo.
    assert len(servicio.buscar()) == 1


def test_pone_la_fecha_de_pago_solo_si_faltaba(importacion, servicio, ids_catalogo):
    """El estado de cuenta prueba que el dinero ya salió."""
    pendiente = _capturar(
        servicio,
        ids_catalogo,
        fecha=date(2026, 7, 22),
        monto=37.0,
        fecha_pago=None,
    )
    pagado = _capturar(
        servicio,
        ids_catalogo,
        fecha=date(2026, 8, 12),
        monto=341.0,
        fecha_pago=date(2026, 8, 12),
    )

    importacion.aplicar_vinculos(_leer(importacion, ids_catalogo))

    assert _fila(servicio, pendiente)["fecha_pago"].date() == date(2026, 7, 23)
    assert _fila(servicio, pagado)["fecha_pago"].date() == date(2026, 8, 12)


def test_desmarcar_despues_de_completar_lo_deja_como_estaba(
    importacion, servicio, ids_catalogo
):
    movimiento_id = _capturar(
        servicio, ids_catalogo, fecha=date(2026, 7, 22), monto=37.0, fecha_pago=None
    )
    antes = _fila(servicio, movimiento_id)

    resultado = _leer(importacion, ids_catalogo)
    importacion.aplicar_vinculos(resultado)
    _del_monto(resultado, 37.0).completar = False
    importacion.aplicar_vinculos(resultado)
    despues = _fila(servicio, movimiento_id)

    assert despues["descripcion_banco"] == ""
    assert despues["fecha_banco"] == antes["fecha_banco"]
    assert despues["fecha_pago"] is None or str(despues["fecha_pago"]) == "NaT"


def test_marcado_para_importar_no_se_completa(importacion, servicio, ids_catalogo):
    """Una línea es un movimiento nuevo o el que ya estaba, no las dos."""
    movimiento_id = _capturar(
        servicio, ids_catalogo, fecha=date(2026, 7, 22), monto=37.0
    )

    resultado = _leer(importacion, ids_catalogo)
    _del_monto(resultado, 37.0).incluir = True
    importacion.aplicar_vinculos(resultado)

    assert _fila(servicio, movimiento_id)["descripcion_banco"] == ""


def test_nunca_pisa_datos_del_banco_que_llegaron_despues(
    importacion, servicio, ids_catalogo, movimientos
):
    """La guarda va en el propio UPDATE, no sólo al leer."""
    movimiento_id = _capturar(
        servicio, ids_catalogo, fecha=date(2026, 7, 22), monto=37.0
    )
    resultado = _leer(importacion, ids_catalogo)

    servicio.actualizar(movimiento_id, descripcion_banco="Otro banco")
    importacion.aplicar_vinculos(resultado)
    candidato = _del_monto(resultado, 37.0)

    assert _fila(servicio, movimiento_id)["descripcion_banco"] == "Otro banco"
    assert not candidato.completado
    assert not candidato.completar


def test_con_el_folio_guardado_la_siguiente_importacion_lo_reconoce(
    importacion, servicio, ids_catalogo
):
    movimiento_id = _capturar(
        servicio,
        ids_catalogo,
        fecha=date(2026, 6, 3),
        monto=0.13,
        tipo=TipoMovimiento.INGRESO,
        categoria_id=ids_catalogo["sueldo"],
    )

    primera = _leer(importacion, ids_catalogo, MP_CUENTA)
    assert _del_monto(primera, 0.13).vinculo_id == movimiento_id
    importacion.aplicar_vinculos(primera)

    segunda = _leer(importacion, ids_catalogo, MP_CUENTA)
    repetido = _del_monto(segunda, 0.13)

    assert repetido.estado == DUPLICADO
    assert "folio" in repetido.motivo
    assert repetido.vinculo_id is None


def test_lo_completado_no_se_recontrasta(importacion, servicio, ids_catalogo):
    """Cambiar la cuenta del documento no debe olvidar lo ya aplicado."""
    _capturar(servicio, ids_catalogo, fecha=date(2026, 7, 22), monto=37.0)

    resultado = _leer(importacion, ids_catalogo)
    importacion.aplicar_vinculos(resultado)
    importacion.contrastar(resultado)

    assert _del_monto(resultado, 37.0).completado


def test_el_vinculo_sobrevive_a_recargar(importacion, servicio, ids_catalogo):
    _capturar(servicio, ids_catalogo, fecha=date(2026, 7, 22), monto=37.0)
    resultado = _leer(importacion, ids_catalogo)
    importacion.aplicar_vinculos(resultado)

    recuperado = _del_monto(deserializar(serializar(resultado)), 37.0)
    original = _del_monto(resultado, 37.0)

    assert recuperado.vinculo_id == original.vinculo_id
    assert recuperado.completar
    assert recuperado.completado
    assert recuperado.vinculo_resumen == original.vinculo_resumen


def test_lo_nuevo_sigue_siendo_nuevo(importacion, ids_catalogo):
    resultado = _leer(importacion, ids_catalogo)

    assert all(c.estado == NUEVO for c in resultado.candidatos)
    assert not resultado.vinculables
