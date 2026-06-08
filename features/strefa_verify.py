# -*- coding: utf-8 -*-
"""
Narzędziownik APP - Wtyczka QGIS
Informacje o autorach, repozytorium: https://github.com/tomasz-gietkowski-geoanalityka/narzedziownik_app
Dokumentacja: https://akademia.geoanalityka.pl/courses/narzedziownik-app-dokumentacja/
Licencja: GNU GPL v3.0 (https://www.gnu.org/licenses/gpl-3.0.html)
"""
from __future__ import annotations

import os
import json
import traceback

from qgis.PyQt.QtWidgets import QMessageBox
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QColor
from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsWkbTypes,
    QgsFeature,
    QgsField,
    QgsPalLayerSettings,
    QgsTextFormat,
    QgsTextBufferSettings,
    QgsVectorLayerSimpleLabeling,
    Qgis,
    QgsMessageLog,
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
    if not layer_name:
        return ""
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


def _load_profil_dodatkowy_dict(plugin_dir: str, analyzed_layer_name: str | None = None, parent=None) -> dict[str, list[str]] | None:
    """Ładuje słownik dozwolonych wartości profilDodatkowy dla każdego symbolu."""
    json_path = os.path.join(plugin_dir, "resources", "config", "profil_dodatkowy.json")
    if not os.path.exists(json_path):
        _show_msg(parent, "Błąd słownika", f"Nie znaleziono: {json_path}")
        return None
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {item["symbol"]: item["opis"] for item in data if "symbol" in item and "opis" in item}
    except Exception as e:
        _show_msg(parent, "Błąd słownika", f"Błąd JSON profil_dodatkowy: {e}")
        return None

# ---------------------------------------------------------
# KONTROLE
# ---------------------------------------------------------


def _parse_profile_values(value) -> list[str]:
    """Parsuje wartość pola profilu (może być lista, tekst z separatorami, lub pojedyncza wartość)."""
    if value is None or value == QVariant():
        return []

    # Jeśli to lista (PostgreSQL array)
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if v]

    # Jeśli to tekst (może zawierać separatory)
    val_str = str(value).strip()
    if not val_str:
        return []

    # Sprawdź czy zawiera separator (przecinek, średnik, lub znak nowej linii)
    if ',' in val_str:
        return [v.strip() for v in val_str.split(',') if v.strip()]
    elif ';' in val_str:
        return [v.strip() for v in val_str.split(';') if v.strip()]
    elif '\n' in val_str:
        return [v.strip() for v in val_str.split('\n') if v.strip()]
    else:
        # Pojedyncza wartość
        return [val_str]


def _check_profil_podstawowy(layer: QgsVectorLayer, symbol_dict: dict[str, list[str]]) -> tuple[bool, str, list[int]]:
    """Sprawdza zgodność wartości w polu profilPodstawowy z dozwolonymi wartościami dla danego symbolu."""
    sym_idx = layer.fields().indexFromName("symbol")
    prof_idx = layer.fields().indexFromName("profilPodstawowy")

    if prof_idx == -1:
        return True, "", []  # Pole nie istnieje - pomijamy kontrolę

    bad_ids = []
    errors_summary = {}  # symbol -> lista błędnych wartości

    for f in layer.getFeatures():
        sym = str(f[sym_idx]).strip()
        if sym not in symbol_dict:
            continue  # Symbol będzie sprawdzony w innej kontroli

        allowed_values = symbol_dict[sym]
        prof_values = _parse_profile_values(f[prof_idx])

        # Sprawdź czy wszystkie wartości w profilPodstawowy są dozwolone dla tego symbolu
        bad_values = [v for v in prof_values if v not in allowed_values]

        if bad_values:
            bad_ids.append(f.id())
            if sym not in errors_summary:
                errors_summary[sym] = set()
            errors_summary[sym].update(bad_values)

    if bad_ids:
        lines = []
        for sym, bad_vals in errors_summary.items():
            vals_str = ", ".join(f"<i>{v}</i>" for v in sorted(bad_vals))
            lines.append(f"&nbsp;&nbsp;&nbsp;&nbsp;<b>{sym}</b>: {vals_str}")

        msg = (
            "❌ <span style='color:#e43d30;'>Wykryto niedozwolone wartości w polu <b>profilPodstawowy</b>:</span><br><br>"
            + "<br>".join(lines) + "<br><br>"
            + "W grupie <b>KONTROLA STREF</b> dodano warstwę:<br>"
            "<span style='color:#8e44ad;'><b>Strefy z błędnym profilPodstawowy</b></span>"
        )
        return False, msg, bad_ids

    return True, "", []


