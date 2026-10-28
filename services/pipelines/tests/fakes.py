from datetime import date

from services.pipelines import amfi_nav, fundamentals, nse_bhavcopy, nse_indices


class FakeAdapter:
    """In-memory source adapter for tests: fixture bytes/text per day go through the real parsers.
    Days not in the dicts return None (holiday / not available); every call is recorded."""

    def __init__(self, name="dev_local", prices=None, indices=None, navs_latest=None, months=None, financials=None):
        self.name = name
        self.financials = financials or {}  # isin -> raw records for fundamentals.build
        self.prices, self.indices, self.months = prices or {}, indices or {}, months or {}
        self.navs_latest = navs_latest
        self.calls: list[tuple[str, object]] = []

    def eod_prices(self, day: date):
        self.calls.append(("eod_prices", day))
        data = self.prices.get(day)
        return nse_bhavcopy.parse(data, day) if data is not None else None

    def index_closes(self, day: date):
        self.calls.append(("index_closes", day))
        data = self.indices.get(day)
        return nse_indices.parse(data, day) if data is not None else None

    def fund_navs_latest(self, today: date):
        self.calls.append(("fund_navs_latest", today))
        if callable(self.navs_latest):
            return amfi_nav.parse(self.navs_latest(), today)
        return amfi_nav.parse(self.navs_latest, today) if self.navs_latest is not None else None

    def fund_navs_month(self, year: int, month: int, today: date):
        self.calls.append(("fund_navs_month", (year, month)))
        text = self.months.get((year, month))
        return amfi_nav.parse(text, today) if text is not None else None

    def fundamentals(self, isin: str):
        self.calls.append(("fundamentals", isin))
        records = self.financials.get(isin)
        return fundamentals.build(records) if records is not None else None
