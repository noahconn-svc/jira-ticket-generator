"""
Jira Ticket Creator — RFC 67: ITDC Data Completeness
Creates and re-ranks ITDC stories for providers with significant variance
from their 3-month ingestion average.

Run: python jira_ticket_creator.py
"""

import logging
import os
import re
import sys
from datetime import datetime

import pandas as pd
import requests
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth

load_dotenv()

# ── Credentials ───────────────────────────────────────────────────────────────
JIRA_URL = "https://conservice.atlassian.net"
JIRA_EMAIL = "nconn@conservice.com"
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")
GOOGLE_CHAT_WEBHOOK = os.getenv("GOOGLE_CHAT_WEBHOOK")

# ── Project ───────────────────────────────────────────────────────────────────
PROJECT_KEY = "ITDC"

STATUS_BACKLOG       = "To Do"
STATUSES_IN_PROGRESS = ("In Progress", "Transferred to DIT", "Needs Follow Up")
STATUSES_DONE        = ("Done", "Quick Fix Complete")

# ── Data source ───────────────────────────────────────────────────────────────
DATA_FILE_PATH = (
    r"U:\Departments\Transformation\Ops Reporting Data"
    r"\Data Completeness Report\Output"
    r"\Variance in Bills and Locations Ingested by Bill Central.csv"
)

# ── Thresholds — edit here to change behavior ─────────────────────────────────
MIN_PERCENT_INGESTED    = 0.10   # Provider must have ingested ≥ this % of locations MTD
MAX_VARIANCE            = -1500  # Variance must be worse than this value vs 3-month avg
DC_LOOKBACK_DAYS        = 30     # Days after completion before a provider re-enters the backlog
SEND_GOOGLE_CHAT_REPORT = True

# ── Column mapping ────────────────────────────────────────────────────────────
_COLUMNS = {
    "provider_name":    "AbbyyName",
    "provider_id":      "AbbyyBCID",
    "percent_ingested": "PercentIngested",
    "variance":         "CurrentVariance",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)


# ── Pre-flight checks ─────────────────────────────────────────────────────────

def preflight_checks():
    """Validate configuration and data before running. Returns list of error strings."""
    errors = []
    if not JIRA_API_TOKEN:
        errors.append("JIRA_API_TOKEN not set in .env")
    if not os.path.exists(DATA_FILE_PATH):
        errors.append(f"Data file not found: {DATA_FILE_PATH}")
    else:
        try:
            df_head = pd.read_csv(DATA_FILE_PATH, nrows=0)
            _detect_columns(df_head)
        except Exception as e:
            errors.append(f"Data file column error: {e}")
    return errors


# ── Data loading ──────────────────────────────────────────────────────────────

def _detect_columns(df):
    """Map logical column keys to actual DataFrame column names (case-insensitive)."""
    normalized = {c.strip().lower(): c for c in df.columns}
    result = {}
    for key, col in _COLUMNS.items():
        if col in df.columns:
            result[key] = col
        elif col.strip().lower() in normalized:
            result[key] = normalized[col.strip().lower()]
        else:
            raise ValueError(
                f"Could not find column '{key}' (expected: '{col}'). "
                f"Available: {list(df.columns)}"
            )
    return result


def load_data():
    """Load the variance CSV and return (df, cols)."""
    df = pd.read_csv(DATA_FILE_PATH)
    log.info("Loaded %d providers from data file", len(df))
    cols = _detect_columns(df)
    return df, cols


def filter_flagged(df, cols):
    """Return providers that meet both thresholds, sorted worst variance first."""
    mask = (
        (df[cols["percent_ingested"]] >= MIN_PERCENT_INGESTED)
        & (df[cols["variance"]] < MAX_VARIANCE)
    )
    return df[mask].copy().sort_values(cols["variance"]).reset_index(drop=True)


# ── Jira helpers ──────────────────────────────────────────────────────────────

def _auth():
    return HTTPBasicAuth(JIRA_EMAIL, JIRA_API_TOKEN)


def _headers():
    return {"Accept": "application/json", "Content-Type": "application/json"}


def _jql_search(jql, fields, max_results=100):
    """Run a JQL search and return all matching issues (handles pagination)."""
    issues = []
    next_page_token = None
    while True:
        body = {"jql": jql, "fields": fields, "maxResults": max_results}
        if next_page_token:
            body["nextPageToken"] = next_page_token
        resp = requests.post(
            f"{JIRA_URL}/rest/api/3/search/jql",
            json=body,
            auth=_auth(),
            headers=_headers(),
        )
        if resp.status_code != 200:
            log.error("JQL search failed (%s): %s | response: %s", resp.status_code, jql[:120], resp.text[:300])
            break
        data = resp.json()
        batch = data.get("issues", [])
        issues.extend(batch)
        if data.get("isLast", True) or not batch:
            break
        next_page_token = data.get("nextPageToken")
        if not next_page_token:
            break
    return issues


