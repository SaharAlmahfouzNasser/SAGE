"""
SAGE-faithful knowledge graph construction.

Implements the KG pipeline from the SAGE paper (Section 3.1, Appendix A.1-A.2),
replacing the generic co-occurrence graph from GraphReasoning (Buehler/MIT, a
materials-science tool that lacks entity typing, confidence gating, relevance
filtering, and embedding-based fusion).

Pipeline:
  A. filter_papers      -- SapBERT relevance + citation proxy, drop ~40%
  B. extract_triples    -- GPT-4o-mini structured output, 8-field typed triples,
                           confidence gate c >= 0.5, ontology relation normalization
  C. build_local_graph  -- per-document directed graph Gd, edge weight = max conf
  D. fuse_graphs        -- BGE-Large entity resolution (cosine tau=0.9),
                           weight summation, prune components < 3 nodes, GraphML

All embedding models are loaded once, run on MPS when available, batch-encoded,
and cached to disk keyed by text hash.
"""

import hashlib
import json
import logging
import math
from pathlib import Path
from typing import Callable, Dict, List, Optional

import networkx as nx
import numpy as np

log = logging.getLogger("build_kg")

# =============================================================================
# Ontology schema (SAGE Appendix A.1: fine-grained entity types)
# =============================================================================

# Canonical analysis-time entity ontology used by downstream path-generation and
# find_related_entities tooling. Node `type` attributes should be normalized to
# this set so graph construction and analysis use a shared vocabulary.
ENTITY_TYPES = [
    # Core molecular biology
    "Gene", "Protein", "Enzyme", "Peptide", "AminoAcid",
    "DNA", "RNA", "LncRNA", "RNAi", "Transcript",
    "Nucleic", "NucleicAcid", "Molecule", "Antibody", "Antigen",
    # Cellular
    "Cell", "CellType", "CellLine", "CellCluster", "StemCell",
    "Neuron", "Exosome", "Nucleus",
    # Anatomical
    "Organ", "Tissue", "AnatomicalStructure", "Anatomy",
    "Brain", "Bone", "Muscle", "Nerve", "Artery", "Vein",
    "Vessel", "Lymph", "LymphNode", "Gland", "Duct",
    # Disease & pathology
    "Disease", "Cancer", "CancerType", "Tumor", "Syndrome",
    "Symptom", "Condition", "Complication", "Injury", "Anomaly",
    "Metastasis",
    # Pharmacology
    "Drug", "Compound", "Chemical", "Treatment", "Therapy",
    "Vaccine", "Nanoparticle",
    # Biochemistry
    "Pathway", "Process", "Metabolite", "Cytokine",
    "Hormone", "Neurotransmitter", "Biomarker", "Marker",
    "Receptor", "Signal",
    # Genetics
    "Genetic", "Genome", "Genomic", "Genotype", "Variant",
    "Mutation", "Chromosome", "Chromosomal", "Karyotype",
    "Plasmid", "Vector",
    # Clinical
    "Patient", "PatientGroup", "Cohort", "ClinicalTrial",
    "ClinicalFactor", "ClinicalVariable", "Outcome",
    "MedicalProcedure", "Surgery", "SurgicalProcedure",
    # Research & methods
    "Method", "Technique", "Assay", "Protocol", "Procedure",
    "Measurement", "Test", "Experiment", "Study",
    "ImagingTechnique", "Imaging",
    # Organisms
    "Organism", "Species", "Genus", "Phylum",
    "Bacteria", "Bacterium", "Virus", "Parasite",
    "Microorganism", "Microbiota", "Microbiome",
    # Medical factors
    "RiskFactor", "Risk", "RiskScore", "RiskCategory",
    "Phenotype", "Trait", "Characteristic", "Feature",
    "Grade", "Stage", "Subtype",
    # Legacy/catch-all currently observed in your graph outputs
    "Population", "Model", "Tool", "Database", "Dataset", "Other",
]
ENTITY_SET = set(ENTITY_TYPES)

