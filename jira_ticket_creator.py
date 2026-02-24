"""
Jira Ticket Creator for Data Completeness Issues
Automatically creates Jira tickets based on Power BI variance data
"""

import argparse
import json
import os
import sys
from datetime import datetime

import pandas as pd
import requests
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth

# Load environment variables
load_dotenv()

# Jira Configuration
JIRA_URL = "https://conservice.atlassian.net"
JIRA_EMAIL = "nconn@conservice.com"
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")
GOOGLE_CHAT_WEBHOOK = os.getenv("GOOGLE_CHAT_WEBHOOK")
PROJECT_KEY = "DIT"

# Column names (matching the Power BI export exactly)
COL_PROVIDER_NAME = "Abbyy Name"
COL_PROVIDER_ID = "Abbyy/BC ID"
COL_PERCENT_INGESTED = "% of Locactions Ingested MTD of Rolling 3 Month Avg"
COL_VARIANCE = "Current Month Expected Variance (Based on 3 Mos Avg)"
COL_ROLLING_AVG_LOCATIONS = "Rolling 3 Month Avg # of Locations Ingested"

# Thresholds for ticket creation
MIN_PERCENT_INGESTED = 0.10  # 10% expressed as decimal (file stores 0.25 = 25%)
MAX_VARIANCE = -1000  # Variance must be less than -1000
MAX_TICKETS_PER_RUN = 2  # Hard cap on tickets created in a single run
TITLE_PREFIX = "[AUTOMATED] - "  # Prefix for testing; remove or replace later

# ANSI color codes for terminal status display
ANSI_RESET  = "\033[0m"
ANSI_YELLOW = "\033[33m"   # Provider name: None or Cancelled
STATUS_COLORS = {
    "To Do":        "\033[38;5;27m",   # Blue
    "Design":       "\033[38;5;33m",   # Blue-cyan
    "Development":  "\033[38;5;39m",   # Teal-blue
    "Testing":      "\033[38;5;45m",   # Blue-cyan
    "Peer Review":  "\033[38;5;51m",   # Cyan
    "Ops Review":   "\033[38;5;48m",   # Teal
    "Completed":    "\033[38;5;46m",   # Green
    "Cancelled":    "\033[90m",        # Dark grey
    "None":         "\033[90m",        # Dark grey
}
STATUS_DISPLAY_MAP = {
    "Backlog":                "To Do",
    "To Do":                  "To Do",
    "In Progress":            "Development",
    "Code Review & Security": "Testing",
    "Testing":                "Peer Review",
    "Merge":                  "Ops Review",
}


