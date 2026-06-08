# -*- coding: utf-8 -*-
"""
Narzędziownik APP - Wtyczka QGIS
Informacje o autorach, repozytorium: https://github.com/tomasz-gietkowski-geoanalityka/narzedziownik_app
Dokumentacja: https://akademia.geoanalityka.pl/courses/narzedziownik-app-dokumentacja/
Licencja: GNU GPL v3.0 (https://www.gnu.org/licenses/gpl-3.0.html)

"""

import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QRadioButton,
    QVBoxLayout,
)
from qgis.core import (
    Qgis,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsLineString,
    QgsPoint,
    QgsPointXY,
    QgsProject,
    QgsSnappingConfig,
    QgsTolerance,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.gui import QgsMapTool, QgsSnapIndicator, QgsVertexMarker
from PyQt5.QtCore import QVariant

_DEBUG = False  # True = pokaż warstwy pośrednie w QGIS

_LINE_TYPES = {
    "Obowiązująca linia zabudowy": "styl-linia-zabudowy.qml",
    "Nieprzekraczalna linia zabudowy": "styl-linia-zabudowy-nie.qml",
}
_active_tool = None  # referencja chroniąca QgsMapTool przed garbage collection

_MESSAGES = [
    "Kliknij punkt początkowy na granicy działki.",
    "Kliknij punkt wskazujący kierunek wyjścia z początku.",
    "Kliknij punkt wskazujący kierunek dojścia do końca.",
    "Kliknij punkt końcowy na granicy działki.",
]


def _fix(geom):
    """Naprawa geometrii przez buffer(0, 0) — jak w merge_selected_to_edit_target."""
    try:
        fixed = geom.buffer(0, 0)
        if fixed and not fixed.isEmpty():
            return fixed
    except Exception:
        pass
    return geom


def _debug_add(name, geom, crs, color=None, geom_type=None):
    """Dodaje geometrię jako tymczasową warstwę debug w QGIS."""
    if not _DEBUG or geom is None or geom.isEmpty():
        return
    if geom_type is None:
        wkb = geom.wkbType()
        gt = QgsWkbTypes.geometryType(wkb)
        if gt == QgsWkbTypes.PointGeometry:
            geom_type = "Point"
        elif gt == QgsWkbTypes.LineGeometry:
            geom_type = "LineString"
        else:
            geom_type = "Polygon"
    lyr = QgsVectorLayer(
        f"{geom_type}?crs={crs.authid()}", f"DBG: {name}", "memory"
    )
    feat = QgsFeature()
    feat.setGeometry(geom)
    lyr.dataProvider().addFeatures([feat])
    if color:
        sym = lyr.renderer().symbol()
        sym.setColor(color)
        sym.setWidth(0.5) if geom_type != "Point" else None
    lyr.updateExtents()
    QgsProject.instance().addMapLayer(lyr)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(iface, plugin_dir):
    """Uruchamia narzędzie linii zabudowy.

    Gdy zaznaczono wiele poligonów, scala je za pomocą Minimum Bounding
    Geometry (convex hull) z algorytmu native:minimumboundinggeometry.
    """
    canvas = iface.mapCanvas()
    layer = iface.activeLayer()

    # --- walidacja warstwy ---
    if not layer or not isinstance(layer, QgsVectorLayer):
        iface.messageBar().pushWarning(
            "Linia zabudowy", "Wybierz warstwę wektorową z poligonami."
        )
        return

    if layer.geometryType() != QgsWkbTypes.PolygonGeometry:
        iface.messageBar().pushWarning(
            "Linia zabudowy", "Aktywna warstwa musi zawierać poligony."
        )
        return

    selected = layer.selectedFeatures()
    if len(selected) < 1:
        iface.messageBar().pushWarning(
            "Linia zabudowy", "Zaznacz co najmniej jeden poligon na warstwie."
        )
        return

    # --- scalenie zaznaczonych poligonów ---
    parts = []
    for feat in selected:
        g = feat.geometry()
        if g and not g.isEmpty():
            g = _fix(g)
            if g and not g.isEmpty() and \
               QgsWkbTypes.geometryType(g.wkbType()) == QgsWkbTypes.PolygonGeometry:
                parts.append(g)

    if not parts:
        iface.messageBar().pushWarning(
            "Linia zabudowy", "Zaznaczone poligony są puste lub niepoprawne."
        )
        return

    if len(parts) == 1:
        polygon_geom = parts[0]
    else:
        try:
            merged = QgsGeometry.unaryUnion(parts)
        except Exception:
            merged = parts[0]
            for g in parts[1:]:
                merged = merged.combine(g)

        if not merged or merged.isEmpty():
            iface.messageBar().pushWarning(
                "Linia zabudowy", "Nie udało się scalić zaznaczonych poligonów."
            )
            return

        merged = _fix(merged)

        if QgsWkbTypes.isMultiType(merged.wkbType()):
            if merged.constGet().numGeometries() > 1:
                iface.messageBar().pushWarning(
                    "Linia zabudowy",
                    "Zaznaczone poligony nie są styczne – "
                    "muszą tworzyć jeden spójny obszar.",
                )
                return
        polygon_geom = merged

    # --- typ linii i odległość odsunięcia ---
    dlg = QDialog(iface.mainWindow())
    dlg.setWindowTitle("Linia zabudowy")

    type_names = list(_LINE_TYPES.keys())
    radios = []
    for i, name in enumerate(type_names):
        rb = QRadioButton(name)
        if i == 0:
            rb.setChecked(True)
        radios.append(rb)

    spin = QDoubleSpinBox()
    spin.setRange(0.01, 10000.0)
    spin.setDecimals(2)
    spin.setSingleStep(0.5)
    spin.setValue(4.0)
    spin.setSuffix(" m")

    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)

    layout = QVBoxLayout(dlg)
    layout.addWidget(QLabel("Typ linii:"))
    for rb in radios:
        layout.addWidget(rb)
    form = QFormLayout()
    form.addRow("Odległość od granicy:", spin)
    layout.addLayout(form)
    layout.addWidget(buttons)

    if dlg.exec_() != QDialog.Accepted:
        return

    line_type = next(rb.text() for rb in radios if rb.isChecked())
    distance = spin.value()

    # --- aktywacja narzędzia mapowego ---
    global _active_tool
    _active_tool = LiniaZabudowyMapTool(
        canvas, polygon_geom, layer.crs(), distance, line_type, iface, plugin_dir
    )
    canvas.setMapTool(_active_tool)

    iface.messageBar().pushInfo("Linia zabudowy", _MESSAGES[0])


