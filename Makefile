# LockBox convenience targets. Run `make setup` ONCE per fresh clone to enable the
# tracked pre-commit gate (git does not carry core.hooksPath across a clone).
.PHONY: setup sync check audit test help

help: ## Show this help
	@grep -E '^[a-z][a-zA-Z0-9_-]*:.*## ' $(MAKEFILE_LIST) \
	  | sed 's/:.*## /\t/' | sort | awk -F '\t' '{printf "  %-8s %s\n", $$1, $$2}'

setup: ## Enable the tracked pre-commit hook (run once per clone)
	git config core.hooksPath .githooks
	@echo "core.hooksPath -> .githooks. The leak-audit + drift check now run on every commit."

sync: ## Vendor canonical files + regenerate allowlists into every managed devcontainer
	./sync.sh

check: ## Verify vendored copies + generated allowlists are in sync (no writes)
	./sync.sh --check

audit: ## Scan the git index for hardcoded secrets / private keys
	./audit.sh

test: ## Boot the sandbox image and assert the egress lock enforces (needs a container runtime)
	./test/egress-smoke.sh
