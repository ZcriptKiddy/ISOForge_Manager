"""Interfaz PySide6 moderna para distribuir y generar múltiples ISO."""

from __future__ import annotations

from pathlib import Path
import threading

from PySide6.QtCore import QObject, QSize, Qt, QThread, Signal, Slot, QStandardPaths
from PySide6.QtGui import QCloseEvent, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QScrollArea,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from file_utils import (
    SourceError,
    SourceItem,
    create_source_item,
    format_size,
    gb_to_bytes,
    normalized_path_key,
    refresh_source_items,
)
from iso_builder import BuildCancelled, build_iso_set
from packing import FIXED_ISO_OVERHEAD, PackingError, PackingPlan, create_packing_plan


from ui_components import ContentTree, DropAdapter, ResponsiveAdapter, STYLE


from distribution_view import DistributionDialog, StoredMetric, IsoCountPanel


BASE_DIR = Path(__file__).resolve().parent
ICON_DIR = BASE_DIR / "assets" / "icons"
FILE_DIALOG_START_DIR = "C:/Users/Admin/Desktop"


def icon(name: str) -> QIcon:
    return QIcon(str(ICON_DIR / f"{name}.svg"))


class SourcePanel(QFrame):
    """Lista reutilizable de fuentes permanentes o variables."""

    items_changed = Signal()

    def __init__(self, permanent: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.permanent = permanent
        self._items: list[SourceItem] = []
        self.setObjectName("sourceCard")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        heading = QHBoxLayout()
        title_block = QVBoxLayout()
        title = QLabel("Contenido fijo" if permanent else "Contenido variable")
        title.setObjectName("cardTitle")
        subtitle = QLabel(
            "Se incluye en todos los ISO"
            if permanent
            else "Sus archivos se distribuyen entre ISO"
        )
        subtitle.setObjectName("muted")
        title_block.addWidget(title)
        title_block.addWidget(subtitle)
        heading.addLayout(title_block)
        heading.addStretch()
        self.count_label = QLabel("0 elementos")
        self.count_label.setObjectName("pill")
        heading.addWidget(self.count_label)
        layout.addLayout(heading)

        self.tree = ContentTree("Arrastra archivos o carpetas aquí\nTambién puedes usar los botones de abajo", allow_drop=True)
        self.tree.setHeaderLabels(["Nombre", "Tipo", "Tamaño"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setUniformRowHeights(True)
        self.tree.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self.tree.setMinimumHeight(270)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, self.tree.header().ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, self.tree.header().ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, self.tree.header().ResizeMode.ResizeToContents)
        layout.addWidget(self.tree, 1)

        buttons = QHBoxLayout()
        prefix = "permanentes" if permanent else "variables"
        self.add_files_button = QPushButton("Agregar archivos")
        self.add_files_button.setIcon(icon("file"))
        self.add_folders_button = QPushButton("Agregar carpetas")
        self.add_folders_button.setIcon(icon("folder"))
        self.remove_button = QPushButton("Quitar")
        self.remove_button.setObjectName("secondaryButton")
        self.remove_button.setIcon(icon("trash"))
        self.remove_button.clicked.connect(self.remove_selected)
        buttons.addWidget(self.add_files_button)
        buttons.addWidget(self.add_folders_button)
        buttons.addStretch()
        buttons.addWidget(self.remove_button)
        layout.addLayout(buttons)

    @property
    def items(self) -> list[SourceItem]:
        return list(self._items)

    def add_source(self, item: SourceItem) -> None:
        self._items.append(item)
        self._append_row(item)
        self._update_count()
        self.items_changed.emit()

    def replace_sources(self, items: list[SourceItem]) -> None:
        self._items = list(items)
        self.tree.clear()
        for item in self._items:
            self._append_row(item)
        self._update_count()

    def _append_row(self, source: SourceItem) -> None:
        kind = "Carpeta" if source.is_directory else "Archivo"
        row = QTreeWidgetItem([source.display_name, kind, format_size(source.size_bytes)])
        row.setData(0, Qt.ItemDataRole.UserRole, normalized_path_key(source.path))
        row.setToolTip(0, str(source.path))
        row.setIcon(0, icon("folder" if source.is_directory else "file"))
        self.tree.addTopLevelItem(row)

    @Slot()
    def remove_selected(self) -> None:
        keys = {
            row.data(0, Qt.ItemDataRole.UserRole) for row in self.tree.selectedItems()
        }
        if not keys:
            return
        self._items = [
            item for item in self._items if normalized_path_key(item.path) not in keys
        ]
        self.replace_sources(self._items)
        self.items_changed.emit()

    def _update_count(self) -> None:
        count = len(self._items)
        self.count_label.setText(f"{count} elemento" if count == 1 else f"{count} elementos")


class MetricCard(QFrame):
    def __init__(self, title: str, value: str = "—", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("metricCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(3)
        label = QLabel(title)
        label.setObjectName("metricTitle")
        self.value_label = QLabel(value)
        self.value_label.setObjectName("metricValue")
        layout.addWidget(label)
        layout.addWidget(self.value_label)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)


class IsoWorker(QObject):
    progress_changed = Signal(int, str)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, plan: PackingPlan, output_directory: Path) -> None:
        super().__init__()
        self.plan = plan
        self.output_directory = output_directory
        self.cancel_event = threading.Event()

    @Slot()
    def run(self) -> None:
        try:
            paths = build_iso_set(
                self.plan,
                self.output_directory,
                progress=self.progress_changed.emit,
                cancel_event=self.cancel_event,
            )
        except BuildCancelled as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.completed.emit(paths)

    def cancel(self) -> None:
        self.cancel_event.set()


class MainWindow(QMainWindow):
    """Ventana principal; coordina validación, vista previa y worker ISO."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Generador ISO Múltiple")
        self.setWindowIcon(icon("app"))
        self.resize(1280, 900)
        self.setMinimumSize(820, 620)

        self.current_plan: PackingPlan | None = None
        self.worker: IsoWorker | None = None
        self.worker_thread: QThread | None = None

        self._build_ui()
        self._apply_style()
        self._wire_events()
        self._drop_adapter = DropAdapter(self)
        self._capacity_changed()

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("centralSurface")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(central)
        self.setCentralWidget(scroll)
        root = QVBoxLayout(central)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        header = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(icon("app").pixmap(QSize(48, 48)))
        header.addWidget(logo)
        title_box = QVBoxLayout()
        title = QLabel("Generador ISO Múltiple")
        title.setObjectName("appTitle")
        subtitle = QLabel("Organiza tu contenido y crea tus imágenes ISO.")
        subtitle.setObjectName("appSubtitle")
        subtitle.setWordWrap(True)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box, 1)
        header.addStretch()
        root.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.permanent_panel = SourcePanel(permanent=True)
        self.variable_panel = SourcePanel(permanent=False)
        splitter.addWidget(self.permanent_panel)
        splitter.addWidget(self.variable_panel)
        splitter.setSizes([650, 650])
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 5)

        config_card = QFrame()
        config_card.setObjectName("configCard")
        config_outer = QVBoxLayout(config_card)
        config_outer.setContentsMargins(18, 16, 18, 16)
        config_outer.setSpacing(14)
        panel_title = QLabel("Configuración y resumen")
        panel_title.setObjectName("cardTitle")
        config_outer.addWidget(panel_title)
        config = QHBoxLayout()
        config_outer.addLayout(config)
        config.setContentsMargins(0, 0, 0, 0)
        config.setSpacing(10)

        capacity_label = QLabel("Capacidad")
        capacity_label.setObjectName("fieldLabel")
        config.addWidget(capacity_label)
        self.capacity_combo = QComboBox()
        for value in (3.5, 3.7, 4.0, 4.2):
            self.capacity_combo.addItem(f"{value:.1f} GB", value)
        self.capacity_combo.addItem("Personalizada", None)
        self.capacity_combo.setCurrentIndex(3)
        config.addWidget(self.capacity_combo)
        self.custom_capacity = QDoubleSpinBox()
        self.custom_capacity.setRange(0.01, 999.0)
        self.custom_capacity.setDecimals(2)
        self.custom_capacity.setSingleStep(0.1)
        self.custom_capacity.setValue(4.2)
        self.custom_capacity.setSuffix(" GB")
        self.custom_capacity.setVisible(False)
        config.addWidget(self.custom_capacity)

        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setObjectName("separator")
        config.addWidget(separator)

        output_label = QLabel("Salida")
        output_label.setObjectName("fieldLabel")
        config.addWidget(output_label)
        self.output_edit = QLineEdit()
        self.output_edit.setReadOnly(True)
        self.output_edit.setText(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation))
        self.output_edit.setPlaceholderText("Selecciona la carpeta donde se crearán ISO01.iso, ISO02.iso…")
        self.output_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        config.addWidget(self.output_edit, 1)
        self.output_button = QPushButton("Elegir carpeta")
        self.output_button.setObjectName("secondaryButton")
        self.output_button.setIcon(icon("output"))
        config.addWidget(self.output_button)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        action_note = QLabel("01  Agrega contenido    /    02  Calcula    /    03  Genera")
        action_note.setObjectName("muted")
        actions.addWidget(action_note)
        actions.addStretch()
        self.distribution_button = QPushButton("Ver distribución")
        self.distribution_button.setIcon(icon("disc"))
        self.distribution_button.setEnabled(False)
        self.distribution_button.clicked.connect(self._show_distribution)
        actions.addWidget(self.distribution_button)
        self.calculate_button = QPushButton("Calcular distribución")
        self.calculate_button.setIcon(icon("calculator"))
        actions.addWidget(self.calculate_button)
        self.generate_button = QPushButton("Generar ISO")
        self.generate_button.setObjectName("primaryButton")
        self.generate_button.setIcon(icon("play"))
        self.generate_button.setEnabled(False)
        actions.addWidget(self.generate_button)

        metrics = QGridLayout()
        metrics.setSpacing(10)
        self.metric_permanent = StoredMetric()
        self.metric_variable = StoredMetric()
        self.metric_capacity = StoredMetric()
        self.metric_free = StoredMetric()
        self.metric_count = IsoCountPanel()
        metrics.addWidget(self.metric_count, 0, 0)
        config_outer.addLayout(metrics)
        config_outer.addLayout(actions)
        root.addWidget(config_card)

        progress_row = QHBoxLayout()
        self.status_label = QLabel("Selecciona los contenidos para comenzar.")
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.cancel_button = QPushButton("Abortar proceso")
        self.cancel_button.setObjectName("dangerButton")
        self.cancel_button.setEnabled(False)
        progress_row.addWidget(self.status_label, 2)
        progress_row.addWidget(self.progress_bar, 3)
        progress_row.addWidget(self.cancel_button)
        root.addLayout(progress_row)
        for label in central.findChildren(QLabel):
            label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._responsive = ResponsiveAdapter(scroll, splitter, metrics, action_note)

    def _wire_events(self) -> None:
        self.permanent_panel.items_changed.connect(self._sources_changed)
        self.variable_panel.items_changed.connect(self._sources_changed)
        self.permanent_panel.add_files_button.clicked.connect(
            lambda: self._choose_files(self.permanent_panel)
        )
        self.variable_panel.add_files_button.clicked.connect(
            lambda: self._choose_files(self.variable_panel)
        )
        self.permanent_panel.add_folders_button.clicked.connect(
            lambda: self._choose_folder(self.permanent_panel)
        )
        self.variable_panel.add_folders_button.clicked.connect(
            lambda: self._choose_folder(self.variable_panel)
        )
        self.capacity_combo.currentIndexChanged.connect(self._capacity_changed)
        self.custom_capacity.valueChanged.connect(self._sources_changed)
        self.output_button.clicked.connect(self._choose_output)
        self.calculate_button.clicked.connect(self._calculate_plan)
        self.generate_button.clicked.connect(self._start_generation)
        self.cancel_button.clicked.connect(self._cancel_generation)

    def _choose_files(self, target: SourcePanel) -> None:
        paths, _selected_filter = QFileDialog.getOpenFileNames(
            self, "Seleccionar archivos", FILE_DIALOG_START_DIR
        )
        if paths:
            self._add_sources(paths, "file", target)

    def _choose_folder(self, target: SourcePanel) -> None:
        path = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta", FILE_DIALOG_START_DIR)
        if path:
            self._add_sources([path], "directory", target)

    def _add_sources(self, paths: list[str], kind: str, target: SourcePanel) -> None:
        current = [*self.permanent_panel.items, *self.variable_panel.items]
        existing_paths = {normalized_path_key(item.path) for item in current}
        existing_names = {item.display_name.casefold() for item in current}
        errors: list[str] = []
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for path in paths:
                try:
                    item = create_source_item(path, kind)  # type: ignore[arg-type]
                    key = normalized_path_key(item.path)
                    if key in existing_paths:
                        raise SourceError(f"Ruta duplicada: {item.path}")
                    if item.display_name.casefold() in existing_names:
                        raise SourceError(
                            f"El nombre '{item.display_name}' ya existe en la raíz del ISO."
                        )
                    target.add_source(item)
                    existing_paths.add(key)
                    existing_names.add(item.display_name.casefold())
                except SourceError as exc:
                    errors.append(str(exc))
        finally:
            QApplication.restoreOverrideCursor()
        if errors:
            QMessageBox.warning(self, "No se agregaron algunas rutas", "\n".join(errors))

    @Slot()
    def _sources_changed(self) -> None:
        if self.worker_thread is not None:
            return
        self.current_plan = None
        self.progress_bar.setValue(0)
        self.status_label.setText("Distribución pendiente de cálculo.")
        self._update_raw_metrics()
        self._update_generate_state()

    @Slot()
    def _capacity_changed(self) -> None:
        custom = self.capacity_combo.currentData() is None
        self.custom_capacity.setVisible(custom)
        self._sources_changed()

    def _capacity_bytes(self) -> int:
        selected = self.capacity_combo.currentData()
        value = self.custom_capacity.value() if selected is None else float(selected)
        return gb_to_bytes(value)

    def _capacity_text(self) -> str:
        return f"{self._capacity_bytes() / 1_000_000_000:.2f} GB"

    def _update_raw_metrics(self) -> None:
        permanent = self.permanent_panel.items
        variable = self.variable_panel.items
        permanent_size = sum(item.size_bytes for item in permanent)
        variable_size = sum(item.size_bytes for item in variable)
        estimated_permanent = sum(item.estimated_bytes for item in permanent)
        capacity = self._capacity_bytes()
        free = max(0, capacity - FIXED_ISO_OVERHEAD - estimated_permanent)
        self.metric_permanent.set_value(format_size(permanent_size))
        self.metric_variable.set_value(format_size(variable_size))
        self.metric_capacity.set_value(self._capacity_text())
        self.metric_free.set_value(format_size(free))
        self.metric_count.set_value("—")

    @Slot()
    def _choose_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta de salida", FILE_DIALOG_START_DIR)
        if path:
            self.output_edit.setText(str(Path(path).resolve()))
            self._update_generate_state()

    @Slot()
    def _calculate_plan(self) -> bool:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            permanent = refresh_source_items(self.permanent_panel.items)
            variable = refresh_source_items(self.variable_panel.items)
            plan = create_packing_plan(permanent, variable, self._capacity_bytes())
        except (SourceError, PackingError, OSError) as exc:
            self.current_plan = None
            self.distribution_button.setEnabled(False)
            self.metric_count.set_value("—")
            self.status_label.setText("Corrige la selección para continuar.")
            QMessageBox.warning(self, "No se puede calcular", str(exc))
            return False
        finally:
            QApplication.restoreOverrideCursor()

        self.permanent_panel.replace_sources(permanent)
        self.variable_panel.replace_sources(variable)
        self.current_plan = plan
        self._render_plan(plan)
        self.metric_permanent.set_value(format_size(plan.permanent_size_bytes))
        self.metric_variable.set_value(format_size(plan.variable_size_bytes))
        self.metric_capacity.set_value(self._capacity_text())
        self.metric_free.set_value(format_size(plan.minimum_free_bytes))
        self.metric_count.set_value(str(plan.iso_count))
        self.status_label.setText(
            f"Distribución lista: {plan.iso_count} ISO con nombres automáticos."
        )
        self._update_generate_state()
        return True

    def _render_plan(self, plan: PackingPlan) -> None:
        """Punto de extensión conservado; la vista previa se retiró de la interfaz."""
        return

    def _show_distribution(self) -> None:
        if self.current_plan is None or self.worker_thread is not None:
            return
        DistributionDialog(self.current_plan, self).exec()

    def _update_generate_state(self) -> None:
        self.distribution_button.setEnabled(self.current_plan is not None and self.worker_thread is None)
        ready = (
            self.current_plan is not None
            and bool(self.output_edit.text().strip())
            and self.worker_thread is None
        )
        self.generate_button.setEnabled(ready)

    @Slot()
    def _start_generation(self) -> None:
        if not self._calculate_plan():
            return
        output_text = self.output_edit.text().strip()
        output = Path(output_text) if output_text else None
        if output is None or not output.is_dir():
            QMessageBox.warning(
                self, "Falta la salida", "Selecciona una carpeta de salida válida."
            )
            return
        assert self.current_plan is not None

        self.worker_thread = QThread(self)
        self.worker = IsoWorker(self.current_plan, output)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress_changed.connect(self._on_progress)
        self.worker.completed.connect(self._on_generation_completed)
        self.worker.failed.connect(self._on_generation_failed)
        self.worker.completed.connect(self.worker_thread.quit)
        self.worker.failed.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self.worker.deleteLater)
        self.worker_thread.finished.connect(self._clear_worker)
        self._set_running(True)
        self.worker_thread.start()

    def _set_running(self, running: bool) -> None:
        self.permanent_panel.setEnabled(not running)
        self.variable_panel.setEnabled(not running)
        self.capacity_combo.setEnabled(not running)
        self.custom_capacity.setEnabled(not running)
        self.output_button.setEnabled(not running)
        self.calculate_button.setEnabled(not running)
        self.distribution_button.setEnabled(not running and self.current_plan is not None)
        can_generate = (
            not running
            and self.current_plan is not None
            and bool(self.output_edit.text().strip())
            and (self.worker_thread is None or not self.worker_thread.isRunning())
        )
        self.generate_button.setEnabled(can_generate)
        self.cancel_button.setEnabled(running)

    @Slot(int, str)
    def _on_progress(self, value: int, message: str) -> None:
        self.progress_bar.setValue(value)
        self.status_label.setText(message)

    @Slot(object)
    def _on_generation_completed(self, paths: object) -> None:
        generated = list(paths)  # type: ignore[arg-type]
        self.progress_bar.setValue(100)
        self.status_label.setText(f"Completado: {len(generated)} ISO generado(s).")
        self.metric_count.set_generated(len(generated))
        self._set_running(False)
        QMessageBox.information(
            self,
            "Generación completada",
            f"Se generaron {len(generated)} archivos ISO en:\n{self.output_edit.text()}",
        )

    @Slot(str)
    def _on_generation_failed(self, message: str) -> None:
        self._set_running(False)
        if "cancelada" in message.casefold():
            self.status_label.setText("Generación cancelada; temporales eliminados.")
        else:
            self.status_label.setText("La generación no pudo completarse.")
        QMessageBox.warning(self, "Generación interrumpida", message)

    @Slot()
    def _cancel_generation(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
            self.cancel_button.setEnabled(False)
            self.status_label.setText("Cancelando y limpiando temporales…")

    @Slot()
    def _clear_worker(self) -> None:
        if self.worker_thread is not None:
            self.worker_thread.deleteLater()
        self.worker = None
        self.worker_thread = None
        self._set_running(False)
        self._update_generate_state()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (API Qt)
        if self.worker_thread is not None and self.worker_thread.isRunning():
            QMessageBox.warning(
                self,
                "Generación en curso",
                "Cancela la generación y espera la limpieza antes de cerrar.",
            )
            event.ignore()
            return
        event.accept()

    def _apply_style(self) -> None:
        self.setStyleSheet(STYLE)
