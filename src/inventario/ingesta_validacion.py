"""Ingesta y validación de data/hist/ y de data/bandeja/ hasta logical_date.

data/processed/ se reconstruye completo en cada corrida a partir de lo entregado
hasta logical_date. Eso hace el paso idempotente y permite repetir una fecha
pasada viendo exactamente lo que se conocía entonces. Toda la validación ocurre
antes de escribir, así que un archivo inválido no altera data/processed/.

La bandeja solo se lee, y las carpetas posteriores a logical_date ni se abren.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from inventario.config import (
    cargar_params,
    cargar_rutas,
    escribir_parquet,
    leer_logical_date,
)

CORRECCION = "ventas_correccion.csv"
NULOS = {"", "nan", "none", "null", "na"}
FUENTES = ("ventas", "oil", "transactions", "holidays", "stores")
LLAVES_VENTAS = ["date", "store_nbr", "family"]


class ValidacionError(Exception):
    """Dato inválido crítico: detiene la corrida antes de escribir."""


@dataclass(frozen=True)
class Esquema:
    columnas: tuple[str, ...]
    llaves: tuple[str, ...]
    no_negativos: tuple[str, ...] = ()
    numericos_opcionales: tuple[str, ...] = ()
    unicas: bool = True
    fechas: tuple[str, ...] = field(default=("date",))


_VENTAS = ("id", "date", "store_nbr", "family", "sales", "onpromotion")
ESQUEMAS = {
    "ventas": Esquema(_VENTAS, tuple(LLAVES_VENTAS), ("sales", "onpromotion")),
    "correccion": Esquema(
        (*_VENTAS, "fecha_correccion"),
        tuple(LLAVES_VENTAS),
        ("sales", "onpromotion"),
        fechas=("date", "fecha_correccion"),
    ),
    "oil": Esquema(("date", "dcoilwtico"), ("date",), numericos_opcionales=("dcoilwtico",)),
    "transactions": Esquema(
        ("date", "store_nbr", "transactions"), ("date", "store_nbr"), ("transactions",)
    ),
    "holidays": Esquema(
        ("date", "type", "locale", "locale_name", "description", "transferred"),
        ("date",),
        unicas=False,
    ),
    "stores": Esquema(
        ("store_nbr", "city", "state", "type", "cluster"), ("store_nbr",), fechas=()
    ),
}


def leer_csv(ruta: Path) -> pd.DataFrame:
    return pd.read_csv(ruta, dtype=str, keep_default_na=False)


def validar(df: pd.DataFrame, esquema: Esquema, origen: str) -> None:
    faltantes = set(esquema.columnas) - set(df.columns)
    if faltantes:
        raise ValidacionError(f"{origen}: faltan columnas {sorted(faltantes)}")

    for llave in esquema.llaves:
        if df[llave].str.strip().str.lower().isin(NULOS).any():
            raise ValidacionError(f"{origen}: nulos en la llave {llave}")

    for columna in esquema.fechas:
        fechas = pd.to_datetime(df[columna], format="%Y-%m-%d", errors="coerce")
        if fechas.isna().any():
            raise ValidacionError(f"{origen}: fechas inválidas en {columna}")

    for columna in esquema.no_negativos:
        valores = pd.to_numeric(df[columna], errors="coerce")
        if valores.isna().any():
            raise ValidacionError(f"{origen}: valores no numéricos en {columna}")
        if (valores < 0).any():
            raise ValidacionError(f"{origen}: valores negativos en {columna}")

    for columna in esquema.numericos_opcionales:
        presentes = df[columna][~df[columna].str.strip().str.lower().isin(NULOS)]
        if pd.to_numeric(presentes, errors="coerce").isna().any():
            raise ValidacionError(f"{origen}: valores no numéricos en {columna}")

    if esquema.unicas and df.duplicated(list(esquema.llaves)).any():
        raise ValidacionError(f"{origen}: llaves duplicadas")


def _leer_entrega(ruta: Path, fuente: str, fecha: str) -> pd.DataFrame:
    df = leer_csv(ruta)
    validar(df, ESQUEMAS[fuente], str(ruta))
    if fuente != "stores" and (df["date"] != fecha).any():
        raise ValidacionError(f"{ruta}: contiene fechas distintas a su entrega {fecha}")
    return df


def _leer_correccion(ruta: Path, fecha: str) -> pd.DataFrame:
    df = leer_csv(ruta)
    validar(df, ESQUEMAS["correccion"], str(ruta))
    if (df["fecha_correccion"] != fecha).any() or (df["date"] >= fecha).any():
        raise ValidacionError(f"{ruta}: la corrección debe referirse a fechas anteriores")
    return df


def recolectar(logical_date: str, hist: Path, bandeja: Path) -> dict:
    """Lee y valida todo lo disponible. No escribe nada."""
    tablas: dict[str, list[pd.DataFrame]] = {}
    for fuente in FUENTES:
        df = leer_csv(hist / f"{fuente}.csv")
        validar(df, ESQUEMAS[fuente], str(hist / f"{fuente}.csv"))
        tablas[fuente] = [df if fuente == "stores" else df[df["date"] <= logical_date]]

    ventas_hist = tablas["ventas"][0]["date"]
    ultimo_hist = ventas_hist.max() if len(ventas_hist) else logical_date
    calendario = pd.date_range(ultimo_hist, logical_date, inclusive="right")
    avisos, correcciones = [], []
    for fecha in calendario.strftime("%Y-%m-%d"):
        carpeta = bandeja / fecha
        for fuente in FUENTES:
            ruta = carpeta / f"{fuente}.csv"
            if ruta.exists():
                tablas[fuente].append(_leer_entrega(ruta, fuente, fecha))
            else:
                avisos.append(f"{fecha}: no llegó {fuente}.csv")
        if (carpeta / CORRECCION).exists():
            correcciones.append(_leer_correccion(carpeta / CORRECCION, fecha))

    return {
        "ventas": pd.concat(tablas["ventas"], ignore_index=True),
        "oil": pd.concat(tablas["oil"], ignore_index=True),
        "transactions": pd.concat(tablas["transactions"], ignore_index=True),
        "holidays": pd.concat(tablas["holidays"], ignore_index=True),
        "stores": tablas["stores"][-1],
        "correcciones": correcciones,
        "avisos": avisos,
    }


def _tipar_ventas(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df["store_nbr"] = df["store_nbr"].astype(int)
    df["sales"] = df["sales"].astype(float)
    df["onpromotion"] = df["onpromotion"].astype(float)
    return df


def aplicar_correcciones(ventas: pd.DataFrame, correcciones: list[pd.DataFrame]) -> pd.DataFrame:
    """Sustituye cada registro corregido; la entrega más reciente prevalece."""
    ventas = _tipar_ventas(ventas)
    ventas["corregido"] = False
    ventas["fecha_correccion"] = pd.NaT
    for correccion in correcciones:
        nuevo = _tipar_ventas(correccion)
        nuevo["fecha_correccion"] = pd.to_datetime(nuevo["fecha_correccion"])
        nuevo = nuevo[[*LLAVES_VENTAS, "sales", "onpromotion", "fecha_correccion"]]
        cruce = ventas[LLAVES_VENTAS].merge(nuevo, on=LLAVES_VENTAS, how="left", indicator=True)
        coincide = (cruce["_merge"] == "both").to_numpy()
        if coincide.sum() != len(nuevo):
            raise ValidacionError("La corrección apunta a registros que no existen")
        for columna in ["sales", "onpromotion", "fecha_correccion"]:
            ventas.loc[coincide, columna] = cruce.loc[coincide, columna].to_numpy()
        ventas.loc[coincide, "corregido"] = True
    return ventas


def oil_diario(oil: pd.DataFrame, logical_date: str) -> pd.DataFrame:
    """Calendario completo con forward-fill: nunca usa un valor posterior."""
    serie = pd.DataFrame({
        "date": pd.to_datetime(oil["date"]),
        "dcoilwtico": pd.to_numeric(oil["dcoilwtico"], errors="coerce"),
    }).set_index("date")["dcoilwtico"]
    calendario = pd.date_range(serie.index.min(), logical_date, name="date")
    serie = serie.reindex(calendario)
    return pd.DataFrame({
        "date": calendario,
        "dcoilwtico": serie.ffill().to_numpy(),
        "oil_imputado": serie.isna().to_numpy(),
    })


def transacciones_diarias(transacciones: pd.DataFrame, logical_date: str) -> pd.DataFrame:
    """Forward-fill por tienda hasta logical_date."""
    tabla = (
        transacciones.assign(
            date=pd.to_datetime(transacciones["date"]),
            store_nbr=transacciones["store_nbr"].astype(int),
            transactions=transacciones["transactions"].astype(float),
        )
        .pivot(index="date", columns="store_nbr", values="transactions")
        .reindex(pd.date_range(transacciones["date"].min(), logical_date, name="date"))
        .ffill()
    )
    return tabla.stack().dropna().rename("transactions").reset_index()


def tipar_feriados(feriados: pd.DataFrame) -> pd.DataFrame:
    return feriados.assign(
        date=pd.to_datetime(feriados["date"]),
        transferred=feriados["transferred"].str.lower() == "true",
    )


def tipar_tiendas(tiendas: pd.DataFrame) -> pd.DataFrame:
    return tiendas.assign(
        store_nbr=tiendas["store_nbr"].astype(int), cluster=tiendas["cluster"].astype(int)
    )


def ingerir(logical_date: str, rutas: dict[str, Path]) -> dict:
    datos = recolectar(logical_date, rutas["hist"], rutas["bandeja"])

    tiendas_validas = set(datos["stores"]["store_nbr"])
    desconocidas = set(datos["ventas"]["store_nbr"]) - tiendas_validas
    if desconocidas:
        raise ValidacionError(f"ventas: tiendas desconocidas {sorted(desconocidas)}")

    ventas = aplicar_correcciones(datos["ventas"], datos["correcciones"])
    oil = oil_diario(datos["oil"], logical_date)
    transacciones = transacciones_diarias(datos["transactions"], logical_date)

    processed = rutas["processed"]
    escribir_parquet(ventas, processed / "ventas_consolidadas.parquet")
    escribir_parquet(oil, processed / "oil_diario.parquet")
    escribir_parquet(transacciones, processed / "transacciones_diarias.parquet")
    escribir_parquet(tipar_feriados(datos["holidays"]), processed / "feriados.parquet")
    escribir_parquet(tipar_tiendas(datos["stores"]), processed / "tiendas.parquet")

    estado = {
        "logical_date": logical_date,
        "ultima_fecha_ventas": ventas["date"].max().strftime("%Y-%m-%d"),
        "filas_ventas": len(ventas),
        "filas_corregidas": int(ventas["corregido"].sum()),
        "dias_oil_imputados": int(oil["oil_imputado"].sum()),
        "avisos": datos["avisos"],
    }
    temporal = processed / "estado_ingesta.json.tmp"
    temporal.write_text(json.dumps(estado, indent=2, ensure_ascii=False), encoding="utf-8")
    temporal.replace(processed / "estado_ingesta.json")
    return estado


def exigir_ingesta(logical_date: str, processed: Path) -> None:
    """Evita usar un consolidado construido para otra fecha de corte."""
    ruta = processed / "estado_ingesta.json"
    actual = json.loads(ruta.read_text(encoding="utf-8"))["logical_date"] if ruta.exists() else None
    if actual != logical_date:
        raise RuntimeError(
            f"data/processed corresponde a {actual}; ejecuta la ingesta de {logical_date}"
        )


def main() -> None:
    logical_date = leer_logical_date(__doc__)
    estado = ingerir(logical_date, cargar_rutas(cargar_params()))
    print(json.dumps({**estado, "avisos": len(estado["avisos"])}, ensure_ascii=False))
    for aviso in estado["avisos"]:
        print("AVISO:", aviso)


if __name__ == "__main__":
    main()
