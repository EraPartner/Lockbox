# LockBox convenience targets. Run `make setup` ONCE per fresh clone to enable the
# tracked pre-commit gate (git does not carry core.hooksPath across a clone).
.PHONY: setup sync check audit test help pins pins-check pins-report

help: ## Show this help
	@grep -E '^[a-z][a-zA-Z0-9_-]*:.*## ' $(MAKEFILE_LIST) \
	  | sed 's/:.*## /\t/' | sort | awk -F '\t' '{printf "  %-8s %s\n", $$1, $$2}'

setup: ## Enable the tracked pre-commit hook (run once per clone)
	git config core.hooksPath .githooks
	@echo "core.hooksPath -> .githooks. The leak-audit + drift check now run on every commit."

sync: ## Vendor canonical files + regenerate allowlists into every managed devcontainer
	./sync.sh

check: ## Verify vendored copies, allowlists, and toolchain pins are in sync (no writes)
	./sync.sh --check
	./bump-pins.sh --check

pins-report: ## Show pinned vs cooldown-eligible vs upstream-latest tool versions (network)
	./bump-pins.sh --report

pins-check: ## Assert tool-pins.env matches every Dockerfile's ARG defaults (offline)
	./bump-pins.sh --check

pins: ## Resolve + rehash the newest cooldown-eligible tool versions and rewrite the pins (network)
	./bump-pins.sh --write

audit: ## Scan the git index for hardcoded secrets / private keys
	./audit.sh

test: ## Boot the sandbox image and assert the egress lock enforces (needs a container runtime)
	./test/egress-smoke.sh
