#!/usr/bin/env python3
"""
Batch-run SAGE hypothesis generation for every question in a CSV, selecting
the correct built-in graph per row's `domain` column — no UI required.

Expected CSV columns (matches SAGE_experiment_questions.csv):
  id, domain, category, endpoint_type, attack_type, subtype, modality,
  therapy_context, rep, prompt, notes

`domain` must be one of: bladder, breast, prostate (case-insensitive) — these
map to your three built-in graphs. Graph resolution is imported directly from
UI/server.py (BUILTIN_GRAPHS / _find_builtin_graph), so this script picks the
exact same file the UI would, including any custom "Save as built-in" graphs.

Usage:
  python run_experiments.py --csv SAGE_experiment_questions.csv
  python run_experiments.py --csv SAGE_experiment_questions.csv --limit 5        # smoke test first
  python run_experiments.py --csv SAGE_experiment_questions.csv --domain bladder # one domain only
  python run_experiments.py --csv SAGE_experiment_questions.csv --resume         # continue an interrupted run
  python run_experiments.py --csv SAGE_experiment_questions.csv --out-dir experiment_results/run1 --delay 2

Outputs, under --out-dir (default: experiment_results/<timestamp>/):
  hypotheses/hyp_<id>.md            - the generated hypothesis (from run_pipeline.py)
  hypotheses/hyp_<id>_console.txt   - full stdout+stderr transcript for that run
  metrics.csv                       - one row per question: timing, tokens, graph, status
  errors.log                        - one line per failure/skip, append-only
  summary.json                      - aggregate counts, written at the end of the run
"""

import argparse
import csv
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "UI"))

# Reuses the exact built-in graph resolution logic from server.py (including
# any custom built-ins saved via "Save as built-in" in the UI), so this
# script and the UI never disagree about which graph a domain maps to.
# Importing server.py does NOT start the server — uvicorn.run() only fires
# under `if __name__ == "__main__":`, which doesn't trigger on import.
from server import _find_builtin_graph  # noqa: E402

DOMAIN_TO_BUILTIN_KEY = {
    "bladder": "builtin-bladder-cancer",
    "breast": "builtin-breast-cancer",
    "prostate": "builtin-prostate-cancer",
}


def resolve_graph_for_domain(domain: str) -> Path | None:
    key = DOMAIN_TO_BUILTIN_KEY.get(domain.strip().lower())
    if not key:
        return None
    _, path = _find_builtin_graph(key)
    return path


def extract_first_json_object(text: str) -> str | None:
    """Find the first balanced top-level JSON object in text via brace-depth
    counting (string-aware, so braces inside quoted values don't confuse it).
    A greedy regex like \\{.*\\} fails here when the critic's message is the
    last section in a file — it swallows trailing content (e.g. the
    SAGE_METRICS line) past the JSON's real closing brace."""
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
    """Remove // line comments that appear OUTSIDE quoted strings. Some LLM
    outputs include these even though they're invalid JSON (e.g. an
    'APPROVE' block with a trailing '// note: ...' after a URL). String-
    aware, so this won't touch '//' that's legitimately part of a quoted
    value like a URL (e.g. "https://doi.org/...")."""
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
    """Try strict json.loads first; if that fails (e.g. due to a stray
    // comment the LLM included), retry after stripping such comments."""
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        try:
            return json.loads(strip_js_style_comments(json_str))
        except json.JSONDecodeError:
            return None


def extract_novelty_assessment(console_text: str) -> dict | None:
    """Pull the novelty_critic_agent's structured JSON block out of a
    run_pipeline.py console transcript. Only present in the .txt/console
    output — the polished .md/.pdf report does not include it.

    Splits on the '---------- MsgType (agent) ----------' section markers
    used by the agent framework, finds TextMessage sections from
    novelty_critic_agent specifically (not its ToolCallRequestEvent /
    ToolCallExecutionEvent sections, which are tool-call noise, not the
    final assessment), and parses the JSON object embedded in it. If the
    critic revises across multiple TextMessages, the LAST one is used.
    """
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


def flatten_novelty_fields(assessment: dict | None) -> dict:
    """Extract just the CSV-friendly summary fields from a parsed novelty
    assessment JSON, defaulting to blanks if not found."""
    if not assessment:
        return {
            "overall_novelty_score": "", "technical_novelty_score": "",
            "application_novelty_score": "", "theoretical_novelty_score": "",
            "novelty_recommendation": "",
        }
    breakdown = assessment.get("novelty_breakdown", {})
    overall = assessment.get("overall_assessment", {})
    return {
        "overall_novelty_score": overall.get("overall_novelty_score", ""),
        "technical_novelty_score": breakdown.get("technical_novelty", {}).get("score", ""),
        "application_novelty_score": breakdown.get("application_novelty", {}).get("score", ""),
        "theoretical_novelty_score": breakdown.get("theoretical_novelty", {}).get("score", ""),
        "novelty_recommendation": assessment.get("recommendation", ""),
    }