# Backward-compatible aliases/coercions for entity types. This protects future
# KG builds if extraction prompts/models emit near-miss ontology labels.
_ENTITY_TYPE_ALIASES = {
    # analysis-time / user-facing category words -> canonical KG node types
    "therapy": "Therapy",
    "therapies": "Therapy",
    "biomarker": "Biomarker",
    "biomarkers": "Biomarker",
    "marker": "Marker",
    "markers": "Marker",
    "outcome": "Outcome",
    "outcomes": "Outcome",
    "cancer": "Cancer",
    "cancers": "Cancer",
    "tumour": "Tumor",
    "tumours": "Tumor",
    "behaviour": "Phenotype",
    # casing/plural near-misses
    "celltype": "CellType",
    "celltypes": "CellType",
    "cell line": "CellLine",
    "cell lines": "CellLine",
    "clinical trial": "ClinicalTrial",
    "clinical trials": "ClinicalTrial",
    "lncrna": "LncRNA",
    "rnai": "RNAi",
}


def normalize_entity_type(etype: str) -> str:
    """Coerce model/user emitted entity type to canonical KG ontology.

    Unknown values are mapped to "Other" to keep graph construction robust.
    """
    if not etype:
        return "Other"
    raw = str(etype).strip()
    if raw in ENTITY_SET:
        return raw

    key = raw.lower().strip().replace("-", " ").replace("_", " ")
    if key in _ENTITY_TYPE_ALIASES:
        mapped = _ENTITY_TYPE_ALIASES[key]
        return mapped if mapped in ENTITY_SET else "Other"

    # title-cased exact fallback (e.g., "gene" -> "Gene")
    title = raw[:1].upper() + raw[1:].lower() if raw else raw
    if title in ENTITY_SET:
        return title
    return "Other"

# Canonical normalized-relation vocabulary (rnorm). The LLM MUST map each
# surface relation (rtext) to exactly one of these during extraction, so the
# graph carries a small, consistent relation set instead of free-text phrases.
# Chosen to cover biomedical literature relations (SAGE A.1 "normalized form").
RELATION_TYPES = [
    "associated_with",     # generic statistical / observed association
    "causes",              # causal: A causes / leads to B
    "contributes_to",      # partial causal / driver
    "treats",              # therapeutic use
    "targets",             # drug/agent targets molecule
    "inhibits",            # decreases activity/function
    "activates",           # increases activity/function
    "upregulates",         # increases expression/level
    "downregulates",       # decreases expression/level
    "regulates",           # modulates (direction unspecified)
    "interacts_with",      # physical/functional interaction, binding
    "biomarker_for",       # measured indicator of a condition
    "predicts",            # predictive of outcome/response
    "prognostic_for",      # prognostic indicator
    "diagnoses",           # diagnostic test/marker for
    "expressed_in",        # entity expressed in tissue/cell
    "located_in",          # anatomical / subcellular location
    "part_of",             # compositional / hierarchical membership
    "subtype_of",          # is-a / classification
    "mutated_in",          # gene/variant altered in disease/sample
    "resistance_to",       # confers/associated with resistance
    "response_to",         # response/sensitivity to therapy
    "measured_by",         # quantified by an assay/metric/method
    "administered_with",   # co-administration / combination
    "evaluated_in",        # studied in trial/cohort/model
    "increases_risk_of",   # risk factor for
    "decreases_risk_of",   # protective factor for
    # diagnostic / imaging / staging relations (head = test/modality, tail = disease)
    "diagnosed_by",        # disease detected/diagnosed by a test/modality
    "imaged_by",           # disease/lesion visualized by an imaging modality
    "detects",             # test/modality detects a disease/lesion/marker
    "stages",              # modality/system used to stage disease
    "screens_for",         # test used to screen for disease
    "stratifies",          # feature/test stratifies risk/prognosis groups
    "other",               # relation outside this vocabulary
]
RELATION_SET = set(RELATION_TYPES)

# Light alias coercion: if the model returns a near-miss, snap it to canonical.
_RELATION_ALIASES = {
    "up-regulates": "upregulates", "up regulates": "upregulates",
    "increases expression of": "upregulates", "induces": "upregulates",
    "down-regulates": "downregulates", "down regulates": "downregulates",
    "decreases expression of": "downregulates", "suppresses": "downregulates",
    "blocks": "inhibits", "antagonizes": "inhibits",
    "agonizes": "activates", "stimulates": "activates",
    "correlates_with": "associated_with", "linked_to": "associated_with",
    "related_to": "associated_with", "is_associated_with": "associated_with",
    "drives": "contributes_to", "promotes": "contributes_to",
    "binds": "interacts_with", "interacts": "interacts_with",
    "is_a_biomarker_for": "biomarker_for", "biomarker_of": "biomarker_for",
    "mutation_in": "mutated_in", "altered_in": "mutated_in",
    "is_a": "subtype_of", "type_of": "subtype_of", "classified_as": "subtype_of",
    "diagnostic_for": "diagnoses",
}