# ---------------------------------------------------------------------------
# Map tool  (4 kliknięcia: początek, kierunek wyjścia, kierunek dojścia, koniec)
# ---------------------------------------------------------------------------

class LiniaZabudowyMapTool(QgsMapTool):
    """QgsMapTool do wskazania czterech punktów.

    Punkt 1 – początek linii zabudowy (na granicy).
    Punkt 2 – kierunek wyjścia z początku.
    Punkt 3 – kierunek dojścia do końca.
    Punkt 4 – koniec linii zabudowy (na granicy).
    """

    def __init__(self, canvas, polygon_geom, crs, distance, line_type, iface, plugin_dir):
        super().__init__(canvas)
        self.polygon_geom = polygon_geom
        self.crs = crs
        self.distance = distance
        self.line_type = line_type
        self.iface = iface
        self.plugin_dir = plugin_dir
        self._points = []          # zebrane punkty (max 4)
        self._markers = []         # QgsVertexMarker dla każdego kliknięcia
        self._snap_indicator = QgsSnapIndicator(canvas)
        self.setCursor(Qt.CrossCursor)

        # Snapping – zapamiętaj tylko zmieniane właściwości i włącz
        project = QgsProject.instance()
        cfg = project.snappingConfig()
        self._orig_snap_enabled = cfg.enabled()
        self._orig_snap_mode = cfg.mode()
        self._orig_snap_type = cfg.type()
        self._orig_snap_tolerance = cfg.tolerance()
        self._orig_snap_units = cfg.units()
        cfg.setEnabled(True)
        cfg.setMode(QgsSnappingConfig.ActiveLayer)
        cfg.setType(QgsSnappingConfig.VertexAndSegment)
        cfg.setTolerance(15)
        cfg.setUnits(QgsTolerance.Pixels)
        project.setSnappingConfig(cfg)

    # -- zdarzenia --

    def canvasMoveEvent(self, event):
        if self._snap_indicator:
            match = self.canvas().snappingUtils().snapToMap(event.pos())
            self._snap_indicator.setMatch(match)

    def canvasReleaseEvent(self, event):
        # Pobierz punkt ze snappingiem QGIS
        match = self.canvas().snappingUtils().snapToMap(event.pos())
        if match.isValid():
            point = QgsPointXY(match.point())
        else:
            point = self.toMapCoordinates(event.pos())

        point_index = len(self._points)

        # Snap do granicy poligonu tylko dla punktów 1 i 4 (na granicy)
        if point_index in (0, 3):
            nearest = self.polygon_geom.nearestPoint(
                QgsGeometry.fromPointXY(point)
            )
            if not nearest.isEmpty():
                point = nearest.asPoint()

        self._points.append(point)
        self._add_marker(point)

        if len(self._points) < 4:
            self.iface.messageBar().pushInfo(
                "Linia zabudowy", _MESSAGES[len(self._points)]
            )
        else:
            # Cztery punkty zebrane – uruchom algorytm
            p1, p2, p3, p4 = self._points
            self._cleanup()

            try:
                offset_geom = _compute_offset_line(
                    self.polygon_geom, p1, p2, p3, p4, self.distance,
                    self.crs,
                )
                _add_to_result_layer(
                    offset_geom, self.distance, self.line_type,
                    self.crs, self.iface, self.plugin_dir,
                )
            except Exception as exc:
                QMessageBox.critical(
                    self.iface.mainWindow(),
                    "Linia zabudowy",
                    f"Nie udało się utworzyć linii zabudowy:\n{exc}",
                )

            self.iface.mapCanvas().unsetMapTool(self)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._cleanup()
            self.iface.mapCanvas().unsetMapTool(self)
            self.iface.messageBar().pushInfo(
                "Linia zabudowy", "Anulowano rysowanie linii zabudowy."
            )

    def deactivate(self):
        self._cleanup()
        global _active_tool
        _active_tool = None
        super().deactivate()

    # -- helpers --

    def _add_marker(self, point):
        m = QgsVertexMarker(self.canvas())
        m.setCenter(point)
        m.setColor(QColor(255, 0, 0))
        m.setIconSize(12)
        m.setIconType(QgsVertexMarker.ICON_CROSS)
        m.setPenWidth(2)
        self._markers.append(m)

    def _cleanup(self):
        for m in self._markers:
            self.canvas().scene().removeItem(m)
        self._markers.clear()
        if self._snap_indicator:
            del self._snap_indicator
            self._snap_indicator = None
        project = QgsProject.instance()
        cfg = project.snappingConfig()
        cfg.setEnabled(self._orig_snap_enabled)
        cfg.setMode(self._orig_snap_mode)
        cfg.setType(self._orig_snap_type)
        cfg.setTolerance(self._orig_snap_tolerance)
        cfg.setUnits(self._orig_snap_units)
        project.setSnappingConfig(cfg)


