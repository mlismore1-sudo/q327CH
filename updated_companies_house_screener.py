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
    "62012", "62020", "63110", "63120", "47910", "46190", "46499", "70229", "73110", "74909", "68209",
    "64209", "68100", "32990", "10890", "86900", "93130", "96040", "82990", "72110", "56101", "58290",
]
TARGET_SIC_CODES = {"62012", "62020", "72110", "63110", "58290"}
TARGET_ADDRESS_TERMS = [
    "86-90 Paul Street",
    "128 City Road",
    "71-75 Shelton Street",
    "Strand",
    "Southwark Bridge Road",
    "66 Paul Street",
    "W12",
]
BONUS_STAR_COUNTRIES = {"sweden", "norway", "united states"}
EXCLUDED_ACSPS = {
    "tide platform ltd acsp",
    "anna admin limited acsp",
}
PARTNER_FORMATIONS_ACSPS = {
    "made simple group limited acsp",
    "1st formations limited acsp",
    "icon offices limited acsp",
}
ALLOWED_COMPANY_TYPES = [
    "ltd",
    "llp",
    "private-limited-guarant-nsc",
    "private-limited-shares-section-30-exemption",
]
COUNTRY_TERMS = {
    "usa", "united states", "united states of america", "france", "germany", "belgium", "norway",
    "sweden", "finland", "denmark", "austria", "poland", "spain", "portugal", "greece", "italy",
    "hungary", "croatia", "ireland", "netherlands", "india", "hong kong", "singapore",
}
NATIONALITY_TERMS = {
    "american", "us", "united states", "united states of america", "french", "german", "belgian",
    "norwegian", "swedish", "finnish", "danish", "austrian", "polish", "spanish", "portuguese",
    "greek", "italian", "hungarian", "croatian", "irish", "indian", "hong kong",
    "hongkong", "singaporean", "dutch", "netherlands",
}
COMPANY_OWNER_KINDS = {
    "corporate-entity-person-with-significant-control",
    "legal-person-person-with-significant-control",
    "super-secure-person-with-significant-control",
}
COUNTRY_FLAG_MAP = {
    "united states": "🇺🇸",
    "france": "🇫🇷",
    "germany": "🇩🇪",
    "belgium": "🇧🇪",
    "norway": "🇳🇴",
    "sweden": "🇸🇪",
    "finland": "🇫🇮",
    "denmark": "🇩🇰",
    "austria": "🇦🇹",
    "poland": "🇵🇱",
    "spain": "🇪🇸",
    "portugal": "🇵🇹",
    "greece": "🇬🇷",
    "italy": "🇮🇹",
    "hungary": "🇭🇺",
    "croatia": "🇭🇷",
    "ireland": "🇮🇪",
    "netherlands": "🇳🇱",
    "india": "🇮🇳",
    "hong kong": "🇭🇰",
    "singapore": "🇸🇬",
}
NATIONALITY_TO_COUNTRY = {
    "american": "united states",
    "us": "united states",
    "united states": "united states",
    "french": "france",
    "german": "germany",
    "belgian": "belgium",
    "norwegian": "norway",
    "swedish": "sweden",
    "finnish": "finland",
    "danish": "denmark",
    "austrian": "austria",
    "polish": "poland",
    "spanish": "spain",
    "portuguese": "portugal",
    "greek": "greece",
    "italian": "italy",
    "hungarian": "hungary",
    "croatian": "croatia",
    "irish": "ireland",
    "indian": "india",
    "hong kong": "hong kong",
    "hongkong": "hong kong",
    "singaporean": "singapore",
    "dutch": "netherlands",
    "netherlands": "netherlands",
}
SIGNAL_OPTIONS = [
    "International Director",
    "International Shareholder",
    "Owned By A Company",
    "Target Address",
    "Partner Formations Company",
]


