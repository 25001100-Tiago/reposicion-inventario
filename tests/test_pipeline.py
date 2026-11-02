import json
import shutil
import sqlite3

import joblib
import numpy as np
import pandas as pd
import pytest

from conftest import FECHA_CORRECCION, FECHA_CORREGIDA
from inventario.entrenar_comparar import (
    aplicar_compuerta,
    artefacto_baseline,
    entrenar_cuantiles,
    entrenar_y_comparar,
    metricas,
    particion_temporal,
)
from inventario.ingesta_validacion import ingerir
from inventario.preparacion import construir_features, leer_features, preparar
from inventario.pronostico_pedido import calcular, pronosticar, publicar, resolver_modelo
from inventario.seguimiento import cruzar, generar

CORTE = CORTE_MODELO = "2017-03-24"
LLAVES = ["date", "store_nbr", "family"]


def correr(entorno, fecha, entrenar=False):
    rutas, params, costos = entorno["rutas"], entorno["params"], entorno["costos"]
    ingerir(fecha, rutas)
    preparar(fecha, rutas, params)
    if entrenar:
        return entrenar_y_comparar(fecha, rutas, params, costos)
    return pronosticar(fecha, rutas, params, costos)


@pytest.fixture
def entrenado(entorno):
    correr(entorno, CORTE_MODELO, entrenar=True)
    candidato_siempre_vigente(entorno)
    return entorno


def candidato_siempre_vigente(entorno):
    """Fija un modelo de cuantiles como vigente, gane o no la compuerta."""
    rutas = entorno["rutas"]
    features = leer_features(CORTE_MODELO, rutas["processed"])
    datos = features[~features["es_futuro"] & features["sales"].notna()]
    ruta = rutas["modelos"] / "modelo_vigente.joblib"
    joblib.dump(
        {**entrenar_cuantiles(datos, entorno["params"]["entrenamiento"]), "version_codigo": "test"},
        ruta,
    )


def tablas(rutas):
    con = sqlite3.connect(rutas["publicados"] / "operacion.db")
    try:
        filas = pd.read_sql("SELECT * FROM pronosticos_pedidos", con)
        corridas = pd.read_sql("SELECT * FROM corridas", con)
    finally:
        con.close()
    return filas, corridas


# --- Features y cero fuga ---

def _insumos(rutas):
    p = rutas["processed"]
    return [pd.read_parquet(p / n) for n in (
        "ventas_consolidadas.parquet", "oil_diario.parquet", "transacciones_diarias.parquet",
        "feriados.parquet", "tiendas.parquet",
    )]


def test_features_no_cambian_si_se_alteran_datos_futuros(entorno):
    rutas = entorno["rutas"]
    ingerir("2017-03-31", rutas)
    ventas, oil, tx, feriados, tiendas = _insumos(rutas)
    args = (feriados, tiendas, CORTE, 7, "2017-01-01")

    normal = construir_features(ventas, oil, tx, *args)

    futuro = ventas["date"] > pd.Timestamp(CORTE)
    v2 = ventas.copy()
    v2.loc[futuro, "sales"] = 1e9
    v2.loc[futuro, "onpromotion"] = 1e9
    o2 = oil.copy()
    o2.loc[o2["date"] > pd.Timestamp(CORTE), "dcoilwtico"] = 1e9
    t2 = tx.copy()
    t2.loc[t2["date"] > pd.Timestamp(CORTE), "transactions"] = 1e9
    alterado = construir_features(v2, o2, t2, *args)

    pd.testing.assert_frame_equal(normal, alterado)


def test_filas_futuras_no_tienen_venta_y_usan_el_rezago_correcto(entorno):
    rutas, params = entorno["rutas"], entorno["params"]
    ingerir(CORTE, rutas)
    features = preparar(CORTE, rutas, params)
    futuro = features[features["es_futuro"]]

    assert futuro["date"].max() == pd.Timestamp(CORTE) + pd.Timedelta(days=7)
    assert futuro["sales"].isna().all()
    assert futuro["lag_7"].notna().all()

    ventas = _insumos(rutas)[0].set_index(LLAVES)["sales"]
    fila = futuro[futuro["date"] == pd.Timestamp("2017-03-31")].iloc[0]
    assert fila["lag_7"] == ventas[(pd.Timestamp(CORTE), fila["store_nbr"], fila["family"])]


