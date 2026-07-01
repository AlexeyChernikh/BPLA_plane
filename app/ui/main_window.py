from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtGui import QCloseEvent, QCursor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)
from pyproj import CRS, Transformer
from shapely.geometry import LineString, mapping
from shapely.ops import transform

from app.core.exporter import export_all, export_mission
from app.core.models import PlannerSettings, PlanningResult
from app.core.planner import plan_terrain_missions
from app.core.polygon_loader import load_polygon
from app.core.project_store import (
    PROJECT_SUFFIX,
    ProjectSession,
    load_project,
    save_project,
)
from app.core.terrain import TerrainModel

from .map_view import MapView


class WorkerSignals(QObject):
    progress = Signal(str)
    finished = Signal(object)
    failed = Signal(Exception)


class PlannerWorker(QObject):
    def __init__(
        self,
        polygon_path: Path,
        dem_path: Path,
        settings: PlannerSettings,
        override_homes_wgs84: list[tuple[float, float]] | None = None,
        forced_grid_sizes: tuple[float, float] | None = None,
        message: str = "Расчёт миссий…",
    ) -> None:
        super().__init__()
        self.signals = WorkerSignals()
        self._polygon_path = polygon_path
        self._dem_path = dem_path
        self._settings = settings
        self._override_homes_wgs84 = override_homes_wgs84
        self._forced_grid_sizes = forced_grid_sizes
        self._message = message

    @Slot()
    def run(self) -> None:
        try:
            self.signals.progress.emit(self._message)
            result = plan_terrain_missions(
                self._polygon_path,
                self._dem_path,
                self._settings,
                self._override_homes_wgs84,
                self._forced_grid_sizes,
            )
            self.signals.progress.emit("Обновление карты…")
            self.signals.finished.emit(result)
        except Exception as error:
            self.signals.failed.emit(error)


class ExportWorker(QObject):
    def __init__(self, result: PlanningResult, directory: Path) -> None:
        super().__init__()
        self.signals = WorkerSignals()
        self._result = result
        self._directory = directory

    @Slot()
    def run(self) -> None:
        try:
            self.signals.progress.emit("Экспорт файлов…")
            created = export_all(self._result, self._directory)
            self.signals.finished.emit(created)
        except Exception as error:
            self.signals.failed.emit(error)


class MissionExportWorker(QObject):
    def __init__(
        self,
        result: PlanningResult,
        zone_id: int,
        mission_id: int,
        directory: Path,
    ) -> None:
        super().__init__()
        self.signals = WorkerSignals()
        self._result = result
        self._zone_id = zone_id
        self._mission_id = mission_id
        self._directory = directory

    @Slot()
    def run(self) -> None:
        try:
            self.signals.progress.emit(
                f"Экспорт миссии {self._zone_id}.{self._mission_id}…"
            )
            created = export_mission(
                self._result,
                self._zone_id,
                self._mission_id,
                self._directory,
            )
            self.signals.finished.emit(created)
        except Exception as error:
            self.signals.failed.emit(error)


class ProjectSaveWorker(QObject):
    def __init__(
        self,
        project_path: Path,
        result: PlanningResult,
        dem_path: Path,
    ) -> None:
        super().__init__()
        self.signals = WorkerSignals()
        self._project_path = project_path
        self._result = result
        self._dem_path = dem_path

    @Slot()
    def run(self) -> None:
        try:
            self.signals.progress.emit("Сохранение данных проекта…")
            path = save_project(self._project_path, self._result, self._dem_path)
            self.signals.finished.emit(path)
        except Exception as error:
            self.signals.failed.emit(error)


