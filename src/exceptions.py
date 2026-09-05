from rapidfuzz import fuzz
from typing import List, Dict, Optional
from datetime import datetime, date, timedelta

class ExceptionClassifier:
    def __init__(self, date_tolerance_days=3, amount_tolerance_pct=0.01, amount_tolerance_abs=5.0):
        self.date_tolerance_days = date_tolerance_days
        self.amount_tolerance_pct = amount_tolerance_pct
        self.amount_tolerance_abs = amount_tolerance_abs
    
    def _to_date(self, d):
        if isinstance(d, datetime):
            return d.date()
        if isinstance(d, date):
            return d
        return None
    
    def _days_diff(self, d1, d2):
        d1 = self._to_date(d1)
        d2 = self._to_date(d2)
        if not d1 or not d2:
            return None
        return abs((d1 - d2).days)
    
    def _amount_beyond_tolerance(self, pr_amt, gstr_amt):
        if pr_amt == 0 and gstr_amt == 0:
            return False
        if pr_amt == 0 or gstr_amt == 0:
            return True
        abs_diff = abs(pr_amt - gstr_amt)
        pct_diff = abs_diff / max(abs(pr_amt), abs(gstr_amt))
        return not (abs_diff <= self.amount_tolerance_abs or pct_diff <= self.amount_tolerance_pct)
    
    def _taxable_beyond_5pct(self, pr_val, gstr_val):
        if pr_val == 0 and gstr_val == 0:
            return False
        if pr_val == 0 or gstr_val == 0:
            return True
        abs_diff = abs(pr_val - gstr_val)
        pct_diff = abs_diff / max(abs(pr_val), abs(gstr_val))
        return pct_diff > 0.05
    
    def _fuzz_ratio(self, s1, s2):
        if not s1 or not s2:
            return 0.0
        return fuzz.ratio(str(s1).upper(), str(s2).upper()) / 100.0
    
    def classify_unmatched_pr(self, pr, all_gstr_records, matched_pairs):
        pr_inv = pr.get('invoice_number_norm', '')
        pr_date = pr.get('invoice_date_parsed') or pr.get('invoice_date_norm')
        pr_taxable = pr.get('taxable_value', 0)
        pr_tax = pr.get('total_tax', 0)
        pr_gstin = pr.get('supplier_gstin', '')
        
        # CHECK 1: WRONG_GSTIN (search ALL GSTR records)
        for gstr in all_gstr_records:
            if gstr.get('supplier_gstin') == pr_gstin:
                continue
            gstr_inv = gstr.get('invoice_number_norm', '')
            gstr_date = gstr.get('invoice_date_parsed') or gstr.get('invoice_date_norm')
            gstr_taxable = gstr.get('taxable_value', 0)
            
            if (self._fuzz_ratio(pr_inv, gstr_inv) >= 0.85 and 
                self._days_diff(pr_date, gstr_date) is not None and
                self._days_diff(pr_date, gstr_date) <= 3 and
                not self._taxable_beyond_5pct(pr_taxable, gstr_taxable)):
                return {
                    'exception_code': 'WRONG_GSTIN',
                    'exception_reason': f"Invoice found under different GSTIN {gstr.get('supplier_gstin')}. PR GSTIN: {pr_gstin}",
                    'action_required': "Reject via IMS, ask supplier to amend via GSTR-1A",
                    'severity': 'MEDIUM'
                }
        
        # CHECK 2: PERIOD_MISMATCH (same GSTIN, fuzz>=0.90, taxable within 5%, date diff 4-35 days)
        same_gstin_gstr = [g for g in all_gstr_records if g.get('supplier_gstin') == pr_gstin]
        for gstr in same_gstin_gstr:
            gstr_inv = gstr.get('invoice_number_norm', '')
            gstr_date = gstr.get('invoice_date_parsed') or gstr.get('invoice_date_norm')
            gstr_taxable = gstr.get('taxable_value', 0)
            
            if (self._fuzz_ratio(pr_inv, gstr_inv) >= 0.90 and 
                not self._taxable_beyond_5pct(pr_taxable, gstr_taxable)):
                diff_days = self._days_diff(pr_date, gstr_date)
                if diff_days is not None and 4 <= diff_days <= 35:
                    return {
                        'exception_code': 'PERIOD_MISMATCH',
                        'exception_reason': f"Invoice found in different period. PR date: {pr_date}, GSTR date: {gstr_date} (diff: {diff_days} days)",
                        'action_required': "Accept in correct period if within Section 16(4) limit",
                        'severity': 'LOW'
                    }
        
        # CHECK 3: AMOUNT_MISMATCH (same GSTIN, fuzz>=0.90, dates within 3 days, amounts beyond tolerance)
        for gstr in same_gstin_gstr:
            gstr_inv = gstr.get('invoice_number_norm', '')
            gstr_date = gstr.get('invoice_date_parsed') or gstr.get('invoice_date_norm')
            gstr_taxable = gstr.get('taxable_value', 0)
            gstr_tax = gstr.get('total_tax', 0)
            
            if (self._fuzz_ratio(pr_inv, gstr_inv) >= 0.90 and 
                self._days_diff(pr_date, gstr_date) is not None and
                self._days_diff(pr_date, gstr_date) <= 3):
                if (self._amount_beyond_tolerance(pr_taxable, gstr_taxable) or 
                    self._amount_beyond_tolerance(pr_tax, gstr_tax)):
                    diff_taxable = abs(pr_taxable - gstr_taxable)
                    diff_tax = abs(pr_tax - gstr_tax)
                    return {
                        'exception_code': 'AMOUNT_MISMATCH',
                        'exception_reason': f"Amount mismatch. Taxable diff: Rs {diff_taxable:.2f}, Tax diff: Rs {diff_tax:.2f}",
                        'action_required': "Request supplier correction or book adjustment",
                        'severity': 'MEDIUM'
                    }
        
        # CHECK 4: SUPPLIER_NOT_FILED vs SUPPLIER_NON_FILERS
        supplier_invoices_in_gstr = [g for g in all_gstr_records if g.get('supplier_gstin') == pr_gstin]
        if supplier_invoices_in_gstr:
            return {
                'exception_code': 'SUPPLIER_NOT_FILED',
                'exception_reason': "Supplier has other invoices in GSTR-2B but not this one",
                'action_required': "Contact supplier to file/amend GSTR-1A",
                'severity': 'HIGH'
            }
        else:
            return {
                'exception_code': 'SUPPLIER_NON_FILERS',
                'exception_reason': "Supplier has zero invoices in GSTR-2B",
                'action_required': "Urgent follow-up with supplier to file GSTR-1",
                'severity': 'CRITICAL'
            }
    
    def classify_unmatched_gstr(self, gstr, all_pr_records):
        gstr_inv = gstr.get('invoice_number_norm', '')
        gstr_date = gstr.get('invoice_date_parsed') or gstr.get('invoice_date_norm')
        gstr_gstin = gstr.get('supplier_gstin', '')
        gstr_taxable = gstr.get('taxable_value', 0)
        
        same_gstin_pr = [p for p in all_pr_records if p.get('supplier_gstin') == gstr_gstin]
        for pr in same_gstin_pr:
            pr_inv = pr.get('invoice_number_norm', '')
            pr_date = pr.get('invoice_date_parsed') or pr.get('invoice_date_norm')
            pr_taxable = pr.get('taxable_value', 0)
            
            fuzz_score = self._fuzz_ratio(gstr_inv, pr_inv)
            date_match = self._days_diff(gstr_date, pr_date) is not None and self._days_diff(gstr_date, pr_date) <= 3
            amt_match = not self._taxable_beyond_5pct(gstr_taxable, pr_taxable)
            
            if fuzz_score >= 0.85 and (date_match or amt_match):
                return {
                    'exception_code': 'DUPLICATE_SUPPLIER_REPORTING',
                    'exception_reason': "Supplier reported invoice not found in books or duplicate entry",
                    'action_required': "Reject duplicate via IMS",
                    'severity': 'LOW'
                }
        
        return {
            'exception_code': 'MISSING_IN_BOOKS',
            'exception_reason': "Invoice in GSTR-2B but missing from Purchase Register",
            'action_required': "Verify purchase and record in books or reject via IMS",
            'severity': 'MEDIUM'
        }
    
    def classify_all(self, unmatched_pr, unmatched_gstr, all_pr, all_gstr, matched_pairs):
        results = []
        for pr in unmatched_pr:
            classification = self.classify_unmatched_pr(pr, all_gstr, matched_pairs)
            results.append({
                'record_type': 'PURCHASE_REGISTER',
                'record': pr,
                **classification
            })
        
        for gstr in unmatched_gstr:
            classification = self.classify_unmatched_gstr(gstr, all_pr)
            results.append({
                'record_type': 'GSTR2B',
                'record': gstr,
                **classification
            })
        
        return results
