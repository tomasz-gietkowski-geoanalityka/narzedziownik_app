# -*- coding: utf-8 -*-
"""
Narzędziownik APP - Wtyczka QGIS
Informacje o autorach, repozytorium: https://github.com/tomasz-gietkowski-geoanalityka/narzedziownik_app
Dokumentacja: https://akademia.geoanalityka.pl/courses/narzedziownik-app-dokumentacja/
Licencja: GNU GPL v3.0 (https://www.gnu.org/licenses/gpl-3.0.html)
"""

import traceback
from datetime import datetime, timezone

from qgis.PyQt.QtWidgets import (
    QMessageBox, QDialog, QVBoxLayout, QLabel, QLineEdit, 
    QDialogButtonBox, QDateEdit, QComboBox, QCheckBox
)
from qgis.PyQt.QtCore import Qt, QDate

from qgis.core import (
    QgsProject, QgsVectorLayer, QgsWkbTypes, QgsFeature, 
    QgsMessageLog, Qgis, QgsFeatureRequest
)

# ---------------------------------------------------------
# HELPER: Konwersja lokalnej daty (00:00:00) na obiekty UTC
# ---------------------------------------------------------

def _get_utc_data_from_local_qdate(qdate: QDate):
    """
    Przelicza QDate na 00:00:00 czasu lokalnego, a następnie na UTC.
    Zwraca format ISO (Z) oraz kompaktowy dla wersjaId.
    """
    local_dt = datetime(qdate.year(), qdate.month(), qdate.day(), 0, 0, 0)
    local_with_tz = local_dt.astimezone()
    utc_dt = local_with_tz.astimezone(timezone.utc)
    
    iso_format = utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    compact_format = utc_dt.strftime("%Y%m%dT%H%M%S")
    
    return iso_format, compact_format

# ---------------------------------------------------------
# HELPER: Identyfikacja rodzaju danych
# ---------------------------------------------------------

def _identify_layer_type(layer: QgsVectorLayer) -> str:
    """
    Rozpoznaje rodzaj danych na podstawie atrybutów pierwszego obiektu.
    """
    req = QgsFeatureRequest().setLimit(1)
    first_feat = next(layer.getFeatures(req), None)
    
    if not first_feat:
        return "NieznanyRodzajDanych"

    fields = layer.fields()
    sym_idx = fields.lookupField("symbol")
    
    # Logika 'symbol'
    if sym_idx != -1:
        val = str(first_feat.attribute(sym_idx)).strip().upper()
        if val == "OUZ":
            return "ObszarUzupelnieniaZabudowy"
        if val == "OZS":
            return "ObszarZabudowySrodmiejskiej"
        if val != "NULL" and val != "":
            return "StrefaPlanistyczna"

    # Logika 'tytul' (jeśli nie dopasowano symbolu lub pola brak)
    tit_idx = fields.lookupField("tytul")
    if tit_idx != -1:
        val_tit = str(first_feat.attribute(tit_idx)).lower()
        if "plan ogólny" in val_tit:
            return "AktPlanowaniaPrzestrzennego"

    # Fallback
    return "StrefaPlanistyczna"

# ---------------------------------------------------------
# HELPER: MESSAGEBOX
# ---------------------------------------------------------

def _msg_box(parent, title: str, text: str, icon: QMessageBox.Icon, min_width: int = 500):
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setIcon(icon)
    box.setTextFormat(Qt.RichText)
    box.setText(text)
    box.setStyleSheet(f"QLabel{{min-width: {min_width}px;}}")
    box.exec()

# ---------------------------------------------------------
# DIALOG PARAMETRÓW
# ---------------------------------------------------------

