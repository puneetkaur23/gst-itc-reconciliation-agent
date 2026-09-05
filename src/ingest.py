import csv
import json
import os
from typing import Dict, List, Optional

from normalize import Normalizer


class DataIngestor:
    """Load and normalize all three GST data sources."""

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.normalizer = Normalizer()

    def _path(self, filename: str) -> str:
        return os.path.join(self.data_dir, filename)

    def load_purchase_register(self) -> List[Dict]:
        records = []
        filepath = self._path("purchase_register.csv")
        with open(filepath, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                n = self.normalizer
                inv_raw = row.get("invoice_number", "").strip()
                date_raw = row.get("invoice_date", "").strip()
                rec = {
                    "invoice_number_raw": inv_raw,
                    "invoice_number_norm": n.normalize_invoice_number(inv_raw),
                    "invoice_date_raw": date_raw,
                    "invoice_date_norm": n.normalize_date(date_raw),
                    "invoice_date_parsed": n.parse_date(date_raw),
                    "supplier_gstin": n.normalize_gstin(row.get("supplier_gstin", "")),
                    "supplier_name": row.get("supplier_name", "").strip(),
                    "taxable_value": n.normalize_amount(row.get("taxable_value", 0)),
                    "igst_amount": n.normalize_amount(row.get("igst_amount", 0)),
                    "cgst_amount": n.normalize_amount(row.get("cgst_amount", 0)),
                    "sgst_amount": n.normalize_amount(row.get("sgst_amount", 0)),
                    "total_tax": n.normalize_amount(row.get("total_tax", 0)),
                    "invoice_total": n.normalize_amount(row.get("invoice_total", 0)),
                    "hsn_code": row.get("hsn_code", "").strip(),
                    "document_type": row.get("document_type", "INV").strip().upper(),
                    "period": row.get("period", "").strip(),
                    "_source": "purchase_register",
                    "_exception_type_ground_truth": row.get("exception_type", "").strip(),
                }
                records.append(rec)
        return records

    def load_gstr2b(self) -> List[Dict]:
        records = []
        filepath = self._path("gstr2b.json")
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)

        n = self.normalizer

        for b2b_entry in data.get("b2b", []):
            ctin = n.normalize_gstin(b2b_entry.get("ctin", ""))
            supplier_name = b2b_entry.get("trdnm", "")

            # Regular invoices
            for inv in b2b_entry.get("inv", []):
                inv_raw = str(inv.get("inum", "")).strip()
                date_raw = str(inv.get("idt", "")).strip()
                # Aggregate tax from items
                txval = 0.0
                iamt = 0.0
                camt = 0.0
                samt = 0.0
                for itm in inv.get("itms", []):
                    det = itm.get("itm_det", {})
                    txval += n.normalize_amount(det.get("txval", 0))
                    iamt += n.normalize_amount(det.get("iamt", 0))
                    camt += n.normalize_amount(det.get("camt", 0))
                    samt += n.normalize_amount(det.get("samt", 0))

                rec = {
                    "invoice_number_raw": inv_raw,
                    "invoice_number_norm": n.normalize_invoice_number(inv_raw),
                    "invoice_date_raw": date_raw,
                    "invoice_date_norm": n.normalize_date(date_raw),
                    "invoice_date_parsed": n.parse_date(date_raw),
                    "supplier_gstin": ctin,
                    "supplier_name": supplier_name,
                    "taxable_value": txval,
                    "igst_amount": iamt,
                    "cgst_amount": camt,
                    "sgst_amount": samt,
                    "total_tax": iamt + camt + samt,
                    "invoice_total": n.normalize_amount(inv.get("val", 0)),
                    "hsn_code": "",
                    "document_type": "INV",
                    "period": data.get("fp", ""),
                    "_source": "gstr2b",
                    "_consumed": False,
                }
                records.append(rec)

            # Credit/Debit notes
            for cdn in b2b_entry.get("cdn", []):
                nt_raw = str(cdn.get("nt_num", "")).strip()
                date_raw = str(cdn.get("nt_dt", "")).strip()
                ntty = cdn.get("ntty", "C")
                doc_type = "CRN" if ntty == "C" else "DBN"

                txval = 0.0
                iamt = 0.0
                camt = 0.0
                samt = 0.0
                for itm in cdn.get("itms", []):
                    det = itm.get("itm_det", {})
                    txval += n.normalize_amount(det.get("txval", 0))
                    iamt += n.normalize_amount(det.get("iamt", 0))
                    camt += n.normalize_amount(det.get("camt", 0))
                    samt += n.normalize_amount(det.get("samt", 0))

                rec = {
                    "invoice_number_raw": nt_raw,
                    "invoice_number_norm": n.normalize_invoice_number(nt_raw),
                    "invoice_date_raw": date_raw,
                    "invoice_date_norm": n.normalize_date(date_raw),
                    "invoice_date_parsed": n.parse_date(date_raw),
                    "supplier_gstin": ctin,
                    "supplier_name": supplier_name,
                    "taxable_value": txval,
                    "igst_amount": iamt,
                    "cgst_amount": camt,
                    "sgst_amount": samt,
                    "total_tax": iamt + camt + samt,
                    "invoice_total": n.normalize_amount(cdn.get("val", 0)),
                    "hsn_code": "",
                    "document_type": doc_type,
                    "period": data.get("fp", ""),
                    "_source": "gstr2b",
                    "_consumed": False,
                }
                records.append(rec)

        return records

    def load_gstr3b_summary(self) -> Dict:
        filepath = self._path("gstr3b_summary.csv")
        with open(filepath, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                n = self.normalizer
                return {
                    "return_period": row.get("return_period", "").strip(),
                    "itc_igst_claimed": n.normalize_amount(row.get("itc_igst_claimed", 0)),
                    "itc_cgst_claimed": n.normalize_amount(row.get("itc_cgst_claimed", 0)),
                    "itc_sgst_claimed": n.normalize_amount(row.get("itc_sgst_claimed", 0)),
                    "total_itc_claimed": n.normalize_amount(row.get("total_itc_claimed", 0)),
                    "eligible_igst_2b": n.normalize_amount(row.get("eligible_igst_2b", 0)),
                    "eligible_cgst_2b": n.normalize_amount(row.get("eligible_cgst_2b", 0)),
                    "eligible_sgst_2b": n.normalize_amount(row.get("eligible_sgst_2b", 0)),
                    "total_eligible_2b": n.normalize_amount(row.get("total_eligible_2b", 0)),
                }
        return {}
