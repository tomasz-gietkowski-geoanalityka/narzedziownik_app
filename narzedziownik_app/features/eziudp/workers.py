"""
Narzędziownik APP - Wtyczka QGIS
Informacje o autorach, repozytorium: https://github.com/tomasz-gietkowski-geoanalityka/narzedziownik_app
Dokumentacja: https://akademia.geoanalityka.pl/courses/narzedziownik-app-dokumentacja/
Licencja: GNU GPL v3.0 (https://www.gnu.org/licenses/gpl-3.0.html)
"""

# -*- coding: utf-8 -*-
import urllib.parse
from PyQt5.QtCore import QThread, pyqtSignal

from .http import http_get, BASE_URL, normalize_caps_url
from .parsers import (
    extract_organs,
    extract_datasets,
    parse_wms_titles_names_and_crs,
    parse_wfs_titles_names_and_crs,
)


class _AbortException(Exception):
    """Wewnętrzny wyjątek do eleganckiego wyjścia z run() po abort()."""
    pass


class OrgSearchWorker(QThread):
    """
    Worker do wyszukiwania organów w EZiUDP.

    - ma timeout na http_get (żeby nie wisiało wiecznie),
    - obsługuje abort() wywoływane z GUI,
    - brak parenta w QThread → zamknięcie dialogu nie zabija QGIS-a.
    """

    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    result = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, organ_query: str, parent=None):
        # UWAGA: nie przekazujemy parent do QThread,
        # dzięki temu zniszczenie dialogu nie niszczy żywego wątku.
        super().__init__()
        self._qt_parent = parent  # tylko informacyjnie, nie jako parent Qt
        self.organ_query = (organ_query or "").strip()
        self._abort = False

    def abort(self):
        """Poproś wątek o grzeczne zakończenie pracy."""
        self._abort = True

    def _check_abort(self):
        if self._abort:
            raise _AbortException()

    def run(self):
        try:
            self._check_abort()
            self.status.emit("Szukam organów w EZiUDP…")
            self.progress.emit(5)

            self._check_abort()
            url = BASE_URL.format(query=urllib.parse.quote(self.organ_query))

            # timeout DUŻO większy niż 10 s, żeby timer w GUI miał sens
            self._check_abort()
            html_bytes = http_get(url, timeout=60)
            self._check_abort()

            html_text = html_bytes.decode("utf-8", errors="ignore")
            self._check_abort()

            organs = extract_organs(html_text)
            self._check_abort()

            self.progress.emit(100)
            self.result.emit(organs)

        except _AbortException:
            # użytkownik przerwał – cicho wychodzimy
            return
        except Exception as e:
            msg = str(e)
            if "timed out" in msg.lower():
                msg = "Przekroczono czas oczekiwania na odpowiedź EZiUDP."
            self.error.emit(msg)


