"""
Management interface for jira_ticket_creator.
Configure thresholds and manually trigger the script.
"""

import json
import os
import subprocess
from datetime import datetime

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.join(_SCRIPT_DIR, "config.json")
_CONFIG_CHANGES_PATH = os.path.join(_SCRIPT_DIR, "config_changes.jsonl")
_RUNS_PATH = os.path.join(_SCRIPT_DIR, "runs.jsonl")

_DEFAULTS = {
    "min_percent_ingested": 0.10,
    "max_variance": -1000,
    "max_tickets_per_run": 2,
    "dc_lookback_days": 30,
    "send_google_chat_report": False,
}


def load_config():
    config = dict(_DEFAULTS)
    if os.path.exists(_CONFIG_PATH):
        with open(_CONFIG_PATH) as f:
            config.update(json.load(f))
    return config


def save_config(config):
    with open(_CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


def log_config_change(field, old_value, new_value, note=""):
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "field": field,
        "old_value": old_value,
        "new_value": new_value,
        "note": note,
    }
    with open(_CONFIG_CHANGES_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


def display_config(config):
    print()
    print("  Current configuration:")
    print(f"    1. Min % Ingested MTD       {config['min_percent_ingested']:.0%}")
    print(f"    2. Max Variance             {config['max_variance']:,.0f}")
    print(f"    3. Max Tickets per Run      {config['max_tickets_per_run']}")
    print(f"    4. DC Lookback (days)       {config['dc_lookback_days']}")
    print(f"    5. Send Google Chat Report  {'Yes' if config['send_google_chat_report'] else 'No'}")
    print()


def show_config():
    display_config(load_config())


def show_config_changes():
    print()
    print("  Config Change History  (last 20)")
    print("  ─────────────────────────────────")
    if not os.path.exists(_CONFIG_CHANGES_PATH):
        print("  No config changes recorded.")
        print()
        return

    entries = []
    with open(_CONFIG_CHANGES_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    if not entries:
        print("  No config changes recorded.")
        print()
        return

    for entry in reversed(entries[-20:]):
        ts = entry.get("timestamp", "")[:19].replace("T", " ")
        field = entry.get("field", "")
        old = entry.get("old_value", "")
        new = entry.get("new_value", "")
        note = entry.get("note", "")
        change = f"{old} → {new}"
        if note:
            print(f"  {ts}  {field:<30}  {change:<25}  {note}")
        else:
            print(f"  {ts}  {field:<30}  {change}")
    print()


def show_run_history():
    print()
    print("  Run History  (last 10)")
    print("  ──────────────────────")
    if not os.path.exists(_RUNS_PATH):
        print("  No runs recorded.")
        print()
        return

    entries = []
    with open(_RUNS_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    if not entries:
        print("  No runs recorded.")
        print()
        return

    for entry in reversed(entries[-10:]):
        ts = entry.get("timestamp", "")[:19].replace("T", " ")
        file_name = os.path.basename(entry.get("file", "unknown"))
        evaluated = entry.get("providers_evaluated", 0)
        flagged = entry.get("providers_flagged", 0)
        created = entry.get("tickets_created", [])
        skipped = entry.get("tickets_skipped", 0)
        errors = entry.get("errors", [])

        print(f"  {ts}  {file_name}")

        if created or skipped:
            print(f"    Evaluated: {evaluated}  ·  Flagged: {flagged}  ·  Created: {len(created)}  ·  Skipped: {skipped}")
        else:
            print(f"    Evaluated: {evaluated}  ·  Flagged: {flagged}")

        if created:
            print(f"    Tickets : {', '.join(created)}")

        if errors:
            print(f"    Errors  : {'; '.join(str(e) for e in errors)}")

        if not created and not skipped:
            print("    (no tickets created or skipped)")

        print()


def git_push_config(changes, note):
    """Commit and push config.json + config_changes.jsonl after a threshold save."""
    summary = ", ".join(f"{k}={v}" for k, v in changes.items())
    msg = f"config: {note} ({summary})" if note else f"config: {summary}"

    cmds = [
        ["git", "add", "config.json", "config_changes.jsonl"],
        ["git", "commit", "-m", msg],
        ["git", "push"],
    ]
    for cmd in cmds:
        result = subprocess.run(cmd, cwd=_SCRIPT_DIR, capture_output=True, text=True)
        if result.returncode != 0:
            if "nothing to commit" in result.stdout + result.stderr:
                print("  Config unchanged — nothing to push.")
                return
            print(f"  Git error: {result.stderr.strip() or result.stdout.strip()}")
            return
    print("  Config pushed to remote.")


def configure_thresholds():
    config = load_config()
    display_config(config)
    print("  Press Enter to keep the current value.\n")

    changes = {}

    raw = input(f"  Min % Ingested MTD [{config['min_percent_ingested']:.0%}]: ").strip()
    if raw:
        try:
            val = float(raw.strip("%"))
            val = val / 100 if val > 1 else val
            if 0 < val <= 1:
                changes["min_percent_ingested"] = val
            else:
                print(f"  Out of range (0–100%). Keeping {config['min_percent_ingested']:.0%}.")
        except ValueError:
            print(f"  Invalid number. Keeping {config['min_percent_ingested']:.0%}.")

    raw = input(f"  Max Variance [{config['max_variance']:,.0f}]: ").strip()
    if raw:
        try:
            val = float(raw.replace(",", ""))
            if val < 0:
                changes["max_variance"] = val
            else:
                print("  Must be negative. Keeping current value.")
        except ValueError:
            print("  Invalid number. Keeping current value.")

    raw = input(f"  Max Tickets per Run [{config['max_tickets_per_run']}]: ").strip()
    if raw:
        try:
            val = int(raw)
            if val >= 1:
                changes["max_tickets_per_run"] = val
            else:
                print("  Must be >= 1. Keeping current value.")
        except ValueError:
            print("  Invalid integer. Keeping current value.")

    raw = input(f"  DC Lookback Days [{config['dc_lookback_days']}]: ").strip()
    if raw:
        try:
            val = int(raw)
            if val >= 1:
                changes["dc_lookback_days"] = val
            else:
                print("  Must be >= 1. Keeping current value.")
        except ValueError:
            print("  Invalid integer. Keeping current value.")

    current_report = "yes" if config["send_google_chat_report"] else "no"
    raw = input(f"  Send Google Chat Report [{current_report}] (yes/no): ").strip().lower()
    if raw in ("yes", "y"):
        changes["send_google_chat_report"] = True
    elif raw in ("no", "n"):
        changes["send_google_chat_report"] = False

    if not changes:
        print("\n  No changes made.")
        return

    note = input("\n  Note for audit log (optional): ").strip()
    for field, new_value in changes.items():
        log_config_change(field, config[field], new_value, note)
        config[field] = new_value

    save_config(config)
    print(f"\n  Saved {len(changes)} change(s).")
    display_config(config)
    git_push_config(changes, note)


def trigger_run():
    from jira_ticket_creator import run as _run

    print()
    raw = input("  Data file path (Enter to auto-detect): ").strip()
    file_path = raw if raw else None

    print()
    try:
        result = _run(file_path=file_path)
        print(f"  Providers evaluated : {result['providers_evaluated']}")
        print(f"  Providers flagged   : {result['providers_flagged']}")
        print(f"  Tickets created     : {len(result['tickets_created'])}")
        for key in result["tickets_created"]:
            print(f"    https://conservice.atlassian.net/browse/{key}")
        print(f"  Skipped             : {result['tickets_skipped']}")
        if result["errors"]:
            print(f"  Errors              : {len(result['errors'])}")
            for err in result["errors"]:
                print(f"    {err}")
    except Exception as e:
        print(f"  ERROR: {e}")


def main():
    while True:
        print()
        print("  Jira Ticket Generator")
        print("  ─────────────────────")
        print("  1. View current config")
        print("  2. View config change history")
        print("  3. View run history")
        print("  4. Configure thresholds")
        print("  5. Run ticket generator")
        print("  6. Quit")
        print()

        choice = input("  > ").strip()

        if choice == "1":
            show_config()
        elif choice == "2":
            show_config_changes()
        elif choice == "3":
            show_run_history()
        elif choice == "4":
            configure_thresholds()
        elif choice == "5":
            trigger_run()
        elif choice == "6":
            break
        else:
            print("  Invalid choice.")


if __name__ == "__main__":
    main()
