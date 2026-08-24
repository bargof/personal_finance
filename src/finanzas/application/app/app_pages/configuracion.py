from __future__ import annotations

from pathlib import Path

import streamlit as st

from finanzas.application.app.components import (
    invalidar_datos,
    moneda,
    obtener_servicios,
    porcentaje,
    reportar_error,
)
from finanzas.config.settings import settings
from finanzas.data.excel_import import importar_excel
from finanzas.domain.entities import ReglasFinancieras

# ═══════════════════════════════════════════════════════════
# Configuración: las reglas que mueven presupuesto y score
# ═══════════════════════════════════════════════════════════

servicios = obtener_servicios()
reglas = servicios.catalogos.reglas()

st.title("Configuración")
st.caption(
    "Estos parámetros alimentan el presupuesto, las alertas y el score. "
    "Cambiarlos recalcula el tablero completo."
)

reglas_tab, datos_tab, ayuda_tab = st.tabs(
    ["Reglas financieras", "Datos", "Cómo se calcula"]
)


# ═══════════════════════════════════════════════════════════
# Reglas
# ═══════════════════════════════════════════════════════════

with reglas_tab:
    with st.container(border=True):
        with st.form("reglas_financieras", border=False):
            fila_1 = st.columns(3)

            with fila_1[0]:
                monedas = ["MXN", "USD", "EUR"]
                if reglas.moneda not in monedas:
                    monedas.insert(0, reglas.moneda)
                nueva_moneda = st.selectbox(
                    "Moneda", monedas, index=monedas.index(reglas.moneda)
                )

            with fila_1[1]:
                meta_ahorro = st.slider(
                    "Meta de ahorro e inversión",
                    min_value=0.0,
                    max_value=0.6,
                    value=float(reglas.meta_ahorro_inversion),
                    step=0.01,
                    format="%.0f%%",
                    help="Proporción del ingreso que quieres convertir en "
                    "ahorro o inversión. Vale 35 puntos del score.",
                )

            with fila_1[2]:
                meses_fondo = st.number_input(
                    "Fondo de emergencia (meses)",
                    min_value=1,
                    max_value=24,
                    value=int(reglas.meses_fondo_emergencia),
                    step=1,
                    help="Meses de gasto esencial que debe cubrir tu colchón. "
                    "La referencia común es de 3 a 6.",
                )

            fila_2 = st.columns(3)

            with fila_2[0]:
                max_deseos = st.slider(
                    "Máximo en gustos",
                    min_value=0.0,
                    max_value=0.8,
                    value=float(reglas.max_deseos),
                    step=0.01,
                    format="%.0f%%",
                    help="Techo del gasto discrecional sobre el gasto total.",
                )

            with fila_2[1]:
                umbral_pequeno = st.number_input(
                    "Umbral de gasto pequeño",
                    min_value=1.0,
                    value=float(reglas.umbral_gasto_pequeno),
                    step=50.0,
                    help="Por debajo de este monto, un gasto cuenta como "
                    "microgasto en el análisis de fugas.",
                )

            with fila_2[2]:
                alerta = st.slider(
                    "Alerta de presupuesto desde",
                    min_value=0.5,
                    max_value=1.0,
                    value=float(reglas.alerta_presupuesto),
                    step=0.01,
                    format="%.0f%%",
                    help="% de presupuesto usado que marca una categoría en "
                    "«Atención», antes de que llegue al 100%.",
                )

            dia_ciclo = st.number_input(
                "Día de inicio del ciclo",
                min_value=1,
                max_value=28,
                value=int(reglas.dia_inicio_ciclo),
                step=1,
            )

            if st.form_submit_button(
                "Guardar reglas", type="primary", icon=":material/save:"
            ):
                try:
                    servicios.catalogos.guardar_reglas(
                        ReglasFinancieras(
                            moneda=nueva_moneda,
                            meta_ahorro_inversion=meta_ahorro,
                            meses_fondo_emergencia=int(meses_fondo),
                            max_deseos=max_deseos,
                            umbral_gasto_pequeno=umbral_pequeno,
                            alerta_presupuesto=alerta,
                            dia_inicio_ciclo=int(dia_ciclo),
                        )
                    )
                except ValueError as error:
                    reportar_error(error)
                else:
                    invalidar_datos()
                    st.success(
                        "Reglas guardadas. El tablero se recalculará.",
                        icon=":material/check_circle:",
                    )
                    st.rerun()

    with st.container(border=True):
        st.subheader("Reglas vigentes")
        st.dataframe(
            {
                "Parámetro": [
                    "Moneda",
                    "Meta de ahorro e inversión",
                    "Fondo de emergencia",
                    "Máximo en gustos",
                    "Umbral de gasto pequeño",
                    "Alerta de presupuesto",
                    "Día de inicio del ciclo",
                ],
                "Valor": [
                    reglas.moneda,
                    porcentaje(reglas.meta_ahorro_inversion, 0),
                    f"{reglas.meses_fondo_emergencia} meses",
                    porcentaje(reglas.max_deseos, 0),
                    moneda(reglas.umbral_gasto_pequeno),
                    porcentaje(reglas.alerta_presupuesto, 0),
                    str(reglas.dia_inicio_ciclo),
                ],
            },
            hide_index=True,
        )


