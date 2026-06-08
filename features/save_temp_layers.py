# -*- coding: utf-8 -*-
"""
Narzędziownik APP - Wtyczka QGIS
Informacje o autorach, repozytorium: https://github.com/tomasz-gietkowski-geoanalityka/narzedziownik_app
Dokumentacja: https://akademia.geoanalityka.pl/courses/narzedziownik-app-dokumentacja/
Licencja: GNU GPL v3.0 (https://www.gnu.org/licenses/gpl-3.0.html)

Zapisuje warstwy tymczasowe (memory) z wybranej grupy do plików GeoPackage (.gpkg)
i przełącza ich źródło na GPKG. Nazwy tabel w GPKG są wymuszane na małe litery
poprzez opcję GDAL/OGR (LAYER_NAME). Komunikaty dotyczące nieudanego
przełączenia źródła (setDataSource) są wyciszone, jeśli zapis na dysk się udał.
"""

import os
import re
import traceback

import processing
from qgis.PyQt.QtWidgets import QFileDialog, QInputDialog, QMessageBox, QProgressDialog
from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.core import QgsProject, QgsVectorLayer, QgsDataProvider, QgsMessageLog, Qgis

# --- FUNKCJA POMOCNICZA DLA NAZW TABEL ---


def _to_gpkg_name(name: str) -> str:
    """
    Konwertuje nazwę warstwy na bezpieczną nazwę tabeli w GPKG (lowercase).

    Przykłady:
        "Warstwa 1" -> "warstwa_1"
        "POLYGON_DATA" -> "polygon_data"
        "123ABC" -> "t_123abc"
        "Strefa@#$" -> "strefa"

    Args:
        name: Nazwa warstwy do konwersji

    Returns:
        str: Bezpieczna nazwa tabeli GPKG (lowercase, bez znaków specjalnych)
    """
    s = (name or "").strip().lower().replace(" ", "_")
    s = re.sub(r"[^a-z0-9_]+", "", s)
    if not s:
        s = "warstwa_temp"
    if s[0].isdigit():
        s = f"t_{s}"
    return s

# --- FUNKCJA POMOCNICZA DLA MESSAGE BOX ---


def _msg_box(parent, title: str, text: str, icon: QMessageBox.Icon, min_width: int = 500):
    """Wyświetla message box z formatowaniem HTML."""
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setIcon(icon)
    box.setTextFormat(Qt.RichText)
    box.setText(text)
    box.setStyleSheet(f"QLabel{{min-width: {min_width}px;}}")
    box.exec()
# ------------------------------------------


