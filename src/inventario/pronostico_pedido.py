"""Pronóstico diario con cuantiles y pedido sugerido para una fecha de corte.

La razón crítica se lee de configs/costos.yaml en cada corrida, de modo que
cambiar los costos cambia el pedido sin reentrenar. La salida es el pedido del
día, porque el dataset no incluye inventario disponible.
"""

import sqlite3
from pathlib import Path

import joblib
import pandas as pd

from inventario.config import (
    cargar_costos,
    cargar_params,
    cargar_rutas,
    escribir_parquet,
    leer_logical_date,
    version_codigo,
)
from inventario.entrenar_comparar import predecir, version_modelo
from inventario.pedido import costos_por_fila, interpolar_cuantiles
from inventario.preparacion import leer_features

ESQUEMA_SQL = """
CREATE TABLE IF NOT EXISTS corridas (
    fecha_corte TEXT PRIMARY KEY,
    tipo_modelo TEXT NOT NULL,
    version_modelo TEXT NOT NULL,
    version_codigo TEXT NOT NULL,
    filas INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS pronosticos_pedidos (
    fecha_corte TEXT NOT NULL,
    date TEXT NOT NULL,
    store_nbr INTEGER NOT NULL,
    family TEXT NOT NULL,
    p10 REAL NOT NULL,
    p50 REAL NOT NULL,
    p90 REAL NOT NULL,
    razon_critica REAL NOT NULL,
    costo_quiebre REAL NOT NULL,
    costo_merma REAL NOT NULL,
    pedido_sugerido REAL NOT NULL,
    version_modelo TEXT NOT NULL,
    version_codigo TEXT NOT NULL,
    PRIMARY KEY (fecha_corte, date, store_nbr, family)
);
"""


def cargar_modelo(ruta: Path, logical_date: str) -> dict:
    if not ruta.exists():
        raise FileNotFoundError(f"No hay modelo vigente en {ruta}; ejecuta el entrenamiento")
    artefacto = joblib.load(ruta)
    if artefacto["fecha_corte"] > logical_date:
        raise RuntimeError(
            f"El modelo vigente usa datos hasta {artefacto['fecha_corte']}, "
            f"posteriores a logical_date {logical_date}"
        )
    return artefacto


def calcular(logical_date: str, rutas: dict[str, Path], params: dict, costos: dict) -> pd.DataFrame:
    ruta_modelo = rutas["modelos"] / "modelo_vigente.joblib"
    artefacto = cargar_modelo(ruta_modelo, logical_date)
    features = leer_features(logical_date, rutas["processed"])
    corte = pd.Timestamp(logical_date)
    fin = corte + pd.Timedelta(days=params["pronostico"]["horizonte_dias"])
    futuro = features[(features["date"] > corte) & (features["date"] <= fin)].reset_index(drop=True)
    if futuro.empty:
        raise RuntimeError(f"Sin filas futuras para {logical_date}")

    matriz = predecir(artefacto, futuro)
    unitarios = costos_por_fila(costos, futuro["family"])
    pedido = interpolar_cuantiles(
        matriz, artefacto["niveles"], unitarios["razon_critica"].to_numpy()
    )
    salida = futuro[["date", "store_nbr", "family"]].copy()
    salida.insert(0, "fecha_corte", corte)
    salida[["p10", "p50", "p90"]] = matriz
    salida = pd.concat([salida, unitarios], axis=1)
    salida["pedido_sugerido"] = pedido
    salida["version_modelo"] = version_modelo(ruta_modelo)
    salida["version_codigo"] = version_codigo()
    salida.attrs["tipo_modelo"] = artefacto["tipo"]
    return salida


def _a_sql(salida: pd.DataFrame) -> pd.DataFrame:
    return salida.assign(
        fecha_corte=salida["fecha_corte"].dt.strftime("%Y-%m-%d"),
        date=salida["date"].dt.strftime("%Y-%m-%d"),
    )[[
        "fecha_corte", "date", "store_nbr", "family", "p10", "p50", "p90", "razon_critica",
        "costo_quiebre", "costo_merma", "pedido_sugerido", "version_modelo", "version_codigo",
    ]]


def publicar(salida: pd.DataFrame, publicados: Path) -> None:
    """Reemplaza la fecha de corte en ambos destinos dentro de una transacción.

    El parquet se sustituye dentro de la transacción SQLite: si algo falla antes
    del commit, la base revierte y el parquet anterior sigue intacto.
    """
    publicados.mkdir(parents=True, exist_ok=True)
    corte = salida["fecha_corte"].iloc[0]
    corte_texto = corte.strftime("%Y-%m-%d")
    ruta_parquet = publicados / "pronosticos_pedidos.parquet"

    tabla = salida
    if ruta_parquet.exists():
        previos = pd.read_parquet(ruta_parquet)
        tabla = pd.concat([previos[previos["fecha_corte"] != corte], salida], ignore_index=True)
    tabla = tabla.sort_values(["fecha_corte", "date", "store_nbr", "family"], ignore_index=True)

    filas = _a_sql(salida)
    con = sqlite3.connect(publicados / "operacion.db")
    try:
        con.executescript(ESQUEMA_SQL)
        with con:
            con.execute("DELETE FROM pronosticos_pedidos WHERE fecha_corte = ?", (corte_texto,))
            con.execute("DELETE FROM corridas WHERE fecha_corte = ?", (corte_texto,))
            con.executemany(
                f"INSERT INTO pronosticos_pedidos VALUES ({','.join('?' * filas.shape[1])})",
                filas.itertuples(index=False, name=None),
            )
            con.execute(
                "INSERT INTO corridas VALUES (?, ?, ?, ?, ?)",
                (corte_texto, salida.attrs["tipo_modelo"], salida["version_modelo"].iloc[0],
                 salida["version_codigo"].iloc[0], len(salida)),
            )
            escribir_parquet(tabla, ruta_parquet)
    finally:
        con.close()


def pronosticar(logical_date: str, rutas: dict[str, Path], params: dict, costos: dict) -> pd.DataFrame:
    salida = calcular(logical_date, rutas, params, costos)
    publicar(salida, rutas["publicados"])
    return salida


def main() -> None:
    logical_date = leer_logical_date(__doc__)
    params = cargar_params()
    salida = pronosticar(logical_date, cargar_rutas(params), params, cargar_costos())
    print(
        f"{logical_date}: {len(salida)} filas publicadas, "
        f"pedido total {salida['pedido_sugerido'].sum():,.0f}, "
        f"modelo {salida['version_modelo'].iloc[0]}"
    )


if __name__ == "__main__":
    main()
