# -*- coding: utf-8 -*-
"""
Narzędziownik APP - Wtyczka QGIS
Informacje o autorach, repozytorium: https://github.com/tomasz-gietkowski-geoanalityka/narzedziownik_app
Dokumentacja: https://akademia.geoanalityka.pl/courses/narzedziownik-app-dokumentacja/
Licencja: GNU GPL v3.0 (https://www.gnu.org/licenses/gpl-3.0.html)

Statystyki stref - oblicza i wizualizuje powierzchnie według symboli
"""

import os
import json
import traceback
from collections import defaultdict
from datetime import datetime

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
    QMessageBox, QFileDialog, QApplication
)
from qgis.PyQt.QtCore import QBuffer, QIODevice
from qgis.PyQt.QtGui import QPixmap
from qgis.core import (
    QgsVectorLayer, QgsWkbTypes,
    QgsMessageLog, Qgis
)

try:
    import matplotlib
    matplotlib.use('Qt5Agg')
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False


# ---------------------------------------------------------
# Funkcje pomocnicze
# ---------------------------------------------------------

# Ustalona kolejność symboli stref
ZONE_ORDER = ["SW", "SJ", "SZ", "SU", "SH", "SP", "SR", "SI", "SN", "SC", "SG", "SO", "SK"]


def _load_zone_colors(plugin_dir: str) -> dict:
    """Wczytuje kolory stref z pliku JSON"""
    json_path = os.path.join(plugin_dir, "resources", "config", "nazwa_strefy.json")
    color_map = {}

    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for item in data:
                    symbol = item.get("symbol")
                    color_hex = item.get("kolor_hex")
                    if symbol and color_hex:
                        color_map[symbol] = color_hex
        except Exception as e:
            QgsMessageLog.logMessage(
                f"Błąd wczytywania kolorów stref: {e}",
                "Narzędziownik APP",
                Qgis.Warning
            )

    return color_map


def _sort_by_zone_order(items):
    """Sortuje elementy według ustalonej kolejności symboli stref"""
    def get_order_index(item):
        symbol = item[0]
        try:
            return ZONE_ORDER.index(symbol)
        except ValueError:
            return len(ZONE_ORDER)  # Nieznane symbole na końcu

    return sorted(items, key=get_order_index)


