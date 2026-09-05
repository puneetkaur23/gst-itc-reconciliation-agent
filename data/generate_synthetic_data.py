"""
Generate synthetic GST records with deliberate messiness for testing.
All 7 exception types are produced cleanly:
  1. CLEAN_MATCH
  2. FORMATTING_DIFF
  3. AMOUNT_ROUNDING
  4. PERIOD_MISMATCH (5 records: DOCUMENT1001-1005 vs DOCUMENT1001A-1005A)
  5. AMOUNT_MISMATCH (4 records: DOCUMENT2001-2004 vs DOCUMENT2001A-2004A)
  6. SUPPLIER_NOT_FILED (supplier has other invoices in GSTR-2B)
  7. SUPPLIER_NON_FILERS (3 records: NF-001 to NF-003, zero in GSTR-2B)
  8. DUPLICATE_SUPPLIER_REPORTING (1 PR + 2 GSTR with DUP-001)
  9. WRONG_GSTIN (4 records: WG-001 to WG-004)
 10. CREDIT_NOTE_UNMATCHED
 11. MISSING_IN_BOOKS (3 records: MIB-001 to MIB-003)
"""

import argparse
import csv
import json
import os
import random
import sys
from datetime import date, timedelta

# Ensure normalize is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from normalize import Normalizer

random.seed(42)

NORM = Normalizer()

parser = argparse.ArgumentParser(description="Generate synthetic GST data.")
parser.add_argument("--outdir", default=None, help="Directory to output data files.")
args, _ = parser.parse_known_args()

OUTPUT_DIR = os.path.abspath(args.outdir) if args.outdir else os.path.dirname(os.path.abspath(__file__))
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ---- Constants -------------------------------------------------------
FY_START = date(2026, 1, 1)   # Use Q1 2026 as our "period"
PERIOD = "032026"
COMPANY_GSTIN = "27AAPFU0939F1ZV"

HSN_CODES = ["8471", "7308", "8443", "3004", "9403", "2710", "8528"]
SUPPLIER_NAMES = [
    "Alpha Tech Solutions Pvt Ltd",
    "Beta Manufacturing Co",
    "Gamma Exports Ltd",
    "Delta Traders",
    "Epsilon Supplies Pvt Ltd",
    "Zeta Components Ltd",
    "Eta Industries",
    "Theta Services Pvt Ltd",
    "Iota Distributors",
    "Kappa Enterprises Pvt Ltd",
    "Lambda Logistics",
    "Mu Hardware Pvt Ltd",
]

# 15 unique supplier GSTINs
SUPPLIER_GSTINS = [
    "27AABCU9603R1ZM", "29AADCB2230M1ZP", "33AAFCR1234R1ZQ",
    "07AAGCM5678N1ZR", "19AAHCK9012P1ZS", "24AAJCL3456Q1ZT",
    "09AAKCM7890R1ZU", "06AALMN2345S1ZV", "18AAMNP6789T1ZW",
    "32AANCP1234U1ZX", "22AAPCQ5678V1ZY", "36AAQCR9012W1ZZ",
    "08AARCV3456X1ZA", "17AASCW7890Y1ZB", "15AATCX2345Z1ZC",
]

# Specific suppliers for targeted exceptions
PRIME_CORP_GSTIN = "27AAAPR1234B1Z1"       # PERIOD_MISMATCH & AMOUNT_MISMATCH
PRIME_CORP_NAME = "Prime Corp"

NON_FILER_GSTIN = "27AAANF5678C1Z2"        # SUPPLIER_NON_FILERS
NON_FILER_NAME = "Non Filer Ltd"

DUPLEX_GSTIN = "27AAADU9012D1Z3"           # DUPLICATE_SUPPLIER_REPORTING
DUPLEX_NAME = "Duplex Corp"

WRONG_GSTIN_CORP = "27AAAWG1111E1Z4"       # WRONG_GSTIN PR supplier
WRONG_GSTIN_NAME = "Wrong GSTIN Corp"

MISSING_IN_BOOKS_GSTIN = "27AAAMI2222F1Z5" # MISSING_IN_BOOKS GSTR supplier
MISSING_IN_BOOKS_NAME = "Missing In Books Ltd"


