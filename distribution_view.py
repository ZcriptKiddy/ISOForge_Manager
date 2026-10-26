"""Vista de solo lectura de los discos asignados por el plan existente."""
from pathlib import Path, PurePosixPath
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFrame,
    QHBoxLayout, QLabel, QListWidget, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)
from file_utils import build_manifest, format_size, SourceError


class StoredMetric:
    """Conserva la interfaz set_value del controlador sin crear indicadores visibles."""
    def set_value(self, value):
        self.value = value


class IsoCountPanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('metricCard')
        layout=QHBoxLayout(self)
        layout.setContentsMargins(16,10,16,10)
        self.title=QLabel('ISO previstos'); self.title.setObjectName('fieldLabel')
        self.value_label=QLabel('Sin calcular'); self.value_label.setObjectName('metricValue')
        layout.addWidget(self.title); layout.addStretch(); layout.addWidget(self.value_label)

    def set_value(self, value):
        self.title.setText('ISO previstos')
        self.value_label.setText('Sin calcular' if value=='—' else value)

    def set_generated(self, count):
        self.title.setText('ISO generados')
        self.value_label.setText(str(count))


class DistributionDialog(QDialog):
    def __init__(self, plan, parent=None):
        super().__init__(parent)
        self.plan=plan
        self.setWindowTitle('Ver distribución')
        self.setModal(True)
        self.resize(1000,650); self.setMinimumSize(720,460)
        layout=QVBoxLayout(self); layout.setContentsMargins(22,20,22,18); layout.setSpacing(14)
        title=QLabel('Distribución de los discos'); title.setObjectName('appTitle'); layout.addWidget(title)
        splitter=QSplitter(Qt.Orientation.Horizontal)
        left=QWidget(); left_layout=QVBoxLayout(left); left_layout.setContentsMargins(0,0,12,0)
        count=QLabel(f'{plan.iso_count} ISO en total'); count.setObjectName('cardTitle'); left_layout.addWidget(count)
        self.discs=QListWidget(); self.discs.setMinimumWidth(150)
        self.discs.setStyleSheet('QListWidget {border:1px solid #dfe5ef; border-radius:8px; background:white;} QListWidget::item {padding:14px 12px;} QListWidget::item:selected {background:#e3ebff; color:#25334b;}')
        for disc in plan.bins: self.discs.addItem(disc.filename)
        left_layout.addWidget(self.discs)
        right=QWidget(); right_layout=QVBoxLayout(right); right_layout.setContentsMargins(6,0,0,0)
        self.disc_title=QLabel(); self.disc_title.setObjectName('cardTitle'); right_layout.addWidget(self.disc_title)
        self.details=QLabel(); self.details.setObjectName('muted'); self.details.setWordWrap(True); right_layout.addWidget(self.details)
        self.tree=QTreeWidget(); self.tree.setHeaderLabels(['Contenido del disco','Tipo','Tamaño'])
        self.tree.setAlternatingRowColors(True); self.tree.setUniformRowHeights(True)
        self.tree.header().setSectionResizeMode(0,self.tree.header().ResizeMode.Stretch)
        self.tree.setColumnWidth(1,110); self.tree.setColumnWidth(2,110)
        right_layout.addWidget(self.tree,1)
        self.notice=QLabel(''); self.notice.setWordWrap(True); self.notice.setObjectName('muted'); right_layout.addWidget(self.notice)
        splitter.addWidget(left); splitter.addWidget(right); splitter.setSizes([190,760]); splitter.setChildrenCollapsible(False)
        layout.addWidget(splitter,1)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText('Cerrar')
        buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        self.discs.currentRowChanged.connect(self.show_disc)
        self.discs.setCurrentRow(0)

    def show_disc(self, index):
        self.tree.clear(); self.notice.clear()
        if index<0 or index>=len(self.plan.bins): return
        disc=self.plan.bins[index]
        self.disc_title.setText(disc.filename)
        self.details.setText(f'Estimado: {format_size(disc.estimated_iso_bytes)}   ·   Libre: {format_size(disc.free_bytes)}')
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            manifest=build_manifest([*self.plan.permanent_items,*disc.items])
            permanent={item.display_name for item in self.plan.permanent_items}
            nodes={}
            icons=Path(__file__).resolve().parent/'assets'/'icons'
            def add(path,is_dir,size=''):
                category='Fijo' if path.parts[0] in permanent else 'Variable'
                row=QTreeWidgetItem([path.name,category,size])
                row.setToolTip(0,path.as_posix())
                row.setIcon(0,QIcon(str(icons/('folder.svg' if is_dir else 'file.svg'))))
                row.setData(0,Qt.ItemDataRole.UserRole,path.as_posix())
                parent=nodes.get(path.parent)
                if parent is None: self.tree.addTopLevelItem(row)
                else: parent.addChild(row)
                if is_dir: nodes[path]=row
            for path in manifest.directories: add(path,True)
            for file in manifest.files: add(file.target,False,format_size(file.size_bytes))
            self.tree.expandToDepth(0)
            self.notice.setText(f'{len(manifest.files)} archivos · {len(manifest.directories)} carpetas. El contenido fijo se incluye en todos los discos.')
        except (OSError,SourceError) as exc:
            self.tree.clear()
            self.notice.setText(f'No se pudo mostrar el contenido: {exc}')
        finally:
            QApplication.restoreOverrideCursor()
