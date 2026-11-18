.PHONY: test build run package
test:
	python3 -m unittest -v
build:
	sh build_desktop.sh
run:
	sh ./agent-orbit panel
package: test build
	python3 scripts/package_release.py