def format_date(d: date, fmt: str) -> str:
    mapping = {
        "iso": d.strftime("%Y-%m-%d"),
        "dmy": d.strftime("%d-%m-%Y"),
        "dmy_slash": d.strftime("%d/%m/%Y"),
        "dmy_short": d.strftime("%d-%m-%y"),
    }
    return mapping.get(fmt, d.strftime("%Y-%m-%d"))


def make_invoice_number(num: int, variant: str = "clean") -> str:
    variants = {
        "clean": str(num).zfill(4),
        "prefix": f"INV/{str(num).zfill(4)}",
        "prefix_year": f"INV/2026/{str(num).zfill(4)}",
        "leading_zeros": str(num).zfill(6),
        "short": str(num),
        "suffix": f"{str(num).zfill(4)}/FY26",
        "bill": f"BILL_{str(num).zfill(4)}",
    }
    return variants.get(variant, str(num).zfill(4))


def compute_taxes(taxable: float, rounding_variant: str = "clean"):
    """Return (igst, cgst, sgst, total_tax, invoice_total) based on 18% GST."""
    igst = round(taxable * 0.18, 2)
    cgst = round(taxable * 0.09, 2)
    sgst = round(taxable * 0.09, 2)

    if rounding_variant == "round_up":
        igst += 0.01; cgst += 0.01; sgst += 0.01
    elif rounding_variant == "round_down":
        igst -= 0.01; cgst -= 0.01; sgst -= 0.01
    elif rounding_variant == "round_diff_1":
        cgst += 0.50; sgst += 0.50
    elif rounding_variant == "round_diff_2":
        igst += 1.00

    total_tax = round(igst + cgst + sgst, 2)
    invoice_total = round(taxable + total_tax, 2)
    return igst, cgst, sgst, total_tax, invoice_total


def gstr2b_item(txval, iamt, camt, samt):
    return {
        "num": 1,
        "itm_det": {"txval": txval, "iamt": iamt, "camt": camt, "samt": samt}
    }


# ---- Build records ---------------------------------------------------
pr_rows = []          # purchase_register.csv rows
gstr2b_by_supplier = {}   # keyed by ctin
ground_truth = {}

inv_counter = 100     # Invoice number base


def next_inv():
    global inv_counter
    inv_counter += 1
    return inv_counter


def add_supplier(gstin, custom_name=None):
    if gstin not in gstr2b_by_supplier:
        if custom_name:
            name = custom_name
        elif gstin in SUPPLIER_GSTINS:
            name = SUPPLIER_NAMES[SUPPLIER_GSTINS.index(gstin) % len(SUPPLIER_NAMES)]
        else:
            name = "Vendor " + gstin[:6]
        gstr2b_by_supplier[gstin] = {
            "ctin": gstin,
            "trdnm": name,
            "inv": [],
            "cdn": [],
        }


def random_taxable():
    return round(random.uniform(5000, 80000), 2)


# -----------------------------------------------------------------------
# 1. CLEAN_MATCH — 35 records
# -----------------------------------------------------------------------
for i in range(35):
    num = next_inv()
    gstin = SUPPLIER_GSTINS[i % len(SUPPLIER_GSTINS)]
    add_supplier(gstin)
    base_date = FY_START + timedelta(days=random.randint(0, 60))
    txval = random_taxable()
    igst, cgst, sgst, total_tax, inv_total = compute_taxes(txval, "clean")
    inv_num = make_invoice_number(num, "clean")
    dt_str = format_date(base_date, "iso")

    pr_rows.append({
        "invoice_number": inv_num,
        "invoice_date": dt_str,
        "supplier_gstin": gstin,
        "supplier_name": SUPPLIER_NAMES[i % len(SUPPLIER_NAMES)],
        "taxable_value": txval, "igst_amount": igst, "cgst_amount": cgst,
        "sgst_amount": sgst, "total_tax": total_tax, "invoice_total": inv_total,
        "hsn_code": random.choice(HSN_CODES), "document_type": "INV",
        "exception_type": "CLEAN_MATCH", "period": PERIOD,
    })
    gstr2b_by_supplier[gstin]["inv"].append({
        "inum": inv_num,
        "idt": dt_str,
        "val": inv_total,
        "itms": [gstr2b_item(txval, igst, cgst, sgst)],
    })
    ground_truth[inv_num] = "CLEAN_MATCH"


