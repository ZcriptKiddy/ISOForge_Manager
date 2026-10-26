"""Punto de entrada de Generador ISO Múltiple."""

from __future__ import annotations

from pathlib import Path
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from gui import MainWindow
from ui_components import configure_light_theme


def main() -> int:
    if sys.version_info < (3, 12):
        print("Generador ISO Múltiple requiere Python 3.12 o posterior.")
        return 1

    app = QApplication(sys.argv)
    app.setApplicationName("Generador ISO Múltiple")
    app.setOrganizationName("Generador_ISO_Multiple")
    app.setStyle("Fusion")
    configure_light_theme(app)

    icon_path = Path(__file__).resolve().parent / "assets" / "icons" / "app.svg"
    app.setWindowIcon(QIcon(str(icon_path)))

    try:
        window = MainWindow()
        window.show()
        return app.exec()
    except Exception as exc:  # Guardia final para fallos de inicialización de Qt.
        QMessageBox.critical(None, "Error de inicio", str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
