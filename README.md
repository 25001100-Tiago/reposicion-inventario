# Reposición de inventario: Laboratorio 1

Sistema de pronóstico de demanda a 7 días y pedido sugerido, construido sobre el
dataset de Kaggle *Store Sales - Time Series Forecasting*. Para cada tienda y familia
de productos publica un pronóstico con incertidumbre (p10, p50, p90) y un pedido que
equilibra el costo de quedarse sin producto con el de que sobre.

El sistema simula una operación diaria real: los datos llegan por entregas, algunas
faltan o se corrigen después, y cada fecha de corte solo puede usar lo que ya se
conocía. Las justificaciones técnicas están en [DECISIONES.md](DECISIONES.md).

## Arquitectura

```text
 data/kaggle_raw/  (5 CSV de Kaggle)
        │  scripts/simular_sistema_origen.py
        ▼
 data/hist/ (hasta 2017-07-15)      data/bandeja/<fecha>/ (solo lectura)
        └───────────────┬───────────────┘
                        ▼  logical_date
  ingesta_validacion ──► preparacion ──► pronostico_pedido ──► seguimiento
   data/processed/       features         data/publicados/      data/publicados/
        ▲                                       ▲               docs/evidencia/
        │ rechaza datos críticos                │
        │                      entrenar_comparar (compuerta)
        │                      models/modelo_vigente.joblib
        │                      models/historial/
        └────────── orquestado por Airflow 3 (dags/) o por el Makefile
```

| Paso | Módulo en `src/inventario/` | Salida |
| --- | --- | --- |
| Ingesta y validación | `ingesta_validacion.py` | `data/processed/*.parquet` |
| Features sin fuga | `preparacion.py` | `data/processed/features.parquet` |
| Entrenamiento y compuerta | `entrenar_comparar.py` | `models/`, `docs/evidencia/metrics.json` |
| Pronóstico y pedido | `pronostico_pedido.py` | `data/publicados/pronosticos_pedidos.parquet`, `operacion.db` |
| Seguimiento | `seguimiento.py` | `data/publicados/seguimiento_desempeno.csv`, `docs/evidencia/` |

| Tecnología | Uso |
| --- | --- |
| Git y GitHub Actions | Código, PR y `pytest` en cada PR |
| DVC | Datos, modelos y salidas; en Git solo van los punteros `.dvc` |
| Airflow 3.3.2, PostgreSQL 16 y Docker Compose | Orquestación con LocalExecutor |
| scikit-learn | `HistGradientBoostingRegressor` con pérdida cuantílica |

## Dónde colocar los datos

Descarga los CSV de Kaggle y colócalos exactamente en `data/kaggle_raw/`, dentro del
repositorio. Git ignora esa carpeta.

```text
reposicion-inventario/
└── data/
    └── kaggle_raw/
        ├── train.csv
        ├── stores.csv
        ├── holidays_events.csv
        ├── oil.csv
        └── transactions.csv
```

## Levantar todo desde cero

Requisitos: Python 3.12 o superior, `make`, `openssl`, Git, y Docker con Compose para
Airflow. Los comandos son para Linux o WSL.

```bash
# 1. Clonar e instalar
git clone https://github.com/25001100-Tiago/reposicion-inventario.git
cd reposicion-inventario
make instalar

# 2. Colocar los 5 CSV en data/kaggle_raw/ (ver la sección anterior)

# 3. Generar la carga histórica y la bandeja diaria
make datos

# 4. Ejecutar las pruebas
make test

# 5. Entrenar y publicar las fechas de corte de agosto
make todo
```

`make datos` regenera exactamente los archivos que versiona DVC, porque el simulador
es determinista. El remoto de DVC es una carpeta local de la máquina donde se
desarrolló, así que `dvc pull` no funcionará en otro equipo salvo que apuntes a una
copia de ese almacén con `.venv/bin/dvc remote modify --local almacen-local url <ruta>`.

Estos pasos se probaron en un clon nuevo, sin datos previos: tardan unos 6 minutos sin
contar la descarga. `make datos` dio archivos idénticos a los versionados, pasaron las
75 pruebas, y las métricas y los 62.370 pedidos de los 5 cortes de `make todo`
coincidieron exactamente. Esa prueba no incluyó Airflow, que se ejecutó en el
repositorio principal. Tras `make todo`, Git puede mostrar cambios en `metrics.json` y
`seguimiento_desempeno.md`: el hash del modelo incluye el commit, y el seguimiento
refleja solo los cortes de esa corrida.

### Airflow