# -----------------------------------------------------------------------
# 2. FORMATTING_DIFF — 13 records
# -----------------------------------------------------------------------
fmt_variants = ["prefix", "prefix_year", "leading_zeros", "short", "suffix", "bill"]
date_formats = ["dmy", "dmy_slash", "dmy_short"]
for i in range(13):
    num = next_inv()
    gstin = SUPPLIER_GSTINS[(i + 2) % len(SUPPLIER_GSTINS)]
    add_supplier(gstin)
    base_date = FY_START + timedelta(days=random.randint(0, 60))
    txval = random_taxable()
    igst, cgst, sgst, total_tax, inv_total = compute_taxes(txval, "clean")

    pr_variant = fmt_variants[i % len(fmt_variants)]
    pr_inv = make_invoice_number(num, pr_variant)
    pr_date_fmt = date_formats[i % len(date_formats)]
    pr_dt = format_date(base_date, pr_date_fmt)

    gstr_inv = NORM.normalize_invoice_number(pr_inv)
    gstr_dt = format_date(base_date, "dmy")

    pr_rows.append({
        "invoice_number": pr_inv, "invoice_date": pr_dt,
        "supplier_gstin": gstin,
        "supplier_name": SUPPLIER_NAMES[(i + 2) % len(SUPPLIER_NAMES)],
        "taxable_value": txval, "igst_amount": igst, "cgst_amount": cgst,
        "sgst_amount": sgst, "total_tax": total_tax, "invoice_total": inv_total,
        "hsn_code": random.choice(HSN_CODES), "document_type": "INV",
        "exception_type": "FORMATTING_DIFF", "period": PERIOD,
    })
    gstr2b_by_supplier[gstin]["inv"].append({
        "inum": gstr_inv,
        "idt": gstr_dt,
        "val": inv_total,
        "itms": [gstr2b_item(txval, igst, cgst, sgst)],
    })
    ground_truth[pr_inv] = "FORMATTING_DIFF"


# -----------------------------------------------------------------------
# 3. AMOUNT_ROUNDING — 10 records
# -----------------------------------------------------------------------
rounding_variants = ["round_up", "round_down", "round_diff_1", "round_diff_2"]
for i in range(10):
    num = next_inv()
    gstin = SUPPLIER_GSTINS[(i + 4) % len(SUPPLIER_GSTINS)]
    add_supplier(gstin)
    base_date = FY_START + timedelta(days=random.randint(0, 60))
    txval = random_taxable()

    pr_round = rounding_variants[i % len(rounding_variants)]
    igst_pr, cgst_pr, sgst_pr, total_pr, inv_total_pr = compute_taxes(txval, pr_round)
    igst_gstr, cgst_gstr, sgst_gstr, total_gstr, inv_total_gstr = compute_taxes(txval, "clean")

    inv_num = make_invoice_number(num, "clean")
    dt_str = format_date(FY_START + timedelta(days=random.randint(0, 60)), "iso")

    pr_rows.append({
        "invoice_number": inv_num, "invoice_date": dt_str,
        "supplier_gstin": gstin,
        "supplier_name": SUPPLIER_NAMES[(i + 4) % len(SUPPLIER_NAMES)],
        "taxable_value": txval, "igst_amount": igst_pr, "cgst_amount": cgst_pr,
        "sgst_amount": sgst_pr, "total_tax": total_pr, "invoice_total": inv_total_pr,
        "hsn_code": random.choice(HSN_CODES), "document_type": "INV",
        "exception_type": "AMOUNT_ROUNDING", "period": PERIOD,
    })
    gstr2b_by_supplier[gstin]["inv"].append({
        "inum": inv_num, "idt": dt_str,
        "val": inv_total_gstr,
        "itms": [gstr2b_item(txval, igst_gstr, cgst_gstr, sgst_gstr)],
    })
    ground_truth[inv_num] = "AMOUNT_ROUNDING"