def normalize_relation(rnorm: str) -> str:
    """Coerce a model-emitted rnorm to the canonical relation vocabulary.

    The schema already constrains rnorm to RELATION_TYPES; this is a safety net
    for alias/casing drift. Unknown values fall back to 'other'.
    """
    if not rnorm:
        return "other"
    key = rnorm.strip().lower().replace(" ", "_").replace("-", "_")
    if key in RELATION_SET:
        return key
    if key in _RELATION_ALIASES:
        return _RELATION_ALIASES[key]
    return "other"


# =============================================================================
# Embedding backend (SapBERT for relevance, BGE-Large for fusion)
# =============================================================================

class EmbeddingCache:
    """Disk-cached, batched sentence embeddings on the best available device."""

    def __init__(self, model_name: str, cache_dir: Path, device: Optional[str] = None,
                 pooling: str = "mean"):
        self.model_name = model_name
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.pooling = pooling
        self._device = device or self._best_device()
        self._model = None
        self._tokenizer = None  # only for SapBERT (transformers path)
        self._mem: Dict[str, np.ndarray] = {}

    @staticmethod
    def _best_device() -> str:
        import torch
        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
        return "cpu"

    def _cache_path(self, text: str) -> Path:
        h = hashlib.sha1((self.model_name + "::" + text).encode("utf-8")).hexdigest()
        return self.cache_dir / f"{h}.npy"

    def _load_sentence_transformer(self):
        from sentence_transformers import SentenceTransformer
        log.info("  [embed] loading %s on %s", self.model_name, self._device)
        self._model = SentenceTransformer(self.model_name, device=self._device)

    def _load_sapbert(self):
        """SapBERT uses CLS-token / mean pooling over a raw transformers model."""
        import torch
        from transformers import AutoModel, AutoTokenizer
        log.info("  [embed] loading %s on %s (transformers)", self.model_name, self._device)
        self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        self._model = AutoModel.from_pretrained(self.model_name).to(self._device)
        self._model.eval()

    def encode(self, texts: List[str], batch_size: int = 32, is_sapbert: bool = False) -> np.ndarray:
        """Return L2-normalized embeddings; hit disk/mem cache where possible."""
        out: List[Optional[np.ndarray]] = [None] * len(texts)
        todo_idx, todo_txt = [], []
        for i, t in enumerate(texts):
            t = t or ""
            if t in self._mem:
                out[i] = self._mem[t]
                continue
            cp = self._cache_path(t)
            if cp.exists():
                v = np.load(cp)
                self._mem[t] = v
                out[i] = v
            else:
                todo_idx.append(i)
                todo_txt.append(t)

        if todo_txt:
            if self._model is None:
                self._load_sapbert() if is_sapbert else self._load_sentence_transformer()
            vecs = self._embed_batch(todo_txt, batch_size, is_sapbert)
            for j, i in enumerate(todo_idx):
                v = vecs[j].astype(np.float32)
                out[i] = v
                self._mem[todo_txt[j]] = v
                np.save(self._cache_path(todo_txt[j]), v)

        return np.vstack(out)

    def _embed_batch(self, texts: List[str], batch_size: int, is_sapbert: bool) -> np.ndarray:
        if not is_sapbert:
            v = self._model.encode(texts, batch_size=batch_size, show_progress_bar=False,
                                    convert_to_numpy=True, normalize_embeddings=True)
            return v

        import torch
        all_vecs = []
        for s in range(0, len(texts), batch_size):
            batch = texts[s:s + batch_size]
            enc = self._tokenizer(batch, padding=True, truncation=True, max_length=256,
                                  return_tensors="pt").to(self._device)
            with torch.no_grad():
                rep = self._model(**enc)[0]  # last_hidden_state
            mask = enc["attention_mask"].unsqueeze(-1).float()
            summed = (rep * mask).sum(1)
            counts = mask.sum(1).clamp(min=1e-9)
            emb = (summed / counts)  # mean pooling
            emb = torch.nn.functional.normalize(emb, p=2, dim=1)
            all_vecs.append(emb.cpu().numpy())
        return np.vstack(all_vecs)


# =============================================================================
# Stage A -- Relevance filtering (SAGE 3.1, A.1)
# =============================================================================

SAPBERT_MODEL = "cambridgeltl/SapBERT-from-PubMedBERT-fulltext"
BGE_MODEL = "BAAI/bge-large-en-v1.5"

