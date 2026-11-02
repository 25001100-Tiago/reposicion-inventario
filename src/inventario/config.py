"""Carga de configuración desde configs/."""

from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[2]


def cargar_yaml(ruta: str | Path) -> dict:
    with open(ruta, encoding="utf-8") as archivo:
        return yaml.safe_load(archivo)


def cargar_params(ruta: str | Path = RAIZ / "configs" / "params.yaml") -> dict:
    return cargar_yaml(ruta)


def cargar_costos(ruta: str | Path = RAIZ / "configs" / "costos.yaml") -> dict:
    return cargar_yaml(ruta)