# -----------------------------------------------------------------------
# 4. PERIOD_MISMATCH — 5 records (Issue 4 specification)
#    PR: DOCUMENT1001 to DOCUMENT1005, 2026-03-25 to 2026-03-29, 27AAAPR1234B1Z1, taxable 10000, tax 1800 (IGST)
#    GSTR: DOCUMENT1001A to DOCUMENT1005A, 2026-04-01 to 2026-04-05, same supplier, same amounts
# -----------------------------------------------------------------------
add_supplier(PRIME_CORP_GSTIN, PRIME_CORP_NAME)
for i in range(5):
    inv_num = f"DOCUMENT{1001 + i}"
    gstr_inv = f"DOCUMENT{1001 + i}A"
    pr_date = date(2026, 3, 25 + i)
    gstr_date = date(2026, 4, 1 + i)
    txval = 10000.0
    igst = 1800.0
    cgst = 0.0
    sgst = 0.0
    total_tax = 1800.0
    inv_total = 11800.0

    pr_rows.append({
        "invoice_number": inv_num,
        "invoice_date": pr_date.strftime("%Y-%m-%d"),
        "supplier_gstin": PRIME_CORP_GSTIN,
        "supplier_name": PRIME_CORP_NAME,
        "taxable_value": txval,
        "igst_amount": igst,
        "cgst_amount": cgst,
        "sgst_amount": sgst,
        "total_tax": total_tax,
        "invoice_total": inv_total,
        "hsn_code": "8471",
        "document_type": "INV",
        "exception_type": "PERIOD_MISMATCH",
        "period": PERIOD,
    })
    gstr2b_by_supplier[PRIME_CORP_GSTIN]["inv"].append({
        "inum": gstr_inv,
        "idt": gstr_date.strftime("%Y-%m-%d"),
        "val": inv_total,
        "itms": [gstr2b_item(txval, igst, cgst, sgst)],
    })
    ground_truth[inv_num] = "PERIOD_MISMATCH"


# -----------------------------------------------------------------------
# 5. AMOUNT_MISMATCH — 4 records (Issue 4 specification)
#    PR: DOCUMENT2001 to DOCUMENT2004, 2026-03-15 to 2026-03-18, 27AAAPR1234B1Z1, taxable 10000, tax 1800
#    GSTR: DOCUMENT2001A to DOCUMENT2004A, same dates, taxable 12000, tax 2160 (20% higher)
# -----------------------------------------------------------------------
for i in range(4):
    inv_num = f"DOCUMENT{2001 + i}"
    gstr_inv = f"DOCUMENT{2001 + i}A"
    inv_date = date(2026, 3, 15 + i)
    txval_pr = 10000.0
    igst_pr = 1800.0
    total_tax_pr = 1800.0
    inv_total_pr = 11800.0

    txval_gstr = 12000.0
    igst_gstr = 2160.0
    total_tax_gstr = 2160.0
    inv_total_gstr = 14160.0

    pr_rows.append({
        "invoice_number": inv_num,
        "invoice_date": inv_date.strftime("%Y-%m-%d"),
        "supplier_gstin": PRIME_CORP_GSTIN,
        "supplier_name": PRIME_CORP_NAME,
        "taxable_value": txval_pr,
        "igst_amount": igst_pr,
        "cgst_amount": 0.0,
        "sgst_amount": 0.0,
        "total_tax": total_tax_pr,
        "invoice_total": inv_total_pr,
        "hsn_code": "8471",
        "document_type": "INV",
        "exception_type": "AMOUNT_MISMATCH",
        "period": PERIOD,
    })
    gstr2b_by_supplier[PRIME_CORP_GSTIN]["inv"].append({
        "inum": gstr_inv,
        "idt": inv_date.strftime("%Y-%m-%d"),
        "val": inv_total_gstr,
        "itms": [gstr2b_item(txval_gstr, igst_gstr, 0.0, 0.0)],
    })
    ground_truth[inv_num] = "AMOUNT_MISMATCH"


