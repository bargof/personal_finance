from __future__ import annotations

import calendar
from datetime import date

import pandas as pd

# ═══════════════════════════════════════════════════════════
# Agregaciones sobre movimientos
#
# Reproducen los cortes de la hoja «Análisis» del Excel:
# tendencia, mezcla de gasto, medios de pago, calendario y
# el bloque de fugas. Todas reciben el DataFrame ya filtrado
# y devuelven otro DataFrame, sin tocar la base de datos.
# ═══════════════════════════════════════════════════════════

#: Nombres de mes en español, para etiquetas legibles.
MESES_ES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def etiqueta_periodo(periodo: str) -> str:
    """Convierte '2026-08' en 'agosto 2026'."""
    try:
        anio, mes = periodo.split("-")
        return f"{MESES_ES[int(mes) - 1]} {anio}"
    except (ValueError, IndexError):
        return periodo


def periodo_de(momento: date) -> str:
    """Devuelve el periodo YYYY-MM de una fecha."""
    return momento.strftime("%Y-%m")


def desplazar_periodo(periodo: str, meses: int) -> str:
    """Suma (o resta) meses a un periodo YYYY-MM."""
    anio, mes = (int(parte) for parte in periodo.split("-"))
    total = anio * 12 + (mes - 1) + meses
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def tendencia_mensual(resumen: pd.DataFrame, periodo_final: str, meses: int = 12):
    """
    Devuelve la serie de los últimos `meses` periodos, rellenando los vacíos.

    Un mes sin movimientos aparece en cero en lugar de desaparecer, para que
    la línea de tendencia no mienta sobre la continuidad del histórico.

    Parameters
    ----------
    resumen : pandas.DataFrame
        Salida de `MovimientosRepository.resumen_mensual`.
    periodo_final : str
        Último periodo de la ventana, en formato YYYY-MM.
    meses : int
        Tamaño de la ventana.

    Returns
    -------
    pandas.DataFrame
        Una fila por periodo, con etiqueta legible y métricas en cero
        donde no hubo movimientos.
    """
    ventana = [
        desplazar_periodo(periodo_final, -desfase)
        for desfase in range(meses - 1, -1, -1)
    ]
    base = pd.DataFrame({"periodo": ventana})

    columnas = ["ingresos", "gastos", "ahorro_inversion", "disponible", "movimientos"]
    if resumen.empty:
        for columna in columnas:
            base[columna] = 0.0
    else:
        base = base.merge(resumen, on="periodo", how="left")
        for columna in columnas:
            if columna not in base.columns:
                base[columna] = 0.0
        base[columnas] = base[columnas].fillna(0.0)

    base["etiqueta"] = base["periodo"].map(etiqueta_periodo)
    return base


def gasto_por_categoria(movimientos: pd.DataFrame) -> pd.DataFrame:
    """Agrupa el gasto real por categoría, de mayor a menor."""
    if movimientos.empty:
        return pd.DataFrame(columns=["categoria", "gasto", "pct_del_total"])

    df = (
        movimientos.groupby("categoria", as_index=False)["gasto_real"]
        .sum()
        .rename(columns={"gasto_real": "gasto"})
    )
    df = df[df["gasto"] > 0].sort_values("gasto", ascending=False)

    total = df["gasto"].sum()
    df["pct_del_total"] = df["gasto"] / total if total else 0.0

    return df.reset_index(drop=True)


def gasto_por_subcategoria(movimientos: pd.DataFrame, top: int = 15) -> pd.DataFrame:
    """Agrupa el gasto real por subcategoría y devuelve las `top` mayores."""
    if movimientos.empty:
        return pd.DataFrame(columns=["categoria", "subcategoria", "gasto"])

    df = movimientos[movimientos["gasto_real"] > 0].copy()
    df["subcategoria"] = df["subcategoria"].replace("", "Sin subcategoría")

    agrupado = (
        df.groupby(["categoria", "subcategoria"], as_index=False)["gasto_real"]
        .sum()
        .rename(columns={"gasto_real": "gasto"})
        .sort_values("gasto", ascending=False)
        .head(top)
    )

    return agrupado.reset_index(drop=True)


def mezcla_de_gasto(movimientos: pd.DataFrame, dimension: str) -> pd.DataFrame:
    """
    Reparte el gasto real entre los valores de una dimensión.

    Parameters
    ----------
    movimientos : pandas.DataFrame
        Movimientos del periodo.
    dimension : {'necesidad', 'naturaleza', 'medio_pago'}
        Columna por la que se reparte el gasto.

    Returns
    -------
    pandas.DataFrame
        Columnas: la dimensión, `gasto` y `proporcion`.
    """
    if dimension not in {"necesidad", "naturaleza", "medio_pago"}:
        raise ValueError(f"Dimensión no soportada: {dimension}")

    if movimientos.empty:
        return pd.DataFrame(columns=[dimension, "gasto", "proporcion"])

    df = movimientos[movimientos["gasto_real"] > 0].copy()
    if df.empty:
        return pd.DataFrame(columns=[dimension, "gasto", "proporcion"])

    if dimension == "medio_pago":
        df[dimension] = df[dimension].replace("", "Sin especificar")

    agrupado = (
        df.groupby(dimension, as_index=False)["gasto_real"]
        .sum()
        .rename(columns={"gasto_real": "gasto"})
        .sort_values("gasto", ascending=False)
    )

    total = agrupado["gasto"].sum()
    agrupado["proporcion"] = agrupado["gasto"] / total if total else 0.0

    return agrupado.reset_index(drop=True)


