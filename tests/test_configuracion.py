import copy
import json
import subprocess

import pandas as pd
import pytest

from inventario.config import RAIZ, cargar_costos, cargar_params, cargar_yaml
from inventario.filtros import filtrar_entrenamiento
from inventario.pedido import pedido_sugerido, razon_critica


def test_params_definen_horizonte_y_ventana_de_entrenamiento():
    params = cargar_params()
    assert params["pronostico"]["horizonte_dias"] == 7
    assert params["entrenamiento"]["fecha_inicio"] == "2017-01-01"
    assert params["entrenamiento"]["fecha_fin"] == "2017-07-31"


def test_costos_por_defecto_dan_razon_critica_de_075():
    assert razon_critica(cargar_costos(), "PRODUCE") == pytest.approx(0.75)


def test_cambiar_el_yaml_cambia_el_pedido_sin_tocar_codigo(tmp_path):
    costos = cargar_costos()
    base = pedido_sugerido(costos, "PRODUCE", 100, 20)

    modificado = copy.deepcopy(costos)
    modificado["defecto"]["costo_quiebre"] = 9.0
    ruta = tmp_path / "costos.yaml"
    ruta.write_text(json.dumps(modificado))  # JSON es YAML válido

    assert pedido_sugerido(cargar_yaml(ruta), "PRODUCE", 100, 20) > base


def test_costo_por_familia_prevalece_sobre_el_defecto():
    costos = {
        "defecto": {"costo_quiebre": 3.0, "costo_merma": 1.0},
        "familias": {"PRODUCE": {"costo_merma": 3.0}},
    }
    assert razon_critica(costos, "PRODUCE") == pytest.approx(0.5)
    assert razon_critica(costos, "DAIRY") == pytest.approx(0.75)


def test_pedido_descuenta_inventario_y_nunca_es_negativo():
    costos = cargar_costos()
    assert pedido_sugerido(costos, "DAIRY", 100, 0, inventario=30) == 70
    assert pedido_sugerido(costos, "DAIRY", 100, 0, inventario=500) == 0


def test_filtro_nunca_supera_logical_date():
    ventas = pd.DataFrame(
        {
            "date": ["2016-12-31", "2017-01-01", "2017-07-10", "2017-07-20"],
            "store_nbr": ["1", "1", "2", "1"],
            "family": ["A", "A", "A", "B"],
        }
    )
    filtro = {"fecha_inicio": "2017-01-01", "fecha_fin": "2017-07-31", "tiendas": None}

    assert filtrar_entrenamiento(ventas, filtro, "2017-07-15")["date"].tolist() == [
        "2017-01-01",
        "2017-07-10",
    ]
    con_tienda = {**filtro, "tiendas": [2]}
    assert len(filtrar_entrenamiento(ventas, con_tienda, "2017-08-15")) == 1
    con_familia = {**filtro, "familias": ["B"]}
    assert len(filtrar_entrenamiento(ventas, con_familia, "2017-08-15")) == 1


def test_git_no_versiona_datos_ni_modelos():
    salida = subprocess.run(
        ["git", "ls-files"], cwd=RAIZ, capture_output=True, text=True, check=True
    ).stdout.split("\n")
    prohibidos = (".csv", ".parquet", ".pkl", ".joblib")
    assert [ruta for ruta in salida if ruta.endswith(prohibidos)] == []


def test_settings_bloquean_force_push():
    deny = json.loads((RAIZ / ".claude" / "settings.json").read_text())["permissions"]["deny"]
    for regla in ["--force*", "-f*", "+*"]:
        assert f"Bash(git push * {regla})" in deny