class ProjectLoadWorker(QObject):
    def __init__(self, project_path: Path) -> None:
        super().__init__()
        self.signals = WorkerSignals()
        self._project_path = project_path

    @Slot()
    def run(self) -> None:
        try:
            self.signals.progress.emit("Чтение данных проекта…")
            session = load_project(self._project_path)
            if session.result is None:
                self.signals.progress.emit(
                    "Пересчёт старого формата проекта…"
                )
                result = plan_terrain_missions(
                    session.polygon_path,
                    session.dem_path,
                    session.settings,
                    session.homes_wgs84,
                    session.forced_grid_sizes,
                )
            else:
                self.signals.progress.emit("Восстановление рассчитанных миссий…")
                result = session.result
            terrain = TerrainModel(
                session.dem_path,
                session.settings.working_crs,
                result.polygon.geometry,
            )
            self.signals.progress.emit("Обновление карты…")
            self.signals.finished.emit((session, result, terrain.preview_overlay()))
        except Exception as error:
            self.signals.failed.emit(error)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Планировщик миссий QGroundControl")
        self.resize(1500, 920)
        self.polygon_path: Path | None = None
        self.dem_path: Path | None = None
        self.result: PlanningResult | None = None
        self.terrain_overlay: tuple[str, list[list[float]]] | None = None
        self.project_path: Path | None = None
        self._project_work_directory: Path | None = None
        self._busy = False
        self._worker_thread: QThread | None = None
        self._worker: QObject | None = None

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter, 4)

        controls = QWidget()
        controls.setMinimumWidth(340)
        controls.setMaximumWidth(430)
        controls_layout = QVBoxLayout(controls)

        self.open_project_button = QPushButton("Открыть проект")
        self.open_project_button.clicked.connect(self.open_project)
        self.open_project_button.setToolTip(
            "Открыть сохранённую сессию со всеми исходными данными."
        )
        self.save_project_button = QPushButton("Сохранить проект")
        self.save_project_button.clicked.connect(self.save_project)
        self.save_project_button.setToolTip(
            "Сохранить полигон, DEM, настройки, Home и параметры сетки."
        )
        self.load_polygon_button = QPushButton("Загрузить полигон")
        self.load_polygon_button.clicked.connect(self.load_polygon_file)
        self.load_polygon_button.setToolTip(
            "Загрузить границу участка: SHP, GeoJSON, GPKG или KML."
        )
        self.polygon_label = QLabel("Полигон не выбран")
        self.polygon_label.setWordWrap(True)
        self.load_dem_button = QPushButton("Загрузить DEM")
        self.load_dem_button.clicked.connect(self.load_dem_file)
        self.load_dem_button.setToolTip(
            "Загрузить GeoTIFF с высотами рельефа, полностью покрывающий участок."
        )
        self.dem_label = QLabel("DEM не выбран")
        self.dem_label.setWordWrap(True)
        controls_layout.addWidget(self.open_project_button)
        controls_layout.addWidget(self.save_project_button)
        controls_layout.addWidget(self.load_polygon_button)
        controls_layout.addWidget(self.polygon_label)
        controls_layout.addWidget(self.load_dem_button)
        controls_layout.addWidget(self.dem_label)

        form = QFormLayout()
        self.crs = QLineEdit("EPSG:28417")
        self.azimuth = _spin(0, 360, 2, 0)
        self.spacing = _spin(0.1, 10000, 2, 75)
        self.altitude = _spin(1, 10000, 1, 110)
        self.speed = _spin(0.1, 100, 2, 5)
        self.flight_time = _spin(0.1, 1440, 1, 30)
        self.reserve = _spin(0, 99.9, 1, 20)
        self.waypoint_step = _spin(0.1, 10000, 1, 100)
        self.extension = _spin(0, 10000, 1, 0)
        self.search_buffer = _spin(0, 10000, 0, 500)
        self.max_zone_side = _spin(0, 100000, 0, 0)
        self.max_mission_side = _spin(0, 100000, 0, 0)
        self.climb_speed = _spin(0.1, 30, 1, 3)
        self.descent_speed = _spin(0.1, 30, 1, 2)
        self.terrain_tolerance = _spin(0, 1000, 1, 10)
        self.relief_warning = _spin(1, 5000, 0, 300)
        self.route_mode = QComboBox()
        self.route_mode.addItems(["snake", "one-way"])
        self.altitude_mode = QComboBox()
        self.altitude_mode.addItem(
            "Calc Above Terrain (как в примере)", "calc_above_terrain"
        )
        self.altitude_mode.addItem("Terrain Frame", "terrain")
        self.altitude_mode.addItem("Абсолютная AMSL", "amsl")
        self.altitude_mode.addItem("Относительно Home", "relative")
        self.mission_mode = QComboBox()
        self.mission_mode.addItem("Survey (как в примере)", "survey")
        self.mission_mode.addItem("Обычные Waypoint", "waypoint")
        for label, widget in (
            ("Рабочая CRS", self.crs),
            ("Азимут, °", self.azimuth),
            ("Шаг профилей, м", self.spacing),
            ("Высота AGL, м", self.altitude),
            ("Скорость, м/с", self.speed),
            ("Время батареи, мин", self.flight_time),
            ("Резерв, %", self.reserve),
            ("Допуск рельефа, м", self.terrain_tolerance),
            ("Вылет профиля, м", self.extension),
            ("Буфер Home, м", self.search_buffer),
            ("Макс. сторона зоны, м", self.max_zone_side),
            ("Макс. сторона миссии, м", self.max_mission_side),
            ("Набор высоты, м/с", self.climb_speed),
            ("Снижение, м/с", self.descent_speed),
            ("Порог рельефа, м", self.relief_warning),
            ("Маршрут", self.route_mode),
            ("Режим QGC", self.mission_mode),
            ("Высоты QGC", self.altitude_mode),
        ):
            form.addRow(label, widget)
            description = _PARAMETER_HELP[label]
            widget.setToolTip(description)
            label_widget = form.labelForField(widget)
            if label_widget is not None:
                label_widget.setToolTip(description)
        controls_layout.addLayout(form)

        self.help_button = QPushButton("Справка по параметрам")
        self.help_button.setToolTip("Открыть подробное описание всех настроек.")
        self.help_button.clicked.connect(self.show_parameter_help)
        self.generate_button = QPushButton("Найти Home и сгенерировать")
        self.generate_button.setToolTip(
            "Найти точки взлёта, разделить участок и построить батарейные миссии."
        )
        self.generate_button.clicked.connect(self.generate)
        self.export_button = QPushButton("Экспортировать QGC Plans")
        self.export_button.setToolTip(
            "Сохранить планы QGroundControl и сопутствующие GIS-файлы."
        )
        self.export_button.clicked.connect(self.export)
        self.progress_label = QLabel("")
        self.progress_label.setWordWrap(True)
        self.progress_label.setVisible(False)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(False)
        controls_layout.addWidget(self.help_button)
        controls_layout.addWidget(self.generate_button)
        controls_layout.addWidget(self.export_button)
        controls_layout.addWidget(self.progress_label)
        controls_layout.addWidget(self.progress_bar)
        controls_layout.addStretch(1)

        self.map_view = MapView()
        self.map_view.homeMoved.connect(self.move_home)
        self.map_view.missionContextRequested.connect(
            self._show_map_mission_context_menu
        )
        controls_scroll = QScrollArea()
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        controls_scroll.setWidget(controls)
        splitter.addWidget(controls_scroll)
        splitter.addWidget(self.map_view)
        splitter.setStretchFactor(1, 1)

        self.table = QTableWidget(0, 11)
        self.table.setHorizontalHeaderLabels(
            [
                "Home",
                "Zone",
                "Mission",
                "Profiles",
                "Length, m",
                "Time, min",
                "Relief, m",
                "Zone side, m",
                "Mission side, m",
                "Edge",
                "Status",
            ]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(
            self._show_mission_context_menu
        )
        layout.addWidget(self.table, 1)
        self.statusBar().showMessage("Готово")

    def settings(self) -> PlannerSettings:
        return PlannerSettings(
            working_crs=CRS.from_user_input(self.crs.text().strip()),
            azimuth_deg=self.azimuth.value(),
            profile_spacing_m=self.spacing.value(),
            altitude_m=self.altitude.value(),
            speed_mps=self.speed.value(),
            max_flight_time_min=self.flight_time.value(),
            battery_reserve_percent=self.reserve.value(),
            waypoint_step_m=self.waypoint_step.value(),
            profile_extension_m=self.extension.value(),
            route_mode=self.route_mode.currentText(),  # type: ignore[arg-type]
            home_search_buffer_m=self.search_buffer.value(),
            climb_speed_mps=self.climb_speed.value(),
            descent_speed_mps=self.descent_speed.value(),
            terrain_adjust_tolerance_m=self.terrain_tolerance.value(),
            terrain_warning_m=self.relief_warning.value(),
            altitude_mode=self.altitude_mode.currentData(),  # type: ignore[arg-type]
            mission_mode=self.mission_mode.currentData(),  # type: ignore[arg-type]
            max_zone_side_m=self.max_zone_side.value(),
            max_mission_side_m=self.max_mission_side.value(),
        )

    def show_parameter_help(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Справка по параметрам")
        dialog.resize(760, 650)
        layout = QVBoxLayout(dialog)
        browser = QTextBrowser(dialog)
        browser.setHtml(_HELP_HTML)
        layout.addWidget(browser)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, parent=dialog)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def open_project(self) -> None:
        if self._busy:
            self.statusBar().showMessage("Дождитесь завершения текущей операции")
            return
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Открыть проект",
            str(self.project_path.parent if self.project_path else Path.cwd()),
            "Проект BPLA (*.bpla)",
        )
        if filename:
            self._start_worker(
                ProjectLoadWorker(Path(filename)),
                self._finish_open_project,
            )

    def _finish_open_project(self, loaded: object) -> None:
        session, result, overlay = loaded  # type: ignore[misc]
        assert isinstance(session, ProjectSession)
        old_work_directory = self._project_work_directory
        self._project_work_directory = session.polygon_path.parent
        self.project_path = session.project_path
        self.polygon_path = session.polygon_path
        self.dem_path = session.dem_path
        self.result = result
        self.terrain_overlay = overlay
        self._apply_settings(session.settings)
        self.polygon_label.setText(f"{self.project_path.name}: полигон проекта")
        self.dem_label.setText(f"{self.project_path.name}: DEM проекта")
        self._show_result()
        self.setWindowTitle(
            f"Планировщик миссий QGroundControl — {self.project_path.name}"
        )
        self._clear_busy(
            f"Проект открыт: Home {len(self.result.takeoff_sites)}, "
            f"миссий {len(self.result.missions)}"
        )
        if (
            old_work_directory is not None
            and old_work_directory != self._project_work_directory
        ):
            shutil.rmtree(old_work_directory, ignore_errors=True)

    def save_project(self) -> None:
        if self._busy:
            self.statusBar().showMessage("Дождитесь завершения текущей операции")
            return
        if self.result is None or self.dem_path is None:
            QMessageBox.warning(
                self,
                "Нет расчёта",
                "Сначала загрузите данные и сгенерируйте миссии.",
            )
            return
        default = self.project_path or Path.cwd() / f"mission_project{PROJECT_SUFFIX}"
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить проект",
            str(default),
            "Проект BPLA (*.bpla)",
        )
        if filename:
            self._start_worker(
                ProjectSaveWorker(Path(filename), self.result, self.dem_path),
                self._finish_save_project,
            )

    def _finish_save_project(self, saved: object) -> None:
        self.project_path = Path(saved)  # type: ignore[arg-type]
        self.setWindowTitle(
            f"Планировщик миссий QGroundControl — {self.project_path.name}"
        )
        self._clear_busy(f"Проект сохранён: {self.project_path}")

    def load_polygon_file(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите полигон",
            "",
            "GIS (*.shp *.geojson *.json *.gpkg *.kml)",
        )
        if not filename:
            return
        try:
            loaded = load_polygon(filename, self.crs.text().strip())
            self.polygon_path = Path(filename)
            self.result = None
            self.polygon_label.setText(str(self.polygon_path))
            self._show_loaded_polygon(loaded.geometry, loaded.working_crs)
            self.statusBar().showMessage("Полигон загружен")
        except Exception as error:
            self._show_error(error)

    def load_dem_file(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "Выберите DEM", "", "GeoTIFF (*.tif *.tiff)"
        )
        if not filename:
            return
        try:
            geometry = None
            if self.polygon_path is not None:
                geometry = load_polygon(
                    self.polygon_path, self.crs.text().strip()
                ).geometry
            terrain = TerrainModel(filename, self.crs.text().strip(), geometry)
            self.dem_path = Path(filename)
            self.result = None
            self.dem_label.setText(str(self.dem_path))
            self.terrain_overlay = terrain.preview_overlay()
            if geometry is not None:
                self._show_loaded_polygon(geometry, terrain.crs)
            self.statusBar().showMessage("DEM загружен")
        except Exception as error:
            self._show_error(error)

    def generate(self) -> None:
        if self._busy:
            self.statusBar().showMessage("Дождитесь завершения текущей операции")
            return
        if self.polygon_path is None or self.dem_path is None:
            QMessageBox.warning(
                self,
                "Недостаточно данных",
                "Сначала загрузите полигон и DEM.",
            )
            return
        worker = PlannerWorker(
            self.polygon_path,
            self.dem_path,
            self.settings(),
            message="Поиск точек Home и расчёт миссий…",
        )
        self._start_worker(worker, self._finish_generate)

    def _finish_generate(self, result: object) -> None:
        self.result = result  # type: ignore[assignment]
        self._show_result()
        if self.result is not None:
            self._clear_busy(
                f"Home: {len(self.result.takeoff_sites)}, "
                f"миссий: {len(self.result.missions)}"
            )

    def move_home(self, identifier: int, latitude: float, longitude: float) -> None:
        if self._busy:
            self.statusBar().showMessage("Дождитесь завершения текущей операции")
            return
        if self.result is None or self.polygon_path is None or self.dem_path is None:
            return
        to_wgs = Transformer.from_crs(
            self.result.settings.working_crs, 4326, always_xy=True
        )
        homes: list[tuple[float, float]] = []
        for site in self.result.takeoff_sites:
            lon, lat = to_wgs.transform(site.point.x, site.point.y)
            homes.append(
                (latitude, longitude) if site.id == identifier else (lat, lon)
            )
        forced_grid_sizes = (
            self.result.zones[0].nominal_side_m,
            self.result.missions[0].nominal_side_m,
        )
        worker = PlannerWorker(
            self.polygon_path,
            self.dem_path,
            self.settings(),
            override_homes_wgs84=homes,
            forced_grid_sizes=forced_grid_sizes,
            message=f"Пересчёт после перемещения Home {identifier}…",
        )
        self._start_worker(worker, self._finish_move_home)

    def _finish_move_home(self, result: object) -> None:
        self.result = result  # type: ignore[assignment]
        self._show_result()
        if self.result is not None and self.result.valid:
            self._clear_busy("Точки Home обновлены")
        else:
            self._clear_busy("Есть непокрытые зоны — экспорт запрещён")

    def export(self) -> None:
        if self._busy:
            self.statusBar().showMessage("Дождитесь завершения текущей операции")
            return
        if self.result is None:
            QMessageBox.warning(
                self, "Нет миссий", "Сначала сгенерируйте миссии."
            )
            return
        if not self.result.valid:
            QMessageBox.critical(
                self,
                "Экспорт запрещён",
                "\n".join(self.result.errors),
            )
            return
        default = Path(__file__).resolve().parents[1] / "outputs"
        directory = QFileDialog.getExistingDirectory(
            self, "Каталог экспорта", str(default)
        )
        if not directory:
            return
        worker = ExportWorker(self.result, Path(directory))
        self._start_worker(
            worker,
            lambda created: self._finish_export(created, Path(directory)),
        )

    def _finish_export(self, created: object, directory: Path) -> None:
        paths = list(created)  # type: ignore[arg-type]
        self._clear_busy(f"Экспорт завершён: {len(paths)} файлов")
        QMessageBox.information(
            self,
            "Экспорт завершён",
            f"Создано файлов: {len(paths)}\n{directory}",
        )

    def _show_mission_context_menu(self, position) -> None:
        item = self.table.itemAt(position)
        if item is None or self.result is None:
            return
        identity_item = self.table.item(item.row(), 0)
        identity = identity_item.data(Qt.UserRole) if identity_item else None
        if not identity:
            return
        zone_id, mission_id = identity
        self._show_mission_export_menu(
            zone_id,
            mission_id,
            self.table.viewport().mapToGlobal(position),
            self.table,
        )

    def _show_map_mission_context_menu(
        self,
        zone_id: int,
        mission_id: int,
    ) -> None:
        self._show_mission_export_menu(
            zone_id,
            mission_id,
            QCursor.pos(),
            self.map_view,
        )

    def _show_mission_export_menu(
        self,
        zone_id: int,
        mission_id: int,
        global_position,
        parent: QWidget,
    ) -> None:
        if self.result is None:
            return
        menu = QMenu(parent)
        export_action = menu.addAction("Экспортировать миссию")
        export_action.setEnabled(not self._busy)
        selected = menu.exec(global_position)
        if selected == export_action:
            self._export_single_mission(zone_id, mission_id)

    def _export_single_mission(self, zone_id: int, mission_id: int) -> None:
        if self._busy or self.result is None:
            return
        default = Path(__file__).resolve().parents[1] / "outputs"
        directory = QFileDialog.getExistingDirectory(
            self,
            f"Экспорт миссии {zone_id}.{mission_id}",
            str(default),
        )
        if not directory:
            return
        worker = MissionExportWorker(
            self.result,
            zone_id,
            mission_id,
            Path(directory),
        )
        self._start_worker(
            worker,
            lambda created: self._finish_single_export(
                created,
                Path(directory),
                zone_id,
                mission_id,
            ),
        )

    def _finish_single_export(
        self,
        created: object,
        directory: Path,
        zone_id: int,
        mission_id: int,
    ) -> None:
        paths = list(created)  # type: ignore[arg-type]
        message = f"Миссия {zone_id}.{mission_id}: создано {len(paths)} файлов"
        self._clear_busy(message)
        QMessageBox.information(
            self,
            "Экспорт миссии завершён",
            f"{message}\n{directory}",
        )

    def _show_loaded_polygon(self, geometry, crs: CRS) -> None:
        to_wgs = Transformer.from_crs(crs, 4326, always_xy=True)
        feature = {
            "type": "Feature",
            "properties": {"kind": "polygon"},
            "geometry": mapping(transform(to_wgs.transform, geometry)),
        }
        self.map_view.show_geojson(
            {"type": "FeatureCollection", "features": [feature]},
            terrain_overlay=self.terrain_overlay,
        )

    def _show_result(self) -> None:
        assert self.result is not None
        rows = [
            (zone, mission)
            for zone in self.result.zones
            for mission in zone.missions
        ]
        self.table.setRowCount(len(rows))
        for row, (zone, mission) in enumerate(rows):
            values = (
                zone.home.id,
                zone.id,
                mission.id,
                len(mission.profiles),
                f"{mission.route_length_m:.1f}",
                f"{mission.estimated_time_min:.1f}",
                f"{zone.relief_m:.1f}",
                f"{zone.nominal_side_m:.0f}",
                f"{mission.nominal_side_m:.0f}",
                "Да" if mission.edge_clipped else "Нет",
                mission.status,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, (zone.id, mission.id))
                self.table.setItem(row, column, item)
        self.map_view.show_geojson(
            _result_geojson(self.result),
            terrain_overlay=self.terrain_overlay,
        )

    def _show_error(self, error: Exception) -> None:
        self.statusBar().showMessage("Ошибка")
        QMessageBox.critical(self, "Ошибка", str(error))

    def _apply_settings(self, settings: PlannerSettings) -> None:
        self.crs.setText(settings.working_crs.to_string())
        self.azimuth.setValue(settings.azimuth_deg)
        self.spacing.setValue(settings.profile_spacing_m)
        self.altitude.setValue(settings.altitude_m)
        self.speed.setValue(settings.speed_mps)
        self.flight_time.setValue(settings.max_flight_time_min)
        self.reserve.setValue(settings.battery_reserve_percent)
        self.waypoint_step.setValue(settings.waypoint_step_m)
        self.extension.setValue(settings.profile_extension_m)
        self.search_buffer.setValue(settings.home_search_buffer_m)
        self.max_zone_side.setValue(settings.max_zone_side_m)
        self.max_mission_side.setValue(settings.max_mission_side_m)
        self.climb_speed.setValue(settings.climb_speed_mps)
        self.descent_speed.setValue(settings.descent_speed_mps)
        self.terrain_tolerance.setValue(settings.terrain_adjust_tolerance_m)
        self.relief_warning.setValue(settings.terrain_warning_m)
        self.route_mode.setCurrentText(settings.route_mode)
        self.altitude_mode.setCurrentIndex(
            max(0, self.altitude_mode.findData(settings.altitude_mode))
        )
        self.mission_mode.setCurrentIndex(
            max(0, self.mission_mode.findData(settings.mission_mode))
        )

    def _set_busy(self, message: str) -> None:
        self._busy = True
        self.progress_label.setText(message)
        self.progress_label.setVisible(True)
        self.progress_bar.setVisible(True)
        self.statusBar().showMessage(message)
        self.load_polygon_button.setEnabled(False)
        self.load_dem_button.setEnabled(False)
        self.open_project_button.setEnabled(False)
        self.save_project_button.setEnabled(False)
        self.generate_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.map_view.setEnabled(False)

    def _clear_busy(self, final_message: str | None = None) -> None:
        self._busy = False
        self.progress_label.clear()
        self.progress_label.setVisible(False)
        self.progress_bar.setVisible(False)
        self.load_polygon_button.setEnabled(True)
        self.load_dem_button.setEnabled(True)
        self.open_project_button.setEnabled(True)
        self.save_project_button.setEnabled(True)
        self.generate_button.setEnabled(True)
        self.export_button.setEnabled(True)
        self.map_view.setEnabled(True)
        if final_message:
            self.statusBar().showMessage(final_message)

    def _start_worker(
        self, worker: QObject, on_success: Callable[[object], None]
    ) -> None:
        if self._busy:
            self.statusBar().showMessage("Дождитесь завершения текущей операции")
            return
        self._set_busy("Подготовка операции…")
        thread = QThread(self)
        self._worker_thread = thread
        self._worker = worker
        worker.moveToThread(thread)
        signals = worker.signals  # type: ignore[attr-defined]
        thread.started.connect(worker.run)  # type: ignore[attr-defined]
        signals.progress.connect(self._set_busy)
        signals.finished.connect(lambda result: self._on_worker_finished(result, on_success))
        signals.failed.connect(self._on_worker_error)
        signals.finished.connect(thread.quit)
        signals.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._cleanup_worker)
        thread.start()

    def _on_worker_finished(
        self, result: object, on_success: Callable[[object], None]
    ) -> None:
        try:
            on_success(result)
        except Exception as error:
            self._on_worker_error(error)

    def _on_worker_error(self, error: Exception) -> None:
        self._clear_busy("Ошибка")
        self._show_error(error)

    def _cleanup_worker(self) -> None:
        self._worker = None
        self._worker_thread = None

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._project_work_directory is not None:
            shutil.rmtree(self._project_work_directory, ignore_errors=True)
        super().closeEvent(event)