class ServicesWorker(QThread):
    """
    Worker do pobierania usług WMS/WFS dla wybranego organu.

    - obsługuje abort() (można przerwać długie pobieranie capabilities),
    - używa timeoutów w http_get.
    """

    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    result = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, organ_name: str, parent=None):
        # jw. – brak parenta w QThread
        super().__init__()
        self._qt_parent = parent
        self.organ_name = (organ_name or "").strip()
        self._abort = False

    def abort(self):
        """Poproś wątek o grzeczne zakończenie pracy."""
        self._abort = True

    def _check_abort(self):
        if self._abort:
            raise _AbortException()

    def run(self):
        try:
            self._check_abort()
            self.status.emit("Wyszukuję usługi dla wybranego organu…")
            self.progress.emit(5)

            import html
            import urllib.parse
            from html.parser import HTMLParser
            from .parsers import classify_service_href

            url = BASE_URL.format(query=urllib.parse.quote(self.organ_name))

            # timeout na HTML z listą usług
            self._check_abort()
            html_bytes = http_get(url, timeout=60)
            self._check_abort()

            html_text = html_bytes.decode("utf-8", errors="ignore")
            self._check_abort()

            ds_urls = extract_datasets(html_text)

            if not ds_urls:
                # fallback: łap wszystkie linki i klasyfikuj AUTO/WMS/WFS
                class _AllLinks(HTMLParser):
                    def __init__(self):
                        super().__init__()
                        self.links = []

                    def handle_starttag(self, tag, attrs):
                        if tag.lower() == "a":
                            for k, v in attrs:
                                if k and k.lower() == "href" and v:
                                    self.links.append(html.unescape(v).strip())

                al = _AllLinks()
                al.feed(html_text)
                self._check_abort()

                ds_urls = {"Nieznany zbiór danych": {"WMS": set(), "WFS": set()}}

                for href in al.links:
                    self._check_abort()
                    kind = classify_service_href(href)
                    if not kind:
                        continue
                    if kind == "AUTO":
                        ds_urls["Nieznany zbiór danych"]["WMS"].add(href)
                        ds_urls["Nieznany zbiór danych"]["WFS"].add(href)
                    else:
                        ds_urls["Nieznany zbiór danych"][kind].add(href)

            datasets = {}
            plan = []
            for ds_name, buckets in ds_urls.items():
                for kind in ("WMS", "WFS"):
                    for href in sorted(buckets.get(kind, [])):
                        plan.append((ds_name, kind, href))

            total = max(1, len(plan))

            for i, (ds_name, kind_try, href) in enumerate(plan, 1):
                self._check_abort()

                self.status.emit(f"Pobieram {kind_try} ({i}/{total})…")
                caps_url = normalize_caps_url(href, kind_try)

                try:
                    # timeout na capabilities
                    self._check_abort()
                    caps = http_get(caps_url, timeout=60)
                    self._check_abort()

                    d = datasets.setdefault(
                        ds_name,
                        {
                            "WMS": set(),
                            "WFS": set(),
                            "WMS_LAYERS": {},
                            "WFS_LAYERS": {},
                            "WMS_CRS": {},
                            "WFS_CRS": {},
                        },
                    )

                    if kind_try == "WMS":
                        items, crs = parse_wms_titles_names_and_crs(caps, href=href)
                        self._check_abort()

                        d["WMS"].add(href)
                        d["WMS_LAYERS"].setdefault(href, [])
                        d["WMS_CRS"].setdefault(href, set()).update(crs)

                        seen_local = set(
                            (it["title"], it["name"], it.get("depth", 0))
                            for it in d["WMS_LAYERS"][href]
                        )

                        for it in items:
                            self._check_abort()
                            key = (it["title"], it["name"], it.get("depth", 0))
                            if key not in seen_local:
                                d["WMS_LAYERS"][href].append(it)
                                seen_local.add(key)

                    else:  # WFS
                        pairs, crs = parse_wfs_titles_names_and_crs(caps)
                        self._check_abort()

                        d["WFS"].add(href)
                        d["WFS_LAYERS"].setdefault(href, [])
                        d["WFS_CRS"].setdefault(href, set()).update(crs)

                        seen_local = set((t, n) for (t, n, _) in d["WFS_LAYERS"][href])

                        for (t, n, is_group) in pairs:
                            self._check_abort()
                            if (t, n) not in seen_local:
                                d["WFS_LAYERS"][href].append((t, n, is_group))
                                seen_local.add((t, n))

                except _AbortException:
                    # przerwane w trakcie pobierania capabilities
                    raise
                except Exception as e:
                    self.status.emit(f"Błąd {kind_try}: {e}")

                self.progress.emit(min(99, int(100 * i / total)))

            # sortowanie warstw
            for v in datasets.values():
                for href, lst in v["WMS_LAYERS"].items():
                    lst.sort(key=lambda it: (it.get("depth", 0), (it.get("title") or "").casefold()))
                for href, lst in v["WFS_LAYERS"].items():
                    lst.sort(key=lambda p: (p[0] or "").casefold())

            # konwersja setów na listy do wygodnego użycia w GUI
            out = {}
            for k, v in datasets.items():
                out[k] = {
                    "WMS": sorted(v["WMS"]),
                    "WFS": sorted(v["WFS"]),
                    "WMS_LAYERS": v["WMS_LAYERS"],
                    "WFS_LAYERS": v["WFS_LAYERS"],
                    "WMS_CRS": {u: sorted(list(s)) for u, s in v["WMS_CRS"].items()},
                    "WFS_CRS": {u: sorted(list(s)) for u, s in v["WFS_CRS"].items()},
                }

            self._check_abort()
            self.progress.emit(100)
            self.result.emit(out)

        except _AbortException:
            # użytkownik przerwał – bez błędu
            return
        except Exception as e:
            msg = str(e)
            if "timed out" in msg.lower():
                msg = "Przekroczono czas oczekiwania na odpowiedź serwera usług (WMS/WFS)."
            self.error.emit(msg)