def get_existing_tickets():
    """Fetch all ITDC stories that are open or recently completed (within lookback window).

    Returns dict: str(provider_id) -> {"key": str, "status": str}
    Provider ID is parsed from the title format: "Provider Name (12345)"
    """
    done_statuses = ', '.join(f'"{s}"' for s in STATUSES_DONE)
    jql = (
        f'project = {PROJECT_KEY} AND issuetype = Story '
        f'AND (status not in ({done_statuses}) OR updated >= "-{DC_LOOKBACK_DAYS}d")'
    )
    issues = _jql_search(jql, ["summary", "status"])
    result = {}
    for issue in issues:
        m = re.search(r'\((\d+)\)\s*$', issue["fields"]["summary"])
        if m:
            pid = m.group(1)
            result[pid] = {
                "key": issue["key"],
                "status": issue["fields"]["status"]["name"],
            }
    log.info("Found %d relevant existing ITDC stories", len(result))
    return result


def update_jira_ticket(key, variance, pct_ingested):
    """Update variance and % ingested fields on an existing ITDC story."""
    resp = requests.put(
        f"{JIRA_URL}/rest/api/3/issue/{key}",
        json={"fields": {"customfield_13486": variance, "customfield_13487": pct_ingested}},
        auth=_auth(),
        headers=_headers(),
    )
    if resp.status_code == 204:
        log.info("Updated %s (variance=%s)", key, f"{variance:,.0f}")
        return True
    log.error("Failed to update %s: %s %s", key, resp.status_code, resp.text[:300])
    return False


def create_jira_ticket(name, provider_id, variance, pct_ingested):
    """Create a single ITDC story. Returns the ticket key or None on failure."""
    title = f"{name} ({int(provider_id)})"
    payload = {
        "fields": {
            "project": {"key": PROJECT_KEY},
            "summary": title,
            "issuetype": {"name": "Story"},
            "customfield_13486": variance,
            "customfield_13487": pct_ingested,
        }
    }
    resp = requests.post(
        f"{JIRA_URL}/rest/api/3/issue",
        json=payload,
        auth=_auth(),
        headers=_headers(),
    )
    if resp.status_code == 201:
        key = resp.json()["key"]
        log.info("Created %s: %s (variance=%s)", key, title, f"{variance:,.0f}")
        return key
    log.error(
        "Failed to create ticket for %s: %s %s",
        name, resp.status_code, resp.text[:300],
    )
    return None


# ── Backlog re-ranking ────────────────────────────────────────────────────────

def rerank_backlog(variance_by_id):
    """Re-rank all ITDC To Do tickets by variance (worst first = top of backlog).

    variance_by_id: dict mapping str(provider_id) -> float variance

    Processes batches of 50 from lowest-priority to highest-priority, each ranked
    before the first issue of the previous batch, so the highest-priority batch
    ends at the top.

    Returns list of (key, provider_id_or_None) in ranked order (index 0 = top).
    """
    issues = _jql_search(
        f'project = {PROJECT_KEY} AND issuetype = Story AND status = "{STATUS_BACKLOG}"',
        ["summary"],
    )
    if not issues:
        log.info("No backlog tickets to re-rank")
        return []

    def _variance_for(issue):
        m = re.search(r'\((\d+)\)\s*$', issue["fields"]["summary"])
        if m:
            return variance_by_id.get(m.group(1), 0)
        return 0  # tickets without parseable ID go to bottom

    sorted_issues = sorted(issues, key=_variance_for)  # ascending: most negative first
    sorted_keys = [i["key"] for i in sorted_issues]
    sorted_pids = []
    for issue in sorted_issues:
        m = re.search(r'\((\d+)\)\s*$', issue["fields"]["summary"])
        sorted_pids.append(m.group(1) if m else None)

    log.info("Re-ranking %d backlog tickets", len(sorted_keys))

    BATCH = 50
    prev_batch_first = None
    for i in range(len(sorted_keys) - 1, -1, -BATCH):
        batch = sorted_keys[max(0, i - BATCH + 1):i + 1]
        if prev_batch_first is None:
            if len(batch) == 1:
                prev_batch_first = batch[0]
                continue
            body = {"issues": batch[:-1], "rankBeforeIssue": batch[-1]}
        else:
            body = {"issues": batch, "rankBeforeIssue": prev_batch_first}

        resp = requests.put(
            f"{JIRA_URL}/rest/agile/1.0/issue/rank",
            json=body,
            auth=_auth(),
            headers=_headers(),
        )
        if resp.status_code in (200, 204):
            prev_batch_first = batch[0]
        else:
            log.error("Re-rank batch failed (%s): %s", resp.status_code, resp.text[:300])
            break

    return list(zip(sorted_keys, sorted_pids))


# ── Google Chat report ────────────────────────────────────────────────────────