def test_media_movil_no_incluye_el_dia_objetivo(entorno):
    rutas, params = entorno["rutas"], entorno["params"]
    ingerir(CORTE, rutas)
    features = preparar(CORTE, rutas, params)
    serie = features[(features["store_nbr"] == 1) & (features["family"] == "DAIRY")].set_index("date")
    t = pd.Timestamp("2017-03-20")
    ventana = pd.date_range(t - pd.Timedelta(days=13), t - pd.Timedelta(days=7))
    assert serie.loc[t, "mm_7"] == pytest.approx(serie.loc[ventana, "sales"].mean())


def test_baseline_promedia_el_mismo_dia_de_semana(entorno):
    rutas, params = entorno["rutas"], entorno["params"]
    ingerir(CORTE, rutas)
    features = preparar(CORTE, rutas, params)
    serie = features[(features["store_nbr"] == 2) & (features["family"] == "PRODUCE")].set_index("date")
    t = pd.Timestamp("2017-03-20")
    fechas = [t - pd.Timedelta(days=d) for d in (7, 14, 21, 28)]
    assert serie.loc[t, "baseline_mm28"] == pytest.approx(serie.loc[fechas, "sales"].mean())


def test_feriado_del_anio_anterior_y_transferidos(entorno):
    rutas, params = entorno["rutas"], entorno["params"]
    ingerir(CORTE, rutas)
    features = preparar(CORTE, rutas, params)
    t = features[features["date"] == pd.Timestamp("2017-03-28")]
    assert (t["feriado_anio_anterior"] == 1).all()
    tienda_quito = features[(features["store_nbr"] == 1) & (features["date"] == pd.Timestamp("2017-03-06"))]
    tienda_cuenca = features[(features["store_nbr"] == 2) & (features["date"] == pd.Timestamp("2017-03-06"))]
    assert (tienda_quito["feriado_lag_7"] == 1).all()
    assert (tienda_cuenca["feriado_lag_7"] == 0).all()


def test_horizonte_mayor_al_rezago_se_rechaza(entorno):
    rutas = entorno["rutas"]
    ingerir(CORTE, rutas)
    ventas, oil, tx, feriados, tiendas = _insumos(rutas)
    with pytest.raises(ValueError, match="Horizonte"):
        construir_features(ventas, oil, tx, feriados, tiendas, CORTE, 8, "2017-01-01")


def test_pronostico_no_cambia_si_cambia_la_bandeja_futura(entrenado):
    rutas, params, costos = entrenado["rutas"], entrenado["params"], entrenado["costos"]
    ingerir(CORTE, rutas)
    preparar(CORTE, rutas, params)
    antes = calcular(CORTE, rutas, params, costos)

    shutil.rmtree(rutas["bandeja"] / "2017-03-28")
    ingerir(CORTE, rutas)
    preparar(CORTE, rutas, params)
    despues = calcular(CORTE, rutas, params, costos)
    pd.testing.assert_frame_equal(antes, despues)


def test_modelo_entrenado_despues_del_corte_se_rechaza(entrenado):
    with pytest.raises(RuntimeError, match="posteriores a logical_date"):
        resolver_modelo(entrenado["rutas"]["modelos"], "2017-03-20")


def test_resuelve_el_modelo_mas_reciente_sin_fuga(entrenado):
    modelos = entrenado["rutas"]["modelos"]
    (modelos / "historial").mkdir()
    joblib.dump({"tipo": "viejo", "fecha_corte": "2017-03-10"}, modelos / "historial" / "modelo_2017-03-10.joblib")
    joblib.dump({"tipo": "medio", "fecha_corte": "2017-03-15"}, modelos / "historial" / "modelo_2017-03-15.joblib")

    assert resolver_modelo(modelos, "2017-03-12")[1]["tipo"] == "viejo"
    assert resolver_modelo(modelos, "2017-03-20")[1]["tipo"] == "medio"
    assert resolver_modelo(modelos, CORTE)[1]["tipo"] == "cuantiles"


def test_solo_historial_no_toca_el_modelo_vigente(entorno):
    rutas = entorno["rutas"]
    ruta = rutas["modelos"] / "modelo_vigente.joblib"
    ruta.parent.mkdir(parents=True)
    joblib.dump({"tipo": "vigente", "fecha_corte": "2017-03-31"}, ruta)
    original = ruta.read_bytes()

    ingerir(CORTE, rutas)
    preparar(CORTE, rutas, entorno["params"])
    entrenar_y_comparar(CORTE, rutas, entorno["params"], entorno["costos"], promover=False)

    assert ruta.read_bytes() == original
    assert (rutas["modelos"] / "historial" / f"modelo_{CORTE}.joblib").exists()
    assert (rutas["evidencia"] / f"metrics_{CORTE}.json").exists()
    assert not (rutas["evidencia"] / "metrics.json").exists()


