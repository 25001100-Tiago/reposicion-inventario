PY := .venv/bin/python
export PYTHONPATH := src

# LOGICAL_DATE: fecha de corte de una corrida.
# FECHA_ENTRENAMIENTO: corte del modelo; CORTES: fechas que publica backfill.
LOGICAL_DATE ?= 2017-08-15
FECHA_ENTRENAMIENTO ?= 2017-07-31
CORTES ?= 2017-07-31 2017-08-01 2017-08-02 2017-08-08 2017-08-15

.PHONY: instalar test ingesta features entrenar pronostico seguimiento corrida \
	entrenamiento_inicial backfill todo versionar

instalar:
	python3 -m venv .venv
	.venv/bin/pip install -q -r requirements.txt -r requirements-dvc.txt

test:
	.venv/bin/pytest -q

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
