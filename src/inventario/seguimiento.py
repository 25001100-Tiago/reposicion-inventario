"""Compara lo publicado con las ventas reales conocidas hasta logical_date.

Las ventas reales salen del consolidado, así que incluyen las correcciones. Una
fila cuyo día aún no tiene venta conocida queda como pendiente.
"""

from pathlib import Path

import numpy as np
import pandas as pd

from inventario.config import cargar_params, cargar_rutas, leer_logical_date
from inventario.ingesta_validacion import exigir_ingesta

LLAVES = ["date", "store_nbr", "family"]


def cruzar(logical_date: str, rutas: dict[str, Path]) -> pd.DataFrame:
    exigir_ingesta(logical_date, rutas["processed"])
    publicados = pd.read_parquet(rutas["publicados"] / "pronosticos_pedidos.parquet")
    ventas = pd.read_parquet(
        rutas["processed"] / "ventas_consolidadas.parquet", columns=[*LLAVES, "sales"]
    )
    ventas = ventas[ventas["date"] <= pd.Timestamp(logical_date)]
    return publicados.merge(ventas, on=LLAVES, how="left")


def resumir(df: pd.DataFrame, por: list[str]) -> pd.DataFrame:
    def agregar(grupo: pd.DataFrame) -> pd.Series:
        conocidas = grupo[grupo["sales"].notna()]
        error = conocidas["sales"] - conocidas["p50"]
        faltantes = np.maximum(conocidas["sales"] - conocidas["pedido_sugerido"], 0)
        sobrantes = np.maximum(conocidas["pedido_sugerido"] - conocidas["sales"], 0)
        quiebre = (conocidas["costo_quiebre"] * faltantes).sum()
        merma = (conocidas["costo_merma"] * sobrantes).sum()
        hay_datos = len(conocidas) > 0
        return pd.Series({
            "filas_evaluadas": len(conocidas),
            "filas_pendientes": len(grupo) - len(conocidas),
            "ventas_reales": conocidas["sales"].sum(),
            "pedido_sugerido": conocidas["pedido_sugerido"].sum(),
            "mae": error.abs().mean() if hay_datos else np.nan,
            "sesgo": error.mean() if hay_datos else np.nan,
            "cobertura_p10_p90": (
                conocidas["sales"].between(conocidas["p10"], conocidas["p90"]).mean()
                if hay_datos else np.nan
            ),
            "faltantes": faltantes.sum(),
            "sobrantes": sobrantes.sum(),
            "costo_quiebre_real": quiebre,
            "costo_merma_real": merma,
            "costo_real": quiebre + merma,
        })

    return pd.DataFrame(
        [{**dict(zip(por, clave)), **agregar(grupo)}
         for clave, grupo in df.groupby(por, sort=True)]
    )


def tabla_markdown(df: pd.DataFrame) -> str:
    texto = df.copy()
    for columna in texto.columns:
        if pd.api.types.is_datetime64_any_dtype(texto[columna]):
            texto[columna] = texto[columna].dt.strftime("%Y-%m-%d")
        elif columna == "cobertura_p10_p90":
            texto[columna] = texto[columna].map(lambda v: "n/d" if pd.isna(v) else f"{v:.1%}")
        elif pd.api.types.is_numeric_dtype(texto[columna]):
            texto[columna] = texto[columna].map(lambda v: "n/d" if pd.isna(v) else f"{v:,.0f}")
    filas = [
        "| " + " | ".join(texto.columns) + " |",
        "|" + " --- |" * len(texto.columns),
        *("| " + " | ".join(map(str, fila)) + " |" for fila in texto.itertuples(index=False)),
    ]
    return "\n".join(filas)


def generar(logical_date: str, rutas: dict[str, Path]) -> pd.DataFrame:
    cruce = cruzar(logical_date, rutas)
    diario = resumir(cruce, ["fecha_corte", "date"])
    diario.to_csv(rutas["publicados"] / "seguimiento_desempeno.csv", index=False, float_format="%.4f")

    por_corte = resumir(cruce, ["fecha_corte"])
    columnas = [
        "fecha_corte", "filas_evaluadas", "filas_pendientes", "ventas_reales", "mae",
        "sesgo", "cobertura_p10_p90", "costo_quiebre_real", "costo_merma_real", "costo_real",
    ]
    versiones = cruce["version_modelo"].drop_duplicates().tolist()
    rutas["evidencia"].mkdir(parents=True, exist_ok=True)
    (rutas["evidencia"] / "seguimiento_desempeno.md").write_text(
        f"# Seguimiento de desempeño\n\n"
        f"Ventas reales conocidas hasta {logical_date}, con correcciones aplicadas. "
        f"Modelos evaluados: {', '.join(versiones)}.\n\n"
        f"{tabla_markdown(por_corte[columnas])}\n",
        encoding="utf-8",
    )
    return diario


def main() -> None:
    logical_date = leer_logical_date(__doc__)
    diario = generar(logical_date, cargar_rutas(cargar_params()))
    print(f"seguimiento: {int(diario['filas_evaluadas'].sum())} filas evaluadas, "
          f"costo real {diario['costo_real'].sum():,.0f}")


if __name__ == "__main__":
    main()
