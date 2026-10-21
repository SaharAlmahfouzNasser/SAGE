#!/usr/bin/env python3
"""
Extract novelty_critic_agent scores from already-completed run_experiments.py
output — no re-running required. Scans hyp_<id>_console.txt files (the full
transcript run_experiments.py already saves for every question) and pulls
out the critic's structured JSON assessment.

The novelty score only exists in the .txt/console transcript, not in the
polished .md/.pdf report — the critic's TextMessage (technical/application/
theoretical scores, overall_novelty_score, recommendation) gets summarized
away in the final report.

Usage:
  python extract_novelty_scores.py --results-dir experiment_results/full_run
  python extract_novelty_scores.py --results-dir experiment_results/full_run --out novelty_scores.csv

Output: one row per question with overall/technical/application/theoretical
novelty scores and the critic's recommendation (APPROVE/REVISE/REJECT etc.),
suitable for joining against your existing metrics.csv on `id`.
"""

import argparse
import csv
import json
import re
from pathlib import Path


def extract_first_json_object(text: str) -> str | None:
    """Find the first balanced top-level JSON object in text via brace-depth
    counting (string-aware). A greedy \\{.*\\} regex fails when the critic's
    message is the last section in a file — it swallows trailing content
    (e.g. the SAGE_METRICS line) past the JSON's real closing brace."""
    start = text.find('{')
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if in_string:
            if escape:
                escape = False
            elif c == '\\':
                escape = True
            elif c == '"':
                in_string = False
        else:
            if c == '"':
                in_string = True
            elif c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return None


def strip_js_style_comments(json_str: str) -> str:
    """Remove // line comments that appear OUTSIDE quoted strings (kept in
    sync with run_experiments.py's copy of this function). String-aware, so
    this won't touch '//' that's legitimately part of a URL."""
    out = []
    in_string = False
    escape = False
    i = 0
    n = len(json_str)
    while i < n:
        c = json_str[i]
        if in_string:
            out.append(c)
            if escape:
                escape = False
            elif c == '\\':
                escape = True
            elif c == '"':
                in_string = False
            i += 1
            continue
        if c == '"':
            in_string = True
            out.append(c)
            i += 1
            continue
        if c == '/' and i + 1 < n and json_str[i + 1] == '/':
            while i < n and json_str[i] not in '\r\n':
                i += 1
            continue
        out.append(c)
        i += 1
    return ''.join(out)


def parse_json_leniently(json_str: str) -> dict | None:
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        try:
            return json.loads(strip_js_style_comments(json_str))
        except json.JSONDecodeError:
            return None


def extract_novelty_assessment(console_text: str) -> dict | None:
    """Same logic as run_experiments.py's extractor — kept in sync manually
    since this is meant to be run standalone against old output, independent
    of which version of run_experiments.py originally produced it."""
    headers = re.findall(r'-{5,}\s*(\w+)\s*\(([^)]+)\)\s*-{5,}', console_text)
    sections = re.split(r'-{5,}\s*\w+\s*\([^)]+\)\s*-{5,}', console_text)[1:]

    result = None
    for (msg_type, agent), section in zip(headers, sections):
        if agent.strip() == "novelty_critic_agent" and msg_type == "TextMessage":
            json_str = extract_first_json_object(section)
            if json_str:
                parsed = parse_json_leniently(json_str)
                if parsed is not None:
                    result = parsed
    return result


def flatten(qid: str, assessment: dict | None) -> dict:
    if not assessment:
        return {
            "id": qid, "found": False, "overall_novelty_score": "",
            "technical_novelty_score": "", "application_novelty_score": "",
            "theoretical_novelty_score": "", "recommendation": "",
            "key_innovations_count": "", "references_count": "",
        }
    breakdown = assessment.get("novelty_breakdown", {})
    overall = assessment.get("overall_assessment", {})
    return {
        "id": qid, "found": True,
        "overall_novelty_score": overall.get("overall_novelty_score", ""),
        "technical_novelty_score": breakdown.get("technical_novelty", {}).get("score", ""),
        "application_novelty_score": breakdown.get("application_novelty", {}).get("score", ""),
        "theoretical_novelty_score": breakdown.get("theoretical_novelty", {}).get("score", ""),
        "recommendation": assessment.get("recommendation", ""),
        "key_innovations_count": len(overall.get("key_innovations", [])),
        "references_count": len(assessment.get("references", [])),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", required=True,
                     help="a run_experiments.py output dir, e.g. experiment_results/full_run "
                          "(looks in <results-dir>/hypotheses/*_console.txt)")
    ap.add_argument("--out", default=None,
                     help="default: <results-dir>/novelty_scores.csv")
    args = ap.parse_args()

    results_dir = Path(args.results_dir)
    hyp_dir = results_dir / "hypotheses"
    if not hyp_dir.exists():
        raise SystemExit(f"No hypotheses/ directory found under {results_dir}")

    out_path = Path(args.out) if args.out else results_dir / "novelty_scores.csv"

    console_files = sorted(hyp_dir.glob("hyp_*_console.txt"))
    if not console_files:
        raise SystemExit(f"No hyp_*_console.txt files found in {hyp_dir}")

    rows = []
    missing = []
    for f in console_files:
        qid = f.stem.replace("hyp_", "").replace("_console", "")
        text = f.read_text(encoding="utf-8", errors="ignore")
        assessment = extract_novelty_assessment(text)
        row = flatten(qid, assessment)
        rows.append(row)
        if not row["found"]:
            missing.append(qid)

    fieldnames = ["id", "found", "overall_novelty_score", "technical_novelty_score",
                  "application_novelty_score", "theoretical_novelty_score",
                  "recommendation", "key_innovations_count", "references_count"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    found_count = sum(1 for r in rows if r["found"])
    print(f"Extracted novelty scores for {found_count}/{len(rows)} questions -> {out_path}")
    if missing:
        print(f"No novelty assessment found for {len(missing)} question(s) "
              f"(likely failed/timed-out runs): {', '.join(missing[:10])}"
              + (" ..." if len(missing) > 10 else ""))

    scored = [r["overall_novelty_score"] for r in rows if r["found"] and r["overall_novelty_score"] != ""]
    if scored:
        avg = sum(scored) / len(scored)
        print(f"Average overall_novelty_score: {avg:.2f} (n={len(scored)}, range {min(scored)}-{max(scored)})")


if __name__ == "__main__":
    main()