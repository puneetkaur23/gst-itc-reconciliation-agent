import json
import os
from collections import defaultdict
from datetime import datetime
from typing import Dict, List

from compliance import ComplianceTracker
from exceptions import ExceptionClassifier
from ingest import DataIngestor
from matcher import InvoiceMatcher
from report import ReportGenerator


class GSTReconciliationAgent:
    """Main orchestrator for GST ITC reconciliation."""

    def __init__(self, data_dir: str, output_dir: str = "./output"):
        self.data_dir = data_dir
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

        # Components
        self.ingestor = DataIngestor(data_dir)
        self.matcher = InvoiceMatcher()
        self.classifier = ExceptionClassifier()
        self.compliance = ComplianceTracker()
        self.report_gen = ReportGenerator()

        # State
        self.pr_records: List[Dict] = []
        self.gstr_records: List[Dict] = []
        self.gstr3b_summary: Dict = {}
        self.matched_pairs: List[Dict] = []
        self.unmatched_pr: List[Dict] = []
        self.unmatched_gstr: List[Dict] = []
        self.exceptions: List[Dict] = []
        self.result: Dict = {}

    @property
    def gstr2b_records(self):
        return self.gstr_records

    @gstr2b_records.setter
    def gstr2b_records(self, val):
        self.gstr_records = val

    @property
    def reporter(self):
        return self.report_gen

    @reporter.setter
    def reporter(self, val):
        self.report_gen = val

    def run(self) -> Dict:
        """Execute the full reconciliation pipeline."""
        print("=" * 60)
        print("GST ITC RECONCILIATION AGENT")
        print("=" * 60)
        print()

        # Step 1: Ingest
        print("[1/5] Ingesting data sources...")
        self.pr_records = self.ingestor.load_purchase_register()
        self.gstr_records = self.ingestor.load_gstr2b()
        self.gstr3b_summary = self.ingestor.load_gstr3b_summary()
        print(f"  Purchase Register: {len(self.pr_records)} records")
        print(f"  GSTR-2B: {len(self.gstr_records)} records")
        print(f"  GSTR-3B Summary: period {self.gstr3b_summary.get('return_period', 'N/A')}")
        print()

        # Step 2: Match
        print("[2/5] Running fuzzy matching engine...")
        self.matched_pairs, self.unmatched_pr, self.unmatched_gstr = self.matcher.match_batch(
            self.pr_records, self.gstr_records
        )
        auto_count = sum(1 for p in self.matched_pairs if p["match_class"] == "AUTO_MATCH")
        suggested_count = sum(1 for p in self.matched_pairs if p["match_class"] == "SUGGESTED_MATCH")
        print(f"  AUTO_MATCHED:       {auto_count}")
        print(f"  SUGGESTED_MATCH:    {suggested_count}")
        print(f"  UNMATCHED (PR):     {len(self.unmatched_pr)}")
        print(f"  UNMATCHED (GSTR-2B): {len(self.unmatched_gstr)}")
        print()

        # Step 3: Classify exceptions
        print("[3/5] Classifying exceptions...")
        raw_exceptions = self.classifier.classify_all(
            self.unmatched_pr, self.unmatched_gstr,
            self.pr_records, self.gstr_records, self.matched_pairs
        )
        code_counts = defaultdict(int)
        for exc in raw_exceptions:
            code_counts[exc["exception_code"]] += 1
        for code, count in sorted(code_counts.items()):
            print(f"  {code}: {count}")
        print()

        # Step 4: Compliance
        print("[4/5] Tracking compliance deadlines...")
        self.exceptions = self.compliance.annotate_exceptions(raw_exceptions)
        eligible_itc = sum(
            p["pr_record"].get("total_tax", 0.0) for p in self.matched_pairs
        )
        drc01c = self.compliance.compute_drc01c_risk(self.gstr3b_summary, eligible_itc)
        if drc01c.get("risk"):
            print(
                f"  [WARN]  DRC-01C RISK: GSTR-3B claimed ITC exceeds eligible ITC "
                f"by {drc01c['excess_percentage']:.1f}%"
            )
        else:
            print("  [OK] No DRC-01C risk detected.")
        print()

        # Step 5: Compile
        print("[5/5] Compiling reconciliation report...")
        self.result = self._compile_result(eligible_itc, drc01c)
        self._print_summary()
        return self.result

    def _compile_result(self, eligible_itc, drc01c):
        from datetime import datetime
        pr_exceptions = [e for e in self.exceptions if e['record_type'] == 'PURCHASE_REGISTER']
        gstr_exceptions = [e for e in self.exceptions if e['record_type'] == 'GSTR2B']
        
        total_pr = len(self.pr_records)
        auto_match_count = len([m for m in self.matched_pairs if m['match_class'] == 'AUTO_MATCH'])
        suggested_match_count = len([m for m in self.matched_pairs if m['match_class'] == 'SUGGESTED_MATCH'])
        
        match_rate_pct = ((auto_match_count + suggested_match_count) / total_pr * 100) if total_pr else 0.0
        auto_match_rate = (auto_match_count / total_pr * 100) if total_pr else 0.0
        
        itc_at_risk = sum(e['record'].get('total_tax', 0) for e in pr_exceptions)
        
        exception_counts = {}
        for e in self.exceptions:
            code = e['exception_code']
            exception_counts[code] = exception_counts.get(code, 0) + 1
        
        return {
            'run_timestamp': datetime.now().isoformat(),
            'summary': {
                'total_pr_records': total_pr,
                'total_gstr_records': len(self.gstr_records),
                'auto_matched': auto_match_count,
                'suggested_match': suggested_match_count,
                'unmatched_pr': len(self.unmatched_pr),
                'unmatched_gstr': len(self.unmatched_gstr),
                'match_rate_pct': round(match_rate_pct, 1),
                'auto_match_rate_pct': round(auto_match_rate, 1),
                'eligible_itc': round(eligible_itc, 2),
                'itc_at_risk': round(itc_at_risk, 2),
                'drc01c_risk': drc01c,
                'exception_breakdown': exception_counts
            },
            'matched_pairs': self.matched_pairs,
            'exceptions': self.exceptions,
            'gstr3b_summary': self.gstr3b_summary,
            'drc01c_risk': drc01c
        }

    def _print_summary(self):
        s = self.result['summary']
        print("\n" + "="*60)
        print("RECONCILIATION SUMMARY")
        print("="*60)
        print(f"Purchase Register Records:         {s['total_pr_records']}")
        print(f"GSTR-2B Records:                   {s['total_gstr_records']}")
        print("-" * 40)
        sm_pct = round(s['suggested_match']/s['total_pr_records']*100,1) if s['total_pr_records'] else 0
        um_pct = round(s['unmatched_pr']/s['total_pr_records']*100,1) if s['total_pr_records'] else 0
        print(f"AUTO MATCHED:                      {s['auto_matched']} ({s['auto_match_rate_pct']}%)")
        print(f"SUGGESTED MATCH (Review):          {s['suggested_match']} ({sm_pct}%)")
        print(f"UNMATCHED PR (Exceptions):         {s['unmatched_pr']} ({um_pct}%)")
        print(f"UNMATCHED GSTR-2B:               {s['unmatched_gstr']}")
        print("-" * 40)
        print(f"Eligible ITC (Matched):      Rs {s['eligible_itc']:>12,.2f}")
        print(f"ITC at Risk (PR only):       Rs {s['itc_at_risk']:>12,.2f}")
        if s['drc01c_risk'].get('risk'):
            print(f"DRC-01C Risk:                YES [{s['drc01c_risk'].get('risk_level', 'HIGH')}]")
        else:
            print(f"DRC-01C Risk:                NO")
        print("="*60)
        
        if s['exception_breakdown']:
            print("\nException Breakdown:")
            for code, count in sorted(s['exception_breakdown'].items()):
                print(f"  {code}: {count}")

    def save_reports(self):
        import os
        os.makedirs(self.output_dir, exist_ok=True)
        
        # JSON result
        json_path = os.path.join(self.output_dir, "reconciliation_result.json")
        import json
        with open(json_path, 'w') as f:
            json.dump(self.result, f, indent=2, default=str)
        print(f"  [SAVED] reconciliation_result.json")
        
        # CSV exceptions
        csv_path = os.path.join(self.output_dir, "exception_list.csv")
        csv_content = self.report_gen.generate_exception_csv(self.exceptions)
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            f.write(csv_content)
        print(f"  [SAVED] exception_list.csv")
        
        # Excel exceptions
        xlsx_path = os.path.join(self.output_dir, "exception_list.xlsx")
        self.report_gen.generate_exception_xlsx(self.exceptions, xlsx_path)
        print(f"  [SAVED] exception_list.xlsx")
        
        # Markdown report
        md_path = os.path.join(self.output_dir, "reconciliation_report.md")
        md_content = self.report_gen.generate_summary_markdown(self.result)
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(md_content)
        print(f"  [SAVED] reconciliation_report.md")
        
        # Audit trail
        audit_path = os.path.join(self.output_dir, "audit_trail.json")
        audit_data = self.report_gen.generate_audit_trail(self.matched_pairs)
        with open(audit_path, 'w') as f:
            json.dump(audit_data, f, indent=2, default=str)
        print(f"  [SAVED] audit_trail.json")