# Journal venue scoring (SAGE composite uses "journal impact factor").
# Score in [0,1] keyed by approximate impact tier / quartile of journals common
# in oncology + biomedical literature. Matched by case-insensitive substring on
# the journal title. Unknown journals get DEFAULT_VENUE_SCORE (neutral).
# Not exhaustive; extend as needed. (Ask the SAGE team for their exact IF table.)
DEFAULT_VENUE_SCORE = 0.5
VENUE_SCORES = {
    # Tier 1 — flagship / very high IF
    "ca-a cancer journal": 1.0, "new england journal of medicine": 1.0,
    "lancet": 0.97, "nature medicine": 0.97, "nature": 0.97, "cell": 0.97,
    "science": 0.95, "jama": 0.95, "nature reviews cancer": 0.95,
    "nature reviews clinical oncology": 0.95, "nature reviews urology": 0.9,
    "nature cancer": 0.92, "cancer cell": 0.92, "nature genetics": 0.92,
    "nature biotechnology": 0.92, "lancet oncology": 0.92,
    # Tier 2 — leading specialty
    "journal of clinical oncology": 0.88, "annals of oncology": 0.85,
    "jama oncology": 0.85, "cancer discovery": 0.88, "molecular cell": 0.85,
    "european urology": 0.85, "gut": 0.82, "blood": 0.82, "immunity": 0.85,
    "clinical cancer research": 0.78, "journal of urology": 0.75,
    "european urology oncology": 0.78, "the journal of urology": 0.75,
    "nature communications": 0.78, "pnas": 0.75,
    "proceedings of the national academy": 0.75,
    # Tier 3 — solid field journals
    "cancer research": 0.7, "oncogene": 0.68, "british journal of cancer": 0.65,
    "prostate cancer and prostatic diseases": 0.7, "the prostate": 0.62,
    "bju international": 0.62, "urologic oncology": 0.6, "cancers": 0.55,
    "molecular oncology": 0.6, "journal of experimental medicine": 0.8,
    "cell reports": 0.7, "elife": 0.7, "genome biology": 0.78,
    # Lower tier / megajournals
    "plos one": 0.4, "scientific reports": 0.42, "frontiers in oncology": 0.45,
    "frontiers in": 0.42, "international journal of molecular sciences": 0.42,
    "cureus": 0.3, "medicine": 0.4,
}


def venue_score(journal: str) -> float:
    """Map a journal title to a venue/impact score in [0,1] (substring match)."""
    if not journal:
        return DEFAULT_VENUE_SCORE
    j = journal.lower().strip()
    # exact-ish first, then substring
    if j in VENUE_SCORES:
        return VENUE_SCORES[j]
    best = None
    for key, sc in VENUE_SCORES.items():
        if key in j:
            best = sc if best is None else max(best, sc)
    return best if best is not None else DEFAULT_VENUE_SCORE