# ---------------------------------------------------------------------------
# Algorytm odsunięcia
# ---------------------------------------------------------------------------

def _extract_subline_forward(ring, d_from, d_to, total_len):
    """Ekstrakcja podlinii wzdłuż pierścienia w kierunku „do przodu".

    Jeśli d_from <= d_to – bezpośredni fragment.
    W przeciwnym razie – fragment owijający koniec/początek pierścienia.
    """
    if d_from <= d_to:
        sub = ring.curveSubstring(d_from, d_to)
        return QgsGeometry(sub.clone())

    part1 = ring.curveSubstring(d_from, total_len)
    part2 = ring.curveSubstring(0, d_to)
    pts1 = [part1.pointN(i) for i in range(part1.numPoints())]
    pts2 = [part2.pointN(i) for i in range(part2.numPoints())]
    merged = QgsLineString(pts1 + pts2[1:])  # pts2[0] == pts1[-1]
    return QgsGeometry(merged)


def _compute_offset_line(polygon_geom, point_1, point_2, point_3, point_4, distance, crs):
    """Zwraca geometrię linii zabudowy odsuniętej do wnętrza poligonu.

    point_1 – punkt początkowy (na granicy)
    point_2 – punkt kierunku wyjścia z początku
    point_3 – punkt kierunku dojścia do końca
    point_4 – punkt końcowy (na granicy)
    """
    # Obsługa MultiPolygon – bierz pierwszą część
    poly_abstract = polygon_geom.constGet()
    if QgsWkbTypes.isMultiType(polygon_geom.wkbType()):
        poly_abstract = poly_abstract.geometryN(0)

    ring = poly_abstract.exteriorRing()  # QgsCurve / QgsLineString
    ring_geom = QgsGeometry(ring.clone())
    total_len = ring_geom.length()

    # DEBUG: punkty kontrolne
    _debug_add("1 Punkt 1 (początek)", QgsGeometry.fromPointXY(point_1), crs, QColor(255, 0, 0), "Point")
    _debug_add("2 Punkt 2 (kier. wyjścia)", QgsGeometry.fromPointXY(point_2), crs, QColor(0, 255, 0), "Point")
    _debug_add("3 Punkt 3 (kier. dojścia)", QgsGeometry.fromPointXY(point_3), crs, QColor(0, 128, 255), "Point")
    _debug_add("4 Punkt 4 (koniec)", QgsGeometry.fromPointXY(point_4), crs, QColor(0, 0, 255), "Point")

    # Pozycje liniowe na pierścieniu
    dist_1 = ring_geom.lineLocatePoint(QgsGeometry.fromPointXY(point_1))
    dist_2 = ring_geom.lineLocatePoint(QgsGeometry.fromPointXY(point_2))
    dist_4 = ring_geom.lineLocatePoint(QgsGeometry.fromPointXY(point_4))

    # Sprawdź, czy punkt 2 (kierunek wyjścia) leży na ścieżce „do przodu" od 1 do 4
    if dist_1 <= dist_4:
        b_on_forward = dist_1 <= dist_2 <= dist_4
    else:
        b_on_forward = (dist_2 >= dist_1) or (dist_2 <= dist_4)

    if b_on_forward:
        subline_geom = _extract_subline_forward(ring, dist_1, dist_4, total_len)
    else:
        subline_geom = _extract_subline_forward(ring, dist_4, dist_1, total_len)

    if subline_geom.isEmpty():
        raise ValueError(
            "Nie można wyodrębnić fragmentu granicy między wskazanymi punktami.\n\n"
            "Możliwe przyczyny:\n"
            "• Punkty początkowy i końcowy są zbyt blisko siebie.\n"
            "• Punkty nie leżą na granicy zaznaczonego poligonu."
        )

    # DEBUG: fragment granicy 1→4
    _debug_add("5 Subline 1→4 (granica)", subline_geom, crs, QColor(255, 165, 0))

    # --- odsunięcie (próbuj obie strony, zostaw tę z dłuższym wynikiem) ---
    clipped = QgsGeometry()
    for sign in (1, -1):
        offset = subline_geom.offsetCurve(sign * distance, 8, Qgis.JoinStyle.Miter, 5.0)
        if offset.isEmpty():
            continue
        # DEBUG: surowy offset
        _debug_add(f"6 Offset (sign={sign:+d})", offset, crs, QColor(200, 200, 200))
        candidate = offset.intersection(polygon_geom)
        if not candidate.isEmpty() and candidate.length() > clipped.length():
            clipped = candidate

    if clipped.isEmpty():
        raise ValueError(
            "Nie udało się utworzyć linii zabudowy.\n\n"
            "Możliwe przyczyny:\n"
            f"• Odległość {distance} m jest zbyt duża w stosunku do rozmiarów poligonu.\n"
            "• Wybrany fragment granicy jest zbyt krótki lub ma bardzo ostry kąt.\n"
            "• Odsunięta linia wypada poza poligon — spróbuj mniejszą odległość\n"
            "  lub wybierz dłuższy fragment granicy (przesuń punkty)."
        )

    # intersection może zwrócić kolekcję geometrii – weź najdłuższą linię
    clipped = _extract_longest_line(clipped)

    # Snap końców do granicy (artefakt precyzji GEOS po intersection)
    clipped = _snap_ends_to_boundary(clipped, polygon_geom)
    _debug_add("7 Po intersection+longest+snap", clipped, crs, QColor(128, 0, 128))

    # Dociągnij końce do granicy przy narożnikach (krótki promień wzdłuż linii)
    clipped = _extend_ends_at_corners(clipped, polygon_geom, distance)
    _debug_add("8 Po extend_corners", clipped, crs, QColor(0, 128, 128))

    # Upewnij się, że trójkąty wskazują wnętrze poligonu
    clipped = _orient_for_markers(clipped, polygon_geom)

    return clipped