def _check_profil_dodatkowy(layer: QgsVectorLayer, dodatkowy_dict: dict[str, list[str]]) -> tuple[bool, str, list[int]]:
    """Sprawdza zgodność wartości w polu profilDodatkowy z dozwolonymi wartościami dla danego symbolu."""
    sym_idx = layer.fields().indexFromName("symbol")
    prof_idx = layer.fields().indexFromName("profilDodatkowy")

    if prof_idx == -1:
        return True, "", []  # Pole nie istnieje - pomijamy kontrolę

    bad_ids = []
    errors_summary = {}  # symbol -> lista błędnych wartości

    for f in layer.getFeatures():
        sym = str(f[sym_idx]).strip()
        if sym not in dodatkowy_dict:
            continue  # Symbol będzie sprawdzony w innej kontroli

        allowed_values = dodatkowy_dict[sym]
        prof_values = _parse_profile_values(f[prof_idx])

        # Sprawdź czy wszystkie wartości w profilDodatkowy są dozwolone dla tego symbolu
        bad_values = [v for v in prof_values if v not in allowed_values]

        if bad_values:
            bad_ids.append(f.id())
            if sym not in errors_summary:
                errors_summary[sym] = set()
            errors_summary[sym].update(bad_values)

    if bad_ids:
        lines = []
        for sym, bad_vals in errors_summary.items():
            vals_str = ", ".join(f"<i>{v}</i>" for v in sorted(bad_vals))
            lines.append(f"&nbsp;&nbsp;&nbsp;&nbsp;<b>{sym}</b>: {vals_str}")

        msg = (
            "❌ <span style='color:#e43d30;'>Wykryto niedozwolone wartości w polu <b>profilDodatkowy</b>:</span><br><br>"
            + "<br>".join(lines) + "<br><br>"
            + "W grupie <b>KONTROLA STREF</b> dodano warstwę:<br>"
            "<span style='color:#8e44ad;'><b>Strefy z błędnym profilDodatkowy</b></span>"
        )
        return False, msg, bad_ids

    return True, "", []


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
            if current != required:
                is_bad = True
        else:
            if current < required:
                is_bad = True

        if is_bad:
            bad_ids.append(f.id())
            errors_summary[sym] = (current, required)

    if bad_ids:
        lines = [f"&nbsp;&nbsp;&nbsp;&nbsp;<b>{s}</b>: podano {v[0]}%, wymagane {v[1]}%" for s, v in errors_summary.items()]
        msg = (
            "❌ <span style='color:#e43d30;'>Wykryto nieprawidłową wartość <b>wskaźnika powierzchni biologicznie czynnej</b>:</span><br><br>"
            + "<br>".join(lines) + "<br><br>"
            + "W grupie <b>KONTROLA STREF</b> dodano warstwę:<br>"
            "<span style='color:#8e44ad;'><b>Strefy z błędnym wskaźnikiem biol.</b></span>"
        )
        return False, msg, bad_ids

    return True, "", []

# ---------------------------------------------------------
# TWORZENIE WARSTW W PAMIĘCI
# ---------------------------------------------------------


def _configure_labels_with_buffer(layer: QgsVectorLayer, field_name: str = "oznaczenie"):
    """
    Konfiguruje etykiety warstwy z pola field_name z efektem buffer (obrys tekstu).
    Jeśli pole field_name nie istnieje, próbuje użyć innych pól w kolejności: symbol, lokalnyId.
    """
    # Sprawdź czy pole istnieje
    if layer.fields().indexFromName(field_name) == -1:
        # Spróbuj alternatywnych pól
        for alt_field in ["symbol", "lokalnyId", "idIIP"]:
            if layer.fields().indexFromName(alt_field) != -1:
                field_name = alt_field
                break
        else:
            # Jeśli żadne pole nie istnieje, użyj pierwszego dostępnego pola
            if layer.fields().count() > 0:
                field_name = layer.fields()[0].name()
            else:
                return  # Brak pól w warstwie

    # Konfiguracja etykiet
    pal = QgsPalLayerSettings()
    pal.fieldName = field_name
    pal.enabled = True

    # Konfiguracja formatu tekstu z bufferem
    text_format = QgsTextFormat()

    # Konfiguracja buffera (obrys tekstu)
    buffer = QgsTextBufferSettings()
    buffer.setEnabled(True)
    buffer.setSize(1.0)  # Rozmiar buffera
    buffer.setColor(QColor(255, 255, 255))  # Biały kolor buffera

    text_format.setBuffer(buffer)
    pal.setFormat(text_format)

    layer.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    layer.setLabelsEnabled(True)


