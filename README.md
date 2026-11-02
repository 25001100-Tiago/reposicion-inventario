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

La configuración está en `configs/params.yaml` y los costos en
`configs/costos.yaml`. Las reglas de trabajo están en `CLAUDE.md`.