def _snap_ends_to_boundary(geom, polygon_geom, tolerance=0.5):
    """Przyciąga końce linii do granicy poligonu (fix artefaktów GEOS)."""
    abstract = geom.constGet()
    if QgsWkbTypes.isMultiType(geom.wkbType()):
        abstract = abstract.geometryN(0)
    n = abstract.numPoints()
    if n < 2:
        return geom

    poly_abstract = polygon_geom.constGet()
    if QgsWkbTypes.isMultiType(polygon_geom.wkbType()):
        poly_abstract = poly_abstract.geometryN(0)
    boundary = QgsGeometry(poly_abstract.exteriorRing().clone())

    pts = [abstract.pointN(i) for i in range(n)]
    changed = False

    for idx in (0, -1):
        ep = QgsPointXY(pts[idx].x(), pts[idx].y())
        ep_geom = QgsGeometry.fromPointXY(ep)
        dist = boundary.distance(ep_geom)
        if 0 < dist < tolerance:
            nearest = boundary.nearestPoint(ep_geom)
            if not nearest.isEmpty():
                p = nearest.asPoint()
                pts[idx] = QgsPoint(p.x(), p.y())
                changed = True

    if changed:
        return QgsGeometry(QgsLineString(pts))
    return geom


def _extend_ends_at_corners(geom, polygon_geom, distance):
    """Dociąga końce linii do granicy przy narożnikach.

    Rzuca krótki promień (distance * 3) wzdłuż kierunku linii z każdego końca.
    Przy narożnikach promień trafia w sąsiednią krawędź granicy.
    Wzdłuż prostych krawędzi promień jest równoległy i nie trafia — koniec
    pozostaje niezmieniony.
    """
    abstract = geom.constGet()
    if QgsWkbTypes.isMultiType(geom.wkbType()):
        abstract = abstract.geometryN(0)
    n = abstract.numPoints()
    if n < 2:
        return geom

    pts = [abstract.pointN(i) for i in range(n)]

    poly_abstract = polygon_geom.constGet()
    if QgsWkbTypes.isMultiType(polygon_geom.wkbType()):
        poly_abstract = poly_abstract.geometryN(0)
    boundary = QgsGeometry(poly_abstract.exteriorRing().clone())

    max_extend = distance * 3

    new_start = _ray_to_boundary(pts[0], pts[1], boundary, max_extend)
    if new_start:
        pts.insert(0, new_start)

    new_end = _ray_to_boundary(pts[-1], pts[-2], boundary, max_extend)
    if new_end:
        pts.append(new_end)

    return QgsGeometry(QgsLineString(pts))


