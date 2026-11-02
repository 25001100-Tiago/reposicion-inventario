"""Revisión estática: Airflow no se instala en CI."""

import ast

from inventario.config import RAIZ, cargar_yaml

DAG = RAIZ / "dags" / "reposicion_inventario_dag.py"
COMPOSE = RAIZ / "airflow" / "docker-compose.yaml"


def test_dag_tiene_las_cuatro_tareas_y_solo_orquesta():
    arbol = ast.parse(DAG.read_text(encoding="utf-8"))
    tareas = [
        nodo.name for nodo in ast.walk(arbol)
        if isinstance(nodo, ast.FunctionDef)
        and any(getattr(d, "id", "") == "task" for d in nodo.decorator_list)
    ]
    assert tareas == [
        "ingestar_validar", "preparar_datos",
        "generar_pronostico_y_pedido", "evaluar_seguimiento_desempeno",
    ]
    importados = {
        nombre.name.split(".")[0] for nodo in ast.walk(arbol)
        if isinstance(nodo, ast.Import) for nombre in nodo.names
    }
    assert not importados & {"pandas", "numpy", "sklearn", "sqlite3"}


def test_dag_serializa_corridas_y_no_recupera_el_pasado():
    texto = DAG.read_text(encoding="utf-8")
    assert "catchup=False" in texto
    assert "max_active_runs=1" in texto
    assert "end_date=" in texto


def test_compose_monta_la_bandeja_solo_lectura_fuera_de_opt_airflow():
    compose = cargar_yaml(COMPOSE)
    comun = compose["x-airflow-comun"]
    assert comun["image"] == "apache/airflow:3.3.2-python3.12"
    assert compose["services"]["postgres"]["image"] == "postgres:16"
    assert comun["environment"]["AIRFLOW__CORE__EXECUTOR"] == "LocalExecutor"
    assert comun["environment"]["AIRFLOW__CORE__LOAD_EXAMPLES"] == "false"
    assert "..:/opt/inventario/proyecto" in comun["volumes"]
    assert "../data/bandeja:/opt/inventario/proyecto/data/bandeja:ro" in comun["volumes"]


def test_compose_no_contiene_secretos():
    texto = COMPOSE.read_text(encoding="utf-8")
    assert "${POSTGRES_PASSWORD:?}" in texto and "${AIRFLOW_JWT_SECRET:?}" in texto