def find_data_file():
    """Find the Power BI export file in the project directory."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    for f in os.listdir(script_dir):
        if f.endswith((".xlsx", ".xls", ".csv")) and "variance" in f.lower():
            return os.path.join(script_dir, f)
    return None


def read_power_bi_data(file_path):
    """Read and parse the Power BI export file."""
    if file_path.endswith(".csv"):
        df = pd.read_csv(file_path)
    else:
        df = pd.read_excel(file_path)

    print(f"Loaded {len(df)} providers from: {os.path.basename(file_path)}")
    return df


def validate_columns(df):
    """Check that all required columns exist in the dataframe."""
    required = [
        COL_PROVIDER_NAME,
        COL_PROVIDER_ID,
        COL_PERCENT_INGESTED,
        COL_VARIANCE,
        COL_ROLLING_AVG_LOCATIONS,
    ]
    missing = [col for col in required if col not in df.columns]
    if missing:
        print("\nERROR: Required columns not found in file!")
        for col in missing:
            print(f"  Missing: '{col}'")
        print(f"\nColumns in file:")
        for col in df.columns:
            print(f"  - {col}")
        sys.exit(1)


def filter_problematic_providers(df):
    """
    Filter providers that meet criteria for ticket creation:
    - % of Locations Ingested MTD >= 10% (0.10 as decimal)
    - Current Month Expected Variance < -1000
    """
    filtered = df[
        (df[COL_PERCENT_INGESTED] >= MIN_PERCENT_INGESTED)
        & (df[COL_VARIANCE] < MAX_VARIANCE)
    ].copy()

    filtered = filtered.sort_values(COL_VARIANCE).reset_index(drop=True)

    print(f"\nFiltering criteria:")
    print(f"  {COL_PERCENT_INGESTED} >= {MIN_PERCENT_INGESTED:.0%}")
    print(f"  {COL_VARIANCE} < {MAX_VARIANCE:,}")
    print(f"  Result: {len(filtered)} providers need tickets")

    return filtered


def get_jira_auth():
    """Return reusable auth and headers for Jira API calls."""
    auth = HTTPBasicAuth(JIRA_EMAIL, JIRA_API_TOKEN)
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    return auth, headers


def check_related_tickets(provider_name, provider_id):
    """Check for existing and related tickets for this provider.

    Returns:
        {
            "has_open_dc": bool,             # True if an open DC story exists
            "dc_ticket": str | None,         # key of DC story (open OR recently completed)
            "dc_status_display": str,        # e.g. "Development" or "Completed - 2025-01-15" or ""
            "dc_status_category": str,       # "To Do", "In Progress", "Done", or ""
            "related": [                     # non-DC related tickets
                {"key": "DIT-99", "summary": "...", "url": "https://..."}
            ]
        }
    """
    auth, headers = get_jira_auth()
    pid = int(provider_id)

    def run_jql(jql, max_results):
        resp = requests.post(
            f"{JIRA_URL}/rest/api/3/search/jql",
            json={
                "jql": jql,
                "fields": ["summary", "status", "resolutiondate"],
                "maxResults": max_results,
            },
            auth=auth,
            headers=headers,
        )
        if resp.status_code == 200:
            return resp.json().get("issues", [])
        return []

    # Query 1: open Data Completeness ticket — hard skip
    jql_dc = (
        f'project = {PROJECT_KEY} '
        f'AND summary ~ "Data Completeness" '
        f'AND (summary ~ "{pid}" OR summary ~ "{provider_name}") '
        f'AND statusCategory != Done'
    )
    dc_issues = run_jql(jql_dc, 1)
    if dc_issues:
        issue = dc_issues[0]
        status_name = issue["fields"]["status"]["name"]
        status_name = STATUS_DISPLAY_MAP.get(status_name, status_name)
        status_cat = issue["fields"]["status"]["statusCategory"]["name"]
        return {
            "has_open_dc": True,
            "dc_ticket": issue["key"],
            "dc_status_display": status_name,
            "dc_status_category": status_cat,
            "related": [],
        }

    # Query 1b: recently completed Data Completeness ticket
    jql_dc_done = (
        f'project = {PROJECT_KEY} '
        f'AND summary ~ "Data Completeness" '
        f'AND (summary ~ "{pid}" OR summary ~ "{provider_name}") '
        f'AND statusCategory = Done AND updated >= -30d'
    )
    dc_ticket = None
    dc_status_display = ""
    dc_status_category = ""
    dc_done_issues = run_jql(jql_dc_done, 1)
    if dc_done_issues:
        issue = dc_done_issues[0]
        dc_ticket = issue["key"]
        resdate = issue["fields"].get("resolutiondate") or ""
        if resdate:
            date_part = resdate[:10]   # "2025-01-15"
            dc_status_display = f"Completed {date_part[5:7]}/{date_part[8:10]}"
        else:
            dc_status_display = "Completed"
        dc_status_category = "Done"

    related = []

    # Query 2: other open tickets mentioning this provider (not Data Completeness)
    jql_open = (
        f'project = {PROJECT_KEY} '
        f'AND NOT summary ~ "Data Completeness" '
        f'AND (summary ~ "{pid}" OR summary ~ "{provider_name}") '
        f'AND statusCategory != Done'
    )
    for issue in run_jql(jql_open, 5):
        related.append({
            "key": issue["key"],
            "summary": issue["fields"]["summary"],
            "url": f"{JIRA_URL}/browse/{issue['key']}",
        })

    # Query 3: recently completed non-DC tickets mentioning this provider
    jql_done = (
        f'project = {PROJECT_KEY} '
        f'AND NOT summary ~ "Data Completeness" '
        f'AND (summary ~ "{pid}" OR summary ~ "{provider_name}") '
        f'AND statusCategory = Done '
        f'AND updated >= -30d'
    )
    for issue in run_jql(jql_done, 5):
        if not any(r["key"] == issue["key"] for r in related):
            related.append({
                "key": issue["key"],
                "summary": issue["fields"]["summary"],
                "url": f"{JIRA_URL}/browse/{issue['key']}",
            })

    return {
        "has_open_dc": False,
        "dc_ticket": dc_ticket,
        "dc_status_display": dc_status_display,
        "dc_status_category": dc_status_category,
        "related": related,
    }


def build_description(provider_name, provider_id):
    """Build the Jira ticket description (AC and DoD are now separate custom fields)."""
    return f"""Provider + ID: {provider_name} - {provider_id}
Issue found in: Data Completeness Report by automated script
Issue Description:

