# -*- coding: utf-8 -*-
"""
Narzędziownik APP - Wtyczka QGIS
Informacje o autorach, repozytorium: https://github.com/tomasz-gietkowski-geoanalityka/narzedziownik_app
Dokumentacja: https://akademia.geoanalityka.pl/courses/narzedziownik-app-dokumentacja/
Licencja: GNU GPL v3.0 (https://www.gnu.org/licenses/gpl-3.0.html)
"""

import re
from collections import Counter

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QMessageBox,
    QDialog,
    QVBoxLayout,
    QLabel,
    QRadioButton,
    QGroupBox,
    QDialogButtonBox,
)

from qgis.core import (
    QgsVectorLayer,
    QgsFeature,
    QgsProject,
    QgsWkbTypes,
)


# ---------------------------------------------------------
# HELPERY PÓL / TEKSTU
# ---------------------------------------------------------

def _find_lokalny_field(layer: QgsVectorLayer) -> str | None:
    """
    Znajduje pole lokalnego identyfikatora.
    Priorytet: 'lokalnyId', ewentualnie 'loklaneId' (dla starszych warstw z literówką).
    """
    field_names = [f.name() for f in layer.fields()]
    for cand in ("lokalnyId", "loklaneId"):
        if cand in field_names:
            return cand
    return None


def _collect_numbers_from_oznaczenie(layer: QgsVectorLayer, oznaczenie_field: str) -> dict:
    """
    Zbiera informacje o liczbowej części z pola 'oznaczenie'.
    """
    result = {}

    for f in layer.getFeatures():
        val = f[oznaczenie_field]
        text = "" if val is None else str(val).strip()
        num = None
        prefix = ""
        suffix = ""

        if text:
            m = re.search(r'(\d+)', text)
            if m:
                try:
                    num = int(m.group(1))
                    prefix = text[:m.start(1)]
                    suffix = text[m.end(1):]
                except Exception:
                    num = None

        result[f.id()] = {
            "raw": text,
            "num": num,
            "prefix": prefix,
            "suffix": suffix,
        }

    return result


def _compute_missing_numbers(nums: set[int]) -> list[int]:
    """
    Dla zbioru numerów zwraca listę brakujących wartości
    w zakresie 1..max(nums).
    """
    if not nums:
        return []
    end = max(nums)
    missing = [n for n in range(1, end + 1) if n not in nums]
    return missing


def _compute_missing_numbers_from_min(nums: set[int]) -> list[int]:
    """
    Dla zbioru numerów zwraca listę brakujących wartości
    w zakresie min(nums)..max(nums).
    """
    if not nums:
        return []
    start = min(nums)
    end = max(nums)
    return [n for n in range(start, end + 1) if n not in nums]


def _compute_missing_by_symbol(
    layer: QgsVectorLayer,
    oznaczenia_info: dict,
    symbol_field: str
) -> dict[str, list[int]]:
    """
    Dla numeracji odrębnej wg symboli:
    dla każdego symbolu osobno wyznacza brakujące liczby min..max(nums_symbol).
    """
    sym_idx = layer.fields().indexFromName(symbol_field)
    if sym_idx == -1:
        return {}

    nums_by_symbol: dict[str, set[int]] = {}

    for f in layer.getFeatures():
        info = oznaczenia_info.get(f.id())
        if not info:
            continue
        num = info["num"]
        if num is None:
            continue

        sym_val = f[sym_idx]
        sym_text = "" if sym_val is None else str(sym_val).strip()

        s = nums_by_symbol.setdefault(sym_text, set())
        s.add(num)

    missing_by_symbol: dict[str, list[int]] = {}
    for sym, sym_nums in nums_by_symbol.items():
        missing = _compute_missing_numbers_from_min(sym_nums)
        if missing:
            missing_by_symbol[sym] = missing

    return missing_by_symbol


def _escape_html(text: str) -> str:
    """
    Proste escapowanie do HTML dla wartości wyświetlanych w QLabel/QMessageBox.
    """
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# ---------------------------------------------------------
# TWORZENIE WARSTWY TYMCZASOWEJ I DODAWANIE DO GRUPY
# ---------------------------------------------------------

def _create_temp_layer_like(layer: QgsVectorLayer, suffix: str) -> QgsVectorLayer:
    """
    Tworzy nową warstwę tymczasową (memory) o takim samym schemacie.
    """
    wkb = layer.wkbType()
    geom_str = QgsWkbTypes.displayString(wkb)
    crs_str = layer.crs().authid()

    uri = f"{geom_str}?crs={crs_str}"
    new_name = f"{layer.name()} {suffix}"

    tmp_layer = QgsVectorLayer(uri, new_name, "memory")
    pr = tmp_layer.dataProvider()
    pr.addAttributes(layer.fields())
    tmp_layer.updateFields()

    return tmp_layer

