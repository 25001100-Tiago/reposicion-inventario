# Ejecuciones de Airflow

Airflow 3.3.2 con LocalExecutor y PostgreSQL 16. DAG `reposicion_inventario`. Las
fechas de ejecución son las reales de la corrida (2 de noviembre de 2026).

## Fases ejecutadas

| Fase | Comando `airflow backfill create` | Propósito | Corridas |
| --- | --- | --- | --- |
| A | `--from-date 2017-07-22 --to-date 2017-07-28 --reprocess-behavior none` | Semana de cortes de julio | 7 |
| B | `--from-date 2017-07-22 --to-date 2017-07-28 --reprocess-behavior completed` | Repetir la semana para medir idempotencia | 7 |
| C | `--from-date 2017-08-02 --to-date 2017-08-02 --reprocess-behavior completed` | Fecha en que llega la corrección | 1 |
| D | `--from-date 2017-07-24 --to-date 2017-07-24 --reprocess-behavior completed` | Volver a una fecha previa a la corrección | 1 |
| E | `--from-date 2017-08-15 --to-date 2017-08-15 --reprocess-behavior completed` | Dejar los datos al último corte | 1 |

Todas usaron `--max-active-runs 1`. En total fueron 17 corridas y 68 tareas.

## Estado final de las corridas

Airflow conserva el último intento de cada fecha lógica.

| Fecha de corte | Estado | Duración | Tareas en success |
| --- | --- | --- | --- |
| 2017-07-22 | success | 26 s | 4/4 |
| 2017-07-23 | success | 25 s | 4/4 |
| 2017-07-24 | success | 24 s | 4/4 |
| 2017-07-25 | success | 22 s | 4/4 |
| 2017-07-26 | success | 25 s | 4/4 |
| 2017-07-27 | success | 25 s | 4/4 |
| 2017-07-28 | success | 25 s | 4/4 |
| 2017-08-02 | success | 25 s | 4/4 |
| 2017-08-15 | success | 27 s | 4/4 |

| Tarea | success | Otro estado |
| --- | --- | --- |
| ingestar_validar | 9 | 0 |
| preparar_datos | 9 | 0 |
| generar_pronostico_y_pedido | 9 | 0 |
| evaluar_seguimiento_desempeno | 9 | 0 |

## Idempotencia

Se calculó una huella de las filas publicadas de cada corte, con cuantiles, pedido
y versiones de modelo y código.

| Comprobación | Resultado |
| --- | --- |
| Huellas de los 7 cortes iguales entre fases A y B | sí |
| `seguimiento_desempeno.csv` idéntico entre fases A y B | sí |
| Filas en `operacion.db` tras cada fase | 149688, 149688, 149688, 149688, 149688 |
| Fechas de corte en `corridas` tras cada fase | 12, 12, 12, 12, 12 |

El número de filas no crece al reprocesar: cada corrida reemplaza su fecha de corte.

## Reprocesamiento de la corrección

La venta del 2017-07-24 de la tienda 44 se entregó mal, y su corrección llegó el
2017-08-02. Ninguna fecha de corte de julio puede conocerla, así que la demostración
necesita una corrida en agosto. El corte 2017-07-22 pronosticó ese día, y el pedido
publicado no cambia: lo que cambia es su evaluación.

| Familia | Pedido publicado | Venta conocida en fase A | Costo en fase A | Venta corregida en fase C | Costo en fase C |
| --- | --- | --- | --- | --- | --- |
| BEVERAGES | 9,964.09 | 4,915.00 | 5,049.09 | 9,830.00 | 134.09 |
| GROCERY I | 10,461.54 | 4,685.00 | 5,776.54 | 9,370.00 | 1,091.54 |
| PRODUCE | 9,168.42 | 4,358.38 | 4,810.04 | 8,716.76 | 451.65 |
| **Total** | | | **15,635.67** | | **1,677.28** |

| Fase | `logical_date` procesado | Filas corregidas en el consolidado |
| --- | --- | --- |
| A | 2017-07-28 | 0 |
| B | 2017-07-28 | 0 |
| C | 2017-08-02 | 3 |
| D | 2017-07-24 | 0 |
| E | 2017-08-15 | 3 |

La fase D prueba que no hay fuga: al repetir el 2017-07-24 después de la corrección,
el consolidado vuelve a 0 filas corregidas, igual que lo conocido en esa fecha.

## Versiones publicadas

El corte usa el modelo más reciente entrenado con datos anteriores: `bcceedf85c55`
hasta el 2017-07-21 para julio, y `2085dd0ebbc6` hasta el 2017-07-31 desde esa fecha.