def test_pronostico_exige_ingesta_de_la_misma_fecha(entrenado):
    rutas, params, costos = entrenado["rutas"], entrenado["params"], entrenado["costos"]
    with pytest.raises(RuntimeError, match="ejecuta la ingesta"):
        calcular("2017-03-27", rutas, params, costos)


# --- Entrenamiento y compuerta ---

def test_particion_temporal_no_se_solapa_y_excluye_sin_baseline():
    df = pd.DataFrame({
        "date": pd.date_range("2017-01-01", periods=20),
        "baseline_mm28": [np.nan] * 16 + [1.0] * 4,
    })
    entrenamiento, validacion = particion_temporal(df, 7)
    assert entrenamiento["date"].max() < validacion["date"].min()
    assert validacion["date"].min() == pd.Timestamp("2017-01-17")


def test_ambos_modelos_se_evaluan_con_la_misma_particion(entorno):
    resultado = correr(entorno, CORTE_MODELO, entrenar=True)
    tabla = pd.read_csv(entorno["rutas"]["evidencia"] / "comparacion_modelos.csv")
    assert set(tabla["modelo"]) == {"baseline_mm28_dow", "hgb_cuantiles"}
    for columna in ["mae", "rmse", "pinball_media", "costo_negocio"]:
        assert tabla[columna].notna().all()
    assert resultado["particion"]["validacion"] == ["2017-03-18", "2017-03-24"]
    metrics = json.loads((entorno["rutas"]["evidencia"] / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["compuerta"]["decision"] in {"reemplazado", "rechazado", "rechazado_baseline_vigente"}


def test_entrenamiento_no_usa_datos_posteriores(entorno):
    resultado = correr(entorno, "2017-03-24", entrenar=True)
    assert resultado["particion"]["validacion"][1] == "2017-03-24"


def test_entrenamiento_respeta_fecha_fin_de_params(entorno):
    entorno["params"]["entrenamiento"]["fecha_fin"] = "2017-03-20"
    resultado = correr(entorno, "2017-03-24", entrenar=True)
    assert resultado["particion"]["validacion"][1] == "2017-03-20"


def test_metricas_calculan_pinball_y_costo():
    df = pd.DataFrame({"sales": [10.0], "family": ["A"]})
    costos = {"defecto": {"costo_quiebre": 3.0, "costo_merma": 1.0}}
    resultado = metricas(df, np.array([[8.0, 8.0, 8.0]]), [0.1, 0.5, 0.9], costos)
    assert resultado["mae"] == pytest.approx(2)
    assert resultado["pinball_p90"] == pytest.approx(1.8)
    assert resultado["costo_negocio"] == pytest.approx(6)


def test_compuerta_reemplaza_solo_si_mejora_el_costo(tmp_path):
    ruta = tmp_path / "modelo_vigente.joblib"
    conf = {"cuantiles": [0.1, 0.5, 0.9]}
    joblib.dump({"tipo": "anterior"}, ruta)

    assert aplicar_compuerta({"tipo": "nuevo"}, 100.0, 100.0, ruta, conf) == "rechazado"
    assert joblib.load(ruta)["tipo"] == "anterior"
    assert aplicar_compuerta({"tipo": "nuevo"}, 120.0, 100.0, ruta, conf) == "rechazado"
    assert joblib.load(ruta)["tipo"] == "anterior"

    assert aplicar_compuerta({"tipo": "nuevo"}, 99.0, 100.0, ruta, conf) == "reemplazado"
    assert joblib.load(ruta)["tipo"] == "nuevo"


def test_sin_vigente_y_candidato_rechazado_queda_el_baseline(tmp_path):
    ruta = tmp_path / "modelo_vigente.joblib"
    conf = {"cuantiles": [0.1, 0.5, 0.9]}
    assert aplicar_compuerta({"tipo": "nuevo"}, 120.0, 100.0, ruta, conf) == "rechazado_baseline_vigente"
    assert joblib.load(ruta)["tipo"] == "baseline"


def test_baseline_vigente_permite_pronosticar(entorno):
    ruta = entorno["rutas"]["modelos"] / "modelo_vigente.joblib"
    ruta.parent.mkdir(parents=True)
    joblib.dump(artefacto_baseline(entorno["params"]["entrenamiento"]), ruta)
    salida = correr(entorno, CORTE)
    assert salida["p10"].equals(salida["p90"])
    assert salida["pedido_sugerido"].to_numpy() == pytest.approx(salida["p50"].to_numpy())


# --- Pronóstico y publicación ---

def test_pronostico_cubre_el_horizonte_con_cuantiles_ordenados(entrenado):
    salida = correr(entrenado, CORTE)
    assert salida["date"].nunique() == 7
    assert salida["date"].min() == pd.Timestamp(CORTE) + pd.Timedelta(days=1)
    assert len(salida) == 7 * 2 * 2
    assert (salida["p10"] >= 0).all()
    assert (salida["p10"] <= salida["p50"]).all() and (salida["p50"] <= salida["p90"]).all()


def test_pedido_interpola_entre_cuantiles_segun_razon_critica(entrenado):
    salida = correr(entrenado, CORTE)
    esperado = salida["p50"] + (0.75 - 0.5) / 0.4 * (salida["p90"] - salida["p50"])
    assert salida["pedido_sugerido"].to_numpy() == pytest.approx(esperado.to_numpy())


def test_publicar_dos_veces_es_idempotente(entrenado):
    rutas = entrenado["rutas"]
    correr(entrenado, CORTE)
    parquet = pd.read_parquet(rutas["publicados"] / "pronosticos_pedidos.parquet")
    filas, corridas = tablas(rutas)

    correr(entrenado, CORTE)
    pd.testing.assert_frame_equal(
        pd.read_parquet(rutas["publicados"] / "pronosticos_pedidos.parquet"), parquet
    )
    filas2, corridas2 = tablas(rutas)
    pd.testing.assert_frame_equal(filas2, filas)
    pd.testing.assert_frame_equal(corridas2, corridas)
    assert len(corridas) == 1


def test_republicar_reemplaza_solo_su_fecha_de_corte(entrenado):
    rutas = entrenado["rutas"]
    correr(entrenado, "2017-03-24")
    correr(entrenado, "2017-03-27")
    correr(entrenado, "2017-03-24")
    parquet = pd.read_parquet(rutas["publicados"] / "pronosticos_pedidos.parquet")
    assert parquet.groupby("fecha_corte").size().tolist() == [28, 28]
    filas, corridas = tablas(rutas)
    assert len(filas) == 56 and len(corridas) == 2
    assert not parquet.duplicated(["fecha_corte", *LLAVES]).any()


def test_fallo_en_publicacion_revierte_ambos_destinos(entrenado, monkeypatch):
    rutas = entrenado["rutas"]
    correr(entrenado, CORTE)
    filas, _ = tablas(rutas)
    parquet = pd.read_parquet(rutas["publicados"] / "pronosticos_pedidos.parquet")

    salida = calcular(CORTE, rutas, entrenado["params"], {
        "defecto": {"costo_quiebre": 9.0, "costo_merma": 1.0}, "familias": {},
    })

    def fallar(*_):
        raise OSError("disco lleno")

    monkeypatch.setattr("inventario.pronostico_pedido.escribir_parquet", fallar)
    with pytest.raises(OSError):
        publicar(salida, rutas["publicados"])

    filas2, _ = tablas(rutas)
    pd.testing.assert_frame_equal(filas2, filas)
    pd.testing.assert_frame_equal(
        pd.read_parquet(rutas["publicados"] / "pronosticos_pedidos.parquet"), parquet
    )


def test_cambiar_costos_cambia_el_pedido_sin_reentrenar(entrenado):
    rutas = entrenado["rutas"]
    ruta = rutas["modelos"] / "modelo_vigente.joblib"
    modelo_antes = ruta.read_bytes()
    base = correr(entrenado, CORTE)

    entrenado["costos"] = {"defecto": {"costo_quiebre": 1.0, "costo_merma": 1.0}, "familias": {}}
    nuevo = correr(entrenado, CORTE)

    assert ruta.read_bytes() == modelo_antes
    assert nuevo["p50"].to_numpy() == pytest.approx(base["p50"].to_numpy())
    assert (nuevo["razon_critica"] == 0.5).all()
    assert nuevo["pedido_sugerido"].to_numpy() == pytest.approx(nuevo["p50"].to_numpy())
    assert (nuevo["pedido_sugerido"] <= base["pedido_sugerido"]).all()
    assert (nuevo["pedido_sugerido"] < base["pedido_sugerido"]).any()


def test_costos_por_familia_afectan_solo_a_esa_familia(entrenado):
    base = correr(entrenado, CORTE)
    entrenado["costos"] = {
        "defecto": {"costo_quiebre": 3.0, "costo_merma": 1.0},
        "familias": {"PRODUCE": {"costo_quiebre": 1.0}},
    }
    nuevo = correr(entrenado, CORTE)
    es_dairy = nuevo["family"] == "DAIRY"
    assert nuevo.loc[es_dairy, "pedido_sugerido"].to_numpy() == pytest.approx(
        base.loc[es_dairy, "pedido_sugerido"].to_numpy()
    )
    assert (nuevo.loc[~es_dairy, "razon_critica"] == 0.5).all()


def test_registra_version_de_codigo_y_de_modelo(entrenado):
    rutas = entrenado["rutas"]
    salida = correr(entrenado, CORTE)
    _, corridas = tablas(rutas)
    fila = corridas.iloc[0]
    assert fila["tipo_modelo"] == "cuantiles"
    assert fila["version_modelo"] == salida["version_modelo"].iloc[0]
    assert fila["version_codigo"]
    assert len(fila["version_modelo"]) == 12


# --- Seguimiento ---

def test_seguimiento_solo_evalua_ventas_ya_conocidas(entrenado):
    rutas = entrenado["rutas"]
    correr(entrenado, CORTE)

    ingerir("2017-03-27", rutas)
    diario = generar("2017-03-27", rutas)
    assert diario["filas_evaluadas"].sum() == 3 * 4
    assert diario["filas_pendientes"].sum() == 4 * 4
    assert diario["date"].max() == pd.Timestamp("2017-03-31")
    assert (diario.loc[diario["date"] > pd.Timestamp("2017-03-27"), "filas_evaluadas"] == 0).all()

    ingerir("2017-03-31", rutas)
    diario = generar("2017-03-31", rutas)
    assert diario["filas_evaluadas"].sum() == 7 * 4
    assert diario["filas_pendientes"].sum() == 0
    assert (rutas["publicados"] / "seguimiento_desempeno.csv").exists()
    texto = (rutas["evidencia"] / "seguimiento_desempeno.md").read_text(encoding="utf-8")
    assert CORTE in texto and "costo_real" in texto


def _publicar_fila(rutas, fecha, **valores):
    fila = pd.DataFrame([{
        "fecha_corte": pd.Timestamp(fecha) - pd.Timedelta(days=1),
        "date": pd.Timestamp(fecha), "store_nbr": 1, "family": "DAIRY",
        "p10": 100.0, "p50": 110.0, "p90": 120.0, "razon_critica": 0.75,
        "costo_quiebre": 3.0, "costo_merma": 1.0, "pedido_sugerido": 115.0,
        "version_modelo": "m", "version_codigo": "c", **valores,
    }])
    rutas["publicados"].mkdir(parents=True, exist_ok=True)
    fila.to_parquet(rutas["publicados"] / "pronosticos_pedidos.parquet", index=False)


def test_seguimiento_calcula_error_y_costo_real(entorno):
    rutas = entorno["rutas"]
    ingerir(CORTE, rutas)
    ventas = pd.read_parquet(rutas["processed"] / "ventas_consolidadas.parquet")
    fecha = "2017-03-22"
    real = ventas[
        (ventas["date"] == fecha) & (ventas["store_nbr"] == 1) & (ventas["family"] == "DAIRY")
    ]["sales"].iloc[0]

    _publicar_fila(rutas, fecha, pedido_sugerido=real - 10)
    diario = generar(CORTE, rutas)
    assert diario["faltantes"].iloc[0] == pytest.approx(10)
    assert diario["costo_real"].iloc[0] == pytest.approx(30)

    _publicar_fila(rutas, fecha, pedido_sugerido=real + 10)
    diario = generar(CORTE, rutas)
    assert diario["sobrantes"].iloc[0] == pytest.approx(10)
    assert diario["costo_real"].iloc[0] == pytest.approx(10)


def test_seguimiento_usa_la_venta_corregida_cuando_llega(entorno):
    rutas = entorno["rutas"]
    _publicar_fila(rutas, FECHA_CORREGIDA)

    ingerir("2017-03-24", rutas)
    antes = cruzar("2017-03-24", rutas)["sales"].iloc[0]
    ingerir(FECHA_CORRECCION, rutas)
    despues = cruzar(FECHA_CORRECCION, rutas)["sales"].iloc[0]
    assert despues == pytest.approx(antes * 2)


def test_seguimiento_exige_ingesta_de_la_misma_fecha(entorno):
    rutas = entorno["rutas"]
    ingerir(CORTE, rutas)
    with pytest.raises(RuntimeError, match="ejecuta la ingesta"):
        generar("2017-03-28", rutas)
