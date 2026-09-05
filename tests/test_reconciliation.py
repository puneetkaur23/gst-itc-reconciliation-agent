import sys
import os
from pathlib import Path

# Run tests from project root — add src/ to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from datetime import date, datetime, timedelta

from normalize import Normalizer
from matcher import InvoiceMatcher
from exceptions import ExceptionClassifier
from compliance import ComplianceTracker


# ============================================================
# TestNormalizer
# ============================================================
class TestNormalizer:
    def setup_method(self):
        self.n = Normalizer()

    def test_prefix_inv_year(self):
        assert self.n.normalize_invoice_number("INV/2026/0042") == "42"

    def test_prefix_bill_underscore(self):
        assert self.n.normalize_invoice_number("BILL_0042") == "42"

    def test_leading_zeros(self):
        assert self.n.normalize_invoice_number("0042") == "42"

    def test_suffix_fy(self):
        assert self.n.normalize_invoice_number("42/FY26") == "42"

    def test_suffix_year(self):
        assert self.n.normalize_invoice_number("0042/2026") == "42"

    def test_plain_number(self):
        assert self.n.normalize_invoice_number("42") == "42"

    def test_prefix_inv_slash(self):
        assert self.n.normalize_invoice_number("INV/0042") == "42"

    def test_prefix_po(self):
        assert self.n.normalize_invoice_number("PO-0042") == "42"

    def test_date_iso(self):
        dt = self.n.parse_date("2026-03-15")
        assert dt == datetime(2026, 3, 15)

    def test_date_dmy_dash(self):
        dt = self.n.parse_date("15-03-2026")
        assert dt == datetime(2026, 3, 15)

    def test_date_dmy_slash(self):
        dt = self.n.parse_date("15/03/2026")
        assert dt == datetime(2026, 3, 15)

    def test_date_short_year(self):
        dt = self.n.parse_date("15-03-26")
        assert dt is not None
        assert dt.month == 3

    def test_date_invalid(self):
        assert self.n.parse_date("not-a-date") is None

    def test_normalize_date(self):
        assert self.n.normalize_date("15-03-2026") == "2026-03-15"

    def test_normalize_date_fallback(self):
        # Invalid date returns raw
        assert self.n.normalize_date("bad") == "bad"

    def test_gstin_exact(self):
        raw = "  27aabcu9603r1zm  "
        assert self.n.normalize_gstin(raw) == "27AABCU9603R1ZM"

    def test_normalize_amount_float(self):
        assert self.n.normalize_amount(1234.56) == 1234.56

    def test_normalize_amount_string_commas(self):
        assert self.n.normalize_amount("1,234.56") == 1234.56

    def test_normalize_amount_invalid(self):
        assert self.n.normalize_amount("abc") == 0.0


