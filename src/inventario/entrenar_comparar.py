"""Entrena y compara el baseline con el modelo de cuantiles, con compuerta explícita.

Ambos modelos se evalúan sobre las mismas filas de validación, los últimos
dias_validacion días disponibles. El modelo aprobado se reentrena con todo lo
disponible antes de publicarse.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from inventario.config import (
    cargar_costos,
    cargar_params,
    cargar_rutas,
    version_codigo,
)
from inventario.filtros import filtrar_entrenamiento
from inventario.pedido import costo_negocio, costos_por_fila, interpolar_cuantiles
from inventario.preparacion import leer_features

CATEGORICAS = ["store_nbr", "family_cod", "dia_semana", "tipo_cod"]
NUMERICAS = [
    "dia_mes", "cluster", "lag_7", "lag_14", "mm_7", "mm_28", "baseline_mm28",
    "promo_lag_7", "promo_mm_7", "feriado_lag_7", "feriado_anio_anterior",
    "oil_lag_7", "transacciones_lag_7",
]
TIPOS_TIENDA = {tipo: i for i, tipo in enumerate("ABCDE")}


def matriz_x(df: pd.DataFrame, familias: list[str]) -> pd.DataFrame:
    codigos = {familia: i for i, familia in enumerate(familias)}
    x = df[NUMERICAS].astype(float).copy()
    x["store_nbr"] = df["store_nbr"].to_numpy()
    x["dia_semana"] = df["dia_semana"].to_numpy()
    x["family_cod"] = df["family"].map(codigos).astype(float).to_numpy()
    x["tipo_cod"] = df["type"].map(TIPOS_TIENDA).astype(float).to_numpy()
    return x


def entrenar_cuantiles(df: pd.DataFrame, entrenamiento: dict) -> dict:
    familias = sorted(df["family"].unique())
    x, y = matriz_x(df, familias), df["sales"].to_numpy()
    modelos = {}
    for nivel in entrenamiento["cuantiles"]:
        modelo = HistGradientBoostingRegressor(
            loss="quantile", quantile=nivel, categorical_features=CATEGORICAS,
            early_stopping=False, **entrenamiento["hgb"],
        )
        modelos[nivel] = modelo.fit(x, y)
    return {
        "tipo": "cuantiles",
        "niveles": list(entrenamiento["cuantiles"]),
        "familias": familias,
        "modelos": modelos,
        "fecha_corte": df["date"].max().strftime("%Y-%m-%d"),
    }


def artefacto_baseline(entrenamiento: dict) -> dict:
    return {
        "tipo": "baseline",
        "niveles": list(entrenamiento["cuantiles"]),
        "fecha_corte": "0001-01-01",  # no aprende de datos: sirve para cualquier corte
    }


def predecir(artefacto: dict, df: pd.DataFrame) -> np.ndarray:
    """Matriz filas x niveles, sin negativos y con cuantiles ordenados por fila."""
    niveles = artefacto["niveles"]
    if artefacto["tipo"] == "baseline":
        columna = df["baseline_mm28"].to_numpy()
        return np.column_stack([columna] * len(niveles))
    x = matriz_x(df, artefacto["familias"])
    matriz = np.column_stack([artefacto["modelos"][n].predict(x) for n in niveles])
    return np.sort(np.maximum(matriz, 0), axis=1)


def metricas(df: pd.DataFrame, matriz: np.ndarray, niveles: list[float], costos: dict) -> dict:
    real = df["sales"].to_numpy()
    mediana = matriz[:, niveles.index(0.5)]
    unitarios = costos_por_fila(costos, df["family"])
    pedido = interpolar_cuantiles(matriz, niveles, unitarios["razon_critica"].to_numpy())
    resultado = {
        "mae": float(np.abs(real - mediana).mean()),
        "rmse": float(np.sqrt(((real - mediana) ** 2).mean())),
    }
    perdidas = []
    for i, nivel in enumerate(niveles):
        error = real - matriz[:, i]
        perdida = float(np.maximum(nivel * error, (nivel - 1) * error).mean())
        resultado[f"pinball_p{round(nivel * 100)}"] = perdida
        perdidas.append(perdida)
    resultado["pinball_media"] = float(np.mean(perdidas))
    resultado.update(costo_negocio(
        real, pedido, unitarios["costo_quiebre"].to_numpy(), unitarios["costo_merma"].to_numpy()
    ))
    return resultado


def particion_temporal(df: pd.DataFrame, dias_validacion: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Validación idéntica para ambos modelos: solo filas evaluables por los dos."""
    fin = df["date"].max()
    inicio_validacion = fin - pd.Timedelta(days=dias_validacion - 1)
    entrenamiento = df[df["date"] < inicio_validacion]
    validacion = df[(df["date"] >= inicio_validacion) & df["baseline_mm28"].notna()]
    return entrenamiento, validacion