def _ask_gml_params(parent) -> tuple:
    dlg = QDialog(parent)
    dlg.setWindowTitle("Uzupełnij pola dla GML APP (Grupa DODAJ ATRYBUTY GML)")
    dlg.setFixedWidth(500)
    layout = QVBoxLayout(dlg)

    layout.addWidget(QLabel("<b>Parametry wspólne dla przetwarzanych warstw:</b>"))

    # 1. Poziom hierarchii aktu
    layout.addWidget(QLabel("Poziom hierarchii aktu:"))
    cb_hierarchy = QComboBox(dlg)
    cb_hierarchy.addItems(["regionalny", "lokalny", "sublokalny"])
    cb_hierarchy.setCurrentIndex(1)
    layout.addWidget(cb_hierarchy)

    # 2. Typ APP
    layout.addWidget(QLabel("Typ APP:"))
    cb_app_type = QComboBox(dlg)
    cb_app_type.addItems([
        "plan ogólny gminy",
        "miejscowy plan zagospodarowania przestrzennego",
        "plan zagospodarowania przestrzennego województwa",
        "studium uwarunkowań i kier. zagosp. przestrz. gminy",
        "miejscowy plan odbudowy",
        "miejscowy plan rewitalizacji"
    ])
    layout.addWidget(cb_app_type)

    layout.addWidget(QLabel("Przestrzeń nazw:"))
    le_ns = QLineEdit(dlg)
    le_ns.setPlaceholderText("np. PL.ZIPPZP.9360/041003-POG")
    layout.addWidget(le_ns)

    layout.addWidget(QLabel("Identyfikator lokalny APP (Prefix):"))
    le_id_local = QLineEdit(dlg)
    le_id_local.setPlaceholderText("np. 1POG")
    layout.addWidget(le_id_local)

    layout.addWidget(QLabel("Status aktu:"))
    cb_status = QComboBox(dlg)
    cb_status.addItems(["w opracowaniu", "prawnie wiążący lub realizowany", 
                        "w trakcie przyjmowania", "nieaktualny"])
    layout.addWidget(cb_status)

    layout.addWidget(QLabel("Początek obowiązywania GML:"))
    de_date_start = QDateEdit(dlg)
    de_date_start.setCalendarPopup(True)
    de_date_start.setDate(QDate.currentDate())
    layout.addWidget(de_date_start)

    layout.addWidget(QLabel("Koniec obowiązywania GML (opcjonalnie):"))
    check_end_date = QCheckBox("Uwzględnij datę końca obowiązywania")
    layout.addWidget(check_end_date)
    
    de_date_end = QDateEdit(dlg)
    de_date_end.setCalendarPopup(True)
    de_date_end.setDate(QDate.currentDate().addYears(10))
    de_date_end.setEnabled(False) 
    layout.addWidget(de_date_end)
    check_end_date.toggled.connect(de_date_end.setEnabled)

    btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dlg)
    layout.addWidget(btn_box)
    btn_box.accepted.connect(dlg.accept)
    btn_box.rejected.connect(dlg.reject)

    if dlg.exec_() != QDialog.Accepted:
        return (None,) * 7

    return (cb_hierarchy.currentText(), cb_app_type.currentText(), le_ns.text().strip(), 
            le_id_local.text().strip(), de_date_start.date(), cb_status.currentText(), 
            de_date_end.date() if check_end_date.isChecked() else None)

# ---------------------------------------------------------
# GŁÓWNA FUNKCJA
# ---------------------------------------------------------