# ============================================================
# TestMatcher
# ============================================================
class TestMatcher:
    def setup_method(self):
        self.m = InvoiceMatcher()

    def _make_pr(self, inv_num, inv_date, taxable, total_tax, gstin="27AABCU9603R1ZM"):
        from normalize import Normalizer
        n = Normalizer()
        dt = n.parse_date(inv_date)
        return {
            "invoice_number_raw": inv_num,
            "invoice_number_norm": n.normalize_invoice_number(inv_num),
            "invoice_date_raw": inv_date,
            "invoice_date_norm": n.normalize_date(inv_date),
            "invoice_date_parsed": dt,
            "supplier_gstin": gstin,
            "supplier_name": "Test Supplier",
            "taxable_value": float(taxable),
            "total_tax": float(total_tax),
            "invoice_total": float(taxable) + float(total_tax),
            "_source": "purchase_register",
        }

    def _make_gstr(self, inv_num, inv_date, taxable, total_tax, gstin="27AABCU9603R1ZM"):
        from normalize import Normalizer
        n = Normalizer()
        dt = n.parse_date(inv_date)
        return {
            "invoice_number_raw": inv_num,
            "invoice_number_norm": n.normalize_invoice_number(inv_num),
            "invoice_date_raw": inv_date,
            "invoice_date_norm": n.normalize_date(inv_date),
            "invoice_date_parsed": dt,
            "supplier_gstin": gstin,
            "supplier_name": "Test Supplier",
            "taxable_value": float(taxable),
            "total_tax": float(total_tax),
            "invoice_total": float(taxable) + float(total_tax),
            "_source": "gstr2b",
            "_consumed": False,
        }

    def test_exact_match_score_1(self):
        pr = self._make_pr("42", "2026-01-15", 10000, 1800)
        gstr = self._make_gstr("42", "2026-01-15", 10000, 1800)
        result = self.m.compute_match_score(pr, gstr)
        assert result["composite_score"] == pytest.approx(1.0)

    def test_formatting_diff_auto_match(self):
        """Invoice with prefix in PR should auto-match clean number in GSTR-2B."""
        pr = self._make_pr("INV/2026/0042", "2026-01-15", 10000, 1800)
        gstr = self._make_gstr("42", "15-01-2026", 10000, 1800)
        result = self.m.compute_match_score(pr, gstr)
        # Both normalize to "42" so number_score = 1.0; date same; amounts same
        assert result["composite_score"] >= 0.90
        assert self.m.classify_match(result["composite_score"]) == "AUTO_MATCH"

    def test_date_1_day_shift_auto_match(self):
        """1-day date shift within tolerance should still auto-match."""
        pr = self._make_pr("42", "2026-01-15", 10000, 1800)
        gstr = self._make_gstr("42", "2026-01-16", 10000, 1800)
        result = self.m.compute_match_score(pr, gstr)
        assert result["composite_score"] >= 0.90

    def test_amount_within_tolerance(self):
        """Amount diff within Rs 5 tolerance scores 1.0."""
        pr = self._make_pr("42", "2026-01-15", 10000, 1800.01)
        gstr = self._make_gstr("42", "2026-01-15", 10000, 1800)
        result = self.m.compute_match_score(pr, gstr)
        assert result["tax_score"] == pytest.approx(1.0)

    def test_no_match_different_invoices(self):
        """Completely different invoice numbers/dates/amounts => NO_MATCH."""
        pr = self._make_pr("999", "2026-01-15", 10000, 1800)
        gstr = self._make_gstr("111", "2026-03-20", 55000, 9900)
        result = self.m.compute_match_score(pr, gstr)
        assert self.m.classify_match(result["composite_score"]) == "NO_MATCH"

    def test_batch_match_basic(self):
        """Batch match with 2 records returns expected structure."""
        pr1 = self._make_pr("101", "2026-01-05", 20000, 3600)
        pr2 = self._make_pr("102", "2026-01-10", 15000, 2700)
        gstr1 = self._make_gstr("101", "2026-01-05", 20000, 3600)
        gstr2 = self._make_gstr("102", "2026-01-10", 15000, 2700)

        matched, unmatched_pr, unmatched_gstr = self.m.match_batch([pr1, pr2], [gstr1, gstr2])
        assert len(matched) == 2
        assert len(unmatched_pr) == 0
        assert len(unmatched_gstr) == 0

    def test_gstin_blocking(self):
        """Records from different GSTINs must never match."""
        pr = self._make_pr("42", "2026-01-15", 10000, 1800, gstin="27AABCU9603R1ZM")
        gstr = self._make_gstr("42", "2026-01-15", 10000, 1800, gstin="29AADCB2230M1ZP")
        matched, unmatched_pr, unmatched_gstr = self.m.match_batch([pr], [gstr])
        assert len(matched) == 0
        assert len(unmatched_pr) == 1


