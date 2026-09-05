from datetime import date
from typing import Dict, List, Optional


class ComplianceTracker:
    """Track GST compliance deadlines and detect DRC-01C risk."""

    def __init__(self):
        # FY 2025-26 deadlines
        self.gstr3b_due_sept = date(2026, 10, 20)   # GSTR-3B for Sept 2026 (cumulative FY deadline)
        self.annual_return_due = date(2026, 12, 31)  # GSTR-9 annual return due
        self.rule37a_cutoff = date(2026, 9, 30)       # Rule 37A reversal trigger cutoff
        self.rule37a_reversal_deadline = date(2026, 11, 30)  # Deadline to reverse ITC

        # FY boundaries for Rule 37A applicability
        self._fy_start = date(2025, 4, 1)
        self._fy_end = date(2026, 3, 31)

    def get_section_16_4_deadline(self, invoice_date) -> Optional[date]:
        """Return the hard ITC claim deadline for FY 2025-26."""
        if invoice_date is None:
            return None
        return min(self.gstr3b_due_sept, self.annual_return_due)

    def get_days_to_deadline(self, deadline: Optional[date]) -> Optional[int]:
        """Days remaining until deadline (negative = expired)."""
        if deadline is None:
            return None
        return (deadline - date.today()).days

    def get_deadline_status(self, days_remaining: Optional[int]) -> str:
        """Classify urgency of deadline."""
        if days_remaining is None:
            return "UNKNOWN"
        if days_remaining < 0:
            return "EXPIRED"
        if days_remaining <= 7:
            return "CRITICAL"
        if days_remaining <= 30:
            return "URGENT"
        return "OK"

    def check_rule_37a_trigger(
        self, invoice_date, supplier_gstr3b_filed: bool = False
    ) -> Dict:
        """
        Determine if Rule 37A ITC reversal is required.
        Triggered when: invoice is in FY 2025-26 AND supplier has not filed GSTR-3B.
        """
        if invoice_date is None:
            return {
                "triggered": False,
                "reason": "Invoice date unknown — cannot assess Rule 37A applicability.",
            }

        invoice_d = invoice_date.date() if hasattr(invoice_date, "date") else invoice_date
        in_fy = self._fy_start <= invoice_d <= self._fy_end

        if in_fy and not supplier_gstr3b_filed:
            return {
                "triggered": True,
                "reversal_required": True,
                "reversal_deadline": self.rule37a_reversal_deadline.isoformat(),
                "reason": (
                    "Invoice is in FY 2025-26 and supplier has not filed GSTR-3B. "
                    "Per Rule 37A, ITC must be reversed if supplier does not file by "
                    f"{self.rule37a_reversal_deadline.isoformat()}. "
                    "Re-credit allowed when supplier eventually files."
                ),
            }
        return {
            "triggered": False,
            "reason": "Rule 37A not triggered — invoice outside FY or supplier has filed.",
        }

    def compute_drc01c_risk(
        self, gstr3b_summary: Dict, eligible_itc: float, threshold_pct: float = 1.05
    ) -> Dict:
        """
        Assess DRC-01C risk: GSTR-3B claimed ITC vs. eligible ITC from matched invoices.
        Risk triggers when claimed > eligible * threshold_pct.
        """
        claimed = gstr3b_summary.get("total_itc_claimed", 0.0)
        threshold = eligible_itc * threshold_pct

        if claimed > threshold:
            excess_amount = claimed - eligible_itc
            excess_pct = (excess_amount / eligible_itc * 100) if eligible_itc > 0 else 0.0
            return {
                "risk": True,
                "risk_level": "HIGH",
                "claimed_itc": claimed,
                "eligible_itc": eligible_itc,
                "excess_amount": round(excess_amount, 2),
                "excess_percentage": round(excess_pct, 2),
                "threshold_pct": threshold_pct * 100,
                "reason": (
                    f"GSTR-3B ITC claimed (Rs {claimed:,.2f}) exceeds eligible ITC "
                    f"(Rs {eligible_itc:,.2f}) by Rs {excess_amount:,.2f} ({excess_pct:.2f}%). "
                    f"Threshold is {threshold_pct * 100:.0f}% of eligible ITC."
                ),
                "action": (
                    "Respond to DRC-01C within 7 days with reconciliation explanation "
                    "or pay differential tax via DRC-03."
                ),
            }

        return {
            "risk": False,
            "risk_level": "NONE",
            "claimed_itc": claimed,
            "eligible_itc": eligible_itc,
            "reason": (
                f"GSTR-3B ITC claimed (Rs {claimed:,.2f}) is within {threshold_pct * 100:.0f}% "
                f"of eligible ITC (Rs {eligible_itc:,.2f}). No DRC-01C risk."
            ),
        }

    def annotate_exceptions(self, exceptions: List[Dict]) -> List[Dict]:
        """Add compliance deadline and Rule 37A annotations to each exception."""
        annotated = []
        for exc in exceptions:
            rec = exc.get("record", {})
            invoice_date_parsed = rec.get("invoice_date_parsed")

            deadline = self.get_section_16_4_deadline(invoice_date_parsed)
            days_remaining = self.get_days_to_deadline(deadline)
            status = self.get_deadline_status(days_remaining)

            exc["section_16_4_deadline"] = deadline.isoformat() if deadline else None
            exc["days_to_deadline"] = days_remaining
            exc["deadline_status"] = status

            # Rule 37A only for PR records (buyer's book entries)
            if exc.get("record_type") == "PURCHASE_REGISTER":
                rule37a = self.check_rule_37a_trigger(
                    invoice_date_parsed, supplier_gstr3b_filed=False
                )
                exc["rule_37a"] = rule37a
            else:
                exc["rule_37a"] = {"triggered": False, "reason": "N/A for GSTR-2B records."}

            annotated.append(exc)

        return annotated
