"""
RiskLens - Banking Risk Investigation Copilot
SYNTHETIC DATA ONLY - Not for production regulatory use.
All outputs are investigation signals, not proof of fraud.
"""
import streamlit as st
import pandas as pd
import json
from datetime import datetime

# â”€â”€ Snowflake session â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
from snowflake.snowpark.context import get_active_session
session = get_active_session()

RISK_VERSION = "v1.0-mvp"
DATA_LABEL = "SYNTHETIC"
LLM_MODEL = "llama3.1-70b"

# â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def run_query(sql):
    try:
        return session.sql(sql).to_pandas()
    except Exception as e:
        st.error(f"Query error: {e}")
        return pd.DataFrame()

def safe_llm(system_prompt, user_message):
    """Call Cortex LLM with proper escaping. Returns string."""
    msgs = json.dumps([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message}
    ])
    escaped = msgs.replace("'", "''")
    sql = f"SELECT SNOWFLAKE.CORTEX.COMPLETE('{LLM_MODEL}', PARSE_JSON('{escaped}')) AS R"
    try:
        row = session.sql(sql).collect()
        return row[0]["R"] if row else "[No response from model]"
    except Exception as e:
        return f"[LLM call failed: {e}]"

def validate_sql(sql_text):
    s = sql_text.strip()
    upper = s.upper()
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        return False, "Only SELECT queries allowed"
    for kw in ["INSERT","UPDATE","DELETE","DROP","CREATE","ALTER","TRUNCATE","MERGE","GRANT","REVOKE","COPY"]:
        if f" {kw} " in f" {upper} " or upper.startswith(kw + " "):
            return False, f"Forbidden keyword: {kw}"
    return True, "OK"

def log_query(question, sql_text, sources, response):
    esc_q = question.replace("'", "''")
    esc_s = (sql_text or "").replace("'", "''")
    esc_r = (response or "").replace("'", "''")
    src_json = json.dumps(sources or []).replace("'", "''")
    session.sql(f"""
        INSERT INTO RISKLENS.AUDIT.QUERY_LOG (QUESTION, GENERATED_SQL, MODEL_USED, SOURCES_CITED, RESPONSE_SUMMARY)
        SELECT '{esc_q}', '{esc_s}', '{LLM_MODEL}', PARSE_JSON('{src_json}'), '{esc_r}'
    """).collect()

def next_id(prefix, table, id_col):
    row = session.sql(f"SELECT COUNT(*) AS N FROM {table}").collect()
    n = row[0]["N"] + 1 if row else 1
    return f"{prefix}-{n:04d}"

