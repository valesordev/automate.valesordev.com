SHELL      := bash
.SHELLFLAGS := -euo pipefail -c

.PHONY: help test

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-13s %s\n", $$1, $$2}'

test: ## Run the toolkit's tests against fixture repos
	python3 -m unittest discover -s toolkit/tests -v
