# -*- coding: utf-8 -*-

import json
import os
import traceback
from qgis.core import QgsProject, QgsRasterLayer, QgsCoordinateReferenceSystem

GROUP_NAME = "Podkłady"


# ----------------------------------------------------------
# ŁADOWANIE JSON-A
# ----------------------------------------------------------

def load_layer_defs(plugin_dir: str):
    json_path = os.path.join(
        plugin_dir,
        "resources",
        "config",
        "definicje_podkladow.json"
    )

    if not os.path.exists(json_path):
        raise FileNotFoundError(f"Brak pliku: {json_path}")

    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ----------------------------------------------------------
# NARZĘDZIA DRZEWA WARSTW
# ----------------------------------------------------------

def _get_or_create_group(project, name):
    root = project.layerTreeRoot()
    group = root.findGroup(name)
    if group is None:
        group = root.addGroup(name)
    return group


def _add_layer_to_group(project, group_name, layer):
    if not layer.isValid():
        raise RuntimeError(f"Warstwa '{layer.name()}' jest nieprawidłowa")

    project.addMapLayer(layer, False)
    group = _get_or_create_group(project, group_name)
    group.addLayer(layer)


def _collapse_layer(layer):
    root = QgsProject.instance().layerTreeRoot()
    node = root.findLayer(layer.id())
    if node:
        node.setExpanded(False)


def _set_layer_visibility(layer, visible):
    root = QgsProject.instance().layerTreeRoot()
    node = root.findLayer(layer.id())
    if node:
        node.setItemVisibilityChecked(visible)


# ----------------------------------------------------------
# BUDOWANIE WARSTWY Z KONFIGURACJI
# ----------------------------------------------------------

def _build_layer_from_cfg(cfg, project_epsg):

    kind = cfg["kind"]
    display_name = cfg["display_name"]

    # XYZ
    if kind == "xyz":
        uri = cfg["uri"]
        return QgsRasterLayer(uri, display_name, "wms")

    # WMTS
    elif kind == "wmts":
        base_url = cfg["base_url"]
        img_format = cfg.get("format", "image/png")
        layers = cfg["layers"]
        styles = cfg.get("styles", "default")

        # Jeżeli projekt jest w EPSG:4326 -> używamy 4326, w pozostałych przypadkach 2180
        crs = "EPSG:4326" if project_epsg == 4326 else "EPSG:2180"

        wmts_source = (
            "contextualWMSLegend=0&"
            f"crs={crs}&dpiMode=0&featureCount=10&"
            f"format={img_format}&"
            f"layers={layers}&"
            f"styles={styles}&"
            f"tileMatrixSet={crs}&"
            f"url={base_url}?service%3DWMTS%26request%3DgetCapabilities"
        )

        return QgsRasterLayer(wmts_source, display_name, "wms")

    else:
        raise ValueError(f"Nieznany typ warstwy: {kind}")


# ----------------------------------------------------------
# GŁÓWNA FUNKCJA
# ----------------------------------------------------------

def run(iface, plugin_dir: str):

    try:
        project = QgsProject.instance()

        # Czy projekt był pusty PRZED dodaniem podkładów?
        project_was_empty = not project.mapLayers()

        if project_was_empty:
            # Projekt pusty -> naszym docelowym układem ma być 2180.
            # Ustawiamy od razu CRS projektu i przyjmujemy EPSG=2180 do dalszych obliczeń.
            try:
                crs_2180 = QgsCoordinateReferenceSystem("EPSG:2180")
                if not crs_2180.isValid():
                    raise RuntimeError("Nie udało się utworzyć CRS EPSG:2180")
                project.setCrs(crs_2180)
                project_epsg = 2180
            except Exception:
                traceback.print_exc()
                # awaryjnie spróbujmy jednak odczytać z projektu
                crs = project.crs()
                try:
                    project_epsg = int(crs.authid().split(":")[1])
                except Exception:
                    project_epsg = crs.postgisSrid() or 0
        else:
            # Projekt nie jest pusty -> nie nadpisujemy użytkownikowi CRS,
            # tylko dopasowujemy się do istniejącego układu.
            crs = project.crs()
            try:
                project_epsg = int(crs.authid().split(":")[1])
            except Exception:
                project_epsg = crs.postgisSrid() or 0

        layer_defs = load_layer_defs(plugin_dir)

        results = []

        for cfg in layer_defs:
            try:
                layer = _build_layer_from_cfg(cfg, project_epsg)

                _add_layer_to_group(project, GROUP_NAME, layer)
                _collapse_layer(layer)
                _set_layer_visibility(layer, cfg.get("visible", False))

                results.append((True, cfg["display_name"], None))

            except Exception as e:
                traceback.print_exc()
                results.append((False, cfg["display_name"], str(e)))

        ok = [name for success, name, err in results if success]
        err = [(name, e) for success, name, e in results if not success]

        if ok:
            iface.messageBar().pushSuccess(
                "Narzędziownik APP",
                "Dodano warstwy: " + ", ".join(ok)
            )

        if err:
            iface.messageBar().pushWarning(
                "Narzędziownik APP",
                "Nie udało się dodać:\n" +
                "\n".join([f"- {n}: {e}" for n, e in err])
            )

        # NAJWAŻNIEJSZY DODATEK:
        # Jeśli projekt był pusty na starcie, to po dodaniu wszystkich warstw
        # nadpisujemy ewentualną zmianę CRS przez QGIS (np. na 3857 od OSM)
        # i "domykamy" projekt w EPSG:2180.
        if project_was_empty:
            try:
                crs_2180 = QgsCoordinateReferenceSystem("EPSG:2180")
                if crs_2180.isValid():
                    project.setCrs(crs_2180)
            except Exception:
                traceback.print_exc()

    except Exception as e:
        traceback.print_exc()
        iface.messageBar().pushWarning(
            "Narzędziownik APP",
            f"Błąd krytyczny: {e}"
        )
