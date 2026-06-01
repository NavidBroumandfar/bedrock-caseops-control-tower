PYTHON ?= python3
SAMPLE_DOC ?= data/sample_documents/fda_warning_letter_01.md
SAMPLE_SOURCE_TYPE ?= FDA
SAMPLE_DOCUMENT_DATE ?= 2026-03-30
SAMPLE_NOTE ?= FDA warning letter - quality system deficiencies

.PHONY: test cli-help doctor check-config intake-sample live-smoke

test:
	$(PYTHON) -m pytest -q

cli-help:
	$(PYTHON) -m app.cli --help

doctor:
	$(PYTHON) -m app.cli doctor

check-config:
	$(PYTHON) -m app.cli check-config

intake-sample:
	$(PYTHON) -m app.cli intake $(SAMPLE_DOC) \
		--source-type $(SAMPLE_SOURCE_TYPE) \
		--document-date $(SAMPLE_DOCUMENT_DATE)

live-smoke:
	$(PYTHON) -m app.cli run $(SAMPLE_DOC) \
		--source-type $(SAMPLE_SOURCE_TYPE) \
		--document-date $(SAMPLE_DOCUMENT_DATE) \
		--submitter-note "$(SAMPLE_NOTE)"
