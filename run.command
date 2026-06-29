#!/bin/bash
# Двойной клик по этому файлу запускает оверлей.
cd "$(dirname "$0")" || exit 1
exec ./.venv/bin/python monitor.py