# ═══════════════════════════════════════════════════════════
# Datos
# ═══════════════════════════════════════════════════════════

with datos_tab:
    with st.container(border=True):
        st.subheader("Base de datos")
        st.code(str(settings.db_path), language=None)

        tamano = (
            settings.db_path.stat().st_size / 1024 if settings.db_path.exists() else 0
        )
        movimientos = servicios.movimientos.buscar(limite=None)

        with st.container(horizontal=True):
            st.metric("Movimientos", len(movimientos), border=True)
            st.metric(
                "Periodos con datos",
                len(servicios.movimientos.periodos_disponibles()),
                border=True,
            )
            st.metric("Tamaño", f"{tamano:,.0f} KB", border=True)

    with st.container(border=True):
        st.subheader("Importar desde el Excel original")
        st.caption(
            "Trae movimientos, patrimonio, suscripciones, metas, cierres y "
            "presupuesto. Los registros se **añaden** a lo que ya existe, así "
            "que importar dos veces duplica."
        )

        ruta_texto = st.text_input(
            "Ruta del archivo .xlsx",
            value=str(settings.excel_source_path),
        )
        incluir_ejemplos = st.checkbox(
            "Incluir las filas marcadas como ejemplo", value=True
        )

        if st.button("Importar", type="primary", icon=":material/upload_file:"):
            try:
                resultado = importar_excel(
                    ruta=Path(ruta_texto),
                    incluir_ejemplos=incluir_ejemplos,
                )
            except FileNotFoundError as error:
                reportar_error(error)
            else:
                invalidar_datos()
                st.success(
                    f"Importado: {resultado.resumen()}",
                    icon=":material/check_circle:",
                )
                if resultado.omitidos:
                    with st.expander(
                        f"{len(resultado.omitidos)} filas omitidas",
                        icon=":material/warning:",
                    ):
                        for omitido in resultado.omitidos[:50]:
                            st.write(f"- {omitido}")

    with st.container(border=True):
        st.subheader("Exportar movimientos")
        st.caption("Descarga todo el histórico en CSV para respaldo o análisis.")

        if movimientos.empty:
            st.caption("Sin movimientos que exportar.")
        else:
            st.download_button(
                "Descargar CSV",
                data=movimientos.to_csv(index=False).encode("utf-8-sig"),
                file_name="movimientos.csv",
                mime="text/csv",
                icon=":material/download:",
            )


# ═══════════════════════════════════════════════════════════
# Ayuda
# ═══════════════════════════════════════════════════════════

with ayuda_tab:
    with st.container(border=True):
        st.subheader("Reglas de captura")
        st.markdown(
            """
            - El **monto siempre se captura en positivo**; el tipo de
              movimiento define si suma o resta.
            - **Transferencia** mueve dinero entre tus propias cuentas: no
              cuenta como ingreso ni como gasto.
            - Sólo los movimientos **confirmados** alimentan presupuesto,
              score e indicadores. Los pendientes quedan registrados sin
              afectar los números.
            """
        )

    with st.container(border=True):
        st.subheader("Score financiero")
        st.markdown("El score reparte 100 puntos entre cuatro señales:")
        st.dataframe(
            {
                "Componente": [
                    "Ahorro e inversión",
                    "Disciplina de presupuesto",
                    "Fondo de emergencia",
                    "Control de deseos",
                ],
                "Puntos": [35, 25, 25, 15],
                "Qué mide": [
                    "Tu tasa de ahorro contra la meta de "
                    f"{porcentaje(reglas.meta_ahorro_inversion, 0)}",
                    "Cuánto gastaste contra el presupuesto activo",
                    "Meses cubiertos contra el objetivo de "
                    f"{reglas.meses_fondo_emergencia}",
                    "Gasto discrecional bajo el techo de "
                    f"{porcentaje(reglas.max_deseos, 0)}",
                ],
            },
            hide_index=True,
        )

    with st.container(border=True):
        st.subheader("Presupuesto activo")
        st.markdown(
            f"""
            Cada categoría toma su presupuesto de una de dos fuentes:

            1. El **monto manual**, si lo capturaste y es mayor que cero.
            2. El **promedio de los últimos tres meses** menos el % de recorte.

            Una categoría pasa a *Atención* al llegar al
            {porcentaje(reglas.alerta_presupuesto, 0)} de su presupuesto y a
            *Excedido* al rebasarlo.
            """
        )
