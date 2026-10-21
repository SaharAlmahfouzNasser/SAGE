#!/usr/bin/env python3
"""
Fast ablation study for the four path-scoring weights (logic, relevance,
novelty, surprise) used by run_path_discovery.py's weighted_total_score.

Unlike calling run_path_discovery.py as a subprocess once per condition
(which reloads the BGE-large embedding model and the graph from scratch on
every single call), this script imports run_path_discovery.py as a module
and:
  1. Loads the embedding model + graph + node embeddings ONCE PER GRAPH
     (3 times total for bladder/breast/prostate), not once per run.
  2. For each question, computes logic_score / relevance_score /
     novelty_score / surprise_score ONCE (these don't depend on the
     weights at all — only weighted_total_score does).
  3. Recombines those four scores 5 different ways (baseline + 4 leave-
     one-out ablations) via the real compute_weighted_total_score() /
     sort_paths_by_score() functions — pure arithmetic, no model calls.

This is the same computation as running the CLI 5x per question, just
without redoing the expensive path-search + Sentence-BERT + embedding-
similarity work 5 times over. Expect this to be dramatically faster than
5x the single-run cost — run a small --limit test first to see actual
timing on your hardware before committing to the full CSV.

Ablation convention: each condition zeroes exactly one weight, others stay
at their defaults (0.2/0.2/0.3/0.3) — see run_path_discovery.py's own
--skip-logic handling, which does the same (zero without renormalizing).
This doesn't change ranking vs. a renormalized version: zeroing one term in
a linear combination and leaving the other ratios unchanged produces
identical relative rankings to renormalizing to sum to 1.

Usage:
  python run_ablation.py --csv SAGE_experiment_questions.csv
  python run_ablation.py --csv SAGE_experiment_questions.csv --limit 5          # time a small batch first
  python run_ablation.py --csv SAGE_experiment_questions.csv --domain prostate
  python run_ablation.py --csv SAGE_experiment_questions.csv --graph-override /path/to/graph.graphml
  python run_ablation.py --csv SAGE_experiment_questions.csv --out-dir experiment_results/ablation --resume

Outputs, under --out-dir (default: experiment_results/ablation_<timestamp>/):
  <condition>/<id>.json                 - full path-ranking output per condition per question
  <condition>/<id>.txt                  - human-readable version (same format as the CLI)
  metrics.csv                            - one row per (question, condition): timing, path counts, top-1 result
  comparison.csv                         - one row per (question, ablated condition): how much the top-N changed vs. baseline
  errors.log                             - one line per failure/skip, append-only
  summary.json                           - aggregate counts + top-1-change rates, written at the end
"""

import argparse
import copy
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "UI"))

# Importing run_path_discovery.py as a module runs its top-level imports
# (transformers, GraphReasoning, HypothesisGeneration) once, and gives us
# direct access to its functions without spawning subprocesses. It has no
# side effects at import time beyond that — main() only runs when the file
# is executed directly (guarded by `if __name__ == "__main__":`).
import run_path_discovery as rpd  # noqa: E402

from server import _find_builtin_graph  # noqa: E402

DOMAIN_TO_BUILTIN_KEY = {
    "bladder": "builtin-bladder-cancer",
    "breast": "builtin-breast-cancer",
    "prostate": "builtin-prostate-cancer",
}

BASE_WEIGHTS = {"logic": 0.2, "relevance": 0.2, "novelty": 0.3, "surprise": 0.3}

ABLATION_CONDITIONS = {
    "baseline":     {},
    "no_logic":     {"logic": 0.0},
    "no_relevance": {"relevance": 0.0},
    "no_novelty":   {"novelty": 0.0},
    "no_surprise":  {"surprise": 0.0},
}


def resolve_graph_for_domain(domain: str) -> Path | None:
    key = DOMAIN_TO_BUILTIN_KEY.get(domain.strip().lower())
    if not key:
        return None
    _, path = _find_builtin_graph(key)
    return path