def _spin(
    minimum: float, maximum: float, decimals: int, value: float
) -> QDoubleSpinBox:
    widget = QDoubleSpinBox()
    widget.setRange(minimum, maximum)
    widget.setDecimals(decimals)
    widget.setValue(value)
    widget.setSingleStep(1)
    return widget


def _result_geojson(result: PlanningResult) -> dict[str, object]:
    transformer = Transformer.from_crs(
        result.settings.working_crs, CRS.from_epsg(4326), always_xy=True
    )
    features: list[dict[str, object]] = [
        {
            "type": "Feature",
            "properties": {"kind": "polygon"},
            "geometry": mapping(
                transform(transformer.transform, result.polygon.geometry)
            ),
        }
    ]
    profile_assignment = {
        profile.id: (mission.home_id, mission.zone_id, mission.id)
        for mission in result.missions
        for profile in mission.profiles
    }
    for zone in result.zones:
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "kind": "operational_zone",
                    "zone_id": zone.id,
                    "home_id": zone.home.id,
                    "status": zone.status,
                    "grid_row": zone.grid_row,
                    "grid_col": zone.grid_col,
                    "side_m": zone.nominal_side_m,
                    "edge_clipped": zone.edge_clipped,
                },
                "geometry": mapping(transform(transformer.transform, zone.geometry)),
            }
        )
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "kind": "home",
                    "home_id": zone.home.id,
                    "elevation_m": round(zone.home.elevation_m, 1),
                },
                "geometry": mapping(
                    transform(transformer.transform, zone.home.point)
                ),
            }
        )
    for profile in result.profiles:
        home_id, zone_id, mission_id = profile_assignment.get(
            profile.id, (0, 0, 0)
        )
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "kind": "profile",
                    "home_id": home_id,
                    "zone_id": zone_id,
                    "mission_id": mission_id,
                },
                "geometry": mapping(
                    transform(transformer.transform, profile.geometry)
                ),
            }
        )
    for mission in result.missions:
        zone = next(item for item in result.zones if item.id == mission.zone_id)
        if mission.zone is not None:
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "kind": "zone",
                        "zone_id": mission.zone_id,
                        "mission_id": mission.id,
                        "grid_row": mission.grid_row,
                        "grid_col": mission.grid_col,
                        "side_m": mission.nominal_side_m,
                        "edge_clipped": mission.edge_clipped,
                    },
                    "geometry": mapping(
                        transform(transformer.transform, mission.zone)
                    ),
                }
            )
        line = LineString(
            [
                (zone.home.point.x, zone.home.point.y),
                *((point.x, point.y) for point in mission.route_points),
                (zone.home.point.x, zone.home.point.y),
            ]
        )
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "kind": "route",
                    "zone_id": mission.zone_id,
                    "mission_id": mission.id,
                },
                "geometry": mapping(transform(transformer.transform, line)),
            }
        )
    return {"type": "FeatureCollection", "features": features}


