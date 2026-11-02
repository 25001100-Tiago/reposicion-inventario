"""Pedido sugerido a partir de cuantiles y de configs/costos.yaml (newsvendor)."""

import numpy as np
import pandas as pd


def costos_de_familia(costos: dict, familia: str) -> dict:
    return {**costos["defecto"], **(costos.get("familias") or {}).get(familia, {})}


def razon_critica(costos: dict, familia: str) -> float:
    """Nivel de servicio óptimo: quiebre / (quiebre + merma)."""
    valores = costos_de_familia(costos, familia)
    quiebre, merma = valores["costo_quiebre"], valores["costo_merma"]
    if quiebre <= 0 or merma <= 0:
        raise ValueError(f"Los costos de {familia} deben ser positivos: {valores}")
    return quiebre / (quiebre + merma)


def costos_por_fila(costos: dict, familias: pd.Series) -> pd.DataFrame:
    """Costos y razón crítica de cada fila, leídos del diccionario de costos."""
    tabla = {
        familia: (*(costos_de_familia(costos, familia)[c] for c in ("costo_quiebre", "costo_merma")),
                  razon_critica(costos, familia))
        for familia in familias.unique()
    }
    return pd.DataFrame(
        [tabla[f] for f in familias],
        columns=["costo_quiebre", "costo_merma", "razon_critica"],
        index=familias.index,
    )


def interpolar_cuantiles(
    matriz: np.ndarray, niveles: list[float], razones: np.ndarray
) -> np.ndarray:
    """Interpola linealmente cada fila entre sus cuantiles.

    Fuera del rango de niveles se usa el cuantil extremo: no se extrapola.
    """
    niveles = np.asarray(niveles)
    razones = np.clip(razones, niveles[0], niveles[-1])
    tramo = np.clip(np.searchsorted(niveles, razones, side="right") - 1, 0, len(niveles) - 2)
    peso = (razones - niveles[tramo]) / (niveles[tramo + 1] - niveles[tramo])
    filas = np.arange(len(matriz))
    return matriz[filas, tramo] * (1 - peso) + matriz[filas, tramo + 1] * peso


def costo_negocio(
    real: np.ndarray, pedido: np.ndarray, quiebre: np.ndarray, merma: np.ndarray
) -> dict:
    faltantes = np.maximum(real - pedido, 0)
    sobrantes = np.maximum(pedido - real, 0)
    return {
        "faltantes": float(faltantes.sum()),
        "sobrantes": float(sobrantes.sum()),
        "costo_quiebre": float((quiebre * faltantes).sum()),
        "costo_merma": float((merma * sobrantes).sum()),
        "costo_negocio": float((quiebre * faltantes + merma * sobrantes).sum()),
    }