Example Controls:
"""


def build_acceptance_criteria():
    """Return Acceptance Criteria text for the customfield_10083 field."""
    return (
        "The requested feature is completely implemented and meets the goal of the story.\n"
        "  - If no specific feature is requested, there should be an attempt to identify a feature.\n"
        "  - A backup Comment in Flexilayout is submitted documenting challenge resolved and changes made.\n"
        "  - Line Item skeletons for those line items related to our template changes, and are necessary for template testing, are set up in Line Item Manager OR contact is made with ops to set these up\n"
        "  - Measures skeletons for those measures related to our template changes, and are necessary for template testing, are set up in Line Item Manager OR contact is made with ops to set these up\n"
        "  - InvoiceType and ReadType aliases have been set up.\n"
        "  - Prohibited element documentation has been entered for any prohibited elements added/removed.\n"
        "  - A Verification/Setup Station batch has been run using the most recent Flexilayout & Line Item Manager information.\n"
        "Review Onboarding report for Linking/Validation issues and correct.  These should decrease!\n"
        "  - A new task is submitted for large fixes necessary\n"
        "Documentation is added to the Jira task describing changes that were made\n"
        "Applicable unfixable changes are discussed in appropriate meetings and documented in appropriate trackers (ie, Unresolved Issues doc).\n"
        "Ensure proper tickets for other projects/teams are submitted."
    )


def build_definition_of_done():
    """Return Definition of Done text for the customfield_10203 field."""
    return (
        "An ABBYY batch containing the specific bills identified and/or random bills from the last 30 days, is run.  "
        "The batch should contain 90-100 bills with a mixture of example control #'s provided as well as additional, random bills.\n"
        "  - The size of the ABBYY batch and what types of bills were included should be detailed in the comments on the ticket.\n"
        "  - Visual confirmation the provider has been classified.\n"
        "  - Visual confirmation the form has been populated with all applicable data and that the data on the bill matches the data in Verification/Setup Station and appropriate action is taken.\n"
        "  - All red flags are resolved or documented in Jira task.\n"
        "  - Prohibited element flags exist on applicable solar, lighting, and summary bills.\n"
        "Add/update necessary template documentation in Confluence."
    )


def text_to_adf(text):
    """Convert plain text description to Atlassian Document Format (ADF).
    Handles *bold* markers and bullet points (* prefix).
    """
    lines = text.split("\n")
    content = []
    bullet_items = []

    def flush_bullets():
        if bullet_items:
            content.append({
                "type": "bulletList",
                "content": list(bullet_items),
            })
            bullet_items.clear()

    for line in lines:
        stripped = line.strip()

        # Bullet point line
        if stripped.startswith("* "):
            flush_bullets()  # top-level list
            bullet_text = stripped[2:]
            bullet_items.append({
                "type": "listItem",
                "content": [{"type": "paragraph", "content": parse_inline(bullet_text)}],
            })
        elif stripped.startswith("* ") or (len(stripped) > 2 and line.startswith("   * ")):
            # Sub-bullet
            indent_text = stripped.lstrip("* ").strip()
            if bullet_items:
                last = bullet_items[-1]
                sub_list = None
                for node in last["content"]:
                    if node["type"] == "bulletList":
                        sub_list = node
                        break
                if not sub_list:
                    sub_list = {"type": "bulletList", "content": []}
                    last["content"].append(sub_list)
                sub_list["content"].append({
                    "type": "listItem",
                    "content": [{"type": "paragraph", "content": parse_inline(indent_text)}],
                })
            else:
                bullet_items.append({
                    "type": "listItem",
                    "content": [{"type": "paragraph", "content": parse_inline(indent_text)}],
                })
        elif stripped == "":
            flush_bullets()
            # skip blank lines (ADF doesn't need explicit empty paragraphs)
        else:
            flush_bullets()
            content.append({
                "type": "paragraph",
                "content": parse_inline(stripped),
            })

    flush_bullets()

    return {"version": 1, "type": "doc", "content": content}


def parse_inline(text):
    """Parse inline *bold* markers into ADF text nodes."""
    import re
    parts = re.split(r'(\*[^*]+\*)', text)
    nodes = []
    for part in parts:
        if not part:
            continue
        if part.startswith("*") and part.endswith("*") and len(part) > 2:
            nodes.append({
                "type": "text",
                "text": part[1:-1],
                "marks": [{"type": "strong"}],
            })
        else:
            nodes.append({"type": "text", "text": part})
    return nodes if nodes else [{"type": "text", "text": " "}]


def plain_text_to_adf(text):
    """Wrap plain multi-line text as ADF paragraphs (for textarea custom fields)."""
    content = [
        {"type": "paragraph", "content": [{"type": "text", "text": line}]}
        for line in text.split("\n")
        if line.strip()
    ]
    if not content:
        content = [{"type": "paragraph", "content": [{"type": "text", "text": " "}]}]
    return {"version": 1, "type": "doc", "content": content}


def create_jira_ticket(provider_name, provider_id, rolling_avg_locations):
    """Create a single Jira ticket for a provider. Returns the ticket key or None."""
    title = f"{TITLE_PREFIX}Data Completeness - {provider_name} - {int(provider_id)}"
    description = build_description(provider_name, provider_id)

    payload = {
        "fields": {
            "project": {"key": PROJECT_KEY},
            "summary": title,
            "description": text_to_adf(description),
            "issuetype": {"name": "Story"},
            "customfield_10083": plain_text_to_adf(build_acceptance_criteria()),
            "customfield_10203": plain_text_to_adf(build_definition_of_done()),
            "customfield_13145": float(rolling_avg_locations),
        }
    }

    auth, headers = get_jira_auth()
    response = requests.post(f"{JIRA_URL}/rest/api/3/issue", json=payload, auth=auth, headers=headers)

    if response.status_code == 201:
        ticket_key = response.json()["key"]
        print(f"  CREATED  {ticket_key}: {title}")
        return ticket_key
    else:
        print(f"  FAILED   {title}")
        print(f"           Status {response.status_code}: {response.text[:200]}")
        return None


def prompt_thresholds():
    """Interactively prompt to override module-level thresholds. Returns updated values."""
    global MIN_PERCENT_INGESTED, MAX_VARIANCE, MAX_TICKETS_PER_RUN

    print("\n" + "-" * 60)
    print("Thresholds (press Enter to keep current value):")

    raw = input(f"  Min % Ingested MTD  [current: {MIN_PERCENT_INGESTED:.0%}]: ").strip()
    if raw:
        try:
            val = float(raw.strip("%"))
            if val > 1:  # treat bare numbers like "10" as 10%
                val = val / 100
            if 0 < val <= 1:
                MIN_PERCENT_INGESTED = val
            else:
                print(f"  WARNING: '{raw}' is out of range (0–100%). Keeping {MIN_PERCENT_INGESTED:.0%}.")
        except ValueError:
            print(f"  WARNING: '{raw}' is not a valid number. Keeping {MIN_PERCENT_INGESTED:.0%}.")

    raw = input(f"  Max Variance        [current: {MAX_VARIANCE:,}]: ").strip()
    if raw:
        try:
            val = float(raw.replace(",", ""))
            if val < 0:
                MAX_VARIANCE = val
            else:
                print(f"  WARNING: Variance must be negative. Keeping {MAX_VARIANCE:,}.")
        except ValueError:
            print(f"  WARNING: '{raw}' is not a valid number. Keeping {MAX_VARIANCE:,}.")

    raw = input(f"  Max Tickets/Run     [current: {MAX_TICKETS_PER_RUN}]: ").strip()
    if raw:
        try:
            val = int(raw)
            if val >= 1:
                MAX_TICKETS_PER_RUN = val
            else:
                print(f"  WARNING: Must be >= 1. Keeping {MAX_TICKETS_PER_RUN}.")
        except ValueError:
            print(f"  WARNING: '{raw}' is not a valid integer. Keeping {MAX_TICKETS_PER_RUN}.")

    print("-" * 60)


def _status_color(status_text):
    """Return the ANSI color code for a given status display string."""
    if status_text in STATUS_COLORS:
        return STATUS_COLORS[status_text]
    if status_text.startswith("Completed"):
        return STATUS_COLORS["Completed"]
    return ""


def _hyperlink(url, text):
    """Render text as an OSC 8 terminal hyperlink (Windows Terminal compatible)."""
    return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"


def _is_default_row(row):
    """Return True if this provider should be included in the default ticket selection.
    Excludes providers that already have an open DC story or a recently completed one.
    """
    if row.get("_has_open_dc", False):
        return False
    if str(row.get("_dc_status_display", "")).startswith("Completed"):
        return False
    return True


def select_providers(df):
    """Display qualifying providers and let the user pick which to action.

    Accepts: blank (smart default), "all", comma-separated numbers, or a range like "1-3".
    Returns a filtered DataFrame. Warns and trims if selection exceeds MAX_TICKETS_PER_RUN.
    """
    has_metadata = "_has_open_dc" in df.columns

    W = {"idx": 3, "name": 38, "id": 8, "pct": 9, "var": 12, "avg": 10, "status": 16}
    SEP = "  "

    print(
        f"\n{'#':<{W['idx']}}{SEP}{'Provider':<{W['name']}}{SEP}{'ID':<{W['id']}}"
        f"{SEP}{'% Ingested':>{W['pct']}}{SEP}{'Variance':>{W['var']}}"
        f"{SEP}{'Rolling Avg':>{W['avg']}}{SEP}{'Status':<{W['status']}}{SEP}Related Tickets"
    )
    print("-" * 130)

    rows = list(df.iterrows())
    for i, (_, row) in enumerate(rows, start=1):
        # Status column (16 chars wide, ANSI-colored)
        status_text = row.get("_dc_status_display", "") if has_metadata else ""

        # Provider name color based on DC story status
        if not status_text or status_text == "Cancelled":
            name_color = ANSI_YELLOW
        else:
            name_color = ""

        name_str = f"{str(row[COL_PROVIDER_NAME]):<{W['name']}}"
        name_col = f"{name_color}{name_str}{ANSI_RESET}" if name_color else name_str

        if not status_text:
            status_col = f"{STATUS_COLORS['None']}{'None':<{W['status']}}{ANSI_RESET}"
        else:
            color = _status_color(status_text)
            padded = f"{status_text:<{W['status']}}"
            status_col = f"{color}{padded}{ANSI_RESET}" if color else padded

        # Related tickets column as hyperlinks
        related_items = row.get("_related_items", []) if has_metadata else []
        related_col = "  ".join(_hyperlink(item["url"], item["key"]) for item in related_items)

        print(
            f"{i:<{W['idx']}}{SEP}{name_col}{SEP}"
            f"{int(row[COL_PROVIDER_ID]):<{W['id']}}{SEP}"
            f"{row[COL_PERCENT_INGESTED]:>{W['pct']}.1%}{SEP}"
            f"{row[COL_VARIANCE]:>{W['var']},.0f}{SEP}"
            f"{row[COL_ROLLING_AVG_LOCATIONS]:>{W['avg']},.0f}{SEP}"
            f"{status_col}{SEP}{related_col}"
        )

    # Compute default selection: exclude providers with open OR recently-completed DC stories
    default_indices = [i for i, (_, row) in enumerate(rows, start=1) if _is_default_row(row)] \
        if has_metadata else []

    if default_indices:
        n = len(default_indices)
        prompt = (
            f'\nEnter row numbers to create tickets (e.g. "1,3" or "1-3"), or "all"\n'
            f'  [default: {n} ticket{"s" if n != 1 else ""}'
            f' — all providers without open or recently completed Data Completeness stories]: '
        )
    else:
        prompt = '\nEnter row numbers to create tickets (e.g. "1,3" or "1-3"), or "all" [default: all]: '

    raw = input(prompt).strip().lower()

    if not raw:
        selected_indices = [i - 1 for i in default_indices] if default_indices else list(range(len(rows)))
    elif raw == "all":
        selected_indices = list(range(len(rows)))
    else:
        selected_indices = []
        for part in raw.split(","):
            part = part.strip()
            if "-" in part:
                bounds = part.split("-", 1)
                try:
                    lo, hi = int(bounds[0]), int(bounds[1])
                    selected_indices.extend(range(lo - 1, hi))
                except ValueError:
                    print(f"  WARNING: Could not parse range '{part}'. Skipping.")
            else:
                try:
                    selected_indices.append(int(part) - 1)
                except ValueError:
                    print(f"  WARNING: Could not parse '{part}'. Skipping.")
        # Deduplicate while preserving order, drop out-of-range
        seen = set()
        clean = []
        for idx in selected_indices:
            if idx not in seen and 0 <= idx < len(rows):
                seen.add(idx)
                clean.append(idx)
        if len(clean) < len(selected_indices):
            print(f"  WARNING: Some entries were out of range and skipped.")
        selected_indices = clean

    if not selected_indices:
        return pd.DataFrame(columns=df.columns)

    selected_rows = [rows[i][1] for i in selected_indices]
    result = pd.DataFrame(selected_rows)

    if len(result) > MAX_TICKETS_PER_RUN:
        print(
            f"\n  WARNING: Selection ({len(result)}) exceeds MAX_TICKETS_PER_RUN ({MAX_TICKETS_PER_RUN}). "
            f"Only the first {MAX_TICKETS_PER_RUN} will be created."
        )
        result = result.head(MAX_TICKETS_PER_RUN)

    return result


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  TEMPORARY TEST SCAFFOLDING — supports the --test CLI flag              ║
# ║                                                                          ║
# ║  TO REMOVE THIS BLOCK:                                                   ║
# ║    1. Delete this entire function (_run_table_test).                     ║
# ║    2. Delete the `--test` argparse argument in main().                   ║
# ║    3. Delete the `if args.test:` early-exit block in main().             ║
# ╚══════════════════════════════════════════════════════════════════════════╝
def _run_table_test():
    """[TEMPORARY] Call select_providers() with synthetic data covering all
    status display paths. No API calls, no file I/O required.
    """
    rows = [
        {
            COL_PROVIDER_NAME: "Test Provider - No Status",
            COL_PROVIDER_ID: 10001,
            COL_PERCENT_INGESTED: 0.15,
            COL_VARIANCE: -1200,
            COL_ROLLING_AVG_LOCATIONS: 200,
            "_dc_status_display": "",
            "_has_open_dc": False,
            "_related_items": [],
        },
        {
            COL_PROVIDER_NAME: "Test Provider - To Do",
            COL_PROVIDER_ID: 10002,
            COL_PERCENT_INGESTED: 0.35,
            COL_VARIANCE: -2500,
            COL_ROLLING_AVG_LOCATIONS: 500,
            "_dc_status_display": "To Do",
            "_has_open_dc": True,
            "_related_items": [{"key": "DIT-100", "url": f"{JIRA_URL}/browse/DIT-100"}],
        },
        {
            COL_PROVIDER_NAME: "Test Provider - Design",
            COL_PROVIDER_ID: 10010,
            COL_PERCENT_INGESTED: 0.42,
            COL_VARIANCE: -2800,
            COL_ROLLING_AVG_LOCATIONS: 600,
            "_dc_status_display": "Design",
            "_has_open_dc": True,
            "_related_items": [{"key": "DIT-109", "url": f"{JIRA_URL}/browse/DIT-109"}],
        },
        {
            COL_PROVIDER_NAME: "Test Provider - Development",
            COL_PROVIDER_ID: 10003,
            COL_PERCENT_INGESTED: 0.55,
            COL_VARIANCE: -3500,
            COL_ROLLING_AVG_LOCATIONS: 800,
            "_dc_status_display": "Development",
            "_has_open_dc": True,
            "_related_items": [{"key": "DIT-101", "url": f"{JIRA_URL}/browse/DIT-101"}],
        },
        {
            COL_PROVIDER_NAME: "Test Provider - Testing",
            COL_PROVIDER_ID: 10004,
            COL_PERCENT_INGESTED: 0.45,
            COL_VARIANCE: -4200,
            COL_ROLLING_AVG_LOCATIONS: 1200,
            "_dc_status_display": "Testing",
            "_has_open_dc": True,
            "_related_items": [{"key": "DIT-102", "url": f"{JIRA_URL}/browse/DIT-102"}],
        },
        {
            COL_PROVIDER_NAME: "Test Provider - Peer Review",
            COL_PROVIDER_ID: 10011,
            COL_PERCENT_INGESTED: 0.60,
            COL_VARIANCE: -4800,
            COL_ROLLING_AVG_LOCATIONS: 1350,
            "_dc_status_display": "Peer Review",
            "_has_open_dc": True,
            "_related_items": [{"key": "DIT-110", "url": f"{JIRA_URL}/browse/DIT-110"}],
        },
        {
            COL_PROVIDER_NAME: "Test Provider - Ops Review",
            COL_PROVIDER_ID: 10005,
            COL_PERCENT_INGESTED: 0.72,
            COL_VARIANCE: -5100,
            COL_ROLLING_AVG_LOCATIONS: 1500,
            "_dc_status_display": "Ops Review",
            "_has_open_dc": True,
            "_related_items": [{"key": "DIT-103", "url": f"{JIRA_URL}/browse/DIT-103"}],
        },
        {
            COL_PROVIDER_NAME: "Test Provider - Completed",
            COL_PROVIDER_ID: 10006,
            COL_PERCENT_INGESTED: 0.83,
            COL_VARIANCE: -6800,
            COL_ROLLING_AVG_LOCATIONS: 2000,
            "_dc_status_display": "Completed 01/15",
            "_has_open_dc": False,
            "_related_items": [{"key": "DIT-104", "url": f"{JIRA_URL}/browse/DIT-104"}],
        },
        {
            COL_PROVIDER_NAME: "Test Provider - Completed",
            COL_PROVIDER_ID: 10007,
            COL_PERCENT_INGESTED: 0.91,
            COL_VARIANCE: -7500,
            COL_ROLLING_AVG_LOCATIONS: 2500,
            "_dc_status_display": "Completed",
            "_has_open_dc": False,
            "_related_items": [{"key": "DIT-105", "url": f"{JIRA_URL}/browse/DIT-105"}],
        },
        {
            COL_PROVIDER_NAME: "Test Provider - Cancelled",
            COL_PROVIDER_ID: 10008,
            COL_PERCENT_INGESTED: 0.68,
            COL_VARIANCE: -8200,
            COL_ROLLING_AVG_LOCATIONS: 2800,
            "_dc_status_display": "Cancelled",
            "_has_open_dc": False,
            "_related_items": [{"key": "DIT-106", "url": f"{JIRA_URL}/browse/DIT-106"}],
        },
        {
            COL_PROVIDER_NAME: "Test Provider - Multi Rel.",
            COL_PROVIDER_ID: 10009,
            COL_PERCENT_INGESTED: 0.95,
            COL_VARIANCE: -9500,
            COL_ROLLING_AVG_LOCATIONS: 3000,
            "_dc_status_display": "Development",
            "_has_open_dc": True,
            "_related_items": [
                {"key": "DIT-107", "url": f"{JIRA_URL}/browse/DIT-107"},
                {"key": "DIT-108", "url": f"{JIRA_URL}/browse/DIT-108"},
            ],
        },
    ]
    df = pd.DataFrame(rows)
    select_providers(df)
# ── END TEMPORARY TEST SCAFFOLDING ───────────────────────────────────────────


def send_google_chat_report(providers_df):
    """POST a Google Chat Card v2 with flagged providers to THE GOAT space."""
    if not GOOGLE_CHAT_WEBHOOK:
        print("\nERROR: GOOGLE_CHAT_WEBHOOK not set in .env")
        print("Add: GOOGLE_CHAT_WEBHOOK=https://chat.googleapis.com/v1/spaces/.../messages?key=...")
        sys.exit(1)

    today = datetime.now().strftime("%Y-%m-%d")
    n = len(providers_df)
    has_metadata = "_has_open_dc" in providers_df.columns

    sections = []
    for _, row in providers_df.iterrows():
        name = str(row[COL_PROVIDER_NAME])
        pid = int(row[COL_PROVIDER_ID])
        pct = row[COL_PERCENT_INGESTED]
        variance = row[COL_VARIANCE]
        avg = row[COL_ROLLING_AVG_LOCATIONS]

        widgets = [
            {"decoratedText": {"topLabel": "% Ingested MTD", "text": f"{pct:.1%}"}},
            {"decoratedText": {"topLabel": "Variance vs 3-Mo Avg", "text": f"{variance:,.0f}"}},
            {"decoratedText": {"topLabel": "Rolling 3-Mo Avg Locations", "text": f"{avg:,.0f}"}},
        ]

        if has_metadata:
            status_text = str(row.get("_dc_status_display", "") or "")
            related_items = row.get("_related_items", []) or []
            if related_items:
                for item in related_items:
                    key = item["key"]
                    url = item["url"]
                    label_text = f"{key} · {status_text}" if status_text else key
                    widgets.append({
                        "decoratedText": {
                            "topLabel": "Jira",
                            "text": label_text,
                            "button": {
                                "text": key,
                                "onClick": {"openLink": {"url": url}},
                            },
                        }
                    })
            elif status_text:
                widgets.append({"decoratedText": {"topLabel": "Jira", "text": status_text}})

        sections.append({
            "header": f"{name} ({pid})",
            "collapsible": True,
            "uncollapsibleWidgetsCount": 1,
            "widgets": widgets,
        })

    payload = {
        "cardsV2": [{
            "cardId": "variance-report",
            "card": {
                "header": {
                    "title": "⚠️ Power BI Variance Report",
                    "subtitle": f"{today} · {n} provider{'s' if n != 1 else ''} flagged",
                    "imageType": "CIRCLE",
                    "imageUrl": "https://fonts.gstatic.com/s/i/googlematerialicons/warning/v6/white-48dp.png",
                },
                "sections": sections,
            },
        }]
    }

    resp = requests.post(GOOGLE_CHAT_WEBHOOK, json=payload)
    if resp.status_code == 200:
        print(f"\nReport sent. ({n} provider{'s' if n != 1 else ''} flagged)")
    else:
        print(f"\nERROR: Failed to send report. Status {resp.status_code}: {resp.text[:300]}")


def main():
    parser = argparse.ArgumentParser(description="Create Jira tickets from Power BI variance data")
    parser.add_argument("--dry-run", action="store_true", help="Preview tickets without creating them")
    parser.add_argument("--file", type=str, help="Path to Power BI export file (auto-detected if omitted)")
    parser.add_argument(
        "--report",
        action="store_true",
        help=(
            "Send a Google Chat card with flagged providers to THE GOAT space, then exit. "
            "Requires GOOGLE_CHAT_WEBHOOK in .env. JIRA_API_TOKEN is optional but recommended "
            "for ticket status enrichment."
        ),
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="[TEMPORARY] Print provider table with fake data covering all status types, then exit.",
    )
    args = parser.parse_args()

    print("=" * 70)
    print("Jira Ticket Creator - Data Completeness Issues")
    if args.dry_run:
        print("*** DRY RUN MODE - No tickets will be created ***")
    print("=" * 70)

    # ── TEMPORARY: --test flag ──────────────────────────────────────────────────
    # Remove this block (and the --test argparse argument above) when no longer needed.
    if args.test:
        _run_table_test()
        return
    # ── END TEMPORARY ───────────────────────────────────────────────────────────

    prompt_thresholds()

    # Check for API token (skip in dry-run and report modes)
    if not args.dry_run and not args.report and not JIRA_API_TOKEN:
        print("\nERROR: JIRA_API_TOKEN not found in environment variables")
        print("Create a .env file with: JIRA_API_TOKEN=your_token_here")
        sys.exit(1)

    # Find data file
    file_path = args.file
    if not file_path:
        file_path = find_data_file()
        if not file_path:
            print("\nERROR: No variance Excel/CSV file found in project directory.")
            print("Use --file to specify the path.")
            sys.exit(1)

    if not os.path.exists(file_path):
        print(f"\nERROR: File not found: {file_path}")
        sys.exit(1)

    # Load and filter data
    df = read_power_bi_data(file_path)
    validate_columns(df)
    problematic = filter_problematic_providers(df)

    if len(problematic) == 0:
        print("\nNo providers meet the criteria for ticket creation.")
        print("All providers are performing within acceptable ranges!")
        return

    # Check for existing tickets (skip if no API token)
    if JIRA_API_TOKEN:
        print("\nChecking for existing tickets...")
        enriched = []
        for _, row in problematic.iterrows():
            result = check_related_tickets(row[COL_PROVIDER_NAME], row[COL_PROVIDER_ID])
            row = row.copy()
            row["_has_open_dc"]       = result["has_open_dc"]
            row["_dc_ticket"]         = result["dc_ticket"]
            row["_dc_status_display"] = result["dc_status_display"]
            row["_dc_status_cat"]     = result["dc_status_category"]
            # Build related items: DC ticket first (if any), then others
            related_items = []
            if result["dc_ticket"]:
                related_items.append({
                    "key": result["dc_ticket"],
                    "url": f"{JIRA_URL}/browse/{result['dc_ticket']}",
                })
            related_items += [{"key": r["key"], "url": r["url"]} for r in result["related"]]
            row["_related_items"]     = related_items
            enriched.append(row)
        problematic = pd.DataFrame(enriched)
        n_open_dc = problematic["_has_open_dc"].sum()
        print(f"  {n_open_dc} with existing open stories, {len(problematic) - n_open_dc} new tickets needed")
    else:
        print("\n  (Skipping duplicate check -- no API token)")

    if args.report:
        send_google_chat_report(problematic)
        return

    # Provider selection UI (also enforces MAX_TICKETS_PER_RUN cap)
    problematic = select_providers(problematic)

    if len(problematic) == 0:
        print("\nNo providers selected. Nothing to do!")
        return

    if args.dry_run:
        print(f"\n*** DRY RUN: {len(problematic)} tickets would be created ***")
        return

    # Create tickets
    print("\nCreating tickets...\n")
    created = []
    for _, row in problematic.iterrows():
        name = row[COL_PROVIDER_NAME]
        pid = row[COL_PROVIDER_ID]

        # Guard against creating duplicates if the user explicitly selected a hard-skip row
        if row.get("_has_open_dc", False):
            dc_ticket = row.get("_dc_ticket", "unknown")
            dc_status = row.get("_dc_status_display", "unknown")
            print(f"\n  WARNING: {name} already has an open DC story ({dc_ticket} — {dc_status}).")
            confirm = input("  Create a duplicate ticket anyway? [y/N]: ").strip().lower()
            if confirm not in ("y", "yes"):
                print("  Skipped.")
                continue

        ticket_key = create_jira_ticket(name, pid, row[COL_ROLLING_AVG_LOCATIONS])
        if ticket_key:
            created.append(ticket_key)

    # Summary
    print(f"\n{'=' * 70}")
    print(f"SUMMARY: Created {len(created)} / {len(problematic)} tickets")
    print("=" * 70)
    for ticket in created:
        print(f"  {JIRA_URL}/browse/{ticket}")
    print(f"\nCompleted at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