_PARAMETER_HELP = {
    "Рабочая CRS": (
        "Проекционная система координат с единицами в метрах. Она используется "
        "для расстояний, площадей и построения сетки. EPSG:28417 подходит только "
        "для соответствующей координатной зоны."
    ),
    "Азимут, °": (
        "Истинное геодезическое направление профилей по часовой стрелке от "
        "севера: 0° — север–юг, 90° — восток–запад. Сближение меридианов CRS "
        "учитывается автоматически."
    ),
    "Шаг профилей, м": (
        "Перпендикулярное расстояние между соседними профилями. Меньший шаг "
        "повышает плотность покрытия, но увеличивает длину и число миссий."
    ),
    "Высота AGL, м": (
        "Требуемая высота полёта над поверхностью земли. Абсолютная высота "
        "каждой точки рассчитывается по DEM."
    ),
    "Скорость, м/с": (
        "Горизонтальная скорость полёта. Используется при расчёте времени "
        "миссии и записывается в QGroundControl."
    ),
    "Время батареи, мин": (
        "Максимальная продолжительность одного вылета до учёта резерва. "
        "Каждая миссия должна вернуться в свой Home в пределах этого времени."
    ),
    "Резерв, %": (
        "Часть батареи, исключённая из планирования. Доступное время равно: "
        "время батареи × (1 − резерв / 100)."
    ),
    "Допуск рельефа, м": (
        "Как в QGroundControl: новая промежуточная точка сохраняется, когда "
        "расчётная высота отличается от последней сохранённой больше этого допуска."
    ),
    "Вылет профиля, м": (
        "Продление каждого профиля за границу участка с обеих сторон. "
        "Используется для разворота и стабилизации до начала съёмки."
    ),
    "Буфер Home, м": (
        "Допустимое расстояние поиска точки взлёта за пределами полигона. "
        "Например, 500 м разрешает искать площадку внутри участка и до 500 м вокруг."
    ),
    "Макс. сторона зоны, м": (
        "Максимальная номинальная сторона крупного квадратного блока Home. "
        "0 — подобрать автоматически по батарее. Границы кратны размеру миссий."
    ),
    "Макс. сторона миссии, м": (
        "Максимальная сторона малой квадратной ячейки миссии. 0 — автоматический "
        "расчёт. Значение должно быть не меньше шага профилей."
    ),
    "Набор высоты, м/с": (
        "Вертикальная скорость подъёма. Используется в консервативном расчёте "
        "батареи и в параметрах Survey."
    ),
    "Снижение, м/с": (
        "Вертикальная скорость снижения. Влияет на расчёт времени возврата "
        "и параметры Survey."
    ),
    "Порог рельефа, м": (
        "Если разница между минимальной и максимальной отметкой DEM внутри "
        "зоны больше порога, зона получает предупреждение «Сложный рельеф»."
    ),
    "Маршрут": (
        "snake — соседние профили проходятся попеременно в противоположных "
        "направлениях; one-way — все профили проходятся в одном направлении."
    ),
    "Режим QGC": (
        "Survey создаёт ComplexItem Survey как в примере: Takeoff → Survey → RTL. "
        "Waypoint создаёт обычную последовательность MAV_CMD_NAV_WAYPOINT."
    ),
    "Высоты QGC": (
        "Calc Above Terrain рассчитывает по загруженному DEM абсолютные высоты "
        "Survey с постоянной AGL, как в примере. "
        "Terrain Frame хранит AGL в MAVLink; AMSL записывает DEM + AGL; "
        "«Относительно Home» использует отметку точки взлёта."
    ),
}


