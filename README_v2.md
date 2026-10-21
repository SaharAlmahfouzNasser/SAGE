# SAGE Prostate Cancer Pipeline — v2
conda activate sage
export OPENAI_MODEL=gpt-5-mini
export OPENAI_REASONING_EFFORT=low
export PAPERCLIP_API_KEY=gxl_b2V5PCBsP6RjXhWfbCEu7L8Maffu1cpSgK_oWpDIASRJVAgp9yRosU19i8MJG1aV

sed -i 's/OPENAI_MODEL=.*/OPENAI_MODEL=gpt-5-mini/' .env
echo 'OPENAI_REASONING_EFFORT=low' >> .env
grep OPENAI_MODEL .env
grep OPENAI_REASONING_EFFORT .env

sed -i 's/OPENAI_MODEL=.*/OPENAI_MODEL=gpt-5-mini/' .env
grep -q OPENAI_REASONING_EFFORT .env && sed -i 's/OPENAI_REASONING_EFFORT=.*/OPENAI_REASONING_EFFORT=low/' .env || echo 'OPENAI_REASONING_EFFORT=low' >> .env
cat .env | grep OPENAI


This document describes the modifications made on top of the original SAGE /
Hypothesis-Generation codebase to build a **prostate-cancer** knowledge graph
from keywords and run the SAGE hypothesis-generation agents against it.

The original `README.md` still describes the upstream project. This file (`README_v2.md`)
covers **what we changed and how to run the full pipeline end-to-end.**

---

## TL;DR — run the whole thing

```bash
# 0. one-time: install deps + local packages
pip install -r requirements.txt
pip install -e GraphReasoning
pip install -e HypothesisGeneration-pkg

# 1. search queries — ALREADY PROVIDED (skip this step)
#    build_kg.py reads keywords/expanded_search_queries.json (60 hand-curated queries).
#    These were authored by hand, NOT by expand_keywords.py — see "Search queries" below.
#    expand_keywords.py exists (programmatic GPT+MeSH expansion) but its output was
#    MeSH-noisy, so we do not use it for the current graph.

# 2. build the knowledge graph
python build_kg.py --no-s2 --max-per-query 50
#    -> outputs/sage_kg/prostate_cancer_kg_<timestamp>.graphml  (+ _latest.graphml)

# 3. generate node embeddings (fast, MPS/CUDA batched)
python gen_node_embeddings.py
#    -> outputs/sage_kg/embeddings-prostate_cancer_kg_latest-bge_large_en_v1.5.pkl

# 4a. inspect ranked graph paths only (fast iteration; no LLM agents)
python run_path_discovery.py \
    --graph outputs/sage_kg/prostate_cancer_kg_latest.graphml \
    --query "Find underexplored prognostic biomarkers in prostate cancer." \
    --source "prostate cancer"
#    -> path_discovery/<run>_<timestamp>.txt (+ .json)

# 4b. run a single full hypothesis
python run_pipeline.py --graph outputs/sage_kg/prostate_cancer_kg_latest.graphml \
    --query "Discover research idea between prostate cancer||Disease and Gene"

# 4c. OR batch-run a set of research questions
python batch_questions.py            # 46 prostate questions -> outputs/prostate_questions/
```

Requires `OPENAI_API_KEY` in `.env`. **No Semantic Scholar key needed** (replaced by OpenAlex).

---

## Pipeline overview