def calendario_de_gasto(movimientos: pd.DataFrame, periodo: str) -> pd.DataFrame:
    """
    Devuelve el gasto de cada día del periodo, incluidos los días en cero.

    Los días sin gasto son la señal que interesa: aparecen explícitos para
    poder contarlos.
    """
    anio, mes = (int(parte) for parte in periodo.split("-"))
    dias_del_mes = calendar.monthrange(anio, mes)[1]

    base = pd.DataFrame({"dia": range(1, dias_del_mes + 1)})
    base["fecha"] = pd.to_datetime([date(anio, mes, dia) for dia in base["dia"]])

    if movimientos.empty:
        base["gasto"] = 0.0
    else:
        por_dia = (
            movimientos.groupby("dia", as_index=False)["gasto_real"]
            .sum()
            .rename(columns={"gasto_real": "gasto"})
        )
        base = base.merge(por_dia, on="dia", how="left")
        base["gasto"] = base["gasto"].fillna(0.0)

    base["sin_gasto"] = base["gasto"] == 0

    return base


def fugas(movimientos: pd.DataFrame, umbral_gasto_pequeno: float) -> dict[str, float]:
    """
    Cuantifica el gasto que suele escaparse sin decisión consciente.

    Parameters
    ----------
    movimientos : pandas.DataFrame
        Movimientos del periodo.
    umbral_gasto_pequeno : float
        Monto por debajo del cual un gasto cuenta como microgasto.

    Returns
    -------
    dict
        Monto y cantidad de microgastos, gasto no planeado y deseos no
        planeados.
    """
    vacio = {
        "microgastos_monto": 0.0,
        "microgastos_cantidad": 0,
        "gasto_no_planeado": 0.0,
        "deseos_no_planeados": 0.0,
    }
    if movimientos.empty:
        return vacio

    gastos = movimientos[movimientos["gasto_real"] > 0]
    if gastos.empty:
        return vacio

    micro = gastos[gastos["monto"] <= umbral_gasto_pequeno]
    no_planeado = gastos[~gastos["planeado"]]
    deseos_sueltos = no_planeado[no_planeado["necesidad"] == "Deseo"]

    return {
        "microgastos_monto": float(micro["gasto_real"].sum()),
        "microgastos_cantidad": int(len(micro)),
        "gasto_no_planeado": float(no_planeado["gasto_real"].sum()),
        "deseos_no_planeados": float(deseos_sueltos["gasto_real"].sum()),
    }


def flujo_por_cuenta(movimientos: pd.DataFrame) -> pd.DataFrame:
    """
    Devuelve entradas, salidas y flujo neto por cuenta.

    Atribuye cada transferencia a sus dos cuentas: sale del origen y
    entra al destino. `impacto_caja` no sirve aquí porque mira la caja
    completa, donde el traspaso vale cero.
    """
    if movimientos.empty:
        return pd.DataFrame(columns=["cuenta", "entradas", "salidas", "flujo_neto"])

    df = movimientos.copy()
    df["entradas"] = df["impacto_caja"].clip(lower=0)
    df["salidas"] = -df["impacto_caja"].clip(upper=0)

    # La pata de destino del traspaso, que `impacto_caja` deja fuera. Se
    # omite si el DataFrame viene sin las columnas del destino, que es el
    # caso de los agregados armados a mano.
    columnas_destino = {"cuenta_destino_id", "cuenta_destino"}
    traspasos = df.iloc[0:0]
    if columnas_destino <= set(df.columns):
        es_traspaso = (df["tipo"] == "Transferencia") & df["cuenta_destino_id"].notna()
        if "pagado" in df.columns:
            es_traspaso &= df["pagado"].astype(bool)
        traspasos = df[es_traspaso]

    if not traspasos.empty:
        df.loc[traspasos.index, "salidas"] = traspasos["monto"]
        destino = pd.DataFrame(
            {
                "cuenta": traspasos["cuenta_destino"],
                "entradas": traspasos["monto"],
                "salidas": 0.0,
            }
        )
        df = pd.concat([df, destino], ignore_index=True)

    agrupado = df.groupby("cuenta", as_index=False)[["entradas", "salidas"]].sum()
    agrupado["flujo_neto"] = agrupado["entradas"] - agrupado["salidas"]

    return agrupado.sort_values("flujo_neto", ascending=False).reset_index(drop=True)
