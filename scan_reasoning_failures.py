"""
scan_reasoning_failures.py

Scans POSITIVE-condition transcripts only (well-posed prompts, not the
deliberately-flawed negative set) for three failure signals:

  1. Weak KG path retrieval -- explicit self-reported signals that Path
     Generation returned very few paths (e.g., "the single provided path",
     "it is the only path", "no additional paths were available").
     This is about the knowledge graph itself, NOT external API/literature
     search issues.

  2. Conflicting evidence -- language indicating the agent encountered
     disagreement across sources/papers/paths (e.g., "conflicting",
     "however, other studies", "mixed evidence", "inconsistent findings").

  3. Overconfident reasoning (proxy) -- a high novelty/confidence score
     (>=7) co-occurring with a weak-retrieval signal ((1) above) or with
     strongly unhedged causal language ("will independently predict",
     "directly causes", "confirms") and no visible hedging ("may",
     "could", "suggests", "possible").

All three are heuristic pattern matches, not ground truth -- manually spot
check a sample of hits (and a sample of non-hits) before reporting a rate,
the same way we caught false positives earlier in this process.

USAGE:
    python scan_reasoning_failures.py \
        --results_dir experiment_results/full_run \
        --questions SAGE_experiment_questions.csv \
        --out reasoning_failure_scan.csv
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


WEAK_PATH_PATTERNS = [
    r"the single (provided )?path",
    r"it is the only path",
    r"the only (provided )?path",
    r"no additional paths",
    r"limited (graph )?paths? (were |was )?available",
    r"only one path",
    r"single path (was )?(provided|available|used)",
]

CONFLICT_STRONG_PATTERNS = [
    # explicit literature/evidence-level disagreement -- the actual failure
    # mode a reviewer means by "conflicting evidence"
    r"conflicting evidence", r"conflicting (results|findings|reports|studies)",
    r"however,? other studies", r"mixed evidence", r"inconsistent findings",
    r"contradictory (evidence|results|findings)", r"discordant (results|findings)",
    r"studies disagree", r"some studies .{0,20} while others",
    r"divergent (result|finding|evidence)",
]

CONFLICT_WEAK_PATTERNS = [
    # mechanistic tension/nuance within the model's own reasoning -- NOT
    # necessarily a failure, may reflect appropriately nuanced reasoning.
    # Kept separate so it is never conflated with genuine literature conflict.
    r"remains? (debated|controversial|disputed)", r"\bconflict(s|ing)?\b",
    r"disagreement", r"\bat odds\b", r"opposing (view|evidence|result)",
    r"tension between", r"not universally", r"controvers", r"unclear whether",
    r"remains debated",
]

HEDGE_PATTERNS = [
    r"\bmay\b", r"\bcould\b", r"\bsuggests?\b", r"\bpossibl[ey]\b",
    r"\bmight\b", r"\bpotentially\b", r"\bappears? to\b", r"\bhypothesiz",
]

UNHEDGED_PATTERNS = [
    r"will independently predict", r"directly causes?", r"\bconfirms?\b",
    r"definitively", r"unambiguously", r"is (the )?primary driver",
]


def count_hits(text_low, patterns):
    return sum(1 for p in patterns if re.search(p, text_low))


def scan_text(text):
    low = text.lower()

    weak_path_hits = count_hits(low, WEAK_PATH_PATTERNS)
    conflict_strong_hits = count_hits(low, CONFLICT_STRONG_PATTERNS)
    conflict_weak_hits = count_hits(low, CONFLICT_WEAK_PATTERNS)
    hedge_hits = count_hits(low, HEDGE_PATTERNS)
    unhedged_hits = count_hits(low, UNHEDGED_PATTERNS)

    score = None
    m = re.search(r'"(?:novelty_)?score"\s*:\s*([\d.]+)', text)
    if m:
        try:
            score = float(m.group(1))
        except ValueError:
            pass

    recommendation = None
    m = re.search(r'"recommendation"\s*:\s*"([^"]*)"', text)
    if m:
        recommendation = m.group(1)

    high_score_no_hedge = (score is not None and score >= 7 and hedge_hits == 0 and unhedged_hits > 0)
    overconfident_proxy = (score is not None and score >= 7 and weak_path_hits > 0) or high_score_no_hedge

    return {
        "weak_kg_path_retrieval": weak_path_hits > 0,
        "weak_path_hit_count": weak_path_hits,
        "conflicting_evidence_strong": conflict_strong_hits > 0,
        "conflict_strong_hit_count": conflict_strong_hits,
        "conflicting_evidence_weak_nuance": conflict_weak_hits > 0,
        "conflict_weak_hit_count": conflict_weak_hits,
        "score": score,
        "recommendation": recommendation,
        "hedge_language_count": hedge_hits,
        "unhedged_language_count": unhedged_hits,
        "overconfident_reasoning_proxy": overconfident_proxy,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results_dir", default="experiment_results/full_run")
    ap.add_argument("--questions", default="SAGE_experiment_questions.csv")
    ap.add_argument("--out", default="reasoning_failure_scan.csv")
    args = ap.parse_args()

    questions = read_questions(args.questions)
    # restrict to positive-condition ids only
    positive_ids = {qid for qid, row in questions.items() if row.get("category") == "positive"}
    print(f"Loaded {len(questions)} questions, {len(positive_ids)} are positive-condition.")

    files = find_candidate_files(args.results_dir)
    print(f"Found {len(files)} candidate files under {args.results_dir}")

    id_to_files = {}
    for path in files:
        fid = extract_id_from_filename(path)
        if fid and fid in positive_ids:
            id_to_files.setdefault(fid, []).append(path)

    rows = []
    for qid, paths in sorted(id_to_files.items()):
        qrow = questions.get(qid, {})
        merged_text = "\n\n".join(load_text(p) for p in paths)
        result = scan_text(merged_text)
        rows.append({
            "id": qid,
            "domain": qrow.get("domain", ""),
            "endpoint_type": qrow.get("endpoint_type", ""),
            "modality": qrow.get("modality", ""),
            **result,
        })

    fieldnames = ["id", "domain", "endpoint_type", "modality",
                  "weak_kg_path_retrieval", "weak_path_hit_count",
                  "conflicting_evidence_strong", "conflict_strong_hit_count",
                  "conflicting_evidence_weak_nuance", "conflict_weak_hit_count",
                  "overconfident_reasoning_proxy", "score", "recommendation",
                  "hedge_language_count", "unhedged_language_count"]
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    n = len(rows)
    print(f"\nWrote {n} rows to {args.out}")
    print(f"\n{'='*60}")
    print(f"Weak KG path retrieval:      {sum(r['weak_kg_path_retrieval'] for r in rows)}/{n}")
    print(f"Conflicting evidence (strong, literature-level): {sum(r['conflicting_evidence_strong'] for r in rows)}/{n}")
    print(f"Conflicting evidence (weak, mechanistic nuance):  {sum(r['conflicting_evidence_weak_nuance'] for r in rows)}/{n}")
    print(f"Overconfident reasoning (proxy): {sum(r['overconfident_reasoning_proxy'] for r in rows)}/{n}")
    print(f"{'='*60}")

    print("\nBy domain (weak KG path retrieval):")
    by_domain = {}
    for r in rows:
        by_domain.setdefault(r["domain"], [0, 0])
        by_domain[r["domain"]][1] += 1
        if r["weak_kg_path_retrieval"]:
            by_domain[r["domain"]][0] += 1
    for d, (flagged, total) in sorted(by_domain.items()):
        print(f"  {d}: {flagged}/{total}")


if __name__ == "__main__":
    main()