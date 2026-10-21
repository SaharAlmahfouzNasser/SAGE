"""
Focused SAGE graph-path discovery script.

This runs only the first path-generation/ranking step from the downstream
pipeline. It does not invoke the ontologist, scientist, novelty critic, report
generation, or any LLM agent.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", "/private/tmp/matplotlib")

ROOT = Path(__file__).resolve().parent
for package_dir in (ROOT / "HypothesisGeneration-pkg", ROOT / "GraphReasoning"):
    package_dir_str = str(package_dir)
    if package_dir.exists() and package_dir_str not in sys.path:
        sys.path.insert(0, package_dir_str)

import networkx as nx
from transformers import AutoModel, AutoTokenizer

import patch_pathfinding  # noqa: F401

from GraphReasoning.graph_analysis import return_giant_component_of_graph
from GraphReasoning.graph_tools import (
    generate_node_embeddings,
    load_embeddings,
    load_graph_with_text_as_JSON,
    save_embeddings,
)
from GraphReasoning.utils import make_dir_if_needed
from HypothesisGeneration.path_evaluation import (
    compute_weighted_total_score,
    evaluate_paths_logic,
    evaluate_paths_novelty,
    evaluate_paths_relevance,
    evaluate_paths_surprise,
    sort_paths_by_score,
)
from HypothesisGeneration.utils import (
    CATEGORY_WORDS,
    INVALID_LABELS,
    find_paths_to_label,
)


EMBED_MODEL = "BAAI/bge-large-en-v1.5"

TARGET_TYPE_GROUPS = {
    "morphology": [
        "Phenotype", "Measurement", "Feature", "Characteristic", "Method",
        "Test", "ImagingTechnique", "Imaging", "Process", "Tissue",
    ],
    "biomarker": ["Biomarker", "Measurement", "Marker", "Phenotype", "Feature"],
    "treatment": ["Treatment", "Therapy", "Drug", "Compound"],
}

QUERY_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does",
    "find", "for", "from", "how", "in", "is", "novel", "of", "or", "that",
    "the", "to", "what", "which", "with",
}

GENERIC_ENDPOINT_TERMS = {
    "analysis", "assay", "data", "dataset", "finding", "findings", "group",
    "groups", "method", "methods", "model", "models", "patient", "patients",
    "result", "results", "scan", "scans", "study", "test", "tests", "trial",
}

MORPHOLOGY_QUERY_TERMS = {
    "morpholog", "histolog", "histopath", "patholog", "gleason", "gland",
    "cribriform", "ductal", "intraductal", "architecture", "stroma",
    "stromal", "nuclear", "image", "imaging", "radiomic",
}

MORPHOLOGY_PATH_TERMS = {
    "biopsy", "cell size", "cribriform", "ductal", "epithelium", "gleason",
    "gland", "grade", "h&e", "histology", "histopathology", "image",
    "imaging", "index lesion", "intraductal", "lesion", "morphology",
    "mpmri", "mri", "nuclear", "pathology", "radiomic", "stroma",
    "stromal", "tissue",
}

SUSPICIOUS_SOURCE_TYPES_BY_RELATION = {
    "biomarker_for": {"Cancer", "CancerType", "Disease", "Condition", "Tumor"},
    "treats": {"Cancer", "CancerType", "Disease", "Condition", "Tumor", "Outcome"},
    "targets": {"Cancer", "CancerType", "Disease", "Condition", "Tumor"},
    "measured_by": {"Drug", "Treatment", "Therapy"},
}


def slugify(value: str, fallback: str = "path_discovery") -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or fallback


def infer_target_type(query: str) -> str:
    q = query.lower()
    rules = [
        ((
            "morpholog", "histolog", "histopath", "patholog", "gleason",
            "gland", "cribriform", "ductal", "intraductal", "architecture",
            "stroma", "stromal", "nuclear", "image", "radiomic",
        ), "Morphology"),
        (("biomarker", "marker", "measurement", "measure", "readout"), "Biomarker"),
        (("gene", "genes", "genomic", "mutation", "mutations"), "Gene"),
        (("protein", "proteins", "receptor", "receptors"), "Protein"),
        (("drug", "drugs", "compound", "compounds"), "Drug"),
        (("therapy", "therapies", "treatment", "treatments"), "Treatment"),
        (("pathway", "pathways", "process", "processes"), "Pathway"),
        (("disease", "diseases", "cancer", "cancers"), "Disease"),
        (("cell", "cells", "cell type", "cell types"), "CellType"),
        (("metabolite", "metabolites", "metabolomic", "metabolomics"), "Metabolite"),
        (("outcome", "outcomes", "survival", "recurrence", "prognosis", "prognostic"), "Outcome"),
    ]
    for needles, target_type in rules:
        if any(needle in q for needle in needles):
            return target_type
    return ""


def target_type_group_members(name: str) -> list[str]:
    return TARGET_TYPE_GROUPS.get(name.strip().lower(), [])


def tokenize_meaningful(text: str) -> set[str]:
    tokens = set(re.findall(r"[a-z0-9][a-z0-9+\-]{2,}", text.lower()))
    return {token for token in tokens if token not in QUERY_STOPWORDS}


def query_terms_for_scoring(query: str) -> set[str]:
    terms = tokenize_meaningful(query)
    if any(term in query.lower() for term in MORPHOLOGY_QUERY_TERMS):
        for phrase in MORPHOLOGY_PATH_TERMS:
            terms.update(tokenize_meaningful(phrase))
    return terms


def cancer_phrases(text: str) -> set[str]:
    return {
        phrase.strip().lower()
        for phrase in re.findall(r"\b[a-z0-9+\- ]{2,40}\bcancers?\b", text.lower())
    }


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def infer_source_entity(query: str) -> str:
    q = " ".join(query.strip().split())
    lower = q.lower()

    known_entities = [
        "prostate cancer", "metastatic castration-resistant prostate cancer",
        "mcrpc", "castration-resistant prostate cancer", "localized prostate cancer",
        "breast cancer", "bladder cancer",
    ]
    for entity in known_entities:
        if entity in lower:
            return entity

    patterns = [
        r"\b(?:for|in|with|among|against)\s+([A-Za-z0-9][A-Za-z0-9+\- /()]*?(?:cancer|tumou?r|disease|syndrome|therapy|treatment))\b",
        r"\bbetween\s+([A-Za-z0-9][A-Za-z0-9+\- /()]*?)\s+(?:and|to)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, q, flags=re.IGNORECASE)
        if match:
            entity = match.group(1).strip(" ?.,;:")
            entity = re.sub(r"\b(?:patients|patient|men|women)$", "", entity, flags=re.IGNORECASE).strip()
            if entity:
                return entity

    return ""


def default_output_paths(args: argparse.Namespace) -> tuple[Path | None, Path | None]:
    out_path = Path(args.out) if args.out else None
    json_path = Path(args.json_out) if args.json_out else None

    if out_path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_name = args.run_name or f"{slugify(args.source)}_to_{slugify(args.target_type, 'inferred_target')}"
        out_path = Path(args.out_dir) / f"{run_name}_{timestamp}.txt"

    if json_path is None and not args.no_json:
        json_path = out_path.with_suffix(".json")

    return out_path, json_path


def to_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    if hasattr(value, "tolist"):
        try:
            return value.tolist()
        except (TypeError, ValueError):
            pass
    if isinstance(value, Path):
        return str(value)
    return value


def build_target_type_aliases(available_types: set[str]) -> dict[str, str]:
    aliases: dict[str, str] = {}

    def add(alias: str, canonical: str) -> None:
        if canonical in available_types:
            aliases[alias.lower()] = canonical

    add("biomarker", "Measurement")
    add("biomarkers", "Measurement")
    add("marker", "Measurement")
    add("markers", "Measurement")
    add("therapy", "Treatment")
    add("therapies", "Treatment")
    add("cancer", "Disease")
    add("cancers", "Disease")
    add("organ", "Tissue")
    add("organs", "Tissue")
    return aliases


def resolve_target_type(target_type: str, available_types: set[str]) -> tuple[str, str | None]:
    raw = (target_type or "").strip()
    if not raw:
        return raw, None

    if raw in available_types:
        return raw, None

    lower_to_type = {t.lower(): t for t in available_types}
    if raw.lower() in lower_to_type:
        resolved = lower_to_type[raw.lower()]
        if resolved != raw:
            return resolved, f"Normalized target_type '{raw}' -> '{resolved}'"
        return resolved, None

    aliases = build_target_type_aliases(available_types)
    if raw.lower() in aliases:
        resolved = aliases[raw.lower()]
        return resolved, f"Mapped target_type alias '{raw}' -> '{resolved}'"

    return raw, None


def resolve_target_types(
    target_type_spec: str,
    available_types: set[str],
    *,
    expand_groups_first: bool = False,
) -> tuple[list[str], list[str]]:
    requested = [part.strip() for part in target_type_spec.split(",") if part.strip()]
    resolved_types: list[str] = []
    notes: list[str] = []

    for item in requested:
        group = target_type_group_members(item) if expand_groups_first else []
        if group:
            notes.append(f"Expanded target_type group '{item}' -> {', '.join(group)}")
            candidates = group
        else:
            resolved, note = resolve_target_type(item, available_types)
            if note:
                notes.append(note)
            if resolved in available_types:
                candidates = [resolved]
            else:
                group = target_type_group_members(item)
                if group:
                    notes.append(f"Expanded target_type group '{item}' -> {', '.join(group)}")
                    candidates = group
                else:
                    candidates = [item]

        for candidate in candidates:
            resolved, note = resolve_target_type(candidate, available_types)
            if note:
                notes.append(note)
            if resolved in available_types and resolved not in resolved_types:
                resolved_types.append(resolved)

    return resolved_types, notes


def available_node_types(G: nx.Graph) -> set[str]:
    return {
        str(data.get("type", "")).strip()
        for _, data in G.nodes(data=True)
        if str(data.get("type", "")).strip()
    }


def embedding_cache_candidates(graph_path: Path) -> list[Path]:
    filename = f"embeddings-{graph_path.stem}-bge_large_en_v1.5.pkl"
    candidates = [graph_path.parent / filename]

    repo_outputs = Path("outputs") / "sage_kg" / filename
    candidates.append(repo_outputs)

    if graph_path.stem.endswith("_latest"):
        non_latest = graph_path.stem.removesuffix("_latest")
        candidates.append(graph_path.parent / f"embeddings-{non_latest}-bge_large_en_v1.5.pkl")
        candidates.append(Path("outputs") / "sage_kg" / f"embeddings-{non_latest}-bge_large_en_v1.5.pkl")

    unique = []
    seen = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved not in seen:
            unique.append(path)
            seen.add(resolved)
    return unique


def compatible_embedding_cache_candidates(graph_path: Path) -> list[Path]:
    candidates: list[Path] = []
    for directory in [graph_path.parent, Path("outputs") / "sage_kg"]:
        if directory.exists():
            candidates.extend(sorted(directory.glob("embeddings-*-bge_large_en_v1.5.pkl")))

    exact = {p.resolve() for p in embedding_cache_candidates(graph_path)}
    unique = []
    seen = set(exact)
    for path in candidates:
        resolved = path.resolve()
        if resolved not in seen:
            unique.append(path)
            seen.add(resolved)
    return unique


def embedding_coverage(G: nx.Graph, node_embeddings: dict) -> float:
    graph_nodes = {str(node) for node in G.nodes()}
    if not graph_nodes:
        return 0.0
    embedding_nodes = {str(node) for node in node_embeddings.keys()}
    return len(graph_nodes & embedding_nodes) / len(graph_nodes)


def load_graph_and_embeddings(graph_path: Path, regenerate_embeddings: bool = False) -> tuple[Any, Any, nx.Graph, nx.Graph, dict]:
    data_dir = graph_path.parent
    graph_name = graph_path.name

    print(f"[1/4] Loading embedding model {EMBED_MODEL} ...")
    embedding_tokenizer = AutoTokenizer.from_pretrained(EMBED_MODEL)
    embedding_model = AutoModel.from_pretrained(EMBED_MODEL)

    print(f"[2/4] Loading graph {graph_path} ...")
    directed_G = load_graph_with_text_as_JSON(data_dir=str(data_dir), graph_name=graph_name)
    directed_G = return_giant_component_of_graph(directed_G)
    G = nx.Graph(directed_G)
    print(f"      giant component: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

    embed_pkl = data_dir / f"embeddings-{graph_path.stem}-bge_large_en_v1.5.pkl"
    if not regenerate_embeddings:
        errors = []
        for candidate in embedding_cache_candidates(graph_path):
            try:
                node_embeddings = load_embeddings(str(candidate))
                print(f"[3/4] Loaded cached node embeddings: {candidate}")
                return embedding_tokenizer, embedding_model, directed_G, G, node_embeddings
            except Exception as exc:
                errors.append(f"{candidate}: {exc}")

        for candidate in compatible_embedding_cache_candidates(graph_path):
            try:
                node_embeddings = load_embeddings(str(candidate))
                coverage = embedding_coverage(G, node_embeddings)
                if coverage >= 0.90:
                    print(
                        f"[3/4] Loaded compatible node embeddings: {candidate} "
                        f"({coverage:.1%} node coverage)"
                    )
                    return embedding_tokenizer, embedding_model, directed_G, G, node_embeddings
                errors.append(f"{candidate}: only {coverage:.1%} node coverage")
            except Exception as exc:
                errors.append(f"{candidate}: {exc}")
        print("[3/4] Could not load cached embeddings from known locations; regenerating ...")

    print("[3/4] Generating node embeddings ...")
    node_embeddings = generate_node_embeddings(G, embedding_tokenizer, embedding_model)
    save_embeddings(node_embeddings, str(embed_pkl))
    print(f"      saved -> {embed_pkl}")
    return embedding_tokenizer, embedding_model, directed_G, G, node_embeddings


def _edge_label(data: dict | None) -> str:
    if not data:
        return "related_to"
    return str(data.get("title") or data.get("rnorm") or data.get("rtext") or "related_to")


def format_path_direction_aware(path_list: list, directed_G: nx.Graph) -> tuple[str, list[dict]]:
    nodes = [str(x) for x in path_list[::2] if str(x).strip()]
    if len(nodes) < 2:
        return " -- ".join(nodes), []

    parts = [nodes[0]]
    hops = []
    for left, right in zip(nodes, nodes[1:]):
        if directed_G.has_edge(left, right):
            data = directed_G.get_edge_data(left, right)
            label = _edge_label(data)
            parts.append(f"-- {label} ->")
            hops.append({"from": left, "to": right, "relation": label, "traversal": "forward"})
        elif directed_G.has_edge(right, left):
            data = directed_G.get_edge_data(right, left)
            label = _edge_label(data)
            parts.append(f"<- {label} --")
            hops.append({"from": right, "to": left, "relation": label, "traversal": "reverse"})
        else:
            label = "related_to"
            parts.append(f"-- {label} --")
            hops.append({"from": left, "to": right, "relation": label, "traversal": "unknown"})
        parts.append(right)

    return " ".join(parts), hops


def direction_penalty(path: dict, G: nx.Graph) -> float:
    penalty = 0.0
    for hop in path.get("path_hops", []):
        relation = str(hop.get("relation", "")).lower()
        suspicious_source_types = SUSPICIOUS_SOURCE_TYPES_BY_RELATION.get(relation)
        if not suspicious_source_types:
            continue
        source_type = str(G.nodes.get(hop.get("from"), {}).get("type", ""))
        if source_type in suspicious_source_types:
            penalty += 0.12
    return min(penalty, 0.30)


def generic_endpoint_penalty(path: dict) -> float:
    target = str(path.get("target", "")).lower()
    target_tokens = tokenize_meaningful(target)
    if target_tokens and target_tokens <= GENERIC_ENDPOINT_TERMS:
        return 0.18
    if len(target_tokens) == 1 and next(iter(target_tokens), "") in GENERIC_ENDPOINT_TERMS:
        return 0.14
    return 0.0


def off_domain_penalty(path: dict, source: str, domain: str) -> float:
    text = f"{path.get('target', '')} {path.get('path_string', '')}"
    phrases = cancer_phrases(text)
    allowed = cancer_phrases(f"{source} {domain}") | {source.lower(), domain.lower()}
    off_domain = {
        phrase for phrase in phrases
        if phrase not in allowed
        and not any(allowed_phrase in phrase for allowed_phrase in allowed)
    }
    return min(0.10 * len(off_domain), 0.25)


def query_match_bonus(path: dict, query: str) -> float:
    terms = query_terms_for_scoring(query)
    if not terms:
        return 0.0
    text = f"{path.get('target', '')} {path.get('path_string', '')}".lower()
    matches = sum(1 for term in terms if term in text)
    return min(matches * 0.025, 0.15)


def add_novel_path_scores(paths: list[dict], *, G: nx.Graph, source: str, domain: str, query: str) -> list[dict]:
    for path in paths:
        bonuses = {
            "query_match": query_match_bonus(path, query),
        }
        penalties = {
            "generic_endpoint": generic_endpoint_penalty(path),
            "off_domain": off_domain_penalty(path, source, domain),
            "direction": direction_penalty(path, G),
        }
        score = (
            0.55 * path.get("novelty_score", 0.0)
            + 0.25 * path.get("relevance_score", 0.0)
            + 0.10 * path.get("logic_score", 0.0)
            + 0.10 * path.get("surprise_score", 0.0)
            + sum(bonuses.values())
            - sum(penalties.values())
        )
        path["novel_path_score"] = clamp01(score)
        path["novel_path_bonuses"] = bonuses
        path["novel_path_penalties"] = penalties
    return paths


RANK_BY_CONFIG = {
    "total": {
        "score_key": "weighted_total_score",
        "label": "weighted total score",
        "description": "weighted total score",
    },
    "novel": {
        "score_key": "novel_path_score",
        "label": "NovelPath score",
        "description": "NovelPath score = novelty-heavy score with query-match bonuses and generic/off-domain/direction penalties",
    },
    "novelty": {
        "score_key": "novelty_score",
        "label": "raw novelty score",
        "description": "raw novelty score only",
    },
}


def evaluate_and_rank_paths(
    *,
    directed_G: nx.Graph,
    G: nx.Graph,
    embedding_tokenizer: Any,
    embedding_model: Any,
    node_embeddings: dict,
    source: str,
    target_types: list[str],
    query: str,
    domain: str,
    max_targets: int,
    top_n: int,
    weights: dict[str, float],
    skip_logic: bool,
    rank_by: str,
) -> tuple[list[dict], dict]:
    all_items = []
    summary = {
        "target_types": target_types,
        "per_target_type": {},
        "total_found": 0,
        "paths_calculated": 0,
        "successful_paths": 0,
        "failed_paths": 0,
    }

    for target_type in target_types:
        paths_dict, type_summary = find_paths_to_label(
            G,
            embedding_tokenizer,
            embedding_model,
            node_embeddings,
            source_keyword=source,
            target_label=target_type,
            max_targets=max_targets,
            shortest_path=True,
            verbatim=True,
            validate_nodes=False,
        )
        summary["per_target_type"][target_type] = type_summary
        summary["total_found"] += type_summary.get("total_found", type_summary.get("total_targets", 0))
        summary["paths_calculated"] += type_summary.get("paths_calculated", 0)
        summary["successful_paths"] += type_summary.get("successful_paths", len(paths_dict))
        summary["failed_paths"] += type_summary.get("failed_paths", 0)

        for target, info in paths_dict.items():
            all_items.append((target_type, target, info))

    if not all_items:
        return [], summary

    sorted_items = sorted(all_items, key=lambda item: item[2]["path_length"])
    paths = []
    seen = set()
    for target_type, target, info in sorted_items:
        directed_path_string, path_hops = format_path_direction_aware(info["path_list"], directed_G)
        key = (target, directed_path_string)
        if key in seen:
            continue
        seen.add(key)
        paths.append({
            "number": len(paths) + 1,
            "target": target,
            "target_type": target_type,
            "length": info["path_length"],
            "path_string": directed_path_string,
            "path_string_legacy": info["path_string"],
            "path_hops": path_hops,
        })

    if skip_logic:
        for path in paths:
            path["logic_score"] = 0.0
            path["logic_passed"] = None
    else:
        paths, logic_stats = evaluate_paths_logic(
            user_query=query,
            paths=paths,
            model_name="all-MiniLM-L6-v2",
        )
        summary["logic_stats"] = logic_stats

    # --- Bug fix: path_string format mismatch -----------------------------
    # evaluate_paths_relevance / evaluate_paths_novelty / evaluate_paths_
    # surprise (from HypothesisGeneration.path_evaluation) parse
    # path['path_string'] by splitting on the literal ' -- ' delimiter —
    # the same plain format the real hypothesis pipeline produces (see
    # HypothesisGeneration.utils' " -- ".join(...) path building).
    # format_path_direction_aware() above instead renders directional
    # arrows ('-- relation ->' / '<- relation --') for nicer CLI display,
    # which breaks that parser: it silently drops entities and pollutes
    # others with leftover relation text, degrading novelty_score to ~0
    # and relevance_score partially, for every path.
    #
    # Fix: temporarily feed these three evaluators the plain-format
    # path_string_legacy (already computed by find_paths_to_label, in the
    # same format the real pipeline uses) instead of the arrow-formatted
    # display string, then restore the display string afterward so
    # printed/saved output is unaffected. Logic scoring is untouched here
    # deliberately — it embeds the whole string via Sentence-BERT rather
    # than parsing discrete entities, so it isn't subject to this bug.
    for path in paths:
        path["_path_string_display"] = path["path_string"]
        path["path_string"] = path.get("path_string_legacy", path["path_string"])

    paths = evaluate_paths_relevance(
        paths=paths,
        target_domain=domain,
        node_embeddings=node_embeddings,
        embedding_tokenizer=embedding_tokenizer,
        embedding_model=embedding_model,
    )
    paths = evaluate_paths_novelty(paths=paths, G=G, node_embeddings=node_embeddings)
    paths = evaluate_paths_surprise(paths=paths, G=G)

    for path in paths:
        path["path_string"] = path.pop("_path_string_display")
    # --- End bug fix --------------------------------------------------------

    paths = compute_weighted_total_score(paths=paths, weights=weights)
    paths = add_novel_path_scores(paths, G=G, source=source, domain=domain, query=query)
    score_key = RANK_BY_CONFIG[rank_by]["score_key"]
    paths = sort_paths_by_score(paths=paths, score_key=score_key, reverse=True)

    for i, path in enumerate(paths, 1):
        path["rank"] = i

    return paths, summary


def format_ranked_paths(
    *,
    paths: list[dict],
    total_found: int,
    source: str,
    target_type: str,
    query: str,
    domain: str,
    weights: dict[str, float],
    rank_by: str,
    resolve_note: str | None,
) -> str:
    lines: list[str] = []
    if resolve_note:
        lines.append(f"Info: {resolve_note}")
        lines.append("")

    weight_str = " + ".join(f"{name.capitalize()}({weight})" for name, weight in weights.items())
    lines.append(f"Query: {query}")
    lines.append(f"Domain: {domain}")
    lines.append(f"Found {total_found} paths from '{source}' to '{target_type}'")
    lines.append(f"Evaluated and ranked by: {weight_str}")
    rank_config = RANK_BY_CONFIG[rank_by]
    lines.append(f"Final sort: {rank_config['description']}")
    lines.append("")
    lines.append(f"Top {len(paths)} Paths (sorted by {rank_config['label']}):")
    lines.append("")

    for path in paths:
        sorted_score = path.get(rank_config["score_key"], 0.0)
        score_text = f"SortedScore({rank_by}): {sorted_score:.3f}"
        if rank_by != "total":
            score_text += f" | Total: {path.get('weighted_total_score', 0.0):.3f}"
        if rank_by != "novelty":
            score_text += f" | Novelty: {path.get('novelty_score', 0.0):.3f}"
        if rank_by != "novel":
            score_text += f" | NovelPath: {path.get('novel_path_score', 0.0):.3f}"
        lines.append(
            f"{path['rank']}. Path to '{path['target']}' [{path.get('target_type', target_type)}] "
            f"(length: {path['length']}) {score_text}"
        )
        lines.append(f"   {path['path_string']}")
        lines.append(
            f"   Logic: {path.get('logic_score', 0.0):.3f} | "
            f"Relevance: {path.get('relevance_score', 0.0):.3f} | "
            f"Novelty: {path.get('novelty_score', 0.0):.3f} | "
            f"Surprise: {path.get('surprise_score', 0.0):.3f}"
        )
        if rank_by == "novel":
            penalties = path.get("novel_path_penalties", {})
            bonuses = path.get("novel_path_bonuses", {})
            lines.append(
                f"   QueryBonus: {bonuses.get('query_match', 0.0):.3f} | "
                f"Penalties: generic {penalties.get('generic_endpoint', 0.0):.3f}, "
                f"off-domain {penalties.get('off_domain', 0.0):.3f}, "
                f"direction {penalties.get('direction', 0.0):.3f}"
            )
        lines.append("")

    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", default="outputs/sage_kg/prostate_cancer_kg.graphml")
    ap.add_argument("--source", default="", help="specific source entity; blank infers from --query")
    ap.add_argument("--target-type", default="", help="target node type or alias; blank infers from --query")
    ap.add_argument(
        "--query",
        default="Find underexplored prognostic biomarkers in prostate cancer.",
        help="research question used for logic scoring",
    )
    ap.add_argument("--domain", default="prostate cancer", help="domain used for relevance scoring")
    ap.add_argument("--max-targets", type=int, default=30, help="number of target nodes sampled for path search")
    ap.add_argument("--top-n", type=int, default=10, help="number of ranked paths to print/save")
    ap.add_argument("--seed", type=int, default=7, help="random seed for target-node sampling")
    ap.add_argument("--logic-weight", type=float, default=0.2)
    ap.add_argument("--relevance-weight", type=float, default=0.2)
    ap.add_argument("--novelty-weight", type=float, default=0.3)
    ap.add_argument("--surprise-weight", type=float, default=0.3)
    ap.add_argument(
        "--rank-by",
        choices=["total", "novel", "novelty"],
        default="total",
        help="final sort key: weighted total score, NovelPath quality score, or raw novelty score",
    )
    ap.add_argument(
        "--skip-logic",
        action="store_true",
        help="skip Sentence-BERT logic scoring; useful for faster novelty/surprise sweeps",
    )
    ap.add_argument(
        "--regenerate-embeddings",
        action="store_true",
        help="ignore cached node embeddings and recompute them",
    )
    ap.add_argument("--out-dir", default="path_discovery", help="default directory for timestamped outputs")
    ap.add_argument("--run-name", default="", help="optional base name for timestamped default outputs")
    ap.add_argument("--out", default="", help="text output file; defaults to path_discovery/<run>_<timestamp>.txt")
    ap.add_argument("--json-out", default="", help="JSON output file with scores; defaults to same basename as --out")
    ap.add_argument("--no-json", action="store_true", help="do not write the default JSON sidecar")
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    random.seed(args.seed)

    source = args.source.strip()
    source_note = None
    if not source:
        source = infer_source_entity(args.query)
        if source:
            source_note = f"Inferred source_entity from query -> '{source}'"
        else:
            raise SystemExit(
                "Could not infer source entity from --query. Provide --source "
                '(for example "prostate cancer", "AR-V7", "PTEN loss").'
            )

    if source.lower() in CATEGORY_WORDS:
        raise SystemExit(f"'{source}' looks like a category. Use a specific source entity.")
    args.source = source

    inferred_note = source_note
    target_type_inferred = False
    if not args.target_type.strip():
        args.target_type = infer_target_type(args.query)
        target_type_inferred = True
        if args.target_type:
            target_note = f"Inferred target_type from query -> '{args.target_type}'"
            inferred_note = f"{inferred_note}; {target_note}" if inferred_note else target_note
        else:
            raise SystemExit(
                "Could not infer target_type from --query. Provide --target-type "
                "(for example Biomarker, Gene, Drug, Treatment, Pathway)."
            )
    else:
        query_target_type = infer_target_type(args.query)
        if query_target_type and query_target_type.lower() != args.target_type.strip().lower():
            note = (
                f"Explicit target_type '{args.target_type}' overrides query-inferred "
                f"target_type '{query_target_type}'"
            )
            inferred_note = f"{inferred_note}; {note}" if inferred_note else note

    if args.target_type.lower() in INVALID_LABELS:
        raise SystemExit(f"'{args.target_type}' is not a valid graph target type.")

    out_path, json_path = default_output_paths(args)

    weights = {
        "logic": args.logic_weight,
        "relevance": args.relevance_weight,
        "novelty": args.novelty_weight,
        "surprise": args.surprise_weight,
    }
    if args.skip_logic and weights.get("logic", 0.0) != 0.0:
        print("Warning: --skip-logic is set; forcing logic weight to 0.0")
        weights["logic"] = 0.0
    if args.skip_logic and args.rank_by == "novelty":
        note = (
            "--rank-by novelty with --skip-logic sorts by graph-structural novelty; "
            "query text only affects inferred source/target when those are not explicit"
        )
        inferred_note = f"{inferred_note}; {note}" if inferred_note else note

    graph_path = Path(args.graph)
    embedding_tokenizer, embedding_model, directed_G, G, node_embeddings = load_graph_and_embeddings(
        graph_path=graph_path,
        regenerate_embeddings=args.regenerate_embeddings,
    )

    types = available_node_types(G)
    target_types, resolve_notes = resolve_target_types(
        args.target_type,
        types,
        expand_groups_first=target_type_inferred,
    )
    if inferred_note:
        resolve_notes.insert(0, inferred_note)
    resolve_note = "; ".join(resolve_notes) if resolve_notes else None

    if not target_types:
        valid = ", ".join(sorted(types))
        raise SystemExit(f"target_type '{args.target_type}' does not exist in this graph.\n\nAvailable types:\n{valid}")
    target_type_display = ", ".join(target_types)

    print(f"[4/4] Ranking paths source='{source}' target_type='{target_type_display}'")
    paths, summary = evaluate_and_rank_paths(
        directed_G=directed_G,
        G=G,
        embedding_tokenizer=embedding_tokenizer,
        embedding_model=embedding_model,
        node_embeddings=node_embeddings,
        source=source,
        target_types=target_types,
        query=args.query,
        domain=args.domain,
        max_targets=args.max_targets,
        top_n=args.top_n,
        weights=weights,
        skip_logic=args.skip_logic,
        rank_by=args.rank_by,
    )
    top_paths = paths[:args.top_n]

    if not top_paths:
        output = (
            f"No paths found from '{source}' to nodes of type '{target_type_display}'.\n"
            f"Total nodes found: {summary.get('total_found', summary.get('total_targets', 0))}"
        )
    else:
        output = format_ranked_paths(
            paths=top_paths,
            total_found=summary.get("successful_paths", len(paths)),
            source=source,
            target_type=target_type_display,
            query=args.query,
            domain=args.domain,
            weights=weights,
            rank_by=args.rank_by,
            resolve_note=resolve_note,
        )

    print()
    print(output)

    if out_path:
        make_dir_if_needed(out_path.parent)
        out_path.write_text(output, encoding="utf-8")
        print(f"\nSaved text output to: {out_path}")

    if json_path:
        make_dir_if_needed(json_path.parent)
        payload = {
            "query": args.query,
            "source": source,
            "target_type": target_type_display,
            "target_types": target_types,
            "domain": args.domain,
            "weights": weights,
            "rank_by": args.rank_by,
            "summary": summary,
            "paths": top_paths,
            "all_ranked_paths": paths,
        }
        json_path.write_text(json.dumps(to_jsonable(payload), indent=2), encoding="utf-8")
        print(f"Saved JSON output to: {json_path}")


if __name__ == "__main__":
    main()