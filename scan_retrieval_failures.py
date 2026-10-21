"""
scan_retrieval_failures.py

Scans every hypothesis transcript for signals of a weak/degraded literature
retrieval step -- specifically:
  (a) explicit API errors during literature search (429 / rate limit / timeout)
  (b) an explicit "total_relevant_papers_found" (or similar) count of 0 or
      suspiciously low
  (c) whether the run STILL produced a normal-looking recommendation/score
      despite (a) or (b) -- this is the "silent degradation" pattern we
      found in the RASSF1A case.

This does NOT rely on metrics.csv (which only logs top-level run success/
failure) -- it greps the actual transcript content, since retrieval
degradation can occur inside an otherwise "ok" run.

USAGE:
    python scan_retrieval_failures.py \
        --results_dir experiment_results/full_run \
        --questions SAGE_experiment_questions.csv \
        --out retrieval_failure_scan.csv
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


# Signals of an API-level retrieval problem during the literature/novelty
# search step. Case-insensitive substring match.
API_ERROR_PATTERNS = [
    r"\b429\b", r"too many requests",
    r"connection (timed out|timeout|error)", r"read timeout",
    r"failed to retrieve", r"max retries exceeded",
    r"service unavailable", r"\b503\b", r"\b502\b",
]
# NOTE: deliberately excludes generic "rate limit" / "retry" phrasing -- these
# false-positive on unrelated boilerplate (e.g., HuggingFace Hub's routine
# "unauthenticated requests... enable higher rate limits" warning at model
# load time, which appears in nearly every run regardless of outcome).

# Explicit low/zero paper-count fields the pipeline itself reports.
PAPER_COUNT_PATTERN = re.compile(
    r'"?(total_relevant_papers_found|papers_found|num_papers|relevant_papers)"?\s*[:=]\s*(\d+)',
    re.IGNORECASE,
)


def scan_text(text):
    low = text.lower()
    api_hits = [p for p in API_ERROR_PATTERNS if re.search(p, low)]
    paper_counts = [int(m.group(2)) for m in PAPER_COUNT_PATTERN.finditer(text)]
    min_paper_count = min(paper_counts) if paper_counts else None

    recommendation = None
    m = re.search(r'"recommendation"\s*:\s*"([^"]*)"', text)
    if m:
        recommendation = m.group(1)

    novelty_score = None
    m = re.search(r'"(?:novelty_)?score"\s*:\s*([\d.]+)', text)
    if m:
        novelty_score = m.group(1)

    return {
        "api_error_hit": bool(api_hits),
        "api_error_terms": ";".join(api_hits),
        "min_paper_count_reported": min_paper_count,
        "zero_papers_reported": (min_paper_count == 0) if min_paper_count is not None else False,
        "recommendation_despite_issue": recommendation,
        "score_despite_issue": novelty_score,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results_dir", default="experiment_results/full_run")
    ap.add_argument("--questions", default="SAGE_experiment_questions.csv")
    ap.add_argument("--out", default="retrieval_failure_scan.csv")
    args = ap.parse_args()

    questions = read_questions(args.questions)
    files = find_candidate_files(args.results_dir)
    print(f"Found {len(files)} candidate files under {args.results_dir}")
    print(f"Loaded {len(questions)} questions from {args.questions}")

    id_to_files = {}
    for path in files:
        fid = extract_id_from_filename(path)
        if fid:
            id_to_files.setdefault(fid, []).append(path)

    rows = []
    for qid, paths in sorted(id_to_files.items()):
        qrow = questions.get(qid, {})
        merged_text = "\n\n".join(load_text(p) for p in paths)
        result = scan_text(merged_text)
        flagged = result["api_error_hit"] or result["zero_papers_reported"]
        rows.append({
            "id": qid,
            "domain": qrow.get("domain", ""),
            "category": qrow.get("category", ""),
            "flagged_weak_retrieval": flagged,
            **result,
        })

    fieldnames = ["id", "domain", "category", "flagged_weak_retrieval",
                  "api_error_hit", "api_error_terms", "min_paper_count_reported",
                  "zero_papers_reported", "recommendation_despite_issue", "score_despite_issue"]
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    n_flagged = sum(r["flagged_weak_retrieval"] for r in rows)
    print(f"\nWrote {len(rows)} rows to {args.out}")
    print(f"\n{'='*60}")
    print(f"SUMMARY: {n_flagged}/{len(rows)} runs show a weak-retrieval signal "
          f"({100*n_flagged/len(rows):.1f}%)")
    print(f"{'='*60}")

    by_domain = {}
    for r in rows:
        by_domain.setdefault(r["domain"], [0, 0])
        by_domain[r["domain"]][1] += 1
        if r["flagged_weak_retrieval"]:
            by_domain[r["domain"]][0] += 1
    print("\nBy domain:")
    for d, (flagged, total) in sorted(by_domain.items()):
        print(f"  {d}: {flagged}/{total} ({100*flagged/total:.1f}%)")

    print("\nFlagged ids:")
    for r in rows:
        if r["flagged_weak_retrieval"]:
            print(f"  {r['id']} ({r['domain']}, {r['category']}): "
                  f"api_error_terms='{r['api_error_terms']}' "
                  f"min_papers={r['min_paper_count_reported']} "
                  f"recommendation={r['recommendation_despite_issue']}")


if __name__ == "__main__":
    main()