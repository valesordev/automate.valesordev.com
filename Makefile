SHELL      := bash
.SHELLFLAGS := -euo pipefail -c
PLUGINS    := $(notdir $(wildcard plugins/*))
DIST       := dist
TK         := python3 toolkit

.PHONY: help validate check-render build package package-done install check-target docs-export mirror cutover test dev clean

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-13s %s\n", $$1, $$2}'

validate: check-render ## claude plugin validate + authoring conventions (examples, skill limits, assist levels, README tables)
	claude plugin validate .
	@for p in $(PLUGINS); do claude plugin validate plugins/$$p; done
	@$(TK)/check_conventions.py --root $(CURDIR)

check-render: ## Fail if any plugins/*/src/** render is stale
	@for p in $(PLUGINS); do $(TK)/render.py --root $(CURDIR) --check plugins/$$p; done

build: ## Render plugins/*/src/** in place, and instructions/* into dist/<plugin>/
	@for p in $(PLUGINS); do $(TK)/render.py --root $(CURDIR) --tree plugins/$$p; done
	@for f in $(wildcard plugins/*/instructions/*); do \
	  p=$$(echo $$f | cut -d/ -f2); out=$(DIST)/$$p/$$(basename $${f%.tmpl}); \
	  $(TK)/render.py --root $(CURDIR) $$f $$out && echo "  $$out"; \
	done

package: ## Build, zip changed skills to dist/skills/ (ALL=1 for everything)
	@$(MAKE) -s build >/dev/null
	@$(TK)/package.py --root $(CURDIR) $(if $(ALL),--all)

package-done: ## Record the current skills as uploaded to claude.ai
	@$(TK)/package.py --root $(CURDIR) --done

install: ## Vendor a target into a repo's .claude/: make install TARGET=solo7-media DEST=../solo7.media
	@test -n "$(TARGET)" -a -n "$(DEST)" || { echo "usage: make install TARGET=<targets/ name> DEST=<repo path>"; exit 1; }
	$(TK)/install.py --root $(CURDIR) $(TARGET) $(DEST)

check-target: ## Report drift between a target repo and this repo: make check-target TARGET=… DEST=…
	@test -n "$(TARGET)" -a -n "$(DEST)" || { echo "usage: make check-target TARGET=<name> DEST=<repo path>"; exit 1; }
	$(TK)/install.py --root $(CURDIR) --check $(TARGET) $(DEST)

docs-export: ## Export docs/ to a site repo as content/<repo>/: make docs-export SITE=../solo7.media [DRY=1]
	@test -n "$(SITE)" || { echo "usage: make docs-export SITE=<site repo path> [DRY=1]"; exit 1; }
	$(TK)/export_docs.py --root $(CURDIR) $(if $(DRY),--dry-run) $(SITE)

mirror: ## Mirror a repo's story files into its GitHub Project (dry run; APPLY=1 writes): make mirror DEST=… [TARGET=…]
	@test -n "$(DEST)" || { echo "usage: make mirror DEST=<repo path> [TARGET=<t> for its _repo.md settings] [REF=origin/main] [REPO=owner/name] [PROJECT=title] [APPLY=1|OFFLINE=1]"; exit 1; }
	git -C $(DEST) fetch -q origin
	python3 plugins/valesor-dev/bin/project-mirror $(DEST) $(if $(TARGET),--config targets/$(TARGET)/roles/_repo.md) $(if $(REF),--ref $(REF)) $(if $(REPO),--repo $(REPO)) $(if $(PROJECT),--project "$(PROJECT)") $(if $(APPLY),--apply) $(if $(OFFLINE),--offline)

cutover: ## Hand a repo's board from story files to GitHub (dry run; APPLY=1 writes): make cutover DEST=… [REF=stories-final]
	@test -n "$(DEST)" || { echo "usage: make cutover DEST=<repo path> [REF=<pushed tag>, default stories-final] [APPLY=1]"; exit 1; }
	git -C $(DEST) fetch -q --tags origin
	python3 plugins/valesor-dev/bin/project-mirror $(DEST) --cutover --ref $(or $(REF),stories-final) $(if $(APPLY),--apply)

test: ## Run the toolkit's fixture tests, then the plugin and target tests
	python3 -m unittest discover -s toolkit/tests -v
	python3 -m unittest discover -s tests -v

dev: ## Register this checkout as local marketplace "valesordev" (plugins load in place)
	claude plugin marketplace add $(CURDIR)

clean: ## Remove build output
	rm -rf $(DIST)
