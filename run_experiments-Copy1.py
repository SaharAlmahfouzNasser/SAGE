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


def run_one(row: dict, graph_path: Path, out_dir: Path, timeout: int) -> dict:
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

    t0 = time.time()
    result = {
        "id": qid, "domain": domain, "category": row.get("category", ""),
        "endpoint_type": row.get("endpoint_type", ""), "modality": row.get("modality", ""),
        "graph": graph_path.name, "status": "ok", "duration_seconds": None,
        "llm_calls": None, "total_tokens": None, "prompt_tokens": None,
        "completion_tokens": None, "error": "",
    }
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout)
        result["duration_seconds"] = round(time.time() - t0, 1)

        # Full console transcript, saved regardless of outcome — useful for debugging.
        (out_dir / "hypotheses" / f"{run_name}_console.txt").write_text(
            proc.stdout + "\n--- STDERR ---\n" + proc.stderr, encoding="utf-8"
        )

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
    args = ap.parse_args()

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
                  "prompt_tokens", "completion_tokens", "error"]
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
                "completion_tokens": "", "error": "no built-in graph found for domain",
            }
            writer.writerow(row_result)
            metrics_f.flush()
            results.append(row_result)
            continue

        print(f"[{i}/{len(rows)}] {qid} ({domain}) -> {graph_path.name}")
        result = run_one(row, graph_path, out_dir, args.timeout)
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



# #!/usr/bin/env python3
# """
# Batch-run SAGE hypothesis generation for every question in a CSV, selecting
# the correct built-in graph per row's `domain` column — no UI required.

# Expected CSV columns (matches SAGE_experiment_questions.csv):
#   id, domain, category, endpoint_type, attack_type, subtype, modality,
#   therapy_context, rep, prompt, notes

# `domain` must be one of: bladder, breast, prostate (case-insensitive) — these
# map to your three built-in graphs. Graph resolution is imported directly from
# UI/server.py (BUILTIN_GRAPHS / _find_builtin_graph), so this script picks the
# exact same file the UI would, including any custom "Save as built-in" graphs.

# Usage:
#   python run_experiments.py --csv SAGE_experiment_questions.csv
#   python run_experiments.py --csv SAGE_experiment_questions.csv --limit 5        # smoke test first
#   python run_experiments.py --csv SAGE_experiment_questions.csv --domain bladder # one domain only
#   python run_experiments.py --csv SAGE_experiment_questions.csv --resume         # continue an interrupted run
#   python run_experiments.py --csv SAGE_experiment_questions.csv --out-dir experiment_results/run1 --delay 2

# Outputs, under --out-dir (default: experiment_results/<timestamp>/):
#   hypotheses/hyp_<id>.md            - the generated hypothesis (from run_pipeline.py)
#   hypotheses/hyp_<id>_console.txt   - full stdout+stderr transcript for that run
#   metrics.csv                       - one row per question: timing, tokens, graph, status
#   errors.log                        - one line per failure/skip, append-only
#   summary.json                      - aggregate counts, written at the end of the run
# """

# import argparse
# import csv
# import json
# import subprocess
# import sys
# import time
# from datetime import datetime
# from pathlib import Path

# ROOT = Path(__file__).resolve().parent
# sys.path.insert(0, str(ROOT / "UI"))

# # Reuses the exact built-in graph resolution logic from server.py (including
# # any custom built-ins saved via "Save as built-in" in the UI), so this
# # script and the UI never disagree about which graph a domain maps to.
# # Importing server.py does NOT start the server — uvicorn.run() only fires
# # under `if __name__ == "__main__":`, which doesn't trigger on import.
# from server import _find_builtin_graph  # noqa: E402

# DOMAIN_TO_BUILTIN_KEY = {
#     "bladder": "builtin-bladder-cancer",
#     "breast": "builtin-breast-cancer",
#     "prostate": "builtin-prostate-cancer",
# }


# def resolve_graph_for_domain(domain: str) -> Path | None:
#     key = DOMAIN_TO_BUILTIN_KEY.get(domain.strip().lower())
#     if not key:
#         return None
#     _, path = _find_builtin_graph(key)
#     return path


# def run_one(row: dict, graph_path: Path, out_dir: Path, timeout: int) -> dict:
#     qid = row["id"]
#     domain = row["domain"]
#     prompt = row["prompt"]
#     run_name = f"hyp_{qid}"

#     cmd = [
#         sys.executable, str(ROOT / "run_pipeline.py"),
#         "--graph", str(graph_path),
#         "--domain", f"{domain} cancer",
#         "--query", prompt,
#         "--out-dir", str(out_dir / "hypotheses"),
#         "--run-name", run_name,
#         "--no-timestamp",
#     ]

#     t0 = time.time()
#     result = {
#         "id": qid, "domain": domain, "category": row.get("category", ""),
#         "endpoint_type": row.get("endpoint_type", ""), "modality": row.get("modality", ""),
#         "graph": graph_path.name, "status": "ok", "duration_seconds": None,
#         "llm_calls": None, "total_tokens": None, "prompt_tokens": None,
#         "completion_tokens": None, "error": "",
#     }
#     try:
#         proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout)
#         result["duration_seconds"] = round(time.time() - t0, 1)

#         # Full console transcript, saved regardless of outcome — useful for debugging.
#         (out_dir / "hypotheses" / f"{run_name}_console.txt").write_text(
#             proc.stdout + "\n--- STDERR ---\n" + proc.stderr, encoding="utf-8"
#         )

