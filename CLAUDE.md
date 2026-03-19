# CLAUDE.md — Jira Ticket Generator

## Project Overview

A Python automation script that reads a Power BI variance report (Excel/CSV) and creates Jira Stories in the `DIT` project at `conservice.atlassian.net` for utility bill data providers that are significantly underperforming their historical ingestion averages.

This is a **proof of concept**. The current workflow is manual; future plans include scheduling/automation and support for additional data sources.

## How to Run

```bash
# Automated run (scheduled/unattended — no prompts)
python jira_ticket_creator.py

# Management interface (configure thresholds, manual trigger)
python manage.py
```

## Environment Setup

Create a `.env` file in the project root:

```
JIRA_API_TOKEN=your_token_here
GOOGLE_CHAT_WEBHOOK=https://chat.googleapis.com/v1/spaces/.../messages?key=...  # required for --report
```

No virtual environment is required. Install dependencies with:

```bash
pip install -r requirements.txt
```

## Data File

Drop the Power BI Excel export into the project directory. The script auto-detects any `.xlsx`, `.xls`, or `.csv` file with "variance" in the filename.

Required columns (must match exactly):
- `Abbyy Name`
- `Abbyy/BC ID`
- `% of Locactions Ingested MTD of Rolling 3 Month Avg`
- `Current Month Expected Variance (Based on 3 Mos Avg)`
- `Rolling 3 Month Avg # of Locations Ingested`

## Key Configuration

Thresholds live in `config.json` (runtime state, gitignored). Edit via `manage.py` or directly. Falls back to hardcoded defaults if absent.

| Key | Default | Meaning |
|---|---|---|
| `min_percent_ingested` | `0.10` | Provider must have ingested ≥10% of locations MTD |
| `max_variance` | `-1000` | Variance must be worse than -1,000 vs 3-month avg |
| `max_tickets_per_run` | `2` | Hard cap on tickets created per execution |
| `dc_lookback_days` | `30` | Days to look back for recently completed DC stories (skip window) |
| `send_google_chat_report` | `false` | Send Google Chat card to THE GOAT space after each run |

Jira constants in `jira_ticket_creator.py`:

| Constant | Value | Meaning |
|---|---|---|
| `PROJECT_KEY` | `DIT` | Jira project target |
| `TITLE_PREFIX` | `[AUTOMATED] - ` | Prefix on ticket titles (remove when out of testing) |
| `customfield_10083` | Acceptance Criteria | DIT Story custom field (textarea, plain string) |
| `customfield_10203` | Definition of Done | DIT Story custom field (textarea, plain string) |
| `customfield_13145` | # of Locations | DIT Story custom field (number — rolling 3-month avg) |

## Jira Safety Rules

- **Never delete Jira tickets** or bulk-modify the backlog.
- **Never create tickets beyond `max_tickets_per_run`** — this cap exists intentionally to prevent flooding the backlog during testing and early rollout. Do not raise it without explicit user instruction.
- The script deduplicates by checking for open tickets with matching provider ID before creating new ones — preserve this behavior.
- Providers with an open DC story or a recently completed one (within `dc_lookback_days`) are automatically skipped.

## Ticket Template

The ticket description (`build_description()`) contains the provider/ID, issue location, and blank placeholders for Issue Description and Example Controls.

Acceptance Criteria (`build_acceptance_criteria()`) and Definition of Done (`build_definition_of_done()`) are sent to their dedicated Jira custom fields (`customfield_10083` and `customfield_10203` respectively). **Do not modify this wording** without explicit instruction — it represents team process agreements.

## Runtime State Files

All three are gitignored.

| File | Purpose |
|---|---|
| `config.json` | Current thresholds. Read at startup; falls back to hardcoded defaults if absent. Edit via `manage.py`. |
| `runs.jsonl` | Append-only run log. One JSON line per execution: timestamp, file, thresholds, providers evaluated/flagged, tickets created, errors. |
| `config_changes.jsonl` | Append-only audit trail of threshold changes: timestamp, field, old value, new value, note. |

## Future Plans

- Additional Power BI data sources / report types
- Scheduled execution via GitHub Webhooks
- Possibly support additional Jira project keys beyond `DIT`
- Data file freshness check: warn/abort if the variance file is older than expected
