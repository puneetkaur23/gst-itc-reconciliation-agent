from typing import Dict, List, Tuple

from rapidfuzz import fuzz


class InvoiceMatcher:
    """Fuzzy match Purchase Register records to GSTR-2B records."""

    def __init__(self):
        # Dimension weights
        self.number_weight = 0.40
        self.date_weight = 0.30
        self.taxable_weight = 0.20
        self.tax_weight = 0.10

        # Thresholds
        self.auto_match_threshold = 0.90
        self.suggested_threshold = 0.70

        # Tolerances
        self.date_tolerance_days = 3
        self.amount_tolerance_pct = 0.01   # 1%
        self.amount_tolerance_abs = 5.0    # Rs 5

    def _score_invoice_number(self, pr_num: str, gstr_num: str) -> float:
        """Score invoice number similarity."""
        if pr_num == gstr_num:
            return 1.0
        return fuzz.ratio(pr_num, gstr_num) / 100.0

    def _score_date(self, pr_date, gstr_date) -> float:
        """Score date closeness with linear decay within tolerance."""
        if pr_date is None or gstr_date is None:
            return 0.0
        if pr_date == gstr_date:
            return 1.0
        diff_days = abs((pr_date - gstr_date).days)
        if diff_days <= self.date_tolerance_days:
            return 1.0 - (diff_days / (self.date_tolerance_days + 1))
        return 0.0

    def _score_amount(self, pr_amt: float, gstr_amt: float) -> float:
        """Score amount closeness with tolerance handling."""
        if pr_amt == 0.0 and gstr_amt == 0.0:
            return 1.0
        if pr_amt == 0.0 or gstr_amt == 0.0:
            return 0.0
        abs_diff = abs(pr_amt - gstr_amt)
        pct_diff = abs_diff / max(abs(pr_amt), abs(gstr_amt))
        if abs_diff <= self.amount_tolerance_abs or pct_diff <= self.amount_tolerance_pct:
            return 1.0
        if pct_diff <= 0.10:
            return 1.0 - (pct_diff / 0.10)
        return 0.0

    def compute_match_score(self, pr: Dict, gstr: Dict) -> Dict:
        """Compute multi-dimensional match score between a PR and GSTR-2B record."""
        number_score = self._score_invoice_number(
            pr["invoice_number_norm"], gstr["invoice_number_norm"]
        )
        date_score = self._score_date(
            pr.get("invoice_date_parsed"), gstr.get("invoice_date_parsed")
        )
        taxable_score = self._score_amount(
            pr.get("taxable_value", 0.0), gstr.get("taxable_value", 0.0)
        )
        tax_score = self._score_amount(
            pr.get("total_tax", 0.0), gstr.get("total_tax", 0.0)
        )

        composite = (
            self.number_weight * number_score
            + self.date_weight * date_score
            + self.taxable_weight * taxable_score
            + self.tax_weight * tax_score
        )

        return {
            "composite_score": composite,
            "number_score": number_score,
            "date_score": date_score,
            "taxable_score": taxable_score,
            "tax_score": tax_score,
            "weights": {
                "number": self.number_weight,
                "date": self.date_weight,
                "taxable": self.taxable_weight,
                "tax": self.tax_weight,
            },
        }

    def classify_match(self, score: float) -> str:
        """Classify a composite score into a match category."""
        if score >= self.auto_match_threshold:
            return "AUTO_MATCH"
        if score >= self.suggested_threshold:
            return "SUGGESTED_MATCH"
        return "NO_MATCH"

    def match_batch(
        self, pr_records: List[Dict], gstr_records: List[Dict]
    ) -> Tuple[List, List, List]:
        """
        Block by GSTIN, fuzzy match PR to GSTR-2B.
        Returns (matched_pairs, unmatched_pr, unmatched_gstr).
        """
        # Build GSTIN index for O(1) candidate lookup
        gstr_index: Dict[str, List[Dict]] = {}
        for rec in gstr_records:
            gstin = rec["supplier_gstin"]
            gstr_index.setdefault(gstin, []).append(rec)
            rec["_consumed"] = False  # reset consumed flag

        matched_pairs: List[Dict] = []
        unmatched_pr: List[Dict] = []

        for pr in pr_records:
            gstin = pr["supplier_gstin"]
            candidates = [r for r in gstr_index.get(gstin, []) if not r["_consumed"]]

            if not candidates:
                unmatched_pr.append(pr)
                continue

            best_score = -1.0
            best_match = None
            best_details = None

            for gstr in candidates:
                score_dict = self.compute_match_score(pr, gstr)
                if score_dict["composite_score"] > best_score:
                    best_score = score_dict["composite_score"]
                    best_match = gstr
                    best_details = score_dict

            match_class = self.classify_match(best_score)

            if match_class in ("AUTO_MATCH", "SUGGESTED_MATCH"):
                best_match["_consumed"] = True
                matched_pairs.append({
                    "pr_record": pr,
                    "gstr_record": best_match,
                    "match_class": match_class,
                    "match_score": best_score,
                    "match_details": best_details,
                })
            else:
                unmatched_pr.append(pr)

        # Unconsumed GSTR-2B records
        unmatched_gstr = [r for r in gstr_records if not r["_consumed"]]

        return matched_pairs, unmatched_pr, unmatched_gstr
