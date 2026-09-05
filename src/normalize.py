import re
from datetime import datetime
from typing import Optional


class Normalizer:
    # Strip known word prefixes followed by optional separator
    _PREFIX_PATTERN = re.compile(
        r"^(?:INV(?:OICE)?|IN|BILL|PI|SAL|SO|PO|PRO)[/\-_ ]*",
        re.IGNORECASE,
    )
    # Strip year markers:
    # 1. Separator followed by year segment: /2026/ or /2026 or -2026
    _YEAR_MID_OR_END = re.compile(
        r"[/\-_](?:FY\d{2,4}|(?:19|20)\d{2})(?=[/\-_]|$)",
        re.IGNORECASE,
    )
    # 2. Leading year segment after prefix was stripped: e.g. 2026/0042 from INV/2026/0042
    _YEAR_START = re.compile(
        r"^(?:FY\d{2,4}|(?:19|20)\d{2})[/\-_]",
        re.IGNORECASE,
    )
    # 3. Trailing FY marker
    _FY_SUFFIX = re.compile(
        r"FY\d{2,4}$",
        re.IGNORECASE,
    )

    def normalize_invoice_number(self, raw: str) -> str:
        if not raw:
            return raw
        s = str(raw).strip()
        # Step 1: strip leading keyword prefix (INV, BILL, PO, etc.)
        s = self._PREFIX_PATTERN.sub("", s)
        # Step 2: strip year markers that are clearly delimited by separators
        s = self._YEAR_MID_OR_END.sub("", s)
        s = self._YEAR_START.sub("", s)
        s = self._FY_SUFFIX.sub("", s)
        # Step 3: remove remaining separators
        s = re.sub(r"[/\-_]", "", s)
        # Step 4: remove leading zeros but keep at least one digit
        s = s.lstrip("0") or "0"
        return s.upper()

    def normalize_gstin(self, raw: str) -> str:
        if not raw:
            return raw
        return str(raw).strip().upper()

    def parse_date(self, raw: str) -> Optional[datetime]:
        if not raw:
            return None
        raw = str(raw).strip()
        formats = ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%y", "%d/%m/%y"]
        for fmt in formats:
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                continue
        return None

    def normalize_date(self, raw: str) -> str:
        dt = self.parse_date(raw)
        if dt:
            return dt.strftime("%Y-%m-%d")
        return str(raw)

    def normalize_amount(self, raw) -> float:
        if isinstance(raw, (int, float)):
            return float(raw)
        if isinstance(raw, str):
            try:
                return float(raw.replace(",", "").strip())
            except ValueError:
                return 0.0
        return 0.0