def _ray_to_boundary(endpoint, prev_point, boundary, max_extend):
    """Promień z końca linii wzdłuż jej kierunku, ograniczony do max_extend.

    Zwraca QgsPoint na granicy lub None gdy promień nie trafia w granicę
    (koniec jest już na granicy lub promień jest równoległy do krawędzi).
    """
    ep_xy = QgsPointXY(endpoint.x(), endpoint.y())
    ep_geom = QgsGeometry.fromPointXY(ep_xy)

    if boundary.distance(ep_geom) < 0.002:
        return None

    dx = endpoint.x() - prev_point.x()
    dy = endpoint.y() - prev_point.y()
    seg_len = (dx * dx + dy * dy) ** 0.5
    if seg_len < 1e-10:
        return None

    f = max_extend / seg_len
    ray = QgsGeometry.fromPolylineXY([
        ep_xy,
        QgsPointXY(endpoint.x() + dx * f, endpoint.y() + dy * f),
    ])
    hit = ray.intersection(boundary)
    if not hit.isEmpty():
        nearest = hit.nearestPoint(ep_geom)
        if not nearest.isEmpty():
            p = nearest.asPoint()
            return QgsPoint(p.x(), p.y())

    return None


def _extract_longest_line(geom):
    """Wyciąga najdłuższą część liniową z geometrii (po intersection)."""
    wkb = geom.wkbType()
    if QgsWkbTypes.geometryType(wkb) == QgsWkbTypes.LineGeometry:
        if not QgsWkbTypes.isMultiType(wkb):
            return geom

    abstract = geom.constGet()
    n = abstract.numGeometries() if hasattr(abstract, 'numGeometries') else 0
    if n == 0:
        return geom

    best, best_len = None, 0
    for i in range(n):
        part = abstract.geometryN(i)
        if QgsWkbTypes.geometryType(part.wkbType()) == QgsWkbTypes.LineGeometry:
            g = QgsGeometry(part.clone())
            if g.length() > best_len:
                best, best_len = g, g.length()

    if not best:
        raise ValueError("Linia zabudowy po docięciu do poligonu jest pusta.")
    return best