def apply_custom_css() -> None:
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"][aria-expanded="true"] > div:first-child {
            width: 340px;
        }
        div[data-testid="metric-container"] {
            background: linear-gradient(180deg, rgba(14, 17, 23, 0.03), rgba(14, 17, 23, 0.01));
            border: 1px solid rgba(120, 120, 120, 0.18);
            padding: 14px 16px;
            border-radius: 14px;
        }
        .signal-legend {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
            margin: 0.5rem 0 0.25rem 0;
        }
        .signal-pill {
            border: 1px solid rgba(120, 120, 120, 0.2);
            border-radius: 999px;
            padding: 6px 10px;
            font-size: 0.85rem;
            background: rgba(49, 51, 63, 0.04);
        }
        .app-note {
            padding: 0.85rem 1rem;
            border-radius: 12px;
            border: 1px solid rgba(120, 120, 120, 0.18);
            background: rgba(49, 51, 63, 0.04);
            margin-bottom: 1rem;
        }
        .excluded-note {
            padding: 0.75rem 1rem;
            border-radius: 12px;
            border: 1px solid rgba(180, 90, 90, 0.22);
            background: rgba(180, 90, 90, 0.06);
            margin: 0.75rem 0 1rem 0;
            font-size: 0.92rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower()
    text = text.replace("-", " ")
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    aliases = {
        "usa": "united states",
        "u s a": "united states",
        "u s": "us",
        "united states of america": "united states",
        "america": "american",
        "hong kong": "hong kong",
        "hongkong": "hong kong",
        "the netherlands": "netherlands",
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
    if norm in NATIONALITY_TO_COUNTRY:
        return NATIONALITY_TO_COUNTRY[norm]
    return ""


def dedupe_preserve_order(values: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        norm = normalize_text(value)
        if norm and norm not in seen:
            seen.add(norm)
            out.append(value)
    return out


def country_label(value: str) -> str:
    if value == "united states":
        return "USA"
    if value == "hong kong":
        return "Hong Kong"
    return value.title()


def format_flagged_countries(values: List[str]) -> str:
    canonical_values = dedupe_preserve_order([
        canonical_country_from_value(v) for v in values if canonical_country_from_value(v)
    ])
    if not canonical_values:
        return ""
    parts = [f"✓ {COUNTRY_FLAG_MAP.get(v, '🌍')} {country_label(v)}" for v in canonical_values]
    return " | ".join(parts)


def format_flagged_addresses(values: List[str]) -> str:
    deduped = dedupe_preserve_order(values)
    if not deduped:
        return ""
    return " | ".join([f"✓ {value}" for value in deduped])


def make_company_profile_url(company_number: str, company_name: str) -> str:
    safe_name = quote(company_name or "company")
    return f"https://find-and-update.company-information.service.gov.uk/company/{company_number}#{safe_name}"


class CHClient:
    def __init__(self, api_keys: List[str]):
        self.api_keys = [k.strip() for k in api_keys if str(k).strip()]
        if not self.api_keys:
            raise ValueError("No Companies House API keys supplied.")
        self.idx = 0
        self.session = requests.Session()

    def _auth(self) -> Tuple[str, str]:
        return (self.api_keys[self.idx % len(self.api_keys)], "")

    def _rotate(self) -> None:
        self.idx = (self.idx + 1) % len(self.api_keys)

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        last_error = None
        for _ in range(max(len(self.api_keys) * 3, 3)):
            try:
                response = self.session.get(
                    f"{BASE_URL}{path}",
                    params=params,
                    auth=self._auth(),
                    timeout=30,
                    headers={"Accept": "application/json"},
                )
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
            raw_json TEXT
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
    ensure_column(conn, "screened_companies", "high_sign_up_potential", "TEXT")
    ensure_column(conn, "screened_companies", "director_verification_acsp", "TEXT")
    ensure_column(conn, "screened_companies", "partner_formations_company", "INTEGER DEFAULT 0")
    ensure_column(conn, "screened_companies", "excluded", "INTEGER DEFAULT 0")
    ensure_column(conn, "screened_companies", "exclusion_reason", "TEXT")
    return conn


def existing_company_numbers(conn: sqlite3.Connection, incorporation_date: str) -> set:
    rows = conn.execute(
        "SELECT company_number FROM screened_companies WHERE incorporation_date = ?",
        (incorporation_date,),
    ).fetchall()
    return {r[0] for r in rows}


def set_shortlisted_state(conn: sqlite3.Connection, company_number: str, shortlisted: bool) -> None:
    conn.execute(
        "UPDATE screened_companies SET shortlisted = ? WHERE company_number = ?",
        (int(shortlisted), company_number),
    )
    conn.commit()


def upsert_company(conn: sqlite3.Connection, row: Dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO screened_companies (
            company_number, company_name, sic_code, incorporation_date, company_type,
            international_director, international_director_detail,
            international_shareholder, international_shareholder_detail,
            owned_by_company, owner_company_name,
            pulled_at, raw_json, profile_url, shortlisted, target_sic,
            target_address, target_address_detail, high_sign_up_potential,
            director_verification_acsp, partner_formations_company, excluded, exclusion_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["company_number"],
            row["company_name"],
            row["sic_code"],
            row["incorporation_date"],
            row["company_type"],
            int(row["international_director"]),
            row.get("international_director_detail", ""),
            int(row["international_shareholder"]),
            row.get("international_shareholder_detail", ""),
            int(row["owned_by_company"]),
            row.get("owner_company_name", ""),
            row["pulled_at"],
            json.dumps(row.get("raw_json", {})),
            row.get("profile_url", ""),
            int(row.get("shortlisted", False)),
            int(row.get("target_sic", False)),
            int(row.get("target_address", False)),
            row.get("target_address_detail", ""),
            row.get("high_sign_up_potential", ""),
            row.get("director_verification_acsp", ""),
            int(row.get("partner_formations_company", False)),
            int(row.get("excluded", False)),
            row.get("exclusion_reason", ""),
        ),
    )
    conn.commit()


def read_db_rows(conn: sqlite3.Connection, incorporation_date: Optional[str] = None) -> pd.DataFrame:
    if incorporation_date:
        return pd.read_sql_query(
            "SELECT * FROM screened_companies WHERE incorporation_date = ? ORDER BY pulled_at DESC",
            conn,
            params=(incorporation_date,),
        )
    return pd.read_sql_query("SELECT * FROM screened_companies ORDER BY pulled_at DESC", conn)


def validate_api_keys() -> List[str]:
    if "COMPANIES_HOUSE_API_KEYS" not in st.secrets:
        raise ValueError("Missing COMPANIES_HOUSE_API_KEYS in .streamlit/secrets.toml")
    keys = [str(k).strip() for k in list(st.secrets["COMPANIES_HOUSE_API_KEYS"]) if str(k).strip()]
    if not keys:
        raise ValueError("COMPANIES_HOUSE_API_KEYS is empty")
    return keys


def paged_get_items(client: CHClient, path: str, page_size: int, extra_params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    start_index = 0
    while True:
        params = {"start_index": start_index}
        if extra_params:
            params.update(extra_params)
        if path == "/advanced-search/companies":
            params["size"] = page_size
        else:
            params["items_per_page"] = page_size
        payload = client.get(path, params=params)
        batch = payload.get("items", []) or []
        items.extend(batch)
        total = payload.get("total_results")
        if total is None:
            total = payload.get("total_count")
        total = int(total or len(items))
        start_index += page_size
        if not batch or start_index >= total:
            break
    return items


def is_allowed_company_type(value: Any) -> bool:
    return normalize_text(value) in NORMALIZED_ALLOWED_COMPANY_TYPES


def search_new_companies(client: CHClient, target_date: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    params = {
        "incorporated_from": target_date,
        "incorporated_to": target_date,
        "company_status": "active",
        "company_type": ",".join(ALLOWED_COMPANY_TYPES),
        "sic_codes": ",".join(ALLOWED_SIC_CODES),
    }
    items = paged_get_items(client, "/advanced-search/companies", SEARCH_PAGE_SIZE, params)
    filtered: List[Dict[str, Any]] = []
    for item in items:
        item_sics = [str(x) for x in (item.get("sic_codes") or [])]
        if not any(code in ALLOWED_SIC_CODES for code in item_sics):
            continue
        if item.get("company_status", "").lower() != "active":
            continue
        if not is_allowed_company_type(item.get("company_type", "")):
            continue
        filtered.append(item)
    deduped = {}
    for item in filtered:
        number = item.get("company_number")
        if number:
            deduped[number] = item
    diagnostics = {
        "raw_results": len(items),
        "filtered_results": len(filtered),
        "deduped_results": len(deduped),
        "company_types_sent": ", ".join(ALLOWED_COMPANY_TYPES),
        "sic_count": len(ALLOWED_SIC_CODES),
    }
    return list(deduped.values()), diagnostics


def get_all_officers(client: CHClient, company_number: str) -> List[Dict[str, Any]]:
    return paged_get_items(client, f"/company/{company_number}/officers", OFFICERS_PAGE_SIZE)


def get_all_pscs(client: CHClient, company_number: str) -> List[Dict[str, Any]]:
    return paged_get_items(client, f"/company/{company_number}/persons-with-significant-control", PSC_PAGE_SIZE)


def collect_international_director_details(client: CHClient, company_number: str) -> Tuple[bool, List[str], List[Dict[str, Any]]]:
    officers = get_all_officers(client, company_number)
    matches: List[str] = []
    director_officers: List[Dict[str, Any]] = []
    for officer in officers:
        role = normalize_text(officer.get("officer_role"))
        if "director" not in role and role != "designated member":
            continue
        director_officers.append(officer)
        for value in [
            officer.get("country_of_residence"),
            (officer.get("address") or {}).get("country"),
            officer.get("nationality"),
        ]:
            if canonical_country_from_value(value):
                matches.append(str(value))
    deduped = dedupe_preserve_order(matches)
    return bool(deduped), deduped, director_officers


def analyse_psc_flags(client: CHClient, company_number: str) -> Tuple[bool, List[str], bool, List[str]]:
    pscs = get_all_pscs(client, company_number)
    shareholder_matches: List[str] = []
    owner_names: List[str] = []
    for psc in pscs:
        kind = str(psc.get("kind", ""))
        for value in [
            psc.get("country_of_residence"),
            (psc.get("address") or {}).get("country"),
            psc.get("nationality"),
        ]:
            if canonical_country_from_value(value):
                shareholder_matches.append(str(value))
        if kind in COMPANY_OWNER_KINDS or "corporate" in kind or "legal-person" in kind:
            owner_name = str(psc.get("name") or "").strip()
            if owner_name:
                owner_names.append(owner_name)
    deduped_shareholders = dedupe_preserve_order(shareholder_matches)
    deduped_owners = dedupe_preserve_order(owner_names)
    return bool(deduped_shareholders), deduped_shareholders, bool(deduped_owners), deduped_owners


def extract_company_address(item: Dict[str, Any]) -> str:
    address = item.get("registered_office_address") or item.get("address") or {}
    parts = [
        address.get("premises"),
        address.get("address_line_1"),
        address.get("address_line_2"),
        address.get("locality"),
        address.get("region"),
        address.get("postal_code"),
        address.get("country"),
    ]
    return ", ".join([str(part).strip() for part in parts if str(part).strip()])


def match_target_address(item: Dict[str, Any]) -> Tuple[bool, List[str], str]:
    full_address = extract_company_address(item)
    normalized_address = normalize_text(full_address)
    if not normalized_address:
        return False, [], full_address
    matches = [term for term in TARGET_ADDRESS_TERMS if normalize_text(term) in normalized_address]
    deduped = dedupe_preserve_order(matches)
    return bool(deduped), deduped, full_address


def walk_strings(value: Any) -> List[str]:
    results: List[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_norm = normalize_text(key)
            if isinstance(child, str):
                child_text = child.strip()
                child_norm = normalize_text(child_text)
                if any(token in key_norm for token in ["verify", "verification", "provider", "acsp", "authorised", "authorized", "identity"]):
                    results.append(child_text)
                elif "acsp" in child_norm:
                    results.append(child_text)
            else:
                results.extend(walk_strings(child))
    elif isinstance(value, list):
        for child in value:
            results.extend(walk_strings(child))
    return results


def extract_director_verification_acsp(director_officers: List[Dict[str, Any]]) -> List[str]:
    matches: List[str] = []
    priority_keys = [
        "verified_by",
        "verified_by_name",
        "identity_verification_provider",
        "identity_verification_provider_name",
        "acsp_name",
        "authorized_corporate_service_provider_name",
        "authorised_corporate_service_provider_name",
        "identity_verified_by",
        "identity_verification",
        "verification_details",
    ]
    for officer in director_officers:
        for key in priority_keys:
            value = officer.get(key)
            if isinstance(value, str) and value.strip():
                matches.append(value.strip())
            elif isinstance(value, (dict, list)):
                matches.extend(walk_strings(value))
        matches.extend(walk_strings(officer))
    return dedupe_preserve_order(matches)


def should_exclude_company(acsp_names: List[str]) -> bool:
    normalized = {normalize_text(name) for name in acsp_names if normalize_text(name)}
    return bool(normalized & EXCLUDED_ACSPS)


def matched_excluded_acsp(acsp_names: List[str]) -> str:
    normalized = {normalize_text(name): name for name in acsp_names if normalize_text(name)}
    matches = [normalized[key] for key in normalized if key in EXCLUDED_ACSPS]
    return " | ".join(dedupe_preserve_order(matches))


def is_partner_formations_company(acsp_names: List[str]) -> bool:
    normalized = {normalize_text(name) for name in acsp_names if normalize_text(name)}
    return bool(normalized & PARTNER_FORMATIONS_ACSPS)


def parse_matching_sic(item: Dict[str, Any]) -> str:
    item_sics = [str(code) for code in (item.get("sic_codes") or [])]
    matched = [code for code in item_sics if code in ALLOWED_SIC_CODES or code in TARGET_SIC_CODES]
    return ", ".join(matched or item_sics[:1])


def is_target_sic(item: Dict[str, Any]) -> bool:
    item_sics = [str(code) for code in (item.get("sic_codes") or [])]
    return any(code in TARGET_SIC_CODES for code in item_sics)


def has_bonus_star(values: List[str]) -> bool:
    canonical_values = {canonical_country_from_value(v) for v in values if canonical_country_from_value(v)}
    return bool(canonical_values & BONUS_STAR_COUNTRIES)


def build_rating(
    international_director: bool,
    international_shareholder: bool,
    owned_by_company: bool,
    target_sic: bool,
    target_address: bool,
    partner_formations_company: bool,
    director_details: List[str],
    shareholder_details: List[str],
) -> str:
    stars = 0
    if international_director:
        stars += 1
    if international_shareholder:
        stars += 1
    if owned_by_company:
        stars += 1
    if target_sic:
        stars += 1
    if target_address:
        stars += 1
    if partner_formations_company:
        stars += 1
    if has_bonus_star(director_details) or has_bonus_star(shareholder_details):
        stars += 1
    return "⭐" * stars


def build_high_sign_up_potential(target_sic: bool, target_address: bool, partner_formations_company: bool) -> str:
    bolts = 0
    if target_sic and target_address:
        bolts += 1
    if partner_formations_company:
        bolts += 1
    return "⚡" * bolts


def process_company(client: CHClient, item: Dict[str, Any], target_date: str) -> Dict[str, Any]:
    company_number = item.get("company_number", "")
    company_name = item.get("company_name") or item.get("title") or ""
    international_director, director_details, director_officers = collect_international_director_details(client, company_number)
    director_verification_acsp_names = extract_director_verification_acsp(director_officers)
    excluded = should_exclude_company(director_verification_acsp_names)
    excluded_match = matched_excluded_acsp(director_verification_acsp_names)

    international_shareholder, shareholder_details, owned_by_company, owner_names = analyse_psc_flags(client, company_number)
    target_sic = is_target_sic(item)
    target_address, target_address_matches, _ = match_target_address(item)
    partner_formations_company = is_partner_formations_company(director_verification_acsp_names)
    high_sign_up_potential = build_high_sign_up_potential(target_sic, target_address, partner_formations_company)
    owner_display = " | ".join([f"✓ {name}" for name in owner_names]) if owner_names else ""
    exclusion_reason = f"ACSP excluded: {excluded_match}" if excluded_match else ("ACSP excluded: Tide/Anna" if excluded else "")

    return {
        "company_number": company_number,
        "company_name": company_name,
        "sic_code": parse_matching_sic(item),
        "incorporation_date": target_date,
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
        "target_address_detail": format_flagged_addresses(target_address_matches),
        "high_sign_up_potential": high_sign_up_potential,
        "director_verification_acsp": " | ".join(director_verification_acsp_names),
        "partner_formations_company": partner_formations_company,
        "excluded": excluded,
        "exclusion_reason": exclusion_reason,
    }


def build_display_df(db_df: pd.DataFrame) -> pd.DataFrame:
    if db_df.empty:
        return pd.DataFrame(columns=[
            "Shortlist", "Excluded", "Exclusion Reason", "Target SIC", "Target Address", "Partner Formations Company", "High Sign Up Potential?", "Rating", "Company Name", "SIC Code", "Signals",
            "International Director", "International Shareholder", "Owned By A Company", "Target Address Detail", "Director Verification ACSP",
            "Profile", "Pulled At", "company_number",
        ])

    signal_labels = []
    rating_series = []
    target_sic_series = db_df.get("target_sic", pd.Series(0, index=db_df.index)).fillna(0).astype(int).astype(bool)
    target_address_series = db_df.get("target_address", pd.Series(0, index=db_df.index)).fillna(0).astype(int).astype(bool)
    partner_series = db_df.get("partner_formations_company", pd.Series(0, index=db_df.index)).fillna(0).astype(int).astype(bool)
    excluded_series = db_df.get("excluded", pd.Series(0, index=db_df.index)).fillna(0).astype(int).astype(bool)
    high_sign_up_series = db_df.get("high_sign_up_potential", pd.Series("", index=db_df.index)).fillna("")

    for idx, row in db_df.iterrows():
        labels = []
        director_flag = bool(int(row.get("international_director", 0) or 0))
        shareholder_flag = bool(int(row.get("international_shareholder", 0) or 0))
        owner_flag = bool(int(row.get("owned_by_company", 0) or 0))
        target_flag = bool(target_sic_series.loc[idx])
        target_address_flag = bool(target_address_series.loc[idx])
        partner_flag = bool(partner_series.loc[idx])
        excluded_flag = bool(excluded_series.loc[idx])

        if director_flag:
            labels.append("Director 🌍")
        if shareholder_flag:
            labels.append("Shareholder 🌍")
        if owner_flag:
            labels.append("Company owner 🏢")
        if target_address_flag:
            labels.append("Target address 📍")
        if partner_flag:
            labels.append("Partner formations ✅")
        if excluded_flag:
            labels.append("Excluded 🚫")
        signal_labels.append(" · ".join(labels))

        director_detail_values = [x.strip() for x in str(row.get("international_director_detail", "")).split("|") if x.strip()]
        shareholder_detail_values = [x.strip() for x in str(row.get("international_shareholder_detail", "")).split("|") if x.strip()]
        rating_series.append(
            build_rating(
                international_director=director_flag,
                international_shareholder=shareholder_flag,
                owned_by_company=owner_flag,
                target_sic=target_flag,
                target_address=target_address_flag,
                partner_formations_company=partner_flag,
                director_details=director_detail_values,
                shareholder_details=shareholder_detail_values,
            )
        )

    return pd.DataFrame({
        "Shortlist": db_df.get("shortlisted", pd.Series(0, index=db_df.index)).fillna(0).astype(int).astype(bool),
        "Excluded": excluded_series.map(lambda x: "🚫" if x else ""),
        "Exclusion Reason": db_df.get("exclusion_reason", pd.Series(dtype=str)).fillna(""),
        "Target SIC": target_sic_series.map(lambda x: "🎯" if x else ""),
        "Target Address": target_address_series.map(lambda x: "📍" if x else ""),
        "Partner Formations Company": partner_series.map(lambda x: "✓" if x else ""),
        "High Sign Up Potential?": high_sign_up_series,
        "Rating": rating_series,
        "Company Name": db_df["company_name"],
        "SIC Code": db_df["sic_code"],
        "Signals": signal_labels,
        "International Director": db_df.get("international_director_detail", pd.Series(dtype=str)).fillna(""),
        "International Shareholder": db_df.get("international_shareholder_detail", pd.Series(dtype=str)).fillna(""),
        "Owned By A Company": db_df.get("owner_company_name", pd.Series(dtype=str)).fillna(""),
        "Target Address Detail": db_df.get("target_address_detail", pd.Series(dtype=str)).fillna(""),
        "Director Verification ACSP": db_df.get("director_verification_acsp", pd.Series(dtype=str)).fillna(""),
        "Profile": db_df.get("profile_url", pd.Series(dtype=str)).fillna(""),
        "Pulled At": db_df["pulled_at"],
        "company_number": db_df["company_number"],
    })


def apply_filters(df: pd.DataFrame, only_flagged: bool, selected_signals: List[str], sic_search: str, company_name_search: str, shortlisted_only: bool) -> pd.DataFrame:
    filtered = df.copy()
    if shortlisted_only and "Shortlist" in filtered.columns:
        filtered = filtered[filtered["Shortlist"] == True].copy()
    if only_flagged:
        mask = pd.Series(False, index=filtered.index)
        if "International Director" in selected_signals:
            mask |= filtered["International Director"].astype(str).str.startswith("✓", na=False)
        if "International Shareholder" in selected_signals:
            mask |= filtered["International Shareholder"].astype(str).str.startswith("✓", na=False)
        if "Owned By A Company" in selected_signals:
            mask |= filtered["Owned By A Company"].astype(str).str.startswith("✓", na=False)
        if "Target Address" in selected_signals:
            mask |= filtered["Target Address"].astype(str).eq("📍")
        if "Partner Formations Company" in selected_signals:
            mask |= filtered["Partner Formations Company"].astype(str).eq("✓")
        filtered = filtered[mask].copy()
    if sic_search.strip():
        filtered = filtered[filtered["SIC Code"].astype(str).str.contains(re.escape(sic_search.strip()), case=False, na=False)].copy()
    if company_name_search.strip():
        filtered = filtered[filtered["Company Name"].astype(str).str.contains(re.escape(company_name_search.strip()), case=False, na=False)].copy()
    return filtered


def render_kpis(display_df: pd.DataFrame) -> None:
    total = len(display_df)
    excluded = int(display_df["Excluded"].astype(str).eq("🚫").sum()) if not display_df.empty else 0
    director = int(display_df["International Director"].astype(str).str.startswith("✓", na=False).sum()) if not display_df.empty else 0
    shareholder = int(display_df["International Shareholder"].astype(str).str.startswith("✓", na=False).sum()) if not display_df.empty else 0
    target_addresses = int(display_df["Target Address"].astype(str).eq("📍").sum()) if not display_df.empty else 0
    partner_companies = int(display_df["Partner Formations Company"].astype(str).eq("✓").sum()) if not display_df.empty else 0
    flagged = int(((display_df["International Director"].astype(str).str.startswith("✓", na=False)) |
                   (display_df["International Shareholder"].astype(str).str.startswith("✓", na=False)) |
                   (display_df["Owned By A Company"].astype(str).str.startswith("✓", na=False)) |
                   (display_df["Target Address"].astype(str).eq("📍")) |
                   (display_df["Partner Formations Company"].astype(str).eq("✓"))).sum()) if not display_df.empty else 0
    shortlisted = int(display_df["Shortlist"].sum()) if not display_df.empty else 0
    target_sics = int(display_df["Target SIC"].astype(str).eq("🎯").sum()) if not display_df.empty else 0

    c1, c2, c3, c4, c5, c6, c7, c8, c9 = st.columns(9)
    c1.metric("Total Results", f"{total:,}")
    c2.metric("Flagged Rows", f"{flagged:,}")
    c3.metric("Excluded", f"{excluded:,}")
    c4.metric("Intl Directors", f"{director:,}")
    c5.metric("Intl Shareholders", f"{shareholder:,}")
    c6.metric("Target SICs", f"{target_sics:,}")
    c7.metric("Target Addresses", f"{target_addresses:,}")
    c8.metric("Partner Formations", f"{partner_companies:,}")
    c9.metric("Shortlisted", f"{shortlisted:,}")


def render_sidebar(default_date: date) -> Tuple[date, bool, List[str], str, str, bool, bool, bool]:
    with st.sidebar:
        st.header("Screening controls")
        target_date = st.date_input("Incorporation date", value=default_date, format="YYYY-MM-DD")
        run = st.button("Pull new companies", type="primary", use_container_width=True)
        st.divider()
        st.subheader("Result filters")
        only_flagged = st.checkbox("Show only flagged rows", value=False)
        show_excluded = st.checkbox("Show excluded Tide/Anna rows", value=False)
        selected_signals = st.multiselect("Signals", options=SIGNAL_OPTIONS, default=SIGNAL_OPTIONS)
        sic_search = st.text_input("Filter by SIC code", placeholder="e.g. 62012")
        company_name_search = st.text_input("Filter by company name", placeholder="e.g. Labs")
        shortlisted_only = st.checkbox("Show shortlisted only", value=False)
        st.divider()
        st.caption("The sidebar keeps controls separate from the results table for faster screening.")
    return target_date, run, selected_signals, sic_search, company_name_search, only_flagged, shortlisted_only, show_excluded


def main() -> None:
    apply_custom_css()
    st.title("Companies House New Incorporations Screener")
    st.caption("Pull newly incorporated active companies, screen target SIC codes, and enrich results with officer, PSC, registered address, and ACSP checks.")

    st.markdown(
        """
        <div class="app-note">
        Designed for rapid lead triage: run the pull, scan KPIs, filter the signals, shortlist candidates, and click through to Companies House profiles.
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.expander("Secrets format", expanded=False):
        st.code('COMPANIES_HOUSE_API_KEYS = [\n  "key-1",\n  "key-2",\n  "key-3"\n]', language="toml")

    try:
        api_keys = validate_api_keys()
    except Exception as exc:
        st.error(str(exc))
        st.stop()

    conn = init_db()
    client = CHClient(api_keys)

    target_date, run, selected_signals, sic_search, company_name_search, only_flagged, shortlisted_only, show_excluded = render_sidebar(date.today())
    date_str = target_date.strftime("%Y-%m-%d")

    if run:
        failures: List[str] = []
        excluded_count = 0
        inserted_count = 0
        with st.status("Running Companies House screening...", expanded=True) as status:
            st.write("Querying advanced search with all SIC codes and company types in one request pattern.")
            companies, diagnostics = search_new_companies(client, date_str)
            already_seen = existing_company_numbers(conn, date_str)
            new_companies = [c for c in companies if c.get("company_number") not in already_seen]
            st.write(f"Raw search results: {diagnostics['raw_results']}")
            st.write(f"Filtered results retained: {diagnostics['filtered_results']}")
            st.write(f"Deduped company numbers: {diagnostics['deduped_results']}")
            st.write(f"Already screened for {date_str}: {len(already_seen)}")
            st.write(f"New companies to enrich: {len(new_companies)}")

            progress = st.progress(0)
            total = max(len(new_companies), 1)
            for idx, item in enumerate(new_companies, start=1):
                company_number = item.get("company_number", "unknown")
                try:
                    row = process_company(client, item, date_str)
                    upsert_company(conn, row)
                    inserted_count += 1
                    if row.get("excluded"):
                        excluded_count += 1
                except Exception as exc:
                    failures.append(f"{company_number}: {exc}")
                progress.progress(min(idx / total, 1.0))

            st.write(f"Rows committed to database: {inserted_count}")
            st.write(f"Excluded due to ACSP rules and committed: {excluded_count}")
            if failures:
                st.warning(f"Failed enrichments: {len(failures)}")
                st.code("\n".join(failures[:50]))
                status.update(label="Completed with some errors", state="error")
            else:
                status.update(label="Refresh complete", state="complete")

    db_df = read_db_rows(conn, date_str)
    display_df = build_display_df(db_df)
    render_kpis(display_df)

    if (display_df["Excluded"].astype(str).eq("🚫").sum() if not display_df.empty else 0) > 0:
        st.markdown(
            "<div class='excluded-note'>Rows marked with 🚫 were verified by Tide or Anna, stored in the database, and will not be re-screened on later refreshes for the same incorporation date.</div>",
            unsafe_allow_html=True,
        )

    st.markdown(
        """
        <div class="signal-legend">
            <div class="signal-pill">Director 🌍 = international director match</div>
            <div class="signal-pill">Shareholder 🌍 = international PSC match</div>
            <div class="signal-pill">Company owner 🏢 = corporate PSC match</div>
            <div class="signal-pill">Target SIC 🎯 = SIC 62012, 62020, 72110, 63110, or 58290</div>
            <div class="signal-pill">Target address 📍 = registered office partially matches a target address</div>
            <div class="signal-pill">Partner formations ✓ = director verified by approved formations partner</div>
            <div class="signal-pill">Excluded 🚫 = verified by Tide or Anna and kept in the database</div>
            <div class="signal-pill">High sign up potential ⚡ = one bolt for target SIC + target address, plus one extra bolt for partner formations company</div>
            <div class="signal-pill">Rating ⭐ = 1 star per signal, including partner formations company, plus a bonus for Sweden, Norway, or USA director/shareholder</div>
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
    )
    if not show_excluded and "Excluded" in filtered_df.columns:
        filtered_df = filtered_df[filtered_df["Excluded"].astype(str) != "🚫"].copy()

    tab_results, tab_shortlist, tab_excluded, tab_settings = st.tabs(["Results", "Shortlist", "Excluded", "Settings"])

    with tab_results:
        st.subheader("Results")
        st.caption(f"Loaded {len(api_keys)} API key(s) for {date_str}. {len(filtered_df):,} active rows currently visible after filters.")
        editor_df = filtered_df[[
            "Shortlist", "Excluded", "Exclusion Reason", "Target SIC", "Target Address", "Partner Formations Company", "High Sign Up Potential?", "Rating", "Company Name", "SIC Code", "Signals",
            "International Director", "International Shareholder", "Owned By A Company", "Target Address Detail", "Director Verification ACSP",
            "Profile", "Pulled At", "company_number",
        ]].copy()

        edited_df = st.data_editor(
            editor_df,
            use_container_width=True,
            hide_index=True,
            disabled=[
                "Excluded", "Exclusion Reason", "Target SIC", "Target Address", "Partner Formations Company", "High Sign Up Potential?", "Rating", "Company Name", "SIC Code", "Signals",
                "International Director", "International Shareholder", "Owned By A Company", "Target Address Detail", "Director Verification ACSP",
                "Profile", "Pulled At", "company_number",
            ],
            column_config={
                "Shortlist": st.column_config.CheckboxColumn("Shortlist", help="Tick to mark this company for follow-up."),
                "Excluded": st.column_config.TextColumn("Excluded", width="small", help="🚫 means Tide or Anna verified. The row is stored and will not be re-screened."),
                "Exclusion Reason": st.column_config.TextColumn("Exclusion Reason", width="large"),
                "Target SIC": st.column_config.TextColumn("Target SIC", width="small", help="Automatically marked 🎯 when SIC includes 62012, 62020, 72110, 63110, or 58290."),
                "Target Address": st.column_config.TextColumn("Target Address", width="small", help="Automatically marked 📍 when the registered office partially matches one of the target addresses."),
                "Partner Formations Company": st.column_config.TextColumn("Partner Formations Company", width="small", help="Marked ✓ when directors were verified by Made Simple Group Limited ACSP, 1st Formations Limited ACSP, or ICON OFFICES LIMITED ACSP."),
                "High Sign Up Potential?": st.column_config.TextColumn("High Sign Up Potential?", width="small", help="Shows ⚡ for target SIC plus target address, and an extra ⚡ for partner formations company."),
                "Rating": st.column_config.TextColumn("Rating", width="small", help="⭐ for each matched signal, including target address and partner formations company, plus a bonus ⭐ for Sweden, Norway, or USA director/shareholder."),
                "Company Name": st.column_config.TextColumn("Company Name", width="large"),
                "SIC Code": st.column_config.TextColumn("SIC Code", width="small"),
                "Signals": st.column_config.TextColumn("Signals", width="medium"),
                "International Director": st.column_config.TextColumn("International Director", width="large"),
                "International Shareholder": st.column_config.TextColumn("International Shareholder", width="large"),
                "Owned By A Company": st.column_config.TextColumn("Owned By A Company", width="large"),
                "Target Address Detail": st.column_config.TextColumn("Target Address Detail", width="large"),
                "Director Verification ACSP": st.column_config.TextColumn("Director Verification ACSP", width="large"),
                "Profile": st.column_config.LinkColumn("Profile", display_text="Open record", width="small"),
                "Pulled At": st.column_config.TextColumn("Pulled At", width="medium"),
                "company_number": None,
            },
            key=f"results_editor_{date_str}",
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
            file_name=f"companies_house_screening_{date_str}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    with tab_shortlist:
        st.subheader("Shortlist")
        shortlist_df = display_df[display_df["Shortlist"] == True].copy()
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
                file_name=f"companies_house_shortlist_{date_str}.csv",
                mime="text/csv",
                use_container_width=True,
            )

    with tab_excluded:
        st.subheader("Excluded")
        excluded_df = display_df[display_df["Excluded"].astype(str) == "🚫"].copy()
        if excluded_df.empty:
            st.info("No Tide/Anna excluded companies have been stored for this date yet.")
        else:
            st.caption("These rows are committed to the database so they will not be re-screened on future refreshes for the same incorporation date.")
            st.dataframe(
                excluded_df.drop(columns=["company_number"], errors="ignore"),
                use_container_width=True,
                hide_index=True,
                column_config={"Profile": st.column_config.LinkColumn("Profile", display_text="Open record")},
            )

    with tab_settings:
        st.subheader("Current search settings")
        st.markdown(
            f"""
- Company status: Active
- Company types sent to API: `{', '.join(ALLOWED_COMPANY_TYPES)}`
- SIC codes sent to API: {len(ALLOWED_SIC_CODES)} values
- Target SIC codes: `{', '.join(sorted(TARGET_SIC_CODES))}`
- Target address partial matches: `{', '.join(TARGET_ADDRESS_TERMS)}`
- Excluded ACSPs: `TIDE Platform Ltd ACSP`, `ANNA ADMIN LIMITED ACSP`
- Partner formations ACSPs: `Made Simple Group Limited ACSP`, `1st Formations Limited ACSP`, `ICON OFFICES LIMITED ACSP`
- Advanced search page size: {SEARCH_PAGE_SIZE}
- Officers page size: {OFFICERS_PAGE_SIZE}
- PSC page size: {PSC_PAGE_SIZE}
- Dedupe rule: company numbers already written to the database for the selected incorporation date are skipped, including Tide/Anna excluded rows
- UI enhancements: sidebar filters, KPI cards, workflow tabs, clickable profile links, shortlist workflow, target SIC tagging, target address tagging, partner formations tagging, high sign up potential column, rating column, persisted Tide/Anna exclusions hidden from main results by default
            """
        )
        st.write("Selected signals for current filter:", ", ".join(selected_signals) if selected_signals else "None")


if __name__ == "__main__":
    main()
