"""
Seam ②: Paperclip-grounded subgraph extraction.

Flow:
  lit_search(query)
    → lit_grep(result_id, RELATION_CUES)    # relation-bearing paragraphs
    → [group paragraphs by paper / pmid]
    → sage_kg.extract_triples(text, generate_structured)   # local LLM, SAGE schema
    → sage_kg.build_local_graph(triples, doc_id)
    → sage_kg.fuse_graphs(local_graphs)
    → nx.DiGraph  (drop-in for nx.compose with existing KG)

Edges carry evidence, sources (comma-separated pmids), source_count in addition
to the standard SAGE weight / rnorm / rtext attributes.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

import networkx as nx

# Ensure project packages are importable when called as a script
_ROOT = Path(__file__).resolve().parent.parent.parent
for _pkg in (_ROOT / "HypothesisGeneration-pkg", _ROOT / "GraphReasoning"):
    if _pkg.exists() and str(_pkg) not in sys.path:
        sys.path.insert(0, str(_pkg))

import sage_kg
from HypothesisGeneration.paperclip_tools import lit_grep, lit_map, lit_search

log = logging.getLogger("paperclip_extractor")

# ---------------------------------------------------------------------------
# Grep pattern — broad enough to capture relation-bearing paragraphs while
# filtering out purely descriptive / demographic / methods text.
# ---------------------------------------------------------------------------
RELATION_CUES = (
    r"associated with|inhibit|activat|upregulat|downregulat|regulat|"
    r"express|biomarker|predict|diagnos|treat|target|resistance|response|"
    r"mutat|variant|pathway|interact|induc|suppress|promot|causes|trigger|"
    r"correlat|stratif|prognos|marker for|located in|part of|measured by"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_subgraph(
    query: str,
    generate_structured: Callable,
    source: str = "pmc",
    n: int = 10,
    since: Optional[str] = None,
    date_end: Optional[str] = None,
    cache_dir: Optional[Path] = None,
    expand_with_map: bool = False,
    conf_threshold: float = 0.5,
    tau: float = 0.9,
) -> nx.DiGraph:
    """Search Paperclip, extract paragraph-level typed triples, return a fused DiGraph.

    The returned graph merges cleanly with the existing KG via::

        import networkx as nx
        G_expanded = nx.compose(G_existing, extract_subgraph(...))

    Args:
        query             : Literature search string.
        generate_structured: LLM callable — same signature as sage_kg.extract_triples
                             expects: (system_prompt, user_prompt, schema) → dict
        source            : Paperclip corpus — "pmc", "biorxiv", "medrxiv",
                             "trials", "fda", "patents".
        n                 : Papers to retrieve.
        since             : Start date filter "YYYY-MM".
        date_end          : End date filter "YYYY-MM" (time-travel / leakage control).
        cache_dir         : BGE embedding cache; defaults to outputs/sage_kg/embed_cache.
        expand_with_map   : If True, also call lit_map for structured enrichment (opt-in;
                            external dependency — keep False on the critical path).
        conf_threshold    : Minimum triple confidence (mirrors sage_kg default 0.5).
        tau               : Entity-resolution cosine threshold for fuse_graphs (0.9).

    Returns:
        nx.DiGraph with SAGE-schema nodes and edges.  Empty graph if no triples extracted.
    """
    if cache_dir is None:
        cache_dir = _ROOT / "outputs" / "sage_kg" / "embed_cache"
    cache_dir = Path(cache_dir)

    # ── 1. Literature search ──────────────────────────────────────────────────
    log.info("[paperclip] search: %r  source=%s  n=%d", query, source, n)
    result_id, papers = lit_search(query, source=source, n=n, since=since, date_end=date_end)
    log.info("[paperclip] result_id=%s  papers=%d", result_id, len(papers))

    if not papers:
        log.warning("[paperclip] no papers returned — check query or source")
        return nx.DiGraph()

    # Build pmid → metadata lookup for attribution
    paper_meta: Dict[str, Dict] = {}
    for p in papers:
        pid = str(p.get("pmid") or p.get("id") or p.get("doi") or "")
        if pid:
            paper_meta[pid] = p

    # ── 2. Grep for relation-containing paragraphs ────────────────────────────
    log.info("[paperclip] grep: result_id=%s", result_id)
    paragraphs = lit_grep(result_id, RELATION_CUES)
    log.info("[paperclip] %d paragraphs matched", len(paragraphs))

    # Group by paper
    para_by_paper: Dict[str, List[str]] = {}
    for para in paragraphs:
        pid = str(para.get("pmid") or para.get("paper_id") or para.get("id") or "unknown")
        text = (para.get("text") or "").strip()
        if text:
            para_by_paper.setdefault(pid, []).append(text)

    # Fallback: if grep returned nothing, use abstract/snippet from search results
    if not para_by_paper:
        log.info("[paperclip] grep returned no matches — falling back to abstracts")
        for p in papers:
            pid = str(p.get("pmid") or p.get("id") or "")
            abstract = (p.get("abstract") or p.get("snippet") or "").strip()
            if pid and abstract:
                para_by_paper[pid] = [abstract]

    # ── 3. Optional lit_map enrichment (opt-in) ───────────────────────────────
    if expand_with_map and result_id:
        log.info("[paperclip] map: structured enrichment (expand_with_map=True)")
        try:
            map_results = lit_map(result_id, query)
            # Fold map answers into existing paragraph text for the same paper
            for mr in map_results:
                pid = str(mr.get("paper_id") or mr.get("pmid") or "")
                answer = (mr.get("answer") or "").strip()
                if pid and answer:
                    para_by_paper.setdefault(pid, []).append(answer)
        except Exception as exc:
            log.warning("[paperclip] lit_map failed (non-critical): %s", exc)

    # ── 4. Triple extraction — one local graph per paper ─────────────────────
    local_graphs: List[nx.DiGraph] = []
    for pid, para_list in para_by_paper.items():
        combined = "\n\n".join(para_list)
        try:
            triples = sage_kg.extract_triples(
                combined, generate_structured, conf_threshold=conf_threshold
            )
        except Exception as exc:
            log.warning("[paperclip] extract_triples failed for %s: %s", pid, exc)
            continue

        if not triples:
            continue

        lg = sage_kg.build_local_graph(triples, doc_id=pid)

        # Tag edges with source paper for evidence traceability
        for _, _, d in lg.edges(data=True):
            d["sources"] = pid
            d["source_count"] = 1

        local_graphs.append(lg)
        log.info("[paperclip] %s → %d triples, %d nodes, %d edges",
                 pid, len(triples), lg.number_of_nodes(), lg.number_of_edges())

    if not local_graphs:
        log.warning("[paperclip] no triples extracted — returning empty graph")
        return nx.DiGraph()

    # ── 5. Fuse into a single subgraph ────────────────────────────────────────
    # min_component=2 instead of 3 so small-but-valid subgraphs survive
    G = sage_kg.fuse_graphs(local_graphs, cache_dir=cache_dir, tau=tau, min_component=2)

    # Annotate fused edges with full paper set (sources already merged by fuse)
    all_pmids = ",".join(paper_meta.keys())
    for _, _, d in G.edges(data=True):
        if not d.get("sources"):
            d["sources"] = all_pmids
        if not d.get("source_count"):
            d["source_count"] = len(paper_meta)

    log.info("[paperclip] subgraph: %d nodes, %d edges", G.number_of_nodes(), G.number_of_edges())
    return G
