import streamlit as st
import sys
from pathlib import Path
import pandas as pd
import json
import plotly.express as px
import os
import tempfile
import subprocess
import shutil

# Add src to path so we can import the backend
sys.path.insert(0, str(Path(__file__).parent / "src"))

from agent import GSTReconciliationAgent

st.set_page_config(
    page_title="GST ITC Reconciliation Agent",
    page_icon="chart_with_upwards_trend",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    .main-header { font-size: 2.2rem; font-weight: bold; color: #7eb6ff; margin-bottom: 0; }
    .risk-box { padding: 15px; border-radius: 8px; margin: 10px 0; }
    .risk-box h4 { color: #ffffff !important; margin-top: 0; }
    .risk-box p { color: #d7dce3 !important; }
    .risk-high { background-color: #3d1f24; border-left: 5px solid #ff4b4b; }
    .risk-safe { background-color: #1e3326; border-left: 5px solid #00c853; }
    .feature-card { background-color: #262730; border: 1px solid #464b58;
                     border-radius: 8px; padding: 15px; }
    .feature-card p { color: #d7dce3 !important; }
</style>
""", unsafe_allow_html=True)

st.markdown('<p class="main-header">GST ITC Reconciliation Agent</p>', unsafe_allow_html=True)
st.markdown("Reconcile Purchase Register against GSTR-2B using fuzzy matching, 7 exception codes, and compliance tracking (Section 16(4) | Rule 37A | DRC-01C).")

# Session state
if 'result' not in st.session_state:
    st.session_state.result = None
    st.session_state.output_dir = None

SEV_COLORS = {
    'CRITICAL': 'background-color: #c00000; color: white; font-weight: bold',
    'HIGH': 'background-color: #ff6600; color: white; font-weight: bold',
    'MEDIUM': 'background-color: #ffc000; color: black; font-weight: bold',
    'LOW': 'background-color: #00b050; color: white; font-weight: bold',
}

def run_agent(data_dir, output_dir):
    """Run the backend agent and save all report files."""
    agent = GSTReconciliationAgent(str(data_dir), str(output_dir))
    result = agent.run()
    agent.save_reports()
    return result

def highlight_severity(val):
    return SEV_COLORS.get(val, '')

# ---------------- Sidebar ----------------
with st.sidebar:
    st.header("Data Sources")
    st.markdown("**Option A: Upload your own files**")
    pr_file = st.file_uploader("Purchase Register (CSV)", type=['csv'])
    gstr2b_file = st.file_uploader("GSTR-2B (JSON)", type=['json'])
    gstr3b_file = st.file_uploader("GSTR-3B Summary (CSV)", type=['csv'])

    st.markdown("**Option B: Synthetic demo data**")
    use_synthetic = st.button("Generate Demo Data and Run", use_container_width=True)

    st.markdown("---")
    run_btn = st.button("Run Reconciliation", type="primary", use_container_width=True)
    st.caption("Built for GST Buildathon 2026")

# ---------------- Execution logic ----------------
if use_synthetic:
    workdir = Path(tempfile.mkdtemp(prefix="gst_recon_"))
    data_dir = workdir / "data"
    out_dir = workdir / "output"
    data_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    with st.spinner("Generating synthetic data..."):
        subprocess.run(
            [sys.executable, "data/generate_synthetic_data.py", "--outdir", str(data_dir)],
            check=True, cwd=str(Path(__file__).parent)
        )
    with st.spinner("Running reconciliation engine (83 records)..."):
        try:
            st.session_state.result = run_agent(data_dir, out_dir)
            st.session_state.output_dir = out_dir
            st.sidebar.success("Demo data reconciled successfully")
        except Exception as e:
            st.sidebar.error(f"Error: {e}")
            st.exception(e)

elif run_btn:
    if pr_file and gstr2b_file and gstr3b_file:
        workdir = Path(tempfile.mkdtemp(prefix="gst_recon_"))
        data_dir = workdir / "data"
        out_dir = workdir / "output"
        data_dir.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "purchase_register.csv").write_bytes(pr_file.getvalue())
        (data_dir / "gstr2b.json").write_bytes(gstr2b_file.getvalue())
        (data_dir / "gstr3b_summary.csv").write_bytes(gstr3b_file.getvalue())
        with st.spinner("Running reconciliation engine..."):
            try:
                st.session_state.result = run_agent(data_dir, out_dir)
                st.session_state.output_dir = out_dir
                st.sidebar.success("Reconciliation complete")
            except Exception as e:
                st.sidebar.error(f"Error: {e}")
                st.exception(e)
    else:
        st.sidebar.warning("Please upload all 3 files first.")

# ---------------- Results display ----------------
if st.session_state.result is not None:
    result = st.session_state.result
    s = result['summary']
    drc = s.get('drc01c_risk', {})

    st.subheader("Reconciliation Summary")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("PR Records", s.get('total_pr_records', 0))
    c2.metric("GSTR-2B Records", s.get('total_gstr_records', 0))
    c3.metric("Auto Matched", f"{s.get('auto_matched', 0)} ({s.get('auto_match_rate_pct', 0)}%)")
    c4.metric("ITC at Risk", f"Rs {s.get('itc_at_risk', 0):,.2f}")
    if drc.get('risk'):
        c5.metric("DRC-01C", "RISK", delta=f"+{drc.get('excess_percentage', 0):.1f}%")
    else:
        c5.metric("DRC-01C", "SAFE")

    col_chart1, col_chart2 = st.columns(2)

    with col_chart1:
        st.markdown("##### Match Distribution")
        match_df = pd.DataFrame({
            'Category': ['Auto Match', 'Suggested Match', 'Unmatched PR'],
            'Count': [s.get('auto_matched', 0), s.get('suggested_match', 0), s.get('unmatched_pr', 0)]
        })
        fig = px.pie(match_df, names='Category', values='Count', hole=0.4,
                     color='Category',
                     color_discrete_map={'Auto Match': '#00b050',
                                         'Suggested Match': '#ffc000',
                                         'Unmatched PR': '#c00000'})
        fig.update_layout(showlegend=True, margin=dict(t=10, b=10, l=10, r=10))
        st.plotly_chart(fig, use_container_width=True)

    with col_chart2:
        st.markdown("##### Exception Code Breakdown")
        breakdown = s.get('exception_breakdown', {})
        if breakdown:
            exc_df = pd.DataFrame(
                [{'Exception Code': k, 'Count': v}
                 for k, v in sorted(breakdown.items(), key=lambda x: -x[1])]
            )
            fig2 = px.bar(exc_df, x='Exception Code', y='Count', color='Count',
                          color_continuous_scale='RdYlGn_r', text='Count')
            fig2.update_layout(showlegend=False, margin=dict(t=10, b=10, l=10, r=10))
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("No exceptions generated.")

    # Compliance alert
    st.subheader("Compliance Status")
    if drc.get('risk'):
        claimed_val = drc.get('claimed_itc', drc.get('claimed', 0))
        eligible_val = drc.get('eligible_itc', drc.get('eligible', 0))
        st.markdown(f"""
        <div class="risk-box risk-high">
            <h4>DRC-01C Risk Detected</h4>
            <p><b>Claimed ITC:</b> Rs {claimed_val:,.2f} &nbsp;|&nbsp;
               <b>Eligible ITC:</b> Rs {eligible_val:,.2f} &nbsp;|&nbsp;
               <b>Excess:</b> Rs {drc.get('excess_amount', 0):,.2f} ({drc.get('excess_percentage', 0):.1f}%)</p>
            <p><b>Action Required:</b> Respond to DRC-01C within 7 days with a reconciliation
            explanation, or reverse the differential ITC via DRC-03.</p>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown("""
        <div class="risk-box risk-safe">
            <h4>No DRC-01C Risk</h4>
            <p>ITC claims are within safe limits (<=105% of eligible ITC).</p>
        </div>
        """, unsafe_allow_html=True)

    # Exception table
    st.subheader("Exception Details")
    exceptions = result.get('exceptions', [])
    if exceptions:
        table_data = []
        for exc in exceptions:
            rec = exc.get('record', {})
            action = exc.get('action_required', '')
            table_data.append({
                'Type': exc.get('record_type', ''),
                'Invoice': rec.get('invoice_number_raw', 'N/A'),
                'Supplier': (rec.get('supplier_name', '') or '')[:25],
                'GSTIN': rec.get('supplier_gstin', ''),
                'Date': str(rec.get('invoice_date_raw', '')),
                'Tax (Rs)': rec.get('total_tax', 0),
                'Exception': exc.get('exception_code', ''),
                'Severity': exc.get('severity', ''),
                'Action': action[:50] + ('...' if len(action) > 50 else ''),
                'S16(4) Deadline': str(exc.get('section_16_4_deadline', 'N/A')),
                'Deadline Status': exc.get('deadline_status', 'N/A'),
                'R37A': 'YES' if (exc.get('rule_37a') or {}).get('triggered') else 'NO',
            })
        df = pd.DataFrame(table_data)
        styler = df.style
        if hasattr(styler, 'map'):
            styled = styler.map(highlight_severity, subset=['Severity'])
        else:
            styled = styler.applymap(highlight_severity, subset=['Severity'])
        st.dataframe(styled, use_container_width=True, height=450)
    else:
        st.success("No exceptions found. All invoices reconciled.")

    # Downloads
    st.subheader("Download Reports")
    out_dir = st.session_state.output_dir
    if out_dir:
        files = [
            ("Full Result (JSON)", "reconciliation_result.json"),
            ("Exception Report (Excel)", "exception_list.xlsx"),
            ("Audit Trail (JSON)", "audit_trail.json"),
            ("Summary Report (MD)", "reconciliation_report.md"),
        ]
        cols = st.columns(4)
        for i, (label, fname) in enumerate(files):
            fpath = Path(out_dir) / fname
            if fpath.exists():
                with open(fpath, "rb") as f:
                    cols[i].download_button(label, f.read(), fname, use_container_width=True)
            else:
                cols[i].button(label, disabled=True, use_container_width=True)

    # Audit trail preview
    with st.expander("Audit Trail Preview (First 5 Matches)"):
        pairs = result.get('matched_pairs', [])
        if pairs:
            for mp in pairs[:5]:
                pr = mp.get('pr_record', {})
                gr = mp.get('gstr_record', {})
                sc = mp.get('match_details', {})
                comp_score = mp.get('match_score', 0)
                if isinstance(comp_score, dict):
                    comp_score = comp_score.get('composite_score', 0)
                st.write(f"**{pr.get('invoice_number_raw')}** <-> **{gr.get('invoice_number_raw')}** "
                         f"| Score: {comp_score:.2f} | Class: {mp.get('match_class')}")
                st.caption(f"Number: {sc.get('number_score', 0):.2f} | Date: {sc.get('date_score', 0):.2f} | "
                           f"Taxable: {sc.get('taxable_score', 0):.2f} | Tax: {sc.get('tax_score', 0):.2f}")
        else:
            st.info("No matched pairs to display.")

else:
    # Landing page
    st.markdown("---")
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        st.markdown('<div class="feature-card"><b>Fuzzy Matching</b><br><span style="color:#d7dce3">4-dimension weighted scoring with GSTIN blocking and consumed-flag deduplication.</span></div>', unsafe_allow_html=True)
    with col_b:
        st.markdown('<div class="feature-card"><b>7 Exception Codes</b><br><span style="color:#d7dce3">WRONG_GSTIN, PERIOD_MISMATCH, AMOUNT_MISMATCH, SUPPLIER_NOT_FILED, SUPPLIER_NON_FILERS, DUPLICATE_SUPPLIER_REPORTING, MISSING_IN_BOOKS.</span></div>', unsafe_allow_html=True)
    with col_c:
        st.markdown('<div class="feature-card"><b>Compliance Tracking</b><br><span style="color:#d7dce3">Section 16(4) deadlines, Rule 37A reversal triggers, and DRC-01C risk computation.</span></div>', unsafe_allow_html=True)
    st.markdown("---")
    st.info("Use the sidebar to upload files or generate synthetic data, then click Run Reconciliation.")
