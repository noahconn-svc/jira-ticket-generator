"""
Jira Ticket Creator for Data Completeness Issues
Automatically creates Jira tickets based on Power BI variance data.
Designed for unattended/scheduled execution — no interactive prompts.
"""

import json
import logging
import os
import sys
from datetime import datetime

import pandas as pd
import requests
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth

load_dotenv()

# Jira / Google Chat credentials
JIRA_URL = "https://conservice.atlassian.net"
JIRA_EMAIL = "nconn@conservice.com"
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")
GOOGLE_CHAT_WEBHOOK = os.getenv("GOOGLE_CHAT_WEBHOOK")
PROJECT_KEY = "DIT"
TITLE_PREFIX = "[AUTOMATED] - "

# Column names (matching the Power BI export exactly)
COL_PROVIDER_NAME = "Abbyy Name"
COL_PROVIDER_ID = "Abbyy/BC ID"
COL_PERCENT_INGESTED = "% of Locactions Ingested MTD of Rolling 3 Month Avg"
COL_VARIANCE = "Current Month Expected Variance (Based on 3 Mos Avg)"
COL_ROLLING_AVG_LOCATIONS = "Rolling 3 Month Avg # of Locations Ingested"

# Config defaults — overridden by config.json if present
_DEFAULTS = {
    "min_percent_ingested": 0.10,
    "max_variance": -1000,
    "max_tickets_per_run": 2,
    "dc_lookback_days": 30,
    "send_google_chat_report": False,
}

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.join(_SCRIPT_DIR, "config.json")
_RUNS_PATH = os.path.join(_SCRIPT_DIR, "runs.jsonl")
_LOG_PATH = os.path.join(_SCRIPT_DIR, "jira_ticket_creator.log")

STATUS_DISPLAY_MAP = {
    "Backlog":                "To Do",
    "To Do":                  "To Do",
    "In Progress":            "Development",
    "Code Review & Security": "Testing",
    "Testing":                "Peer Review",
    "Merge":                  "Ops Review",
}

# Logging — file only; no console output
logging.basicConfig(
    filename=_LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def load_config():
    """Load config.json, falling back to defaults for any missing keys."""
    config = dict(_DEFAULTS)
    if os.path.exists(_CONFIG_PATH):
        try:
            with open(_CONFIG_PATH) as f:
                config.update(json.load(f))
        except Exception as e:
            log.warning("Could not read config.json: %s — using defaults", e)
    return config


def find_data_file():
    """Find the Power BI export file in the project directory."""
    for f in os.listdir(_SCRIPT_DIR):
        if f.endswith((".xlsx", ".xls", ".csv")) and "variance" in f.lower():
            return os.path.join(_SCRIPT_DIR, f)
    return None


def read_power_bi_data(file_path):
    """Read and parse the Power BI export file."""
    if file_path.endswith(".csv"):
        df = pd.read_csv(file_path)
    else:
        df = pd.read_excel(file_path)
    log.info("Loaded %d providers from: %s", len(df), os.path.basename(file_path))
    return df


def validate_columns(df):
    """Raise ValueError if required columns are missing."""
    required = [
        COL_PROVIDER_NAME,
        COL_PROVIDER_ID,
        COL_PERCENT_INGESTED,
        COL_VARIANCE,
        COL_ROLLING_AVG_LOCATIONS,
    ]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"Required columns missing from file: {missing}")


def filter_problematic_providers(df, min_percent_ingested, max_variance):
    """Filter providers meeting criteria for ticket creation."""
    filtered = df[
        (df[COL_PERCENT_INGESTED] >= min_percent_ingested)
        & (df[COL_VARIANCE] < max_variance)
    ].copy()
    return filtered.sort_values(COL_VARIANCE).reset_index(drop=True)


def get_jira_auth():
    """Return reusable auth and headers for Jira API calls."""
    auth = HTTPBasicAuth(JIRA_EMAIL, JIRA_API_TOKEN)
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    return auth, headers


def check_related_tickets(provider_name, provider_id, dc_lookback_days):
    """Check for existing and related tickets for this provider.

    Returns:
        {
            "has_open_dc": bool,
            "dc_ticket": str | None,
            "dc_status_display": str,
            "dc_status_category": str,
            "related": [{"key": ..., "summary": ..., "url": ...}]
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
        return resp.json().get("issues", []) if resp.status_code == 200 else []

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
        status_name = STATUS_DISPLAY_MAP.get(
            issue["fields"]["status"]["name"], issue["fields"]["status"]["name"]
        )
        return {
            "has_open_dc": True,
            "dc_ticket": issue["key"],
            "dc_status_display": status_name,
            "dc_status_category": issue["fields"]["status"]["statusCategory"]["name"],
            "related": [],
        }

    # Query 1b: recently completed Data Completeness ticket
    jql_dc_done = (
        f'project = {PROJECT_KEY} '
        f'AND summary ~ "Data Completeness" '
        f'AND (summary ~ "{pid}" OR summary ~ "{provider_name}") '
        f'AND statusCategory = Done AND updated >= -{dc_lookback_days}d'
    )
    dc_ticket = dc_status_display = dc_status_category = ""
    dc_done_issues = run_jql(jql_dc_done, 1)
    if dc_done_issues:
        issue = dc_done_issues[0]
        dc_ticket = issue["key"]
        resdate = issue["fields"].get("resolutiondate") or ""
        dc_status_display = f"Completed {resdate[5:7]}/{resdate[8:10]}" if resdate else "Completed"
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
        f'AND updated >= -{dc_lookback_days}d'
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
    """Build the Jira ticket description."""
    return f"""Provider + ID: {provider_name} - {provider_id}
