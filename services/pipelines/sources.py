"""Source adapters: where pipeline data comes from, kept apart from how it is validated and loaded.

Every adapter returns the same normalised Parsed objects the loaders already take, so swapping the source never
touches loaders, the schema or agents. Loaders stamp each row with source = adapter.name, and
data_sources.public_ok decides whether anything derived from those rows may be shown publicly.

Pick one with DATA_SOURCE=dev_local|licensed_vendor (default dev_local) or --source on the pipeline CLIs.
"""

import os
from datetime import date
from pathlib import Path
from typing import Protocol

from services.pipelines import amfi_nav, nse_bhavcopy, nse_indices
from services.pipelines.common import RAW_DATA_DIR, PipelineError


class SourceAdapter(Protocol):
    name: str  # a data_sources.name

    def eod_prices(self, day: date) -> nse_bhavcopy.Parsed | None:
        """Equity EOD rows for one trading day (validated, deduplicated); None when there is no data (holiday)."""

    def index_closes(self, day: date) -> nse_indices.Parsed | None:
        """Index closes for one trading day; must include Nifty 50; None when there is no data."""

    def fund_navs_latest(self, today: date) -> amfi_nav.Parsed | None:
        """Latest NAV per scheme plus the scheme master; None when nothing is available for today."""

    def fund_navs_month(self, year: int, month: int, today: date) -> amfi_nav.Parsed | None:
        """Every NAV published in a calendar month (history backfill); None when the month is unavailable."""


class DevLocal:
    """Files already downloaded into RAW_DATA_DIR during development (NSE bhavcopy/index files, AMFI reports).
    Never touches the network. Their sites' terms forbid public display, so data_sources marks 'dev_local'
    public_ok = false and nothing derived from it can reach a public page."""

    name = "dev_local"
    UDIFF_FROM = date(2024, 7, 8)  # first day NSE files use the UDiFF name/format
    MONTHS = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()

    def __init__(self, raw_dir: Path = RAW_DATA_DIR):
        self.raw_dir = raw_dir

    def _read(self, folder: str, filename: str) -> bytes | None:
        path = self.raw_dir / folder / filename
        return path.read_bytes() if path.exists() else None

    def _text(self, folder: str, filename: str) -> str | None:
        data = self._read(folder, filename)
        if data is None:
            return None
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise PipelineError(f"{folder}/{filename}: not UTF-8: {e}") from e

    def bhavcopy_filename(self, day: date) -> str:
        if day >= self.UDIFF_FROM:
            return f"BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"
        return f"cm{day:%d}{self.MONTHS[day.month - 1]}{day:%Y}bhav.csv.zip"

    def eod_prices(self, day: date) -> nse_bhavcopy.Parsed | None:
        data = self._read("nse", self.bhavcopy_filename(day))
        return nse_bhavcopy.parse(data, day) if data is not None else None

    def index_closes(self, day: date) -> nse_indices.Parsed | None:
        data = self._read("nse_indices", f"ind_close_all_{day:%d%m%Y}.csv")
        return nse_indices.parse(data, day) if data is not None else None

    def fund_navs_latest(self, today: date) -> amfi_nav.Parsed | None:
        text = self._text("amfi", f"NAVAll_{today:%Y-%m-%d}.txt")
        return amfi_nav.parse(text, today) if text is not None else None

    def fund_navs_month(self, year: int, month: int, today: date) -> amfi_nav.Parsed | None:
        text = self._text("amfi_history", f"NAVHistory_{year}-{month:02d}.txt")
        return amfi_nav.parse(text, today) if text is not None else None


class LicensedVendor:
    """Stub for a licensed feed (e.g. Accord/ACE, CMOTS, NSE Data & Analytics). Implement each method by mapping
    the vendor's feed onto the same Parsed shape DevLocal returns (see the Parsed docstrings in each pipeline).
    Set data_sources.public_ok for 'licensed_vendor' only once the signed licence permits display."""

    name = "licensed_vendor"

    def __init__(self, vendor: str | None = None):
        self.vendor = vendor or os.environ.get("LICENSED_VENDOR", "")

    def _todo(self, method: str, shape: str) -> None:
        raise NotImplementedError(f"licensed_vendor ({self.vendor or 'vendor not set'}): {method} not implemented; "
                                  f"map the vendor feed to {shape}")

    def eod_prices(self, day: date) -> nse_bhavcopy.Parsed | None:
        self._todo("eod_prices", "nse_bhavcopy.Parsed")

    def index_closes(self, day: date) -> nse_indices.Parsed | None:
        self._todo("index_closes", "nse_indices.Parsed")

    def fund_navs_latest(self, today: date) -> amfi_nav.Parsed | None:
        self._todo("fund_navs_latest", "amfi_nav.Parsed")

    def fund_navs_month(self, year: int, month: int, today: date) -> amfi_nav.Parsed | None:
        self._todo("fund_navs_month", "amfi_nav.Parsed")


ADAPTERS: dict[str, type] = {"dev_local": DevLocal, "licensed_vendor": LicensedVendor}


def get_adapter(name: str | None = None) -> SourceAdapter:
    name = name or os.environ.get("DATA_SOURCE", "dev_local")
    if name not in ADAPTERS:
        raise ValueError(f"unknown data source {name!r}; choose from {sorted(ADAPTERS)}")
    return ADAPTERS[name]()