| Fecha de corte | Modelo | Código | Pedido total |
| --- | --- | --- | --- |
| 2017-07-22 | `bcceedf85c55` | `73af7ceb3c5c` | 6,374,273 |
| 2017-07-23 | `bcceedf85c55` | `73af7ceb3c5c` | 6,286,682 |
| 2017-07-24 | `bcceedf85c55` | `73af7ceb3c5c` | 6,310,919 |
| 2017-07-25 | `bcceedf85c55` | `73af7ceb3c5c` | 6,549,902 |
| 2017-07-26 | `bcceedf85c55` | `73af7ceb3c5c` | 6,737,005 |
| 2017-07-27 | `bcceedf85c55` | `73af7ceb3c5c` | 6,875,002 |
| 2017-07-28 | `bcceedf85c55` | `73af7ceb3c5c` | 7,005,063 |
| 2017-07-31 | `2085dd0ebbc6` | `13fbcca66e4c` | 7,273,673 |
| 2017-08-01 | `2085dd0ebbc6` | `13fbcca66e4c` | 7,067,129 |
| 2017-08-02 | `2085dd0ebbc6` | `73af7ceb3c5c` | 6,922,588 |
| 2017-08-08 | `2085dd0ebbc6` | `13fbcca66e4c` | 6,414,580 |
| 2017-08-15 | `2085dd0ebbc6` | `73af7ceb3c5c` | 6,210,978 |

Dos cortes ya publicados con `make` se reprocesaron en Airflow, que usa otro entorno
(Python 3.12 y pandas 3.0.5, frente a Python 3.14 y pandas 3.1.0). La tabla muestra
la mayor diferencia absoluta de pedido por fila en cada corte.

| Fecha de corte | Código anterior | Código actual | Diferencia máxima |
| --- | --- | --- | --- |
| 2017-07-31 | `13fbcca66e4c` | `13fbcca66e4c` | 0.00e+00 |
| 2017-08-01 | `13fbcca66e4c` | `13fbcca66e4c` | 0.00e+00 |
| 2017-08-02 | `13fbcca66e4c` | `73af7ceb3c5c` | 0.00e+00 |
| 2017-08-08 | `13fbcca66e4c` | `13fbcca66e4c` | 0.00e+00 |
| 2017-08-15 | `13fbcca66e4c` | `73af7ceb3c5c` | 0.00e+00 |

## Seguimiento de desempeño

Ventas conocidas hasta 2017-08-15, con correcciones aplicadas.

| fecha_corte | filas_evaluadas | filas_pendientes | ventas_reales | mae | sesgo | cobertura_p10_p90 | costo_quiebre_real | costo_merma_real | costo_real |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2017-07-22 | 12,474 | 0 | 5,822,348 | 60 | 17 | 89.8% | 652,803 | 769,526 | 1,422,329 |
| 2017-07-23 | 12,474 | 0 | 5,921,812 | 63 | 30 | 89.2% | 830,903 | 641,838 | 1,472,741 |
| 2017-07-24 | 12,474 | 0 | 5,991,104 | 65 | 34 | 89.4% | 913,138 | 624,194 | 1,537,332 |
| 2017-07-25 | 12,474 | 0 | 6,266,051 | 71 | 40 | 89.2% | 1,035,580 | 629,045 | 1,664,625 |
| 2017-07-26 | 12,474 | 0 | 6,490,109 | 75 | 45 | 89.0% | 1,127,783 | 622,823 | 1,750,606 |
| 2017-07-27 | 12,474 | 0 | 6,558,328 | 77 | 39 | 89.2% | 1,066,354 | 672,125 | 1,738,478 |
| 2017-07-28 | 12,474 | 0 | 6,551,004 | 77 | 28 | 89.6% | 959,331 | 773,836 | 1,733,167 |
| 2017-07-31 | 12,474 | 0 | 6,321,802 | 70 | -7 | 90.7% | 478,297 | 1,111,304 | 1,589,601 |
| 2017-08-01 | 12,474 | 0 | 6,051,040 | 66 | -16 | 90.8% | 369,324 | 1,139,197 | 1,508,521 |
| 2017-08-02 | 12,474 | 0 | 5,820,468 | 64 | -25 | 90.9% | 261,487 | 1,189,282 | 1,450,769 |
| 2017-08-08 | 12,474 | 0 | 5,393,755 | 75 | -19 | 88.8% | 457,468 | 1,173,315 | 1,630,783 |
| 2017-08-15 | 0 | 12,474 | 0 | n/d | n/d | n/d | 0 | 0 | 0 |

## Incidente durante la ejecución

La fase A quedó en `queued` unos cuatro minutos porque el DAG estaba pausado: el
scheduler no inicia corridas de backfill de un DAG en pausa. Se reanudó con
`airflow dags unpause`. No generó corridas con la fecha actual, porque `catchup=False`
y `end_date` dejan `next_dagrun` vacío.
