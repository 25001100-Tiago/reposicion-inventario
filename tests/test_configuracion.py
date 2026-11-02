import json
import subprocess

import numpy as np
import pandas as pd
import pytest

from inventario.config import RAIZ, cargar_costos, cargar_params
from inventario.filtros import filtrar_entrenamiento
from inventario.pedido import costo_negocio, interpolar_cuantiles, razon_critica


def test_params_definen_horizonte_y_ventana_de_entrenamiento():
    params = cargar_params()
    assert params["pronostico"]["horizonte_dias"] == 7
    assert params["entrenamiento"]["fecha_inicio"] == "2017-01-01"
    assert params["entrenamiento"]["fecha_fin"] == "2017-07-31"


def test_costos_por_defecto_dan_razon_critica_de_075():
    assert razon_critica(cargar_costos(), "PRODUCE") == pytest.approx(0.75)


def test_interpolacion_respeta_la_razon_critica():
    matriz = np.array([[10.0, 20.0, 40.0]])
    niveles = [0.1, 0.5, 0.9]
    assert interpolar_cuantiles(matriz, niveles, np.array([0.5])) == pytest.approx(20)
    assert interpolar_cuantiles(matriz, niveles, np.array([0.7])) == pytest.approx(30)
    assert interpolar_cuantiles(matriz, niveles, np.array([0.3])) == pytest.approx(15)


def test_interpolacion_no_extrapola_fuera_de_los_cuantiles():
    matriz = np.array([[10.0, 20.0, 40.0]])
    niveles = [0.1, 0.5, 0.9]
    assert interpolar_cuantiles(matriz, niveles, np.array([0.99])) == pytest.approx(40)
    assert interpolar_cuantiles(matriz, niveles, np.array([0.01])) == pytest.approx(10)


def test_costo_negocio_pondera_faltantes_y_sobrantes():
    resultado = costo_negocio(
        np.array([10.0, 10.0]), np.array([8.0, 15.0]), np.array([3.0, 3.0]), np.array([1.0, 1.0])
    )
    assert resultado["costo_negocio"] == pytest.approx(3 * 2 + 1 * 5)


def test_costo_por_familia_prevalece_sobre_el_defecto():
    costos = {
        "defecto": {"costo_quiebre": 3.0, "costo_merma": 1.0},
        "familias": {"PRODUCE": {"costo_merma": 3.0}},
    }
    assert razon_critica(costos, "PRODUCE") == pytest.approx(0.5)
    assert razon_critica(costos, "DAIRY") == pytest.approx(0.75)


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
    prohibidos = (".csv", ".parquet", ".pkl", ".joblib", ".db")
    versionados = [ruta for ruta in salida if not ruta.startswith("docs/evidencia/")]
    assert [ruta for ruta in versionados if ruta.endswith(prohibidos)] == []


def test_settings_bloquean_force_push():
    deny = json.loads((RAIZ / ".claude" / "settings.json").read_text())["permissions"]["deny"]
    for regla in ["--force*", "-f*", "+*"]:
        assert f"Bash(git push * {regla})" in deny
