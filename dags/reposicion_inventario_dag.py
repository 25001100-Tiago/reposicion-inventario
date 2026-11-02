"""Orquesta el pipeline de src/inventario/; aquí no vive lógica de negocio.

Todas las corridas comparten data/processed, por eso max_active_runs=1. El
end_date acota el DAG a las fechas simuladas: sin él, activarlo crearía una
corrida con la fecha actual. Por XCom solo viajan rutas.
"""

from datetime import datetime, timezone

from airflow.sdk import dag, task


def _fecha(logical_date) -> str:
    return logical_date.strftime("%Y-%m-%d")


def _entorno() -> tuple[dict, dict]:
    from inventario.config import cargar_params, cargar_rutas

    params = cargar_params()
    return params, cargar_rutas(params)


@dag(
    dag_id="reposicion_inventario",
    schedule="@daily",
    start_date=datetime(2017, 7, 16, tzinfo=timezone.utc),
    end_date=datetime(2017, 8, 15, tzinfo=timezone.utc),
    catchup=False,
    max_active_runs=1,
    tags=["inventario"],
)
def reposicion_inventario():
    @task
    def ingestar_validar(logical_date=None) -> str:
        from inventario.ingesta_validacion import ingerir

        _, rutas = _entorno()
        ingerir(_fecha(logical_date), rutas)
        return str(rutas["processed"] / "ventas_consolidadas.parquet")

    @task
    def preparar_datos(ruta_ventas: str, logical_date=None) -> str:
        from inventario.preparacion import preparar

        params, rutas = _entorno()
        preparar(_fecha(logical_date), rutas, params)
        return str(rutas["processed"] / "features.parquet")

    @task
    def generar_pronostico_y_pedido(ruta_features: str, logical_date=None) -> str:
        from inventario.config import cargar_costos
        from inventario.pronostico_pedido import pronosticar

        params, rutas = _entorno()
        pronosticar(_fecha(logical_date), rutas, params, cargar_costos())
        return str(rutas["publicados"] / "pronosticos_pedidos.parquet")

    @task
    def evaluar_seguimiento_desempeno(ruta_pronosticos: str, logical_date=None) -> str:
        from inventario.seguimiento import generar

        _, rutas = _entorno()
        generar(_fecha(logical_date), rutas)
        return str(rutas["publicados"] / "seguimiento_desempeno.csv")

    evaluar_seguimiento_desempeno(generar_pronostico_y_pedido(preparar_datos(ingestar_validar())))


reposicion_inventario()
