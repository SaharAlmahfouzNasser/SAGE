"""
parse_results.py

Run this locally, in the same environment where your SAGE batch results live.
It extracts just the fields needed for analysis from each raw hypothesis
transcript (endpoint, recommendation, a short hypothesis snippet, and a
flag-language heuristic for the negative/wrong-premise rows) and writes one
compact CSV. You only need to send back the output CSV (parsed_results.csv)
plus metrics.csv -- no need to upload the raw transcripts themselves.

USAGE:
    python parse_results.py \
        --results_dir experiment_results/full_run \
        --questions SAGE_experiment_questions.csv \
        --out parsed_results.csv

Assumes transcripts are plain text/log files somewhere under --results_dir
(searched recursively, any extension) and follow the same agent-transcript
format seen throughout this project (TextMessage (user)/(scientist)/etc.,
with the Scientist / Hypothesis Expansion agent output containing a JSON-like
"endpoint" field, and the Novelty Debate output containing a "recommendation"
field). If your harness uses a different format, tweak the regexes in
extract_fields() below -- the matching-by-prompt-text logic should still work
unchanged.
"""

import argparse
import csv
import glob
import os
import re
import sys
from difflib import SequenceMatcher

# Heuristic phrases suggesting SAGE explicitly flagged a premise as wrong /
# implausible / unsupported. This is a rough recall-oriented heuristic --
# always spot check flagged rows manually, and also skim a sample of
# non-flagged rows in case phrasing you use isn't covered here.
FLAG_PHRASES = [
    "contradict", "inconsistent", "implausible", "unsupported",
    "is incorrect", "inaccurate", "does not align", "conflicts with",
    "not consistent with known", "lacks support", "no evidence for",
    "cannot be reconciled", "biologically implausible", "not established in",
    "erroneous", "misstates", "overstate",
]


def extract_id_from_filename(path):
    """Look for a P#### or N#### token in the filename itself, e.g. hyp_N0109.md."""
    base = os.path.basename(path)
    m = re.search(r"(?<![A-Za-z0-9])([PN]\d{4})(?![0-9])", base)
    return m.group(1) if m else None


def read_questions(path):
    """id -> dict(prompt, domain, category, endpoint_type, attack_type, ...)"""
    q = {}
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            q[row["id"]] = row
    return q


def find_candidate_files(results_dir):
    exts = ("*.txt", "*.log", "*.json", "*.md")
    files = []
    for ext in exts:
        files.extend(glob.glob(os.path.join(results_dir, "**", ext), recursive=True))
    return sorted(set(files))


