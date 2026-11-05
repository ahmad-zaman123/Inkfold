"""Measures how well Inkfold reads documents, using the test set in evals/.

Each document goes through the same code as an upload (Gemini, then the checks and clean-up),
and every field is compared with the known answer. It calls the real Gemini API once per document.

    GEMINI_API_KEY=... python scripts/eval_accuracy.py            # reads only documents not read yet
    GEMINI_API_KEY=... python scripts/eval_accuracy.py --fresh    # reads them all again

Replies are saved in evals/runs/, so a run cut short by a daily limit picks up where it stopped,
and the scoring can change without asking Gemini again. Results go to evals/results.md.
"""

import argparse
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv  # noqa: E402

from app.config import DEFAULT_EXTRACT_MODELS  # noqa: E402
from app.extract import ExtractionFailed, ExtractionUnavailable, _client, extract_document  # noqa: E402
from app.main import sniff_mime  # noqa: E402
from app.normalize import normalize_fields  # noqa: E402
from app.schema import FIELDS  # noqa: E402

EVALS = BACKEND.parent / "evals"
RUNS = EVALS / "runs"
# The review screen asks for a closer look below this confidence, as well as for any failed check.
LOW_CONFIDENCE = 0.7
PAUSE_SECONDS = 5  # The free tier allows about 15 requests a minute.
KINDS = {"business_card": "Business cards", "invoice": "Invoices", "lead_form": "Lead forms", "email": "Emails and notes"}


class Recorder:
    """Passes calls to the Gemini client and notes which model answered."""

    def __init__(self, client):
        # Holding the client itself keeps its connection open; with only .models it gets closed.
        self.client = client
        self.inner = client.models
        self.models = self
        self.last_model = None

    def generate_content(self, model, **kwargs):
        reply = self.inner.generate_content(model=model, **kwargs)
        self.last_model = model
        return reply


def read_case(case, settings, recorder):
    data = (EVALS / "cases" / case["file"]).read_bytes()
    mime = sniff_mime(data, case["file"])
    try:
        extracted = extract_document(data, mime, settings, client=recorder)
    except ExtractionFailed as failure:
        return {"failed": str(failure), "model": recorder.last_model}
    return {"doc_type": extracted["doc_type"], "fields": extracted["fields"], "model": recorder.last_model}


