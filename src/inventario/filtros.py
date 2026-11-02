"""Filtros de entrenamiento con cero fuga temporal."""

import pandas as pd


def filtrar_entrenamiento(
    ventas: pd.DataFrame, entrenamiento: dict, logical_date: str
) -> pd.DataFrame:
    """Aplica el filtro de params.yaml, recortado siempre a logical_date.

    Las fechas son texto ISO (AAAA-MM-DD), que se ordena igual que el calendario.
    """
    fin = min(str(entrenamiento["fecha_fin"]), str(logical_date))
    fechas = ventas["date"].astype(str)
    mascara = fechas.between(str(entrenamiento["fecha_inicio"]), fin)

    if entrenamiento.get("tiendas") is not None:
        tiendas = {str(tienda) for tienda in entrenamiento["tiendas"]}
        mascara &= ventas["store_nbr"].astype(str).isin(tiendas)
    if entrenamiento.get("familias") is not None:
        mascara &= ventas["family"].isin(entrenamiento["familias"])

    return ventas[mascara]
