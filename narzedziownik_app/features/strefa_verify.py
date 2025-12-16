# -*- coding: utf-8 -*-
"""
Narzędziownik APP - Wtyczka QGIS
Informacje o autorach, repozytorium: https://github.com/tomasz-gietkowski-geoanalityka/narzedziownik_app
Dokumentacja: https://akademia.geoanalityka.pl/courses/narzedziownik-app-dokumentacja/
Licencja: GNU GPL v3.0 (https://www.gnu.org/licenses/gpl-3.0.html)
"""

import os
import json
import traceback

from qgis.PyQt.QtWidgets import QMessageBox
from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsWkbTypes,
    QgsFeature,
    QgsField,
    QgsPalLayerSettings,
    QgsTextFormat,
    QgsVectorLayerSimpleLabeling,
    Qgis,
    QgsMessageLog,
    edit,
)

# ---------------------------------------------------------
# HELPERY
# ---------------------------------------------------------

def _qgs_string_field(name: str) -> QgsField:
    try:
        from qgis.PyQt.QtCore import QMetaType
        try:
            return QgsField(name, QMetaType.Type.QString)
        except TypeError:
            return QgsField(name, QVariant.String)
    except Exception:
        return QgsField(name, QVariant.String)

def _show_msg(parent, title: str, html: str):
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(html)
    box.setIcon(QMessageBox.NoIcon)
    box.exec()

def _analyzed_layer_prefix_html(layer_name: str | None) -> str:
    if not layer_name: return ""
    return f"Warstwa analizowana: <span style='color:#8e44ad;'><b>{str(layer_name)}</b></span><br><br>"

# ---------------------------------------------------------
# ŁADOWANIE SŁOWNIKÓW
# ---------------------------------------------------------

def _load_symbol_dictionary(plugin_dir: str, analyzed_layer_name: str | None = None, parent=None):
    json_path = os.path.join(plugin_dir, "resources", "config", "profil_podstawowy.json")
    if not os.path.exists(json_path):
        _show_msg(parent, "Błąd słownika", f"Nie znaleziono: {json_path}")
        return None
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {str(i["symbolSpPOG"]).strip(): i["przeznaczeniaPodstSpPOG"] for i in data if i.get("symbolSpPOG")}
    except Exception as e:
        _show_msg(parent, "Błąd słownika", f"Błąd JSON: {e}")
        return None

def _load_min_biol_dict(plugin_dir: str, analyzed_layer_name: str | None = None, parent=None) -> dict[str, int] | None:
    json_path = os.path.join(plugin_dir, "resources", "config", "min_udzi_biol.json")
    if not os.path.exists(json_path):
        return None 
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {item["symbol"]: item["min_udzi_biol"] for item in data if "symbol" in item}
    except Exception:
        return None

# ---------------------------------------------------------
# KONTROLE
# ---------------------------------------------------------

def _check_biol_surface(layer: QgsVectorLayer, biol_dict: dict[str, int]) -> tuple[bool, str, list[int]]:
    sym_idx = layer.fields().indexFromName("symbol")
    biol_idx = layer.fields().indexFromName("minUdzialPowierzchniBiologicznieCzynnej")
    
    if biol_idx == -1:
        return False, "Brak pola <i>minUdzialPowierzchniBiologicznieCzynnej</i> w warstwie.", []

    bad_ids = []
    errors_summary = {} 

    for f in layer.getFeatures():
        sym = str(f[sym_idx]).strip()
        if sym not in biol_dict:
            continue
        
        required = biol_dict[sym]
        val = f[biol_idx]
        current = 0 if val is None or val == QVariant() else float(val)

        is_bad = False
        if sym == "SK": 
            if current != required: is_bad = True
        else:
            if current < required: is_bad = True
        
        if is_bad:
            bad_ids.append(f.id())
            errors_summary[sym] = (current, required)

    if bad_ids:
        lines = [f"&nbsp;&nbsp;&nbsp;&nbsp;<b>{s}</b>: podano {v[0]}%, wymagane {v[1]}%" for s, v in errors_summary.items()]
        msg = (
            "❌ Wykryto nieprawidłową wartość wskaźnika powierzchni biologicznie czynnej:<br><br>" + 
            "<br>".join(lines) + "<br><br>" +
            "W grupie <b>KONTROLA STREF</b> dodano warstwę:<br>"
            "<span style='color:#8e44ad;'><b>Strefy z błędnym wskaźnikiem biol.</b></span>"
        )
        return False, msg, bad_ids

    return True, "", []