def filter_papers(papers: List[Dict], queries: List[Dict], cache_dir: Path,
                  drop_frac: float = 0.40,
                  weights: Optional[Dict[str, float]] = None) -> List[Dict]:
    """Score papers and drop the bottom `drop_frac` (SAGE relevance filter).

    Composite combines up to three SAGE terms, renormalized over whichever are
    available so missing signals don't penalize:
        composite = w_sap * sapbert_sim + w_venue * venue_score + w_cite * cite_score
    - sapbert_sim : always present (semantic relevance to the query set)
    - venue_score : journal IF/quartile lookup; neutral 0.5 if journal unknown
    - cite_score  : log-normalized citationCount (0 for all => term dropped,
                    e.g. when Semantic Scholar is disabled / unkeyed)
    """
    log.info("=" * 60)
    log.info("STAGE A: Relevance filtering (drop_frac=%.2f)", drop_frac)
    log.info("=" * 60)
    if not papers:
        return papers

    sap = EmbeddingCache(SAPBERT_MODEL, cache_dir / "sapbert", pooling="mean")

    # Query-set centroid: embed each query string, average, renormalize.
    qtexts = [q["query"] for q in queries]
    qvecs = sap.encode(qtexts, is_sapbert=True)
    qcentroid = qvecs.mean(axis=0)
    qnorm = np.linalg.norm(qcentroid)
    if qnorm > 0:
        qcentroid = qcentroid / qnorm

    # Paper embeddings from title + abstract (abstract always available).
    ptexts = [f"{p.get('title','')} {p.get('abstract','')}".strip() for p in papers]
    pvecs = sap.encode(ptexts, is_sapbert=True)
    sims = pvecs @ qcentroid  # cosine (both normalized) in [-1,1]
    sims = (sims + 1.0) / 2.0  # -> [0,1]

    # Venue / journal-IF term.
    venues = np.array([venue_score(p.get("journal", "")) for p in papers])
    venue_active = float(np.std(venues)) > 1e-6  # all-neutral => no signal

    # Citation term: log-normalized to [0,1].
    cites = np.array([float(p.get("citationCount", 0) or 0) for p in papers])
    logc = np.log1p(cites)
    cmax = logc.max() if logc.max() > 0 else 0.0
    cite_score = logc / cmax if cmax > 0 else np.zeros_like(logc)
    cite_active = cmax > 0  # all-zero (e.g. S2 disabled) => drop term

    # Default SAGE-style weights; renormalize over active terms only.
    w = weights or {"sapbert": 0.5, "venue": 0.25, "cite": 0.25}
    active = {"sapbert": w["sapbert"]}
    if venue_active:
        active["venue"] = w["venue"]
    if cite_active:
        active["cite"] = w["cite"]
    wsum = sum(active.values())
    active = {k: v / wsum for k, v in active.items()}

    composite = active["sapbert"] * sims
    if venue_active:
        composite = composite + active["venue"] * venues
    if cite_active:
        composite = composite + active["cite"] * cite_score

    for p, s, vv, cs, comp in zip(papers, sims, venues, cite_score, composite):
        p["sapbert_sim"] = float(s)
        p["venue_score"] = float(vv)
        p["cite_score"] = float(cs)
        p["relevance_score"] = float(comp)

    log.info("  Score terms active: sapbert=%.2f venue=%s cite=%s",
             active["sapbert"],
             f"{active.get('venue', 0):.2f}" if venue_active else "OFF(no journal data)",
             f"{active.get('cite', 0):.2f}" if cite_active else "OFF(all citations 0)")

    # Drop bottom drop_frac by composite.
    order = np.argsort(composite)  # ascending
    n_drop = int(round(len(papers) * drop_frac))
    drop_set = set(order[:n_drop].tolist())
    kept = [p for i, p in enumerate(papers) if i not in drop_set]

    log.info("  Papers in: %d  dropped: %d  kept: %d", len(papers), n_drop, len(kept))
    log.info("  Score range kept: [%.3f, %.3f]",
             min(p["relevance_score"] for p in kept) if kept else 0,
             max(p["relevance_score"] for p in kept) if kept else 0)
    return kept


# =============================================================================
# Stage B -- Typed triple extraction (SAGE 3.1, A.1)
# =============================================================================

TRIPLE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "biomedical_triples",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "triples": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "h": {"type": "string", "description": "head entity"},
                            "th": {"type": "string", "enum": ENTITY_TYPES},
                            "rtext": {"type": "string", "description": "surface relation phrase, as written"},
                            "rnorm": {"type": "string", "enum": RELATION_TYPES,
                                      "description": "normalized relation from the controlled vocabulary"},
                            "t": {"type": "string", "description": "tail entity"},
                            "tt": {"type": "string", "enum": ENTITY_TYPES},
                            "c": {"type": "number", "description": "confidence 0-1"},
                            "e": {"type": "string", "description": "evidence span from text"},
                        },
                        "required": ["h", "th", "rtext", "rnorm", "t", "tt", "c", "e"],
                    },
                }
            },
            "required": ["triples"],
        },
    },
}

EXTRACT_SYS_PROMPT = (
    "You are a biomedical knowledge extractor. From the given text chunk, extract "
    "factual biomedical triples (head entity, relation, tail entity). For each: assign "
    "the head and tail an entity type from the allowed ontology; give the surface relation "
    "phrase (rtext) as written; map that relation to exactly ONE normalized relation "
    "(rnorm) from the controlled vocabulary; a confidence score c in [0,1] reflecting how "
    "clearly the text supports the triple; and the exact supporting evidence span (e) "
    "copied verbatim from the text. Choose the single best-fitting rnorm; use 'other' only "
    "when no listed relation fits. Only extract relations explicitly stated or strongly "
    "implied. Do not invent facts. Prefer specific, disease-relevant biomedical relations.\n\n"
    "DIRECTION RULES (critical — head and tail are NOT interchangeable):\n"
    "- For treats/targets/inhibits/activates/upregulates/downregulates/regulates: the HEAD "
    "is the agent/cause (a Drug, Gene, Protein, Treatment, Pathway) and the TAIL is the "
    "target/effect. A Disease, Outcome, Symptom, or Measurement must NEVER be the head of "
    "these relations. Example: write (radiation therapy, treats, prostate cancer), NEVER "
    "(prostate cancer, treats, radiation therapy).\n"
    "- For diagnosed_by/imaged_by/screens_for/stratifies: HEAD is the disease/lesion, TAIL "
    "is the test/modality. For detects/stages: HEAD is the test/modality, TAIL is the "
    "disease/finding.\n"
    "- Do not extract vague, non-specific entities (e.g. 'trends', 'incidence', 'care', "
    "'different approaches', 'imaging techniques'). Entities must be concrete, nameable "
    "biomedical concepts."
)


