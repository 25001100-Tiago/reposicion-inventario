"""Simula el sistema de origen a partir de los CSV de Kaggle.

Genera la carga histórica (data/hist/) y la bandeja externa diaria
(data/bandeja/<fecha>/). Todo se lee como texto para conservar los valores tal
como vienen en la fuente.
"""

import argparse
import json
import shutil
import sys
from datetime import date
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from inventario.config import cargar_params  # noqa: E402

# nombre publicado -> archivo de Kaggle
FUENTES_FECHADAS = {
    "ventas": "train.csv",
    "oil": "oil.csv",
    "transactions": "transactions.csv",
    "holidays": "holidays_events.csv",
}
CLAVES = {
    "ventas": ["date", "store_nbr", "family"],
    "oil": ["date"],
    "transactions": ["date", "store_nbr"],
    "holidays": ["date", "locale_name", "description"],
}
CORRECCION = "ventas_correccion.csv"


def leer_csv(ruta: Path) -> pd.DataFrame:
    return pd.read_csv(ruta, dtype=str, keep_default_na=False)


def escribir_csv(df: pd.DataFrame, ruta: Path) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(ruta, index=False, lineterminator="\n")


def dias(inicio: str, fin: str) -> list[str]:
    return [d.strftime("%Y-%m-%d") for d in pd.date_range(inicio, fin)]


def validar_simulacion(sim: dict, oil: pd.DataFrame) -> None:
    corr = sim["correccion_ventas"]
    if not sim["fecha_corte_hist"] < sim["bandeja_inicio"] <= sim["bandeja_fin"]:
        raise ValueError("Las fechas de corte y de bandeja no son consecutivas")
    if not sim["bandeja_inicio"] <= sim["dia_sin_oil"] <= sim["bandeja_fin"]:
        raise ValueError("dia_sin_oil está fuera de la bandeja")
    if sim["dia_sin_oil"] not in set(oil.loc[oil["dcoilwtico"] != "", "date"]):
        raise ValueError("dia_sin_oil debe ser un día con cotización real")
    if not sim["bandeja_inicio"] <= corr["fecha_ventas"] < corr["fecha_entrega"] <= sim["bandeja_fin"]:
        raise ValueError("La corrección debe llegar después de la fecha que corrige")


