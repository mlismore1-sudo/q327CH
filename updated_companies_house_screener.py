import json
import re
import sqlite3
import time
from datetime import date, datetime
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

ALLOWED_SIC_CODES = [
    "62012", "62020", "63120", "47910", "46190", "46499", "70229", "73110", "74909", "68209",
    "64209", "68100", "32990", "10890", "86900", "93130", "96040", "82990", "72110", "56101",
]
TARGET_SIC_CODES = {"62012", "72110", "56101"}
MANUFACTURING_WHOLESALE_SIC_CODES = {
    "10110", "10130", "10310", "10410", "10511", "10512", "10611", "10612", "10840", "10850", "10890",
    "10920", "13100", "13200", "13300", "13921", "13923", "13960", "14131", "15110", "16290", "19200",
    "20110", "20120", "20130", "20140", "20150", "20160", "20170", "20200", "20301", "20302", "20411",
    "20412", "20590", "21100", "22210", "22290", "23190", "23910", "23990", "24100", "24200", "24310",
    "24320", "24330", "24340", "24410", "24420", "24430", "24340", "24440", "24450", "24460", "24510",
    "25110", "25210", "25500", "25990", "26110", "26200", "26300", "26511", "26512", "26600", "27110",
    "27200", "28110", "28290", "28300", "28990", "29100", "29310", "30110", "30300", "31090", "32990",
    "46110", "46120", "46130", "46140", "46150", "46160", "46170", "46180", "46190", "46210", "46220",
    "46230", "46240", "46310", "46320", "46330", "46341", "46342", "46350", "46360", "46370", "46380",
    "46390", "46410", "46420", "46431", "46439", "46440", "46450", "46460", "46470", "46480", "46499",
    "46510", "46520", "46530", "46610", "46620", "46630", "46640", "46650", "46660", "46690", "46711",
    "46719", "46720", "46730", "46740", "46750", "46900",
}
ALL_ALLOWED_SIC_CODES = sorted(set(ALLOWED_SIC_CODES) | MANUFACTURING_WHOLESALE_SIC_CODES)

BONUS_STAR_COUNTRIES = {"sweden", "norway", "united states"}
ALLOWED_COMPANY_TYPES = [
    "ltd", "llp", "private-limited-guarant-nsc", "private-limited-shares-section-30-exemption",
]
COUNTRY_TERMS = {
    "usa", "united states", "united states of america", "france", "germany", "belgium", "norway",
    "sweden", "finland", "denmark", "austria", "poland", "spain", "portugal", "greece", "italy",
    "hungary", "croatia", "ireland", "china", "netherlands", "india", "hong kong", "singapore",
}
COMPANY_OWNER_KINDS = {
    "corporate-entity-person-with-significant-control",
    "legal-person-person-with-significant-control",
    "super-secure-person-with-significant-control",
}
COUNTRY_FLAG_MAP = {
    "united states": "🇺🇸", "france": "🇫🇷", "germany": "🇩🇪", "belgium": "🇧🇪",
    "norway": "🇳🇴", "sweden": "🇸🇪", "finland": "🇫🇮", "denmark": "🇩🇰",
    "austria": "🇦🇹", "poland": "🇵🇱", "spain": "🇪🇸", "portugal": "🇵🇹",
    "greece": "🇬🇷", "italy": "🇮🇹", "hungary": "🇭🇺", "croatia": "🇭🇷",
    "ireland": "🇮🇪", "china": "🇨🇳", "netherlands": "🇳🇱", "india": "🇮🇳",
    "hong kong": "🇭🇰", "singapore": "🇸🇬",
}
NATIONALITY_TO_COUNTRY = {
    "american": "united states", "us": "united states", "united states": "united states",
    "french": "france", "german": "germany", "belgian": "belgium", "norwegian": "norway",
    "swedish": "sweden", "finnish": "finland", "danish": "denmark", "austrian": "austria",
    "polish": "poland", "spanish": "spain", "portuguese": "portugal", "greek": "greece",
    "italian": "italy", "hungarian": "hungary", "croatian": "croatia", "irish": "ireland",
    "chinese": "china", "indian": "india", "hong kong": "hong kong", "hongkong": "hong kong",
    "singaporean": "singapore", "dutch": "netherlands", "netherlands": "netherlands",
}
SIGNAL_OPTIONS = ["International Director", "International Shareholder", "Owned By A Company"]
CHECKBOX_COLUMNS = ["Funding secured?", "NAB'd", "Owned by B/J", "Shortlist"]
DB_CHECKBOX_COLUMNS = {
    "Funding secured?": "funding_secured",
    "NAB'd": "nabd",
    "Owned by B/J": "owned_by_bj",
    "Shortlist": "shortlisted",
}


