"""
Narzędziownik APP - Wtyczka QGIS
Informacje o autorach, repozytorium: https://github.com/tomasz-gietkowski-geoanalityka/narzedziownik_app
Dokumentacja: https://akademia.geoanalityka.pl/courses/narzedziownik-app-dokumentacja/
Licencja: GNU GPL v3.0 (https://www.gnu.org/licenses/gpl-3.0.html)

Generuje raport HTML dla zaznaczonej/aktywnej warstwy z geopaczki (GPKG),
na podstawie pól zgodnych z arkuszem XLS i układu tabeli zgodnego ze wzorem DOCX.
"""
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
import html
import sys
import subprocess
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from qgis.PyQt.QtWidgets import (
    QMessageBox, QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout
)
from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.core import (
    QgsVectorLayer,
    QgsProject,
    QgsMessageLog,
    Qgis,
)

# -------------------------------------------------------------------
# Konfiguracja pól
# -------------------------------------------------------------------
FIELD_SYMBOL = "symbol"
FIELD_NAME = "nazwa"
FIELD_PROFILE_BASIC = "profilPodstawowy"
FIELD_OZN = "oznaczenie"
FIELD_PROFILE_EXTRA = "profilDodatkowy"
FIELD_INTENS = "maksNadziemnaIntensywnoscZabudowy"
FIELD_HEIGHT = "maksWysokoscZabudowy"
FIELD_COV = "maksUdzialPowierzchniZabudowy"
FIELD_BIO = "minUdzialPowierzchniBiologicznieCzynnej"


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------
def _log(msg: str):
    QgsMessageLog.logMessage(msg, "Narzędziownik APP / Raport stref", Qgis.Info)


def _format_header(symbol: Any, name: Any) -> str:
    s = "" if symbol is None else str(symbol).strip().upper()
    n = "" if name is None else str(name).strip().upper()
    if not s:
        return html.escape(n)
    if not n:
        return html.escape(s)
    return f"{html.escape(s)} - {html.escape(n)}"


def _pick_layer(iface) -> Optional[QgsVectorLayer]:
    try:
        lyr = iface.activeLayer()
        if isinstance(lyr, QgsVectorLayer) and ".gpkg" in (lyr.source() or "").lower():
            return lyr
    except Exception:
        pass

    try:
        for lyr in iface.layerTreeView().selectedLayers() or []:
            if isinstance(lyr, QgsVectorLayer) and ".gpkg" in (lyr.source() or "").lower():
                return lyr
    except Exception:
        pass

    for lyr in QgsProject.instance().mapLayers().values():
        if isinstance(lyr, QgsVectorLayer) and ".gpkg" in (lyr.source() or "").lower():
            return lyr

    return None


def _gpkg_path_from_layer(layer: QgsVectorLayer) -> Optional[str]:
    src = layer.source() or ""
    path = src.split("|")[0]
    if path.lower().endswith(".gpkg") and os.path.exists(path):
        return path
    return None


def _has_fields(layer: QgsVectorLayer, required: List[str]) -> Tuple[bool, List[str]]:
    names = {f.name() for f in layer.fields()}
    missing = [f for f in required if f not in names]
    return len(missing) == 0, missing


def _nat_key(v: Any):
    s = "" if v is None else str(v)
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def _normalize_commas(v: Any) -> str:
    if v is None:
        return ""
    return re.sub(r"\s*,\s*", ", ", str(v)).strip()


def _fmt_num(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str) and v.strip().lower() in ("", "null"):
        return ""
    try:
        return f"{float(v):.4f}".rstrip("0").rstrip(".")
    except Exception:
        return ""


