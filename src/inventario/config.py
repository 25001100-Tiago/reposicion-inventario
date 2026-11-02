"""Configuración y utilidades compartidas por los módulos del pipeline."""

import argparse
import os
import subprocess
from pathlib import Path

import pandas as pd
import yaml

RAIZ = Path(__file__).resolve().parents[2]


def cargar_yaml(ruta: str | Path) -> dict:
    with open(ruta, encoding="utf-8") as archivo:
        return yaml.safe_load(archivo)


def cargar_params(ruta: str | Path = RAIZ / "configs" / "params.yaml") -> dict:
    return cargar_yaml(ruta)


def cargar_costos(ruta: str | Path = RAIZ / "configs" / "costos.yaml") -> dict:
    return cargar_yaml(ruta)


def cargar_rutas(params: dict, raiz: Path = RAIZ) -> dict[str, Path]:
    return {clave: Path(raiz) / valor for clave, valor in params["rutas"].items()}


def version_codigo() -> str:
    """Commit actual, con -dirty si hay cambios sin confirmar en código o configuración.

    La evidencia queda fuera a propósito: el pipeline la reescribe y marcaría
    como distinta una corrida repetida.
    """

    def git(*argumentos: str) -> str:
        return subprocess.run(
            ["git", *argumentos], cwd=RAIZ, capture_output=True, text=True, check=True
        ).stdout.strip()

    try:
        cambios = git("status", "--porcelain", "--", "src", "configs", "dags", "scripts")
        return git("rev-parse", "--short=12", "HEAD") + ("-dirty" if cambios else "")
    except (OSError, subprocess.CalledProcessError):
        return "desconocida"


def escribir_parquet(df: pd.DataFrame, ruta: Path) -> None:
    """Escritura atómica: el archivo final nunca queda a medias."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_name(ruta.name + ".tmp")
    df.to_parquet(temporal, index=False)
    os.replace(temporal, ruta)


def leer_logical_date(descripcion: str) -> str:
    parser = argparse.ArgumentParser(description=descripcion)
    parser.add_argument("--logical-date", required=True, help="AAAA-MM-DD")
    fecha = parser.parse_args().logical_date
    return pd.Timestamp(fecha).strftime("%Y-%m-%d")