def apply_custom_css() -> None:
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"][aria-expanded="true"] > div:first-child { width: 340px; }
        div[data-testid="metric-container"] {
            background: linear-gradient(180deg, rgba(14, 17, 23, 0.03), rgba(14, 17, 23, 0.01));
            border: 1px solid rgba(120, 120, 120, 0.18); padding: 14px 16px; border-radius: 14px;
        }
        .app-note { padding: 0.85rem 1rem; border-radius: 12px; border: 1px solid rgba(120, 120, 120, 0.18); background: rgba(49, 51, 63, 0.04); margin-bottom: 1rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower().replace("-", " ")
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    aliases = {
        "usa": "united states", "u s a": "united states", "u s": "us",
        "united states of america": "united states", "america": "american",
        "hongkong": "hong kong", "the netherlands": "netherlands",
    }
    return aliases.get(text, text)


NORMALIZED_COUNTRY_TERMS = {normalize_text(x) for x in COUNTRY_TERMS}
NORMALIZED_ALLOWED_COMPANY_TYPES = {normalize_text(x) for x in ALLOWED_COMPANY_TYPES}


def canonical_country_from_value(value: Any) -> str:
    norm = normalize_text(value)
    if not norm:
        return ""
    if norm in NORMALIZED_COUNTRY_TERMS:
        return norm
    return NATIONALITY_TO_COUNTRY.get(norm, "")


def dedupe_preserve_order(values: List[str]) -> List[str]:
    output: List[str] = []
    seen = set()
    for value in values:
        norm = normalize_text(value)
        if norm and norm not in seen:
            seen.add(norm)
            output.append(value)
    return output


def country_label(value: str) -> str:
    if value == "united states":
        return "USA"
    if value == "hong kong":
        return "Hong Kong"
    return value.title()


def format_flagged_countries(values: List[str]) -> str:
    countries = dedupe_preserve_order([canonical_country_from_value(v) for v in values])
    return " | ".join(f"✓ {COUNTRY_FLAG_MAP.get(v, '🌍')} {country_label(v)}" for v in countries)


def extract_country_flags(values: List[str]) -> List[str]:
    countries = dedupe_preserve_order([canonical_country_from_value(v) for v in values])
    return [COUNTRY_FLAG_MAP[v] for v in countries if v in COUNTRY_FLAG_MAP]


def make_company_profile_url(company_number: str, company_name: str) -> str:
    return f"https://find-and-update.company-information.service.gov.uk/company/{company_number}#{quote(company_name or 'company')}"


def make_google_funding_search_url(company_name: str) -> str:
    clean_name = re.sub(r"\s+(?:ltd|limited)\.?\s*$", "", str(company_name or ""), flags=re.IGNORECASE).strip()
    return f"https://www.google.com/search?q={quote(f'{clean_name} funding')}"


class CHClient:
    def __init__(self, api_keys: List[str]):
        self.api_keys = [key.strip() for key in api_keys if str(key).strip()]
        if not self.api_keys:
            raise ValueError("No Companies House API keys supplied.")
        self.idx = 0
        self.session = requests.Session()

    def _auth(self) -> Tuple[str, str]:
        return self.api_keys[self.idx % len(self.api_keys)], ""

    def _rotate(self) -> None:
        self.idx = (self.idx + 1) % len(self.api_keys)

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        last_error = "Unknown error"
        for _ in range(max(len(self.api_keys) * 3, 3)):
            try:
                response = self.session.get(f"{BASE_URL}{path}", params=params, auth=self._auth(), timeout=30, headers={"Accept": "application/json"})
                if response.status_code == 404:
                    return {}
                if response.status_code in (401, 403, 429):
                    last_error = f"HTTP {response.status_code}"
                    self._rotate()
                    time.sleep(0.5)
                    continue
                response.raise_for_status()
                return response.json()
            except requests.RequestException as exc:
                last_error = str(exc)
                self._rotate()
                time.sleep(0.5)
        raise RuntimeError(f"Companies House API request failed after retries: {last_error}")


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        conn.commit()


def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS screened_companies (
            company_number TEXT PRIMARY KEY,
            company_name TEXT,
            sic_code TEXT,
            incorporation_date TEXT,
            company_type TEXT,
            international_director INTEGER,
            international_shareholder INTEGER,
            owned_by_company INTEGER,
            pulled_at TEXT,
            raw_json TEXT,
            international_director_detail TEXT,
            international_shareholder_detail TEXT,
            owner_company_name TEXT,
            profile_url TEXT,
            shortlisted INTEGER DEFAULT 0,
            target_sic INTEGER DEFAULT 0,
            target_address INTEGER DEFAULT 0,
            target_address_detail TEXT,
            target_indicators TEXT,
            funding_secured INTEGER DEFAULT 0,
            nabd INTEGER DEFAULT 0,
            owned_by_bj INTEGER DEFAULT 0
        )
        """
    )
    conn.commit()
    ensure_column(conn, "screened_companies", "international_director_detail", "TEXT")
    ensure_column(conn, "screened_companies", "international_shareholder_detail", "TEXT")
    ensure_column(conn, "screened_companies", "owner_company_name", "TEXT")
    ensure_column(conn, "screened_companies", "profile_url", "TEXT")
    ensure_column(conn, "screened_companies", "shortlisted", "INTEGER DEFAULT 0")
    ensure_column(conn, "screened_companies", "target_sic", "INTEGER DEFAULT 0")
    ensure_column(conn, "screened_companies", "target_address", "INTEGER DEFAULT 0")
    ensure_column(conn, "screened_companies", "target_address_detail", "TEXT")
    ensure_column(conn, "screened_companies", "target_indicators", "TEXT")
    ensure_column(conn, "screened_companies", "funding_secured", "INTEGER DEFAULT 0")
    ensure_column(conn, "screened_companies", "nabd", "INTEGER DEFAULT 0")
    ensure_column(conn, "screened_companies", "owned_by_bj", "INTEGER DEFAULT 0")
    return conn


def existing_company_numbers(conn: sqlite3.Connection, start_date: str, end_date: str) -> set:
    rows = conn.execute("SELECT company_number FROM screened_companies WHERE incorporation_date BETWEEN ? AND ?", (start_date, end_date)).fetchall()
    return {row[0] for row in rows}


def set_company_checkbox_state(conn: sqlite3.Connection, company_number: str, column: str, checked: bool) -> None:
    if column not in DB_CHECKBOX_COLUMNS.values():
        raise ValueError(f"Unsupported checkbox database column: {column}")
    conn.execute(f"UPDATE screened_companies SET {column} = ? WHERE company_number = ?", (int(checked), company_number))
    conn.commit()


def upsert_company(conn: sqlite3.Connection, row: Dict[str, Any]) -> None:
    existing = conn.execute("SELECT shortlisted, funding_secured, nabd, owned_by_bj FROM screened_companies WHERE company_number = ?", (row["company_number"],)).fetchone()
    preserved = {
        "shortlisted": existing[0] if existing else int(row.get("shortlisted", False)),
        "funding_secured": existing[1] if existing else int(row.get("funding_secured", False)),
        "nabd": existing[2] if existing else int(row.get("nabd", False)),
        "owned_by_bj": existing[3] if existing else int(row.get("owned_by_bj", False)),
    }
    conn.execute(
        """
        INSERT OR REPLACE INTO screened_companies (
            company_number, company_name, sic_code, incorporation_date, company_type,
            international_director, international_director_detail,
            international_shareholder, international_shareholder_detail,
            owned_by_company, owner_company_name, pulled_at, raw_json,
            profile_url, shortlisted, target_sic, target_address,
            target_address_detail, target_indicators, funding_secured, nabd, owned_by_bj
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["company_number"], row["company_name"], row["sic_code"], row["incorporation_date"], row["company_type"],
            int(row["international_director"]), row.get("international_director_detail", ""),
            int(row["international_shareholder"]), row.get("international_shareholder_detail", ""),
            int(row["owned_by_company"]), row.get("owner_company_name", ""), row["pulled_at"], json.dumps(row.get("raw_json", {})),
            row.get("profile_url", ""), preserved["shortlisted"], int(row.get("target_sic", False)), int(row.get("target_address", False)),
            row.get("target_address_detail", ""), row.get("target_indicators", ""), preserved["funding_secured"], preserved["nabd"], preserved["owned_by_bj"],
        ),
    )
    conn.commit()