def chunk_text(text: str, chunk_tokens: int = 1500, overlap: int = 200) -> List[str]:
    """Token-based overlapping chunks (SAGE: ~1500-token chunks)."""
    import tiktoken
    enc = tiktoken.get_encoding("cl100k_base")
    toks = enc.encode(text)
    if not toks:
        return []
    chunks, start = [], 0
    step = max(1, chunk_tokens - overlap)
    while start < len(toks):
        piece = toks[start:start + chunk_tokens]
        chunks.append(enc.decode(piece))
        start += step
    return chunks


def extract_triples(text: str, generate_structured: Callable, conf_threshold: float = 0.5,
                    chunk_tokens: int = 1500, overlap: int = 200) -> List[Dict]:
    """Extract typed triples from a document, gated at c >= conf_threshold.

    `generate_structured(system_prompt, user_prompt, schema)` must return parsed JSON
    matching TRIPLE_SCHEMA.
    """
    triples = []
    for chunk in chunk_text(text, chunk_tokens, overlap):
        try:
            result = generate_structured(EXTRACT_SYS_PROMPT, f"```{chunk}```", TRIPLE_SCHEMA)
        except Exception as e:
            log.warning("  [extract] chunk failed: %s", e)
            continue
        for tr in result.get("triples", []):
            c = float(tr.get("c", 0))
            if c < conf_threshold:
                continue
            h, t = (tr.get("h") or "").strip(), (tr.get("t") or "").strip()
            if not h or not t:
                continue
            tr["c"] = c
            # rnorm comes from the model (constrained to RELATION_TYPES); coerce
            # any alias/casing drift back to the canonical vocabulary.
            tr["rnorm"] = normalize_relation(tr.get("rnorm", ""))
            triples.append(tr)
    return triples


# =============================================================================
# Stage C -- Per-document local graph (SAGE A.2)
# =============================================================================

def build_local_graph(triples: List[Dict], doc_id: str) -> nx.DiGraph:
    """Build directed Gd. Nodes = unique entities (typed). Edge weight = max conf."""
    G = nx.DiGraph()
    for tr in triples:
        h, t = tr["h"].strip(), tr["t"].strip()
        for n, ntype in ((h, tr.get("th", "Other")), (t, tr.get("tt", "Other"))):
            ntype = normalize_entity_type(ntype)
            if not G.has_node(n):
                G.add_node(n, type=ntype, evidence=tr.get("e", ""))
        c = float(tr["c"])
        rnorm = tr.get("rnorm", "")
        if G.has_edge(h, t):
            # aggregate triples for this pair: weight = max confidence (A.2)
            ed = G[h][t]
            if c > ed["weight"]:
                ed["weight"] = c
                ed["rnorm"] = rnorm
                ed["rtext"] = tr.get("rtext", "")
                ed["evidence"] = tr.get("e", "")
        else:
            G.add_edge(h, t, weight=c, rnorm=rnorm, rtext=tr.get("rtext", ""),
                       evidence=tr.get("e", ""), doc=doc_id)
    return G


# Relations that are directional with a hard constraint on the HEAD's type.
# If the head's entity type is in the "bad head" set, the edge is reversed
# (or dropped if the reverse already exists). Fixes LLM head/tail flips like
# (prostate cancer, treats, radiation therapy).
# Entity types that should not act as the agent/head of directional intervention
# or molecular-action relations. Keep this broader than the prompt examples
# because extraction may type cancer concepts as Cancer/CancerType/Tumor rather
# than Disease, and clinical readouts as Risk/Feature/Stage/etc.
_BAD_HEAD_TYPES = {
    "Disease", "Cancer", "CancerType", "Tumor", "Syndrome",
    "Outcome", "Symptom", "Measurement",
    "RiskFactor", "Risk", "RiskScore", "RiskCategory",
    "Phenotype", "Trait", "Characteristic", "Feature",
    "Grade", "Stage", "Subtype",
    "ClinicalFactor", "ClinicalVariable",
}
_AGENT_RELATIONS = {"treats", "targets", "inhibits", "activates",
                    "upregulates", "downregulates", "regulates"}


