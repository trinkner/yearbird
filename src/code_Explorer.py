import csv
import json
import ssl
import urllib.request
import urllib.error

# Build an SSL context that works inside a PyInstaller bundle on Windows.
# certifi ships its own CA bundle; fall back to the default context on platforms
# where certifi is unavailable (the default context uses the OS trust store).
try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()

import code_Filter
import code_DataBase
import code_Web

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QCursor, QFont
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFormLayout, QLabel,
    QMdiSubWindow, QMessageBox, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)


# ── Background thread for eBird region list fetches ───────────────────────────

class _RegionFetch(QThread):
    """Fetches /v2/ref/region/list/{regionType}/{parentCode} in a background thread."""
    done = Signal(list)   # emits list of {"code":…, "name":…} or [] on error

    def __init__(self, path, api_key):
        super().__init__()
        self._path    = path
        self._api_key = api_key

    def run(self):
        url = "https://api.ebird.org" + self._path
        req = urllib.request.Request(url, headers={"X-eBirdApiToken": self._api_key})
        try:
            with urllib.request.urlopen(req, timeout=20, context=_SSL_CTX) as resp:
                self.done.emit(json.loads(resp.read().decode("utf-8")))
        except Exception:
            self.done.emit([])


# ── Subnational-1 CSV loader (cached at module level) ─────────────────────────

_STATE_DATA = None   # dict: country_code → sorted list of (state_name, state_code)

def _load_state_data():
    global _STATE_DATA
    if _STATE_DATA is not None:
        return
    path = code_DataBase.resource_path("ebird_api_ref_location_eBird_list_subnational1.csv")
    data = {}
    try:
        with open(path, "r", errors="replace") as f:
            reader = csv.reader(f)
            next(reader)  # skip header
            for row in reader:
                if len(row) < 3:
                    continue
                country_code, state_code, state_name = row[0].strip(), row[1].strip(), row[2].strip()
                if not state_code or state_code.endswith("-"):
                    # Entry with no subnational1 divisions — skip
                    continue
                data.setdefault(country_code, []).append((state_name, state_code))
    except Exception:
        pass
    for code in data:
        data[code].sort()
    _STATE_DATA = data


# ── Explorer window ───────────────────────────────────────────────────────────