def run(iface, plugin_dir):
    mw = iface.mainWindow()
    bar = iface.messageBar()
    project = QgsProject.instance()
    root = project.layerTreeRoot()

    output_dir = ""
    saved, failed = [], []

    # 1) Grupy
    group_nodes = [ch for ch in root.children() if ch.nodeType() == 0]
    group_names = [g.name() for g in group_nodes]
    if not group_names:
        bar.pushCritical("Zapis warstw", "Projekt nie zawiera żadnych grup.")
        return {"saved": [], "failed": [], "output_dir": output_dir}

    # 2) Wybór grupy
    if len(group_names) == 1:
        selected_group_name = group_names[0]
    else:
        selected_group_name, ok = QInputDialog.getItem(
            mw, "Wybierz grupę",
            "Z której grupy chcesz zapisać warstwy tymczasowe?",
            group_names, 0, False
        )
        if not ok:
            bar.pushInfo("Zapis warstw", "Operacja anulowana.")
            return None

    group = root.findGroup(selected_group_name)
    if not group:
        bar.pushCritical("Zapis warstw", f"Nie znaleziono grupy '{selected_group_name}'.")
        return {"saved": [], "failed": [], "output_dir": output_dir}

    # 3) Warstwy memory
    layers = [
        ch.layer() for ch in group.children()
        if hasattr(ch, "layer")
        and isinstance(ch.layer(), QgsVectorLayer)
        and (
            ch.layer().providerType() == "memory"
            or (ch.layer().source() or "").lower().startswith("memory:")
            or "&uid=" in (ch.layer().source() or "")
        )
    ]
    if not layers:
        bar.pushInfo("Zapis warstw", f"Grupa '{selected_group_name}' nie zawiera warstw tymczasowych.")
        return {"saved": [], "failed": [], "output_dir": ""}

    # 4) Katalog zapisu
    output_dir = QFileDialog.getExistingDirectory(
        mw,
        "Wybierz katalog do zapisania warstw",
        "",
        QFileDialog.ShowDirsOnly
    )
    if not output_dir:
        bar.pushWarning("Zapis warstw", "Nie wybrano katalogu.")
        return None

    if not os.access(output_dir, os.W_OK):
        bar.pushCritical("Zapis warstw", f"Katalog '{output_dir}' nie jest zapisywalny.")
        return {"saved": [], "failed": [], "output_dir": output_dir}

    # 5) Progress dialog
    progress = QProgressDialog("Zapisywanie warstw tymczasowych…", "Anuluj", 0, len(layers), mw)
    progress.setWindowModality(Qt.WindowModal)
    progress.setWindowTitle("Zapis warstw")
    progress.show()

    # 6) Zapis + przełączenie źródła
    for idx, layer in enumerate(layers):
        if progress.wasCanceled():
            bar.pushWarning("Zapis warstw", "Operacja anulowana przez użytkownika.")
            progress.close()
            return None

        layer_name = layer.name()
        progress.setLabelText(f"Zapisywanie: {layer_name}…")

        file_name = layer_name.replace(" ", "_") + ".gpkg"
        output_path = os.path.join(output_dir, file_name)

        table_name = _to_gpkg_name(layer_name)

        style_mgr = layer.styleManager()
        current_style_name = style_mgr.currentStyle()
        style_xml = style_mgr.style(current_style_name)

        if layer.isEditable():
            layer.commitChanges()

        try:
            # Wymuszenie nazwy tabeli za pomocą opcji GDAL
            options_list = [f"LAYER_NAME={table_name}"]

            # Zapis do pliku
            result = processing.run("native:savefeatures", {
                'INPUT': layer,
                'OUTPUT': output_path,
                'OPTIONS': options_list
            })

            ok_out = bool(result and 'OUTPUT' in result and os.path.exists(output_path))

            if not ok_out:
                failed.append(layer_name)
                continue

            # URI do tabeli w GPKG
            ds_uri = f"{output_path}|layername={table_name}"

            # Przełączenie źródła
            opts = QgsDataProvider.ProviderOptions()
            opts.transformContext = project.transformContext()

            if layer.setDataSource(ds_uri, layer.name(), "ogr", opts):
                # SUKCES PRZEŁĄCZENIA
                if layer.styleManager().style(layer.styleManager().currentStyle()) != style_xml:
                    style_mgr.addStyle("_tmp_", style_xml)
                    style_mgr.setCurrentStyle("_tmp_")
                    style_mgr.renameStyle("_tmp_", current_style_name)

                layer.reload()
                layer.triggerRepaint()
                saved.append(layer_name)
            else:
                # Porażka w setDataSource, ale sukces w zapisie. Uznajemy za sukces.
                saved.append(layer_name)
                # WAŻNE: Wyciszamy ostrzeżenie.

        except Exception as e:
            error_msg = f"Błąd przy zapisie warstwy '{layer_name}': {str(e)}"
            QgsMessageLog.logMessage(
                f"{error_msg}\n{traceback.format_exc()}",
                "Narzędziownik APP",
                Qgis.Critical
            )
            failed.append(layer_name)

        # Aktualizacja progress
        progress.setValue(idx + 1)

    progress.close()

    # 7) Komunikat końcowy
    if saved or failed:
        total = len(saved) + len(failed)

        if failed:
            # Częściowy sukces lub pełna porażka
            msg_html = (
                f"<b>Wyniki zapisu warstw tymczasowych:</b><br><br>"
                f"Zapisane: <span style='color:green;'><b>{len(saved)}</b></span> / {total}<br>"
                f"Nieudane: <span style='color:red;'><b>{len(failed)}</b></span> / {total}<br><br>"
            )
            if len(failed) <= 5:
                msg_html += f"Nieudane warstwy:<br><i>{', '.join(failed)}</i><br><br>"
            msg_html += f"Katalog zapisu: <code>{output_dir}</code>"

            icon = QMessageBox.Warning if saved else QMessageBox.Critical

            # Message box z przyciskiem do otwarcia katalogu (jeśli coś zapisano)
            if saved:
                box = QMessageBox(mw)
                box.setWindowTitle("Zapis warstw")
                box.setIcon(icon)
                box.setTextFormat(Qt.RichText)
                box.setText(msg_html)
                box.setStyleSheet("QLabel{min-width: 500px;}")

                btn_open_folder = box.addButton("Przejdź do katalogu", QMessageBox.ActionRole)
                btn_ok = box.addButton(QMessageBox.Ok)
                box.setDefaultButton(btn_ok)

                box.exec()

                if box.clickedButton() == btn_open_folder:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(output_dir))
            else:
                # Pełna porażka - bez przycisku do katalogu
                _msg_box(mw, "Zapis warstw", msg_html, icon)
        else:
            # Pełny sukces
            msg_html = (
                f"<b>Sukces!</b><br><br>"
                f"Zapisano wszystkie warstwy: <b>{len(saved)}</b><br><br>"
                f"Katalog zapisu: <code>{output_dir}</code>"
            )
            # Message box z przyciskiem do otwarcia katalogu
            box = QMessageBox(mw)
            box.setWindowTitle("Zapis warstw")
            box.setIcon(QMessageBox.Information)
            box.setTextFormat(Qt.RichText)
            box.setText(msg_html)
            box.setStyleSheet("QLabel{min-width: 500px;}")

            btn_open_folder = box.addButton("Przejdź do katalogu", QMessageBox.ActionRole)
            btn_ok = box.addButton(QMessageBox.Ok)
            box.setDefaultButton(btn_ok)

            box.exec()

            if box.clickedButton() == btn_open_folder:
                QDesktopServices.openUrl(QUrl.fromLocalFile(output_dir))

    return {"saved": saved, "failed": failed, "output_dir": output_dir}
