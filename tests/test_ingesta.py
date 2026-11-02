import hashlib

import pandas as pd
import pytest

from conftest import DIA_SIN_OIL, FECHA_CORRECCION, FECHA_CORREGIDA, FIN_BANDEJA
from inventario.ingesta_validacion import ValidacionError, exigir_ingesta, ingerir

CONSOLIDADO = "ventas_consolidadas.parquet"


def huella(carpeta) -> dict:
    return {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(carpeta.rglob("*")) if p.is_file()
    }


def alterar_ventas(rutas, fecha, cambio):
    ruta = rutas["bandeja"] / fecha / "ventas.csv"
    df = pd.read_csv(ruta, dtype=str, keep_default_na=False)
    cambio(df).to_csv(ruta, index=False)


def test_ingesta_es_idempotente(entorno):
    rutas = entorno["rutas"]
    ingerir("2017-03-28", rutas)
    primera = huella(rutas["processed"])
    ingerir("2017-03-28", rutas)
    assert huella(rutas["processed"]) == primera


def test_bandeja_nunca_se_modifica(entorno):
    rutas = entorno["rutas"]
    antes = huella(rutas["bandeja"])
    ingerir(FIN_BANDEJA, rutas)
    assert huella(rutas["bandeja"]) == antes


def test_no_lee_datos_posteriores_a_logical_date(entorno):
    rutas = entorno["rutas"]
    ingerir("2017-03-18", rutas)
    ventas = pd.read_parquet(rutas["processed"] / CONSOLIDADO)
    oil = pd.read_parquet(rutas["processed"] / "oil_diario.parquet")
    assert ventas["date"].max() == pd.Timestamp("2017-03-18")
    assert oil["date"].max() == pd.Timestamp("2017-03-18")


def test_logical_date_anterior_al_corte_recorta_el_historico(entorno):
    rutas = entorno["rutas"]
    ingerir("2017-02-10", rutas)
    assert pd.read_parquet(rutas["processed"] / CONSOLIDADO)["date"].max() == pd.Timestamp("2017-02-10")


def test_archivo_invalido_en_el_futuro_no_afecta(entorno):
    rutas = entorno["rutas"]
    alterar_ventas(rutas, "2017-03-30", lambda d: d.assign(sales="-1"))
    ingerir("2017-03-28", rutas)


@pytest.mark.parametrize(
    "cambio, mensaje",
    [
        (lambda d: d.assign(sales="-5.0"), "negativos"),
        (lambda d: d.assign(sales="abc"), "no numéricos"),
        (lambda d: d.drop(columns="onpromotion"), "faltan columnas"),
        (lambda d: d.assign(family=""), "nulos en la llave family"),
        (lambda d: d.assign(store_nbr=""), "nulos en la llave store_nbr"),
        (lambda d: d.assign(date=""), "nulos en la llave date"),
        (lambda d: d.assign(date="2017-03-31"), "distintas a su entrega"),
        (lambda d: pd.concat([d, d]), "duplicadas"),
    ],
)
def test_dato_critico_detiene_y_no_contamina_processed(entorno, cambio, mensaje):
    rutas = entorno["rutas"]
    ingerir("2017-03-27", rutas)
    antes = huella(rutas["processed"])
    alterar_ventas(rutas, "2017-03-28", cambio)
    with pytest.raises(ValidacionError, match=mensaje):
        ingerir("2017-03-28", rutas)
    assert huella(rutas["processed"]) == antes


def test_tienda_desconocida_detiene(entorno):
    rutas = entorno["rutas"]
    alterar_ventas(
        rutas, "2017-03-28", lambda d: d.assign(store_nbr=d["store_nbr"].replace("2", "99"))
    )
    with pytest.raises(ValidacionError, match="tiendas desconocidas"):
        ingerir("2017-03-28", rutas)


def test_oil_ausente_y_fin_de_semana_se_imputan_sin_fallar(entorno):
    rutas = entorno["rutas"]
    estado = ingerir("2017-03-26", rutas)
    oil = pd.read_parquet(rutas["processed"] / "oil_diario.parquet").set_index("date")

    assert f"{DIA_SIN_OIL}: no llegó oil.csv" in estado["avisos"]
    previo = pd.Timestamp(DIA_SIN_OIL) - pd.Timedelta(days=1)
    assert oil.loc[DIA_SIN_OIL, "dcoilwtico"] == oil.loc[previo, "dcoilwtico"]
    assert oil.loc[DIA_SIN_OIL, "oil_imputado"]
    assert oil.loc["2017-03-18", "oil_imputado"]  # sábado