def repair_edge_directions(G: "nx.DiGraph") -> int:
    """Reverse (or drop) agent-relation edges whose head is a Disease/Outcome/etc.
    Returns the number of edges fixed. Mutates G in place."""
    to_fix = []
    for u, v, d in G.edges(data=True):
        if d.get("rnorm") in _AGENT_RELATIONS and G.nodes[u].get("type") in _BAD_HEAD_TYPES:
            # only flip if the tail is NOT also a bad-head type (else ambiguous)
            if G.nodes[v].get("type") not in _BAD_HEAD_TYPES:
                to_fix.append((u, v, d))
    fixed = 0
    for u, v, d in to_fix:
        if not G.has_edge(u, v):
            continue
        if G.has_edge(v, u):
            G.remove_edge(u, v)  # reverse already exists -> drop the wrong one
        else:
            attrs = dict(d)
            G.remove_edge(u, v)
            G.add_edge(v, u, **attrs)  # flip head/tail
        fixed += 1
    return fixed


# =============================================================================
# Stage D -- Fusion into global KG (SAGE 3.1, A.2)
# =============================================================================

def fuse_graphs(local_graphs: List[nx.DiGraph], cache_dir: Path,
                tau: float = 0.9, min_component: int = 3) -> nx.DiGraph:
    """Fuse local graphs: BGE entity resolution (cosine>=tau), sum edge weights,
    prune disconnected components with < min_component nodes."""
    log.info("=" * 60)
    log.info("STAGE D: Fusion (tau=%.2f, prune comps < %d nodes)", tau, min_component)
    log.info("=" * 60)

    # Collect all unique entity strings + their dominant type.
    entity_types: Dict[str, str] = {}
    for G in local_graphs:
        for n, d in G.nodes(data=True):
            entity_types.setdefault(n, d.get("type", "Other"))
    entities = list(entity_types.keys())
    log.info("  Unique raw entities: %d", len(entities))
    if not entities:
        return nx.DiGraph()

    # BGE embeddings for entity resolution.
    bge = EmbeddingCache(BGE_MODEL, cache_dir / "bge")
    evecs = bge.encode(entities, is_sapbert=False)

    # Greedy agglomeration at cosine >= tau -> canonical id per entity.
    canon: Dict[str, str] = {}
    reps: List[int] = []  # indices of representative entities
    rep_vecs = np.empty((0, evecs.shape[1]), dtype=np.float32)
    for i, ent in enumerate(entities):
        if rep_vecs.shape[0] > 0:
            sims = rep_vecs @ evecs[i]
            best = int(np.argmax(sims))
            if sims[best] >= tau:
                canon[ent] = entities[reps[best]]
                continue
        canon[ent] = ent  # new representative
        reps.append(i)
        rep_vecs = np.vstack([rep_vecs, evecs[i]])
    n_merged = len(entities) - len(reps)
    log.info("  Entities merged: %d  canonical nodes: %d", n_merged, len(reps))

    # Build global graph on canonical nodes; sum weights on redundant edges.
    GG = nx.DiGraph()
    for ent, c in canon.items():
        if not GG.has_node(c):
            GG.add_node(c, type=entity_types.get(c, "Other"))
    for G in local_graphs:
        for u, v, d in G.edges(data=True):
            cu, cv = canon[u], canon[v]
            if cu == cv:
                continue
            if GG.has_edge(cu, cv):
                ed = GG[cu][cv]
                ed["weight"] += d.get("weight", 0.0)  # sum weights (A.2)
                # keep highest-confidence relation label
                if d.get("weight", 0) > ed.get("_maxc", 0):
                    ed["_maxc"] = d.get("weight", 0)
                    ed["rnorm"] = d.get("rnorm", "")
                    ed["rtext"] = d.get("rtext", "")
                    ed["evidence"] = d.get("evidence", "")
            else:
                GG.add_edge(cu, cv, weight=d.get("weight", 0.0),
                            _maxc=d.get("weight", 0.0), rnorm=d.get("rnorm", ""),
                            rtext=d.get("rtext", ""), evidence=d.get("evidence", ""))

    # Repair backwards directional edges (e.g. Disease -treats-> Therapy).
    n_fixed = repair_edge_directions(GG)
    if n_fixed:
        log.info("  Repaired %d backwards directional edges", n_fixed)

    # Prune small disconnected components (weakly connected, A.2).
    before = GG.number_of_nodes()
    for comp in list(nx.weakly_connected_components(GG)):
        if len(comp) < min_component:
            GG.remove_nodes_from(comp)
    log.info("  Pruned %d nodes in components < %d", before - GG.number_of_nodes(), min_component)

    # Strip internal helper attr before serialization.
    for _, _, d in GG.edges(data=True):
        d.pop("_maxc", None)

    log.info("  Global KG: %d nodes, %d edges", GG.number_of_nodes(), GG.number_of_edges())
    return GG


