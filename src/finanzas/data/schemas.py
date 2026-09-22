from __future__ import annotations

import pandas as pd
from pandera.pandas import Check, Column, DataFrameSchema

# ═══════════════════════════════════════════════════════════
# Contratos de los DataFrames que salen de la base de datos
#
# Validan la frontera entre el repositorio y todo lo que
# consume datos (servicios, analítica y, más adelante, ML).
# ═══════════════════════════════════════════════════════════

TIPOS_MOVIMIENTO = ["Ingreso", "Gasto", "Ahorro", "Inversión", "Transferencia"]

movimientos_schema = DataFrameSchema(
    columns={
        "id": Column(int, nullable=False, description="Identificador del movimiento."),
        "fecha": Column(
            "datetime64[ns]",
            nullable=False,
            description="Fecha en que ocurrió el movimiento.",
        ),
        "periodo": Column(
            str,
            nullable=False,
            checks=Check.str_matches(
                r"^\d{4}-\d{2}$",
                error="El periodo debe tener formato YYYY-MM.",
            ),
            description="Periodo mensual del movimiento.",
        ),
        "tipo": Column(
            str,
            nullable=False,
            checks=Check.isin(
                TIPOS_MOVIMIENTO,
                error="El tipo debe ser uno de los cinco tipos del catálogo.",
            ),
            description="Tipo de movimiento.",
        ),
        "monto": Column(
            float,
            nullable=False,
            checks=Check.gt(0, error="El monto siempre se captura en positivo."),
            description="Importe del movimiento.",
        ),
        "categoria": Column(str, nullable=False, description="Categoría asignada."),
        "cuenta": Column(str, nullable=False, description="Cuenta afectada."),
        "necesidad": Column(
            str,
            nullable=False,
            checks=Check.isin(["Esencial", "Deseo"]),
            description="Gasto indispensable o discrecional.",
        ),
        "naturaleza": Column(
            str,
            nullable=False,
            checks=Check.isin(["Fijo", "Variable"]),
            description="Gasto comprometido o variable.",
        ),
        "estado": Column(
            str,
            nullable=False,
            checks=Check.isin(["Confirmado", "Pendiente"]),
            description="Sólo lo confirmado alimenta los cálculos.",
        ),
        "impacto_caja": Column(
            float,
            nullable=False,
            description="Efecto neto sobre el efectivo.",
        ),
        "gasto_real": Column(
            float,
            nullable=False,
            checks=Check.ge(0),
            description="Gasto confirmado del periodo.",
        ),
        "patrimonio_creado": Column(
            float,
            nullable=False,
            checks=Check.ge(0),
            description="Ahorro e inversión confirmados.",
        ),
        "ingreso_real": Column(
            float,
            nullable=False,
            checks=Check.ge(0),
            description="Ingreso confirmado del periodo.",
        ),
    },
    checks=[
        Check(
            lambda df: (
                (df["tipo"] != "Ingreso") | (df["impacto_caja"] == df["monto"])
            ).all(),
            error="Un ingreso debe sumar su monto completo al impacto en caja.",
        ),
        Check(
            lambda df: (
                (df["tipo"] != "Transferencia") | (df["impacto_caja"] == 0)
            ).all(),
            error="Una transferencia entre cuentas propias no mueve la caja.",
        ),
    ],
    strict=False,
    coerce=True,
)


resumen_mensual_schema = DataFrameSchema(
    columns={
        "periodo": Column(
            str,
            nullable=False,
            checks=Check.str_matches(r"^\d{4}-\d{2}$"),
            description="Periodo mensual.",
        ),
        "ingresos": Column(float, nullable=False, checks=Check.ge(0)),
        "gastos": Column(float, nullable=False, checks=Check.ge(0)),
        "aportaciones": Column(float, nullable=False, checks=Check.ge(0)),
        "retiros": Column(float, nullable=False, checks=Check.ge(0)),
        # Neto: puede ser negativo en un mes en que se sacó más de lo que entró.
        "ahorro_inversion": Column(float, nullable=False),
        "disponible": Column(
            float,
            nullable=False,
            description="Ingresos menos gastos menos ahorro e inversión.",
        ),
    },
    checks=[
        Check(
            lambda df: df["periodo"].duplicated().sum() == 0,
            error="El resumen mensual no puede repetir periodos.",
        ),
    ],
    strict=False,
    coerce=True,
)


patrimonio_schema = DataFrameSchema(
    columns={
        "nombre": Column(str, nullable=False),
        "tipo": Column(
            str,
            nullable=False,
            checks=Check.isin(
                ["Activo", "Pasivo"],
                error="Una posición patrimonial es Activo o Pasivo.",
            ),
        ),
        "saldo": Column(
            float,
            nullable=False,
            checks=Check.ge(
                0,
                error="Los pasivos también se capturan en positivo; el signo lo "
                "pone el cálculo.",
            ),
        ),
        "liquidez": Column(
            str,
            nullable=False,
            checks=Check.isin(["Alta", "Media", "Baja", "No aplica"]),
        ),
    },
    strict=False,
    coerce=True,
)


def validar_movimientos(df: pd.DataFrame) -> pd.DataFrame:
    """Valida el DataFrame de movimientos enriquecido."""
    return movimientos_schema.validate(df)


def validar_resumen_mensual(df: pd.DataFrame) -> pd.DataFrame:
    """Valida el resumen agregado por periodo."""
    return resumen_mensual_schema.validate(df)


def validar_patrimonio(df: pd.DataFrame) -> pd.DataFrame:
    """Valida el balance de activos y pasivos."""
    return patrimonio_schema.validate(df)