def test_forward_fill_no_usa_un_valor_posterior(entorno):
    rutas = entorno["rutas"]
    ingerir(DIA_SIN_OIL, rutas)
    oil = pd.read_parquet(rutas["processed"] / "oil_diario.parquet")
    assert oil["date"].max() == pd.Timestamp(DIA_SIN_OIL)
    assert oil.iloc[-1]["dcoilwtico"] == oil.iloc[-2]["dcoilwtico"]


def test_archivo_que_aun_no_llega_no_falla(entorno):
    rutas = entorno["rutas"]
    (rutas["bandeja"] / "2017-03-27" / "transactions.csv").unlink()
    estado = ingerir("2017-03-27", rutas)
    tx = pd.read_parquet(rutas["processed"] / "transacciones_diarias.parquet")
    assert "2017-03-27: no llegó transactions.csv" in estado["avisos"]
    assert (tx["date"] == pd.Timestamp("2017-03-27")).sum() == 2


def test_venta_que_no_llega_no_se_inventa(entorno):
    rutas = entorno["rutas"]
    (rutas["bandeja"] / "2017-03-27" / "ventas.csv").unlink()
    estado = ingerir("2017-03-27", rutas)
    ventas = pd.read_parquet(rutas["processed"] / CONSOLIDADO)
    assert "2017-03-27: no llegó ventas.csv" in estado["avisos"]
    assert ventas["date"].max() == pd.Timestamp("2017-03-26")


def _venta(rutas, tienda=1, familia="DAIRY"):
    df = pd.read_parquet(rutas["processed"] / CONSOLIDADO)
    return df[
        (df["date"] == FECHA_CORREGIDA) & (df["store_nbr"] == tienda) & (df["family"] == familia)
    ].iloc[0]


def test_correccion_actualiza_el_registro_sin_duplicarlo(entorno):
    rutas, reales = entorno["rutas"], entorno["ventas_reales"]
    real = reales[
        (reales["date"] == FECHA_CORREGIDA) & (reales["store_nbr"] == 1) & (reales["family"] == "DAIRY")
    ]["sales"].iloc[0]

    ingerir(FECHA_CORRECCION, rutas)
    fila = _venta(rutas)
    assert fila["sales"] == pytest.approx(real)
    assert fila["corregido"]

    df = pd.read_parquet(rutas["processed"] / CONSOLIDADO)
    assert not df.duplicated(["date", "store_nbr", "family"]).any()
    assert df["corregido"].sum() == 2


def test_correccion_no_existe_antes_de_su_entrega(entorno):
    rutas, reales = entorno["rutas"], entorno["ventas_reales"]
    real = reales[
        (reales["date"] == FECHA_CORREGIDA) & (reales["store_nbr"] == 1) & (reales["family"] == "DAIRY")
    ]["sales"].iloc[0]

    ingerir("2017-03-24", rutas)
    assert _venta(rutas)["sales"] == pytest.approx(real * 0.5)
    assert not _venta(rutas)["corregido"]


def test_repetir_una_fecha_anterior_vuelve_a_lo_que_se_conocia(entorno):
    rutas = entorno["rutas"]
    ingerir("2017-03-24", rutas)
    anterior = huella(rutas["processed"])
    ingerir(FECHA_CORRECCION, rutas)
    ingerir("2017-03-24", rutas)
    assert huella(rutas["processed"]) == anterior


def test_correccion_a_registro_inexistente_detiene(entorno):
    rutas = entorno["rutas"]
    ruta = rutas["bandeja"] / FECHA_CORRECCION / "ventas_correccion.csv"
    df = pd.read_csv(ruta, dtype=str, keep_default_na=False)
    df.assign(family=df["family"].replace("DAIRY", "NO EXISTE")).to_csv(ruta, index=False)
    with pytest.raises(ValidacionError, match="no existen"):
        ingerir(FECHA_CORRECCION, rutas)


def test_correccion_de_fecha_no_anterior_detiene(entorno):
    rutas = entorno["rutas"]
    ruta = rutas["bandeja"] / FECHA_CORRECCION / "ventas_correccion.csv"
    df = pd.read_csv(ruta, dtype=str, keep_default_na=False)
    df.assign(date=FECHA_CORRECCION).to_csv(ruta, index=False)
    with pytest.raises(ValidacionError, match="fechas anteriores"):
        ingerir(FECHA_CORRECCION, rutas)


def test_exigir_ingesta_rechaza_otra_fecha(entorno):
    rutas = entorno["rutas"]
    ingerir("2017-03-24", rutas)
    exigir_ingesta("2017-03-24", rutas["processed"])
    with pytest.raises(RuntimeError, match="corresponde a 2017-03-24"):
        exigir_ingesta("2017-03-25", rutas["processed"])