class Explorer(QMdiSubWindow):

    def __init__(self):
        super().__init__()
        self.mdiParent  = ""
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setWindowTitle("Community Sightings Explorer")

        self._api_key    = ""
        self._fetch_thread = None   # active QThread, kept alive until done

        _load_state_data()
        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(16, 12, 16, 14)
        root.setSpacing(10)

        # Info notice
        notice = QLabel("Reports from this window use the selected region,\nnot the Sighting Filter.")
        notice.setAlignment(Qt.AlignLeft)
        notice_font = notice.font()
        notice_font.setItalic(True)
        notice_font.setPointSize(notice_font.pointSize() - 1)
        notice.setFont(notice_font)
        notice.setStyleSheet("color: #666;")
        root.addWidget(notice)

        # Form: Country / State / County
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        root.addLayout(form)

        self.countryCombo = QComboBox()
        self.countryCombo.setMinimumWidth(240)
        self.countryCombo.addItem("Loading countries…")
        self.countryCombo.setEnabled(False)
        form.addRow("Country:", self.countryCombo)

        self.stateCombo = QComboBox()
        self.stateCombo.setMinimumWidth(240)
        self.stateCombo.addItem("— select country first —")
        self.stateCombo.setEnabled(False)
        form.addRow("State / Province:", self.stateCombo)

        self.countyCombo = QComboBox()
        self.countyCombo.setMinimumWidth(240)
        self.countyCombo.addItem("— select state first —")
        self.countyCombo.setEnabled(False)
        form.addRow("County (optional):", self.countyCombo)

        # Status label (shown during county fetch)
        self._statusLabel = QLabel("")
        status_font = self._statusLabel.font()
        status_font.setItalic(True)
        status_font.setPointSize(status_font.pointSize() - 1)
        self._statusLabel.setFont(status_font)
        self._statusLabel.setStyleSheet("color: #888;")
        self._statusLabel.setVisible(False)
        root.addWidget(self._statusLabel)

        root.addStretch()

        # Buttons
        self.notableBtn = QPushButton("Notable Community Sightings (Past 3 days)")
        self.notableBtn.setEnabled(False)
        self.notableBtn.clicked.connect(self._runNotable)
        root.addWidget(self.notableBtn)

        self.allBtn = QPushButton("All Community Sightings (Past 3 days)")
        self.allBtn.setEnabled(False)
        self.allBtn.clicked.connect(self._runAll)
        root.addWidget(self.allBtn)

        self.notableMapBtn = QPushButton("Notable Sightings Map (Past 3 days)")
        self.notableMapBtn.setEnabled(False)
        self.notableMapBtn.clicked.connect(self._runNotableMap)
        root.addWidget(self.notableMapBtn)

        self.hotspotBtn = QPushButton("Hotspot Map")
        self.hotspotBtn.setEnabled(False)
        self.hotspotBtn.clicked.connect(self._runHotspotMap)
        root.addWidget(self.hotspotBtn)

        self.speciesListBtn = QPushButton("Species List")
        self.speciesListBtn.setEnabled(False)
        self.speciesListBtn.clicked.connect(self._runSpeciesList)
        root.addWidget(self.speciesListBtn)

        # Signals
        self.countryCombo.currentIndexChanged.connect(self._onCountryChanged)
        self.stateCombo.currentIndexChanged.connect(self._onStateChanged)

    # ── Initialisation (called after mdiParent is set) ────────────────────────

    def load(self):
        """Fetch the country list and populate the country combobox."""
        self._api_key = self.mdiParent.db.ebirdApiKey.strip()
        thread = _RegionFetch("/v2/ref/region/list/country/world", self._api_key)
        thread.done.connect(self._onCountriesLoaded)
        self._fetch_thread = thread
        thread.start()

    def scaleMe(self):
        sf = self.mdiParent.scaleFactor
        self.resize(int(440 * sf), int(395 * sf))

    # ── Slot: country list arrived ────────────────────────────────────────────

    def _onCountriesLoaded(self, regions):
        self.countryCombo.clear()
        if not regions:
            self.countryCombo.addItem("(could not load — check API key)")
            return

        # Sort alphabetically; put United States and Canada at the top for convenience
        def _sort_key(r):
            name = r.get("name", "")
            if name == "United States":   return "  " + name
            if name == "Canada":          return " "  + name
            return name

        regions.sort(key=_sort_key)
        self.countryCombo.addItem("— select country —", None)
        for r in regions:
            self.countryCombo.addItem(r.get("name", r["code"]), r["code"])
        self.countryCombo.setEnabled(True)

    # ── Slot: country changed ─────────────────────────────────────────────────

    def _onCountryChanged(self, index):
        country_code = self.countryCombo.currentData()

        # Reset downstream combos
        self.stateCombo.clear()
        self.countyCombo.clear()
        self.countyCombo.addItem("— select state first —")
        self.countyCombo.setEnabled(False)
        self._setButtonsEnabled(False)

        if not country_code:
            self.stateCombo.addItem("— select country first —")
            self.stateCombo.setEnabled(False)
            return

        states = (_STATE_DATA or {}).get(country_code, [])
        if not states:
            self.stateCombo.addItem("(no state/province data)")
            self.stateCombo.setEnabled(False)
            # Allow reports at country level
            self._setButtonsEnabled(True)
            return

        self.stateCombo.addItem("— select state (optional) —", None)
        for name, code in states:
            self.stateCombo.addItem(name, code)
        self.stateCombo.setEnabled(True)
        # Country is selected — enable buttons at country level
        self._setButtonsEnabled(True)

    # ── Slot: state changed ───────────────────────────────────────────────────

    def _onStateChanged(self, index):
        state_code = self.stateCombo.currentData()

        self.countyCombo.clear()
        self.countyCombo.setEnabled(False)
        self._statusLabel.setVisible(False)

        if not state_code:
            self.countyCombo.addItem("— select state first —")
            return

        # Fetch counties for this state in the background
        self.countyCombo.addItem("Loading counties…")
        self._statusLabel.setText(f"Fetching counties for {self.stateCombo.currentText()}…")
        self._statusLabel.setVisible(True)

        thread = _RegionFetch(
            f"/v2/ref/region/list/subnational2/{state_code}",
            self._api_key,
        )
        thread.done.connect(self._onCountiesLoaded)
        self._fetch_thread = thread
        thread.start()

    # ── Slot: county list arrived ─────────────────────────────────────────────

    def _onCountiesLoaded(self, regions):
        self._statusLabel.setVisible(False)
        self.countyCombo.clear()

        if not regions:
            self.countyCombo.addItem("(no county data available)")
            return

        regions.sort(key=lambda r: r.get("name", ""))
        self.countyCombo.addItem("— all counties —", None)
        for r in regions:
            self.countyCombo.addItem(r.get("name", r["code"]), r["code"])
        self.countyCombo.setEnabled(True)

    # ── Filter construction ───────────────────────────────────────────────────

    def _build_filter(self):
        """Return a (filter, region_code, region_label) triple from current selections,
        or (None, None, None) if no region is selected."""
        county_code  = self.countyCombo.currentData()  if self.countyCombo.isEnabled()  else None
        state_code   = self.stateCombo.currentData()   if self.stateCombo.isEnabled()   else None
        country_code = self.countryCombo.currentData()

        if county_code:
            region_code  = county_code
            region_label = self.countyCombo.currentText()
        elif state_code:
            region_code  = state_code
            region_label = self.stateCombo.currentText()
        elif country_code:
            region_code  = country_code
            region_label = self.countryCombo.currentText()
        else:
            return None, None, None

        f = code_Filter.Filter()
        f.setLocationType("EBirdRegion")
        f.setLocationName(region_code)
        f.regionLabel = region_label
        return f, region_code, region_label

    # ── Report launchers ──────────────────────────────────────────────────────

    def _runNotable(self):
        f, code, label = self._build_filter()
        if not f:
            return
        sub = code_Web.Web()
        sub.mdiParent = self.mdiParent
        if sub.loadNotableSightings(f) is True:
            self.mdiParent.mdiArea.addSubWindow(sub)
            self.mdiParent.PositionChildWindow(sub, self)
            sub.show()

    def _runAll(self):
        f, code, label = self._build_filter()
        if not f:
            return
        sub = code_Web.Web()
        sub.mdiParent = self.mdiParent
        if sub.loadAllSightings(f) is True:
            self.mdiParent.mdiArea.addSubWindow(sub)
            self.mdiParent.PositionChildWindow(sub, self)
            sub.show()

    def _runNotableMap(self):
        f, code, label = self._build_filter()
        if not f:
            return
        sub = code_Web.Web()
        sub.mdiParent = self.mdiParent
        if sub.loadNotableMap(f) is True:
            self.mdiParent.mdiArea.addSubWindow(sub)
            self.mdiParent.PositionChildWindow(sub, self)
            sub.show()

    def _runHotspotMap(self):
        f, code, label = self._build_filter()
        if not f:
            return
        sub = code_Web.Web()
        sub.mdiParent = self.mdiParent
        if sub.loadHotspotMap(f) is True:
            self.mdiParent.mdiArea.addSubWindow(sub)
            self.mdiParent.PositionChildWindow(sub, self)
            sub.show()

    def _runSpeciesList(self):
        f, code, label = self._build_filter()
        if not f:
            return
        sub = code_Web.Web()
        sub.mdiParent = self.mdiParent
        if sub.loadRegionalTaxonomy(f) is True:
            self.mdiParent.mdiArea.addSubWindow(sub)
            self.mdiParent.PositionChildWindow(sub, self)
            sub.show()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _setButtonsEnabled(self, enabled):
        self.notableBtn.setEnabled(enabled)
        self.allBtn.setEnabled(enabled)
        self.notableMapBtn.setEnabled(enabled)
        self.hotspotBtn.setEnabled(enabled)
        self.speciesListBtn.setEnabled(enabled)