def run(iface, plugin_dir: str = None):
    try:
        project = QgsProject.instance()
        root = project.layerTreeRoot()
        
        # Znalezienie grupy DODAJ ATRYBUTY GML
        src_group = root.findGroup("DODAJ ATRYBUTY GML")
        if not src_group:
            _msg_box(iface.mainWindow(), "Błąd", "Nie znaleziono grupy o nazwie '<b>DODAJ ATRYBUTY GML</b>'.", icon=QMessageBox.Warning)
            return

        # Pobranie warstw wektorowych z grupy
        layers_to_process = []
        for node in src_group.findLayers():
            lyr = node.layer()
            if lyr and isinstance(lyr, QgsVectorLayer):
                layers_to_process.append(lyr)

        if not layers_to_process:
            _msg_box(iface.mainWindow(), "Błąd", "Grupa 'DODAJ ATRYBUTY GML' nie zawiera żadnych warstw wektorowych.", icon=QMessageBox.Warning)
            return

        # Pobranie parametrów
        res = _ask_gml_params(iface.mainWindow())
        if res[0] is None or not res[2] or not res[3]: 
            return
        
        poziom_h, typ_app, ns, prefix, d_start, status, d_end = res

        # Czasy UTC (zsynchronizowane)
        poczatek_iso, wersja_id_compact = _get_utc_data_from_local_qdate(d_start)
        obow_od_iso = d_start.toString("yyyy-MM-dd")
        
        koniec_vers_iso, obow_do_iso = (None, None)
        if d_end:
            koniec_vers_iso, _ = _get_utc_data_from_local_qdate(d_end)
            obow_do_iso = d_end.toString("yyyy-MM-dd")

        # Grupa wyjściowa
        out_group = root.findGroup("WARSTWY GOTOWE DO GML") or root.addGroup("WARSTWY GOTOWE DO GML")
        
        processed_count = 0
        for src_layer in layers_to_process:
            # Automatyczne ustalenie nazwy na podstawie atrybutów
            rodzaj_danych = _identify_layer_type(src_layer)
            
            uri = f"{QgsWkbTypes.displayString(src_layer.wkbType())}?crs={src_layer.crs().authid()}"
            out_layer = QgsVectorLayer(uri, rodzaj_danych, "memory")
            pr = out_layer.dataProvider()
            pr.addAttributes(src_layer.fields())
            out_layer.updateFields()

            f_idx = {out_layer.fields()[i].name(): i for i in range(len(out_layer.fields()))}
            ozn_idx_src = src_layer.fields().lookupField("oznaczenie")
            
            new_features = []
            for f in src_layer.getFeatures():
                new_f = QgsFeature(out_layer.fields())
                new_f.setGeometry(f.geometry())
                attrs = list(f.attributes())
                
                # Zapewnienie odpowiedniej długości listy atrybutów
                if len(attrs) < len(out_layer.fields()):
                    attrs.extend([None] * (len(out_layer.fields()) - len(attrs)))

                # Wypełnianie pól technicznych
                if "przestrzenNazw" in f_idx: attrs[f_idx["przestrzenNazw"]] = ns
                if "wersjaId" in f_idx: attrs[f_idx["wersjaId"]] = wersja_id_compact
                if "poczatekWersjiObiektu" in f_idx: attrs[f_idx["poczatekWersjiObiektu"]] = poczatek_iso
                if "koniecWersjiObiektu" in f_idx: attrs[f_idx["koniecWersjiObiektu"]] = koniec_vers_iso
                if "obowiazujeOd" in f_idx: attrs[f_idx["obowiazujeOd"]] = obow_od_iso
                if "obowiazujeDo" in f_idx: attrs[f_idx["obowiazujeDo"]] = obow_do_iso
                if "status" in f_idx: attrs[f_idx["status"]] = status
                if "poziomHierarchii" in f_idx: attrs[f_idx["poziomHierarchii"]] = poziom_h
                if "typPlanu" in f_idx: attrs[f_idx["typPlanu"]] = typ_app
                if "charakterUstalenia" in f_idx: attrs[f_idx["charakterUstalenia"]] = "ogólnie wiążące"
                
                # --- LOGIKA LOKALNY ID (WYJĄTEK DLA PLANU OGÓLNEGO) ---
                if "lokalnyId" in f_idx:
                    if rodzaj_danych == "AktPlanowaniaPrzestrzennego":
                        # Wyjątek: dla Planu Ogólnego wstawiamy tylko czysty prefix
                        attrs[f_idx["lokalnyId"]] = prefix
                    else:
                        # Standard: Prefix-Oznaczenie
                        ozn_val = str(f.attribute(ozn_idx_src)) if ozn_idx_src != -1 and f.attribute(ozn_idx_src) is not None else "BRAK"
                        attrs[f_idx["lokalnyId"]] = f"{prefix}-{ozn_val}"

                new_f.setAttributes(attrs)
                new_features.append(new_f)

            out_layer.startEditing()
            out_layer.addFeatures(new_features)
            out_layer.commitChanges()
            out_layer.updateExtents()
            
            project.addMapLayer(out_layer, False)
            out_group.addLayer(out_layer)
            processed_count += 1

        _msg_box(iface.mainWindow(), "Sukces", f"Przetworzono poprawnie <b>{processed_count}</b> warstw/y.", icon=QMessageBox.Information)

    except Exception as e:
        err_info = traceback.format_exc()
        QgsMessageLog.logMessage(err_info, "Narzędziownik APP", Qgis.Critical)
        _msg_box(iface.mainWindow(), "Błąd krytyczny", f"Wystąpił błąd: <b>{str(e)}</b>", icon=QMessageBox.Critical)