# ---------------------------------------------------------
# Dialog z wykresem
# ---------------------------------------------------------
class ChartDialog(QDialog):
    def __init__(self, parent, stats_data, layer_name, color_map):
        super().__init__(parent)
        self.setWindowTitle("Statystyki stref - wykres powierzchni")
        self.setModal(False)
        self.stats_data = stats_data
        self.layer_name = layer_name
        self.color_map = color_map

        layout = QVBoxLayout(self)

        # Wykres - proporcje 700x450 pikseli (7x4.5 cali przy 100 DPI)
        self.figure = Figure(figsize=(7, 4.5))
        self.canvas = FigureCanvas(self.figure)
        layout.addWidget(self.canvas)

        # Przyciski
        btn_layout = QHBoxLayout()

        btn_save = QPushButton("Zapisz do PNG")
        btn_save.clicked.connect(self._save_to_png)
        btn_layout.addWidget(btn_save)

        btn_copy = QPushButton("Kopiuj do schowka")
        btn_copy.clicked.connect(self._copy_to_clipboard)
        btn_layout.addWidget(btn_copy)

        btn_layout.addStretch()

        btn_close = QPushButton("Zamknij")
        btn_close.clicked.connect(self.accept)
        btn_layout.addWidget(btn_close)

        layout.addLayout(btn_layout)

        self.resize(750, 550)

        # Narysuj wykres
        self._draw_chart()

    def _draw_chart(self):
        """Rysuje wykres słupkowy powierzchni"""
        # Sortuj dane według ustalonej kolejności symboli stref
        sorted_data = _sort_by_zone_order(list(self.stats_data.items()))

        symbols = [item[0] for item in sorted_data]
        areas_ha = [item[1]['area'] / 10000.0 for item in sorted_data]  # Konwersja m² na ha
        percentages = [item[1]['percentage'] for item in sorted_data]

        # Sprawdź czy strefa SO ma więcej niż 50% powierzchni
        so_percentage = next((pct for sym, pct in zip(symbols, percentages) if sym == "SO"), 0)
        use_broken_axis = so_percentage > 50 and len(symbols) > 1

        # Pobierz kolory dla symboli
        colors = []
        for sym in symbols:
            color = self.color_map.get(sym)
            if color:
                colors.append(color)
            else:
                # Domyślny kolor jeśli nie ma w słowniku
                colors.append('#CCCCCC')

        if use_broken_axis:
            # Użyj przerwania osi dla lepszej wizualizacji małych wartości
            self._draw_broken_axis_chart(symbols, areas_ha, percentages, colors)
        else:
            # Standardowy wykres
            self._draw_standard_chart(symbols, areas_ha, percentages, colors)

    def _draw_standard_chart(self, symbols, areas_ha, percentages, colors):
        """Rysuje standardowy wykres słupkowy"""
        ax = self.figure.add_subplot(111)

        # Wykres słupkowy
        bars = ax.bar(symbols, areas_ha, color=colors, edgecolor='black', linewidth=0.7)

        # Dodaj wartości procentowe nad słupkami
        for bar, pct in zip(bars, percentages):
            height = bar.get_height()
            # Dla małych wartości wyświetl "<0.1%" zamiast "0.0%"
            if pct > 0 and round(pct, 1) == 0.0:
                pct_text = '<0.1%'
            else:
                pct_text = f'{pct:.1f}%'
            ax.text(bar.get_x() + bar.get_width()/2., height,
                    pct_text,
                    ha='center', va='bottom', fontsize=9, fontweight='bold')

        # Formatowanie osi Y - liczby całkowite
        ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(
            lambda x, p: f'{x:,.0f}'.replace(',', ' ')
        ))

        # Etykieta osi Y
        ax.set_ylabel('Powierzchnia (ha)', fontsize=9, fontweight='bold')

        # Siatka
        ax.grid(axis='y', alpha=0.3, linestyle='--')
        ax.set_axisbelow(True)

        # Legenda na dole (bez ramki)
        legend_labels = []
        for sym, area, pct in zip(symbols, areas_ha, percentages):
            # Dla małych wartości wyświetl "<0.1%" zamiast "0.0%"
            if pct > 0 and round(pct, 1) == 0.0:
                pct_text = '<0.1%'
            else:
                pct_text = f'{pct:.1f}%'
            legend_labels.append(f'{sym}: {area:.2f} ha ({pct_text})')

        ax.legend(bars, legend_labels, loc='upper center', bbox_to_anchor=(0.5, -0.08),
                  ncol=min(3, len(symbols)), frameon=False, fontsize=9)

        # Dopasuj układ z uwzględnieniem legendy
        self.figure.tight_layout()
        # Dodaj dodatkowe miejsce na dole dla legendy (max 35% wysokości)
        bottom_margin = min(0.35, 0.18 + (len(symbols) / 25.0))
        self.figure.subplots_adjust(bottom=bottom_margin)

        self.canvas.draw()

    def _draw_broken_axis_chart(self, symbols, areas_ha, percentages, colors):
        """Rysuje wykres z przerwaniem osi dla lepszej wizualizacji małych wartości"""
        import matplotlib.gridspec as gridspec

        # Znajdź maksymalną wartość (SO) i pozostałe
        max_area = max(areas_ha)
        other_max = max([a for a in areas_ha if a < max_area * 0.8], default=0)

        # Ustal granice dla dwóch części wykresu
        # Dolna część: od 0 do other_max * 1.2
        # Górna część: od max_area * 0.7 do max_area * 1.1
        ylim_bottom = (0, other_max * 1.2)
        ylim_top = (max_area * 0.85, max_area * 1.05)

        # Stwórz dwie osie z przerwaniem
        gs = gridspec.GridSpec(2, 1, height_ratios=[1, 2], hspace=0.05)
        ax_top = self.figure.add_subplot(gs[0])
        ax_bottom = self.figure.add_subplot(gs[1])

        # Rysuj słupki na obu osiach
        for ax in [ax_top, ax_bottom]:
            bars = ax.bar(symbols, areas_ha, color=colors, edgecolor='black', linewidth=0.7)

            # Siatka
            ax.grid(axis='y', alpha=0.3, linestyle='--')
            ax.set_axisbelow(True)

        # Ustaw limity osi Y
        ax_top.set_ylim(ylim_top)
        ax_bottom.set_ylim(ylim_bottom)

        # Usuń etykiety X z górnego wykresu
        ax_top.set_xticklabels([])
        ax_top.tick_params(axis='x', length=0)

        # Dodaj wartości procentowe
        for i, (sym, area, pct) in enumerate(zip(symbols, areas_ha, percentages)):
            # Wybierz odpowiednią oś
            if area > ylim_bottom[1]:
                ax = ax_top
            else:
                ax = ax_bottom

            # Dla małych wartości wyświetl "<0.1%" zamiast "0.0%"
            if pct > 0 and round(pct, 1) == 0.0:
                pct_text = '<0.1%'
            else:
                pct_text = f'{pct:.1f}%'

            ax.text(i, area, pct_text,
                    ha='center', va='bottom', fontsize=9, fontweight='bold')

        # Formatowanie osi Y - liczby całkowite
        for ax in [ax_top, ax_bottom]:
            ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(
                lambda x, p: f'{x:,.0f}'.replace(',', ' ')
            ))

        # Dodaj oznaczenie przerwania osi
        d = 0.015  # Rozmiar przekreślenia
        kwargs = dict(transform=ax_top.transAxes, color='k', clip_on=False, linewidth=1)
        ax_top.plot((-d, +d), (-d, +d), **kwargs)
        ax_top.plot((1 - d, 1 + d), (-d, +d), **kwargs)

        kwargs.update(transform=ax_bottom.transAxes)
        ax_bottom.plot((-d, +d), (1 - d, 1 + d), **kwargs)
        ax_bottom.plot((1 - d, 1 + d), (1 - d, 1 + d), **kwargs)

        # Legenda na dole (bez ramki)
        legend_labels = []
        for sym, area, pct in zip(symbols, areas_ha, percentages):
            # Dla małych wartości wyświetl "<0.1%" zamiast "0.0%"
            if pct > 0 and round(pct, 1) == 0.0:
                pct_text = '<0.1%'
            else:
                pct_text = f'{pct:.1f}%'
            legend_labels.append(f'{sym}: {area:.2f} ha ({pct_text})')

        # Pobierz bars z dolnego wykresu dla legendy
        bars = ax_bottom.patches[:len(symbols)]
        ax_bottom.legend(bars, legend_labels, loc='upper center',
                         bbox_to_anchor=(0.5, -0.12),
                         ncol=min(3, len(symbols)), frameon=False, fontsize=9)

        # Dopasuj układ z uwzględnieniem legendy
        self.figure.tight_layout()
        # Dodaj dodatkowe miejsce na dole dla legendy w broken axis (max 38% wysokości)
        bottom_margin = min(0.38, 0.20 + (len(symbols) / 25.0))
        self.figure.subplots_adjust(bottom=bottom_margin)

        # Wspólna etykieta osi Y (pomiędzy obiema osiami) - dodaj PO subplots_adjust
        # Oblicz środek między górną i dolną osią
        fig_coords_bottom = ax_bottom.get_position()
        fig_coords_top = ax_top.get_position()
        y_middle = (fig_coords_bottom.y0 + fig_coords_top.y1) / 2

        self.figure.text(0.04, y_middle, 'Powierzchnia (ha)',
                         ha='center', va='center', rotation='vertical',
                         fontsize=9, fontweight='bold')

        self.canvas.draw()

    def _save_to_png(self):
        """Zapisuje wykres do pliku PNG"""
        default_name = f"statystyki_stref_{self.layer_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Zapisz wykres jako PNG",
            default_name,
            "PNG Image (*.png)"
        )

        if file_path:
            try:
                # Zapisz z bbox_inches='tight' aby uwzględnić całą legendę
                self.figure.savefig(file_path, dpi=300, bbox_inches='tight', pad_inches=0.3)
                QMessageBox.information(
                    self,
                    "Sukces",
                    f"Wykres zapisano do pliku:\n{file_path}"
                )
                QgsMessageLog.logMessage(
                    f"Wykres zapisany: {file_path}",
                    "Narzędziownik APP",
                    Qgis.Info
                )
            except Exception as e:
                QMessageBox.critical(
                    self,
                    "Błąd",
                    f"Nie udało się zapisać wykresu:\n{str(e)}"
                )

    def _copy_to_clipboard(self):
        """Kopiuje wykres do schowka jako obrazek"""
        try:
            # Renderuj wykres do bufora z pełną legendą
            buffer = QBuffer()
            buffer.open(QIODevice.WriteOnly)

            # Zapisz do bufora jako PNG z bbox_inches='tight'
            self.canvas.print_figure(buffer, format='png', dpi=150,
                                     bbox_inches='tight', pad_inches=0.3)
            buffer.close()

            # Stwórz QPixmap z bufora
            pixmap = QPixmap()
            pixmap.loadFromData(buffer.data(), 'PNG')

            # Skopiuj do schowka
            clipboard = QApplication.clipboard()
            clipboard.setPixmap(pixmap)

            QMessageBox.information(
                self,
                "Sukces",
                "Wykres skopiowano do schowka.\nMożesz teraz wkleić go do innego programu (Ctrl+V)."
            )
        except Exception as e:
            QMessageBox.critical(
                self,
                "Błąd",
                f"Nie udało się skopiować wykresu:\n{str(e)}"
            )