def infer_and_validate(query: str, types: set[str]) -> tuple[str, list[str]]:
    """Same source/target-type inference + validation as run_path_discovery.py's
    main(), but raising ValueError instead of SystemExit so a batch run can
    skip one bad question instead of aborting."""
    source = rpd.infer_source_entity(query)
    if not source:
        raise ValueError("Could not infer source entity from query")
    if source.lower() in rpd.CATEGORY_WORDS:
        raise ValueError(f"Inferred source '{source}' looks like a category, not a specific entity")

    target_type_raw = rpd.infer_target_type(query)
    if not target_type_raw:
        raise ValueError("Could not infer target_type from query")
    if target_type_raw.lower() in rpd.INVALID_LABELS:
        raise ValueError(f"target_type '{target_type_raw}' is not a valid graph target type")

    target_types, _notes = rpd.resolve_target_types(target_type_raw, types, expand_groups_first=True)
    if not target_types:
        raise ValueError(f"target_type '{target_type_raw}' does not exist in this graph")

    return source, target_types


def save_condition_output(cond_dir: Path, qid: str, paths: list[dict], summary: dict,
                          source: str, target_types: list[str], query: str, domain: str,
                          weights: dict) -> None:
    cond_dir.mkdir(parents=True, exist_ok=True)
    target_type_display = ", ".join(target_types)

    text = rpd.format_ranked_paths(
        paths=paths, total_found=summary.get("successful_paths", len(paths)),
        source=source, target_type=target_type_display, query=query, domain=domain,
        weights=weights, rank_by="total", resolve_note=None,
    ) if paths else f"No paths found from '{source}' to nodes of type '{target_type_display}'."

    (cond_dir / f"{qid}.txt").write_text(text, encoding="utf-8")

    payload = {
        "query": query, "source": source, "target_type": target_type_display,
        "target_types": target_types, "domain": domain, "weights": weights,
        "rank_by": "total", "summary": summary, "paths": paths,
    }
    (cond_dir / f"{qid}.json").write_text(json.dumps(rpd.to_jsonable(payload), indent=2), encoding="utf-8")


def top_n_targets(out_dir: Path, condition: str, qid: str, n: int) -> list[str]:
    json_path = out_dir / condition / f"{qid}.json"
    if not json_path.exists():
        return []
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        return [p.get("target") for p in data.get("paths", [])[:n]]
    except Exception:
        return []