#         metrics_line = next(
#             (l for l in proc.stdout.splitlines() if l.startswith("SAGE_METRICS:")), None
#         )
#         if metrics_line:
#             try:
#                 m = json.loads(metrics_line[len("SAGE_METRICS:"):].strip())
#                 result.update({
#                     "llm_calls": m.get("llm_calls"),
#                     "total_tokens": m.get("total_tokens"),
#                     "prompt_tokens": m.get("prompt_tokens"),
#                     "completion_tokens": m.get("completion_tokens"),
#                 })
#             except Exception:
#                 pass

#         if proc.returncode != 0:
#             result["status"] = "failed"
#             result["error"] = proc.stderr.strip()[-2000:]
#     except subprocess.TimeoutExpired:
#         result["status"] = "timeout"
#         result["duration_seconds"] = timeout
#         result["error"] = f"Exceeded {timeout}s timeout"
#     except Exception as e:
#         result["status"] = "error"
#         result["error"] = str(e)

#     return result


# def main():
#     ap = argparse.ArgumentParser()
#     ap.add_argument("--csv", required=True)
#     ap.add_argument("--out-dir", default=None,
#                      help="default: experiment_results/<timestamp>/")
#     ap.add_argument("--domain", default=None, help="only run rows matching this domain")
#     ap.add_argument("--limit", type=int, default=0, help="only run the first N rows (0 = all)")
#     ap.add_argument("--resume", action="store_true",
#                      help="skip ids that already have a completed hypothesis .md in --out-dir")
#     ap.add_argument("--timeout", type=int, default=1800,
#                      help="per-question timeout in seconds (default 30 min)")
#     ap.add_argument("--delay", type=float, default=0,
#                      help="seconds to sleep between questions (basic rate limiting)")
#     args = ap.parse_args()

#     out_dir = Path(args.out_dir) if args.out_dir else ROOT / "experiment_results" / datetime.now().strftime("%Y%m%d_%H%M%S")
#     (out_dir / "hypotheses").mkdir(parents=True, exist_ok=True)
#     errors_path = out_dir / "errors.log"
#     metrics_path = out_dir / "metrics.csv"

#     with open(args.csv, newline="", encoding="utf-8") as f:
#         rows = list(csv.DictReader(f))

#     if args.domain:
#         rows = [r for r in rows if r["domain"].strip().lower() == args.domain.strip().lower()]
#     if args.limit:
#         rows = rows[: args.limit]

#     if args.resume:
#         done_ids = {p.stem.replace("hyp_", "") for p in (out_dir / "hypotheses").glob("hyp_*.md")}
#         before = len(rows)
#         rows = [r for r in rows if r["id"] not in done_ids]
#         print(f"Resume: skipping {before - len(rows)} already-completed question(s)")

#     print(f"Running {len(rows)} question(s) -> {out_dir}")

#     fieldnames = ["id", "domain", "category", "endpoint_type", "modality", "graph",
#                   "status", "duration_seconds", "llm_calls", "total_tokens",
#                   "prompt_tokens", "completion_tokens", "error"]
#     write_header = not metrics_path.exists()
#     metrics_f = open(metrics_path, "a", newline="", encoding="utf-8")
#     writer = csv.DictWriter(metrics_f, fieldnames=fieldnames)
#     if write_header:
#         writer.writeheader()

#     results = []
#     for i, row in enumerate(rows, 1):
#         qid = row["id"]
#         domain = row["domain"]
#         graph_path = resolve_graph_for_domain(domain)

#         if not graph_path:
#             msg = f"[{qid}] No built-in graph available for domain '{domain}' — skipped."
#             print(msg)
#             with open(errors_path, "a", encoding="utf-8") as ef:
#                 ef.write(msg + "\n")
#             row_result = {
#                 "id": qid, "domain": domain, "category": row.get("category", ""),
#                 "endpoint_type": row.get("endpoint_type", ""), "modality": row.get("modality", ""),
#                 "graph": "", "status": "skipped_no_graph", "duration_seconds": "",
#                 "llm_calls": "", "total_tokens": "", "prompt_tokens": "",
#                 "completion_tokens": "", "error": "no built-in graph found for domain",
#             }
#             writer.writerow(row_result)
#             metrics_f.flush()
#             results.append(row_result)
#             continue

#         print(f"[{i}/{len(rows)}] {qid} ({domain}) -> {graph_path.name}")
#         result = run_one(row, graph_path, out_dir, args.timeout)
#         writer.writerow(result)
#         metrics_f.flush()
#         results.append(result)

#         if result["status"] != "ok":
#             with open(errors_path, "a", encoding="utf-8") as ef:
#                 ef.write(f"[{qid}] status={result['status']}: {result['error'][:500]}\n")
#         else:
#             print(f"    done in {result['duration_seconds']}s, {result.get('total_tokens')} tokens")

#         if args.delay:
#             time.sleep(args.delay)

#     metrics_f.close()

#     ok = sum(1 for r in results if r["status"] == "ok")
#     summary = {
#         "total": len(results), "ok": ok, "failed": len(results) - ok,
#         "out_dir": str(out_dir), "finished_at": datetime.now().isoformat(),
#     }
#     (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
#     print(f"\nDone: {ok}/{len(results)} succeeded. See {out_dir}")


# if __name__ == "__main__":
#     main()