```bash
# Modelo con datos hasta el 21 de julio, necesario para los cortes de esa semana
make entrenar-historial LOGICAL_DATE=2017-07-21

# Genera airflow/.env con secretos propios y espera a que los servicios estén sanos
make airflow-up
curl http://localhost:8080/api/v2/monitor/health

# El scheduler no inicia corridas de backfill de un DAG pausado
AF="docker compose -f airflow/docker-compose.yaml exec airflow-scheduler airflow"
$AF dags unpause reposicion_inventario -y

# Semana de fechas de corte
$AF backfill create --dag-id reposicion_inventario \
  --from-date 2017-07-22 --to-date 2017-07-28 --max-active-runs 1

# Repetir la semana para comprobar la idempotencia
$AF backfill create --dag-id reposicion_inventario \
  --from-date 2017-07-22 --to-date 2017-07-28 --max-active-runs 1 \
  --reprocess-behavior completed
```

La interfaz queda en <http://localhost:8080>. Reanudar el DAG es seguro: `catchup=False`
y `end_date` impiden que cree una corrida con la fecha actual. Para detener todo,
usa `make airflow-down`.

### Comandos útiles

| Comando | Qué hace |
| --- | --- |
| `make corrida LOGICAL_DATE=2017-08-08` | Una fecha con el modelo vigente |
| `make entrenar LOGICAL_DATE=2017-07-31` | Entrena y aplica la compuerta |
| `make versionar` | `dvc add` y `dvc push` de las salidas |

## Situaciones de negocio simuladas

| Situación | Fecha | Comportamiento esperado |
| --- | --- | --- |
| No llega `oil.csv` | 2017-08-08 | Aviso y forward-fill, sin fallar |
| Fin de semana sin cotización | Sábados y domingos | Archivo sin filas y forward-fill |
| Corrección de ventas | Llega el 2017-08-02, corrige el 2017-07-24 | Actualiza el consolidado solo desde esa fecha |

## Resultados

### Comparación en validación

Partición temporal idéntica para ambos modelos: entrenamiento del 2017-01-01 al
2017-07-03, validación del 2017-07-04 al 2017-07-31, con 49.896 filas.

| Métrica | Baseline | Cuantiles (vigente) |
| --- | --- | --- |
| Costo de negocio | 5.672.534 | **5.279.248** (-6,9 %) |
| MAE | 60,26 | 58,66 |
| RMSE | 226,45 | 229,95 |
| Pinball medio | 30,13 | 29,79 |
| Pinball p10 | 32,86 | 41,80 |
| Pinball p50 | 30,13 | 29,33 |
| Pinball p90 | 27,40 | 18,24 |

La compuerta aprobó el modelo. Para reprocesar julio se entrenó otro con datos hasta
el 2017-07-21, que redujo el costo un 15,1 % en su propia validación. Detalle en
`docs/evidencia/metrics.json` y `docs/evidencia/metrics_2017-07-21.json`.

### Desempeño real

Ventas conocidas hasta el 2017-08-15. Cada corte cubre 7 días y 12.474 filas. Los
costos no deben sumarse entre cortes, porque sus horizontes se solapan.

| Fecha de corte | Modelo | MAE | Sesgo | Cobertura p10–p90 | Costo quiebre | Costo merma | Costo total |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2017-07-22 | hasta 07-21 | 60 | 17 | 89,8 % | 652.803 | 769.526 | 1.422.329 |
| 2017-07-25 | hasta 07-21 | 71 | 40 | 89,2 % | 1.035.580 | 629.045 | 1.664.625 |
| 2017-07-28 | hasta 07-21 | 77 | 28 | 89,6 % | 959.331 | 773.836 | 1.733.167 |
| 2017-07-31 | hasta 07-31 | 70 | -7 | 90,7 % | 478.297 | 1.111.304 | 1.589.601 |
| 2017-08-02 | hasta 07-31 | 64 | -25 | 90,9 % | 261.487 | 1.189.282 | 1.450.769 |
| 2017-08-08 | hasta 07-31 | 75 | -19 | 88,8 % | 457.468 | 1.173.315 | 1.630.783 |

Sesgo positivo significa que la venta real superó el p50. La tabla completa está en
`docs/evidencia/seguimiento_desempeno.md`.

### Airflow

17 corridas y 68 tareas; las 9 corridas vigentes terminaron con 36 de 36 tareas en
`success`. Repetir la semana dio huellas idénticas, sin filas duplicadas. El corte
2017-07-22 pronosticó el día corregido: el costo de esas 3 filas pasó de 15.635,67 con
la venta errónea a 1.677,28 con la corregida, sin cambiar el pedido publicado. Detalle
en `docs/evidencia/ejecuciones_airflow.md`.

### Limitaciones conocidas

