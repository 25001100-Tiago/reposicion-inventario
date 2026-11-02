"""Recalcula un pronóstico pasado y lo compara exactamente con el publicado.

Se ejecuta dentro de un checkout de la versión a reproducir, con PYTHONPATH=src,
para usar su código, sus datos y su modelo. No escribe en data/publicados/. La
comparación exige igualdad exacta de bits, sin tolerancia.

Incluye un control negativo: recalcula con otro costo y exige que la comparación
detecte la diferencia, para probar que el comparador puede fallar.
"""

import argparse
import hashlib
import sys

import numpy as np
import pandas as pd

from inventario.config import cargar_costos, cargar_params, cargar_rutas
from inventario.ingesta_validacion import ingerir
from inventario.preparacion import preparar
from inventario.pronostico_pedido import calcular

LLAVES = ["fecha_corte", "date", "store_nbr", "family"]
VALORES = ["p10", "p50", "p90", "razon_critica", "costo_quiebre", "costo_merma", "pedido_sugerido"]


def ordenar(df: pd.DataFrame) -> pd.DataFrame:
    return df.sort_values(LLAVES, ignore_index=True)


def huella(df: pd.DataFrame) -> str:
    return hashlib.sha256(df[VALORES].to_numpy(dtype="float64").tobytes()).hexdigest()


def filas_distintas(a: pd.DataFrame, b: pd.DataFrame) -> int:
    distintas = a[VALORES].to_numpy(dtype="float64") != b[VALORES].to_numpy(dtype="float64")
    return int(distintas.any(axis=1).sum())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logical-date", required=True)
    corte = parser.parse_args().logical_date

    params = cargar_params()
    rutas = cargar_rutas(params)
    costos = cargar_costos()

    todo = pd.read_parquet(rutas["publicados"] / "pronosticos_pedidos.parquet")
    publicado = ordenar(todo[todo["fecha_corte"] == pd.Timestamp(corte)])
    estado = ingerir(corte, rutas)
    preparar(corte, rutas, params)
    recalculado = ordenar(calcular(corte, rutas, params, costos))

    llaves_iguales = publicado[LLAVES].equals(recalculado[LLAVES])
    distintas = filas_distintas(publicado, recalculado) if llaves_iguales else len(publicado)
    modelo_igual = set(publicado["version_modelo"]) == set(recalculado["version_modelo"])

    alterado = {**costos, "defecto": {**costos["defecto"], "costo_quiebre": 9.0}}
    control = filas_distintas(publicado, ordenar(calcular(corte, rutas, params, alterado)))

    print(f"fecha de corte:              {corte}")
    print(f"filas corregidas conocidas:  {estado['filas_corregidas']}")
    print(f"filas publicadas:            {len(publicado)}")
    print(f"filas recalculadas:          {len(recalculado)}")
    print(f"modelo publicado:            {publicado['version_modelo'].iloc[0]}")
    print(f"modelo recalculado:          {recalculado['version_modelo'].iloc[0]}")
    print(f"código publicado:            {publicado['version_codigo'].iloc[0]}")
    print(f"sha256 publicado:            {huella(publicado)}")
    print(f"sha256 recalculado:          {huella(recalculado)}")
    print(f"filas distintas:             {distintas}")
    print(f"control negativo (costo 9):  {control} filas distintas")

    if not (llaves_iguales and modelo_igual and distintas == 0 and control > 0):
        print("RESULTADO: NO REPRODUCIDO")
        sys.exit(1)
    print(f"RESULTADO: REPRODUCIDO AL 100 % ({len(publicado)}/{len(publicado)} filas idénticas)")


if __name__ == "__main__":
    main()
