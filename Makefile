PY := .venv/bin/python
export PYTHONPATH := src

# LOGICAL_DATE: fecha de corte de una corrida.
# FECHA_ENTRENAMIENTO: corte del modelo; CORTES: fechas que publica backfill.
LOGICAL_DATE ?= 2017-08-15
FECHA_ENTRENAMIENTO ?= 2017-07-31
CORTES ?= 2017-07-31 2017-08-01 2017-08-02 2017-08-08 2017-08-15

.PHONY: instalar test datos ingesta features entrenar pronostico seguimiento corrida \
	entrenamiento_inicial backfill todo versionar entrenar-historial \
	airflow-env airflow-up airflow-down

instalar:
	python3 -m venv .venv
	.venv/bin/pip install -q -r requirements.txt -r requirements-dvc.txt

test:
	.venv/bin/pytest -q

# Regenera data/hist y data/bandeja a partir de data/kaggle_raw.
datos:
	$(PY) scripts/simular_sistema_origen.py --sobrescribir

ingesta:
	$(PY) -m inventario.ingesta_validacion --logical-date $(LOGICAL_DATE)

features: ingesta
	$(PY) -m inventario.preparacion --logical-date $(LOGICAL_DATE)

entrenar: features
	$(PY) -m inventario.entrenar_comparar --logical-date $(LOGICAL_DATE)

pronostico: features
	$(PY) -m inventario.pronostico_pedido --logical-date $(LOGICAL_DATE)

seguimiento: ingesta
	$(PY) -m inventario.seguimiento --logical-date $(LOGICAL_DATE)

# Corrida diaria con el modelo vigente, sin reentrenar.
corrida: pronostico
	$(PY) -m inventario.seguimiento --logical-date $(LOGICAL_DATE)

entrenamiento_inicial:
	$(MAKE) entrenar LOGICAL_DATE=$(FECHA_ENTRENAMIENTO)

backfill:
	@for fecha in $(CORTES); do \
		$(MAKE) --no-print-directory corrida LOGICAL_DATE=$$fecha || exit 1; \
	done

todo: entrenamiento_inicial backfill

versionar:
	.venv/bin/dvc add data/processed data/publicados models
	.venv/bin/dvc push

# Entrena un modelo para reprocesar fechas pasadas, sin tocar el vigente.
entrenar-historial: features
	$(PY) -m inventario.entrenar_comparar --logical-date $(LOGICAL_DATE) --solo-historial

COMPOSE := docker compose -f airflow/docker-compose.yaml

airflow-env:
	@test -f airflow/.env || { \
		echo "AIRFLOW_UID=$$(id -u)"; \
		echo "POSTGRES_PASSWORD=$$(openssl rand -hex 24)"; \
		echo "AIRFLOW_JWT_SECRET=$$(openssl rand -hex 32)"; \
	} > airflow/.env

airflow-up: airflow-env
	mkdir -p airflow/logs
	$(COMPOSE) up -d --wait airflow-apiserver airflow-scheduler airflow-dag-processor

airflow-down:
	$(COMPOSE) down