# ---------------------------------------------------------
# TWORZENIE WARSTW W PAMIĘCI
# ---------------------------------------------------------

def _create_error_layer(src_layer: QgsVectorLayer, name: str, feature_ids: list[int]) -> QgsVectorLayer:
    uri = f"{QgsWkbTypes.displayString(src_layer.wkbType())}?crs={src_layer.crs().authid()}"
    err_layer = QgsVectorLayer(uri, name, "memory")
    pr = err_layer.dataProvider()
    pr.addAttributes(src_layer.fields())
    err_layer.updateFields()
    
    feats = [f for f in src_layer.getFeatures() if f.id() in feature_ids]
    pr.addFeatures(feats)
    
    pal = QgsPalLayerSettings()
    pal.fieldName = "symbol"
    pal.enabled = True
    err_layer.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    err_layer.setLabelsEnabled(True)
    return err_layer

def _add_to_kontrola_stref_group(layer):
    root = QgsProject.instance().layerTreeRoot()
    group = root.findGroup("KONTROLA STREF") or root.addGroup("KONTROLA STREF")
    QgsProject.instance().addMapLayer(layer, False)
    group.addLayer(layer)

# ---------------------------------------------------------
# GŁÓWNA FUNKCJA
# ---------------------------------------------------------

def run(iface, plugin_dir: str):
    try:
        layer = iface.activeLayer()
        if not layer or not isinstance(layer, QgsVectorLayer):
            _show_msg(iface.mainWindow(), "Błąd", "Wybierz warstwę wektorową stref.")
            return

        analyzed_name = layer.name()
        analyzed_prefix = _analyzed_layer_prefix_html(analyzed_name)
        symbol_idx = layer.fields().indexFromName("symbol")
        
        if symbol_idx == -1:
            _show_msg(iface.mainWindow(), "Błąd", analyzed_prefix + "Brak pola 'symbol'.")
            return

        # KROK 1: Walidacja wypełnienia symbolu
        bad_empty_ids = [f.id() for f in layer.getFeatures() if not f[symbol_idx] or not str(f[symbol_idx]).strip()]
        if bad_empty_ids:
            err_empty = _create_error_layer(layer, "Strefy z pustym symbolem", bad_empty_ids)
            _add_to_kontrola_stref_group(err_empty)
            _show_msg(iface.mainWindow(), "Błąd", analyzed_prefix + "Wykryto obiekty bez wpisanego symbolu. Uzupełnij dane.")
            return

        # KROK 2: Kontrola zgodności symboli (Kontrola 1)
        symbol_dict = _load_symbol_dictionary(plugin_dir, analyzed_name, iface.mainWindow())
        if not symbol_dict: return
        
        bad_sym_ids = [f.id() for f in layer.getFeatures() if str(f[symbol_idx]).strip() not in symbol_dict]
        
        if bad_sym_ids:
            err_lyr = _create_error_layer(layer, "Strefy z błędnym symbolem", bad_sym_ids)
            _add_to_kontrola_stref_group(err_lyr)
            _show_msg(iface.mainWindow(), "Błędne symbole", 
                analyzed_prefix + 
                "❌ Wykryto symbole niezgodne z rozporządzeniem.<br><br>"
                "W grupie <b>KONTROLA STREF</b> dodano warstwę:<br>"
                "<span style='color:#8e44ad;'><b>Strefy z błędnym symbolem</b></span>"
            )
            return

        # KROK 3: Kontrola wskaźnika biologicznego (Kontrola 1.1)
        biol_dict = _load_min_biol_dict(plugin_dir, analyzed_name, iface.mainWindow())
        if biol_dict:
            ok_biol, msg_biol, bad_biol_ids = _check_biol_surface(layer, biol_dict)
            if not ok_biol:
                err_lyr_biol = _create_error_layer(layer, "Strefy z błędnym wskaźnikiem biol.", bad_biol_ids)
                _add_to_kontrola_stref_group(err_lyr_biol)
                _show_msg(iface.mainWindow(), "Błędny wskaźnik", analyzed_prefix + msg_biol)
                return

        _show_msg(iface.mainWindow(), "Sukces", analyzed_prefix + "✅ Symbole oraz wskaźniki biologiczne są poprawne.")

    except Exception:
        QgsMessageLog.logMessage(traceback.format_exc(), "Narzędziownik APP", Qgis.Critical)
        _show_msg(iface.mainWindow(), "Błąd", "Wystąpił błąd krytyczny. Szczegóły w logach.")