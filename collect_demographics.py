#!/usr/bin/env python3
"""
collect_demographics.py - recover the "about you" (demographics) form for a
participant who finished a session without completing it.

The demographics form is normally a modal inside session_controller.py that
fires near the end of a session. There is no standalone flag to re-run just
that modal, so this tool reproduces it: same four questions, same options, same
EN/PT labels, and it writes the SAME file the controller writes, in the SAME
schema, so the analysis cannot tell the difference:

    $THESIS_DIR/participants/<PID>/demographics.json

Usage:
    python collect_demographics.py P09
    python collect_demographics.py P09 --language pt
    python collect_demographics.py P09 --session-id P09_20260603T....Z

If --session-id is omitted, the script finds the participant's most recent
session directory and uses that ID for the record's session_id field.
"""

import os
import argparse
import json
import sys
import datetime as dt
from pathlib import Path

THESIS_DIR = Path(os.environ.get("THESIS_DIR", Path(__file__).resolve().parent))
PARTICIPANTS_DIR = THESIS_DIR / "participants"
SESSIONS_DIR = THESIS_DIR / "sessions"

# Questions, options, and labels copied verbatim from session_controller.py
# STRINGS so the recovered record matches a controller-collected one exactly.
QUESTIONS = [
    ("age_range", {
        "en": ("Age range", ['18-29', '30-39', '40-49', '50+'],
                             ['18-29', '30-39', '40-49', '50+']),
        "pt": ("Faixa etária", ['18-29', '30-39', '40-49', '50+'],
                               ['18-29', '30-39', '40-49', '50+']),
    }),
    ("gender", {
        "en": ("Gender",
               ['Female', 'Male', 'Non-binary', 'Prefer not to say'],
               ['Female', 'Male', 'Non-binary', 'Prefer not to say']),
        "pt": ("Género",
               ['Female', 'Male', 'Non-binary', 'Prefer not to say'],
               ['Feminino', 'Masculino', 'Não-binário', 'Prefiro não responder']),
    }),
    ("native_language", {
        "en": ("Native language",
               ['Portuguese', 'English', 'Other'],
               ['Portuguese', 'English', 'Other']),
        "pt": ("Língua materna",
               ['Portuguese', 'English', 'Other'],
               ['Português', 'Inglês', 'Outra']),
    }),
    ("experience_years", {
        "en": ("Years of professional knowledge-work experience",
               ['<1', '1-3', '4-7', '8-15', '15+'],
               ['<1', '1-3', '4-7', '8-15', '15+']),
        "pt": ("Anos de experiência em trabalho de escritório ou intelectual",
               ['<1', '1-3', '4-7', '8-15', '15+'],
               ['<1', '1-3', '4-7', '8-15', '15+']),
    }),
]


def now_utc_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def find_latest_session_id(pid):
    if not SESSIONS_DIR.is_dir():
        return None
    matches = sorted(SESSIONS_DIR.glob(f"{pid}_*"))
    return matches[-1].name if matches else None


def ask(question_label, display_labels, canonical_values):
    """Present a numbered choice list, return the canonical value chosen."""
    print(f"\n{question_label}")
    for i, lab in enumerate(display_labels, 1):
        print(f"  {i}. {lab}")
    while True:
        raw = input("  Choice [number]: ").strip()
        if raw.isdigit():
            n = int(raw)
            if 1 <= n <= len(display_labels):
                return canonical_values[n - 1]
        print("  Please enter a number from the list.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("participant_id", help="e.g. P09")
    ap.add_argument("--language", choices=("en", "pt"), default="en")
    ap.add_argument("--session-id", default=None,
                    help="session_id for the record; defaults to the participant's latest session")
    args = ap.parse_args()

    pid = args.participant_id
    lang = args.language

    pdir = PARTICIPANTS_DIR / pid
    demo_path = pdir / "demographics.json"

    if demo_path.exists():
        print(f"WARNING: demographics.json already exists for {pid}:")
        print(f"  {demo_path}")
        if input("Overwrite it? [y/N] ").strip().lower() != "y":
            print("Aborted. Nothing changed.")
            return 1

    session_id = args.session_id or find_latest_session_id(pid)
    if not session_id:
        print(f"NOTE: no session directory found for {pid}; session_id will be null.")
    else:
        print(f"Recording demographics for {pid} (session_id: {session_id}, lang: {lang})")

    responses = {}
    for key, by_lang in QUESTIONS:
        label, canon, display = by_lang[lang]
        responses[key] = ask(label, display, canon)

    record = {
        **responses,
        "recorded_utc": now_utc_iso(),
        "recorded_language": lang,
        "session_id": session_id,
        "collected_via": "collect_demographics_recovery",
    }

    print("\nReview:")
    print(json.dumps(record, indent=2, ensure_ascii=False))
    if input("\nSave this? [Y/n] ").strip().lower() == "n":
        print("Aborted. Nothing saved.")
        return 1

    pdir.mkdir(parents=True, exist_ok=True)
    demo_path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    print(f"\nSaved: {demo_path}")
    print("This participant will now be treated as having demographics on file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