def run_one(row: dict, graph_path: Path, out_dir: Path, timeout: int,
           path_eval_weights: dict | None = None) -> dict:
    qid = row["id"]
    domain = row["domain"]
    prompt = row["prompt"]
    run_name = f"hyp_{qid}"

    cmd = [
        sys.executable, str(ROOT / "run_pipeline.py"),
        "--graph", str(graph_path),
        "--domain", f"{domain} cancer",
        "--query", prompt,
        "--out-dir", str(out_dir / "hypotheses"),
        "--run-name", run_name,
        "--no-timestamp",
    ]
    if path_eval_weights:
        if "logic" in path_eval_weights:
            cmd += ["--logic-weight", str(path_eval_weights["logic"])]
        if "relevance" in path_eval_weights:
            cmd += ["--relevance-weight", str(path_eval_weights["relevance"])]
        if "novelty" in path_eval_weights:
            cmd += ["--novelty-weight", str(path_eval_weights["novelty"])]
        if "surprise" in path_eval_weights:
            cmd += ["--surprise-weight", str(path_eval_weights["surprise"])]

    t0 = time.time()
    result = {
        "id": qid, "domain": domain, "category": row.get("category", ""),
        "endpoint_type": row.get("endpoint_type", ""), "modality": row.get("modality", ""),
        "graph": graph_path.name, "status": "ok", "duration_seconds": None,
        "llm_calls": None, "total_tokens": None, "prompt_tokens": None,
        "completion_tokens": None,
        "overall_novelty_score": "", "technical_novelty_score": "",
        "application_novelty_score": "", "theoretical_novelty_score": "",
        "novelty_recommendation": "",
        "error": "",
    }
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout)
        result["duration_seconds"] = round(time.time() - t0, 1)

        # Full console transcript, saved regardless of outcome — useful for debugging.
        console_text = proc.stdout + "\n--- STDERR ---\n" + proc.stderr
        (out_dir / "hypotheses" / f"{run_name}_console.txt").write_text(console_text, encoding="utf-8")

        novelty_assessment = extract_novelty_assessment(proc.stdout)
        result.update(flatten_novelty_fields(novelty_assessment))

        metrics_line = next(
            (l for l in proc.stdout.splitlines() if l.startswith("SAGE_METRICS:")), None
        )
        if metrics_line:
            try:
                m = json.loads(metrics_line[len("SAGE_METRICS:"):].strip())
                result.update({
                    "llm_calls": m.get("llm_calls"),
                    "total_tokens": m.get("total_tokens"),
                    "prompt_tokens": m.get("prompt_tokens"),
                    "completion_tokens": m.get("completion_tokens"),
                })
            except Exception:
                pass

        if proc.returncode != 0:
            result["status"] = "failed"
            result["error"] = proc.stderr.strip()[-2000:]
    except subprocess.TimeoutExpired:
        result["status"] = "timeout"
        result["duration_seconds"] = timeout
        result["error"] = f"Exceeded {timeout}s timeout"
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)

    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out-dir", default=None,
                     help="default: experiment_results/<timestamp>/")
    ap.add_argument("--domain", default=None, help="only run rows matching this domain")
    ap.add_argument("--graph-override", default=None,
                     help="use this exact .graphml path for every row, bypassing domain-based "
                          "built-in resolution entirely. Useful for reruns/corrections where the "
                          "auto-picked built-in wasn't the graph you actually meant.")
    ap.add_argument("--limit", type=int, default=0, help="only run the first N rows (0 = all)")
    ap.add_argument("--resume", action="store_true",
                     help="skip ids that already have a completed hypothesis .md in --out-dir")
    ap.add_argument("--timeout", type=int, default=1800,
                     help="per-question timeout in seconds (default 30 min)")
    ap.add_argument("--delay", type=float, default=0,
                     help="seconds to sleep between questions (basic rate limiting)")
    ap.add_argument("--logic-weight", type=float, default=None,
                     help="override the logic weight used for path selection in every question's "
                          "hypothesis generation (default: SciAgentsGraphFlow's built-in 0.2)")
    ap.add_argument("--relevance-weight", type=float, default=None, help="default 0.2")
    ap.add_argument("--novelty-weight", type=float, default=None, help="default 0.3")
    ap.add_argument("--surprise-weight", type=float, default=None, help="default 0.3")
    args = ap.parse_args()

    path_eval_weights = None
    _defaults = {"logic": 0.2, "relevance": 0.2, "novelty": 0.3, "surprise": 0.3}
    _overrides = {}
    if args.logic_weight is not None:
        _overrides["logic"] = args.logic_weight
    if args.relevance_weight is not None:
        _overrides["relevance"] = args.relevance_weight
    if args.novelty_weight is not None:
        _overrides["novelty"] = args.novelty_weight
    if args.surprise_weight is not None:
        _overrides["surprise"] = args.surprise_weight
    if _overrides:
        path_eval_weights = {**_defaults, **_overrides}
        print(f"Using custom path_eval_weights for every question: {path_eval_weights}")

    out_dir = Path(args.out_dir) if args.out_dir else ROOT / "experiment_results" / datetime.now().strftime("%Y%m%d_%H%M%S")
    (out_dir / "hypotheses").mkdir(parents=True, exist_ok=True)
    errors_path = out_dir / "errors.log"
    metrics_path = out_dir / "metrics.csv"

    with open(args.csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if args.domain:
        rows = [r for r in rows if r["domain"].strip().lower() == args.domain.strip().lower()]
    if args.limit:
        rows = rows[: args.limit]

    graph_override = None
    if args.graph_override:
        graph_override = Path(args.graph_override)
        if not graph_override.exists():
            raise SystemExit(f"--graph-override path does not exist: {graph_override}")
        print(f"Using explicit graph override for all rows: {graph_override}")

    if args.resume:
        done_ids = {p.stem.replace("hyp_", "") for p in (out_dir / "hypotheses").glob("hyp_*.md")}
        before = len(rows)
        rows = [r for r in rows if r["id"] not in done_ids]
        print(f"Resume: skipping {before - len(rows)} already-completed question(s)")

    print(f"Running {len(rows)} question(s) -> {out_dir}")

    fieldnames = ["id", "domain", "category", "endpoint_type", "modality", "graph",
                  "status", "duration_seconds", "llm_calls", "total_tokens",
                  "prompt_tokens", "completion_tokens",
                  "overall_novelty_score", "technical_novelty_score",
                  "application_novelty_score", "theoretical_novelty_score",
                  "novelty_recommendation", "error"]
    write_header = not metrics_path.exists()
    metrics_f = open(metrics_path, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(metrics_f, fieldnames=fieldnames)
    if write_header:
        writer.writeheader()

    results = []
    for i, row in enumerate(rows, 1):
        qid = row["id"]
        domain = row["domain"]
        graph_path = graph_override if graph_override else resolve_graph_for_domain(domain)

        if not graph_path:
            msg = f"[{qid}] No built-in graph available for domain '{domain}' — skipped."
            print(msg)
            with open(errors_path, "a", encoding="utf-8") as ef:
                ef.write(msg + "\n")
            row_result = {
                "id": qid, "domain": domain, "category": row.get("category", ""),
                "endpoint_type": row.get("endpoint_type", ""), "modality": row.get("modality", ""),
                "graph": "", "status": "skipped_no_graph", "duration_seconds": "",
                "llm_calls": "", "total_tokens": "", "prompt_tokens": "",
                "completion_tokens": "",
                "overall_novelty_score": "", "technical_novelty_score": "",
                "application_novelty_score": "", "theoretical_novelty_score": "",
                "novelty_recommendation": "",
                "error": "no built-in graph found for domain",
            }
            writer.writerow(row_result)
            metrics_f.flush()
            results.append(row_result)
            continue

        print(f"[{i}/{len(rows)}] {qid} ({domain}) -> {graph_path.name}")
        result = run_one(row, graph_path, out_dir, args.timeout, path_eval_weights=path_eval_weights)
        writer.writerow(result)
        metrics_f.flush()
        results.append(result)

        if result["status"] != "ok":
            with open(errors_path, "a", encoding="utf-8") as ef:
                ef.write(f"[{qid}] status={result['status']}: {result['error'][:500]}\n")
        else:
            print(f"    done in {result['duration_seconds']}s, {result.get('total_tokens')} tokens")

        if args.delay:
            time.sleep(args.delay)

    metrics_f.close()

    ok = sum(1 for r in results if r["status"] == "ok")
    summary = {
        "total": len(results), "ok": ok, "failed": len(results) - ok,
        "out_dir": str(out_dir), "finished_at": datetime.now().isoformat(),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nDone: {ok}/{len(results)} succeeded. See {out_dir}")


if __name__ == "__main__":
    main()