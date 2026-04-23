# CLAUDE.md — Jira Ticket Generator (RFC 67) / Shepherd

## Project Overview

A Python automation script that reads the daily Data Completeness variance report (CSV) and creates Jira Stories in the `ITDC` project at `conservice.atlassian.net` for utility bill data providers that are significantly underperforming their historical ingestion averages. On every run, all open backlog tickets are re-ranked by variance severity so the worst offenders always appear at the top.

## How to Run

```bash
python jira_ticket_creator.py
```

Designed for unattended/scheduled execution — no interactive prompts.

## Environment Setup

Create a `.env` file in the project root:

```
JIRA_API_TOKEN=your_token_here
GOOGLE_CHAT_WEBHOOK=https://chat.googleapis.com/v1/spaces/.../messages?key=...
```

Install dependencies:

```bash
pip install -r requirements.txt
```

## Data File

The variance CSV is auto-updated each morning at 8:30 from Power BI at:

```
U:\Departments\Transformation\Ops Reporting Data\Data Completeness Report\Output\Variance in Bills and Locations Ingested by Bill Central.csv
```

Flexible column detection handles minor renames and the known `Locactions` → `Locations` typo. Required logical columns:

- Provider name (`Abbyy Name`)
- Provider ID (`Abbyy/BC ID`)
- % of Locations Ingested MTD vs 3-Month Avg
- Current Month Expected Variance (vs 3-Month Avg)
- Rolling 3-Month Avg # of Locations Ingested

## Key Configuration

All thresholds are hardcoded constants at the top of `jira_ticket_creator.py` — edit them there directly.

| Constant | Default | Meaning |
|---|---|---|
| `MIN_PERCENT_INGESTED` | `0.10` | Provider must have ingested ≥10% of locations MTD |
| `MAX_VARIANCE` | `-1000` | Variance must be strictly worse than -1,000 vs 3-month avg |
| `DC_LOOKBACK_DAYS` | `30` | Days after completion before a provider re-enters the backlog |
| `SEND_GOOGLE_CHAT_REPORT` | `True` | Send Google Chat card to THE GOAT space after each run |

Jira constants in `jira_ticket_creator.py`:

| Constant | Value | Meaning |
|---|---|---|
| `PROJECT_KEY` | `ITDC` | Jira project (Ingestion Templates Data Completeness) |
| `customfield_10083` | Acceptance Criteria | ITDC Story custom field (textarea) |
| `customfield_10203` | Definition of Done | ITDC Story custom field (textarea) |
| `customfield_13486` | Variance | Variance vs 3-month avg (number) |
| `customfield_13487` | % Ingested | % of locations ingested MTD (number) |

## ITDC Workflow Statuses

| Status | Category | Script behavior |
|---|---|---|
| `To Do` | Backlog | New tickets created here; all re-ranked on every run |
| `In Progress` | Active | Provider skipped — already being worked |
| `Transferred to DIT` | Active | Provider skipped |
| `Needs Follow Up` | Active | Provider skipped |
| `Done` | Complete | Provider skipped for `DC_LOOKBACK_DAYS` after completion date |
| `Quick Fix Complete` | Complete | Same as Done |

## Backlog Re-ranking

On every run the script fetches all `To Do` tickets and re-ranks them by variance (most negative = highest priority = top of backlog). This requires ITDC to be a Jira Software Scrum or Kanban project with the backlog view and ranking enabled. Re-rank API calls log errors but do not fail the overall run if ranking is unavailable.

Ranking uses `PUT /rest/agile/1.0/issue/rank` in batches of 50, processed lowest-priority-first so the highest-priority batch ends at the very top.

## Pre-flight Checks

Before each run the script validates:
- `JIRA_API_TOKEN` is set in the environment
- The data CSV exists at `DATA_FILE_PATH`
- The CSV contains all required columns

Any pre-flight failure is included in the Google Chat webhook report (if enabled) and causes the script to exit with code 1 without touching Jira.

## Jira Safety Rules

- **Never delete Jira tickets.**
- **Never touch tickets outside `To Do` status** — in-progress and completed tickets are read-only.
- The script deduplicates via a single bulk JQL query for all open and recently-completed ITDC stories before creating any new tickets. Provider ID is extracted from the title format `Provider Name (ID)`.
- Providers with an open or in-progress ticket are skipped entirely.
- Providers completed within `DC_LOOKBACK_DAYS` are skipped.

## Ticket Template

**Title format:** `Provider Name (ProviderID)`

The description (`_build_description()`) shows variance, % ingested, and rolling avg at creation time.

Acceptance Criteria (`_build_acceptance_criteria()`) and Definition of Done (`_build_definition_of_done()`) are sent to `customfield_10083` and `customfield_10203` respectively. **Do not modify this wording** without explicit instruction — it represents team process agreements.

## Google Chat Report

When `SEND_GOOGLE_CHAT_REPORT = True`, posts a Card v2 to THE GOAT space containing:

1. **New Tickets Created** — tickets opened this run (or "No new tickets created.")
2. **Top 5 Backlog** — highest-priority `To Do` tickets after re-ranking, with variance and % ingested
3. **Errors** — pre-flight failures or ticket creation errors (if any)

The report is sent even when the run fails early (e.g., data file missing), so errors are always surfaced via the webhook.

## Phase 2 Roadmap

### Scheduled Execution and Alerting

1. **Windows Task Scheduler** — Deploy as a scheduled wrapper on an on-prem server, triggered daily at 9:00 AM (after the 8:30 CSV refresh).
2. **Secrets in Credential Manager** — Replace the `.env` file with Windows Credential Manager lookups so credentials are not stored in plaintext on disk.
3. **DataDog monitoring** — Emit a custom metric on each run (tickets created, errors, providers evaluated). Alert on consecutive failures or unexpected zero-coverage days.
4. **Staleness check** — Abort and alert if the variance CSV has not been updated since the last expected refresh time.
5. **Remote trigger** — Allow a manual run via a GitHub Actions workflow dispatch or inbound webhook.
