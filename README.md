# reposicion-inventario

Laboratorio 1: pronóstico y reposición de inventario con el dataset de Kaggle
*Store Sales - Time Series Forecasting*.

## Puesta en marcha

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dvc.txt
.venv/bin/dvc pull          # descarga data/hist y data/bandeja
.venv/bin/pytest
```

## Datos

| Ruta | Contenido |
| --- | --- |
| `data/hist/` | Carga histórica inicial, hasta 2017-07-15 |
| `data/bandeja/<fecha>/` | Entrega diaria externa, de 2017-07-16 a 2017-08-15. Solo lectura para el pipeline |
| `data/processed/`, `data/publicados/`, `models/` | Salidas del pipeline, sin versionar en Git |

Cada carpeta diaria trae `ventas.csv`, `oil.csv`, `transactions.csv`,
`holidays.csv` y `stores.csv`. Dos situaciones de negocio son deliberadas:

- **2017-08-08:** no llega `oil.csv`. Los fines de semana sí llega, pero sin
  filas, porque no hay cotización.
- **2017-08-02:** llega `ventas_correccion.csv`, que corrige ventas de la tienda
  44 del 2017-07-24. La corrección solo existe para `logical_date >= 2017-08-02`.

Para regenerar los datos a partir de los CSV de Kaggle:

```bash
.venv/bin/python scripts/simular_sistema_origen.py --sobrescribir
```

## Pipeline

```bash
make todo                                # entrena y publica todas las fechas de corte
make corrida LOGICAL_DATE=2017-08-08     # una fecha, con el modelo vigente
make entrenar LOGICAL_DATE=2017-07-31    # entrena y aplica la compuerta
make test
make versionar                           # dvc add y dvc push de las salidas
```

| Paso | Módulo | Salida |
| --- | --- | --- |
| Ingesta y validación | `ingesta_validacion.py` | `data/processed/*.parquet` |
| Features | `preparacion.py` | `data/processed/features.parquet` |
| Entrenamiento y compuerta | `entrenar_comparar.py` | `models/modelo_vigente.joblib`, `docs/evidencia/` |
| Pronóstico y pedido | `pronostico_pedido.py` | `data/publicados/pronosticos_pedidos.parquet`, `operacion.db` |
| Seguimiento | `seguimiento.py` | `data/publicados/seguimiento_desempeno.csv`, `docs/evidencia/` |

La configuración está en `configs/params.yaml` y los costos en
`configs/costos.yaml`. Las reglas de trabajo están en `CLAUDE.md`.