def _orient_for_markers(geom, polygon_geom):
    """Odwraca kierunek linii, gdy trójkąty stylu nie wskazują wnętrza poligonu.

    Styl QML: MarkerLine offset=-1.2 (prawa strona), equilateral_triangle
    rotate=1, angle=0 → wierzchołek trójkąta wskazuje LEWO od kierunku linii.
    Chcemy, by LEWO = wnętrze poligonu, czyli centroid poligonu po lewej stronie.
    """
    abstract = geom.constGet()
    line = abstract.geometryN(0) if QgsWkbTypes.isMultiType(geom.wkbType()) else abstract

    n = line.numPoints()
    if n < 2:
        return geom

    # Weź środkowy segment linii
    mid = min(n // 2, n - 2)
    p1 = line.pointN(mid)
    p2 = line.pointN(mid + 1)

    dx = p2.x() - p1.x()
    dy = p2.y() - p1.y()
    if dx * dx + dy * dy < 1e-20:
        return geom

    # Środek segmentu
    mx = (p1.x() + p2.x()) / 2
    my = (p1.y() + p2.y()) / 2

    # Wektor od środka linii do centroidu poligonu (reprezentuje „wnętrze")
    centroid = polygon_geom.centroid().asPoint()
    vx = centroid.x() - mx
    vy = centroid.y() - my

    # Iloczyn wektorowy D × V:  >0 → centroid po LEWEJ,  <0 → po PRAWEJ
    cross = dx * vy - dy * vx

    if cross >= 0:
        # Wnętrze jest po lewej – trójkąty wskazują poprawnie
        return geom

    # Wnętrze jest po prawej – odwróć kierunek linii
    if QgsWkbTypes.isMultiType(geom.wkbType()):
        parts = []
        for i in range(abstract.numGeometries()):
            parts.append(QgsGeometry(abstract.geometryN(i).reversed()))
        return QgsGeometry.collectGeometry(parts)

    return QgsGeometry(abstract.reversed())


# ---------------------------------------------------------------------------
# Warstwa wynikowa
# ---------------------------------------------------------------------------

def _find_existing_layer(layer_name):
    """Zwraca istniejącą warstwę o podanej nazwie lub None."""
    for lyr in QgsProject.instance().mapLayers().values():
        if lyr.name() == layer_name and isinstance(lyr, QgsVectorLayer):
            return lyr
    return None


def _add_to_result_layer(offset_geom, distance, line_type, crs, iface, plugin_dir):
    """Dodaje linię zabudowy do warstwy wynikowej (tworzy ją, jeśli nie istnieje)."""
    layer = _find_existing_layer(line_type)

    if layer is None:
        crs_authid = crs.authid()
        layer = QgsVectorLayer(
            f"LineString?crs={crs_authid}", line_type, "memory"
        )
        pr = layer.dataProvider()
        pr.addAttributes([QgsField("odsuniecie_m", QVariant.Double)])
        layer.updateFields()

        qml_file = _LINE_TYPES.get(line_type, "styl-linia-zabudowy.qml")
        qml_path = os.path.join(plugin_dir, "resources", "qml", qml_file)
        if os.path.exists(qml_path):
            layer.loadNamedStyle(qml_path)

        QgsProject.instance().addMapLayer(layer)

    feat = QgsFeature(layer.fields())
    feat.setGeometry(offset_geom)
    feat.setAttribute("odsuniecie_m", distance)

    layer.dataProvider().addFeatures([feat])
    layer.updateExtents()
    layer.triggerRepaint()

    iface.messageBar().pushSuccess(
        "Linia zabudowy",
        f"Utworzono: {line_type} (odsunięcie: {distance} m).",
    )
