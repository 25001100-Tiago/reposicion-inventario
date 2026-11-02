# Decisiones técnicas

Cada decisión indica por qué se tomó, qué alternativas se descartaron y qué costo
tiene. Las cifras provienen de `docs/evidencia/`.

## 1. Disponibilidad de la información en el tiempo

**Regla:** una predicción con fecha de corte `logical_date` (L) solo usa datos
disponibles en L. Las ventas del día L se consideran conocidas al cierre, así que el
pronóstico cubre de L+1 a L+7.

### Las variables se miran desde t-7

Cada fila representa un día objetivo t, y todo lo observado se toma de t-7 o antes.
Como el horizonte es 7, t-7 nunca supera L, y la misma tabla sirve para todos los
horizontes. El código rechaza un horizonte mayor que 7.

| Alternativa | Por qué se descartó |
| --- | --- |
| Un modelo por horizonte | Siete veces más entrenamiento por cuantil, con más riesgo de incoherencia entre días |
| Pronóstico recursivo | Alimenta el modelo con sus propias predicciones y acumula error |
| Filas por par (corte, horizonte) | Permite usar t-1 para h=1, pero multiplica las filas por 7 |

**Costo:** para h=1 se ignoran seis días ya conocidos. Se sacrificó precisión de corto
plazo a cambio de simplicidad y de una garantía fácil de verificar.

### Las variables del día objetivo no se usan si no se conocen

| Variable | Tratamiento | Motivo |
| --- | --- | --- |
| Día de semana y del mes | Del día t | Es calendario, siempre se conoce |
| Promociones | Rezago de 7 días | La fuente no entrega el plan futuro |
| Petróleo y transacciones | Rezago de 7 días | Son observaciones |
| Feriados | t-7 y misma fecha del año anterior | La bandeja no entrega calendario futuro |

Usar `onpromotion` o el feriado del día t mejoraría las métricas en validación, pero
sería una fuga: en producción ese dato no existiría y el modelo se habría entrenado
con una variable que no tendría al pronosticar. El feriado del año anterior recupera
los feriados de fecha fija sin mirar el futuro, aunque no los móviles como Carnaval.

### Una entrega vale desde su llegada, no desde la fecha que describe

Una corrección entregada el día E para el día D solo existe si L >= E. La carpeta de
la bandeja marca esa fecha, y la ingesta ni siquiera abre las carpetas posteriores a L.

### Varias defensas independientes

| Defensa | Evita |
| --- | --- |
| La ingesta no abre carpetas posteriores a L | Leer datos futuros |
| `construir_features` recorta sus insumos a L | Fuga aunque el consolidado sea más nuevo |
| `exigir_ingesta` y la fecha en `features.parquet` | Usar un consolidado de otra fecha de corte |
| `resolver_modelo` | Usar un modelo entrenado con datos posteriores a L |
| `seguimiento` recorta las ventas a L | Evaluar con ventas aún no conocidas |

La prueba clave altera todos los datos posteriores a L con valores absurdos y exige
que las features no cambien en nada.

### Historial de modelos

El pronóstico rechazaba cortes anteriores al modelo vigente, lo que impedía reprocesar
julio con un modelo entrenado hasta el 31. Se agregó `models/historial/`, y cada corte
usa el modelo más reciente con datos <= L. Desactivar la verificación habría sido la
solución rápida, pero habría introducido la fuga que el sistema debe impedir.

## 2. Validación de datos

**Criterio:** se detiene todo lo que haría mentir al sistema. Se tolera lo que el
negocio espera que ocurra, dejando constancia.

| Situación | Tratamiento | Razón |
| --- | --- | --- |
| Columnas faltantes | Detiene | El contrato con la fuente se rompió |
| Nulos en `date`, `store_nbr`, `family` | Detiene | La fila no se puede ubicar |
| Ventas negativas o no numéricas | Detiene | Un modelo las aprendería sin avisar |
| Llaves duplicadas | Detiene | Se contaría la venta dos veces |
| Fechas distintas a la de la entrega | Detiene | Podría introducir datos futuros |
| Tienda desconocida | Detiene | Rompe la relación con `stores` |
| Corrección a un registro inexistente o no anterior | Detiene | No es una corrección válida |
| `oil.csv` no llega | Aviso y forward-fill | Situación esperada |
| Fin de semana o feriado sin cotización | Forward-fill | El mercado no opera |
| `transactions.csv` no llega | Aviso y forward-fill por tienda | Es una señal auxiliar |
| `holidays.csv` o `stores.csv` no llegan | Aviso | Se usa lo último conocido |
| `ventas.csv` no llega | Aviso, **sin imputar** | Ver abajo |