_XML_BAD = None  # compiled lazily


def _xml_clean(s: str) -> str:
    """Strip characters GraphML/XML can't serialize (NULL + C0/C1 control chars,
    except tab/newline/CR). PDF-extracted evidence spans sometimes carry these."""
    global _XML_BAD
    if _XML_BAD is None:
        import re
        # disallow control chars except \t \n \r
        _XML_BAD = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
    if not isinstance(s, str):
        return s
    return _XML_BAD.sub("", s)


def save_graph(G: nx.DiGraph, out_dir: Path, graph_root: str = "sage_kg",
               timestamp: bool = True) -> str:
    """Serialize to GraphML + typed node/edge JSON (SAGE: GraphML format).

    By default appends a timestamp to the filename so prior graphs are preserved
    (set timestamp=False to write a fixed name). Also maintains a stable
    `<graph_root>_latest.graphml` symlink/copy pointing at the newest build.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if timestamp:
        from datetime import datetime
        graph_root = f"{graph_root}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    graphml = str(out_dir / f"{graph_root}.graphml")

    # Sanitize string attributes + node ids so GraphML's XML writer doesn't choke
    # on NULL/control chars from PDF text extraction.
    relabel = {}
    for n in list(G.nodes()):
        cn = _xml_clean(n) if isinstance(n, str) else n
        if cn != n:
            relabel[n] = cn
    if relabel:
        nx.relabel_nodes(G, relabel, copy=False)
        log.info("  Sanitized %d node ids (removed control chars)", len(relabel))
    for _, d in G.nodes(data=True):
        for k, v in list(d.items()):
            if isinstance(v, str):
                d[k] = _xml_clean(v)
    for _, _, d in G.edges(data=True):
        for k, v in list(d.items()):
            if isinstance(v, str):
                d[k] = _xml_clean(v)

    # Add a `label` attribute (= entity name) so visualization tools (Gephi,
    # gephi-lite) display node names instead of "(Empty label)". Downstream
    # agents key on node id + `type`, so this is purely cosmetic for them.
    for n, d in G.nodes(data=True):
        d.setdefault("label", str(n))
    # `title` = normalized relation, so path strings render the relation
    # (path-finding/visualization read edge `title`).
    for _, _, d in G.edges(data=True):
        if not d.get("title"):
            d["title"] = d.get("rnorm") or d.get("rtext") or ""

    nx.write_graphml(G, graphml)

    nodes = [{"id": n, **d} for n, d in G.nodes(data=True)]
    edges = [{"source": u, "target": v, **d} for u, v, d in G.edges(data=True)]
    (out_dir / f"{graph_root}_nodes.json").write_text(json.dumps(nodes, indent=2))
    (out_dir / f"{graph_root}_edges.json").write_text(json.dumps(edges, indent=2))

    # Stable "_latest" copy so downstream tools can find the newest build without
    # knowing the timestamp. (Copy, not symlink, for portability.)
    if timestamp:
        import shutil
        base = graph_root.rsplit("_", 2)[0]  # strip the _YYYYmmdd_HHMMSS suffix
        latest = out_dir / f"{base}_latest.graphml"
        shutil.copyfile(graphml, latest)
        log.info("  Latest pointer: %s", latest)

    # Entity-type distribution for inspection.
    from collections import Counter
    dist = Counter(d.get("type", "Other") for _, d in G.nodes(data=True))
    log.info("  Entity type distribution: %s", dict(dist))
    log.info("  GraphML: %s", graphml)
    return graphml
