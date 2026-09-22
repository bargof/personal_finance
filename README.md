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
    cuadrar_registros.py     Corrige lo capturado antes del modelo de cuentas
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
| `cuentas`, `medios_pago` | Dónde y cómo se mueve el dinero. El tipo de cuenta decide de qué lado del balance cae |
| `movimientos` | Una fila por movimiento, siempre con monto positivo |
| `saldos_verificados` | Lo único del balance que se captura: el saldo real de una cuenta al cierre de un día |
| `presupuestos` | Monto manual y % de recorte por categoría y periodo |
| `metas` | Objetivos con monto, fecha y aportación |
| `patrimonio` | Bienes y deudas que no son cuenta: la casa, el auto |
| `suscripciones` | Cobros recurrentes normalizados a costo mensual |
| `configuracion` | Reglas editables (metas, umbrales, moneda) |

Las vistas concentran lo derivado:

- `v_movimientos` — añade periodo, impacto en caja, gasto real, patrimonio
  creado, ahorro retirado e ingreso reconocido, más los nombres de catálogo.
- `v_resumen_mensual` — ingresos, gastos, aportaciones, retiros, ahorro neto
  y disponible por periodo.
- `v_flujo_cuentas` — el libro: una fila por pata de cada movimiento pagado
  y confirmado, con su efecto sobre la cuenta.

## Cuentas como libros

Cada cuenta es un libro y todo movimiento confirmado y pagado mueve una o
dos. El saldo no se captura: se deduce de los movimientos a partir de un
**saldo verificado** —lo que dice el estado de cuenta al inicio y al fin de
su periodo, o la app del banco hoy—. Con un saldo verificado *después* de
los movimientos, el sistema deduce hacia atrás cuánto había al principio,
que es lo que permite cargar estados de cuenta viejos sin conocer el saldo
inicial. Entre dos saldos verificados, los movimientos tienen que explicar
la diferencia; donde no, Patrimonio avisa cuánto falta y en qué cuenta.

| Tipo de cuenta | Lado | Qué implica |
|---|---|---|
| Efectivo, Débito, Vales | Activo | Caja |
| Ahorro, Inversión | Activo | Mover dinero aquí es aportación; sacarlo, retiro. El apartado de cada banco va aquí |
| Crédito, Préstamo | Pasivo | Deuda: el saldo va en negativo. Comprar la sube; pagarla la baja sin ser gasto |

## Reglas de captura

- El **monto siempre es positivo**; el `tipo` define su efecto.
- Sólo los movimientos **confirmados** alimentan presupuesto, score y KPIs.
- Cada tipo captura sólo lo que le corresponde (`domain/captura.py`): un
  ingreso no es esencial ni deseo, y a un traspaso no se le apuntan
  productos.
- Un gasto lleva **empresa** (quién cobró: Walmart, DiDi) y **lugar** (dónde:
  Mitikah, Coyoacán), los dos de texto libre con lo ya usado como
  sugerencia.
- Un gasto con **tarjeta de crédito** queda pagado por la tarjeta en el
  acto; lo que se debe es la tarjeta. Pagarla después es una
  `Transferencia` a la tarjeta, nunca otro gasto. Sólo un gasto desde
  efectivo, débito o ahorro puede quedar «por pagar».
- `Ahorro` e `Inversión` llevan cuenta de destino obligatoria, de tipo
  Ahorro o Inversión. Un retiro es una `Transferencia` desde esa cuenta, y
  el ahorro del mes es **neto**: aportaciones menos retiros.
- `Transferencia` mueve dinero entre cuentas propias con origen y destino
  obligatorios: pago de tarjeta, retiro en cajero (a Efectivo), traspaso
  entre bancos. Si el dinero fue a alguien más, es un gasto. Una
  transferencia *recibida* de otra cuenta propia tampoco es ingreso: es
  el traspaso visto desde la cuenta que recibe (el importador la reconoce
  por el nombre del titular, `TITULAR` en `.env`), y cuando llegue el
  estado de la cuenta emisora se reconocerá como su otra pata.
- Al importar un estado de cuenta, sus saldos declarados quedan como saldos
  verificados de la cuenta, y el pago de la tarjeta visto desde el otro
  estado se reconoce como la otra pata del mismo traspaso. Las ganancias
  de intereses de centavos se juntan en un ingreso por mes que conserva
  todos los folios, y lo que se desmarca en el asistente no se guarda (o
  se borra, si ya se había guardado).

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
