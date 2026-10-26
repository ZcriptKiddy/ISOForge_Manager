"""Componentes exclusivamente visuales; reutilizan los métodos del controlador."""
from pathlib import Path
from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPalette, QFont, QFontDatabase
from PySide6.QtWidgets import QAbstractItemView, QTreeWidget, QProgressBar


class ContentTree(QTreeWidget):
    paths_dropped = Signal(list)

    def __init__(self, empty_text='', allow_drop=False, parent=None):
        super().__init__(parent)
        self.empty_text = empty_text
        self.setAcceptDrops(allow_drop)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly if allow_drop
                             else QAbstractItemView.DragDropMode.NoDragDrop)
        self.setMinimumHeight(110)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.topLevelItemCount():
            painter = QPainter(self.viewport())
            painter.setPen(QColor('#7b879c'))
            painter.drawText(self.viewport().rect().adjusted(16, 8, -16, -8),
                             Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                             self.empty_text)

    def dragEnterEvent(self, event):
        if self.isEnabled() and event.mimeData().hasUrls() and all(
                url.isLocalFile() for url in event.mimeData().urls()):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        if not self.isEnabled() or not event.mimeData().hasUrls():
            event.ignore()
            return
        urls = event.mimeData().urls()
        if not all(url.isLocalFile() for url in urls):
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        self.paths_dropped.emit([url.toLocalFile() for url in urls])


class DropAdapter(QObject):
    """Entrega cada ruta al mismo método que utilizan los selectores existentes."""
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        for panel in (window.permanent_panel, window.variable_panel):
            panel.tree.paths_dropped.connect(
                lambda paths, target=panel: self.add_paths(paths, target))

    def add_paths(self, paths, target):
        if self.window.worker_thread is not None or not target.isEnabled():
            return
        for path in paths:
            self.window._add_sources([path], 'directory' if Path(path).is_dir() else 'file', target)


def occupancy_bar(used, capacity, parent=None):
    bar = QProgressBar(parent)
    bar.setObjectName('occupancyBar')
    bar.setRange(0, 1000)
    ratio = used / capacity if capacity else 0
    bar.setValue(min(1000, max(0, round(ratio * 1000))))
    bar.setFormat(f'{ratio:.1%}')
    bar.setToolTip(f'Ocupación estimada: {used:,} de {capacity:,} bytes; incluye metadatos.')
    bar.setMinimumWidth(120)
    bar.setMaximumHeight(22)
    bar.setAccessibleName('Ocupación estimada del ISO')
    return bar


STYLE = '''
QWidget { background: #ffffff; color: #25334b; font-size: 10pt; }
QMainWindow, QWidget#centralSurface { background: #f1f4f9; }
QLabel { background: transparent; border: none; }
QLabel#appTitle { font-size: 23pt; font-weight: 700; color: #1b2b48; }
QLabel#appSubtitle, QLabel#muted { color: #6d7a91; }
QLabel#cardTitle { font-size: 12pt; font-weight: 700; color: #253554; }
QLabel#fieldLabel { color: #53627b; font-weight: 600; }
QLabel#pill { background: #edf1fd; color: #5366b1; border-radius: 10px; padding: 5px 10px; }
QLabel#statusBadge { background: #e4f3ed; color: #26745b; border-radius: 12px; padding: 7px 12px; font-size: 9pt; font-weight: 600; }
QFrame#sourceCard, QFrame#configCard, QFrame#previewCard, QFrame#metricCard {
 background: white; border: 1px solid #dfe5ef; border-radius: 14px;
}
QFrame#metricCard { background: #f9fbff; }
QLabel#metricTitle { color: #75829a; font-size: 8pt; font-weight: 600; }
QLabel#metricValue { color: #354e85; font-size: 17pt; font-weight: 700; }
QTreeWidget { background: #ffffff; alternate-background-color: #f7f9fd; border: 1px solid #e7ecf4; border-radius: 8px; outline: none; selection-color: #253f82; }
QTreeWidget::item { min-height: 30px; padding: 3px; }
QTreeWidget::item:selected { background: #e3ebff; color: #234484; }
QTreeWidget::item:hover { background: #edf3ff; }
QHeaderView::section { background: #f5f7fb; color: #748198; border: none; border-bottom: 1px solid #e5eaf3; padding: 8px; font-size: 9pt; font-weight: 600; }
QPushButton { background: #f4f6fc; color: #40567e; border: 1px solid #dce3ef; border-radius: 8px; padding: 8px 13px; font-weight: 600; min-height: 18px; }
QPushButton:hover { background: #e8eefc; border-color: #acbde1; }
QPushButton:pressed { background: #dbe5fa; }
QPushButton:focus { border: 2px solid #7a96e5; }
QPushButton#primaryButton { background: #536bd8; color: white; border-color: #536bd8; padding: 9px 20px; }
QPushButton#primaryButton:hover { background: #4359c1; }
QPushButton#dangerButton { color: #b45262; background: #fff2f3; border-color: #f1d7dc; }
QPushButton:disabled, QPushButton#primaryButton:disabled, QPushButton#dangerButton:disabled { color: #9ba5b8; background: #edf0f5; border-color: #e4e8f0; }
QLineEdit, QComboBox, QDoubleSpinBox { background: #f9fbfe; color: #314362; border: 1px solid #d9e1ed; border-radius: 8px; padding: 8px; min-height: 18px; }
QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus { border-color: #7189df; }
QComboBox QAbstractItemView { background: white; color: #314362; selection-background-color: #e3ebff; selection-color: #234484; }
QProgressBar { background: #e7ecf7; border: none; border-radius: 7px; min-height: 18px; text-align: center; color: #243965; font-size: 9pt; }
QProgressBar::chunk { background: #99b1ed; border-radius: 7px; }
QProgressBar#occupancyBar { background: #e9edf8; margin: 3px 8px; }
QProgressBar#occupancyBar::chunk { background: #a4b6ef; }
QSplitter::handle { background: transparent; width: 10px; height: 10px; }
QScrollBar:vertical { background: #f1f4fa; width: 10px; margin: 0px; }
QScrollBar::handle:vertical { background: #c9d3e5; border-radius: 5px; min-height: 28px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QToolTip { background: white; color: #25334b; padding: 6px; border: 1px solid #d9e1ed; }
QMessageBox, QDialog, QFileDialog { background: white; color: #25334b; }
QScrollArea { border: none; background: #f1f4f9; }
'''


