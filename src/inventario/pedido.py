"""Pedido sugerido según los costos de configs/costos.yaml (modelo newsvendor)."""

from statistics import NormalDist


def costos_de_familia(costos: dict, familia: str) -> dict:
    return {**costos["defecto"], **(costos.get("familias") or {}).get(familia, {})}


def razon_critica(costos: dict, familia: str) -> float:
    """Nivel de servicio óptimo: quiebre / (quiebre + merma)."""
    valores = costos_de_familia(costos, familia)
    quiebre, merma = valores["costo_quiebre"], valores["costo_merma"]
    if quiebre <= 0 or merma <= 0:
        raise ValueError(f"Los costos de {familia} deben ser positivos: {valores}")
    return quiebre / (quiebre + merma)


def pedido_sugerido(
    costos: dict, familia: str, media: float, desviacion: float, inventario: float = 0.0
) -> float:
    """Unidades a pedir para una demanda normal(media, desviacion) en el horizonte."""
    objetivo = media
    if desviacion > 0:
        objetivo = NormalDist(media, desviacion).inv_cdf(razon_critica(costos, familia))
    return max(0.0, objetivo - inventario)
