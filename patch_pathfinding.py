"""
Patch: restore GraphReasoning path functions into HypothesisGeneration.utils.

HypothesisGeneration.utils does `from GraphReasoning import *`, but those names
are NOT re-exported by GraphReasoning's package __init__, so several functions
that `create_path` / `find_paths_to_label` call resolve to NameError at runtime
(e.g. `find_path`, `print_path_with_edges_as_list`). This makes the path-
generation agent return "No paths found" for every query regardless of graph.

This module injects the missing names into the utils module namespace without
editing the vendored package. Import it once before running the pipeline:

    import patch_pathfinding  # noqa: F401  (applies on import)
"""

import os
import time

import requests

import HypothesisGeneration.utils as _utils
import GraphReasoning.graph_analysis as _ga
import GraphReasoning.graph_tools as _gt


# ---------------------------------------------------------------------------
# OpenAlex replacement for semanticscholar_search (no API key, no 403/429 wall).
# Same name / signature / string-return contract as the original, so it drops
# in wherever the novelty critic uses it.
# ---------------------------------------------------------------------------
_OPENALEX = "https://api.openalex.org/works"
_MAILTO = os.environ.get("OPENALEX_MAILTO", "jduke99@gmail.com")


def semanticscholar_search(query: str, max_results: int = 10) -> str:
    """Literature search via OpenAlex (drop-in for the Semantic Scholar tool)."""
    try:
        time.sleep(0.15)
        r = requests.get(_OPENALEX, timeout=15, params={
            "search": query, "per_page": max_results, "mailto": _MAILTO,
            "select": "title,publication_year,cited_by_count,doi,"
                      "primary_location,authorships,abstract_inverted_index",
        })
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return f"No results found in OpenAlex for query: {query}"

        out = f"OpenAlex Literature Search Results for: {query}\n" + "=" * 70 + "\n\n"
        for i, w in enumerate(results, 1):
            title = w.get("title") or "No title"
            year = w.get("publication_year") or "Unknown"
            src = (w.get("primary_location") or {}).get("source") or {}
            venue = src.get("display_name") or "Unknown venue"
            doi = (w.get("doi") or "").replace("https://doi.org/", "")
            cites = w.get("cited_by_count", 0)
            auths = [a.get("author", {}).get("display_name", "")
                     for a in (w.get("authorships") or [])[:3]]
            astr = ", ".join([a for a in auths if a])
            if len(w.get("authorships") or []) > 3:
                astr += " et al."
            # reconstruct abstract from inverted index (OpenAlex format)
            abstract = _deinvert_abstract(w.get("abstract_inverted_index"))
            out += f"{i}. {title}\n"
            out += f"   Authors: {astr}\n"
            out += f"   Source: {venue}\n"
            out += f"   Year: {year}\n"
            out += f"   Citations: {cites}\n"
            if doi:
                out += f"   DOI: {doi}\n"
            if abstract:
                out += f"   Abstract: {abstract[:300]}{'...' if len(abstract) > 300 else ''}\n"
            out += "\n"
        return out
    except Exception as e:
        return f"Error searching OpenAlex: {e}"


def _deinvert_abstract(inv) -> str:
    """OpenAlex stores abstracts as an inverted index {word: [positions]}."""
    if not inv:
        return ""
    pos = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos))

# IPython display() is referenced by some path/vis helpers.
try:
    from IPython.display import display as _display
except Exception:  # pragma: no cover
    def _display(*_a, **_k):
        pass

_INJECT = [
    "find_path",
    "find_shortest_path_with2hops",
    "print_path_with_edges_as_list",
    "heuristic_path_with_embeddings",
    "heuristic_path_with_embeddings_with_randomization_waypoints",
    "find_best_fitting_node",
    "find_best_fitting_node_list",
]


def apply() -> list:
    """Inject missing GraphReasoning names into HypothesisGeneration.utils. Returns the names patched."""
    patched = []
    for name in _INJECT:
        if hasattr(_utils, name):
            continue
        fn = getattr(_ga, name, None) or getattr(_gt, name, None)
        if fn is not None:
            setattr(_utils, name, fn)
            patched.append(name)
    if not hasattr(_utils, "display"):
        setattr(_utils, "display", _display)
        patched.append("display")

    # Override the Semantic Scholar tool with the OpenAlex version everywhere it's
    # bound. pipeline_graph imports the name at load time, so patch it there too.
    _utils.semanticscholar_search = semanticscholar_search
    try:
        import HypothesisGeneration.hypothesis_generation_pipeline_graph as _pg
        _pg.semanticscholar_search = semanticscholar_search
        patched.append("semanticscholar_search->openalex")
    except Exception:
        pass
    try:
        import HypothesisGeneration.agents.novelty_critic_agent as _nc
        _nc.semanticscholar_search = semanticscholar_search
    except Exception:
        pass

    # Set the report generator's active domain (drives the validation-plan TCGA
    # cohort). Defaults to prostate; can be overridden via the SAGE_DOMAIN env var.
    try:
        import HypothesisGeneration.report_generator as _rg
        _rg.ACTIVE_DOMAIN = os.environ.get("SAGE_DOMAIN", "prostate cancer")
        patched.append(f"report.ACTIVE_DOMAIN={_rg.ACTIVE_DOMAIN}")
    except Exception:
        pass

    return patched


_PATCHED = apply()