def run(cases, fresh):
    # A key in Backend/.env works too, so it never has to be typed on the command line.
    load_dotenv(BACKEND / ".env")
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("Set GEMINI_API_KEY first.")
    models = [m.strip() for m in os.environ.get("GEMINI_EXTRACT_MODELS", "").split(",") if m.strip()] or list(DEFAULT_EXTRACT_MODELS)
    settings = {"gemini_api_key": key, "extract_models": models}
    recorder = Recorder(_client(key))
    RUNS.mkdir(exist_ok=True)
    todo = [c for c in cases if fresh or not (RUNS / f"{c['id']}.json").exists()]
    print(f"{len(cases) - len(todo)} already read, {len(todo)} to read with Gemini.")
    for index, case in enumerate(todo):
        if index:
            time.sleep(PAUSE_SECONDS)
        for attempt in (1, 2):
            try:
                reply = read_case(case, settings, recorder)
                break
            except ExtractionUnavailable:
                if attempt == 2:
                    print("Gemini is busy or out of quota for today. Run this again later to carry on.")
                    return
                print("  Gemini is busy, waiting a minute...")
                time.sleep(60)
        (RUNS / f"{case['id']}.json").write_text(json.dumps(reply, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"  {case['id']}: {'failed' if 'failed' in reply else 'read'} ({reply.get('model')})")


# Scoring


def _text(value):
    return re.sub(r"\s+", " ", str(value)).strip().strip(".,;").casefold()


def _tokens(value):
    return sorted(re.findall(r"[^\W_]+", str(value).casefold()))


def same(key, got, want):
    if want is None or got is None:
        return want is None and got is None
    kind = FIELDS[key]["type"]
    if key == "company.address":
        return _tokens(got) == _tokens(want)
    if kind == "phone" and not str(want).startswith("+"):
        # A number with no country code stays as written, flagged. The digits must match.
        return re.sub(r"\D", "", str(got)) == re.sub(r"\D", "", str(want))
    if kind in ("email", "domain", "currency"):
        return str(got).lower() == str(want).lower()
    if kind in ("date", "money", "phone"):
        return str(got) == str(want)
    return _text(got) == _text(want)


def score(cases):
    rows = []
    for case in cases:
        path = RUNS / f"{case['id']}.json"
        if not path.exists():
            continue
        reply = json.loads(path.read_text(encoding="utf-8"))
        kind = "email" if case["file"].endswith(".txt") else case["doc_type"]
        types = case["doc_type"] if isinstance(case["doc_type"], list) else [case["doc_type"]]
        fields = normalize_fields(reply["fields"]) if "fields" in reply else None
        row = {"id": case["id"], "kind": kind, "model": reply.get("model"), "failed": reply.get("failed"),
               "doc_type_ok": reply.get("doc_type") in types, "fields": []}
        for key, wanted in case["fields"].items():
            options = wanted if isinstance(wanted, list) else [wanted]
            field = fields[key] if fields else {"value": None, "issues": [], "confidence": 0.0}
            got = field["value"]
            correct = any(same(key, got, option) for option in options)
            present = any(option is not None for option in options)
            if correct:
                outcome = "correct"
            elif got is None:
                outcome = "missed"
            else:
                # A value where the document has none was made up; any other mismatch is just wrong.
                outcome = "wrong" if present else "invented"
            # Only a value can be flagged. An empty field is plain to see anyway.
            flagged = got is not None and (bool(field["issues"]) or field["confidence"] < LOW_CONFIDENCE)
            # A phone number with no country code should be flagged even when it's read right.
            should_flag = FIELDS[key]["type"] == "phone" and any(o and not str(o).startswith("+") for o in options)
            row["fields"].append({"key": key, "outcome": outcome, "flagged": flagged, "should_flag": should_flag,
                                  "got": got, "want": wanted, "present": present})
        if fields and case.get("line_items"):
            items = fields["invoice.line_items"]
            row["line_items"] = {"want": case["line_items"], "got": len(items["value"] or []),
                                 "adds_up": bool(items["value"]) and fields["invoice.total"]["value"] is not None
                                 and not any("add up" in issue for issue in fields["invoice.total"]["issues"])}
        rows.append(row)
    return rows


def short(model):
    return model.removeprefix("models/")


def pct(part, whole):
    return f"{100 * part / whole:.0f}%" if whole else "n/a"


def report(rows, total_cases):
    results = [f for row in rows for f in row["fields"]]
    outcomes = Counter(f["outcome"] for f in results)
    present = [f for f in results if f["present"]]
    absent = [f for f in results if not f["present"]]
    errors = [f for f in results if f["outcome"] in ("wrong", "invented")]
    caught = [f for f in errors if f["flagged"]]
    correct_flagged = [f for f in results if f["outcome"] == "correct" and f["flagged"] and not f["should_flag"]]
    meant = [f for f in results if f["should_flag"]]
    models = Counter(row["model"] for row in rows if row["model"])
    lines = [
        "# Extraction accuracy",
        "",
        f"Measured on {date.today().isoformat()} with `Backend/scripts/eval_accuracy.py`, on the {total_cases} made-up documents in `evals/cases/`.",
        "Each one went through the same code as an upload: Gemini reads it, then Inkfold checks and cleans every field.",
        "Models that answered: " + ", ".join(f"`{short(m)}` ({n})" for m, n in models.most_common()) + ".",
        "",
    ]
    if len(rows) < total_cases:
        lines += [f"**Partial run: {len(rows)} of {total_cases} documents read so far.**", ""]
    lines += [
        "## Summary",
        "",
        "| Measure | Result |",
        "| --- | --- |",
        f"| Fields read correctly, where the document has the value | **{pct(sum(f['outcome'] == 'correct' for f in present), len(present))}** ({sum(f['outcome'] == 'correct' for f in present)} of {len(present)}) |",
        f"| Fields correctly left blank, where the document doesn't have them | {pct(sum(f['outcome'] == 'correct' for f in absent), len(absent))} ({sum(f['outcome'] == 'correct' for f in absent)} of {len(absent)}) |",
        f"| All fields, both kinds together | {pct(outcomes['correct'], len(results))} ({outcomes['correct']} of {len(results)}) |",
        f"| Wrong or made-up values that the review screen flagged | {pct(len(caught), len(errors))} ({len(caught)} of {len(errors)}) |",
        f"| Phone numbers with no country code, flagged as they should be | {sum(f['flagged'] for f in meant)} of {len(meant)} |",
        f"| Correct values flagged anyway (extra checking for the reviewer) | {len(correct_flagged)} |",
        f"| Document type recognised | {pct(sum(r['doc_type_ok'] for r in rows), len(rows))} ({sum(r['doc_type_ok'] for r in rows)} of {len(rows)}) |",
    ]
    invoices = [r for r in rows if "line_items" in r]
    if invoices:
        lines.append(f"| Invoices whose line items add up to the total | {pct(sum(r['line_items']['adds_up'] for r in invoices), len(invoices))} ({sum(r['line_items']['adds_up'] for r in invoices)} of {len(invoices)}) |")
    lines += ["", "A value is flagged when one of Inkfold's checks fails or Gemini's confidence is under 70%.",
              "Missed values show up as empty fields, which the reviewer sees too.", "", "## By document type", "",
              "| Type | Documents | Fields read correctly | Left blank correctly |", "| --- | --- | --- | --- |"]
    by_kind = defaultdict(list)
    for row in rows:
        by_kind[row["kind"]].append(row)
    for kind, label in KINDS.items():
        group = [f for row in by_kind.get(kind, []) for f in row["fields"]]
        if not group:
            continue
        p = [f for f in group if f["present"]]
        a = [f for f in group if not f["present"]]
        lines.append(f"| {label} | {len(by_kind[kind])} | {pct(sum(f['outcome'] == 'correct' for f in p), len(p))} | {pct(sum(f['outcome'] == 'correct' for f in a), len(a))} |")
    lines += ["", "## By field", "", "| Field | Correct | Of |", "| --- | --- | --- |"]
    by_field = defaultdict(list)
    for f in results:
        by_field[f["key"]].append(f)
    for key, group in by_field.items():
        lines.append(f"| {FIELDS[key]['label']} | {pct(sum(f['outcome'] == 'correct' for f in group), len(group))} | {len(group)} |")
    mistakes = [(row["id"], f) for row in rows for f in row["fields"] if f["outcome"] != "correct"]
    lines += ["", "## Every mistake", ""]
    if not mistakes:
        lines.append("None.")
    else:
        lines += ["| Document | Field | Expected | Got | Kind | Flagged |", "| --- | --- | --- | --- | --- | --- |"]
        for case_id, f in mistakes:
            want = " or ".join("blank" if w is None else str(w) for w in (f["want"] if isinstance(f["want"], list) else [f["want"]]))
            got = "blank" if f["got"] is None else str(f["got"])
            lines.append(f"| {case_id} | {FIELDS[f['key']]['label']} | {want.replace('|', '/')} | {got.replace('|', '/')} | {f['outcome']} | {'n/a' if f['got'] is None else 'yes' if f['flagged'] else 'no'} |")
    failed = [row for row in rows if row["failed"]]
    if failed:
        lines += ["", "Documents Inkfold couldn't read at all: " + ", ".join(f"{r['id']} ({r['failed']})" for r in failed)]
    wrong_type = [row["id"] for row in rows if not row["doc_type_ok"]]
    if wrong_type:
        lines += ["", "Wrong document type: " + ", ".join(wrong_type)]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--fresh", action="store_true", help="read every document again")
    parser.add_argument("--score-only", action="store_true", help="score the saved replies without calling Gemini")
    args = parser.parse_args()
    cases = json.loads((EVALS / "expected.json").read_text(encoding="utf-8"))
    if not args.score_only:
        run(cases, args.fresh)
    rows = score(cases)
    if not rows:
        sys.exit("Nothing has been read yet.")
    (EVALS / "results.md").write_text(report(rows, len(cases)), encoding="utf-8")
    (EVALS / "results.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    print(f"Scored {len(rows)} documents. See evals/results.md.")


if __name__ == "__main__":
    main()