- **Cuantiles mal calibrados:** el rango p10–p90 cubre cerca del 90 % de las ventas,
  no el 80 % nominal, y p10 es peor que el baseline en pinball.
- **Sesgo que cambia de signo:** el modelo hasta el 21 de julio se quedó corto y el
  entrenado hasta el 31 pidió de más. Sin reentrenamiento periódico el error persiste.
- **Beneficio sobre el baseline:** el baseline no lleva colchón para la razón crítica,
  así que parte del 6,9 % puede deberse a esa asimetría.
- **Sin inventario:** el dataset no trae existencias, por lo que el pedido es la
  cantidad objetivo de cada día.

## Prueba de reproducción con DVC

**Objetivo:** demostrar que un pronóstico pasado se reproduce exactamente a partir de
su versión de código y datos.

**Método:**

1. Se eligió el corte 2017-08-02, publicado con el código `13fbcca`. Sus artefactos
   quedaron versionados en `cf5f044`, y `src` y `configs` son iguales en ambos commits.
2. Se creó un `git worktree` temporal en `cf5f044`, sin caché local de DVC.
3. `dvc pull` trajo solo desde el remoto los datos, el modelo y lo publicado entonces.
4. `scripts/reproducir_pronostico.py` reconstruyó ingesta y features, y recalculó el
   pronóstico con el modelo de esa versión. Exige igualdad exacta, sin tolerancia.
5. Un control negativo recalculó con otro costo para probar que el comparador detecta
   diferencias.
6. Se eliminó el worktree.

**Resultado:** 12.474 de 12.474 filas idénticas, con la misma huella SHA-256 y el
mismo modelo. El corte incluye las 3 filas corregidas conocidas en esa fecha.

**Evidencia completa:** comandos y salidas tal como se ejecutaron, también disponibles
en `docs/evidencia/reproduccion_dvc.txt`.

```text
$ git log -1 --format="%h %s" cf5f044
cf5f044 chore: versionar data/processed, data/publicados y models con DVC
[exit 0]

$ git diff --stat 13fbcca cf5f044 -- src configs
[exit 0]

$ git worktree add --detach /tmp/lab1-repro cf5f044
Preparing worktree (detached HEAD cf5f044)
HEAD is now at cf5f044 chore: versionar data/processed, data/publicados y models con DVC
[exit 0]

$ git rev-parse --short=12 HEAD
cf5f044fe432
[exit 0]

$ ls data
bandeja.dvc
hist.dvc
processed.dvc
publicados.dvc
[exit 0]

$ cat data/publicados.dvc
outs:
- md5: c91ecad95c805cbb66bda138d8d4ea2b.dir
  size: 11376587
  nfiles: 3
  hash: md5
  path: publicados
[exit 0]

$ test -d .dvc/cache && echo "cache presente" || echo "sin cache local"
sin cache local
[exit 0]

$ dvc pull 2>&1 | tail -2
A       models/
114 files fetched and 171 files added
[exit 0]

$ dvc status
Data and pipelines are up to date.
[exit 0]

$ du -sh data/hist data/bandeja data/processed data/publicados models
116M	data/hist
2.8M	data/bandeja
32M	data/processed
11M	data/publicados
2.7M	models
[exit 0]

$ PYTHONPATH=src python $REPO/scripts/reproducir_pronostico.py --logical-date 2017-08-02
fecha de corte:              2017-08-02
filas corregidas conocidas:  3
filas publicadas:            12474
filas recalculadas:          12474
modelo publicado:            2085dd0ebbc6
modelo recalculado:          2085dd0ebbc6
código publicado:            13fbcca66e4c
sha256 publicado:            27d2864410e9b3fa59d8f53224bce38b0436aaad7d0917cda64134390480855a
sha256 recalculado:          27d2864410e9b3fa59d8f53224bce38b0436aaad7d0917cda64134390480855a
filas distintas:             0
control negativo (costo 9):  12474 filas distintas
RESULTADO: REPRODUCIDO AL 100 % (12474/12474 filas idénticas)
[exit 0]

$ git worktree remove --force /tmp/lab1-repro
[exit 0]

$ git worktree prune
[exit 0]

$ git worktree list
/home/santi_torres/product-dev/reposicion-inventario 073700c [docs/entrega-final]
[exit 0]

$ test -e /tmp/lab1-repro && echo 'worktree presente' || echo 'worktree eliminado'
worktree eliminado
[exit 0]
```

## Reglas del proyecto

Sin datos ni modelos en Git, trabajo por ramas y PR con Conventional Commits, sin
force push, y toda predicción usa solo datos con fecha `<= logical_date`. El detalle
está en [CLAUDE.md](CLAUDE.md).