def compare_to_baseline(out_dir: Path, qid: str, top_n: int) -> list[dict]:
    baseline_top = top_n_targets(out_dir, "baseline", qid, top_n)
    rows = []
    for condition in ABLATION_CONDITIONS:
        if condition == "baseline":
            continue
        ablated_top = top_n_targets(out_dir, condition, qid, top_n)
        overlap = len(set(baseline_top) & set(ablated_top)) if baseline_top and ablated_top else None
        top1_changed = (baseline_top[0] != ablated_top[0]) if (baseline_top and ablated_top) else None
        rows.append({
            "id": qid, "ablated_condition": condition,
            "baseline_top1": baseline_top[0] if baseline_top else None,
            "ablated_top1": ablated_top[0] if ablated_top else None,
            "top1_changed": top1_changed,
            f"top{top_n}_overlap": overlap,
            f"top{top_n}_overlap_frac": round(overlap / top_n, 2) if overlap is not None else None,
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out-dir", default=None,
                     help="default: experiment_results/ablation_<timestamp>/")
    ap.add_argument("--domain", default=None, help="only run rows matching this domain")
    ap.add_argument("--graph-override", default=None,
                     help="use this exact .graphml path for every row, bypassing domain-based "
                          "built-in resolution")
    ap.add_argument("--limit", type=int, default=0, help="only run the first N rows (0 = all)")
    ap.add_argument("--resume", action="store_true",
                     help="skip questions that already have baseline output JSON in --out-dir")
    ap.add_argument("--max-targets", type=int, default=30)
    ap.add_argument("--top-n", type=int, default=5)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    import random
    random.seed(args.seed)

    out_dir = Path(args.out_dir) if args.out_dir else ROOT / "experiment_results" / f"ablation_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    errors_path = out_dir / "errors.log"
    metrics_path = out_dir / "metrics.csv"
    comparison_path = out_dir / "comparison.csv"

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

    if args.resume:
        before = len(rows)
        rows = [r for r in rows if not (out_dir / "baseline" / f"{r['id']}.json").exists()]
        print(f"Resume: skipping {before - len(rows)} already-completed question(s)")

    # Group by resolved graph path so each graph's model/embeddings load once.
    groups: dict[Path, list[dict]] = {}
    skipped = 0
    for row in rows:
        graph_path = graph_override if graph_override else resolve_graph_for_domain(row["domain"])
        if not graph_path:
            msg = f"[{row['id']}] No built-in graph for domain '{row['domain']}' — skipped."
            print(msg)
            with open(errors_path, "a", encoding="utf-8") as ef:
                ef.write(msg + "\n")
            skipped += 1
            continue
        groups.setdefault(graph_path, []).append(row)

    total_questions = sum(len(g) for g in groups.values())
    print(f"{total_questions} question(s) across {len(groups)} graph(s), "
          f"{len(ABLATION_CONDITIONS)} conditions each -> {out_dir}")

    fieldnames = ["id", "domain", "condition", "logic_weight", "relevance_weight",
                  "novelty_weight", "surprise_weight", "graph", "status",
                  "duration_seconds", "total_found", "paths_calculated",
                  "successful_paths", "top1_target", "top1_score", "error"]
    write_header = not metrics_path.exists()
    metrics_f = open(metrics_path, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(metrics_f, fieldnames=fieldnames)
    if write_header:
        writer.writeheader()

    run_t0 = time.time()
    done_questions = 0

    for graph_path, group_rows in groups.items():
        print(f"\n=== Loading graph + embedding model for {graph_path.name} "
              f"({len(group_rows)} questions) ===")
        load_t0 = time.time()
        embedding_tokenizer, embedding_model, directed_G, G, node_embeddings = \
            rpd.load_graph_and_embeddings(graph_path)
        types = rpd.available_node_types(G)
        print(f"    loaded in {time.time() - load_t0:.1f}s "
              f"({G.number_of_nodes()} nodes, {G.number_of_edges()} edges)")

        for row in group_rows:
            qid, domain, query = row["id"], row["domain"], row["prompt"]
            full_domain = f"{domain} cancer"
            done_questions += 1
            print(f"[{done_questions}/{total_questions}] {qid} ({domain})")

            try:
                source, target_types = infer_and_validate(query, types)
            except ValueError as e:
                msg = f"[{qid}] {e}"
                print(f"    SKIPPED: {msg}")
                with open(errors_path, "a", encoding="utf-8") as ef:
                    ef.write(msg + "\n")
                for condition_name, override in ABLATION_CONDITIONS.items():
                    weights = {**BASE_WEIGHTS, **override}
                    writer.writerow({
                        "id": qid, "domain": domain, "condition": condition_name,
                        "logic_weight": weights["logic"], "relevance_weight": weights["relevance"],
                        "novelty_weight": weights["novelty"], "surprise_weight": weights["surprise"],
                        "graph": graph_path.name, "status": "skipped_inference",
                        "duration_seconds": None, "total_found": None, "paths_calculated": None,
                        "successful_paths": None, "top1_target": None, "top1_score": None,
                        "error": str(e),
                    })
                metrics_f.flush()
                continue

            # Expensive part — done ONCE per question, at baseline weights.
            # logic/relevance/novelty/surprise scores don't depend on weights,
            # so this single call gives every condition what it needs.
            q_t0 = time.time()
            try:
                paths, summary = rpd.evaluate_and_rank_paths(
                    directed_G=directed_G, G=G,
                    embedding_tokenizer=embedding_tokenizer, embedding_model=embedding_model,
                    node_embeddings=node_embeddings,
                    source=source, target_types=target_types, query=query, domain=full_domain,
                    max_targets=args.max_targets, top_n=args.top_n,
                    weights=BASE_WEIGHTS, skip_logic=False, rank_by="total",
                )
            except Exception as e:
                msg = f"[{qid}] scoring failed: {e}"
                print(f"    FAILED: {msg}")
                with open(errors_path, "a", encoding="utf-8") as ef:
                    ef.write(msg + "\n")
                for condition_name, override in ABLATION_CONDITIONS.items():
                    weights = {**BASE_WEIGHTS, **override}
                    writer.writerow({
                        "id": qid, "domain": domain, "condition": condition_name,
                        "logic_weight": weights["logic"], "relevance_weight": weights["relevance"],
                        "novelty_weight": weights["novelty"], "surprise_weight": weights["surprise"],
                        "graph": graph_path.name, "status": "error",
                        "duration_seconds": round(time.time() - q_t0, 1), "total_found": None,
                        "paths_calculated": None, "successful_paths": None,
                        "top1_target": None, "top1_score": None, "error": str(e),
                    })
                metrics_f.flush()
                continue

            base_duration = time.time() - q_t0

            # Cheap part — recombine the same per-path scores 5 different
            # ways. No model calls here, just arithmetic + sorting.
            for condition_name, override in ABLATION_CONDITIONS.items():
                weights = {**BASE_WEIGHTS, **override}
                cond_t0 = time.time()

                paths_copy = copy.deepcopy(paths)
                paths_copy = rpd.compute_weighted_total_score(paths=paths_copy, weights=weights)
                paths_copy = rpd.add_novel_path_scores(paths_copy, G=G, source=source,
                                                        domain=full_domain, query=query)
                paths_copy = rpd.sort_paths_by_score(paths=paths_copy, score_key="weighted_total_score",
                                                      reverse=True)
                for i, p in enumerate(paths_copy, 1):
                    p["rank"] = i
                top_paths = paths_copy[: args.top_n]

                cond_dir = out_dir / condition_name
                save_condition_output(cond_dir, qid, top_paths, summary, source, target_types,
                                      query, full_domain, weights)

                cond_duration = round(base_duration + (time.time() - cond_t0), 2) \
                    if condition_name == "baseline" else round(time.time() - cond_t0, 3)

                writer.writerow({
                    "id": qid, "domain": domain, "condition": condition_name,
                    "logic_weight": weights["logic"], "relevance_weight": weights["relevance"],
                    "novelty_weight": weights["novelty"], "surprise_weight": weights["surprise"],
                    "graph": graph_path.name, "status": "ok",
                    "duration_seconds": cond_duration,
                    "total_found": summary.get("total_found"),
                    "paths_calculated": summary.get("paths_calculated"),
                    "successful_paths": summary.get("successful_paths"),
                    "top1_target": top_paths[0]["target"] if top_paths else None,
                    "top1_score": top_paths[0].get("weighted_total_score") if top_paths else None,
                    "error": "",
                })
                metrics_f.flush()

            print(f"    scored in {base_duration:.1f}s (shared across all 5 conditions)")

    metrics_f.close()
    total_elapsed = time.time() - run_t0
    print(f"\nAll scoring done in {total_elapsed/60:.1f} min.")

    print("Building comparison.csv (baseline vs. each ablated condition)...")
    comparison_rows = []
    for group_rows in groups.values():
        for row in group_rows:
            comparison_rows.extend(compare_to_baseline(out_dir, row["id"], args.top_n))

    top1_change_summary = {}
    if comparison_rows:
        comp_fields = list(comparison_rows[0].keys())
        with open(comparison_path, "w", newline="", encoding="utf-8") as cf:
            comp_writer = csv.DictWriter(cf, fieldnames=comp_fields)
            comp_writer.writeheader()
            comp_writer.writerows(comparison_rows)

        by_condition: dict[str, list[bool]] = {}
        for r in comparison_rows:
            if r["top1_changed"] is not None:
                by_condition.setdefault(r["ablated_condition"], []).append(r["top1_changed"])
        print("\nTop-1 result changed when removing each term:")
        for condition, changes in by_condition.items():
            pct = 100 * sum(changes) / len(changes) if changes else 0
            top1_change_summary[condition] = {"changed": sum(changes), "total": len(changes), "pct": round(pct, 1)}
            print(f"  {condition:15s}: {sum(changes)}/{len(changes)} questions ({pct:.0f}%)")

    summary = {
        "questions": total_questions, "skipped_no_graph": skipped,
        "conditions": list(ABLATION_CONDITIONS.keys()),
        "total_elapsed_minutes": round(total_elapsed / 60, 1),
        "top1_change_by_condition": top1_change_summary,
        "out_dir": str(out_dir), "finished_at": datetime.now().isoformat(),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nDone. See {out_dir} (metrics.csv, comparison.csv, summary.json, errors.log)")


if __name__ == "__main__":
    main()