# ============================================================
# TestExceptionClassifier
# ============================================================
class TestExceptionClassifier:
    def setup_method(self):
        self.classifier = ExceptionClassifier()
        from normalize import Normalizer
        self.n = Normalizer()

    def _rec(self, inv_num, date_str, taxable, gstin, source="purchase_register"):
        n = self.n
        dt = n.parse_date(date_str)
        return {
            "invoice_number_raw": inv_num,
            "invoice_number_norm": n.normalize_invoice_number(inv_num),
            "invoice_date_raw": date_str,
            "invoice_date_norm": n.normalize_date(date_str),
            "invoice_date_parsed": dt,
            "supplier_gstin": gstin,
            "supplier_name": "Test",
            "taxable_value": float(taxable),
            "total_tax": round(float(taxable) * 0.18, 2),
            "_source": source,
        }

    def test_supplier_not_filed(self):
        """Supplier has other invoices in GSTR-2B but this one missing."""
        gstin = "27AABCU9603R1ZM"
        pr = self._rec("INV001", "2026-01-10", 10000, gstin)
        # GSTR-2B has a different invoice from same supplier
        other_gstr = self._rec("INV999", "2026-01-05", 5000, gstin, source="gstr2b")
        result = self.classifier.classify_unmatched_pr(pr, [other_gstr], [])
        assert result["exception_code"] == "SUPPLIER_NOT_FILED"
        assert result["severity"] == "HIGH"

    def test_supplier_non_filers(self):
        """Supplier has NO invoices in GSTR-2B."""
        gstin = "27AABCU9603R1ZM"
        pr = self._rec("INV001", "2026-01-10", 10000, gstin)
        other_gstr = self._rec("INV999", "2026-01-05", 5000, "29DIFFERENTGSTIN1ZP", source="gstr2b")
        result = self.classifier.classify_unmatched_pr(pr, [other_gstr], [])
        assert result["exception_code"] == "SUPPLIER_NON_FILERS"
        assert result["severity"] == "CRITICAL"

    def test_missing_in_books(self):
        """GSTR-2B record with no matching PR record."""
        gstin = "27AABCU9603R1ZM"
        gstr = self._rec("INV999", "2026-01-05", 5000, gstin, source="gstr2b")
        # No PR records with this GSTIN
        result = self.classifier.classify_unmatched_gstr(gstr, [])
        assert result["exception_code"] == "MISSING_IN_BOOKS"
        assert result["severity"] == "MEDIUM"

    def test_period_mismatch(self):
        from exceptions import ExceptionClassifier
        ec = ExceptionClassifier()
        pr = {
            'invoice_number_norm': 'DOCUMENT1001',
            'invoice_date_parsed': datetime(2026, 3, 25).date(),
            'taxable_value': 10000,
            'total_tax': 1800,
            'supplier_gstin': '27AAAPR1234B1Z1'
        }
        gstr = [{
            'invoice_number_norm': 'DOCUMENT1001A',
            'invoice_date_parsed': datetime(2026, 4, 1).date(),
            'taxable_value': 10000,
            'total_tax': 1800,
            'supplier_gstin': '27AAAPR1234B1Z1'
        }]
        result = ec.classify_unmatched_pr(pr, gstr, [])
        assert result['exception_code'] == 'PERIOD_MISMATCH'

    def test_amount_mismatch(self):
        from exceptions import ExceptionClassifier
        ec = ExceptionClassifier()
        pr = {
            'invoice_number_norm': 'DOCUMENT2001',
            'invoice_date_parsed': datetime(2026, 3, 15).date(),
            'taxable_value': 10000,
            'total_tax': 1800,
            'supplier_gstin': '27AAAPR1234B1Z1'
        }
        gstr = [{
            'invoice_number_norm': 'DOCUMENT2001A',
            'invoice_date_parsed': datetime(2026, 3, 15).date(),
            'taxable_value': 12000,
            'total_tax': 2160,
            'supplier_gstin': '27AAAPR1234B1Z1'
        }]
        result = ec.classify_unmatched_pr(pr, gstr, [])
        assert result['exception_code'] == 'AMOUNT_MISMATCH'

    def test_duplicate_supplier_reporting(self):
        from exceptions import ExceptionClassifier
        ec = ExceptionClassifier()
        gstr = {
            'invoice_number_norm': 'DUP001',
            'invoice_date_parsed': datetime(2026, 3, 10).date(),
            'taxable_value': 5000,
            'total_tax': 900,
            'supplier_gstin': '27AAADU9012D1Z3'
        }
        pr = [{
            'invoice_number_norm': 'DUP001',
            'invoice_date_parsed': datetime(2026, 3, 10).date(),
            'taxable_value': 5000,
            'total_tax': 900,
            'supplier_gstin': '27AAADU9012D1Z3'
        }]
        result = ec.classify_unmatched_gstr(gstr, pr)
        assert result['exception_code'] == 'DUPLICATE_SUPPLIER_REPORTING'