def aplicar_compuerta(
    candidato: dict, costo_candidato: float, costo_baseline: float, ruta: Path, entrenamiento: dict
) -> str:
    """Solo reemplaza al vigente si el candidato mejora el costo del baseline."""
    if costo_candidato < costo_baseline:
        _guardar(candidato, ruta)
        return "reemplazado"
    if not ruta.exists():
        _guardar(artefacto_baseline(entrenamiento), ruta)
        return "rechazado_baseline_vigente"
    return "rechazado"


def _guardar(artefacto: dict, ruta: Path) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_name(ruta.name + ".tmp")
    joblib.dump({**artefacto, "version_codigo": version_codigo()}, temporal)
    os.replace(temporal, ruta)


def version_modelo(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()[:12]


def entrenar_y_comparar(
    logical_date: str, rutas: dict[str, Path], params: dict, costos: dict, promover: bool = True
) -> dict:
    """Con promover=False el resultado va a models/historial/ y no toca el vigente.

    Sirve para reprocesar fechas anteriores al corte del modelo vigente.
    """
    conf = params["entrenamiento"]
    features = leer_features(logical_date, rutas["processed"])
    disponibles = features[~features["es_futuro"] & features["sales"].notna()]
    datos = filtrar_entrenamiento(disponibles, conf, logical_date)

    entrenamiento, validacion = particion_temporal(datos, conf["dias_validacion"])
    niveles = list(conf["cuantiles"])
    candidato = entrenar_cuantiles(entrenamiento, conf)
    filas = {
        "baseline_mm28_dow": metricas(validacion, predecir(artefacto_baseline(conf), validacion), niveles, costos),
        "hgb_cuantiles": metricas(validacion, predecir(candidato, validacion), niveles, costos),
    }

    costo_base = filas["baseline_mm28_dow"]["costo_negocio"]
    costo_ml = filas["hgb_cuantiles"]["costo_negocio"]
    if costo_ml < costo_base:
        candidato = entrenar_cuantiles(datos, conf)
    ruta = rutas["modelos"] / "modelo_vigente.joblib"
    sufijo = ""
    if not promover:
        ruta = rutas["modelos"] / "historial" / f"modelo_{logical_date}.joblib"
        sufijo = f"_{logical_date}"
    decision = aplicar_compuerta(candidato, costo_ml, costo_base, ruta, conf)

    rutas["evidencia"].mkdir(parents=True, exist_ok=True)
    tabla = pd.DataFrame(filas).T.rename_axis("modelo").reset_index()
    tabla.to_csv(rutas["evidencia"] / f"comparacion_modelos{sufijo}.csv", index=False, float_format="%.4f")
    resultado = {
        "logical_date": logical_date,
        "particion": {
            "entrenamiento": [str(entrenamiento["date"].min().date()), str(entrenamiento["date"].max().date())],
            "validacion": [str(validacion["date"].min().date()), str(validacion["date"].max().date())],
            "filas_entrenamiento": len(entrenamiento),
            "filas_validacion": len(validacion),
        },
        "modelos": filas,
        "compuerta": {
            "criterio": "costo_negocio del candidato < costo_negocio del baseline en validación",
            "mejora_relativa": 1 - costo_ml / costo_base,
            "decision": decision,
        },
        "modelo_vigente": version_modelo(ruta),
    }
    (rutas["evidencia"] / f"metrics{sufijo}.json").write_text(
        json.dumps(resultado, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return resultado


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logical-date", required=True, help="AAAA-MM-DD")
    parser.add_argument("--solo-historial", action="store_true")
    args = parser.parse_args()
    params = cargar_params()
    resultado = entrenar_y_comparar(
        args.logical_date, cargar_rutas(params), params, cargar_costos(),
        promover=not args.solo_historial,
    )
    print(json.dumps(resultado, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