**No contaminar:** toda la validación termina antes de la primera escritura. Por eso
un archivo inválido deja `data/processed/` byte a byte como estaba, y una prueba lo
verifica para cada tipo de error.

**Forward-fill y no otra imputación:** interpolar usaría el valor siguiente, que no se
conoce todavía. La media histórica cambiaría el nivel de la serie.

**Las ventas no se imputan:** son la variable objetivo y la referencia del seguimiento.
Inventarlas fabricaría errores falsos. Quedan como faltantes, que el modelo admite de
forma nativa. Esto se apartó de una lectura literal de "aplicar forward-fill" a todo
archivo que no llegue, a propósito.

**Fines de semana:** el simulador entrega un `oil.csv` sin filas. Así "no hay cotización"
se distingue de "el archivo no llegó", que no se podría separar si faltara en ambos casos.

**Archivos futuros inválidos:** no detienen la corrida, porque no se abren. El rechazo
aplica a lo disponible hasta L.

## 3. Manejo idempotente de correcciones

**Decisión:** `data/processed/` se reconstruye completo en cada corrida desde el
histórico y las entregas hasta L, aplicando las correcciones en orden de llegada.

| Alternativa | Por qué se descartó |
| --- | --- |
| Modificar el consolidado incrementalmente | Depende del orden de ejecución: tras procesar agosto, repetir julio vería la corrección y habría fuga |
| Guardar todas las versiones (bitemporal) | Correcto, pero con más complejidad de la necesaria para este volumen |
| Reescribir el archivo original de la bandeja | La bandeja es de solo lectura y se perdería lo que se conoció en cada fecha |

**Consecuencias:** la misma L produce el mismo resultado, y repetir una fecha pasada
vuelve a lo que se conocía entonces. La fase D de Airflow lo demuestra: tras procesar
el 2 de agosto, repetir el 24 de julio dejó 0 filas corregidas.

**Costo:** cada ingesta relee unos 3 millones de filas, dentro de una corrida completa
que tarda unos 25 segundos. Con este volumen es aceptable. A otra escala se necesitaría
un enfoque bitemporal.

**Publicación:** cada corte se reemplaza dentro de una transacción SQLite, y el parquet
se sustituye de forma atómica dentro de ella. Si algo falla antes del commit, la base
revierte y el parquet anterior sigue intacto. El caso que queda sin cubrir es un fallo
justo entre el reemplazo del parquet y el commit; repetir la corrida lo resuelve.

**Corrección y pedido publicado:** el pedido de un corte pasado no cambia al llegar una
corrección, porque refleja lo que se decidió con la información de entonces. Lo que
cambia es su evaluación: en el corte 2017-07-22, el costo de 3 filas pasó de 15.635,67
a 1.677,28. Sin esto, la evaluación culparía al modelo por un error de registro.

## 4. Modelo cuantílico

**Decisión:** un `HistGradientBoostingRegressor` con `loss='quantile'` por cada nivel
(0,10, 0,50 y 0,90), más tiendas y familias como variables categóricas.

| Motivo | Detalle |
| --- | --- |
| Incertidumbre directa | Cada cuantil se estima sin asumir distribución normal, inadecuada para ventas asimétricas y con ceros |
| Faltantes nativos | Un día sin venta no obliga a imputar |
| Velocidad | Entrena en menos de un minuto con 330.000 filas |

| Alternativa | Por qué se descartó |
| --- | --- |
| Media más desviación normal | La primera versión de `pedido.py` lo hacía; no refleja la asimetría |
| Un modelo por serie (ARIMA, ETS) | 1.782 series sin compartir información |
| LightGBM o redes | Dependencias adicionales sin necesidad demostrada |

**Modelos independientes:** como cada cuantil se entrena por separado, pueden cruzarse.
Se ordenan por fila y se recortan a cero antes de publicar.

**Resultado honesto:** el modelo mejora el costo de negocio, pero está mal calibrado.
El rango p10–p90 cubre cerca del 90 % frente al 80 % nominal, y su pinball en p10 es
peor que el del baseline. Como la razón crítica de 0,75 interpola entre p50 y p90, el
p10 no afecta al pedido, pero sí a cualquier uso del intervalo. La recalibración
conforme sobre validación sería el siguiente paso, y no se hizo para no ajustar
hiperparámetros sobre el mismo periodo que decide la compuerta.

## 5. Esquema temporal de validación

