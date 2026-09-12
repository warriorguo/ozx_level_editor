APP_NAME    := OZX Level Studio
APP_BUNDLE  := build/$(APP_NAME).app
EXEC_NAME   := LevelStudio
SWIFT_BUILD := .build/release/$(EXEC_NAME)
ICON_OUT     := Resources/AppIcon.icns
ICON_STAGING := .build/AppIcon.iconset
ICON_MASTER  := .build/icon-master-1024.png
ICON_COMPACT := .build/icon-compact-1024.png
REPO        := ..
INSTALL_DIR := /Applications

.PHONY: help build run install clean swift bundle icon

help: ## Show targets
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-10s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

build: bundle ## Build the .app bundle (default)

swift: ## Build the Swift release binary
	swift build -c release

# Unlike the Go-backed sibling app there is nothing to compile on the server
# side: serve.py runs on the system python3, so the sources are copied in as-is.
# That also means editing the Python inside the installed .app takes effect on
# the next launch without rebuilding.
bundle: swift ## Assemble the .app bundle
	@echo "Assembling $(APP_BUNDLE)..."
	@rm -rf "$(APP_BUNDLE)"
	@mkdir -p "$(APP_BUNDLE)/Contents/MacOS"
	@mkdir -p "$(APP_BUNDLE)/Contents/Resources"
	@cp "$(SWIFT_BUILD)" "$(APP_BUNDLE)/Contents/MacOS/$(EXEC_NAME)"
	@cp "$(REPO)/serve.py" "$(APP_BUNDLE)/Contents/Resources/serve.py"
	@# --exclude keeps __pycache__ and test artifacts out of the shipped bundle
	@rsync -a --exclude '__pycache__' --exclude '*.pyc' \
		"$(REPO)/ozxlevel" "$(APP_BUNDLE)/Contents/Resources/"
	@rsync -a "$(REPO)/web" "$(APP_BUNDLE)/Contents/Resources/"
	@if [ -f Resources/AppIcon.icns ]; then \
		cp Resources/AppIcon.icns "$(APP_BUNDLE)/Contents/Resources/AppIcon.icns"; \
	else echo "note: Resources/AppIcon.icns missing — app will use the default icon"; fi
	@cp Resources/Info.plist "$(APP_BUNDLE)/Contents/Info.plist"
	@chmod +x "$(APP_BUNDLE)/Contents/MacOS/$(EXEC_NAME)"
	@echo "Built $(APP_BUNDLE) ($$(du -sh '$(APP_BUNDLE)' | cut -f1))"

run: build ## Build and launch
	open "$(APP_BUNDLE)"

# `ditto` preserves the source mtime, so a stale destination can look fresh.
# Remove it first rather than trusting the bundle directory's timestamp.
install: build ## Install to /Applications
	@rm -rf "$(INSTALL_DIR)/$(APP_NAME).app"
	@ditto "$(APP_BUNDLE)" "$(INSTALL_DIR)/$(APP_NAME).app"
	@echo "Installed to $(INSTALL_DIR)/$(APP_NAME).app"

# The .icns is checked in so `make build` works without regenerating it; run
# this only when the mark itself changes.
icon: ## Regenerate Resources/AppIcon.icns
	@mkdir -p .build "$(ICON_STAGING)" Resources
	@swift tools/MakeIcon.swift "$(ICON_MASTER)"
	@swift tools/MakeIcon.swift "$(ICON_COMPACT)" --compact
	@# 16 and 32 get the compact artwork: the detailed mark turns to mush
	@# below ~48px, and macOS takes different art per slot precisely for this.
	@for s in 16 32; do \
	  s2=$$((s * 2)); \
	  sips -z $$s  $$s  "$(ICON_COMPACT)" --out "$(ICON_STAGING)/icon_$${s}x$${s}.png"    >/dev/null; \
	  sips -z $$s2 $$s2 "$(ICON_COMPACT)" --out "$(ICON_STAGING)/icon_$${s}x$${s}@2x.png" >/dev/null; \
	done
	@for s in 128 256 512; do \
	  s2=$$((s * 2)); \
	  sips -z $$s  $$s  "$(ICON_MASTER)" --out "$(ICON_STAGING)/icon_$${s}x$${s}.png"    >/dev/null; \
	  sips -z $$s2 $$s2 "$(ICON_MASTER)" --out "$(ICON_STAGING)/icon_$${s}x$${s}@2x.png" >/dev/null; \
	done
	@cp "$(ICON_MASTER)" "$(ICON_STAGING)/icon_512x512@2x.png"
	@iconutil --convert icns "$(ICON_STAGING)" --output "$(ICON_OUT)"
	@rm -rf "$(ICON_STAGING)" "$(ICON_MASTER)" "$(ICON_COMPACT)"
	@echo "Built $(ICON_OUT) ($$(du -h '$(ICON_OUT)' | cut -f1))"

clean: ## Remove build artifacts
	rm -rf build .build