def load_text(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


def extract_user_prompt(text):
    """Best-effort extraction of the first user-turn prompt from a transcript."""
    m = re.search(r"TextMessage \(user\)[-\s]*\r?\n(.*?)\r?\n----------", text, re.S)
    if m:
        return m.group(1).strip()
    # fallback: first non-empty line
    for line in text.split("\n"):
        line = line.strip()
        if line:
            return line
    return ""


def best_match_id(prompt_text, questions, cache={}):
    """Match a transcript's extracted prompt to the closest question id."""
    if not prompt_text:
        return None, 0.0
    # exact/substring match first (fast path, should cover most cases)
    for qid, row in questions.items():
        qprompt = row["prompt"].strip()
        if qprompt and (qprompt in prompt_text or prompt_text in qprompt):
            return qid, 1.0
    # fallback: fuzzy match
    best_id, best_score = None, 0.0
    for qid, row in questions.items():
        score = SequenceMatcher(None, prompt_text[:300], row["prompt"][:300]).ratio()
        if score > best_score:
            best_id, best_score = qid, score
    return best_id, best_score


def extract_fields(text):
    out = {"declared_endpoint": "", "recommendation": "", "hypothesis_snippet": "", "flag_hit": "", "flag_phrase": ""}

    m = re.search(r'"endpoint"\s*:\s*"([^"]*)"', text)
    if m:
        out["declared_endpoint"] = m.group(1)
    else:
        m = re.search(r"Primary endpoint\s*\|\s*([^\n|]+)", text)
        if m:
            out["declared_endpoint"] = m.group(1).strip()

    m = re.search(r'"recommendation"\s*:\s*"([^"]*)"', text)
    if m:
        out["recommendation"] = m.group(1)
    else:
        m = re.search(r"\b(APPROVE[D]?|REJECT(?:ED)?|REVISE)\b", text)
        if m:
            out["recommendation"] = m.group(1)

    m = re.search(r'"expanded_hypothesis"\s*:\s*"(.*?)",\s*\n\s*"justification"', text, re.S)
    if not m:
        m = re.search(r'"hypothesis"\s*:\s*"(.*?)"\s*\n\}', text, re.S)
    if m:
        snippet = re.sub(r"\s+", " ", m.group(1)).strip()
        out["hypothesis_snippet"] = snippet[:500]

    low = text.lower()
    for phrase in FLAG_PHRASES:
        if phrase in low:
            out["flag_hit"] = "1"
            out["flag_phrase"] = phrase
            break

    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results_dir", default="experiment_results/full_run")
    ap.add_argument("--questions", default="SAGE_experiment_questions.csv")
    ap.add_argument("--out", default="parsed_results.csv")
    ap.add_argument("--min_match_score", type=float, default=0.5,
                     help="Below this fuzzy-match score, id is left blank for manual review.")
    args = ap.parse_args()

    if not os.path.isdir(args.results_dir):
        sys.exit(f"Results dir not found: {args.results_dir}")
    if not os.path.isfile(args.questions):
        sys.exit(f"Questions CSV not found: {args.questions}")

    questions = read_questions(args.questions)
    files = find_candidate_files(args.results_dir)
    if not files:
        sys.exit(f"No candidate transcript files found under {args.results_dir}")

    print(f"Found {len(files)} candidate files under {args.results_dir}")
    print(f"Loaded {len(questions)} questions from {args.questions}")

    # Group files by ID: prefer filename-derived ID; fall back to prompt-text
    # fuzzy matching only for files where no ID token appears in the filename.
    id_to_files = {}
    no_filename_id = []
    for path in files:
        fid = extract_id_from_filename(path)
        if fid:
            id_to_files.setdefault(fid, []).append(path)
        else:
            no_filename_id.append(path)

    unmatched_fuzzy = []
    for path in no_filename_id:
        text = load_text(path)
        if not text.strip():
            continue
        prompt = extract_user_prompt(text)
        qid, score = best_match_id(prompt, questions)
        if qid is None or score < args.min_match_score:
            unmatched_fuzzy.append(path)
        else:
            id_to_files.setdefault(qid, []).append(path)

    rows = []
    ids_with_no_question_row = []
    for qid, paths in sorted(id_to_files.items()):
        qrow = questions.get(qid)
        if qrow is None:
            ids_with_no_question_row.append(qid)
            continue
        # merge text across all files sharing this id, so fields split across
        # e.g. a hypothesis file + a debate/log file are both captured
        merged_text = "\n\n".join(load_text(p) for p in paths)
        fields = extract_fields(merged_text)
        rows.append({
            "id": qid,
            "matched_file": ";".join(paths),
            "match_score": 1.0,
            "domain": qrow.get("domain", ""),
            "category": qrow.get("category", ""),
            "endpoint_type_requested": qrow.get("endpoint_type", ""),
            "attack_type": qrow.get("attack_type", ""),
            **fields,
        })

    fieldnames = ["id", "matched_file", "match_score", "domain", "category",
                  "endpoint_type_requested", "attack_type", "declared_endpoint",
                  "recommendation", "flag_hit", "flag_phrase", "hypothesis_snippet"]
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    print(f"\nWrote {len(rows)} rows to {args.out}  ({len(id_to_files)} unique ids found, {len(questions)} expected)")

    missing_ids = sorted(set(questions.keys()) - set(id_to_files.keys()))
    if missing_ids:
        print(f"\nWARNING: {len(missing_ids)} question ids had no matching output file at all:")
        for mid in missing_ids[:20]:
            print(f"  {mid}")
        if len(missing_ids) > 20:
            print(f"  ... and {len(missing_ids) - 20} more")

    if ids_with_no_question_row:
        print(f"\nNOTE: {len(ids_with_no_question_row)} filename-derived ids didn't match any row in "
              f"{args.questions} (extra/unexpected files) -- ignored: {ids_with_no_question_row[:10]}")

    if unmatched_fuzzy:
        print(f"\nWARNING: {len(unmatched_fuzzy)} files had no ID in their filename and couldn't be "
              f"confidently fuzzy-matched by prompt text either -- these were dropped entirely:")
        for p in unmatched_fuzzy[:20]:
            print(f"  {p}")
        if len(unmatched_fuzzy) > 20:
            print(f"  ... and {len(unmatched_fuzzy) - 20} more")


if __name__ == "__main__":
    main()