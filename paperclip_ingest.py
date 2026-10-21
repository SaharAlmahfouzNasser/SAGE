"""
CLI wrapper around paperclip_extractor.extract_subgraph().

paperclip_extractor.py only exposes a Python function, not a command-line
entry point — this script gives it one, matching the same conventions as
ingest_source.py (same env/key loading, same OUTPUT_GRAPHML= stdout line so
server.py's existing _ingest_on_done() parser works unmodified).

Two modes:
  --graph omitted   -> build a brand-new graph purely from the Paperclip
                        search results (nothing to merge into).
  --graph provided  -> merge the extracted subgraph into that existing
                        GraphML file (accumulating weight/evidence on
                        overlapping edges, same approach as
                        ingest_source.py's merge_local_into_global).

Usage:
    # Start a new graph from a Paperclip literature search
    python paperclip_ingest.py --query "FGFR3 bladder cancer" --source pmc \
                               --n 15 --out-dir outputs/sage_kg \
                               --graph-root bladder_paperclip

    # Enhance an existing graph
    python paperclip_ingest.py --graph outputs/sage_kg/my_kg.graphml \
                               --query "CXCL13 tertiary lymphoid structures" \
                               --source pmc --n 10 --out-dir outputs/sage_kg
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import networkx as nx

# paperclip_extractor.py / paperclip_tools.py live inside HypothesisGeneration-pkg/
# HypothesisGeneration/ (same place as path_evaluation.py, utils.py) — this needs
# to be on sys.path BEFORE `from HypothesisGeneration import paperclip_extractor`
# can succeed. Same pattern run_path_discovery.py uses.
ROOT = Path(__file__).resolve().parent
for _pkg in (ROOT / "HypothesisGeneration-pkg", ROOT / "GraphReasoning"):
    if _pkg.exists() and str(_pkg) not in sys.path:
        sys.path.insert(0, str(_pkg))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("paperclip_ingest")

# ---------------------------------------------------------------------------
# Env / keys — same pattern as ingest_source.py / build_kg.py
# ---------------------------------------------------------------------------
for _env in (ROOT / ".env", Path(".env")):
    if _env.exists():
        for _line in _env.read_text().splitlines():
            if "=" in _line and not _line.startswith("#"):
                k, v = _line.strip().split("=", 1)
                os.environ.setdefault(k, v)
        break

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")


def _make_generator():
    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Triple extraction requires an OpenAI key."
        )
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    cc = {"count": 0, "errors": 0}

    def generate_structured(system_prompt: str, user_prompt: str, schema: dict):
        cc["count"] += 1
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": system_prompt},
                      {"role": "user", "content": user_prompt}],
            temperature=0.1,
            max_tokens=4096,
            response_format=schema,
        )
        return json.loads(resp.choices[0].message.content)

    return generate_structured, cc


# ---------------------------------------------------------------------------
# Merge — same accumulate-weight approach as ingest_source.py's
# merge_local_into_global, extended to also merge Paperclip's provenance
# fields (sources / source_count) rather than clobbering them.
# ---------------------------------------------------------------------------

def merge_paperclip_into_global(G_global: nx.DiGraph, G_new: nx.DiGraph) -> int:
    """Add nodes/edges from G_new into G_global. Returns count of new edges."""
    added = 0
    for n, d in G_new.nodes(data=True):
        if not G_global.has_node(n):
            G_global.add_node(n, **d)
    for u, v, d in G_new.edges(data=True):
        if G_global.has_edge(u, v):
            existing = G_global[u][v]
            existing["weight"] = existing.get("weight", 0) + d.get("weight", 0)
            # Merge provenance rather than overwrite it
            existing_sources = set(filter(None, str(existing.get("sources", "")).split(",")))
            new_sources = set(filter(None, str(d.get("sources", "")).split(",")))
            merged_sources = existing_sources | new_sources
            if merged_sources:
                existing["sources"] = ",".join(sorted(merged_sources))
                existing["source_count"] = len(merged_sources)
        else:
            G_global.add_edge(u, v, **d)
            added += 1
    return added


def main():
    ap = argparse.ArgumentParser(description="Ingest a Paperclip literature search into a KG.")
    ap.add_argument("--graph", default="",
                    help="path to an existing .graphml to merge into (omit to create a new graph)")
    ap.add_argument("--query", required=True, help="literature search string")
    ap.add_argument("--source", default="pmc",
                    choices=["pmc", "biorxiv", "medrxiv", "trials", "fda", "patents"],
                    help="Paperclip corpus to search")
    ap.add_argument("--n", type=int, default=10, help="papers to retrieve")
    ap.add_argument("--since", default=None, help='start date filter, e.g. "2020-01"')
    ap.add_argument("--until", default=None, help='end date filter, e.g. "2024-12"')
    ap.add_argument("--conf-threshold", type=float, default=0.5)
    ap.add_argument("--tau", type=float, default=0.9, help="entity-resolution cosine threshold")
    ap.add_argument("--expand-with-map", action="store_true",
                    help="opt-in structured enrichment via Paperclip's lit_map (slower)")
    ap.add_argument("--out-dir", default="outputs/sage_kg")
    ap.add_argument("--graph-root", default="",
                    help="base name for a brand-new graph (only used when --graph is omitted)")
    args = ap.parse_args()

    # sage_kg / paperclip_extractor need HypothesisGeneration-pkg / GraphReasoning
    # on sys.path — paperclip_extractor.py already does this relative to its own
    # location, so import it first and let that path setup run.
    from HypothesisGeneration import paperclip_extractor
    import sage_kg

    generate_structured, cc = _make_generator()

    log.info("Searching Paperclip: query=%r source=%s n=%d", args.query, args.source, args.n)
    G_new = paperclip_extractor.extract_subgraph(
        query=args.query,
        generate_structured=generate_structured,
        source=args.source,
        n=args.n,
        since=args.since,
        date_end=args.until,
        expand_with_map=args.expand_with_map,
        conf_threshold=args.conf_threshold,
        tau=args.tau,
    )
    log.info("Paperclip subgraph: %d nodes, %d edges (LLM calls: %d)",
             G_new.number_of_nodes(), G_new.number_of_edges(), cc["count"])

    if G_new.number_of_nodes() == 0:
        log.warning("No triples extracted from Paperclip results — nothing to save.")
        sys.exit(0)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.graph:
        graph_path = Path(args.graph)
        if not graph_path.exists():
            log.error("Graph not found: %s", graph_path)
            sys.exit(1)
        log.info("Loading existing graph: %s", graph_path)
        G_global = nx.read_graphml(str(graph_path))
        log.info("  Existing: %d nodes, %d edges", G_global.number_of_nodes(), G_global.number_of_edges())

        n_added = merge_paperclip_into_global(G_global, G_new)
        log.info("  After merge: %d nodes, %d edges (+%d new edges)",
                 G_global.number_of_nodes(), G_global.number_of_edges(), n_added)

        graph_root = args.graph_root or graph_path.stem.rsplit("_", 2)[0]
        out_path = sage_kg.save_graph(G_global, out_dir, graph_root=graph_root)
    else:
        graph_root = args.graph_root or "paperclip_kg"
        log.info("No --graph provided — saving Paperclip results as a new graph "
                 "(graph_root=%s)", graph_root)
        out_path = sage_kg.save_graph(G_new, out_dir, graph_root=graph_root)

    log.info("Saved: %s", out_path)
    print(f"OUTPUT_GRAPHML={out_path}")


if __name__ == "__main__":
    main()
