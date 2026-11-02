# Reglas del proyecto reposicion-inventario

## Datos y modelos

- Cero datos o modelos en Git. Todo se versiona con DVC (`dvc add`, `dvc push`);
  en Git solo entran los punteros `.dvc`. La única excepción son las tablas de
  métricas agregadas en `docs/evidencia/`.
- `data/bandeja/` es una entrega externa y es de solo lectura para el pipeline.
  Únicamente `scripts/simular_sistema_origen.py` la genera.

## Git

- Se trabaja por ramas y Pull Requests hacia `main`. No se hace commit directo a `main`.
- Los commits siguen Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`, `test:`).
- Prohibido el force push en cualquier forma: `--force`, `-f`,
  `--force-with-lease` o refspec con `+`.
- Un PR se fusiona solo con `pytest` en verde en CI.

## Cero fuga temporal

Toda predicción usa solo datos con fecha `<= logical_date`. Sin excepciones:

- Los filtros de entrenamiento se recortan siempre a `logical_date`
  (`inventario.filtros.filtrar_entrenamiento`).
- Una entrega cuenta por su fecha de llegada, no por la fecha que describe: una
  corrección entregada el día E para el día D solo existe si `logical_date >= E`.
- Las variables y la imputación solo miran hacia atrás. Si falta `oil`, se usa el
  último valor conocido, nunca uno posterior.
- `docs/evidencia/simulacion_resumen.json` describe el futuro de la simulación:
  el pipeline no debe leerlo.

## Configuración

- Los parámetros van en `configs/params.yaml` y los costos en `configs/costos.yaml`.
  No se escriben fechas, tiendas ni costos en el código.