# -----------------------------------------------------------------------
# 6. SUPPLIER_NOT_FILED — 5 records
#    Supplier has OTHER invoices in GSTR-2B, but this specific one is missing.
# -----------------------------------------------------------------------
snf_gstin = SUPPLIER_GSTINS[0]
add_supplier(snf_gstin)
seed_num = next_inv()
seed_txval = random_taxable()
seed_igst, seed_cgst, seed_sgst, seed_total, seed_inv_total = compute_taxes(seed_txval, "clean")
gstr2b_by_supplier[snf_gstin]["inv"].append({
    "inum": make_invoice_number(seed_num, "clean"),
    "idt": format_date(FY_START, "iso"),
    "val": seed_inv_total,
    "itms": [gstr2b_item(seed_txval, seed_igst, seed_cgst, seed_sgst)],
})

for i in range(5):
    num = next_inv()
    base_date = FY_START + timedelta(days=random.randint(1, 70))
    txval = random_taxable()
    igst, cgst, sgst, total_tax, inv_total = compute_taxes(txval, "clean")
    inv_num = make_invoice_number(num, "clean")
    dt_str = format_date(base_date, "iso")

    pr_rows.append({
        "invoice_number": inv_num, "invoice_date": dt_str,
        "supplier_gstin": snf_gstin,
        "supplier_name": SUPPLIER_NAMES[0],
        "taxable_value": txval, "igst_amount": igst, "cgst_amount": cgst,
        "sgst_amount": sgst, "total_tax": total_tax, "invoice_total": inv_total,
        "hsn_code": random.choice(HSN_CODES), "document_type": "INV",
        "exception_type": "SUPPLIER_NOT_FILED", "period": PERIOD,
    })
    ground_truth[inv_num] = "SUPPLIER_NOT_FILED"


# -----------------------------------------------------------------------
# 7. SUPPLIER_NON_FILERS — 3 records (Issue 4 specification)
#    PR: NF-001, NF-002, NF-003, supplier 27AAANF5678C1Z2 (Non Filer Ltd), March 2026
#    GSTR: ZERO records for this supplier GSTIN
# -----------------------------------------------------------------------
for i in range(3):
    inv_num = f"NF-00{i+1}"
    base_date = date(2026, 3, 10 + i * 5)
    txval = 15000.0 + i * 5000.0
    igst, cgst, sgst, total_tax, inv_total = compute_taxes(txval, "clean")

    pr_rows.append({
        "invoice_number": inv_num,
        "invoice_date": base_date.strftime("%Y-%m-%d"),
        "supplier_gstin": NON_FILER_GSTIN,
        "supplier_name": NON_FILER_NAME,
        "taxable_value": txval,
        "igst_amount": igst,
        "cgst_amount": cgst,
        "sgst_amount": sgst,
        "total_tax": total_tax,
        "invoice_total": inv_total,
        "hsn_code": "8471",
        "document_type": "INV",
        "exception_type": "SUPPLIER_NON_FILERS",
        "period": PERIOD,
    })
    ground_truth[inv_num] = "SUPPLIER_NON_FILERS"


# -----------------------------------------------------------------------
# 8. DUPLICATE_SUPPLIER_REPORTING — 1 PR + 2 GSTR (Issue 4 specification)
#    PR: DUP-001, supplier 27AAADU9012D1Z3, date 2026-03-10, taxable 5000, tax 900
#    GSTR: TWO records both with DUP-001, same supplier, same date, same amounts
# -----------------------------------------------------------------------
add_supplier(DUPLEX_GSTIN, DUPLEX_NAME)
dup_inv = "DUP-001"
dup_dt = "2026-03-10"
dup_txval = 5000.0
dup_igst = 900.0
dup_cgst = 0.0
dup_sgst = 0.0
dup_total_tax = 900.0
dup_inv_total = 5900.0

pr_rows.append({
    "invoice_number": dup_inv,
    "invoice_date": dup_dt,
    "supplier_gstin": DUPLEX_GSTIN,
    "supplier_name": DUPLEX_NAME,
    "taxable_value": dup_txval,
    "igst_amount": dup_igst,
    "cgst_amount": dup_cgst,
    "sgst_amount": dup_sgst,
    "total_tax": dup_total_tax,
    "invoice_total": dup_inv_total,
    "hsn_code": "8471",
    "document_type": "INV",
    "exception_type": "DUPLICATE_SUPPLIER_REPORTING",
    "period": PERIOD,
})