# ---------------------------------------------------------
# Główna funkcja
# ---------------------------------------------------------
def run(iface, plugin_dir: str):
    """Uruchamia narzędzie statystyki stref dla aktywnej warstwy"""

    # Sprawdź czy matplotlib jest dostępny
    if not MATPLOTLIB_AVAILABLE:
        QMessageBox.critical(
            iface.mainWindow(),
            "Błąd",
            "Brak biblioteki matplotlib.\n"
            "To narzędzie wymaga zainstalowanej biblioteki matplotlib."
        )
        return

    try:
        # Krok 1: Pobierz aktywną warstwę
        layer = iface.activeLayer()

        if not layer:
            QMessageBox.warning(
                iface.mainWindow(),
                "Błąd",
                "Brak aktywnej warstwy.\n"
                "Wybierz warstwę w panelu warstw przed uruchomieniem narzędzia."
            )
            return

        if not isinstance(layer, QgsVectorLayer):
            QMessageBox.warning(
                iface.mainWindow(),
                "Błąd",
                "Aktywna warstwa nie jest warstwą wektorową.\n"
                "Wybierz warstwę wektorową w panelu warstw."
            )
            return

        # Sprawdź czy warstwa ma pole 'symbol'
        symbol_idx = layer.fields().indexFromName("symbol")
        if symbol_idx == -1:
            QMessageBox.critical(
                iface.mainWindow(),
                "Błąd",
                f"Warstwa '{layer.name()}' nie posiada pola 'symbol'.\n"
                "To narzędzie wymaga pola 'symbol' w warstwie."
            )
            return

        # Sprawdź czy warstwa jest poligonowa
        if layer.geometryType() != QgsWkbTypes.PolygonGeometry:
            QMessageBox.warning(
                iface.mainWindow(),
                "Ostrzeżenie",
                "Warstwa nie jest warstwą poligonową.\n"
                "Powierzchnie mogą nie być obliczone poprawnie."
            )

        # Krok 2: Oblicz powierzchnie według symboli
        stats = defaultdict(lambda: {'area': 0.0, 'count': 0})

        for feature in layer.getFeatures():
            symbol = feature[symbol_idx]
            if symbol is None or str(symbol).strip() == "":
                symbol = "(brak symbolu)"
            else:
                symbol = str(symbol).strip()

            geom = feature.geometry()
            if geom and not geom.isNull():
                # Oblicz powierzchnię
                area = geom.area()
                stats[symbol]['area'] += area
                stats[symbol]['count'] += 1

        if not stats:
            QMessageBox.information(
                iface.mainWindow(),
                "Informacja",
                "Brak danych do analizy w wybranej warstwie."
            )
            return

        # Krok 3: Oblicz procenty
        total_area = sum(data['area'] for data in stats.values())

        for symbol in stats:
            stats[symbol]['percentage'] = (stats[symbol]['area'] / total_area * 100) if total_area > 0 else 0

        # Krok 4: Wczytaj kolory stref
        color_map = _load_zone_colors(plugin_dir)

        # Krok 5: Wyświetl wykres
        chart_dlg = ChartDialog(iface.mainWindow(), dict(stats), layer.name(), color_map)
        chart_dlg.exec_()

    except Exception as e:
        QgsMessageLog.logMessage(
            traceback.format_exc(),
            "Narzędziownik APP",
            Qgis.Critical
        )
        QMessageBox.critical(
            iface.mainWindow(),
            "Błąd",
            f"Wystąpił błąd krytyczny:\n{str(e)}\n\nSzczegóły w logach QGIS."
        )