class ResponsiveAdapter(QObject):
    """Reorganiza solo la presentación según el ancho disponible."""
    def __init__(self, scroll, splitter, metrics, note):
        super().__init__(scroll)
        self.scroll, self.splitter, self.metrics, self.note = scroll, splitter, metrics, note
        self.cards = [metrics.itemAt(i).widget() for i in range(metrics.count())]
        self.compact = None
        scroll.viewport().installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize:
            compact = watched.width() < 1050
            if compact != self.compact:
                self.compact = compact
                self.splitter.setOrientation(Qt.Orientation.Vertical if compact else Qt.Orientation.Horizontal)
                self.note.setVisible(not compact)
                for card in self.cards:
                    self.metrics.removeWidget(card)
                columns = 3 if compact else 5
                for i, card in enumerate(self.cards):
                    self.metrics.addWidget(card, i // columns, i % columns)
        return super().eventFilter(watched, event)


class LightWindowFrames(QObject):
    """Pide bordes y títulos claros a Windows sin cambiar la configuración del sistema."""
    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Show and hasattr(watched, 'isWindow') and watched.isWindow():
            import sys
            if sys.platform == 'win32':
                import ctypes
                from ctypes import wintypes
                try:
                    dwm = ctypes.WinDLL('dwmapi')
                    setter = dwm.DwmSetWindowAttribute
                    setter.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
                    setter.restype = ctypes.c_long
                    hwnd = int(watched.winId())
                    disabled = wintypes.BOOL(False)
                    setter(hwnd, 20, ctypes.byref(disabled), ctypes.sizeof(disabled))
                    background = wintypes.DWORD(0x00FFFFFF)
                    text_color = wintypes.DWORD(0x004B3325)
                    setter(hwnd, 35, ctypes.byref(background), ctypes.sizeof(background))
                    setter(hwnd, 36, ctypes.byref(text_color), ctypes.sizeof(text_color))
                except (OSError, AttributeError):
                    pass
        return super().eventFilter(watched, event)


def configure_light_theme(app):
    app.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, True)
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    families = set(QFontDatabase.families())
    family = next((name for name in ('Segoe UI Variable Text', 'Aptos', 'Bahnschrift', 'Segoe UI')
                   if name in families), app.font().family())
    app.setFont(QFont(family, 10))
    palette = QPalette()
    colors = {
        QPalette.ColorRole.Window: '#ffffff', QPalette.ColorRole.Base: '#ffffff',
        QPalette.ColorRole.AlternateBase: '#f5f7fb', QPalette.ColorRole.WindowText: '#25334b',
        QPalette.ColorRole.Text: '#25334b', QPalette.ColorRole.Button: '#f4f6fc',
        QPalette.ColorRole.ButtonText: '#25334b', QPalette.ColorRole.ToolTipBase: '#ffffff',
        QPalette.ColorRole.ToolTipText: '#25334b', QPalette.ColorRole.Highlight: '#dfe8ff',
        QPalette.ColorRole.HighlightedText: '#25334b', QPalette.ColorRole.PlaceholderText: '#748198',
    }
    for role, color in colors.items():
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    app.setStyleSheet(STYLE)
    app._light_frames = LightWindowFrames(app)
    app.installEventFilter(app._light_frames)
