"""Точка входа: запускает нужную версию оверлея под текущую ОС."""

import platform
import sys


def main():
    system = platform.system()
    if system == "Darwin":
        from . import monitor
        monitor.main()
    elif system == "Windows":
        from . import monitor_win
        monitor_win.main()
    else:
        sys.stderr.write(
            "sysmon-overlay поддерживает только macOS и Windows.\n"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