# ============================================================
# TestComplianceTracker
# ============================================================
class TestComplianceTracker:
    def setup_method(self):
        self.tracker = ComplianceTracker()

    def test_section_16_4_deadline(self):
        """Deadline for FY 2025-26 invoices is Oct 20, 2026."""
        inv_date = datetime(2026, 1, 15)
        deadline = self.tracker.get_section_16_4_deadline(inv_date)
        assert deadline == date(2026, 10, 20)

    def test_section_16_4_no_date(self):
        assert self.tracker.get_section_16_4_deadline(None) is None

    def test_drc01c_triggered_above_105pct(self):
        """DRC-01C risk triggers when claimed > eligible * 1.05."""
        gstr3b = {"total_itc_claimed": 110000.0}
        eligible = 100000.0
        result = self.tracker.compute_drc01c_risk(gstr3b, eligible, threshold_pct=1.05)
        assert result["risk"] is True
        assert result["risk_level"] == "HIGH"
        assert result["excess_amount"] == pytest.approx(10000.0)

    def test_drc01c_safe_at_105pct(self):
        """No DRC-01C risk when claimed is exactly 105% of eligible."""
        gstr3b = {"total_itc_claimed": 105000.0}
        eligible = 100000.0
        result = self.tracker.compute_drc01c_risk(gstr3b, eligible, threshold_pct=1.05)
        assert result["risk"] is False

    def test_drc01c_safe_below_threshold(self):
        gstr3b = {"total_itc_claimed": 98000.0}
        eligible = 100000.0
        result = self.tracker.compute_drc01c_risk(gstr3b, eligible, threshold_pct=1.05)
        assert result["risk"] is False

    def test_rule_37a_triggered(self):
        """Rule 37A triggers for FY 2025-26 invoice when supplier hasn't filed."""
        inv_date = datetime(2026, 1, 15)
        result = self.tracker.check_rule_37a_trigger(inv_date, supplier_gstr3b_filed=False)
        assert result["triggered"] is True
        assert result["reversal_required"] is True
        assert "2026-11-30" in result["reversal_deadline"]

    def test_rule_37a_not_triggered_when_filed(self):
        inv_date = datetime(2026, 1, 15)
        result = self.tracker.check_rule_37a_trigger(inv_date, supplier_gstr3b_filed=True)
        assert result["triggered"] is False

    def test_deadline_status_expired(self):
        assert self.tracker.get_deadline_status(-1) == "EXPIRED"

    def test_deadline_status_critical(self):
        assert self.tracker.get_deadline_status(5) == "CRITICAL"

    def test_deadline_status_urgent(self):
        assert self.tracker.get_deadline_status(20) == "URGENT"

    def test_deadline_status_ok(self):
        assert self.tracker.get_deadline_status(60) == "OK"


# ============================================================
# TestEndToEnd
# ============================================================
class TestEndToEnd:
    def test_full_run(self, tmp_path):
        """Run full agent pipeline and verify key metrics."""
        import subprocess
        import json

        data_dir = str(Path(__file__).parent.parent / "data")

        # Import agent directly
        from agent import GSTReconciliationAgent
        output_dir = str(tmp_path / "output")
        agent = GSTReconciliationAgent(data_dir, output_dir)
        result = agent.run()

        summary = result["summary"]
        assert summary["match_rate_pct"] >= 70.0, (
            f"Match rate too low: {summary['match_rate_pct']:.1f}%"
        )
        assert len(result["exceptions"]) > 0, "Expected at least one exception"
        assert "drc01c_risk" in result, "DRC-01C risk key missing"
        assert result["drc01c_risk"]["risk"] is True, "Expected DRC-01C risk to be triggered"
        assert summary["total_pr_records"] >= 65, (
            f"Expected 65+ PR records, got {summary['total_pr_records']}"
        )

    def test_all_exception_codes_present(self):
        from agent import GSTReconciliationAgent
        agent = GSTReconciliationAgent('./data', './output')
        result = agent.run()
        codes = result['summary']['exception_breakdown'].keys()
        required_codes = {
            'WRONG_GSTIN', 'PERIOD_MISMATCH', 'AMOUNT_MISMATCH',
            'SUPPLIER_NOT_FILED', 'SUPPLIER_NON_FILERS',
            'DUPLICATE_SUPPLIER_REPORTING', 'MISSING_IN_BOOKS'
        }
        missing = required_codes - set(codes)
        assert not missing, f"Missing exception codes: {missing}"
    
    def test_itc_at_risk_excludes_gstr2b(self):
        from agent import GSTReconciliationAgent
        agent = GSTReconciliationAgent('./data', './output')
        result = agent.run()
        gstr_exceptions = [e for e in result['exceptions'] if e['record_type'] == 'GSTR2B']
        assert len(gstr_exceptions) > 0, "Need some GSTR2B exceptions to validate"
        itc_sum = sum(e['record'].get('total_tax', 0) for e in gstr_exceptions)
        assert itc_sum > 0
        assert result['summary']['itc_at_risk'] < itc_sum + result['summary']['eligible_itc']
