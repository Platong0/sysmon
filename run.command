#!/bin/bash
# Двойной клик по этому файлу запускает оверлей (из исходников).
cd "$(dirname "$0")" || exit 1
PYTHONPATH=src exec ./.venv/bin/python -m sysmon_overlay