def read_db_rows(conn: sqlite3.Connection, start_date: str, end_date: str) -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM screened_companies WHERE incorporation_date BETWEEN ? AND ? ORDER BY pulled_at DESC", conn, params=(start_date, end_date))


def validate_api_keys() -> List[str]:
    if "COMPANIES_HOUSE_API_KEYS" not in st.secrets:
        raise ValueError("Missing COMPANIES_HOUSE_API_KEYS in .streamlit/secrets.toml")
    keys = [str(key).strip() for key in list(st.secrets["COMPANIES_HOUSE_API_KEYS"]) if str(key).strip()]
    if not keys:
        raise ValueError("COMPANIES_HOUSE_API_KEYS is empty")
    return keys


def paged_get_items(client: CHClient, path: str, page_size: int, extra_params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    start_index = 0
    while True:
        params: Dict[str, Any] = {"start_index": start_index}
        if extra_params:
            params.update(extra_params)
        params["size" if path == "/advanced-search/companies" else "items_per_page"] = page_size
        payload = client.get(path, params=params)
        batch = payload.get("items", []) or []
        items.extend(batch)
        total = int(payload.get("total_results") or payload.get("total_count") or len(items))
        start_index += page_size
        if not batch or start_index >= total:
            break
    return items


def is_allowed_company_type(value: Any) -> bool:
    return normalize_text(value) in NORMALIZED_ALLOWED_COMPANY_TYPES


def search_new_companies(client: CHClient, start_date: str, end_date: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    params = {"incorporated_from": start_date, "incorporated_to": end_date, "company_status": "active", "company_type": ",".join(ALLOWED_COMPANY_TYPES), "sic_codes": ",".join(ALL_ALLOWED_SIC_CODES)}
    items = paged_get_items(client, "/advanced-search/companies", SEARCH_PAGE_SIZE, params)
    filtered = [item for item in items if any(str(code) in ALL_ALLOWED_SIC_CODES for code in (item.get("sic_codes") or [])) and item.get("company_status", "").lower() == "active" and is_allowed_company_type(item.get("company_type", ""))]
    deduped = {item["company_number"]: item for item in filtered if item.get("company_number")}
    return list(deduped.values()), {"raw_results": len(items), "filtered_results": len(filtered), "deduped_results": len(deduped), "company_types_sent": ", ".join(ALLOWED_COMPANY_TYPES), "sic_count": len(ALL_ALLOWED_SIC_CODES)}


def get_all_officers(client: CHClient, company_number: str) -> List[Dict[str, Any]]:
    return paged_get_items(client, f"/company/{company_number}/officers", OFFICERS_PAGE_SIZE)


def get_all_pscs(client: CHClient, company_number: str) -> List[Dict[str, Any]]:
    return paged_get_items(client, f"/company/{company_number}/persons-with-significant-control", PSC_PAGE_SIZE)


def collect_international_director_details(client: CHClient, company_number: str) -> Tuple[bool, List[str], int]:
    matches: List[str] = []
    director_count = 0
    for officer in get_all_officers(client, company_number):
        role = normalize_text(officer.get("officer_role"))
        if "director" not in role and role != "designated member":
            continue
        director_count += 1
        for value in [officer.get("country_of_residence"), (officer.get("address") or {}).get("country"), officer.get("nationality")]:
            if canonical_country_from_value(value):
                matches.append(str(value))
    matches = dedupe_preserve_order(matches)
    return bool(matches), matches, director_count


def analyse_psc_flags(client: CHClient, company_number: str) -> Tuple[bool, List[str], bool, List[str]]:
    shareholder_matches: List[str] = []
    owner_names: List[str] = []
    for psc in get_all_pscs(client, company_number):
        kind = str(psc.get("kind", ""))
        for value in [psc.get("country_of_residence"), (psc.get("address") or {}).get("country"), psc.get("nationality")]:
            if canonical_country_from_value(value):
                shareholder_matches.append(str(value))
        if kind in COMPANY_OWNER_KINDS or "corporate" in kind or "legal-person" in kind:
            name = str(psc.get("name") or "").strip()
            if name:
                owner_names.append(name)
    return bool(shareholder_matches), dedupe_preserve_order(shareholder_matches), bool(owner_names), dedupe_preserve_order(owner_names)


def parse_matching_sic(item: Dict[str, Any]) -> str:
    codes = [str(code) for code in (item.get("sic_codes") or [])]
    return ", ".join([code for code in codes if code in ALL_ALLOWED_SIC_CODES] or codes[:1])


def is_target_sic(item: Dict[str, Any]) -> bool:
    return any(str(code) in TARGET_SIC_CODES for code in (item.get("sic_codes") or []))


def has_bonus_star(values: List[str]) -> bool:
    return bool({canonical_country_from_value(value) for value in values} & BONUS_STAR_COUNTRIES)


def is_target_address(item: Dict[str, Any]) -> Tuple[bool, str]:
    address = item.get("registered_office_address") or item.get("address") or {}
    country = canonical_country_from_value(address.get("country"))
    return (True, f"✓ {COUNTRY_FLAG_MAP.get(country, '🏠')} {country_label(country)}") if country else (False, "")


def build_target_indicators(target_sic: bool, target_address: bool, director_details: List[str], shareholder_details: List[str], director_count: int) -> str:
    indicators: List[str] = []
    if target_sic:
        indicators.append("🎯")
    if target_address:
        indicators.append("🏠")
    for flag in extract_country_flags(director_details) + extract_country_flags(shareholder_details):
        if flag not in indicators:
            indicators.append(flag)
    if director_count >= 2:
        indicators.append({2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣", 6: "6️⃣", 7: "7️⃣", 8: "8️⃣", 9: "9️⃣", 10: "🔟"}.get(min(director_count, 10), "#️⃣"))
    return " ".join(indicators)


def build_rating(international_director: bool, international_shareholder: bool, owned_by_company: bool, target_sic: bool, director_details: List[str], shareholder_details: List[str]) -> str:
    stars = sum([international_director, international_shareholder, owned_by_company, target_sic])
    if has_bonus_star(director_details) or has_bonus_star(shareholder_details):
        stars += 1
    return "⭐" * stars


def process_company(client: CHClient, item: Dict[str, Any]) -> Dict[str, Any]:
    company_number = item.get("company_number", "")
    company_name = item.get("company_name") or item.get("title") or ""
    international_director, director_details, director_count = collect_international_director_details(client, company_number)
    international_shareholder, shareholder_details, owned_by_company, owner_names = analyse_psc_flags(client, company_number)
    target_sic = is_target_sic(item)
    target_address, target_address_detail = is_target_address(item)
    return {"company_number": company_number, "company_name": company_name, "sic_code": parse_matching_sic(item), "incorporation_date": item.get("date_of_creation", ""), "company_type": item.get("company_type", ""), "international_director": international_director, "international_director_detail": format_flagged_countries(director_details), "international_shareholder": international_shareholder, "international_shareholder_detail": format_flagged_countries(shareholder_details), "owned_by_company": owned_by_company, "owner_company_name": " | ".join(f"✓ {name}" for name in owner_names), "pulled_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"), "raw_json": item, "profile_url": make_company_profile_url(company_number, company_name), "shortlisted": False, "target_sic": target_sic, "target_address": target_address, "target_address_detail": target_address_detail, "target_indicators": build_target_indicators(target_sic, target_address, director_details, shareholder_details, director_count)}


def build_display_df(db_df: pd.DataFrame) -> pd.DataFrame:
    columns = ["Company Name", "Google Funding Search", "Funding secured?", "NAB'd", "Owned by B/J", "Shortlist", "Incorporation Date", "Target SIC", "Rating", "Target Indicators", "SIC Code", "Signals", "International Director", "International Shareholder", "Owned By A Company", "Profile", "Pulled At", "company_number"]
    if db_df.empty:
        return pd.DataFrame(columns=columns)
    rows = []
    for _, row in db_df.iterrows():
        director_values = [value.strip() for value in str(row.get("international_director_detail", "")).split("|") if value.strip()]
        shareholder_values = [value.strip() for value in str(row.get("international_shareholder_detail", "")).split("|") if value.strip()]
        signals = []
        for flag in extract_country_flags(director_values) + extract_country_flags(shareholder_values):
            if flag not in signals:
                signals.append(flag)
        if str(row.get("owner_company_name", "")).startswith("✓"):
            signals.append("🏢")
        company_name = row.get("company_name", "")
        rows.append({"Company Name": company_name, "Google Funding Search": make_google_funding_search_url(company_name), "Funding secured?": bool(row.get("funding_secured", 0)), "NAB'd": bool(row.get("nabd", 0)), "Owned by B/J": bool(row.get("owned_by_bj", 0)), "Shortlist": bool(row.get("shortlisted", 0)), "Incorporation Date": row.get("incorporation_date", ""), "Target SIC": "🎯" if bool(row.get("target_sic", 0)) else "", "Rating": build_rating(bool(extract_country_flags(director_values)), bool(extract_country_flags(shareholder_values)), str(row.get("owner_company_name", "")).startswith("✓"), bool(row.get("target_sic", 0)), director_values, shareholder_values), "Target Indicators": row.get("target_indicators", ""), "SIC Code": row.get("sic_code", ""), "Signals": " ".join(signals), "International Director": row.get("international_director_detail", ""), "International Shareholder": row.get("international_shareholder_detail", ""), "Owned By A Company": row.get("owner_company_name", ""), "Profile": row.get("profile_url", ""), "Pulled At": row.get("pulled_at", ""), "company_number": row.get("company_number", "")})
    return pd.DataFrame(rows, columns=columns)


def apply_filters(df: pd.DataFrame, only_flagged: bool, selected_signals: List[str], sic_search: str, company_name_search: str, shortlisted_only: bool, hide_mfg_wholesale: bool) -> pd.DataFrame:
    filtered = df.copy()
    if shortlisted_only:
        filtered = filtered[filtered["Shortlist"]].copy()
    if only_flagged:
        mask = pd.Series(False, index=filtered.index)
        if "International Director" in selected_signals:
            mask |= filtered["International Director"].astype(str).str.startswith("✓", na=False)
        if "International Shareholder" in selected_signals:
            mask |= filtered["International Shareholder"].astype(str).str.startswith("✓", na=False)
        if "Owned By A Company" in selected_signals:
            mask |= filtered["Owned By A Company"].astype(str).str.startswith("✓", na=False)
        filtered = filtered[mask].copy()
    if sic_search.strip():
        filtered = filtered[filtered["SIC Code"].astype(str).str.contains(re.escape(sic_search.strip()), case=False, na=False)].copy()
    if company_name_search.strip():
        filtered = filtered[filtered["Company Name"].astype(str).str.contains(re.escape(company_name_search.strip()), case=False, na=False)].copy()
    if hide_mfg_wholesale:
        filtered = filtered[~filtered["SIC Code"].astype(str).apply(lambda s: any(code.strip() in MANUFACTURING_WHOLESALE_SIC_CODES for code in s.split(",")))].copy()
    return filtered


def render_kpis(display_df: pd.DataFrame) -> None:
    total = len(display_df)
    director = int(display_df["International Director"].astype(str).str.startswith("✓", na=False).sum()) if total else 0
    shareholder = int(display_df["International Shareholder"].astype(str).str.startswith("✓", na=False).sum()) if total else 0
    flagged = int((display_df["International Director"].astype(str).str.startswith("✓", na=False) | display_df["International Shareholder"].astype(str).str.startswith("✓", na=False) | display_df["Owned By A Company"].astype(str).str.startswith("✓", na=False)).sum()) if total else 0
    shortlisted = int(display_df["Shortlist"].sum()) if total else 0
    target_sics = int(display_df["Target SIC"].eq("🎯").sum()) if total else 0
    for column, label, value in zip(st.columns(6), ["Total Results", "Flagged Rows", "Intl Directors", "Intl Shareholders", "Target SICs", "Shortlisted"], [total, flagged, director, shareholder, target_sics, shortlisted]):
        column.metric(label, f"{value:,}")


def render_sidebar(default_start: date, default_end: date) -> Tuple[date, date, bool, List[str], str, str, bool, bool, bool]:
    with st.sidebar:
        st.header("Screening controls")
        start_date = st.date_input("Incorporation date from", value=default_start, format="YYYY-MM-DD")
        end_date = st.date_input("Incorporation date to", value=default_end, format="YYYY-MM-DD")
        run = st.button("Pull new companies", type="primary", use_container_width=True)
        st.divider()
        st.subheader("Result filters")
        only_flagged = st.checkbox("Show only flagged rows", value=False)
        selected_signals = st.multiselect("Signals", options=SIGNAL_OPTIONS, default=SIGNAL_OPTIONS)
        hide_mfg_wholesale = st.checkbox("Hide Manufacturing & Wholesale SICs", value=False)
        sic_search = st.text_input("Filter by SIC code", placeholder="e.g. 62012")
        company_name_search = st.text_input("Filter by company name", placeholder="e.g. Labs")
        shortlisted_only = st.checkbox("Show shortlisted only", value=False)
    return start_date, end_date, run, selected_signals, sic_search, company_name_search, only_flagged, shortlisted_only, hide_mfg_wholesale


def main() -> None:
    apply_custom_css()
    st.title("Companies House New Incorporations Screener")
    st.caption("Pull newly incorporated active companies, screen target SIC codes, and enrich results with officer and PSC checks.")
    st.markdown('<div class="app-note">Designed for rapid lead triage: select a date range, run the pull, filter signals, shortlist candidates, and open Companies House profiles.</div>', unsafe_allow_html=True)
    with st.expander("Secrets format", expanded=False):
        st.code('COMPANIES_HOUSE_API_KEYS = [\n  "key-1",\n  "key-2"\n]', language="toml")
    try:
        api_keys = validate_api_keys()
    except Exception as exc:
        st.error(str(exc))
        st.stop()
    conn = init_db()
    client = CHClient(api_keys)
    start_date, end_date, run, selected_signals, sic_search, company_name_search, only_flagged, shortlisted_only, hide_mfg_wholesale = render_sidebar(date.today(), date.today())
    if start_date > end_date:
        st.sidebar.error("The 'from' date must be on or before the 'to' date.")
        st.stop()
    start_date_str = start_date.isoformat()
    end_date_str = end_date.isoformat()
    range_label = start_date_str if start_date_str == end_date_str else f"{start_date_str} to {end_date_str}"
    if run:
        failures: List[str] = []
        with st.status("Running Companies House screening...", expanded=True) as status:
            st.write(f"Searching incorporation dates from {start_date_str} to {end_date_str}.")
            companies, diagnostics = search_new_companies(client, start_date_str, end_date_str)
            already_seen = existing_company_numbers(conn, start_date_str, end_date_str)
            new_companies = [company for company in companies if company.get("company_number") not in already_seen]
            st.write(f"Raw search results: {diagnostics['raw_results']}")
            st.write(f"Filtered results retained: {diagnostics['filtered_results']}")
            st.write(f"Deduped company numbers: {diagnostics['deduped_results']}")
            st.write(f"Already screened for {range_label}: {len(already_seen)}")
            st.write(f"New companies to enrich: {len(new_companies)}")
            progress = st.progress(0)
            for index, item in enumerate(new_companies, start=1):
                company_number = item.get("company_number", "unknown")
                try:
                    upsert_company(conn, process_company(client, item))
                except Exception as exc:
                    failures.append(f"{company_number}: {exc}")
                progress.progress(index / max(len(new_companies), 1))
            if failures:
                st.warning(f"Failed enrichments: {len(failures)}")
                st.code("\n".join(failures[:50]))
                status.update(label="Completed with some errors", state="error")
            else:
                status.update(label="Refresh complete", state="complete")
    display_df = build_display_df(read_db_rows(conn, start_date_str, end_date_str))
    render_kpis(display_df)
    filtered_df = apply_filters(display_df, only_flagged, selected_signals, sic_search, company_name_search, shortlisted_only, hide_mfg_wholesale)
    tab_results, tab_shortlist, tab_settings = st.tabs(["Results", "Shortlist", "Settings"])
    with tab_results:
        st.subheader("Results")
        st.caption(f"Loaded {len(api_keys)} API key(s) for {range_label}. {len(filtered_df):,} rows currently visible after filters.")
        editable_columns = ["Company Name", "Google Funding Search", "Funding secured?", "NAB'd", "Owned by B/J", "Shortlist", "Incorporation Date", "Target SIC", "Rating", "Target Indicators", "SIC Code", "Signals", "International Director", "International Shareholder", "Owned By A Company", "Profile", "Pulled At", "company_number"]
        editor_df = filtered_df[editable_columns].copy()
        edited_df = st.data_editor(editor_df, use_container_width=True, hide_index=True, disabled=[column for column in editable_columns if column not in set(CHECKBOX_COLUMNS)], column_config={"Funding secured?": st.column_config.CheckboxColumn("Funding secured?"), "NAB'd": st.column_config.CheckboxColumn("NAB'd"), "Owned by B/J": st.column_config.CheckboxColumn("Owned by B/J"), "Shortlist": st.column_config.CheckboxColumn("Shortlist"), "Google Funding Search": st.column_config.LinkColumn("Google Funding Search", display_text="Search funding"), "Profile": st.column_config.LinkColumn("Profile", display_text="Open record"), "company_number": None}, key=f"results_editor_{start_date_str}_{end_date_str}")
        if not edited_df.empty:
            changes = edited_df[["company_number"] + CHECKBOX_COLUMNS].merge(display_df[["company_number"] + CHECKBOX_COLUMNS], on="company_number", suffixes=("_new", "_old"), how="left")
            changed_rows = []
            for _, changed_row in changes.iterrows():
                for display_column in CHECKBOX_COLUMNS:
                    if bool(changed_row[f"{display_column}_new"]) != bool(changed_row[f"{display_column}_old"]):
                        changed_rows.append((changed_row["company_number"], DB_CHECKBOX_COLUMNS[display_column], bool(changed_row[f"{display_column}_new"])))
            for company_number, db_column, checked in changed_rows:
                set_company_checkbox_state(conn, company_number, db_column, checked)
            if changed_rows:
                st.rerun()
        st.download_button("Download filtered CSV", filtered_df.drop(columns=["company_number"], errors="ignore").to_csv(index=False).encode("utf-8"), f"companies_house_screening_{start_date_str}_{end_date_str}.csv", "text/csv", use_container_width=True)
    with tab_shortlist:
        shortlist_df = display_df[display_df["Shortlist"]].copy()
        if shortlist_df.empty:
            st.info("No shortlisted companies yet. Tick the shortlist checkbox in the Results tab to build a follow-up queue.")
        else:
            st.dataframe(shortlist_df.drop(columns=["company_number"], errors="ignore"), use_container_width=True, hide_index=True, column_config={"Funding secured?": st.column_config.CheckboxColumn("Funding secured?", disabled=True), "NAB'd": st.column_config.CheckboxColumn("NAB'd", disabled=True), "Owned by B/J": st.column_config.CheckboxColumn("Owned by B/J", disabled=True), "Google Funding Search": st.column_config.LinkColumn("Google Funding Search", display_text="Search funding"), "Profile": st.column_config.LinkColumn("Profile", display_text="Open record")})
            st.download_button("Download shortlist CSV", shortlist_df.drop(columns=["company_number"], errors="ignore").to_csv(index=False).encode("utf-8"), f"companies_house_shortlist_{start_date_str}_{end_date_str}.csv", "text/csv", use_container_width=True)
    with tab_settings:
        st.subheader("Current search settings")
        st.markdown(f"""
- Incorporation date range: `{range_label}`
- Company status: Active
- Company types sent to API: `{', '.join(ALLOWED_COMPANY_TYPES)}`
- SIC codes sent to API: {len(ALL_ALLOWED_SIC_CODES)}
- Target SIC codes: `{', '.join(sorted(TARGET_SIC_CODES))}`
- Dedupe rule: company numbers already screened within the selected date range are skipped
        """)


if __name__ == "__main__":
    main()