# Two identical records in GSTR-2B
for _ in range(2):
    gstr2b_by_supplier[DUPLEX_GSTIN]["inv"].append({
        "inum": dup_inv,
        "idt": dup_dt,
        "val": dup_inv_total,
        "itms": [gstr2b_item(dup_txval, dup_igst, dup_cgst, dup_sgst)],
    })
ground_truth[dup_inv] = "DUPLICATE_SUPPLIER_REPORTING"


# -----------------------------------------------------------------------
# 9. WRONG_GSTIN — 4 records (Issue 4 specification)
#    PR: supplier 27AAAWG1111E1Z4 (Wrong GSTIN Corp), WG-001 to WG-004
#    GSTR: Put these same invoice numbers under DIFFERENT supplier GSTIN
# -----------------------------------------------------------------------
target_other_gstin = SUPPLIER_GSTINS[0]
add_supplier(target_other_gstin)
for i in range(4):
    inv_num = f"WG-00{i+1}"
    base_date = date(2026, 3, 5 + i * 4)
    txval = 20000.0 + i * 3000.0
    igst, cgst, sgst, total_tax, inv_total = compute_taxes(txval, "clean")
    dt_str = base_date.strftime("%Y-%m-%d")

    pr_rows.append({
        "invoice_number": inv_num,
        "invoice_date": dt_str,
        "supplier_gstin": WRONG_GSTIN_CORP,
        "supplier_name": WRONG_GSTIN_NAME,
        "taxable_value": txval,
        "igst_amount": igst,
        "cgst_amount": cgst,
        "sgst_amount": sgst,
        "total_tax": total_tax,
        "invoice_total": inv_total,
        "hsn_code": "8471",
        "document_type": "INV",
        "exception_type": "WRONG_GSTIN",
        "period": PERIOD,
    })
    gstr2b_by_supplier[target_other_gstin]["inv"].append({
        "inum": inv_num,
        "idt": dt_str,
        "val": inv_total,
        "itms": [gstr2b_item(txval, igst, cgst, sgst)],
    })
    ground_truth[inv_num] = "WRONG_GSTIN"


# -----------------------------------------------------------------------
# 10. CREDIT_NOTE_UNMATCHED — 3 records
#     CRN in PR, missing from GSTR-2B.
# -----------------------------------------------------------------------
cn_gstin = SUPPLIER_GSTINS[3]
add_supplier(cn_gstin)
for i in range(3):
    num = next_inv()
    base_date = FY_START + timedelta(days=random.randint(5, 55))
    txval = round(random.uniform(2000, 20000), 2)
    igst, cgst, sgst, total_tax, inv_total = compute_taxes(txval, "clean")
    inv_num = f"CN-{num}"

    pr_rows.append({
        "invoice_number": inv_num, "invoice_date": format_date(base_date, "iso"),
        "supplier_gstin": cn_gstin,
        "supplier_name": SUPPLIER_NAMES[3],
        "taxable_value": txval, "igst_amount": igst, "cgst_amount": cgst,
        "sgst_amount": sgst, "total_tax": total_tax, "invoice_total": inv_total,
        "hsn_code": random.choice(HSN_CODES), "document_type": "CRN",
        "exception_type": "CREDIT_NOTE_UNMATCHED", "period": PERIOD,
    })
    ground_truth[inv_num] = "CREDIT_NOTE_UNMATCHED"