```
keywords/prostate_cancer_keywords.txt
        │  (hand-curated into boolean queries — see "Search queries" note)
        ▼
keywords/expanded_search_queries.json   (60 curated queries — what build_kg actually reads)
        │  build_kg.py
        │    Phase 2  : search PubMed (+optional S2)
        │    enrich   : OpenAlex citations + venue
        │    Stage A  : SapBERT + venue + citation relevance filter (drop ~40%)
        │    Phase 3  : full-text (PMC -> EuropePMC -> Unpaywall -> abstract)  [kept papers only]
        │    Stage B  : GPT-4o-mini typed-triple extraction (8-field, c>=0.5) [checkpointed]
        │    Stage C  : per-document local graphs (edge weight = max confidence)
        │    Stage D  : BGE-Large fusion (cosine tau=0.9) + direction repair + prune
        ▼
outputs/sage_kg/prostate_cancer_kg_<ts>.graphml  (+ _latest pointer, typed node/edge JSON)
        │  gen_node_embeddings.py  (BGE-Large, batched)
        ▼
outputs/sage_kg/embeddings-...-bge_large_en_v1.5.pkl
        │  run_path_discovery.py
        │    path-gen only: find paths -> score/rank -> save txt/json
        │
        │  run_pipeline.py / batch_questions.py
        │    full SciAgentsGraphFlow: path-gen -> ontologist -> scientist
        │                             -> hypothesis-expansion -> novelty critic
        ▼
hypothesis runs (.txt/.md/.pdf)
```

---

## New / modified files

