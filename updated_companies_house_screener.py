import json
import re
import sqlite3
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Companies House New Incorporations Screener", layout="wide")

BASE_URL = "https://api.company-information.service.gov.uk"
DB_PATH = "companies_house_screening.db"
SEARCH_PAGE_SIZE = 5000
OFFICERS_PAGE_SIZE = 100
PSC_PAGE_SIZE = 100

# Keep all of your existing constants and helper functions unchanged above this point.
# The only functional changes required are shown below.


def existing_company_numbers(
    conn: sqlite3.Connection,
    start_date: str,
    end_date: Optional[str] = None,
) -> set:
    end_date = end_date or start_date
    rows = conn.execute(
        """
        SELECT company_number
        FROM screened_companies
        WHERE incorporation_date BETWEEN ? AND ?
        """,
        (start_date, end_date),
    ).fetchall()
    return {r[0] for r in rows}


def search_new_companies(
    client: CHClient,
    start_date: str,
    end_date: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    end_date = end_date or start_date
    params = {
        "incorporated_from": start_date,
        "incorporated_to": end_date,
        "company_status": "active",
        "company_type": ",".join(ALLOWED_COMPANY_TYPES),
        "sic_codes": ",".join(ALL_ALLOWED_SIC_CODES),
    }
    items = paged_get_items(client, "/advanced-search/companies", SEARCH_PAGE_SIZE, params)
    filtered: List[Dict[str, Any]] = []
    for item in items:
        item_sics = [str(x) for x in (item.get("sic_codes") or [])]
        if not any(code in ALL_ALLOWED_SIC_CODES for code in item_sics):
            continue
        if item.get("company_status", "").lower() != "active":
            continue
        if not is_allowed_company_type(item.get("company_type", "")):
            continue
        filtered.append(item)

    deduped: Dict[str, Dict[str, Any]] = {}
    for item in filtered:
        number = item.get("company_number")
        if number:
            deduped[number] = item

    diagnostics = {
        "raw_results": len(items),
        "filtered_results": len(filtered),
        "deduped_results": len(deduped),
        "company_types_sent": ", ".join(ALLOWED_COMPANY_TYPES),
        "sic_count": len(ALL_ALLOWED_SIC_CODES),
    }
    return list(deduped.values()), diagnostics


def process_company(
    client: CHClient,
    item: Dict[str, Any],
    incorporation_date: str,
) -> Dict[str, Any]:
    # Keep the original body of process_company unchanged, but use the actual
    # incorporation date returned by Companies House rather than the old
    # singular target date.
    company_number = item.get("company_number", "")
    company_name = item.get("company_name") or item.get("title") or ""
    international_director, director_details, director_count = collect_international_director_details(
        client, company_number
    )
    international_shareholder, shareholder_details, owned_by_company, owner_names = analyse_psc_flags(
        client, company_number
    )
    target_sic = is_target_sic(item)
    target_address, target_address_detail = is_target_address(item)
    owner_display = " | ".join([f"✓ {name}" for name in owner_names]) if owner_names else ""
    target_indicators = build_target_indicators(
        target_sic=target_sic,
        target_address=target_address,
        address_detail=target_address_detail,
        director_details=director_details,
        shareholder_details=shareholder_details,
        director_count=director_count,
    )

    actual_date = item.get("date_of_creation") or incorporation_date
    return {
        "company_number": company_number,
        "company_name": company_name,
        "sic_code": parse_matching_sic(item),
        "incorporation_date": actual_date,
        "company_type": item.get("company_type", ""),
        "international_director": international_director,
        "international_director_detail": format_flagged_countries(director_details),
        "international_shareholder": international_shareholder,
        "international_shareholder_detail": format_flagged_countries(shareholder_details),
        "owned_by_company": owned_by_company,
        "owner_company_name": owner_display,
        "pulled_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "raw_json": item,
        "profile_url": make_company_profile_url(company_number, company_name),
        "shortlisted": False,
        "target_sic": target_sic,
        "target_address": target_address,
        "target_address_detail": target_address_detail,
        "target_indicators": target_indicators,
    }


def render_sidebar(
    default_start: date,
    default_end: date,
) -> Tuple[date, date, bool, List[str], str, str, bool, bool, bool]:
    with st.sidebar:
        st.header("Screening controls")
        start_date = st.date_input(
            "Incorporation date from",
            value=default_start,
            format="YYYY-MM-DD",
        )
        end_date = st.date_input(
            "Incorporation date to",
            value=default_end,
            format="YYYY-MM-DD",
        )
        run = st.button("Pull new companies", type="primary", use_container_width=True)
        st.divider()
        st.subheader("Result filters")
        only_flagged = st.checkbox("Show only flagged rows", value=False)
        selected_signals = st.multiselect("Signals", options=SIGNAL_OPTIONS, default=SIGNAL_OPTIONS)
        hide_mfg_wholesale = st.checkbox("Hide Manufacturing & Wholesale SICs", value=False)
        sic_search = st.text_input("Filter by SIC code", placeholder="e.g. 62012")
        company_name_search = st.text_input("Filter by company name", placeholder="e.g. Labs")
        shortlisted_only = st.checkbox("Show shortlisted only", value=False)
        st.divider()
        st.caption("The sidebar keeps controls separate from the results table for faster screening.")

    return (
        start_date,
        end_date,
        run,
        selected_signals,
        sic_search,
        company_name_search,
        only_flagged,
        shortlisted_only,
        hide_mfg_wholesale,
    )


def date_range_label(start_date: str, end_date: str) -> str:
    return start_date if start_date == end_date else f"{start_date} to {end_date}"


def main() -> None:
    apply_custom_css()
    st.title("Companies House New Incorporations Screener")
    st.caption(
        "Pull newly incorporated active companies, screen target SIC codes, and enrich results with officer and PSC checks."
    )

    st.markdown(
        """
        <div class="app-note">
        Designed for rapid lead triage: run the pull, scan KPIs, filter the signals, shortlist candidates, and click through to Companies House profiles.
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.expander("Secrets format", expanded=False):
        st.code(
            '''COMPANIES_HOUSE_API_KEYS = [
  "key-1",
  "key-2",
  "key-3"
]''',
            language="toml",
        )

    try:
        api_keys = validate_api_keys()
    except Exception as exc:
        st.error(str(exc))
        st.stop()

    conn = init_db()
    client = CHClient(api_keys)

    (
        start_date,
        end_date,
        run,
        selected_signals,
        sic_search,
        company_name_search,
        only_flagged,
        shortlisted_only,
        hide_mfg_wholesale,
    ) = render_sidebar(date.today(), date.today())

    if start_date > end_date:
        st.sidebar.error("The 'from' date must be on or before the 'to' date.")
        st.stop()

    start_date_str = start_date.strftime("%Y-%m-%d")
    end_date_str = end_date.strftime("%Y-%m-%d")
    range_label = date_range_label(start_date_str, end_date_str)

    if run:
        failures: List[str] = []
        with st.status("Running Companies House screening...", expanded=True) as status:
            st.write(f"Searching incorporation dates from {start_date_str} to {end_date_str}.")
            companies, diagnostics = search_new_companies(client, start_date_str, end_date_str)
            already_seen = existing_company_numbers(conn, start_date_str, end_date_str)
            new_companies = [c for c in companies if c.get("company_number") not in already_seen]
            st.write(f"Raw search results: {diagnostics['raw_results']}")
            st.write(f"Filtered results retained: {diagnostics['filtered_results']}")
            st.write(f"Deduped company numbers: {diagnostics['deduped_results']}")
            st.write(f"Already screened for {range_label}: {len(already_seen)}")
            st.write(f"New companies to enrich: {len(new_companies)}")

            progress = st.progress(0)
            total = max(len(new_companies), 1)
            for idx, item in enumerate(new_companies, start=1):
                company_number = item.get("company_number", "unknown")
                try:
                    incorporation_date = item.get("date_of_creation") or start_date_str
                    row = process_company(client, item, incorporation_date)
                    upsert_company(conn, row)
                except Exception as exc:
                    failures.append(f"{company_number}: {exc}")
                progress.progress(min(idx / total, 1.0))

            if failures:
                st.warning(f"Failed enrichments: {len(failures)}")
                st.code("\n".join(failures[:50]))
                status.update(label="Completed with some errors", state="error")
            else:
                status.update(label="Refresh complete", state="complete")

    db_df = read_db_rows(conn)
    if not db_df.empty:
        db_df = db_df[
            (db_df["incorporation_date"] >= start_date_str)
            & (db_df["incorporation_date"] <= end_date_str)
        ].copy()

    display_df = build_display_df(db_df)
    render_kpis(display_df)

    st.markdown(
        """
        <div class="signal-legend">
            <div class="signal-pill">Target SIC 🎯</div>
            <div class="signal-pill">Target Registered Address 🏠</div>
            <div class="signal-pill">Signals = country flags (director/shareholder based in target countries) + 🏢 for corporate PSCs</div>
            <div class="signal-pill">Target Indicators = 🎯, 🏠, country flags, and number emoji for 2+ directors</div>
            <div class="signal-pill">Rating ⭐ = 1 star per signal, plus a bonus for Sweden, Norway, or USA director/shareholder</div>
            <div class="signal-pill">Manufacturing & Wholesale SICs = can be hidden via sidebar toggle</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    filtered_df = apply_filters(
        display_df,
        only_flagged=only_flagged,
        selected_signals=selected_signals,
        sic_search=sic_search,
        company_name_search=company_name_search,
        shortlisted_only=shortlisted_only,
        hide_mfg_wholesale=hide_mfg_wholesale,
    )

    tab_results, tab_shortlist, tab_settings = st.tabs(["Results", "Shortlist", "Settings"])

    with tab_results:
        st.subheader("Results")
        st.caption(
            f"Loaded {len(api_keys)} API key(s) for {range_label}. {len(filtered_df):,} rows currently visible after filters."
        )

        editor_df = filtered_df[
            [
                "Shortlist",
                "Target SIC",
                "Rating",
                "Target Indicators",
                "Company Name",
                "SIC Code",
                "Signals",
                "International Director",
                "International Shareholder",
                "Owned By A Company",
                "Profile",
                "Pulled At",
                "company_number",
            ]
        ].copy()

        edited_df = st.data_editor(
            editor_df,
            use_container_width=True,
            hide_index=True,
            disabled=[
                "Target SIC", "Rating", "Target Indicators", "Company Name", "SIC Code",
                "Signals", "International Director", "International Shareholder",
                "Owned By A Company", "Profile", "Pulled At", "company_number",
            ],
            column_config={
                "Shortlist": st.column_config.CheckboxColumn("Shortlist", help="Tick to mark this company for follow-up."),
                "Target SIC": st.column_config.TextColumn("Target SIC", width="small"),
                "Rating": st.column_config.TextColumn("Rating", width="small"),
                "Target Indicators": st.column_config.TextColumn("Target Indicators", width="medium"),
                "Company Name": st.column_config.TextColumn("Company Name", width="large"),
                "SIC Code": st.column_config.TextColumn("SIC Code", width="small"),
                "Signals": st.column_config.TextColumn("Signals", width="medium"),
                "International Director": st.column_config.TextColumn("International Director", width="large"),
                "International Shareholder": st.column_config.TextColumn("International Shareholder", width="large"),
                "Owned By A Company": st.column_config.TextColumn("Owned By A Company", width="large"),
                "Profile": st.column_config.LinkColumn("Profile", display_text="Open record", width="small"),
                "Pulled At": st.column_config.TextColumn("Pulled At", width="medium"),
                "company_number": None,
            },
            key=f"results_editor_{start_date_str}_{end_date_str}",
        )

        if not edited_df.empty:
            changes = edited_df[["company_number", "Shortlist"]].merge(
                display_df[["company_number", "Shortlist"]],
                on="company_number",
                suffixes=("_new", "_old"),
                how="left",
            )
            changed_rows = changes[changes["Shortlist_new"] != changes["Shortlist_old"]]
            for _, row in changed_rows.iterrows():
                set_shortlisted_state(conn, row["company_number"], bool(row["Shortlist_new"]))
            if not changed_rows.empty:
                st.success(f"Updated shortlist state for {len(changed_rows)} compan{'y' if len(changed_rows) == 1 else 'ies'}.")
                st.rerun()

        csv = filtered_df.drop(columns=["company_number"], errors="ignore").to_csv(index=False).encode("utf-8")
        st.download_button(
            "Download filtered CSV",
            data=csv,
            file_name=f"companies_house_screening_{start_date_str}_{end_date_str}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    with tab_shortlist:
        st.subheader("Shortlist")
        shortlist_df = display_df[display_df["Shortlist"]].copy()
        if shortlist_df.empty:
            st.info("No shortlisted companies yet. Tick the shortlist checkbox in the Results tab to build a follow-up queue.")
        else:
            st.dataframe(
                shortlist_df.drop(columns=["company_number"], errors="ignore"),
                use_container_width=True,
                hide_index=True,
                column_config={"Profile": st.column_config.LinkColumn("Profile", display_text="Open record")},
            )
            shortlist_csv = shortlist_df.drop(columns=["company_number"], errors="ignore").to_csv(index=False).encode("utf-8")
            st.download_button(
                "Download shortlist CSV",
                data=shortlist_csv,
                file_name=f"companies_house_shortlist_{start_date_str}_{end_date_str}.csv",
                mime="text/csv",
                use_container_width=True,
            )

    with tab_settings:
        st.subheader("Current search settings")
        st.markdown(
            f"""
- Company status: Active
- Company types sent to API: `{', '.join(ALLOWED_COMPANY_TYPES)}`
- Incorporation date range: `{range_label}`
- SIC codes sent to API (core): {len(ALLOWED_SIC_CODES)} values
- Manufacturing & Wholesale SIC codes: {len(MANUFACTURING_WHOLESALE_SIC_CODES)} values
- Target SIC codes: `{', '.join(sorted(TARGET_SIC_CODES))}`
- Advanced search page size: {SEARCH_PAGE_SIZE}
- Officers page size: {OFFICERS_PAGE_SIZE}
- PSC page size: {PSC_PAGE_SIZE}
- Dedupe rule: company numbers already screened within the selected incorporation-date range are skipped
            """
        )
        st.write("Selected signals for current filter:", ", ".join(selected_signals) if selected_signals else "None")


if __name__ == "__main__":
    main()