_HELP_HTML = """
<h2>Исходные данные</h2>
<p><b>Полигон</b> — граница всего участка. Поддерживаются SHP, GeoJSON, GPKG
и KML. MultiPolygon обрабатывается полностью.</p>
<p><b>DEM</b> — GeoTIFF с отметками рельефа. Он должен покрывать весь участок;
по нему рассчитываются высоты маршрута, уклон площадок Home и предупреждения.</p>

<h2>Геометрия полёта</h2>
<p><b>Рабочая CRS</b> — метрическая проекция для всех расчётов. Неверная зона
CRS приводит к искажению расстояний.</p>
<p><b>Азимут</b> задаётся по часовой стрелке от истинного севера: 0° —
север–юг, 90° — восток–запад. Приложение автоматически учитывает сближение
меридианов выбранной проекции.</p>
<p><b>Шаг профилей</b> определяет плотность съёмки. Уменьшение шага увеличивает
число профилей, миссий и итоговый объём файлов.</p>
<p><b>Высота AGL</b> — постоянное превышение над рельефом.</p>
<p><b>Допуск рельефа</b> работает как Terrain Adjust Tolerance в QGroundControl:
DEM сначала читается с полной доступной детализацией, после чего лишние точки удаляются.</p>
<p><b>Вылет профиля</b> продлевает рабочую линию за границу участка.</p>

<h2>Батарея и Home</h2>
<p><b>Доступное время</b> = время батареи × (1 − резерв / 100). В расчёт входят
перелёт от Home, профили, переходы, возврат, набор высоты и снижение.</p>
<p><b>Буфер Home</b> задаёт область автоматического поиска площадок вокруг
участка. Алгоритм сначала минимизирует количество Home, затем предпочитает
более плоские площадки и короткие перелёты.</p>
<p><b>Максимальная сторона зоны/миссии</b> ограничивает регулярную квадратную
сетку. Значение 0 включает автоматический подбор. Крайние квадраты обрезаются
исходной границей участка.</p>
<p><b>Набор/снижение</b> используются для расчёта вертикальной части времени.</p>
<p><b>Порог рельефа</b> создаёт предупреждение, но не запрещает генерацию.</p>

<h2>Маршруты и экспорт</h2>
<p><b>snake</b> формирует непрерывную змейку. <b>one-way</b> сохраняет одно
направление всех рабочих проходов.</p>
<p><b>Survey</b> — режим по умолчанию, совместимый со структурой примера:
Camera Mode → Takeoff → ComplexItem Survey → RTL.</p>
<p><b>Waypoint</b> экспортирует обычные команды изменения скорости и waypoint.</p>
<p><b>Calc Above Terrain</b> — режим примера: приложение записывает в Survey
абсолютный профиль DEM + AGL и соблюдает заданные скорости набора и снижения.
<b>Terrain Frame</b> хранит постоянную AGL в MAVLink; <b>AMSL</b> — абсолютные
высоты; <b>Относительно Home</b> — высоты от отметки точки взлёта.</p>

<h2>Цветные полигоны</h2>
<p>Крупный полигон соответствует одной точке Home. Внутри него малые полигоны
соответствуют отдельным батарейным миссиям и полностью покрывают зону.</p>
"""
