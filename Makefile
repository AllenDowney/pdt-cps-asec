PYTHON ?= python
# A PublicDataToolkit checkout. Or `pip install -e $(PDT)` and run `pdt check`.
PDT ?= ../PublicDataToolkit

.PHONY: download data build views check

## Fetch the sources into data/raw. Read the publisher's terms first, then: make download ARGS=--accept-terms
download:
	PYTHONPATH=$(PDT)/lib $(PYTHON) scripts/download.py $(ARGS)

## Write the common-format parquet files in data/processed (STANDARD.md §8)
data:
	PYTHONPATH=$(PDT)/lib $(PYTHON) scripts/build_data.py

## Build metadata records from data/raw (build/build_records.py writes build/report.md)
build:
	PYTHONPATH=$(PDT)/lib $(PYTHON) build/build_records.py

## Generate agent/, docs/ and export/ from the records (pdt views)
views:
	PYTHONPATH=$(PDT)/lib $(PYTHON) -m pdt views .

## Check the repo against the PublicDataToolkit standard (pdt check)
check:
	PYTHONPATH=$(PDT)/lib $(PYTHON) -m pdt check .