# -----------------------------------------------------------------------
# 11. MISSING_IN_BOOKS — 3 records (Issue 4 specification)
#     GSTR only: supplier 27AAAMI2222F1Z5, MIB-001 to MIB-003, not in PR
# -----------------------------------------------------------------------
add_supplier(MISSING_IN_BOOKS_GSTIN, MISSING_IN_BOOKS_NAME)
for i in range(3):
    inv_num = f"MIB-00{i+1}"
    base_date = date(2026, 3, 12 + i * 5)
    txval = 18000.0 + i * 4000.0
    igst, cgst, sgst, total_tax, inv_total = compute_taxes(txval, "clean")
    dt_str = base_date.strftime("%Y-%m-%d")

    gstr2b_by_supplier[MISSING_IN_BOOKS_GSTIN]["inv"].append({
        "inum": inv_num,
        "idt": dt_str,
        "val": inv_total,
        "itms": [gstr2b_item(txval, igst, cgst, sgst)],
    })
    ground_truth[inv_num] = "MISSING_IN_BOOKS"


# -----------------------------------------------------------------------
# Assemble GSTR-2B JSON
# -----------------------------------------------------------------------
gstr2b_data = {
    "gstin": COMPANY_GSTIN,
    "fp": PERIOD,
    "b2b": list(gstr2b_by_supplier.values()),
}


# -----------------------------------------------------------------------
# GSTR-3B Summary
# Claimed ITC is set higher than eligible ITC to trigger DRC-01C risk (>105%)
# -----------------------------------------------------------------------
matched_eligible_types = (
    "CLEAN_MATCH", "FORMATTING_DIFF", "AMOUNT_ROUNDING", "DUPLICATE_SUPPLIER_REPORTING"
)
eligible_matched_rows = [r for r in pr_rows if r["exception_type"] in matched_eligible_types]
total_eligible_matched = sum(r["total_tax"] for r in eligible_matched_rows)

# Claimed = 115% of matched eligible ITC so DRC-01C risk triggers reliably
claimed_total = round(total_eligible_matched * 1.15, 2)
igst_eligible = sum(r["igst_amount"] for r in eligible_matched_rows)
cgst_eligible = sum(r["cgst_amount"] for r in eligible_matched_rows)
sgst_eligible = sum(r["sgst_amount"] for r in eligible_matched_rows)

gstr3b = {
    "return_period": PERIOD,
    "itc_igst_claimed": round(igst_eligible * 1.15, 2),
    "itc_cgst_claimed": round(cgst_eligible * 1.15, 2),
    "itc_sgst_claimed": round(sgst_eligible * 1.15, 2),
    "total_itc_claimed": claimed_total,
    "eligible_igst_2b": round(igst_eligible, 2),
    "eligible_cgst_2b": round(cgst_eligible, 2),
    "eligible_sgst_2b": round(sgst_eligible, 2),
    "total_eligible_2b": round(total_eligible_matched, 2),
}


# -----------------------------------------------------------------------
# Write files
# -----------------------------------------------------------------------
pr_path = os.path.join(OUTPUT_DIR, "purchase_register.csv")
fieldnames = [
    "invoice_number", "invoice_date", "supplier_gstin", "supplier_name",
    "taxable_value", "igst_amount", "cgst_amount", "sgst_amount", "total_tax",
    "invoice_total", "hsn_code", "document_type", "exception_type", "period",
]
with open(pr_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(pr_rows)
print(f"  Written: purchase_register.csv ({len(pr_rows)} rows)")

gstr2b_path = os.path.join(OUTPUT_DIR, "gstr2b.json")
with open(gstr2b_path, "w", encoding="utf-8") as f:
    json.dump(gstr2b_data, f, indent=2)
total_gstr2b = sum(
    len(s["inv"]) + len(s["cdn"]) for s in gstr2b_data["b2b"]
)
print(f"  Written: gstr2b.json ({total_gstr2b} records)")

gstr3b_path = os.path.join(OUTPUT_DIR, "gstr3b_summary.csv")
with open(gstr3b_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=list(gstr3b.keys()))
    writer.writeheader()
    writer.writerow(gstr3b)
print("  Written: gstr3b_summary.csv")

gt_path = os.path.join(OUTPUT_DIR, "ground_truth.json")
with open(gt_path, "w", encoding="utf-8") as f:
    json.dump(ground_truth, f, indent=2)
print(f"  Written: ground_truth.json ({len(ground_truth)} entries)")

print("\nSynthetic data generation complete.")
print(f"Total PR records: {len(pr_rows)}")