**Decisión:** partición cronológica. La validación son los últimos 28 días disponibles
y el entrenamiento es todo lo anterior, con las mismas filas para ambos modelos.

- **Una división aleatoria se descartó:** mezclaría días futuros en el entrenamiento y
  daría métricas optimistas.
- **28 días y no 7:** cubre cuatro semanas completas y no depende de una semana
  particular. Es más exigente que el uso real, porque evalúa más lejos de lo que se
  pronostica.
- **Validación cruzada temporal descartada:** sería más robusta, pero cuesta varios
  entrenamientos por decisión. Es una mejora natural.
- **Reentrenamiento tras aprobar:** el modelo aprobado se reentrena con todos los datos
  para incorporar el último mes. El modelo publicado no es literalmente el evaluado.
  Se aceptó porque la alternativa publica un modelo con un mes menos de información.

## 6. Compuerta de reemplazo

**Decisión:** el candidato reemplaza al vigente solo si su costo de negocio en validación
es estrictamente menor que el del baseline.

- **Por qué costo y no MAE:** el objetivo es la decisión de compra. En validación el
  candidato tuvo peor RMSE y aun así redujo el costo un 6,9 %.
- **Un empate rechaza:** cambiar de modelo tiene riesgo operativo, así que sin mejora
  demostrada se mantiene lo actual.
- **Si se rechaza y no hay vigente:** se instala el baseline, para que el pipeline siga
  operando.
- **Reproducibilidad:** el reemplazo es atómico, y `version_modelo` es un hash del
  archivo, registrado junto a la versión de código en cada publicación.

**Debilidades conocidas:**

1. El baseline no lleva colchón para la razón crítica, mientras el candidato sí, por lo
   que la comparación no es del todo simétrica.
2. Se compara contra el baseline y no contra el modelo vigente, como se pidió. Un
   candidato peor que el vigente pero mejor que el baseline lo reemplazaría.

## 7. Pedido sugerido

**Decisión:** modelo newsvendor. El nivel óptimo es la razón crítica
`costo_quiebre / (costo_quiebre + costo_merma)`, que con 3,0 y 1,0 da 0,75. El pedido
se obtiene interpolando linealmente esa razón entre los cuantiles de cada fila.

- **Costos fuera del modelo:** la razón crítica se lee de `configs/costos.yaml` en cada
  corrida, y una prueba verifica que cambiar los costos cambia el pedido sin tocar el
  modelo. Si los costos estuvieran dentro del entrenamiento, cada cambio obligaría a
  reentrenar.
- **Por familia:** `costos.yaml` admite valores por familia sobre un defecto, porque
  perecederos y no perecederos no cuestan lo mismo.
- **Interpolación y no un modelo por razón crítica:** entrenar el cuantil 0,75
  directamente sería más preciso, pero acoplaría el modelo a los costos.
- **Sin extrapolación:** fuera de 0,10–0,90 se usa el cuantil extremo. Una razón de 0,99
  pediría lo mismo que 0,90, que es una limitación consciente: los extremos no se
  estimaron y extrapolarlos sería inventar la cola.
- **Costos publicados con cada fila:** el seguimiento evalúa con el costo vigente en la
  decisión, no con uno posterior.

**Limitaciones:** el pedido se calcula por día, sin descontar inventario porque el
dataset no lo incluye. Tampoco considera tiempos de entrega, lotes mínimos ni que lo
no vendido un día sirva al siguiente. En el seguimiento de los cortes de agosto el
pedido cubrió cerca del 84 % de los casos frente al 75 % objetivo, efecto de la mala
calibración.

## 8. Orquestación

- **El DAG solo orquesta:** toda la lógica vive en `src/inventario/`, y por XCom viajan
  solo rutas. Así Airflow y `make` ejecutan el mismo código, y los resultados coinciden
  en diferencia 0,0 entre ambos entornos.
- **`max_active_runs=1`:** todas las corridas comparten `data/processed/`. Dos en
  paralelo se pisarían. Es una limitación aceptada; aislar por carpeta permitiría
  paralelismo.
- **`end_date` en el DAG:** con datos simulados de 2017, activarlo sin ese límite crearía
  una corrida con la fecha actual.
- **Imagen oficial sin modificar:** ya incluye scikit-learn, pandas y pyarrow. Sus
  versiones difieren levemente de `requirements.txt` (pandas 3.0.5 frente a 3.1.0), lo
  que se comprobó que no altera los resultados en esta ejecución.
- **Sin DVC en el DAG:** versionar es una decisión deliberada de quien opera, con
  `make versionar`, no un efecto de cada corrida diaria.
