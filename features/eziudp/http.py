"""
Narzędziownik APP - Wtyczka QGIS
Informacje o autorach, repozytorium: https://github.com/tomasz-gietkowski-geoanalityka/narzedziownik_app
Dokumentacja: https://akademia.geoanalityka.pl/courses/narzedziownik-app-dokumentacja/
Licencja: GNU GPL v3.0 (https://www.gnu.org/licenses/gpl-3.0.html)

"""

# -*- coding: utf-8 -*-
import html
import re
import ssl
import urllib.parse
import urllib.request

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
BASE_URL = "https://services.gugik.gov.pl/eziudp/index.php?idiip=&teryt=&rodzaj=&nazwa={query}&zbior=&temat=&usluga=&adres="

_SSL_UNVERIFIED = ssl.create_default_context()
_SSL_UNVERIFIED.check_hostname = False
_SSL_UNVERIFIED.verify_mode = ssl.CERT_NONE


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def http_get(url: str, timeout=25) -> bytes:
    scheme = urllib.parse.urlsplit(url).scheme
    if scheme not in ("http", "https"):
        raise ValueError(f"Niedozwolony schemat URL: {scheme!r}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.read()
    except urllib.error.URLError as e:
        if "CERTIFICATE_VERIFY_FAILED" in str(e) or "SSL" in str(e).upper():
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_UNVERIFIED) as resp:  # noqa: S310
                return resp.read()
        raise


def ensure_param(url: str, key: str, value: str) -> str:
    p = urllib.parse.urlsplit(url)
    q = urllib.parse.parse_qs(p.query, keep_blank_values=True)
    q[key] = [value]
    new_q = urllib.parse.urlencode(q, doseq=True)
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, new_q, p.fragment))


def normalize_caps_url(service_url: str, service_kind: str) -> str:
    u = html.unescape(service_url.strip())
    p = urllib.parse.urlsplit(u)
    q = urllib.parse.parse_qs(p.query, keep_blank_values=True)
    if not any(k.lower() == "service" for k in q.keys()):
        u = ensure_param(u, "SERVICE", service_kind.upper())
    if not any(k.lower() == "request" for k in q.keys()):
        u = ensure_param(u, "REQUEST", "GetCapabilities")
    return u
