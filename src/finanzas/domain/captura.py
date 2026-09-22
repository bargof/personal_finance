from __future__ import annotations

from finanzas.domain.enums import TipoCuenta, TipoMovimiento

# ═══════════════════════════════════════════════════════════
# Qué se captura en cada tipo de movimiento
#
# Un ingreso no es esencial ni deseo, un traspaso no estaba
# planeado ni deja de estarlo, y a un ahorro no se le apuntan
# productos. Cada tipo declara sus campos aquí, en un solo
# lugar, y los formularios los consultan en vez de decidirlo
# cada uno por su cuenta.
# ═══════════════════════════════════════════════════════════

#: Campos que admite cada tipo, además de fecha, monto, descripción y nota.
CAMPOS_POR_TIPO: dict[str, frozenset[str]] = {
    TipoMovimiento.GASTO: frozenset(
        {
            "categoria",
            "cuenta",
            "medio_pago",
            "necesidad",
            "naturaleza",
            "recurrente",
            "planeado",
            "empresa",
            "lugar",
            "hora",
            "productos",
            "pago",
            "estado",
            "proyecto",
            "etiquetas",
            "fecha_banco",
        }
    ),
    TipoMovimiento.INGRESO: frozenset(
        {"categoria", "cuenta", "estado", "proyecto", "etiquetas", "fecha_banco"}
    ),
    TipoMovimiento.AHORRO: frozenset(
        {
            "categoria",
            "cuenta",
            "cuenta_destino",
            "estado",
            "proyecto",
            "etiquetas",
            "fecha_banco",
        }
    ),
    TipoMovimiento.INVERSION: frozenset(
        {
            "categoria",
            "cuenta",
            "cuenta_destino",
            "estado",
            "proyecto",
            "etiquetas",
            "fecha_banco",
        }
    ),
    TipoMovimiento.TRANSFERENCIA: frozenset(
        {"cuenta", "cuenta_destino", "proyecto", "etiquetas", "fecha_banco"}
    ),
}


def admite(tipo: str, campo: str) -> bool:
    """Indica si un tipo de movimiento captura ese campo."""
    return campo in CAMPOS_POR_TIPO.get(str(tipo), frozenset())


def con_destino(tipo: str) -> bool:
    """Indica si el tipo mueve dinero a una segunda cuenta propia."""
    return admite(tipo, "cuenta_destino")


def etiqueta_cuenta(tipo: str) -> str:
    """Cómo se llama la cuenta de origen en el formulario, según el tipo."""
    if str(tipo) == TipoMovimiento.INGRESO:
        return "Cuenta donde entra"
    if con_destino(tipo):
        return "Cuenta de origen"
    return "Cuenta"


def pago_lo_decide_la_cuenta(tipo: str, tipo_cuenta: str | None) -> bool:
    """
    Indica si «ya se pagó» no se pregunta porque la cuenta lo responde.

    Con una tarjeta de crédito el gasto queda pagado en el acto: lo pagó
    la tarjeta, y lo que se debe es la tarjeta, no el gasto. En los demás
    tipos la pregunta ni existe: un ingreso o un traspaso ocurren o no.
    """
    if not admite(tipo, "pago"):
        return True
    if tipo_cuenta is None:
        return False

    return TipoCuenta(tipo_cuenta).es_pasivo


def describir_traspaso(
    tipo: str, tipo_origen: str | None, tipo_destino: str | None
) -> tuple[str, str] | None:
    """
    Explica qué significa mover dinero entre esas dos cuentas.

    Devuelve `(nivel, mensaje)` con nivel «info», «aviso» o «error», o
    None cuando no hay nada que decir. Es lo que el formulario muestra
    para que el usuario vea las consecuencias antes de guardar.
    """
    if not con_destino(tipo) or tipo_origen is None:
        return None

    origen = TipoCuenta(tipo_origen)
    destino = TipoCuenta(tipo_destino) if tipo_destino else None
    es_ahorro = str(tipo) in (TipoMovimiento.AHORRO, TipoMovimiento.INVERSION)

    if destino is None:
        if es_ahorro:
            return (
                "error",
                "Elige la cuenta de ahorro o inversión a la que entra el dinero.",
            )
        return (
            "error",
            "Elige a dónde llega el dinero. Si fue a alguien más, no es un "
            "traspaso: regístralo como gasto.",
        )

    if es_ahorro and not destino.guarda_ahorro:
        return (
            "error",
            f"«{destino}» no es una cuenta de ahorro ni de inversión. Da de "
            "alta un apartado en Catálogos o usa Transferencia.",
        )

    if destino.es_pasivo:
        return (
            "info",
            "Es un pago de tarjeta o préstamo: baja la deuda sin contar como "
            "gasto, porque el gasto ya se contó al comprar.",
        )

    if origen.guarda_ahorro and not destino.guarda_ahorro:
        return (
            "aviso",
            "Sale de tu ahorro: cuenta como retiro y resta al ahorro neto del mes.",
        )

    if destino.guarda_ahorro and not es_ahorro and not origen.guarda_ahorro:
        return (
            "aviso",
            "Entra a una cuenta de ahorro: cuenta como aportación. Si es "
            "ahorro a propósito, regístralo como Ahorro para que lleve su "
            "categoría.",
        )

    if destino == TipoCuenta.EFECTIVO:
        return ("info", "Retiro en efectivo: el dinero sigue siendo tuyo, en cartera.")

    if es_ahorro:
        return ("info", f"Entra a «{destino}» y suma a tu ahorro del mes.")

    return None
