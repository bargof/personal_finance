from __future__ import annotations

from pathlib import Path

import streamlit as st

from finanzas.application.app.theme import aplicar_estilos, firma_lateral
from finanzas.config.logging import setup_logging
from finanzas.config.settings import settings
from finanzas.data.seed import preparar_base

APP_DIR = Path(__file__).resolve().parent
PAGINAS_DIR = APP_DIR / "app_pages"
FAVICON = APP_DIR / "assets" / "favicon.png"


@st.cache_resource
def _arrancar() -> bool:
    """
    Prepara logging y base de datos una sola vez por proceso.

    Deja la aplicación utilizable en un clon recién bajado del repositorio:
    si el archivo SQLite no existe, se crea y se siembra aquí mismo.
    """
    setup_logging(level=settings.log_level, log_file=str(settings.log_file_path))
    preparar_base()

    return True


def main() -> None:
    """Inicializa la navegación multipágina de Streamlit."""
    st.set_page_config(
        page_title="Finanzas personales",
        # El icono propio si está; si no, uno de la tipografía de iconos,
        # para que un clon sin el PNG arranque igual.
        page_icon=str(FAVICON) if FAVICON.exists() else ":material/savings:",
        layout="wide",
    )
    _arrancar()
    aplicar_estilos()

    dashboard = st.Page(
        APP_DIR / "streamlit_app.py",
        title="Dashboard",
        icon=":material/dashboard:",
        default=True,
    )
    movimientos = st.Page(
        PAGINAS_DIR / "movimientos.py",
        title="Movimientos",
        icon=":material/receipt_long:",
    )
    presupuesto = st.Page(
        PAGINAS_DIR / "presupuesto.py",
        title="Presupuesto",
        icon=":material/savings:",
    )
    proyectos = st.Page(
        PAGINAS_DIR / "proyectos.py",
        title="Proyectos",
        icon=":material/folder_special:",
    )
    deseos = st.Page(
        PAGINAS_DIR / "deseos.py",
        title="Lista de deseos",
        icon=":material/favorite:",
    )
    metas = st.Page(
        PAGINAS_DIR / "metas.py",
        title="Metas",
        icon=":material/flag:",
    )
    patrimonio = st.Page(
        PAGINAS_DIR / "patrimonio.py",
        title="Patrimonio",
        icon=":material/account_balance_wallet:",
    )
    suscripciones = st.Page(
        PAGINAS_DIR / "suscripciones.py",
        title="Suscripciones",
        icon=":material/autorenew:",
    )
    analisis = st.Page(
        PAGINAS_DIR / "analisis.py",
        title="Análisis",
        icon=":material/insights:",
    )
    importar = st.Page(
        PAGINAS_DIR / "importar.py",
        title="Importar",
        icon=":material/upload_file:",
    )
    catalogos = st.Page(
        PAGINAS_DIR / "catalogos.py",
        title="Catálogos",
        icon=":material/category:",
    )
    configuracion = st.Page(
        PAGINAS_DIR / "configuracion.py",
        title="Configuración",
        icon=":material/settings:",
    )

    firma_lateral("Finanzas", "Control personal")

    navegacion = st.navigation(
        {
            "Resumen": [dashboard, analisis],
            "Captura": [movimientos, importar, presupuesto],
            "Planes": [metas, proyectos, deseos],
            "Balance": [patrimonio, suscripciones],
            "Ajustes": [catalogos, configuracion],
        }
    )

    navegacion.run()


"""
Pendientes del proyecto.

Hecho
-----
1. Subcategoría en suscripciones. Se agregó `suscripciones.subcategoria_id`
   y el servicio valida que la subcategoría cuelgue de la categoría elegida:
   el esquema no lo puede exigir porque las dos claves van por separado.
2. IDs de la base. El sembrado usaba `ON CONFLICT DO NOTHING` sobre tablas
   `AUTOINCREMENT`, que reserva el id antes de detectar el conflicto y no lo
   devuelve; corriendo en cada arranque, quemaba ~92 ids por arranque. Ya usa
   `WHERE NOT EXISTS` y sólo siembra cuando el catálogo está vacío.
   `scripts/compactar_ids.py` compactó los ids existentes a 1..N.
3. Monto de las cuentas. `patrimonio.cuenta_id` liga una posición del balance
   con su cuenta del catálogo, sin capturar el nombre dos veces. La página de
   patrimonio avisa qué cuentas activas aún no tienen saldo, para que el
   estado de situación financiera no se lea como completo estando incompleto.

4. Dark mode. El tema vive en `.streamlit/config.toml` y los colores que
   los gráficos reciben explícitos, en `theme.py`, que lee el tema activo
   en cada rerun para que las series sigan al tema.

4b. Gasto devengado. `movimientos.fecha_pago` separa cuándo se incurrió
   el gasto de cuándo salió el dinero: nula significa adeudo generado,
   que consume presupuesto en su mes pero no toca la caja y entra al
   balance como pasivo vía `v_por_pagar`. `estado` sigue respondiendo
   otra pregunta —si el movimiento ocurrió o es una proyección—, y las
   dos son independientes.

5. Lector de estados de cuenta. Cuatro formatos en `data/lectores`: los
   dos de Nu —el viejo con categoría, el nuevo con doble fecha—, la
   tarjeta de Mercado Pago en PDF y su cuenta en CSV. La extracción va
   por coordenadas y el parser trabaja sobre líneas, así que se puede
   probar sin arrastrar el PDF. El cuadre contra los totales del propio
   documento avisa si se perdió una fila.

6. Proyectos y lista de deseos. El proyecto agrupa gasto ya hecho y le
   pone tope; el deseo mide lo que falta contra el saldo de las cuentas
   ligadas al balance. El asistente de importación guarda conforme se
   avanza y deja su avance en `importacion_en_curso`, porque
   `session_state` de Streamlit no sobrevive una recarga.

Por hacer
---------
7. El cuarto banco, cuando se pueda sacar el archivo.
8. Clasificación que aprenda: hoy sólo se traduce la categoría que trae
   Nu. Guardando el concepto del banco —que ya va en la nota— se podría
   sugerir la categoría de un comercio visto antes.
9. Pasar base de datos a imac con api de consulta y escritura.
10. Proteger endpoints, base, app.

Además, encontrado al revisar el esquema y aún sin resolver:
- `movimientos` tampoco garantiza que `subcategoria_id` pertenezca a
  `categoria_id`. La UI lo filtra, pero el importador de Excel y la futura
  API no pasan por ahí. La validación ya existe en
  `CatalogosRepository.subcategoria_pertenece_a`; falta llamarla desde
  `MovimientosService`.
- `movimientos.cuenta_destino_id` cierra el traspaso entre cuentas propias.
  `v_flujo_cuentas` lo descompone en dos patas —sale del origen, entra al
  destino— porque por cuenta un traspaso no es neutro aunque lo sea para la
  caja. Es lo que permite registrar el pago de una tarjeta sin volver a
  contar el gasto: se marcan pagados los devengados y se captura el traspaso
  del banco a la tarjeta, que no es gasto.
- Sigue faltando derivar el saldo por cuenta de los movimientos; hoy el
  balance se captura a mano y se liga con `patrimonio.cuenta_id`.
"""