def send_google_chat_report(created_tickets, top5_backlog, errors):
    """POST a Google Chat Card v2 to THE GOAT space.

    created_tickets: list of {key, name, variance, pct_ingested}
    top5_backlog:    list of {key, name, variance, pct_ingested}
    errors:          list of str
    """
    if not GOOGLE_CHAT_WEBHOOK:
        log.error("GOOGLE_CHAT_WEBHOOK not set in .env")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    n = len(created_tickets)
    e = len(errors)

    def _ticket_widget(t):
        return {"decoratedText": {
            "topLabel": t["name"],
            "text": f"Variance: {t['variance']:,.0f} · {t['pct_ingested']:.1%} ingested",
            "button": {
                "text": t["key"],
                "onClick": {"openLink": {"url": f"{JIRA_URL}/browse/{t['key']}"}},
            },
        }}

    sections = []

    sections.append({
        "header": "New Tickets Created",
        "widgets": (
            [_ticket_widget(t) for t in created_tickets]
            if created_tickets
            else [{"textParagraph": {"text": "No new tickets created."}}]
        ),
    })

    if top5_backlog:
        sections.append({
            "header": "Top 5 Backlog",
            "widgets": [_ticket_widget(t) for t in top5_backlog],
        })

    if errors:
        sections.append({
            "header": "Errors",
            "widgets": [{"textParagraph": {"text": err}} for err in errors],
        })

    payload = {"cardsV2": [{"cardId": "itdc-dc-report", "card": {
        "header": {
            "title": "ITDC Data Completeness Report",
            "subtitle": (
                f"{today} · {n} new ticket{'s' if n != 1 else ''}"
                f", {e} error{'s' if e != 1 else ''}"
            ),
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


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run():
    """Execute the full pipeline. Returns dict with created_ticket_data, top5_backlog, errors."""
    log.info(
        "Run started — project=%s, min_pct=%.0f%%, max_var=%s, lookback=%dd",
        PROJECT_KEY, MIN_PERCENT_INGESTED * 100, MAX_VARIANCE, DC_LOOKBACK_DAYS,
    )

    errors = []
    df, cols = load_data()
    flagged = filter_flagged(df, cols)
    log.info("%d of %d providers flagged", len(flagged), len(df))

    # Build provider lookup for re-ranking and report enrichment
    provider_info = {}
    for _, row in df.iterrows():
        if pd.notna(row[cols["provider_id"]]):
            try:
                pid = str(int(row[cols["provider_id"]]))
                provider_info[pid] = {
                    "name": row[cols["provider_name"]],
                    "variance": float(row[cols["variance"]]),
                    "pct_ingested": float(row[cols["percent_ingested"]]),
                }
            except (ValueError, TypeError):
                pass
    variance_by_id = {pid: info["variance"] for pid, info in provider_info.items()}

    existing = get_existing_tickets()

    created_ticket_data = []
    tickets_skipped = 0
    tickets_updated = 0
    for _, row in flagged.iterrows():
        try:
            pid = str(int(row[cols["provider_id"]]))
        except (ValueError, TypeError):
            log.warning("Skipping row with invalid provider_id: %s", row[cols["provider_id"]])
            continue

        if pid in existing:
            entry = existing[pid]
            if entry["status"] == STATUS_BACKLOG:
                if update_jira_ticket(entry["key"], float(row[cols["variance"]]), float(row[cols["percent_ingested"]])):
                    tickets_updated += 1
                else:
                    errors.append(f"Failed to update ticket {entry['key']} for {row[cols['provider_name']]} ({pid})")
            else:
                tickets_skipped += 1
                log.debug(
                    "Skip %s (%s) — existing ticket %s (%s)",
                    row[cols["provider_name"]], pid,
                    entry["key"], entry["status"],
                )
            continue

        key = create_jira_ticket(
            row[cols["provider_name"]],
            row[cols["provider_id"]],
            float(row[cols["variance"]]),
            float(row[cols["percent_ingested"]]),
        )
        if key:
            created_ticket_data.append({
                "key": key,
                "name": row[cols["provider_name"]],
                "variance": float(row[cols["variance"]]),
                "pct_ingested": float(row[cols["percent_ingested"]]),
            })
        else:
            errors.append(f"Failed to create ticket for {row[cols['provider_name']]} ({pid})")

    log.info("Created %d ticket(s), updated %d, skipped %d", len(created_ticket_data), tickets_updated, tickets_skipped)

    ranked = rerank_backlog(variance_by_id)

    top5_backlog = []
    for key, pid in ranked[:5]:
        info = provider_info.get(pid) if pid else None
        if info:
            top5_backlog.append({"key": key, **info})

    return {
        "created_ticket_data": created_ticket_data,
        "top5_backlog": top5_backlog,
        "errors": errors,
    }


def main():
    created_ticket_data = []
    top5_backlog = []
    errors = []

    try:
        errors = preflight_checks()
        if not errors:
            result = run()
            created_ticket_data = result["created_ticket_data"]
            top5_backlog = result["top5_backlog"]
            errors = result["errors"]
    except Exception as e:
        log.exception("Fatal error: %s", e)
        errors.append(f"Fatal error: {e}")

    if SEND_GOOGLE_CHAT_REPORT:
        send_google_chat_report(created_ticket_data, top5_backlog, errors)

    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