| File | Status | Purpose |
| --- | --- | --- |
| `sage_kg.py` | **new** | SAGE-faithful KG construction: relevance filter, typed-triple extraction, local graphs, BGE fusion, GraphML save. Replaces the generic GraphReasoning co-occurrence graph. |
| `build_kg.py` | **rewritten** | Orchestrates fetch → OpenAlex enrich → filter → full-text → extract (checkpointed) → fuse → save. |
| `expand_keywords.py` | **new** | Phase-1 keyword expansion: parse keyword doc → GPT + MeSH expansion → boolean queries + flat keyword list. **Not used for the current graph** (its MeSH expansion was noisy — see "Search queries"). |
| `gen_node_embeddings.py` | **new** | Fast MPS/CUDA-batched node embeddings (replicates GraphReasoning's method, ~30x faster). |
| `patch_pathfinding.py` | **new** | Runtime patch module — fixes broken path-finding + swaps Semantic Scholar → OpenAlex + sets report domain. Imported by `run_pipeline.py` / `batch_questions.py`. |
| `run_path_discovery.py` | **new** | Focused path-generation/ranking CLI. Runs only graph path discovery, writes timestamped `path_discovery/*.txt` + `.json`, and skips all downstream agents. |
| `run_pipeline.py` | **new** | Runnable form of `notebooks/new_graph_flow_pipeline.ipynb`, corrected to the real `SciAgentsGraphFlow` API. Runs one hypothesis. |
| `batch_questions.py` | **new** | Loads graph + embeddings once, runs a list of research questions, saves `<name>.md` each. Resumable. |
| `keywords/prostate_questions.json` | **new** | 46 prostate-adapted research questions (mirrors the SAGE team's `sage_questions/`). |
| `keywords/expanded_search_queries.json` | **replaced** | **60 hand-curated** prostate-cancer boolean search queries — the actual input to `build_kg.py`. |
| `keywords/prostate_cancer_expanded_keywords.{json,txt}` | **new** | Flat expanded keyword set from `expand_keywords.py`. Reference only — **not consumed by `build_kg.py`**. |
| `keywords/expanded_search_queries.OLD47.json.bak` | **backup** | The earlier auto-generated (MeSH-noisy) 47-query set, kept for reference. |
| `HypothesisGeneration-pkg/.../domains.py` | **modified** | Added `PROSTATE_CANCER` domain config + `get_domain()` lookup. |
| `HypothesisGeneration-pkg/.../report_generator.py` | **modified** | Validation-plan data source is now domain-aware (TCGA-PRAD for prostate) instead of hardcoded TCGA-BLCA. |
| `requirements.txt` | **modified** | Added `pdfplumber`, `PyMuPDF`, `tiktoken`, `sentence-transformers`. |

---

## What we changed and why

### KG construction (`sage_kg.py`, `build_kg.py`)

The original `build_kg.py` called `GraphReasoning.make_graph_from_text` (Markus
Buehler's materials-science tool), which produces a generic `{node_1, node_2, edge}`
co-occurrence graph — **not** the SAGE method. We reimplemented SAGE's KG pipeline
(paper §3.1 / Appendix A.1-A.2):

- **Typed triples** — GPT-4o-mini with **structured output** enforcing the 8-field
  schema `(h, th, rtext, rnorm, t, tt, c, e)`: head/tail entities, their ontology
  types, surface + normalized relation, confidence, evidence span.
- **Entity ontology** — the 36-type set from the published KG legend (Gene, Disease,
  Drug, Pathway, Protein, …).
- **Relation vocabulary** — a controlled set of ~34 normalized relations (`rnorm`),
  emitted directly by the model via a schema enum (includes diagnostic/imaging
  relations: `diagnosed_by`, `imaged_by`, `detects`, `stages`, …).
- **Confidence gate** — keep triples with `c >= 0.5`.
- **Per-document local graphs** — edge weight = max confidence among supporting triples.
- **Fusion** — BGE-Large entity resolution at cosine `tau = 0.9`, weight summation,
  prune components `< 3` nodes, serialize GraphML.

### Search queries (what build_kg actually fetches with)

`build_kg.py` reads **`keywords/expanded_search_queries.json`** — a list of
`{category, subcategory, query, rationale}` objects whose `query` field is a PubMed
boolean string. It uses these 60 queries directly; it does **not** read the flat
expanded-keyword files.

How the current 60 queries were produced: **they were hand-curated** directly from
`keywords/prostate_cancer_keywords.txt`, with correct gene aliases (BRCA1/BRCA2/CDK12…),
drug brand+generic names (Xtandi/enzalutamide…), and trial names (CHAARTED/STAMPEDE/
VISION…).

We first tried the programmatic route (`expand_keywords.py`: GPT + MeSH + Semantic
Scholar expansion), but the MeSH step injected **wrong-domain noise** — e.g. mammary-
gland and rat-gene synonyms ended up inside prostate queries. So we discarded the
auto-generated set (kept as `expanded_search_queries.OLD47.json.bak`) and wrote clean
queries by hand.

**This is a deliberate deviation from SAGE's automated Phase-1 keyword expansion.** If
you want the programmatic path instead, run `python expand_keywords.py` — but review/
prune its output, or improve the MeSH filtering, before feeding it to `build_kg.py`.
To use a different query set: `python build_kg.py --queries path/to/queries.json`.

### Relevance filtering (Stage A)

SAGE filters ~40% of papers on a composite of SapBERT similarity, venue / journal
impact, and keyword frequency. We implement:

```
composite = w_sap * sapbert_sim + w_venue * venue_score + w_cite * cite_score
```

with **adaptive renormalization** — terms with no signal (e.g. all-zero citations)
drop out automatically. `venue_score` comes from a journal → impact-tier lookup
table; `cite_score` from OpenAlex citation counts. **The filter runs before
full-text retrieval**, so we only download the full text of papers we keep.

### Full-text retrieval (Phase 3)

Four-tier fallback per paper: **PMC efetch → Europe PMC full-text XML → Unpaywall
OA PDF (3-tier pdfplumber/PyMuPDF/pypdf) → abstract**. Source recorded per paper.

### OpenAlex replaces Semantic Scholar (no API key)

Semantic Scholar's keyless endpoint is rate-limited (429), and an invalid/placeholder
key returns a hard 403. We replaced S2 with **OpenAlex** (free, no key) in two places:

1. `build_kg.py` `enrich_openalex()` — citation counts + venue for the Stage A filter.
2. The novelty-critic's literature-search tool — `patch_pathfinding.py` swaps
   `semanticscholar_search` for an OpenAlex-backed implementation (same signature /
   output), so the critic's references populate instead of 403ing.

### Path-finding fix (`patch_pathfinding.py`)

`HypothesisGeneration.utils` does `from GraphReasoning import *`, but GraphReasoning's
package `__init__` re-exports **nothing**, so several functions that `create_path` /
`find_paths_to_label` call (`find_path`, `print_path_with_edges_as_list`, …) raise
`NameError` at runtime. The result: the path-generation agent returned **"No paths
found" for every query, regardless of the graph.** `patch_pathfinding.py` injects the
missing names into the utils namespace, restoring real multi-path traversal. (This is
a bug in the vendored GraphReasoning code — worth reporting upstream.)

### Graph-quality fixes

- **Edge-direction repair** — LLM extraction sometimes flips head/tail, producing
  e.g. `prostate cancer --treats--> radiation therapy`. `repair_edge_directions()`
  reverses (or drops) agent-relation edges (`treats/targets/inhibits/...`) whose head
  is a Disease/Outcome/Symptom. The extraction prompt also now states explicit
  direction rules and rejects vague entities ("trends", "incidence", "care").
- **GraphML sanitization** — strips NULL / control characters (from PDF text) that
  otherwise break the XML writer.
- **Visualization attributes** — every node gets a `label` (= entity name) and every
  edge a `title` (= normalized relation) so Gephi / gephi-lite show names + relations.

### Domain awareness (`domains.py`, `report_generator.py`)

Added a `PROSTATE_CANCER` domain config (TCGA-PRAD cohort, prostate gene sets, AR/DDR/
PSMA therapy proxies). The report's validation-plan data source now resolves from the
active domain instead of the hardcoded `TCGA-BLCA`. Set via `SAGE_DOMAIN` env var
(default `prostate cancer`).

---

## Outputs & caching

| Path | What |
| --- | --- |
| `inputs/sage_kg/paper_metadata.json` | fetched papers + full-text + identifiers |
| `inputs/sage_kg/triples_checkpoint.jsonl` | extracted triples, one doc per line (**resumable** — re-runs skip done docs) |
| `inputs/sage_kg/embed_cache/` | cached SapBERT/BGE embeddings (filter + fusion) |
| `outputs/sage_kg/prostate_cancer_kg_<ts>.graphml` | timestamped knowledge graph |
| `outputs/sage_kg/prostate_cancer_kg_latest.graphml` | stable pointer to newest graph |
| `outputs/sage_kg/*_nodes.json` / `*_edges.json` | typed nodes / edges with evidence + confidence |
| `outputs/sage_kg/embeddings-*.pkl` | node embeddings for the agent pipeline |
| `path_discovery/<run>_<timestamp>.txt` / `.json` | focused ranked graph-path outputs from `run_path_discovery.py` |
| `outputs/prostate_questions/<name>.{md,txt,pdf}` | per-question hypothesis runs |

---

## Useful flags

`build_kg.py`:
- `--max-per-query N` — papers fetched per query (15 = small graph, 50 ≈ SAGE scale).
- `--no-s2` — skip Semantic Scholar search (OpenAlex still supplies citations).
- `--no-openalex` — skip citation/venue enrichment.
- `--drop-frac F` — relevance filter drop fraction (default 0.40).
- `--from-checkpoint` — rebuild the graph from cached triples only (no fetch / no LLM;
  seconds, not hours). Use after a save change or to re-fuse.
- `--fresh` — clear the triples checkpoint and re-extract everything.
- `--limit-papers N` — cap papers after fetch (testing).

`run_pipeline.py`:
- `--graph PATH` — graph to use (default `_latest`).
- `--query "...||Type and Type"` — the research prompt; `entity||Type` pins an entity.
- `--domain "prostate cancer"` — target domain for path scoring + reports.
- `--no-timestamp` — overwrite a fixed output name instead of timestamping.

`run_path_discovery.py`:
- `--graph PATH` — GraphML to use (default `outputs/sage_kg/prostate_cancer_kg.graphml`).
- `--source ENTITY` — specific source entity, e.g. `"prostate cancer"`.
- `--target-type TYPE` — target node type, alias, named group, or comma-separated
  list, e.g. `Gene`, `Drug`, `Biomarker`, `Morphology`, or `Phenotype,Measurement`.
  Blank infers from `--query`. Morphology wording expands across morphology-adjacent
  graph types instead of forcing all paths into one endpoint class.
- `--query "..."` — research question used for logic scoring.
- `--domain "prostate cancer"` — target domain for relevance scoring.
- `--max-targets N` — number of target nodes sampled for path search.
- `--top-n N` — number of ranked paths to save.
- `--logic-weight`, `--relevance-weight`, `--novelty-weight`, `--surprise-weight` —
  weights for the final ranking score; they are ranking weights, not score targets.
- `--skip-logic` — skip Sentence-BERT logic scoring for faster novelty/surprise sweeps.
- `--out-dir DIR` — default directory for timestamped outputs (default `path_discovery`).
- `--run-name NAME` — base name for timestamped default outputs.
- `--out FILE`, `--json-out FILE`, `--no-json` — override or disable default outputs.

`batch_questions.py`:
- `--questions FILE` — question list JSON (default `keywords/prostate_questions.json`).
- `--graph PATH` — graph to use (default `outputs/sage_kg/prostate_cancer_kg_latest.graphml`).
- `--out-dir DIR` — output folder (default `outputs/prostate_questions`).
- `--domain "prostate cancer"` — target domain for path scoring + reports.
- `--only name1,name2` — run a subset (matches the `name` field in the JSON).
- `--redo` — re-run questions whose output already exists (default: skip done ones).

---

## Example commands

### `run_path_discovery.py` — ranked graph paths only

Use this when iterating on path quality / novelty before running the full SAGE
agent pipeline. It loads the graph and cached node embeddings, finds graph paths
from a source entity to one or more target node types, scores them together, and
saves only the ranked path block.

```bash
# Default prostate biomarker path discovery.
# Output: path_discovery/prostate_cancer_to_biomarker_<timestamp>.txt (+ .json)
python run_path_discovery.py

# Use the latest GraphML explicitly.
python run_path_discovery.py \
  --graph outputs/sage_kg/prostate_cancer_kg_latest.graphml

# Absolute GraphML paths work too.
python run_path_discovery.py \
  --graph /Users/jduke34/Documents/GitHub/Hypothesis-Generation-LD/outputs/sage_kg/prostate_cancer_kg_latest.graphml

# Focus on a particular question/source/type.
python run_path_discovery.py \
  --query "Find underexplored prognostic biomarkers in prostate cancer." \
  --source "prostate cancer" \
  --target-type Biomarker \
  --top-n 20

# Let the script infer source and target group from a morphology question.
python run_path_discovery.py \
  --query "Find morphological biomarkers of prostate cancer" \
  --top-n 20

# Or explicitly search multiple endpoint types and rank them together.
python run_path_discovery.py \
  --query "Find morphology-linked prostate cancer biomarkers" \
  --source "prostate cancer" \
  --target-type Phenotype,Measurement,ImagingTechnique,Tissue

# Heavier novelty/surprise ranking for idea discovery.
python run_path_discovery.py \
  --novelty-weight 0.45 \
  --surprise-weight 0.35 \
  --relevance-weight 0.15 \
  --logic-weight 0.05

# Sort final results by a novelty-heavy path-quality score.
python run_path_discovery.py \
  --query "Find morphological biomarkers of prostate cancer" \
  --rank-by novel \
  --novelty-weight 0.5 \
  --relevance-weight 0.2 \
  --logic-weight 0.1 \
  --surprise-weight 0.2 \
  --top-n 20

# Faster sweeps: skip the logic scorer and save under a custom run name.
python run_path_discovery.py \
  --skip-logic \
  --run-name anant1_paths
```

`--novelty-weight` changes how strongly the final ranker prefers paths containing
rarer / less-connected graph entities. It does not set a target novelty score.
`--rank-by novel` changes the final sort to a novelty-heavy `NovelPath` score
that keeps relevance/logic/surprise but adds query-match bonuses and generic
endpoint, off-domain cancer, and suspicious relation-direction penalties.
The script looks for the matching embedding cache next to the GraphML, e.g.
`embeddings-prostate_cancer_kg_latest-bge_large_en_v1.5.pkl`; if missing, it
generates and saves it.

The full pipeline's `find_related_entities` tool now accepts the same
multi-target shape through its existing `target_type` argument. For morphology
questions, a `Biomarker`/`Measurement` target is expanded internally to the
`Morphology` group so the path search is not limited to one graph node type.

### `run_pipeline.py` — single hypothesis

```bash
# Default: prostate cancer -> Gene, on the latest graph
python run_pipeline.py

# Pin a specific entity and target type (entity||Type and Type)
python run_pipeline.py \
  --query "Discover research idea between AR-V7||Protein and Drug"

python run_pipeline.py \
  --query "Discover research idea between BRCA2||Gene and Drug"

python run_pipeline.py \
  --query "Discover research idea between PTEN loss||Gene and Pathway"

# Free-form clinical question (no ||Type pin needed)
python run_pipeline.py \
  --query "What biomarkers predict response to PSMA radioligand therapy in mCRPC?"

# Use a specific (older) graph instead of _latest, and a fixed output name
python run_pipeline.py \
  --graph outputs/sage_kg/prostate_cancer_kg_20260531_114334.graphml \
  --query "Discover research idea between neuroendocrine prostate cancer||Disease and Gene" \
  --run-name nepc_run --no-timestamp
```

Output: `outputs/sage_kg/hypothesis_run_<timestamp>.{txt,md,pdf}` (or `--run-name`).
First run on a new graph generates node embeddings (cached afterwards); to make that
fast, run `python gen_node_embeddings.py` first.

### `batch_questions.py` — many questions at once

```bash
# Run all 46 prostate questions -> outputs/prostate_questions/<name>.{md,txt,pdf}
python batch_questions.py

# Run only a subset (names match the JSON "name" field)
python batch_questions.py --only anant1,anant2,tilak1

# Run one question end-to-end (handy for a quick check)
python batch_questions.py --only anant1

# Re-run questions even if their output already exists
python batch_questions.py --only abe1 --redo

# Point at a different graph / output folder
python batch_questions.py \
  --graph outputs/sage_kg/prostate_cancer_kg_latest.graphml \
  --out-dir outputs/prostate_questions_v2

# Your own question list
python batch_questions.py --questions keywords/my_questions.json
```

`batch_questions.py` loads the graph + embeddings **once** and reuses them across all
questions, and is **resumable** — re-running skips questions whose `<name>.md` already
exists (use `--redo` to force). Question-list JSON format:

```json
[
  {"name": "q1", "question": "Find underexplored prognostic biomarkers in prostate cancer."},
  {"name": "q2", "question": "Which histological features predict AR-V7 status?"}
]
```

---

## Known limitations / for the SAGE team

- **Relation vocabulary and journal-IF table are our designs**, not from the paper
  (which cites Peng 2017 / Zhang 2018 for relation mapping but doesn't list the set).
  Ask the team for their canonical relation list and impact-factor table.
- **Fusion is greedy** (order-dependent, O(N²)); fine at this scale, but a FAISS +
  union-find approach would be more robust at SAGE's 41k-node scale.
- **No ontology-ID grounding** — nodes are `(string, type)`, not linked to UMLS / HGNC /
  MeSH IDs. The Ontologist agent implies such grounding.
- **GraphReasoning re-export bug** worked around in `patch_pathfinding.py` — report upstream.
- The diagnostic-relation vocabulary + junk-entity prompt rules only affect **new**
  extractions; cached triples keep their original labels until a `--fresh` rebuild.
