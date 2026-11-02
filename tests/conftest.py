"""Entorno sintético y pequeño con la misma estructura de datos del proyecto."""

import numpy as np
import pandas as pd
import pytest

FIN_HIST = "2017-03-15"
FIN_BANDEJA = "2017-03-31"
DIA_SIN_OIL = "2017-03-22"
FECHA_CORREGIDA = "2017-03-20"
FECHA_CORRECCION = "2017-03-25"
TIENDAS = [1, 2]
FAMILIAS = ["DAIRY", "PRODUCE"]

COSTOS = {"defecto": {"costo_quiebre": 3.0, "costo_merma": 1.0}, "familias": {}}


def _ventas(fechas: pd.DatetimeIndex) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    filas = []
    for fecha in fechas:
        for tienda in TIENDAS:
            for familia in FAMILIAS:
                base = 100 * tienda + (50 if familia == "PRODUCE" else 0) + 30 * fecha.day_of_week
                filas.append({
                    "id": len(filas),
                    "date": fecha.strftime("%Y-%m-%d"),
                    "store_nbr": tienda,
                    "family": familia,
                    "sales": round(float(base + rng.normal(0, 15)), 3),
                    "onpromotion": int(fecha.day % 3 == 0),
                })
    return pd.DataFrame(filas)


def _escribir(df: pd.DataFrame, ruta) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(ruta, index=False)


def _feriados() -> pd.DataFrame:
    return pd.DataFrame([
        {"date": "2016-03-28", "type": "Holiday", "locale": "National", "locale_name": "Ecuador",
         "description": "Anterior", "transferred": "False"},
        {"date": "2017-02-27", "type": "Holiday", "locale": "Local", "locale_name": "Quito",
         "description": "Carnaval", "transferred": "False"},
    ])


def construir_datos(raiz) -> dict:
    fechas = pd.date_range("2017-01-01", FIN_BANDEJA)
    ventas = _ventas(fechas)
    habiles = fechas[fechas.day_of_week < 5]
    oil = pd.DataFrame({
        "date": habiles.strftime("%Y-%m-%d"),
        "dcoilwtico": np.round(50 + np.arange(len(habiles)) * 0.1, 2),
    })
    transacciones = pd.DataFrame(
        [{"date": f.strftime("%Y-%m-%d"), "store_nbr": t, "transactions": 500 + t}
         for f in fechas for t in TIENDAS]
    )
    tiendas = pd.DataFrame([
        {"store_nbr": 1, "city": "Quito", "state": "Pichincha", "type": "A", "cluster": 1},
        {"store_nbr": 2, "city": "Cuenca", "state": "Azuay", "type": "B", "cluster": 2},
    ])
    feriados = _feriados()
    hist, bandeja = raiz / "data" / "hist", raiz / "data" / "bandeja"

    _escribir(ventas[ventas["date"] <= FIN_HIST], hist / "ventas.csv")
    _escribir(oil[oil["date"] <= FIN_HIST], hist / "oil.csv")
    _escribir(transacciones[transacciones["date"] <= FIN_HIST], hist / "transactions.csv")
    _escribir(feriados, hist / "holidays.csv")
    _escribir(tiendas, hist / "stores.csv")

    entrega = ventas.copy()
    filtro = (entrega["date"] == FECHA_CORREGIDA) & (entrega["store_nbr"] == 1)
    correccion = entrega[filtro].assign(fecha_correccion=FECHA_CORRECCION)
    entrega.loc[filtro, "sales"] = entrega.loc[filtro, "sales"] * 0.5

    for fecha in pd.date_range("2017-03-16", FIN_BANDEJA).strftime("%Y-%m-%d"):
        carpeta = bandeja / fecha
        _escribir(entrega[entrega["date"] == fecha], carpeta / "ventas.csv")
        _escribir(transacciones[transacciones["date"] == fecha], carpeta / "transactions.csv")
        _escribir(feriados[feriados["date"] == fecha], carpeta / "holidays.csv")
        _escribir(tiendas, carpeta / "stores.csv")
        if fecha != DIA_SIN_OIL:
            _escribir(oil[oil["date"] == fecha], carpeta / "oil.csv")
    _escribir(correccion, bandeja / FECHA_CORRECCION / "ventas_correccion.csv")
    return {"ventas_reales": ventas}


@pytest.fixture
def entorno(tmp_path):
    datos = construir_datos(tmp_path)
    rutas = {
        "hist": tmp_path / "data" / "hist",
        "bandeja": tmp_path / "data" / "bandeja",
        "processed": tmp_path / "data" / "processed",
        "publicados": tmp_path / "data" / "publicados",
        "modelos": tmp_path / "models",
        "evidencia": tmp_path / "docs" / "evidencia",
    }
    rutas["processed"].mkdir(parents=True)
    params = {
        "pronostico": {"horizonte_dias": 7},
        "entrenamiento": {
            "fecha_inicio": "2017-01-01",
            "fecha_fin": "2017-03-31",
            "tiendas": None,
            "familias": None,
            "dias_validacion": 7,
            "cuantiles": [0.10, 0.50, 0.90],
            "hgb": {"max_iter": 30, "learning_rate": 0.2, "min_samples_leaf": 5, "random_state": 42},
        },
    }
    return {"rutas": rutas, "params": params, "costos": COSTOS, **datos}