def _add_layer_to_do_gml_group(layer: QgsVectorLayer):
    """
    Dodaje warstwę do grupy 'DODAJ ATRYBUTY GML'. Jeśli grupa nie istnieje, tworzy ją.
    """
    root = QgsProject.instance().layerTreeRoot()
    group = root.findGroup("DODAJ ATRYBUTY GML")
    if group is None:
        group = root.addGroup("DODAJ ATRYBUTY GML")
    
    QgsProject.instance().addMapLayer(layer, False)
    group.addLayer(layer)


# ---------------------------------------------------------
# TRYBY PRZELICZANIA
# ---------------------------------------------------------

def _renumber_mode_1_to_temp(src_layer, dst_layer, oznaczenie_field, lokalny_field, symbol_field):
    ozn_idx = dst_layer.fields().indexFromName(oznaczenie_field)
    sym_idx = dst_layer.fields().indexFromName(symbol_field)

    if ozn_idx == -1 or sym_idx == -1:
        return

    pr = dst_layer.dataProvider()
    new_features = []

    for row_num, f in enumerate(src_layer.getFeatures(), start=1):
        new_f = QgsFeature(dst_layer.fields())
        new_f.setGeometry(f.geometry())
        attrs = f.attributes()
        sym_val = attrs[sym_idx]
        sym_text = "" if sym_val is None else str(sym_val).strip()
        new_ozn = f"{row_num}{sym_text}"
        attrs[ozn_idx] = new_ozn
        new_f.setAttributes(attrs)
        new_features.append(new_f)

    pr.addFeatures(new_features)
    dst_layer.updateExtents()


def _renumber_mode_2_to_temp(src_layer, dst_layer, oznaczenie_field, lokalny_field, symbol_field):
    ozn_idx = dst_layer.fields().indexFromName(oznaczenie_field)
    sym_idx = dst_layer.fields().indexFromName(symbol_field)

    if ozn_idx == -1 or sym_idx == -1:
        return

    pr = dst_layer.dataProvider()
    new_features = []
    counters = {}

    for f in src_layer.getFeatures():
        new_f = QgsFeature(dst_layer.fields())
        new_f.setGeometry(f.geometry())
        attrs = f.attributes()
        sym_val = attrs[sym_idx]
        sym_text = "" if sym_val is None else str(sym_val).strip()
        current = counters.get(sym_text, 0) + 1
        counters[sym_text] = current
        new_ozn = f"{current}{sym_text}"
        attrs[ozn_idx] = new_ozn
        new_f.setAttributes(attrs)
        new_features.append(new_f)

    pr.addFeatures(new_features)
    dst_layer.updateExtents()


# ---------------------------------------------------------
# DIALOGI
# ---------------------------------------------------------

def _show_symbol_check_dialog(parent, has_nulls, null_count, layer_name_html):
    dlg = QDialog(parent)
    dlg.setWindowTitle("Kontrola pola 'symbol'")
    dlg.setMinimumWidth(500)
    layout = QVBoxLayout(dlg)

    info_header = QLabel(f"Aktywna warstwa:<br><br>&nbsp;&nbsp;&nbsp;&nbsp;<b>{layer_name_html}</b><br><br>")
    info_header.setTextFormat(Qt.RichText)
    layout.addWidget(info_header)

    if not has_nulls:
        label = QLabel("✅<b>Wszystkie obiekty mają wartość w polu 'symbol'.</b>")
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dlg)
        btn_box.button(QDialogButtonBox.Ok).setText("Przelicz mimo to…")
        btn_box.button(QDialogButtonBox.Cancel).setText("Anuluj")
    else:
        label = QLabel(f"<span style='color:#c0392b;'>❌<b>Istnieją obiekty bez symbolu ({null_count}).</b></span>")
        btn_box = QDialogButtonBox(QDialogButtonBox.Close, parent=dlg)
        btn_box.button(QDialogButtonBox.Close).setText("Zakończ")
        
    label.setWordWrap(True)
    label.setTextFormat(Qt.RichText)
    layout.addWidget(label)
    layout.addWidget(btn_box)

    btn_box.accepted.connect(dlg.accept)
    btn_box.rejected.connect(dlg.reject)
    if has_nulls: btn_box.button(QDialogButtonBox.Close).clicked.connect(dlg.reject)
        
    result = dlg.exec_()
    return result == QDialog.Accepted and not has_nulls


def _ask_numbering_scheme(parent, layer_name_html):
    dlg = QDialog(parent)
    dlg.setWindowTitle("Przeliczanie oznaczeń – sposób numeracji")
    dlg.setMinimumWidth(500)
    layout = QVBoxLayout(dlg)
    info_header = QLabel(f"Aktywna warstwa:<br><br>&nbsp;&nbsp;&nbsp;&nbsp;<b>{layer_name_html}</b><br><br>")
    info_header.setTextFormat(Qt.RichText)
    layout.addWidget(info_header)
    
    group = QGroupBox("Wybierz stosowaną dotychczas metodę numeracji", dlg)
    g_layout = QVBoxLayout(group)
    rb1 = QRadioButton("1 – numeracja ciągła dla wszystkich symboli", group)
    rb2 = QRadioButton("2 – numeracja odrębna dla poszczególnych symboli", group)
    rb1.setChecked(True)
    g_layout.addWidget(rb1); g_layout.addWidget(rb2)
    layout.addWidget(group)

    btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dlg)
    layout.addWidget(btn_box)
    btn_box.accepted.connect(dlg.accept); btn_box.rejected.connect(dlg.reject)

    if dlg.exec_() != QDialog.Accepted: return None
    return 1 if rb1.isChecked() else 2