SCHEMA_CONTEXT = """You are a risk investigation SQL assistant for the RISKLENS database (Snowflake).
Available tables (all data is synthetic/illustrative):

RISKLENS.RAW.CUSTOMERS (CUSTOMER_ID, NAME, CUSTOMER_TYPE, RISK_RATING, ONBOARDING_DATE, KYC_STATUS, COUNTRY, INDUSTRY)
RISKLENS.RAW.ACCOUNTS (ACCOUNT_ID, CUSTOMER_ID, ACCOUNT_TYPE, STATUS, OPEN_DATE, BALANCE, BRANCH, CURRENCY)
RISKLENS.RAW.TRANSACTIONS (TXN_ID, ACCOUNT_ID, TXN_DATE, TXN_TYPE, AMOUNT, CURRENCY, COUNTERPARTY, COUNTERPARTY_COUNTRY, CHANNEL, DESCRIPTION)
RISKLENS.RAW.WATCHLISTS (ENTRY_ID, ENTITY_NAME, LIST_TYPE, MATCH_TYPE, ADDED_DATE, REASON, SOURCE)
RISKLENS.RAW.POLICIES (POLICY_ID, TITLE, CATEGORY, VERSION, EFFECTIVE_DATE, JURISDICTION, CONTENT)
RISKLENS.ANALYTICS.RISK_SUMMARY (CUSTOMER_ID, CUSTOMER_NAME, ACCOUNT_ID, TOTAL_RISK_SCORE, SEVERITY, ALERT_COUNT, ALERT_TYPES, TRIGGERED_RULES, LATEST_ALERT_DATE, SCORING_VERSION)
RISKLENS.ANALYTICS.STRUCTURING_ALERTS (ACCOUNT_ID, CUSTOMER_ID, ALERT_DATE, CASH_TXN_COUNT, TOTAL_CASH_AMOUNT, AVG_TXN_AMOUNT, ALERT_TYPE, RULE_ID)
RISKLENS.ANALYTICS.VELOCITY_ALERTS (ACCOUNT_ID, CUSTOMER_ID, ALERT_DATE, TXN_COUNT_7D, TXN_AMOUNT_7D, BASELINE_WEEKLY_COUNT, VELOCITY_RATIO, ALERT_TYPE, RULE_ID)
RISKLENS.ANALYTICS.DORMANT_REACTIVATION_ALERTS (ACCOUNT_ID, CUSTOMER_ID, ALERT_DATE, DORMANCY_DAYS, TXNS_SINCE_REACTIVATION, AMOUNT_SINCE_REACTIVATION, ALERT_TYPE, RULE_ID)
RISKLENS.ANALYTICS.JURISDICTION_ALERTS (ACCOUNT_ID, CUSTOMER_ID, ALERT_DATE, TXN_ID, AMOUNT, COUNTERPARTY, COUNTERPARTY_COUNTRY, ALERT_TYPE, RULE_ID)
RISKLENS.ANALYTICS.WATCHLIST_ALERTS (CUSTOMER_ID, CUSTOMER_NAME, WATCHLIST_ENTITY, LIST_TYPE, MATCH_SCORE, ALERT_TYPE, RULE_ID)
RISKLENS.APP.CASES (CASE_ID, ALERT_SOURCE, ALERT_DETAIL, ACCOUNT_ID, CUSTOMER_ID, STATUS, PRIORITY, ASSIGNEE, CREATED_AT, UPDATED_AT, NOTES)
RISKLENS.APP.FINDINGS (FINDING_ID, CASE_ID, TITLE, NARRATIVE, EVIDENCE_REFS, RISK_INDICATORS, POLICY_REFS, STATUS, CREATED_BY, REVIEWED_BY, CREATED_AT, REVIEWED_AT, RULE_VERSION)

Rules:
- Generate ONLY a single SELECT statement.
- Only query RISKLENS.* tables listed above.
- LIMIT results to 100 rows.
- Return the SQL only, no explanation. No markdown fences."""

# â”€â”€ Page config â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
st.set_page_config(page_title="RiskLens", layout="wide")

st.sidebar.title("RiskLens")
st.sidebar.caption(f"Data: {DATA_LABEL} | Rules: {RISK_VERSION}")
st.sidebar.markdown("---")
page = st.sidebar.radio("Navigate", [
    "Dashboard",
    "Investigate",
    "Ask",
    "Cases",
    "Reports"
])
st.sidebar.markdown("---")
st.sidebar.info("All data is **synthetic**. Outputs are investigation signals, not proof of fraud. Regulatory drafts require human review.")

# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# DASHBOARD
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
if page == "Dashboard":
    st.title("Risk Investigation Dashboard")

    alerts = run_query("SELECT * FROM RISKLENS.ANALYTICS.RISK_SUMMARY ORDER BY TOTAL_RISK_SCORE DESC")
    cases = run_query("SELECT STATUS, COUNT(*) AS CNT FROM RISKLENS.APP.CASES GROUP BY STATUS")
    txn_total = run_query("SELECT COUNT(*) AS N, SUM(ABS(AMOUNT)) AS VOL FROM RISKLENS.RAW.TRANSACTIONS")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Active Alerts", len(alerts) if not alerts.empty else 0)
    high = len(alerts[alerts["SEVERITY"] == "HIGH"]) if not alerts.empty else 0
    c2.metric("High Severity", high)
    open_cases = int(cases[cases["STATUS"] == "OPEN"]["CNT"].sum()) if not cases.empty and "OPEN" in cases["STATUS"].values else 0
    c3.metric("Open Cases", open_cases)
    vol = f"${txn_total['VOL'].iloc[0]:,.0f}" if not txn_total.empty and txn_total['VOL'].iloc[0] else "$0"
    c4.metric("Transaction Volume", vol)

    st.subheader("Risk Alerts by Customer")
    if not alerts.empty:
        def color_severity(val):
            colors = {"HIGH": "background-color: #ffcccc", "MEDIUM": "background-color: #fff3cd", "LOW": "background-color: #d4edda"}
            return colors.get(val, "")
        display_cols = ["CUSTOMER_NAME", "ACCOUNT_ID", "SEVERITY", "TOTAL_RISK_SCORE", "ALERT_COUNT", "ALERT_TYPES", "TRIGGERED_RULES", "LATEST_ALERT_DATE"]
        available = [c for c in display_cols if c in alerts.columns]
        styled = alerts[available].style.applymap(color_severity, subset=["SEVERITY"] if "SEVERITY" in available else [])
        st.dataframe(styled, use_container_width=True)
    else:
        st.info("No active alerts.")

    st.subheader("Alert Distribution")
    all_alerts_detail = run_query("""
        SELECT ALERT_TYPE, COUNT(*) AS COUNT
        FROM (
            SELECT ALERT_TYPE FROM RISKLENS.ANALYTICS.STRUCTURING_ALERTS
            UNION ALL SELECT ALERT_TYPE FROM RISKLENS.ANALYTICS.VELOCITY_ALERTS
            UNION ALL SELECT ALERT_TYPE FROM RISKLENS.ANALYTICS.DORMANT_REACTIVATION_ALERTS
            UNION ALL SELECT ALERT_TYPE FROM RISKLENS.ANALYTICS.JURISDICTION_ALERTS
            UNION ALL SELECT ALERT_TYPE FROM RISKLENS.ANALYTICS.WATCHLIST_ALERTS
        ) GROUP BY ALERT_TYPE ORDER BY COUNT DESC
    """)
    if not all_alerts_detail.empty:
        st.bar_chart(all_alerts_detail.set_index("ALERT_TYPE"))

# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# INVESTIGATE
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
elif page == "Investigate":
    st.title("Account Investigation")

    customers = run_query("SELECT CUSTOMER_ID, NAME FROM RISKLENS.RAW.CUSTOMERS ORDER BY NAME")
    if customers.empty:
        st.warning("No customer data.")
        st.stop()

    cust_options = dict(zip(customers["NAME"], customers["CUSTOMER_ID"]))
    selected_name = st.selectbox("Select Customer", list(cust_options.keys()))
    cust_id = cust_options[selected_name]

    cust_detail = run_query(f"SELECT * FROM RISKLENS.RAW.CUSTOMERS WHERE CUSTOMER_ID = '{cust_id}'")
    if not cust_detail.empty:
        row = cust_detail.iloc[0]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Risk Rating", row.get("RISK_RATING", "N/A"))
        c2.metric("KYC Status", row.get("KYC_STATUS", "N/A"))
        c3.metric("Type", row.get("CUSTOMER_TYPE", "N/A"))
        c4.metric("Country", row.get("COUNTRY", "N/A"))

    # Accounts
    accounts = run_query(f"SELECT * FROM RISKLENS.RAW.ACCOUNTS WHERE CUSTOMER_ID = '{cust_id}'")
    if not accounts.empty:
        st.subheader("Accounts")
        st.dataframe(accounts[["ACCOUNT_ID","ACCOUNT_TYPE","STATUS","BALANCE","BRANCH"]], use_container_width=True)

    # Risk signals
    st.subheader("Risk Signals")
    risk = run_query(f"SELECT * FROM RISKLENS.ANALYTICS.RISK_SUMMARY WHERE CUSTOMER_ID = '{cust_id}'")
    if not risk.empty:
        for _, r in risk.iterrows():
            severity = r.get("SEVERITY", "N/A")
            icon = {"HIGH": "ðŸ”´", "MEDIUM": "ðŸŸ¡", "LOW": "ðŸŸ¢"}.get(severity, "âšª")
            acct = r.get("ACCOUNT_ID", "N/A")
            with st.expander(f"{icon} {acct} â€” Score: {r.get('TOTAL_RISK_SCORE', 0)} ({severity}) â€” Rules: {r.get('TRIGGERED_RULES', '')}"):
                st.write(f"**Alert types:** {r.get('ALERT_TYPES', '')}")
                st.write(f"**Alert count:** {r.get('ALERT_COUNT', 0)}")
                st.write(f"**Latest alert:** {r.get('LATEST_ALERT_DATE', 'N/A')}")
                st.write(f"**Scoring version:** {r.get('SCORING_VERSION', RISK_VERSION)}")

                # Show detailed alerts for this account
                acct_val = r.get("ACCOUNT_ID")
                if acct_val and str(acct_val) != "None":
                    detail_alerts = run_query(f"""
                        SELECT ALERT_TYPE, RULE_ID, ALERT_DESCRIPTION, ALERT_DATE FROM RISKLENS.ANALYTICS.STRUCTURING_ALERTS WHERE ACCOUNT_ID = '{acct_val}'
                        UNION ALL SELECT ALERT_TYPE, RULE_ID, ALERT_DESCRIPTION, ALERT_DATE FROM RISKLENS.ANALYTICS.VELOCITY_ALERTS WHERE ACCOUNT_ID = '{acct_val}'
                        UNION ALL SELECT ALERT_TYPE, RULE_ID, ALERT_DESCRIPTION, ALERT_DATE FROM RISKLENS.ANALYTICS.DORMANT_REACTIVATION_ALERTS WHERE ACCOUNT_ID = '{acct_val}'
                        UNION ALL SELECT ALERT_TYPE, RULE_ID, ALERT_DESCRIPTION, ALERT_DATE FROM RISKLENS.ANALYTICS.JURISDICTION_ALERTS WHERE ACCOUNT_ID = '{acct_val}'
                    """)
                    if not detail_alerts.empty:
                        st.dataframe(detail_alerts, use_container_width=True)
    else:
        st.success("No risk signals for this customer.")

    # Watchlist hits
    wl = run_query(f"SELECT * FROM RISKLENS.ANALYTICS.WATCHLIST_ALERTS WHERE CUSTOMER_ID = '{cust_id}'")
    if not wl.empty:
        st.subheader("Watchlist Matches")
        st.dataframe(wl[["WATCHLIST_ENTITY","LIST_TYPE","MATCH_SCORE","WATCHLIST_REASON","WATCHLIST_SOURCE"]], use_container_width=True)

    # Transaction timeline
    st.subheader("Transaction History")
    acct_ids = accounts["ACCOUNT_ID"].tolist() if not accounts.empty else []
    if acct_ids:
        acct_filter = "','".join(acct_ids)
        txns = run_query(f"""
            SELECT TXN_ID, ACCOUNT_ID, TXN_DATE, TXN_TYPE, AMOUNT, COUNTERPARTY, COUNTERPARTY_COUNTRY, CHANNEL, DESCRIPTION
            FROM RISKLENS.RAW.TRANSACTIONS
            WHERE ACCOUNT_ID IN ('{acct_filter}')
            ORDER BY TXN_DATE DESC
        """)
        if not txns.empty:
            st.dataframe(txns, use_container_width=True)
        else:
            st.info("No transactions found.")

    # Relevant policies
    st.subheader("Related Policies")
    policies = run_query("SELECT POLICY_ID, TITLE, CATEGORY, VERSION, EFFECTIVE_DATE, JURISDICTION FROM RISKLENS.RAW.POLICIES")
    if not policies.empty:
        selected_policy = st.selectbox("View Policy", policies["TITLE"].tolist(), key="policy_select")
        if selected_policy:
            pol = run_query(f"SELECT CONTENT FROM RISKLENS.RAW.POLICIES WHERE TITLE = '{selected_policy.replace(chr(39), chr(39)+chr(39))}'")
            if not pol.empty:
                st.text_area("Policy Content", pol.iloc[0]["CONTENT"], height=200, disabled=True)

    # Create case from investigation
    st.subheader("Actions")
    if st.button("Open Investigation Case", key="open_case_btn"):
        case_id = next_id("CASE", "RISKLENS.APP.CASES", "CASE_ID")
        alert_detail = f"Manual investigation of {selected_name} ({cust_id})"
        if not risk.empty:
            alert_detail = f"Risk score {risk['TOTAL_RISK_SCORE'].max()}: {risk['ALERT_TYPES'].iloc[0]}"
        session.sql(f"""
            INSERT INTO RISKLENS.APP.CASES (CASE_ID, ALERT_SOURCE, ALERT_DETAIL, CUSTOMER_ID, STATUS, PRIORITY)
            VALUES ('{case_id}', 'INVESTIGATION', '{alert_detail.replace("'","''")}', '{cust_id}', 'OPEN', 'MEDIUM')
        """).collect()
        st.success(f"Case {case_id} created.")

# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# ASK - Natural Language Q&A
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
elif page == "Ask":
    st.title("Investigation Assistant")
    st.caption("Ask questions about transactions, accounts, risk signals, or policies. Answers are AI-generated from synthetic data and require human verification.")

    question = st.text_area("Your question:", placeholder="e.g., Which accounts show potential structuring activity?", height=80)

    if st.button("Ask", disabled=not question):
        with st.spinner("Generating query..."):
            generated_sql = safe_llm(SCHEMA_CONTEXT, question).strip()
            # Clean markdown fences if model adds them
            if generated_sql.startswith("```"):
                lines = generated_sql.split("\n")
                generated_sql = "\n".join(l for l in lines if not l.strip().startswith("```"))
            generated_sql = generated_sql.strip()

        ok, reason = validate_sql(generated_sql)
        if not ok:
            st.error(f"Generated SQL rejected: {reason}")
            st.code(generated_sql, language="sql")
            log_query(question, generated_sql, [], f"Rejected: {reason}")
        else:
            with st.expander("Generated SQL", expanded=False):
                st.code(generated_sql, language="sql")

            with st.spinner("Running query..."):
                results = run_query(generated_sql)

            if results.empty:
                st.info("No results returned.")
                log_query(question, generated_sql, [], "No results")
            else:
                st.subheader("Results")
                st.dataframe(results, use_container_width=True)

                # Synthesize answer
                with st.spinner("Synthesizing answer..."):
                    result_summary = results.head(20).to_string(index=False)
                    synth_prompt = f"""You are a financial crime investigation analyst reviewing synthetic data.
Based on the query results below, provide a concise analytical summary answering the user's question.
- State facts from the data; do not speculate beyond what is shown.
- Note any patterns or anomalies.
- Reference specific account IDs, amounts, and dates.
- End with: "Source: {len(results)} records from RISKLENS synthetic dataset. This is an investigation signal and requires human review."

Query results:
{result_summary}"""
                    answer = safe_llm(synth_prompt, question)

                st.subheader("Analysis")
                st.markdown(answer)

                sources = results.columns.tolist()
                log_query(question, generated_sql, sources, answer[:500] if answer else "")

    st.markdown("---")
    st.subheader("Recent Queries")
    recent = run_query("SELECT QUESTION, CREATED_AT, RESPONSE_SUMMARY FROM RISKLENS.AUDIT.QUERY_LOG ORDER BY CREATED_AT DESC LIMIT 5")
    if not recent.empty:
        for _, r in recent.iterrows():
            with st.expander(f"{r['CREATED_AT']} â€” {r['QUESTION'][:80]}"):
                st.write(r.get("RESPONSE_SUMMARY", ""))

# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# CASES
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
elif page == "Cases":
    st.title("Case Management")

    cases = run_query("SELECT * FROM RISKLENS.APP.CASES ORDER BY CREATED_AT DESC")
    if cases.empty:
        st.info("No cases yet. Open a case from the Investigate page.")
        st.stop()

    st.dataframe(cases[["CASE_ID","CUSTOMER_ID","STATUS","PRIORITY","ALERT_SOURCE","ALERT_DETAIL","CREATED_AT"]], use_container_width=True)

    st.markdown("---")
    selected_case = st.selectbox("Select Case", cases["CASE_ID"].tolist())
    case_row = cases[cases["CASE_ID"] == selected_case].iloc[0]

    c1, c2, c3 = st.columns(3)
    new_status = c1.selectbox("Status", ["OPEN", "IN_REVIEW", "ESCALATED", "CLOSED_NO_ACTION", "CLOSED_SAR_FILED"], index=["OPEN","IN_REVIEW","ESCALATED","CLOSED_NO_ACTION","CLOSED_SAR_FILED"].index(case_row["STATUS"]) if case_row["STATUS"] in ["OPEN","IN_REVIEW","ESCALATED","CLOSED_NO_ACTION","CLOSED_SAR_FILED"] else 0)
    new_priority = c2.selectbox("Priority", ["LOW", "MEDIUM", "HIGH", "CRITICAL"], index=["LOW","MEDIUM","HIGH","CRITICAL"].index(case_row["PRIORITY"]) if case_row["PRIORITY"] in ["LOW","MEDIUM","HIGH","CRITICAL"] else 1)
    new_assignee = c3.text_input("Assignee", value=case_row.get("ASSIGNEE") or "")

    if st.button("Update Case"):
        esc_assignee = new_assignee.replace("'", "''")
        session.sql(f"""
            UPDATE RISKLENS.APP.CASES
            SET STATUS='{new_status}', PRIORITY='{new_priority}', ASSIGNEE='{esc_assignee}', UPDATED_AT=CURRENT_TIMESTAMP()
            WHERE CASE_ID='{selected_case}'
        """).collect()
        session.sql(f"""
            INSERT INTO RISKLENS.AUDIT.REVIEW_HISTORY (CASE_ID, ACTION, COMMENTS)
            VALUES ('{selected_case}', 'STATUS_UPDATE', 'Status={new_status}, Priority={new_priority}, Assignee={esc_assignee}')
        """).collect()
        st.success("Case updated.")
        st.rerun()

    # Findings section
    st.subheader("Findings")
    findings = run_query(f"SELECT * FROM RISKLENS.APP.FINDINGS WHERE CASE_ID = '{selected_case}' ORDER BY CREATED_AT DESC")
    if not findings.empty:
        st.dataframe(findings[["FINDING_ID","TITLE","STATUS","CREATED_BY","CREATED_AT"]], use_container_width=True)

    with st.expander("Create New Finding"):
        finding_title = st.text_input("Finding Title", key="finding_title")
        auto_draft = st.checkbox("Auto-draft narrative with AI", value=True)

        if auto_draft and finding_title and st.button("Generate Draft", key="gen_draft"):
            cust_id = case_row.get("CUSTOMER_ID", "")
            risk_data = run_query(f"SELECT * FROM RISKLENS.ANALYTICS.RISK_SUMMARY WHERE CUSTOMER_ID = '{cust_id}'")
            risk_text = risk_data.to_string(index=False) if not risk_data.empty else "No risk data"
            txn_data = run_query(f"""
                SELECT t.* FROM RISKLENS.RAW.TRANSACTIONS t
                JOIN RISKLENS.RAW.ACCOUNTS a ON t.ACCOUNT_ID = a.ACCOUNT_ID
                WHERE a.CUSTOMER_ID = '{cust_id}'
                ORDER BY t.TXN_DATE DESC LIMIT 20
            """)
            txn_text = txn_data.to_string(index=False) if not txn_data.empty else "No transactions"

            draft_prompt = f"""You are drafting an investigation finding for a compliance case.
Case ID: {selected_case}
Finding title: {finding_title}
Customer: {cust_id}

Risk signals:
{risk_text}

Recent transactions:
{txn_text}

Write a structured investigation finding with:
1. SUMMARY - one paragraph overview
2. RISK INDICATORS - bullet list of specific signals found
3. EVIDENCE - cite specific transactions by ID, amount, date
4. POLICY REFERENCE - note which monitoring rules triggered (TM-001 through TM-006)
5. RECOMMENDATION - suggest next steps (this is a DRAFT requiring human review)

End with: "DRAFT - This finding was generated from synthetic data using AI assistance. It requires human review and must not be submitted to regulators without approval."
"""
            with st.spinner("Drafting..."):
                draft = safe_llm(draft_prompt, f"Draft finding for: {finding_title}")
            st.session_state["draft_narrative"] = draft

        narrative = st.text_area(
            "Narrative",
            value=st.session_state.get("draft_narrative", ""),
            height=300,
            key="narrative_input"
        )

        if st.button("Save Finding", key="save_finding"):
            if not finding_title or not narrative:
                st.warning("Title and narrative required.")
            else:
                fid = next_id("FIND", "RISKLENS.APP.FINDINGS", "FINDING_ID")
                esc_title = finding_title.replace("'", "''")
                esc_narr = narrative.replace("'", "''")
                evidence = json.dumps([selected_case]).replace("'", "''")
                session.sql(f"""
                    INSERT INTO RISKLENS.APP.FINDINGS
                    (FINDING_ID, CASE_ID, TITLE, NARRATIVE, EVIDENCE_REFS, STATUS, RULE_VERSION)
                    SELECT '{fid}', '{selected_case}', '{esc_title}', '{esc_narr}', PARSE_JSON('{evidence}'), 'DRAFT', '{RISK_VERSION}'
                """).collect()
                session.sql(f"""
                    INSERT INTO RISKLENS.AUDIT.REVIEW_HISTORY (FINDING_ID, CASE_ID, ACTION, COMMENTS)
                    VALUES ('{fid}', '{selected_case}', 'FINDING_CREATED', 'Draft finding created')
                """).collect()
                st.success(f"Finding {fid} saved as DRAFT.")
                if "draft_narrative" in st.session_state:
                    del st.session_state["draft_narrative"]
                st.rerun()

# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# REPORTS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
elif page == "Reports":
    st.title("Report Generation")
    st.caption("Generate downloadable investigation reports. All reports are DRAFTS requiring human review.")

    cases = run_query("SELECT CASE_ID, CUSTOMER_ID, STATUS, PRIORITY, ALERT_DETAIL FROM RISKLENS.APP.CASES ORDER BY CREATED_AT DESC")
    if cases.empty:
        st.info("No cases to report on.")
        st.stop()

    selected_case = st.selectbox("Select Case", cases["CASE_ID"].tolist(), key="report_case")
    case_row = cases[cases["CASE_ID"] == selected_case].iloc[0]
    cust_id = case_row.get("CUSTOMER_ID", "")

    if st.button("Generate Report"):
        with st.spinner("Compiling report..."):
            # Gather all data
            cust = run_query(f"SELECT * FROM RISKLENS.RAW.CUSTOMERS WHERE CUSTOMER_ID = '{cust_id}'")
            accts = run_query(f"SELECT * FROM RISKLENS.RAW.ACCOUNTS WHERE CUSTOMER_ID = '{cust_id}'")
            acct_ids = accts["ACCOUNT_ID"].tolist() if not accts.empty else []
            acct_filter = "','".join(acct_ids)
            txns = run_query(f"SELECT * FROM RISKLENS.RAW.TRANSACTIONS WHERE ACCOUNT_ID IN ('{acct_filter}') ORDER BY TXN_DATE") if acct_ids else pd.DataFrame()
            risk = run_query(f"SELECT * FROM RISKLENS.ANALYTICS.RISK_SUMMARY WHERE CUSTOMER_ID = '{cust_id}'")
            findings = run_query(f"SELECT * FROM RISKLENS.APP.FINDINGS WHERE CASE_ID = '{selected_case}'")
            reviews = run_query(f"SELECT * FROM RISKLENS.AUDIT.REVIEW_HISTORY WHERE CASE_ID = '{selected_case}' ORDER BY CREATED_AT")
            wl = run_query(f"SELECT * FROM RISKLENS.ANALYTICS.WATCHLIST_ALERTS WHERE CUSTOMER_ID = '{cust_id}'")

            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
            cust_name = cust.iloc[0]["NAME"] if not cust.empty else cust_id

            report = f"""{'='*70}
RISKLENS INVESTIGATION REPORT
{'='*70}
DRAFT - REQUIRES HUMAN REVIEW BEFORE ANY REGULATORY SUBMISSION
DATA SOURCE: SYNTHETIC (NOT REAL FINANCIAL DATA)
{'='*70}

Report Generated: {now}
Case ID:          {selected_case}
Customer:         {cust_name} ({cust_id})
Case Status:      {case_row.get('STATUS', 'N/A')}
Case Priority:    {case_row.get('PRIORITY', 'N/A')}
Scoring Version:  {RISK_VERSION}

{'â”€'*70}
1. CUSTOMER PROFILE
{'â”€'*70}
"""
            if not cust.empty:
                c = cust.iloc[0]
                report += f"Name:           {c.get('NAME','')}\n"
                report += f"Type:           {c.get('CUSTOMER_TYPE','')}\n"
                report += f"Risk Rating:    {c.get('RISK_RATING','')}\n"
                report += f"KYC Status:     {c.get('KYC_STATUS','')}\n"
                report += f"Country:        {c.get('COUNTRY','')}\n"
                report += f"Onboarded:      {c.get('ONBOARDING_DATE','')}\n"

            report += f"\n{'â”€'*70}\n2. ACCOUNTS ({len(accts)} total)\n{'â”€'*70}\n"
            if not accts.empty:
                for _, a in accts.iterrows():
                    report += f"  {a['ACCOUNT_ID']}  {a['ACCOUNT_TYPE']:15s}  Balance: ${a.get('BALANCE',0):>12,.2f}  Status: {a.get('STATUS','')}\n"

            report += f"\n{'â”€'*70}\n3. RISK SIGNALS\n{'â”€'*70}\n"
            if not risk.empty:
                for _, r in risk.iterrows():
                    report += f"  Account: {r.get('ACCOUNT_ID','N/A')}\n"
                    report += f"  Score:   {r.get('TOTAL_RISK_SCORE',0)} ({r.get('SEVERITY','N/A')})\n"
                    report += f"  Types:   {r.get('ALERT_TYPES','')}\n"
                    report += f"  Rules:   {r.get('TRIGGERED_RULES','')}\n\n"
            else:
                report += "  No risk signals detected.\n"

            if not wl.empty:
                report += f"\n{'â”€'*70}\n4. WATCHLIST MATCHES\n{'â”€'*70}\n"
                for _, w in wl.iterrows():
                    report += f"  {w.get('LIST_TYPE','')}: \"{w.get('CUSTOMER_NAME','')}\" ~ \"{w.get('WATCHLIST_ENTITY','')}\" (score: {w.get('MATCH_SCORE','')})\n"
                    report += f"  Reason: {w.get('WATCHLIST_REASON','')}\n\n"

            report += f"\n{'â”€'*70}\n5. TRANSACTION SUMMARY ({len(txns)} transactions)\n{'â”€'*70}\n"
            if not txns.empty:
                report += f"  Date Range:     {txns['TXN_DATE'].min()} to {txns['TXN_DATE'].max()}\n"
                report += f"  Total Volume:   ${txns['AMOUNT'].abs().sum():,.2f}\n"
                report += f"  By Type:\n"
                for ttype, group in txns.groupby("TXN_TYPE"):
                    report += f"    {ttype:20s}  Count: {len(group):>4d}  Total: ${group['AMOUNT'].abs().sum():>12,.2f}\n"
                report += f"\n  Transactions (most recent 20):\n"
                for _, t in txns.tail(20).iterrows():
                    report += f"    {t['TXN_ID']}  {str(t['TXN_DATE'])[:19]}  {t['TXN_TYPE']:15s}  ${t['AMOUNT']:>10,.2f}  {t.get('COUNTERPARTY','') or ''}\n"

            report += f"\n{'â”€'*70}\n6. FINDINGS\n{'â”€'*70}\n"
            if not findings.empty:
                for _, f_row in findings.iterrows():
                    report += f"\n  Finding: {f_row['FINDING_ID']} - {f_row.get('TITLE','')}\n"
                    report += f"  Status:  {f_row.get('STATUS','')}\n"
                    report += f"  Created: {f_row.get('CREATED_AT','')}\n"
                    report += f"  Rule Version: {f_row.get('RULE_VERSION','')}\n"
                    report += f"  Narrative:\n"
                    narr = f_row.get("NARRATIVE", "")
                    if narr:
                        for line in str(narr).split("\n"):
                            report += f"    {line}\n"
            else:
                report += "  No findings recorded.\n"

            report += f"\n{'â”€'*70}\n7. REVIEW HISTORY\n{'â”€'*70}\n"
            if not reviews.empty:
                for _, rv in reviews.iterrows():
                    report += f"  {rv.get('CREATED_AT','')}  {rv.get('REVIEWER','')}  {rv.get('ACTION','')}  {rv.get('COMMENTS','')}\n"
            else:
                report += "  No review actions recorded.\n"

            report += f"""
{'â”€'*70}
8. APPLICABLE POLICIES
{'â”€'*70}
"""
            policies = run_query("SELECT POLICY_ID, TITLE, CATEGORY, VERSION, EFFECTIVE_DATE FROM RISKLENS.RAW.POLICIES")
            if not policies.empty:
                for _, p in policies.iterrows():
                    report += f"  {p['POLICY_ID']}  {p['TITLE']}  v{p['VERSION']}  ({p['EFFECTIVE_DATE']})\n"

            report += f"""
{'='*70}
DISCLAIMER
{'='*70}
This report was generated by RiskLens using SYNTHETIC data and AI
assistance ({LLM_MODEL}). It is a DRAFT intended for internal review only.

- All data is illustrative and does not represent real persons or entities.
- Risk signals are investigation indicators, not determinations of fraud.
- AI-generated narratives require human verification before use.
- This report must NOT be submitted to any regulatory authority without
  formal review, approval, and adaptation to applicable regulations.
- No jurisdiction-specific compliance is claimed.
- Rule version: {RISK_VERSION}

Generated by: RiskLens MVP | {now}
{'='*70}
"""
            st.text_area("Report Preview", report, height=500)
            st.download_button(
                label="Download Report",
                data=report,
                file_name=f"risklens_{selected_case}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
                mime="text/plain"
            )

            # Log the report generation
            session.sql(f"""
                INSERT INTO RISKLENS.AUDIT.REVIEW_HISTORY (CASE_ID, ACTION, COMMENTS)
                VALUES ('{selected_case}', 'REPORT_GENERATED', 'Investigation report generated as draft')
            """).collect()
