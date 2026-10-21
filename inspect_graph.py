"""
inspect_graph.py

Loads a .graphml knowledge graph file and checks:
  1. Basic stats (node/edge counts, node type distribution) -- sanity check
     against the numbers reported in the paper/rebuttal.
  2. Whether specific suspicious node names appear at all (e.g. the
     Alzheimer's-related terms that showed up in the N0164 output: ACHE,
     VILIP-1 / Visinin-like protein-1, Donepezil, Tacrine, FINGER_trial).
  3. A broader automatic scan for likely off-topic / non-oncology nodes,
     using a keyword blocklist -- this is a heuristic, not authoritative,
     so review the printed list yourself rather than trusting counts alone.

Requires networkx (pip install networkx --break-system-packages if missing).
Falls back to raw XML parsing if networkx can't read the file (e.g. due to
nonstandard attribute typing).

USAGE:
    python inspect_graph.py --graph prostate_cancer_kg_latest.graphml
    python inspect_graph.py --graph prostate_cancer_kg_latest.graphml \
        --search ACHE VILIP-1 Donepezil Tacrine FINGER_trial
"""

import argparse
import sys
from collections import Counter

SUSPICIOUS_TERMS_DEFAULT = [
    "ACHE", "VILIP", "Donepezil", "Tacrine", "FINGER_trial", "FINGER trial",
    "Alzheimer", "acetylcholinesterase", "cholinergic", "cognitive decline",
    "ADAS-Cog", "CIBIC",
]

# Rough non-oncology disease-area keywords, for a broad contamination scan.
# Heuristic only -- false positives/negatives are expected, inspect the
# printed hits yourself before drawing conclusions.
OFF_TOPIC_BLOCKLIST = [
    "alzheimer", "parkinson", "dementia", "cholinergic", "acetylcholinesterase",
    "neurodegenerat", "epilepsy", "schizophrenia", "depression", "diabetes",
    "cardiovascular", "myocardial", "stroke", "asthma", "arthritis",
    "osteoporosis", "covid", "influenza", "hepatitis b", "hiv",
]


def load_with_networkx(path):
    import networkx as nx
    return nx.read_graphml(path)


def load_with_raw_xml(path):
    """Fallback: parse GraphML XML directly to get node id + data attributes,
    without relying on networkx's type coercion (which can fail on some
    custom-attribute GraphML files)."""
    import xml.etree.ElementTree as ET
    ns = {"g": "http://graphml.graphdrawing.org/xmlns"}
    tree = ET.parse(path)
    root = tree.getroot()

    # map key id -> attribute name
    key_map = {}
    for key in root.findall(".//g:key", ns):
        key_map[key.get("id")] = key.get("attr.name", key.get("id"))

    nodes = []
    for node in root.findall(".//g:node", ns):
        attrs = {"__id__": node.get("id")}
        for data in node.findall("g:data", ns):
            key = key_map.get(data.get("key"), data.get("key"))
            attrs[key] = data.text
        nodes.append(attrs)

    edges = []
    for edge in root.findall(".//g:edge", ns):
        edges.append((edge.get("source"), edge.get("target")))

    return nodes, edges


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", required=True, help="path to .graphml file")
    ap.add_argument("--search", nargs="*", default=SUSPICIOUS_TERMS_DEFAULT,
                     help="specific terms to search for verbatim (case-insensitive substring match)")
    ap.add_argument("--skip_blocklist_scan", action="store_true",
                     help="skip the broader off-topic keyword scan")
    args = ap.parse_args()

    print(f"Loading {args.graph} ...")
    try:
        import networkx as nx
        G = load_with_networkx(args.graph)
        node_items = list(G.nodes(data=True))
        n_nodes, n_edges = G.number_of_nodes(), G.number_of_edges()
        print(f"Loaded via networkx: {n_nodes} nodes, {n_edges} edges")
        get_name = lambda nid, data: str(data.get("name", data.get("label", nid)))
        get_type = lambda data: str(data.get("type", data.get("node_type", data.get("category", "UNKNOWN"))))
        all_text = lambda nid, data: " ".join(str(v) for v in [nid, *data.values()])
    except Exception as e:
        print(f"networkx failed to load ({e}); falling back to raw XML parsing.")
        nodes, edges = load_with_raw_xml(args.graph)
        n_nodes, n_edges = len(nodes), len(edges)
        print(f"Loaded via raw XML: {n_nodes} nodes, {n_edges} edges")
        node_items = [(n.get("__id__"), n) for n in nodes]
        get_name = lambda nid, data: str(data.get("name", data.get("label", nid)))
        get_type = lambda data: str(data.get("type", data.get("node_type", data.get("category", "UNKNOWN"))))
        all_text = lambda nid, data: " ".join(str(v) for v in data.values())

    # ---- node type distribution ----
    type_counts = Counter(get_type(data) for _, data in node_items)
    print(f"\nDistinct node types: {len(type_counts)}")
    print("Top 20 node types by count:")
    for t, c in type_counts.most_common(20):
        print(f"  {t}: {c}")

    # ---- targeted search for the specific suspicious terms ----
    print(f"\n{'='*70}\nTargeted search for specific terms: {args.search}\n{'='*70}")
    for term in args.search:
        hits = [(nid, get_name(nid, data), get_type(data)) for nid, data in node_items
                if term.lower() in all_text(nid, data).lower()]
        print(f"\n'{term}': {len(hits)} matching node(s)")
        for nid, name, typ in hits[:10]:
            print(f"    id={nid}  name={name}  type={typ}")
        if len(hits) > 10:
            print(f"    ... and {len(hits) - 10} more")

    # ---- broad off-topic keyword scan ----
    if not args.skip_blocklist_scan:
        print(f"\n{'='*70}\nBroad off-topic keyword scan (heuristic -- verify hits manually)\n{'='*70}")
        total_hits = 0
        for term in OFF_TOPIC_BLOCKLIST:
            hits = [(nid, get_name(nid, data)) for nid, data in node_items
                    if term.lower() in all_text(nid, data).lower()]
            if hits:
                total_hits += len(hits)
                print(f"\n'{term}': {len(hits)} matching node(s)")
                for nid, name in hits[:5]:
                    print(f"    id={nid}  name={name}")
                if len(hits) > 5:
                    print(f"    ... and {len(hits) - 5} more")
        if total_hits == 0:
            print("No hits from the off-topic blocklist. (Doesn't rule out contamination --")
            print("this list is short and heuristic; consider eyeballing a random node sample too.)")
        else:
            print(f"\nTOTAL potential off-topic node hits: {total_hits} out of {n_nodes} nodes "
                  f"({100*total_hits/n_nodes:.1f}%)")

    # ---- random sample for manual eyeballing ----
    print(f"\n{'='*70}\nRandom sample of 15 node names (for a quick manual sanity check)\n{'='*70}")
    import random
    sample = random.sample(node_items, min(15, len(node_items)))
    for nid, data in sample:
        print(f"  id={nid}  name={get_name(nid, data)}  type={get_type(data)}")


if __name__ == "__main__":
    main()