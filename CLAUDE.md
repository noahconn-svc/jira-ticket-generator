# CLAUDE.md — Jira Ticket Generator

## Project Overview

A Python automation script that reads a Power BI variance report (Excel/CSV) and creates Jira Stories in the `DIT` project at `conservice.atlassian.net` for utility bill data providers that are significantly underperforming their historical ingestion averages.

This is a **proof of concept**. The current workflow is manual; future plans include scheduling/automation and support for additional data sources.

## How to Run

```bash
# Dry run (preview only — no tickets created)
python jira_ticket_creator.py --dry-run

# Live run (requires JIRA_API_TOKEN in .env)
python jira_ticket_creator.py

# Specify a data file explicitly
python jira_ticket_creator.py --file "path/to/report.xlsx"
```

## Environment Setup

Create a `.env` file in the project root:

```
JIRA_API_TOKEN=your_token_here
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

## Key Configuration (jira_ticket_creator.py)

| Constant | Value | Meaning |
|---|---|---|
| `MIN_PERCENT_INGESTED` | `0.10` | Provider must have ingested ≥10% of locations MTD |
| `MAX_VARIANCE` | `-1000` | Variance must be worse than -1,000 vs 3-month avg |
| `MAX_TICKETS_PER_RUN` | `2` | Hard cap on tickets created per execution |
| `PROJECT_KEY` | `DIT` | Jira project target |
| `TITLE_PREFIX` | `[AUTOMATED] - ` | Prefix on ticket titles (remove when out of testing) |
| `customfield_10083` | Acceptance Criteria | DIT Story custom field (textarea, plain string) |
| `customfield_10203` | Definition of Done | DIT Story custom field (textarea, plain string) |
| `customfield_13145` | # of Locations | DIT Story custom field (number — rolling 3-month avg) |

## Jira Safety Rules

- **Never delete Jira tickets** or bulk-modify the backlog.
- **Never create tickets beyond `MAX_TICKETS_PER_RUN`** — this cap exists intentionally to prevent flooding the backlog during testing and early rollout. Do not raise it without explicit user instruction.
- The script already deduplicates by checking for open tickets with matching provider ID before creating new ones — preserve this behavior.
- Always prefer `--dry-run` when testing changes to ticket content or filtering logic.

## Ticket Template

The ticket description (`build_description()`) contains the provider/ID, issue location, and blank placeholders for Issue Description and Example Controls.

Acceptance Criteria (`build_acceptance_criteria()`) and Definition of Done (`build_definition_of_done()`) are sent to their dedicated Jira custom fields (`customfield_10083` and `customfield_10203` respectively). **Do not modify this wording** without explicit instruction — it represents team process agreements.

## Future Plans

- Additional Power BI data sources / report types
- Scheduled/automated execution (cron or task scheduler)
- Possibly support additional Jira project keys beyond `DIT`
