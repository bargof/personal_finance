# Sistema financiero personal

Aplicación en Streamlit sobre SQLite que sustituye el Excel
`Sistema_Financiero_Personal.xlsx`: captura de movimientos, catálogos,
presupuesto mensual, metas, patrimonio, suscripciones y un dashboard de
análisis.

La lógica que en el Excel vivía en fórmulas (impacto en caja, gasto real,
presupuesto activo, score financiero) está en el dominio y en vistas SQL,
de modo que no pueda desincronizarse entre hojas.

## Arranque rápido

```bash
poetry install

# Opción A: base vacía con catálogos base
poetry run python scripts/init_db.py

# Opción B: además, importar el Excel original
poetry run python scripts/import_excel.py

poetry run streamlit run app.py
```

La aplicación crea y siembra la base automáticamente en el primer arranque,
así que `init_db.py` sólo hace falta si quieres preparar la base sin abrir
la interfaz.

## Estructura

```
app.py                       Punto de entrada de Streamlit
data/finanzas.db             Base SQLite (ignorada por git)
scripts/
    init_db.py               Crea el esquema y siembra catálogos
    import_excel.py          Migra el .xlsx original a SQLite
src/finanzas/
    config/                  Settings (pydantic-settings) y logging
    domain/                  Entidades, enums y reglas de negocio
    data/
        database.py          Esquema, vistas y conexión
        schemas.py           Contratos pandera de los DataFrames
        seed.py              Catálogos y reglas por defecto
        excel_import.py      Lectura del Excel original
        repositories/        Acceso a datos por agregado
    application/
        services/            Casos de uso
        app/                 Interfaz Streamlit (main + app_pages)
    analytics/               Agregaciones y KPIs del tablero
    intelligence/            Punto de extensión para ML y LLM
tests/                       Unitarias e integración
```

Las páginas sólo hablan con servicios; los servicios, con repositorios; y los
repositorios son los únicos que tocan SQLite.

## Modelo de datos

| Tabla | Qué guarda |
|---|---|
| `categorias`, `subcategorias` | Árbol de clasificación |
| `cuentas`, `medios_pago` | Dónde y cómo se mueve el dinero |
| `movimientos` | Una fila por movimiento, siempre con monto positivo |
| `presupuestos` | Monto manual y % de recorte por categoría y periodo |
| `metas` | Objetivos con monto, fecha y aportación |
| `patrimonio` | Activos y pasivos con su saldo actual |
| `suscripciones` | Cobros recurrentes normalizados a costo mensual |
| `cierres_mensuales` | Fotografía del balance al cierre de cada mes |
| `configuracion` | Reglas editables (metas, umbrales, moneda) |

Tres vistas concentran lo derivado:

- `v_movimientos` — añade periodo, impacto en caja, gasto real, patrimonio
  creado e ingreso reconocido, más los nombres de catálogo.
- `v_resumen_mensual` — ingresos, gastos, ahorro y disponible por periodo.
- `v_patrimonio_neto` — activos, pasivos y patrimonio neto.

## Reglas de captura

- El **monto siempre es positivo**; el `tipo` define su efecto.
- `Transferencia` mueve dinero entre cuentas propias y no toca ingresos ni
  gastos.
- Sólo los movimientos **confirmados** alimentan presupuesto, score y KPIs.

## Score financiero

Reparte 100 puntos entre cuatro señales, con la misma fórmula del Excel:

| Componente | Puntos | Mide |
|---|---|---|
| Ahorro e inversión | 35 | Tasa de ahorro frente a la meta |
| Disciplina de presupuesto | 25 | Gasto frente al presupuesto activo |
| Fondo de emergencia | 25 | Meses de gasto esencial cubiertos |
| Control de deseos | 15 | Gasto discrecional bajo su techo |

## Desarrollo

```bash
poetry run pytest
poetry run ruff check .
poetry run ruff format .
```

## Siguientes pasos

`src/finanzas/intelligence/` está reservado para lo que viene: clasificación
automática de la categoría a partir de la descripción, detección de gasto
anómalo y resúmenes en lenguaje natural. `siguiente_mejor_accion()` en
`analytics/kpis.py` es la regla determinista que esos modelos reemplazarán.
