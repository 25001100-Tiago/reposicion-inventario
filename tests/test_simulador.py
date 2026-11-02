import pandas as pd
import pytest

from simular_sistema_origen import CORRECCION, leer_csv, simular, verificar

FECHAS = pd.date_range("2017-07-13", "2017-07-23").strftime("%Y-%m-%d").tolist()
SIM = {
    "fecha_corte_hist": "2017-07-15",
    "bandeja_inicio": "2017-07-16",
    "bandeja_fin": "2017-07-22",
    "dia_sin_oil": "2017-07-18",
    "correccion_ventas": {
        "fecha_ventas": "2017-07-17",
        "fecha_entrega": "2017-07-20",
        "tienda": 44,
        "familias": ["PRODUCE"],
        "factor_entrega_original": 0.5,
    },
}


@pytest.fixture
def raw(tmp_path):
    carpeta = tmp_path / "raw"
    carpeta.mkdir()
    ventas = [
        {"id": i, "date": f, "store_nbr": 44, "family": fam, "sales": 10.5 + i, "onpromotion": 0}
        for i, (f, fam) in enumerate(
            (f, fam) for f in FECHAS for fam in ["PRODUCE", "LIQUOR,WINE,BEER"]
        )
    ]
    pd.DataFrame(ventas).to_csv(carpeta / "train.csv", index=False)
    habiles = [f for f in FECHAS if pd.Timestamp(f).weekday() < 5]
    pd.DataFrame({"date": habiles, "dcoilwtico": 46.5}).to_csv(carpeta / "oil.csv", index=False)
    pd.DataFrame({"date": FECHAS, "store_nbr": 44, "transactions": 100}).to_csv(
        carpeta / "transactions.csv", index=False
    )
    pd.DataFrame(
        [
            {"date": "2017-07-14", "type": "Holiday", "locale": "Local", "locale_name": "A",
             "description": "x", "transferred": "False"},
            {"date": "2017-07-19", "type": "Holiday", "locale": "Local", "locale_name": "B",
             "description": "y", "transferred": "False"},
            {"date": "2017-12-25", "type": "Holiday", "locale": "National", "locale_name": "C",
             "description": "z", "transferred": "False"},
        ]
    ).to_csv(carpeta / "holidays_events.csv", index=False)
    pd.DataFrame(
        [{"store_nbr": 44, "city": "Quito", "state": "Pichincha", "type": "A", "cluster": 5}]
    ).to_csv(carpeta / "stores.csv", index=False)
    return carpeta


@pytest.fixture
def salida(raw, tmp_path):
    hist, bandeja = tmp_path / "hist", tmp_path / "bandeja"
    resumen = simular(raw, hist, bandeja, SIM)
    return hist, bandeja, resumen


def test_hist_no_contiene_fechas_posteriores_al_corte(salida):
    hist, _, _ = salida
    for nombre in ["ventas", "oil", "transactions", "holidays"]:
        assert leer_csv(hist / f"{nombre}.csv")["date"].max() <= SIM["fecha_corte_hist"]


def test_cada_dia_trae_las_cinco_fuentes_salvo_oil_el_dia_sin_entrega(salida):
    _, bandeja, _ = salida
    for carpeta in sorted(p for p in bandeja.iterdir()):
        nombres = {p.name for p in carpeta.iterdir()} - {CORRECCION}
        esperados = {"ventas.csv", "oil.csv", "transactions.csv", "holidays.csv", "stores.csv"}
        if carpeta.name == SIM["dia_sin_oil"]:
            esperados.remove("oil.csv")
        assert nombres == esperados


def test_fin_de_semana_entrega_oil_sin_filas_y_distinto_de_no_entrega(salida):
    _, bandeja, resumen = salida
    assert leer_csv(bandeja / "2017-07-16" / "oil.csv").empty
    assert "2017-07-16" in resumen["bandeja"]["dias_oil_sin_filas"]
    assert not (bandeja / SIM["dia_sin_oil"] / "oil.csv").exists()


def test_correccion_llega_despues_y_restaura_el_valor_real(raw, salida):
    _, bandeja, _ = salida
    corr = SIM["correccion_ventas"]
    entregado = leer_csv(bandeja / corr["fecha_ventas"] / "ventas.csv")
    nuevo = leer_csv(bandeja / corr["fecha_entrega"] / CORRECCION)
    real = leer_csv(raw / "train.csv")

    assert len(nuevo) == 1
    assert nuevo["date"].iloc[0] == corr["fecha_ventas"]
    assert not list(bandeja.glob(f"{corr['fecha_ventas']}/{CORRECCION}"))
    fila = (entregado["family"] == "PRODUCE")
    valor_real = real[(real["date"] == corr["fecha_ventas"]) & (real["family"] == "PRODUCE")]
    assert float(entregado.loc[fila, "sales"].iloc[0]) == pytest.approx(
        float(valor_real["sales"].iloc[0]) * 0.5
    )
    assert nuevo["sales"].iloc[0] == valor_real["sales"].iloc[0]


def test_verificar_acepta_una_salida_integra(raw, salida):
    hist, bandeja, _ = salida
    verificar(raw, hist, bandeja, SIM)


def test_verificar_detecta_una_fuga_en_hist(raw, salida):
    hist, bandeja, _ = salida
    ruta = hist / "oil.csv"
    ruta.write_text(ruta.read_text() + "2017-07-20,46.5\n")
    with pytest.raises(AssertionError, match="posteriores al corte"):
        verificar(raw, hist, bandeja, SIM)


def test_rechaza_dia_sin_oil_que_no_tiene_cotizacion(raw, tmp_path):
    sim = {**SIM, "dia_sin_oil": "2017-07-16"}  # domingo
    with pytest.raises(ValueError, match="cotización"):
        simular(raw, tmp_path / "h", tmp_path / "b", sim)