def aplicar_error_de_ventas(ventas: pd.DataFrame, corr: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Devuelve (entrega con error, corrección con los valores reales)."""
    filas = (
        (ventas["date"] == corr["fecha_ventas"])
        & (ventas["store_nbr"] == str(corr["tienda"]))
        & ventas["family"].isin(corr["familias"])
        & (ventas["sales"].astype(float) > 0)
    )
    if not filas.any():
        raise ValueError("La corrección de ventas no afecta ninguna fila")

    correccion = ventas[filas].copy()
    correccion["fecha_correccion"] = corr["fecha_entrega"]
    entrega = ventas.copy()
    entrega.loc[filas, "sales"] = (
        (ventas.loc[filas, "sales"].astype(float) * corr["factor_entrega_original"])
        .round(3)
        .astype(str)
    )
    return entrega, correccion


def simular(raw: Path, hist: Path, bandeja: Path, sim: dict) -> dict:
    """Escribe hist y bandeja, y devuelve un resumen de lo generado."""
    corte = sim["fecha_corte_hist"]
    fechas = dias(sim["bandeja_inicio"], sim["bandeja_fin"])
    fuentes = {nombre: leer_csv(raw / archivo) for nombre, archivo in FUENTES_FECHADAS.items()}
    stores = leer_csv(raw / "stores.csv")
    validar_simulacion(sim, fuentes["oil"])

    escribir_csv(stores, hist / "stores.csv")
    resumen = {"hist": {"stores": len(stores)}, "bandeja": {}}
    for nombre, df in fuentes.items():
        historico = df[df["date"] <= corte]
        escribir_csv(historico, hist / f"{nombre}.csv")
        resumen["hist"][nombre] = len(historico)

    corr = sim["correccion_ventas"]
    fuentes["ventas"], correccion = aplicar_error_de_ventas(fuentes["ventas"], corr)

    for fecha in fechas:
        carpeta = bandeja / fecha
        carpeta.mkdir(parents=True)
        escribir_csv(stores, carpeta / "stores.csv")
        for nombre, df in fuentes.items():
            if nombre == "oil" and fecha == sim["dia_sin_oil"]:
                continue
            escribir_csv(df[df["date"] == fecha], carpeta / f"{nombre}.csv")
    escribir_csv(correccion, bandeja / corr["fecha_entrega"] / CORRECCION)

    resumen["bandeja"] = {
        "inicio": fechas[0],
        "fin": fechas[-1],
        "dias": len(fechas),
        "archivos": sum(1 for r in bandeja.rglob("*.csv")),
        "dia_sin_oil": sim["dia_sin_oil"],
        "dias_oil_sin_filas": [
            f for f in fechas if date.fromisoformat(f).weekday() >= 5
        ],
        "correccion_ventas": {
            "fecha_ventas": corr["fecha_ventas"],
            "fecha_entrega": corr["fecha_entrega"],
            "filas": len(correccion),
        },
    }
    return resumen


def verificar(raw: Path, hist: Path, bandeja: Path, sim: dict) -> None:
    """Comprueba que hist + bandeja + correcciones reconstruyen la fuente sin fugas."""
    corte, fin = sim["fecha_corte_hist"], sim["bandeja_fin"]
    corr = sim["correccion_ventas"]

    for nombre, archivo in FUENTES_FECHADAS.items():
        historico = leer_csv(hist / f"{nombre}.csv")
        if (historico["date"] > corte).any():
            raise AssertionError(f"{nombre}: hist contiene fechas posteriores al corte")

        partes = [historico]
        for ruta in sorted(bandeja.glob(f"*/{nombre}.csv")):
            df = leer_csv(ruta)
            if (df["date"] != ruta.parent.name).any():
                raise AssertionError(f"{ruta}: contiene fechas ajenas a su entrega")
            partes.append(df)
        reconstruido = pd.concat(partes, ignore_index=True)

        if nombre == "ventas":
            nuevo = leer_csv(bandeja / corr["fecha_entrega"] / CORRECCION)
            if (nuevo["date"] >= nuevo["fecha_correccion"]).any():
                raise AssertionError("La corrección no es posterior a la fecha corregida")
            nuevo = nuevo.drop(columns="fecha_correccion").set_index(CLAVES[nombre])
            reconstruido = reconstruido.set_index(CLAVES[nombre])
            reconstruido.update(nuevo)
            reconstruido = reconstruido.reset_index()

        fuente = leer_csv(raw / archivo)
        esperado = fuente[fuente["date"] <= fin]
        if nombre == "oil":
            esperado = esperado[esperado["date"] != sim["dia_sin_oil"]]
        columnas = list(esperado.columns)
        a = reconstruido[columnas].sort_values(columnas).reset_index(drop=True)
        b = esperado.sort_values(columnas).reset_index(drop=True)
        if not a.equals(b):
            raise AssertionError(f"{nombre}: la reconstrucción no coincide con la fuente")


def preparar_destino(ruta: Path, sobrescribir: bool) -> None:
    if ruta.exists() and any(ruta.iterdir()):
        if not sobrescribir:
            raise SystemExit(f"{ruta} no está vacío; usa --sobrescribir para regenerarlo")
        shutil.rmtree(ruta)
    ruta.mkdir(parents=True, exist_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sobrescribir", action="store_true")
    args = parser.parse_args()

    params = cargar_params()
    rutas = {clave: RAIZ / valor for clave, valor in params["rutas"].items()}
    sim = params["simulacion"]

    preparar_destino(rutas["hist"], args.sobrescribir)
    preparar_destino(rutas["bandeja"], args.sobrescribir)
    resumen = simular(rutas["kaggle_raw"], rutas["hist"], rutas["bandeja"], sim)
    verificar(rutas["kaggle_raw"], rutas["hist"], rutas["bandeja"], sim)

    rutas["evidencia"].mkdir(parents=True, exist_ok=True)
    salida = rutas["evidencia"] / "simulacion_resumen.json"
    salida.write_text(json.dumps(resumen, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(resumen, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
