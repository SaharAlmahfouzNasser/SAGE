"""
extract_full_negative.py

Pulls the FULL (untruncated) generated hypothesis text for every negative
(wrong-premise) row, for proper classification (reproduced / silently
corrected / drifted / flagged) rather than relying on 500-char snippets.

Reuses the same filename-ID matching + multi-file merge logic as
parse_results.py, but only processes category=='negative' rows and keeps
full text instead of truncating.

Outputs TWO files:
  1. full_negative_hypotheses.csv  -- structured, one row per id, full text
     in a single column (easy to reload programmatically)
  2. full_negative_hypotheses_digest.md -- human-readable, grouped by
     attack_type then domain, with prompt/notes/declared fields above each
     full hypothesis (easy to skim or send back for review)

USAGE:
    python extract_full_negative.py \
        --results_dir experiment_results/full_run \
        --questions SAGE_experiment_questions.csv \
        --out_csv full_negative_hypotheses.csv \
        --out_digest full_negative_hypotheses_digest.md
"""

import argparse
import csv
import glob
import os
import re


def extract_id_from_filename(path):
    base = os.path.basename(path)
    m = re.search(r"(?<![A-Za-z0-9])([PN]\d{4})(?![0-9])", base)
    return m.group(1) if m else None


def read_questions(path):
    q = {}
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            q[row["id"]] = row
    return q


def load_text(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


def find_candidate_files(results_dir):
    exts = ("*.txt", "*.log", "*.json", "*.md")
    files = []
    for ext in exts:
        files.extend(glob.glob(os.path.join(results_dir, "**", ext), recursive=True))
    return sorted(set(files))


def extract_full_hypothesis(text):
    """Best-effort full-text extraction, trying JSON-style field first
    (raw transcript / .txt / _console.txt), then markdown section (.md)."""
    # JSON-style: "expanded_hypothesis": "...", "justification"
    m = re.search(r'"expanded_hypothesis"\s*:\s*"(.*?)",\s*\n\s*"justification"', text, re.S)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()

    m = re.search(r'"hypothesis"\s*:\s*"(.*?)"\s*\n\}', text, re.S)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()

    # markdown: "## Working Hypothesis" section up to next "##" header
    m = re.search(r"## Working Hypothesis\s*\n(.*?)(?=\n#{1,2} |\Z)", text, re.S)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()

    return ""


def extract_clinical_question(text):
    m = re.search(r"## Clinical Question\s*\n(.*?)(?=\n#{1,2} |\Z)", text, re.S)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    return ""


def extract_field(text, field_name):
    m = re.search(rf'"{field_name}"\s*:\s*"([^"]*)"', text)
    if m:
        return m.group(1)
    if field_name == "endpoint":
        m = re.search(r"Primary endpoint\s*\|\s*([^\n|]+)", text)
        if m:
            return m.group(1).strip()
    return ""


def extract_anchor_genes(text):
    m = re.search(r"Anchor gene\(s\)\s*\|\s*([^\n|]+)", text)
    return m.group(1).strip() if m else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results_dir", default="experiment_results/full_run")
    ap.add_argument("--questions", default="SAGE_experiment_questions.csv")
    ap.add_argument("--out_csv", default="full_negative_hypotheses.csv")
    ap.add_argument("--out_digest", default="full_negative_hypotheses_digest.md")
    args = ap.parse_args()

    questions = read_questions(args.questions)
    neg_ids = {qid: row for qid, row in questions.items() if row.get("category") == "negative"}
    print(f"Loaded {len(questions)} total questions, {len(neg_ids)} negative rows to process.")

    files = find_candidate_files(args.results_dir)
    print(f"Found {len(files)} candidate files under {args.results_dir}")

    id_to_files = {}
    for path in files:
        fid = extract_id_from_filename(path)
        if fid and fid in neg_ids:
            id_to_files.setdefault(fid, []).append(path)

    rows = []
    missing = []
    for qid, qrow in sorted(neg_ids.items()):
        paths = id_to_files.get(qid)
        if not paths:
            missing.append(qid)
            continue
        merged_text = "\n\n".join(load_text(p) for p in paths)
        full_hyp = extract_full_hypothesis(merged_text)
        clinical_q = extract_clinical_question(merged_text)
        declared_endpoint = extract_field(merged_text, "endpoint")
        recommendation = extract_field(merged_text, "recommendation")
        anchor_genes = extract_anchor_genes(merged_text)

        rows.append({
            "id": qid,
            "domain": qrow.get("domain", ""),
            "attack_type": qrow.get("attack_type", ""),
            "prompt": qrow.get("prompt", ""),
            "correct_direction_notes": qrow.get("notes", ""),
            "declared_endpoint": declared_endpoint,
            "recommendation": recommendation,
            "anchor_genes": anchor_genes,
            "clinical_question": clinical_q,
            "full_hypothesis": full_hyp,
            "matched_files": ";".join(paths),
        })

    fieldnames = ["id", "domain", "attack_type", "prompt", "correct_direction_notes",
                  "declared_endpoint", "recommendation", "anchor_genes",
                  "clinical_question", "full_hypothesis", "matched_files"]
    with open(args.out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows to {args.out_csv}")

    if missing:
        print(f"WARNING: {len(missing)} negative ids had no matching output file: {missing}")

    # human-readable digest, grouped by attack_type then domain
    rows_sorted = sorted(rows, key=lambda r: (r["attack_type"], r["domain"], r["id"]))
    with open(args.out_digest, "w", encoding="utf-8") as f:
        f.write("# Full-text digest: negative (wrong-premise) hypothesis runs\n\n")
        f.write(f"Total rows: {len(rows)}\n\n")
        current_type = None
        for r in rows_sorted:
            if r["attack_type"] != current_type:
                current_type = r["attack_type"]
                f.write(f"\n---\n## Attack type: `{current_type}`\n---\n")
            f.write(f"\n### {r['id']} ({r['domain']})\n\n")
            f.write(f"**Prompt (wrong premise given to SAGE):**\n> {r['prompt']}\n\n")
            f.write(f"**Correct direction (for grading only):**\n> {r['correct_direction_notes']}\n\n")
            f.write(f"**declared_endpoint:** {r['declared_endpoint']}  |  **recommendation:** {r['recommendation']}  |  **anchor_genes:** {r['anchor_genes']}\n\n")
            if r["clinical_question"]:
                f.write(f"**Clinical question generated:**\n{r['clinical_question']}\n\n")
            f.write(f"**Full generated hypothesis:**\n{r['full_hypothesis']}\n\n")
    print(f"Wrote human-readable digest to {args.out_digest}")


if __name__ == "__main__":
    main()