Issue found in: Data Completeness Report by automated script
Issue Description:

Example Controls:
"""


def build_acceptance_criteria():
    """Return Acceptance Criteria text for customfield_10083."""
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
    """Return Definition of Done text for customfield_10203."""
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
            content.append({"type": "bulletList", "content": list(bullet_items)})
            bullet_items.clear()

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("* "):
            flush_bullets()
            bullet_items.append({
                "type": "listItem",
                "content": [{"type": "paragraph", "content": parse_inline(stripped[2:])}],
            })
        elif len(stripped) > 2 and line.startswith("   * "):
            indent_text = stripped.lstrip("* ").strip()
            if bullet_items:
                last = bullet_items[-1]
                sub_list = next((n for n in last["content"] if n["type"] == "bulletList"), None)
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
        else:
            flush_bullets()
            content.append({"type": "paragraph", "content": parse_inline(stripped)})

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
            nodes.append({"type": "text", "text": part[1:-1], "marks": [{"type": "strong"}]})
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
    payload = {
        "fields": {
            "project": {"key": PROJECT_KEY},
            "summary": title,
            "description": text_to_adf(build_description(provider_name, provider_id)),
            "issuetype": {"name": "Story"},
            "customfield_10083": plain_text_to_adf(build_acceptance_criteria()),
            "customfield_10203": plain_text_to_adf(build_definition_of_done()),
            "customfield_13145": float(rolling_avg_locations),
        }
    }
    auth, headers = get_jira_auth()
    response = requests.post(f"{JIRA_URL}/rest/api/3/issue", json=payload, auth=auth, headers=headers)
    if response.status_code == 201:
        key = response.json()["key"]
        log.info("Created %s: %s", key, title)
        return key
    else:
        log.error("Failed to create ticket for %s: %s %s", provider_name, response.status_code, response.text[:200])
        return None


def send_google_chat_report(created_tickets, errors):
    """POST a Google Chat Card v2 summary of created tickets to THE GOAT space.

    Args:
        created_tickets: list of dicts with keys: key, name, variance, pct_ingested
        errors: list of error strings
    """
    if not GOOGLE_CHAT_WEBHOOK:
        log.error("send_google_chat_report: GOOGLE_CHAT_WEBHOOK not set in .env")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    n = len(created_tickets)
    e = len(errors)

    # Section 1: Tickets Created
    if created_tickets:
        ticket_widgets = []
        for t in created_tickets:
            ticket_widgets.append({"decoratedText": {
                "topLabel": t["name"],
                "text": f"Variance: {t['variance']:,.0f} · {t['pct_ingested']:.1%} ingested",
                "button": {
                    "text": t["key"],
                    "onClick": {"openLink": {"url": f"https://conservice.atlassian.net/browse/{t['key']}"}},
                },
            }})
    else:
        ticket_widgets = [{"textParagraph": {"text": "No tickets created."}}]

    sections = [{"header": "Tickets Created", "widgets": ticket_widgets}]

    # Section 2: Errors (only if present)
    if errors:
        sections.append({
            "header": "Errors",
            "widgets": [{"textParagraph": {"text": err}} for err in errors],
        })

    payload = {"cardsV2": [{"cardId": "jira-ticket-report", "card": {
        "header": {
            "title": "Jira Ticket Report",
            "subtitle": f"{today} · {n} created, {e} error{'s' if e != 1 else ''}",
            "imageType": "CIRCLE",
            "imageUrl": "https://fonts.gstatic.com/s/i/googlematerialicons/assignment/v6/white-48dp.png",
        },
        "sections": sections,
    }}]}

    resp = requests.post(GOOGLE_CHAT_WEBHOOK, json=payload)
    if resp.status_code == 200:
        log.info("Google Chat report sent (%d created, %d errors)", n, e)
    else:
        log.error("Google Chat report failed: %s %s", resp.status_code, resp.text[:300])


def _append_run_log(entry):
    with open(_RUNS_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _git_push_run_log():
    import subprocess
    for cmd in [
        ["git", "add", "runs.jsonl"],
        ["git", "commit", "-m", f"runs: {datetime.now().strftime('%Y-%m-%d %H:%M')}"],
        ["git", "push"],
    ]:
        result = subprocess.run(cmd, cwd=_SCRIPT_DIR, capture_output=True, text=True)
        if result.returncode != 0:
            if "nothing to commit" in result.stdout + result.stderr:
                return
            log.warning("git push failed for runs.jsonl: %s", result.stderr.strip() or result.stdout.strip())
            return


def run(file_path=None):
    """Execute the full ticket-creation pipeline.

    Returns a result dict with run summary.
    Raises RuntimeError on configuration or data errors.
    """
    config = load_config()
    min_percent_ingested = config["min_percent_ingested"]
    max_variance = config["max_variance"]
    max_tickets_per_run = config["max_tickets_per_run"]
    dc_lookback_days = config["dc_lookback_days"]
    send_report = config["send_google_chat_report"]

    log.info(
        "Run started — min_pct=%.0f%%, max_var=%s, max_tickets=%d, lookback=%dd",
        min_percent_ingested * 100, max_variance, max_tickets_per_run, dc_lookback_days,
    )

    errors = []
    run_entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "file": None,
        "thresholds": {
            "min_percent_ingested": min_percent_ingested,
            "max_variance": max_variance,
            "max_tickets_per_run": max_tickets_per_run,
            "dc_lookback_days": dc_lookback_days,
        },
        "providers_evaluated": 0,
        "providers_flagged": 0,
        "tickets_created": [],
        "tickets_skipped": 0,
        "errors": errors,
    }

    if not JIRA_API_TOKEN:
        raise RuntimeError("JIRA_API_TOKEN not set in environment")

    if not file_path:
        file_path = find_data_file()
    if not file_path or not os.path.exists(file_path):
        raise RuntimeError(f"No variance file found: {file_path!r}")

    run_entry["file"] = os.path.basename(file_path)

    df = read_power_bi_data(file_path)
    validate_columns(df)
    run_entry["providers_evaluated"] = len(df)

    flagged = filter_problematic_providers(df, min_percent_ingested, max_variance)
    run_entry["providers_flagged"] = len(flagged)
    log.info("%d of %d providers flagged", len(flagged), len(df))

    if len(flagged) == 0:
        log.info("No providers meet ticket-creation criteria. Run complete.")
        _append_run_log(run_entry)
        return run_entry

    # Enrich with existing ticket data
    log.info("Checking for existing tickets (%d providers)...", len(flagged))
    enriched = []
    for _, row in flagged.iterrows():
        result = check_related_tickets(row[COL_PROVIDER_NAME], row[COL_PROVIDER_ID], dc_lookback_days)
        row = row.copy()
        row["_has_open_dc"]       = result["has_open_dc"]
        row["_dc_ticket"]         = result["dc_ticket"]
        row["_dc_status_display"] = result["dc_status_display"]
        row["_dc_status_cat"]     = result["dc_status_category"]
        related_items = []
        if result["dc_ticket"]:
            related_items.append({"key": result["dc_ticket"], "url": f"{JIRA_URL}/browse/{result['dc_ticket']}"})
        related_items += [{"key": r["key"], "url": r["url"]} for r in result["related"]]
        row["_related_items"] = related_items
        enriched.append(row)
    flagged = pd.DataFrame(enriched)

    # Auto-select: skip providers with open DC or recently completed DC story
    def is_eligible(row):
        if row.get("_has_open_dc", False):
            return False
        if str(row.get("_dc_status_display", "")).startswith("Completed"):
            return False
        return True

    eligible = flagged[flagged.apply(is_eligible, axis=1)].head(max_tickets_per_run)
    skipped = len(flagged) - len(eligible)
    run_entry["tickets_skipped"] = skipped
    log.info("%d eligible, %d skipped (existing stories)", len(eligible), skipped)

    # Create tickets
    created = []
    created_tickets = []
    for _, row in eligible.iterrows():
        key = create_jira_ticket(row[COL_PROVIDER_NAME], row[COL_PROVIDER_ID], row[COL_ROLLING_AVG_LOCATIONS])
        if key:
            created.append(key)
            created_tickets.append({
                "key": key,
                "name": row[COL_PROVIDER_NAME],
                "variance": row[COL_VARIANCE],
                "pct_ingested": row[COL_PERCENT_INGESTED],
            })
        else:
            errors.append(f"Failed to create ticket for {row[COL_PROVIDER_NAME]}")

    run_entry["tickets_created"] = created
    log.info("Run complete — created %d ticket(s): %s", len(created), created or "none")
    _append_run_log(run_entry)
    _git_push_run_log()

    if send_report:
        send_google_chat_report(created_tickets, errors)

    return run_entry


def main():
    try:
        run()
        sys.exit(0)
    except Exception as e:
        log.exception("Fatal error: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
