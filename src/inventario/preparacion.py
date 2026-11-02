"""Features temporales con datos estrictamente <= logical_date.

Cada fila describe un día objetivo t. Todo lo observado se toma desde t-7 hacia
atrás, así la misma fila es válida para cualquier horizonte de 1 a 7 días: para
t = logical_date + h, t-7 nunca supera logical_date. Promociones, petróleo y
transacciones del día t no se conocen al pronosticar, por lo que también van
rezagadas. El calendario futuro de feriados no se entrega; se usan los feriados
de hace una semana y los de la misma fecha del año anterior.
"""

from pathlib import Path

import pandas as pd

from inventario.config import cargar_params, cargar_rutas, escribir_parquet, leer_logical_date
from inventario.ingesta_validacion import exigir_ingesta

REZAGO_MINIMO = 7
HISTORIA_NECESARIA = REZAGO_MINIMO + 28
LLAVES = ["store_nbr", "family"]
TIPOS_NO_FERIADO = {"Work Day", "Event"}


def dias_feriado(feriados: pd.DataFrame, tiendas: pd.DataFrame) -> pd.DataFrame:
    """Pares (date, store_nbr) con feriado efectivo según su alcance geográfico."""
    vigentes = feriados[~feriados["transferred"] & ~feriados["type"].isin(TIPOS_NO_FERIADO)]
    pares = []
    for alcance, columna in [("Local", "city"), ("Regional", "state")]:
        del_alcance = vigentes[vigentes["locale"] == alcance]
        pares.append(del_alcance.merge(
            tiendas, left_on="locale_name", right_on=columna, suffixes=("", "_tienda")
        )[["date", "store_nbr"]])
    nacionales = vigentes.loc[vigentes["locale"] == "National", ["date"]]
    pares.append(nacionales.merge(tiendas[["store_nbr"]], how="cross"))
    return pd.concat(pares, ignore_index=True).drop_duplicates()


def _marcar(base: pd.DataFrame, pares: pd.DataFrame, columna: str) -> pd.DataFrame:
    marcados = pares.assign(**{columna: 1.0}).drop_duplicates(["date", "store_nbr"])
    resultado = base.merge(marcados, on=["date", "store_nbr"], how="left")
    resultado[columna] = resultado[columna].fillna(0.0)
    return resultado


def construir_features(
    ventas: pd.DataFrame,
    oil: pd.DataFrame,
    transacciones: pd.DataFrame,
    feriados: pd.DataFrame,
    tiendas: pd.DataFrame,
    logical_date: str,
    horizonte: int,
    fecha_inicio: str,
) -> pd.DataFrame:
    if horizonte > REZAGO_MINIMO:
        raise ValueError(f"Horizonte {horizonte} mayor que el rezago mínimo {REZAGO_MINIMO}")

    corte = pd.Timestamp(logical_date)
    inicio = min(pd.Timestamp(fecha_inicio), corte)
    desde = inicio - pd.Timedelta(days=HISTORIA_NECESARIA)
    # Primera defensa contra la fuga: nada posterior al corte entra al cálculo.
    ventas = ventas[ventas["date"].between(desde, corte)]
    oil = oil[oil["date"] <= corte]
    transacciones = transacciones[transacciones["date"] <= corte]
    feriados = feriados[feriados["date"] <= corte]

    fechas = pd.date_range(desde, corte + pd.Timedelta(days=horizonte), name="date")
    combinaciones = ventas[LLAVES].drop_duplicates()
    malla = combinaciones.merge(pd.DataFrame({"date": fechas}), how="cross")
    df = malla.merge(ventas[[*LLAVES, "date", "sales", "onpromotion"]], on=[*LLAVES, "date"], how="left")
    df = df.sort_values([*LLAVES, "date"], ignore_index=True)

    grupo = df.groupby(LLAVES, sort=False, observed=True)
    df["lag_7"] = grupo["sales"].shift(7)
    df["lag_14"] = grupo["sales"].shift(14)
    df["baseline_mm28"] = pd.concat(
        [df["lag_7"], df["lag_14"], grupo["sales"].shift(21), grupo["sales"].shift(28)], axis=1
    ).mean(axis=1)
    df["promo_lag_7"] = grupo["onpromotion"].shift(7)
    for ventana, origen, destino in [(7, "lag_7", "mm_7"), (28, "lag_7", "mm_28"), (7, "promo_lag_7", "promo_mm_7")]:
        df[destino] = (
            df.groupby(LLAVES, sort=False, observed=True)[origen]
            .rolling(ventana, min_periods=1).mean()
            .reset_index(level=[0, 1], drop=True)
        )

    df["dia_semana"] = df["date"].dt.day_of_week
    df["dia_mes"] = df["date"].dt.day

    rezago = pd.Timedelta(days=REZAGO_MINIMO)
    oil_rezagado = oil.assign(date=oil["date"] + rezago)[["date", "dcoilwtico"]]
    df = df.merge(oil_rezagado.rename(columns={"dcoilwtico": "oil_lag_7"}), on="date", how="left")
    tx_rezagadas = transacciones.assign(date=transacciones["date"] + rezago)
    df = df.merge(
        tx_rezagadas.rename(columns={"transactions": "transacciones_lag_7"}),
        on=["date", "store_nbr"], how="left",
    )

    pares = dias_feriado(feriados, tiendas)
    df = _marcar(df, pares.assign(date=pares["date"] + rezago), "feriado_lag_7")
    df = _marcar(df, pares.assign(date=pares["date"] + pd.DateOffset(years=1)), "feriado_anio_anterior")

    df = df.merge(tiendas[["store_nbr", "cluster", "type"]], on="store_nbr", how="left")
    df["es_futuro"] = df["date"] > corte
    df["logical_date"] = corte
    return df[df["date"] >= inicio].reset_index(drop=True)


def preparar(logical_date: str, rutas: dict[str, Path], params: dict) -> pd.DataFrame:
    processed = rutas["processed"]
    exigir_ingesta(logical_date, processed)
    features = construir_features(
        pd.read_parquet(processed / "ventas_consolidadas.parquet"),
        pd.read_parquet(processed / "oil_diario.parquet"),
        pd.read_parquet(processed / "transacciones_diarias.parquet"),
        pd.read_parquet(processed / "feriados.parquet"),
        pd.read_parquet(processed / "tiendas.parquet"),
        logical_date,
        params["pronostico"]["horizonte_dias"],
        params["entrenamiento"]["fecha_inicio"],
    )
    escribir_parquet(features, processed / "features.parquet")
    return features


def leer_features(logical_date: str, processed: Path) -> pd.DataFrame:
    exigir_ingesta(logical_date, processed)
    features = pd.read_parquet(processed / "features.parquet")
    if set(features["logical_date"]) != {pd.Timestamp(logical_date)}:
        raise RuntimeError(f"features.parquet no corresponde a {logical_date}")
    return features


def main() -> None:
    logical_date = leer_logical_date(__doc__)
    params = cargar_params()
    features = preparar(logical_date, cargar_rutas(params), params)
    print(f"features: {len(features)} filas, {int(features['es_futuro'].sum())} futuras")


if __name__ == "__main__":
    main()