def _esc(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    if s == "" or s.lower() == "null":
        return ""
    return html.escape(s)


def _open_folder_select_file(path: str) -> bool:
    """
    Otwiera folder z podświetlonym plikiem.
    - Windows: explorer.exe /select,"path"
    - Inne systemy: otwiera folder (bez selekcji)
    """
    try:
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer.exe", "/select,", os.path.normpath(path)])
            return True

        folder = os.path.dirname(path)
        return QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
    except Exception as e:
        _log(f"Nie udało się otworzyć folderu z selekcją: {e}")
        return False


def _show_done_dialog(parent, out_path: str, layer_name: str):
    """
    Końcowy popup jako QDialog z RichText i linkiem,
    który otwiera folder z zaznaczonym plikiem DOPIERO po kliknięciu.
    """
    dlg = QDialog(parent)
    dlg.setWindowTitle("Raport stref")
    dlg.setModal(True)

    layout = QVBoxLayout(dlg)

    # Nagłówek: nazwa warstwy na górze (fiolet)
    lbl_layer = QLabel(
        f"Nazwa warstwy: <span style='color:#8a2be2; font-weight:600;'>{html.escape(layer_name)}</span>"
    )
    lbl_layer.setTextFormat(Qt.RichText)
    lbl_layer.setWordWrap(True)
    layout.addWidget(lbl_layer)

    # Informacja o pliku + link
    tip = (
        "Wygenerowany plik HTML otwórz w edytorze Word lub LibreOffice (Plik → Otwórz). "
        "Unikaj Kopiuj+Wklej, które uszkadza formatowanie."
    )

    lbl = QLabel()
    lbl.setTextFormat(Qt.RichText)
    lbl.setTextInteractionFlags(Qt.TextBrowserInteraction)
    lbl.setOpenExternalLinks(False)  # obsługujemy sami
    lbl.setWordWrap(True)

    # link specjalny, przechwytujemy w linkActivated
    link_html = "<a href='action:open_select'>Otwórz folder z raportem (z zaznaczonym plikiem)</a>"

    lbl.setText(
        f"<br><b>Raport wygenerowany poprawnie</b><br><br>"
        f"Plik:<br><code>{html.escape(out_path)}</code><br><br>"
        f"{link_html}<br><br>"
        f"<i>{html.escape(tip)}</i>"
    )
    layout.addWidget(lbl)

    def _on_link(href: str):
        if href == "action:open_select":
            _open_folder_select_file(out_path)

    lbl.linkActivated.connect(_on_link)

    # Przyciski
    btn_row = QHBoxLayout()
    btn_row.addStretch(1)
    btn_ok = QPushButton("OK")
    btn_ok.clicked.connect(dlg.accept)
    btn_row.addWidget(btn_ok)
    layout.addLayout(btn_row)

    dlg.resize(640, 260)
    dlg.exec()


# -------------------------------------------------------------------
# HTML
# -------------------------------------------------------------------
def _build_html(groups: List[Dict[str, Any]], title: str, source_info: str) -> str:
    css = """
    body { font-family: Calibri, Arial, sans-serif; font-size: 11pt; margin: 24px; }
    h1 { font-size: 16pt; margin-bottom: 12px; }
    .meta { font-size: 9.5pt; color: #333; margin-bottom: 18px; }

    .section { margin-bottom: 28px; }
    .section-title { font-weight: 700; text-transform: uppercase; margin-bottom: 6px; }
    .profile-basic { margin-bottom: 10px; }

    table { border-collapse: collapse; width: 100%; table-layout: fixed; }
    th, td {
        border: 1px solid #000;
        padding: 6px 8px;
        text-align: center;
        vertical-align: top;
        overflow-wrap: anywhere;
    }
    th { font-weight: 700; vertical-align: middle; }

    th.col-prof, td.col-prof { text-align: left; }

    /* szerokości: ozn 8%, num 4x13%, profil reszta (40%) */
    .col-ozn { width: 8%; }
    .col-int { width: 13%; }
    .col-h   { width: 13%; }
    .col-pz  { width: 13%; }
    .col-bio { width: 13%; }
    .col-prof { width: 40%; }
    """

    out = [
        "<!doctype html>",
        "<html lang='pl'><head>",
        "<meta charset='utf-8'>",
        f"<title>{html.escape(title)}</title>",
        f"<style>{css}</style>",
        "</head><body>",
        f"<h1>{html.escape(title)}</h1>",
        f"<div class='meta'>Wygenerowano: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')};<br>"
        f"Plik: {html.escape(source_info)}</div>"
    ]

    for g in groups:
        out.append("<div class='section'>")
        out.append(f"<div class='section-title'>{_format_header(g['symbol'], g['name'])}</div>")
        out.append(
            f"<div class='profile-basic'><b>Profil podstawowy</b> - "
            f"{_esc(_normalize_commas(g['profil_podstawowy']))}</div>"
        )

        out.append("<table><thead><tr>")
        out.append("<th class='col-ozn'>Oznaczenie terenów</th>")
        out.append("<th class='col-prof'>Profil dodatkowy</th>")
        out.append("<th class='col-int'>Maksymalna nadziemna intensywność zabudowy</th>")
        out.append("<th class='col-h'>Maksymalna wysokość zabudowy<br>(m)</th>")
        out.append("<th class='col-pz'>Maksymalny udział powierzchni zabudowy<br>(%)</th>")
        out.append("<th class='col-bio'>Minimalny udział powierzchni biologicznie czynnej wyznaczony w PO<br>(%)</th>")
        out.append("</tr></thead><tbody>")

        for r in g["rows"]:
            out.append("<tr>")
            out.append(f"<td class='col-ozn'>{_esc(r['oznaczenie'])}</td>")
            out.append(f"<td class='col-prof'>{_esc(_normalize_commas(r['profil_dodatkowy']))}</td>")
            out.append(f"<td class='col-int'>{_fmt_num(r['intens'])}</td>")
            out.append(f"<td class='col-h'>{_fmt_num(r['height'])}</td>")
            out.append(f"<td class='col-pz'>{_fmt_num(r['cov'])}</td>")
            out.append(f"<td class='col-bio'>{_fmt_num(r['bio'])}</td>")
            out.append("</tr>")

        out.append("</tbody></table></div>")

    out.append("</body></html>")
    return "\n".join(out)


# -------------------------------------------------------------------
# Main
# -------------------------------------------------------------------
def run(iface, plugin_dir: str):
    layer = _pick_layer(iface)
    if not layer:
        QMessageBox.warning(iface.mainWindow(), "Raport stref", "Nie znaleziono warstwy GPKG.")
        return

    gpkg = _gpkg_path_from_layer(layer)
    if not gpkg:
        QMessageBox.warning(iface.mainWindow(), "Raport stref", "Nie udało się ustalić ścieżki do GPKG.")
        return

    required = [
        FIELD_SYMBOL, FIELD_NAME, FIELD_PROFILE_BASIC, FIELD_OZN,
        FIELD_PROFILE_EXTRA, FIELD_INTENS, FIELD_HEIGHT, FIELD_COV, FIELD_BIO
    ]
    ok, missing = _has_fields(layer, required)
    if not ok:
        QMessageBox.critical(
            iface.mainWindow(), "Raport stref – brak pól",
            "Brak wymaganych pól:\n- " + "\n- ".join(missing)
        )
        return

    groups: Dict[str, Dict[str, Any]] = {}

    for f in layer.getFeatures():
        sym = f[FIELD_SYMBOL]
        if sym is None:
            continue
        s = str(sym).strip()
        if s == "" or s.lower() == "null":
            continue

        if s not in groups:
            groups[s] = {
                "symbol": s,
                "name": f[FIELD_NAME],
                "profil_podstawowy": f[FIELD_PROFILE_BASIC],
                "rows": []
            }

        groups[s]["rows"].append({
            "oznaczenie": f[FIELD_OZN],
            "profil_dodatkowy": f[FIELD_PROFILE_EXTRA],
            "intens": f[FIELD_INTENS],
            "height": f[FIELD_HEIGHT],
            "cov": f[FIELD_COV],
            "bio": f[FIELD_BIO],
        })

    if not groups:
        QMessageBox.information(iface.mainWindow(), "Raport stref", "Brak danych do raportu.")
        return

    groups_list = [groups[k] for k in sorted(groups.keys(), key=_nat_key)]
    for g in groups_list:
        g["rows"].sort(key=lambda r: _nat_key(r.get("oznaczenie")))

    base = os.path.splitext(os.path.basename(gpkg))[0]
    out_path = os.path.join(
        os.path.dirname(gpkg),
        f"raport_stref_{base}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    )

    source_info = f"{gpkg} {{layername={layer.name()}}}"

    try:
        with open(out_path, "w", encoding="utf-8") as fp:
            fp.write(_build_html(groups_list, f"Raport stref planistycznych – {base}", source_info))
    except Exception as e:
        _log(f"Błąd zapisu HTML: {e}")
        QMessageBox.critical(
            iface.mainWindow(),
            "Raport stref",
            f"Nie udało się zapisać raportu:\n{out_path}\n\nBłąd: {e}"
        )
        return

    # końcowy popup: nazwa warstwy na górze + link (klik = otwórz folder i zaznacz plik)
    _show_done_dialog(iface.mainWindow(), out_path, layer.name())
    _log(f"Zapisano raport: {out_path}")