def _create_error_layer(src_layer: QgsVectorLayer, name: str, feature_ids: list[int], label_field: str = "oznaczenie") -> QgsVectorLayer:
    uri = f"{QgsWkbTypes.displayString(src_layer.wkbType())}?crs={src_layer.crs().authid()}"
    err_layer = QgsVectorLayer(uri, name, "memory")
    pr = err_layer.dataProvider()
    pr.addAttributes(src_layer.fields())
    err_layer.updateFields()

    # Pobierz obiekty źródłowe
    src_feats = [f for f in src_layer.getFeatures() if f.id() in feature_ids]

    # Utwórz nowe obiekty z polami docelowej warstwy i skopiuj atrybuty
    new_feats = []
    for src_feat in src_feats:
        new_feat = QgsFeature(err_layer.fields())
        new_feat.setGeometry(src_feat.geometry())
        new_feat.setAttributes(src_feat.attributes())
        new_feats.append(new_feat)

    pr.addFeatures(new_feats)

    # Konfiguracja etykiet z efektem buffer
    _configure_labels_with_buffer(err_layer, label_field)

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
            _show_msg(
                iface.mainWindow(), "Błąd",
                analyzed_prefix
                + "❌ <span style='color:#e43d30;'>Wykryto obiekty bez uzupełnionego pola <b>symbol</b>."
                " Uzupełnij dane, aby przejść do dalszych kontroli.</span><br><br>"
                "W grupie <b>KONTROLA STREF</b> dodano warstwę:<br>"
                "<span style='color:#8e44ad;'><b>Strefy z pustym symbolem</b></span>"
            )
            return

        # KROK 2: Kontrola zgodności symboli (Kontrola 1)
        symbol_dict = _load_symbol_dictionary(plugin_dir, analyzed_name, iface.mainWindow())
        if not symbol_dict:
            _show_msg(iface.mainWindow(), "Błąd słownika", analyzed_prefix + "Słownik symboli jest pusty lub nie mógł zostać załadowany.")
            return

        bad_sym_ids = [f.id() for f in layer.getFeatures() if str(f[symbol_idx]).strip() not in symbol_dict]

        if bad_sym_ids:
            err_lyr = _create_error_layer(layer, "Strefy z błędnym symbolem", bad_sym_ids, label_field="symbol")
            _add_to_kontrola_stref_group(err_lyr)
            _show_msg(
                iface.mainWindow(), "Błędne symbole",
                analyzed_prefix
                + "❌ <span style='color:#e43d30;'>Wykryto symbole niezgodne z rozporządzeniem.</span><br>"
                "<span style='color:#e43d30;'><b>Popraw symbole stref, aby przejść do kontroli wartości profili.<b></span><br><br>"
                "W grupie <b>KONTROLA STREF</b> dodano warstwę:<br>"
                "<span style='color:#8e44ad;'><b>Strefy z błędnym symbolem</b></span>"
            )
            return

        # KROK 3: Kontrola profilPodstawowy (Kontrola 2)
        ok_prof_podst, msg_prof_podst, bad_prof_podst_ids = _check_profil_podstawowy(layer, symbol_dict)
        if not ok_prof_podst:
            err_lyr_prof_podst = _create_error_layer(layer, "Strefy z błędnym profilPodstawowy", bad_prof_podst_ids)
            _add_to_kontrola_stref_group(err_lyr_prof_podst)
            _show_msg(iface.mainWindow(), "Błędny profil podstawowy", analyzed_prefix + msg_prof_podst)
            return

        # KROK 4: Kontrola profilDodatkowy (Kontrola 3)
        dodatkowy_dict = _load_profil_dodatkowy_dict(plugin_dir, analyzed_name, iface.mainWindow())
        if dodatkowy_dict:
            ok_prof_dodat, msg_prof_dodat, bad_prof_dodat_ids = _check_profil_dodatkowy(layer, dodatkowy_dict)
            if not ok_prof_dodat:
                err_lyr_prof_dodat = _create_error_layer(layer, "Strefy z błędnym profilDodatkowy", bad_prof_dodat_ids)
                _add_to_kontrola_stref_group(err_lyr_prof_dodat)
                _show_msg(iface.mainWindow(), "Błędny profil dodatkowy", analyzed_prefix + msg_prof_dodat)
                return

        # KROK 5: Kontrola wskaźnika biologicznego (Kontrola 4)
        biol_dict = _load_min_biol_dict(plugin_dir, analyzed_name, iface.mainWindow())
        if biol_dict:
            ok_biol, msg_biol, bad_biol_ids = _check_biol_surface(layer, biol_dict)
            if not ok_biol:
                err_lyr_biol = _create_error_layer(layer, "Strefy z błędnym wskaźnikiem biol.", bad_biol_ids)
                _add_to_kontrola_stref_group(err_lyr_biol)
                _show_msg(iface.mainWindow(), "Błędny wskaźnik", analyzed_prefix + msg_biol)
                return

        _show_msg(iface.mainWindow(), "Sukces", analyzed_prefix + "✅ Wszystkie kontrole przeszły pomyślnie:<br><br>"
                  "&nbsp;&nbsp;• Symbole są poprawne<br>"
                  "&nbsp;&nbsp;• Profile podstawowe są zgodne z rozporządzeniem<br>"
                  "&nbsp;&nbsp;• Profile dodatkowe są zgodne z rozporządzeniem<br>"
                  "&nbsp;&nbsp;• Wskaźniki biologiczne są poprawne")

    except Exception:
        QgsMessageLog.logMessage(traceback.format_exc(), "Narzędziownik APP", Qgis.Critical)
        _show_msg(iface.mainWindow(), "Błąd", "Wystąpił błąd krytyczny. Szczegóły w logach.")