def _show_mode_dialog(parent, layer_name_html, summary_html):
    dlg = QDialog(parent)
    dlg.setWindowTitle("Przeliczanie oznaczeń")
    dlg.setMinimumWidth(500)
    layout = QVBoxLayout(dlg)
    label = QLabel(f"Aktywna warstwa:<br><br>&nbsp;&nbsp;&nbsp;&nbsp;<b>{layer_name_html}</b><br><br>{summary_html}")
    label.setTextFormat(Qt.RichText); label.setWordWrap(True)
    layout.addWidget(label)

    group = QGroupBox("Wybierz sposób przeliczenia:", dlg)
    g_layout = QVBoxLayout(group)
    rb1 = QRadioButton("1 – numeracja ciągła dla wszystkich symboli", group)
    rb2 = QRadioButton("2 – numeracja odrębna dla poszczególnych symboli", group)
    rb1.setChecked(True)
    g_layout.addWidget(rb1); g_layout.addWidget(rb2)
    layout.addWidget(group)

    btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dlg)
    layout.addWidget(btn_box); btn_box.accepted.connect(dlg.accept); btn_box.rejected.connect(dlg.reject)

    if dlg.exec_() != QDialog.Accepted: return None
    return 1 if rb1.isChecked() else 2


# ---------------------------------------------------------
# GŁÓWNA FUNKCJA
# ---------------------------------------------------------

def run(iface):
    layer = iface.activeLayer()
    if not isinstance(layer, QgsVectorLayer):
        QMessageBox.warning(iface.mainWindow(), "Błąd", "Aktywna warstwa nie jest warstwą wektorową.")
        return

    layer_name_html = f"<span style='color:#8e44ad;'>{layer.name()}</span>"
    field_names = [f.name() for f in layer.fields()]

    if "symbol" not in field_names:
        QMessageBox.warning(iface.mainWindow(), "Błąd", "Brak pola 'symbol'.")
        return

    null_count = sum(1 for f in layer.getFeatures() if not str(f["symbol"]).strip())
    
    if null_count > 0:
        if not _show_symbol_check_dialog(iface.mainWindow(), True, null_count, layer_name_html):
            return

    scheme = _ask_numbering_scheme(iface.mainWindow(), layer_name_html)
    if scheme is None: return

    oznaczenie_field = "oznaczenie"
    lokalny_field = _find_lokalny_field(layer)
    if lokalny_field is None: return

    oznaczenia_info = _collect_numbers_from_oznaczenie(layer, oznaczenie_field)
    nums = {info["num"] for info in oznaczenia_info.values() if info["num"] is not None}

    # ZMIANA: Skrypt nie przerywa pracy, jeśli nie ma liczb. 
    # Przygotowuje podsumowanie informujące o braku dotychczasowej numeracji.
    summary_parts = []
    if not nums:
        summary_parts.append("⚠️ <b>Brak dotychczasowej części liczbowej w polach 'oznaczenie'.</b>")
        summary_parts.append("Nowa numeracja zostanie nadana od wartości 1.")
    else:
        if scheme == 1:
            missing = _compute_missing_numbers(nums)
            if missing: summary_parts.append(f"❌ Brakuje: {', '.join(map(str, missing))}")
        else:
            m_bs = _compute_missing_by_symbol(layer, oznaczenia_info, "symbol")
            if not m_bs:
                summary_parts.append("✅ Numeracja wg symboli wydaje się ciągła.")
            for s, m in m_bs.items(): summary_parts.append(f"❌ <b>{s}</b>: {', '.join(map(str, m))}")

    mode = _show_mode_dialog(iface.mainWindow(), layer_name_html, "<br>".join(summary_parts))
    if mode is None: return

    tmp_layer = _create_temp_layer_like(layer, "(po naprawie oznaczen)")

    if mode == 1:
        _renumber_mode_1_to_temp(layer, tmp_layer, oznaczenie_field, lokalny_field, "symbol")
    else:
        _renumber_mode_2_to_temp(layer, tmp_layer, oznaczenie_field, lokalny_field, "symbol")

    _add_layer_to_do_gml_group(tmp_layer)

    QMessageBox.information(
        iface.mainWindow(),
        "Sukces",
        f"Przeliczono warstwę i dodano ją do grupy <b>DODAJ ATRYBUTY GML</b> jako:<br><br><b>{tmp_layer.name()}</b>"
    )