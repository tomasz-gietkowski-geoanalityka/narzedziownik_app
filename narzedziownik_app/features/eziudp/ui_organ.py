"""
Narzędziownik APP - Wtyczka QGIS
Informacje o autorach, repozytorium: https://github.com/tomasz-gietkowski-geoanalityka/narzedziownik_app
Dokumentacja: https://akademia.geoanalityka.pl/courses/narzedziownik-app-dokumentacja/
Licencja: GNU GPL v3.0 (https://www.gnu.org/licenses/gpl-3.0.html)
"""

# -*- coding: utf-8 -*-

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QListWidget, QListWidgetItem, QDialogButtonBox,
    QSizePolicy, QMessageBox, QProgressBar
)
from PyQt5.QtCore import QTimer

from .workers import OrgSearchWorker


class EziudpOrganDialog(QDialog):
    """
    Krok 1: wybór organu w EZiUDP.

    - uruchamia OrgSearchWorker w osobnym wątku,
    - po 10 s ZAWSZE pyta użytkownika, czy czekać dalej (jeśli worker nadal działa),
    - Cancel / X przerywa wyszukiwanie bez wywalania QGIS.
    """

    def __init__(self, parent=None, initial_query: str = None, initial_organs: list = None):
        super().__init__(parent)
        self.setWindowTitle("EZiUDP – wybór organu")
        self.setMinimumWidth(640)

        # aktywny worker (lub None)
        self.worker = None

        # ile razy już minęło 10 s
        self.wait_intervals = 0

        # timer pojedynczego strzału – po 10 s od startu szukania
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self._on_search_timeout)

        # --- UI ---

        v = QVBoxLayout(self)

        row = QHBoxLayout()
        row.addWidget(QLabel("Wpisz nazwę organu, jednostki lub ich część:"))
        self.ed_query = QLineEdit()
        self.ed_query.setPlaceholderText("np. 'nak', 'Bydgoszcz'…")
        row.addWidget(self.ed_query, 1)
        self.btn_search = QPushButton("Szukaj")
        row.addWidget(self.btn_search)
        v.addLayout(row)

        self.lbl_status = QLabel("")
        self.prg = QProgressBar()
        self.prg.setRange(0, 100)
        self.prg.setValue(0)
        v.addWidget(self.lbl_status)
        v.addWidget(self.prg)

        self.lst = QListWidget()
        self.lst.setSelectionMode(QListWidget.SingleSelection)
        self.lst.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        v.addWidget(self.lst, 1)

        # tylko Cancel – wybór organu przez double-click lub selected_organ()
        self.button_box = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.button_box.rejected.connect(self.reject)
        v.addWidget(self.button_box)

        # sygnały UI
        self.btn_search.clicked.connect(self._on_search)
        self.ed_query.returnPressed.connect(self._on_search)
        self.lst.itemDoubleClicked.connect(self._on_item_double_clicked)

        self.ed_query.setText(initial_query or "")
        if initial_organs:
            for name in initial_organs:
                self.lst.addItem(QListWidgetItem(name))
            self.lbl_status.setText(f"Znaleziono: {len(initial_organs)}")
            self.prg.setRange(0, 100)
            self.prg.setValue(100)

        self.ed_query.setFocus()

    # --- gettery używane przez kod wywołujący dialog ---

    def current_query(self) -> str:
        return self.ed_query.text().strip()

    def current_organs(self) -> list:
        return [self.lst.item(i).text() for i in range(self.lst.count())]

    def selected_organ(self) -> str:
        it = self.lst.selectedItems()
        return it[0].text() if it else ""

    # --- logika UI ---

    def _on_item_double_clicked(self, item: QListWidgetItem):
        if item is not None:
            self.lst.setCurrentItem(item)
            self.accept()

    def _on_search(self):
        q = self.ed_query.text().strip()
        if not q:
            QMessageBox.information(self, "Informacja", "Wpisz frazę wyszukującą organ.")
            return

        # zatrzymaj ewentualny poprzedni worker
        self._stop_worker()

        self.lst.clear()
        self.lbl_status.setText("Szukam w EZiUDP…")
        # tryb „busy”
        self.prg.setRange(0, 0)
        self.prg.setValue(0)

        # na czas szukania blokujemy tylko „Szukaj”
        self.btn_search.setEnabled(False)

        # licznik czekania
        self.wait_intervals = 0
        self.search_timer.start(10_000)  # pierwsze 10 s

        # worker bez parenta (ważne, żeby dialog nie ubił QThread)
        self.worker = OrgSearchWorker(q)

        self.worker.progress.connect(self._on_progress)
        self.worker.status.connect(self._on_status)
        self.worker.result.connect(self._on_result)
        self.worker.error.connect(self._on_error)
        self.worker.finished.connect(self._on_finished)

        self.worker.start()

    # --- sygnały z workera ---

    def _on_progress(self, value: int):
        # jeżeli nadal w trybie „busy”, przełączamy na normalny progress
        if self.prg.minimum() == 0 and self.prg.maximum() == 0:
            self.prg.setRange(0, 100)
        self.prg.setValue(value)

    def _on_status(self, text: str):
        self.lbl_status.setText(text)

    def _on_result(self, organs: list):
        self.lst.clear()
        if not organs:
            self.lbl_status.setText("Brak dopasowanych organów.")
            self.prg.setRange(0, 100)
            self.prg.setValue(100)
        else:
            for name in sorted(organs, key=lambda s: s.lower()):
                self.lst.addItem(QListWidgetItem(name))
            self.lbl_status.setText(f"Znaleziono: {len(organs)}")
            self.prg.setRange(0, 100)
            self.prg.setValue(100)

        # po wyniku można znowu szukać
        self.btn_search.setEnabled(True)

    def _on_error(self, msg: str):
        QMessageBox.critical(self, "Błąd", msg)
        self.lbl_status.setText("Błąd.")
        # sprzątanie po błędzie
        self._stop_worker()
        self.btn_search.setEnabled(True)

    def _on_finished(self):
        """
        Worker zakończył się (wynik/błąd/abort).
        """
        self.search_timer.stop()
        self.worker = None
        # przy normalnym wyniku _on_result już ustawi progress/status

    # --- timer 10 sekund – pytanie „czy czekać dalej?” ---

    def _on_search_timeout(self):
        # jeżeli worker już się skończył – nic nie robimy
        if self.worker is None:
            return

        self.wait_intervals += 1
        seconds = self.wait_intervals * 10

        reply = QMessageBox.question(
            self,
            "Czekać dalej?",
            f"Szukanie w EZiUDP trwa już około {seconds} s.\n\n"
            "Czy chcesz czekać kolejne 10 sekund?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.No:
            self.lbl_status.setText("Przerywanie operacji…")
            self._stop_worker()
            self.btn_search.setEnabled(True)
        else:
            # kolejne 10 s czekania
            self.search_timer.start(10_000)

    # --- bezpieczne sprzątanie workera ---

    def _stop_worker(self):
        if self.worker is not None:
            try:
                if hasattr(self.worker, "abort"):
                    self.worker.abort()
            except Exception:
                pass

            # odpinamy sygnały, żeby wątek nie strzelał w zamknięte okno
            for sig_name in ("progress", "status", "result", "error", "finished"):
                try:
                    getattr(self.worker, sig_name).disconnect()
                except Exception:
                    pass

            self.worker = None

        self.search_timer.stop()
        self.prg.setRange(0, 100)
        self.prg.setValue(0)

    def reject(self):
        """Anulowanie dialogu (przycisk Cancel)."""
        self._stop_worker()
        super().reject()

    def closeEvent(self, event):
        """Zamknięcie okna krzyżykiem X."""
        self._stop_worker()
        super().closeEvent(event)
