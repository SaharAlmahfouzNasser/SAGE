"""
SAGE downstream hypothesis-generation pipeline (runnable script form).

Mirrors notebooks/new_graph_flow_pipeline.ipynb, but wired to the KG built by
build_kg.py and corrected to the actual SciAgentsGraphFlow API.

Flow:
  load GraphML  ->  giant component  ->  node embeddings (BGE-large, cached .pkl)
  ->  SciAgentsGraphFlow (path-gen -> ontologist -> scientist -> critics)
  ->  run_with_console(query)  ->  hypothesis run saved to outputs/

Usage:
  python run_pipeline.py
  python run_pipeline.py --query "Discover research idea between prostate cancer||Disease and Gene"
  python run_pipeline.py --graph outputs/sage_kg/prostate_cancer_kg.graphml --domain "prostate cancer"

Metrics:
  Prints a "SAGE_METRICS: {json}" line to stdout when the run finishes, with
  wall-clock duration and (best-effort) LLM token/call counts. server.py's
  job runner watches for this exact prefix and captures it automatically —
  see METRICS_PREFIX in server.py. Token counts come from monkey-patching
  the OpenAI client's chat.completions.create (see _install_usage_tracker
  below), since SciAgentsGraphFlow's internal LLM calls (in the
  HypothesisGeneration package) aren't otherwise instrumented. This patches
  known internal call sites; if your installed openai package's internal
  module layout differs, or if HypothesisGeneration uses something other
  than the openai SDK directly (e.g. litellm, langchain), token counts may
  come back as 0 even though duration_seconds is still accurate. Check the
  "SAGE_METRICS" line after a run — if total_tokens is 0, tell me and I'll
  adjust the patch target once I can see how HypothesisGeneration actually
  calls the model.
"""

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

import networkx as nx
from transformers import AutoModel, AutoTokenizer

import patch_pathfinding  # noqa: F401 — injects missing GraphReasoning path fns into utils

from GraphReasoning.graph_tools import (
    load_graph_with_text_as_JSON,
    load_embeddings,
    generate_node_embeddings,
    save_embeddings,
)
from GraphReasoning.graph_analysis import return_giant_component_of_graph
from GraphReasoning.utils import make_dir_if_needed
from HypothesisGeneration import SciAgentsGraphFlow

EMBED_MODEL = "BAAI/bge-large-en-v1.5"

METRICS_PREFIX = "SAGE_METRICS:"
_usage = {"llm_calls": 0, "llm_errors": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def _install_usage_tracker() -> None:
    """Monkey-patch the OpenAI SDK's chat.completions.create (sync + async)
    so every call SciAgentsGraphFlow makes internally is counted, without
    needing to modify the HypothesisGeneration package itself. Best-effort:
    if the package doesn't go through these exact call sites (e.g. it uses
    litellm or a custom HTTP client instead of the openai SDK), this won't
    catch anything and total_tokens will read 0 in the final metrics line.
    """
    try:
        import openai
    except ImportError:
        return

    def _record(resp):
        usage = getattr(resp, "usage", None)
        if usage:
            _usage["prompt_tokens"] += getattr(usage, "prompt_tokens", 0) or 0
            _usage["completion_tokens"] += getattr(usage, "completion_tokens", 0) or 0
            _usage["total_tokens"] += getattr(usage, "total_tokens", 0) or 0
        _usage["llm_calls"] += 1

    # Async client — most likely path, since run_with_console is awaited.
    try:
        from openai.resources.chat.completions import AsyncCompletions
        orig_async_create = AsyncCompletions.create

        async def patched_async_create(self, *args, **kwargs):
            try:
                resp = await orig_async_create(self, *args, **kwargs)
                _record(resp)
                return resp
            except Exception:
                _usage["llm_errors"] += 1
                raise

        AsyncCompletions.create = patched_async_create
    except Exception as e:
        print(f"[metrics] could not patch AsyncCompletions.create: {e}")

    # Sync client — in case any internal calls aren't async.
    try:
        from openai.resources.chat.completions import Completions
        orig_sync_create = Completions.create

        def patched_sync_create(self, *args, **kwargs):
            try:
                resp = orig_sync_create(self, *args, **kwargs)
                _record(resp)
                return resp
            except Exception:
                _usage["llm_errors"] += 1
                raise

        Completions.create = patched_sync_create
    except Exception as e:
        print(f"[metrics] could not patch Completions.create: {e}")


def _emit_metrics(duration_seconds: float, extra: dict | None = None) -> None:
    """Print the SAGE_METRICS line server.py's job runner watches for."""
    payload = {"duration_seconds": round(duration_seconds, 1), **_usage}
    if duration_seconds and _usage["total_tokens"]:
        payload["tokens_per_second"] = round(_usage["total_tokens"] / duration_seconds, 1)
    if extra:
        payload.update(extra)
    print(f"{METRICS_PREFIX} {json.dumps(payload)}")


def load_env():
    if Path(".env").exists():
        for line in open(".env"):
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ.setdefault(k, v)


def _build_path_eval_weights(args) -> dict | None:
    """Build a complete 4-key weights dict if the person overrode any of the
    four --*-weight flags, filling in the rest with SciAgentsGraphFlow's own
    defaults. Returns None (unchanged) if none were set, so the class's
    built-in default applies exactly as before.

    IMPORTANT: SciAgentsGraphFlow does `path_eval_weights or {defaults}` —
    a *partial* dict (e.g. just {"surprise": 0.0}) would silently replace
    the whole weights dict rather than merging, dropping the other three
    terms entirely. This always returns a complete 4-key dict or None, never
    a partial one, to avoid that trap.
    """
    defaults = {"logic": 0.2, "relevance": 0.2, "novelty": 0.3, "surprise": 0.3}
    overrides = {}
    if args.logic_weight is not None:
        overrides["logic"] = args.logic_weight
    if args.relevance_weight is not None:
        overrides["relevance"] = args.relevance_weight
    if args.novelty_weight is not None:
        overrides["novelty"] = args.novelty_weight
    if args.surprise_weight is not None:
        overrides["surprise"] = args.surprise_weight
    if not overrides:
        return None
    return {**defaults, **overrides}


async def main():
    t0 = time.time()
    _install_usage_tracker()

    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", default="outputs/sage_kg/prostate_cancer_kg.graphml")
    ap.add_argument("--domain", default="prostate cancer",
                    help="target domain for path novelty/relevance scoring")
    ap.add_argument("--query",
                    default="Discover research idea between prostate cancer||Disease and Gene",
                    help="hypothesis-generation prompt; use 'entity||Type' to pin an entity")
    ap.add_argument("--out-dir", default="outputs/sage_kg")
    ap.add_argument("--run-name", default="hypothesis_run")
    ap.add_argument("--no-timestamp", action="store_true",
                    help="don't append a timestamp to the run name (overwrites prior run)")
    ap.add_argument("--logic-weight", type=float, default=None,
                    help="weight for logic score in path selection (default 0.2, matches "
                         "SciAgentsGraphFlow's built-in default if omitted)")
    ap.add_argument("--relevance-weight", type=float, default=None,
                    help="weight for relevance score in path selection (default 0.2)")
    ap.add_argument("--novelty-weight", type=float, default=None,
                    help="weight for novelty score in path selection (default 0.3)")
    ap.add_argument("--surprise-weight", type=float, default=None,
                    help="weight for surprise score in path selection (default 0.3)")
    args = ap.parse_args()

    load_env()
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY not set (put it in .env)")

    graph_path = Path(args.graph)
    data_dir = graph_path.parent
    graph_name = graph_path.name
    out_dir = Path(args.out_dir)
    make_dir_if_needed(out_dir)

    try:
        # 1) Embedding model (BGE-large) — same model the KG fusion used.
        print(f"[1/4] Loading embedding model {EMBED_MODEL} ...")
        embedding_tokenizer = AutoTokenizer.from_pretrained(EMBED_MODEL)
        embedding_model = AutoModel.from_pretrained(EMBED_MODEL)

        # 2) Load graph -> giant component (path reasoning needs one connected graph).
        print(f"[2/4] Loading graph {graph_path} ...")
        G = load_graph_with_text_as_JSON(data_dir=str(data_dir), graph_name=graph_name)
        G = return_giant_component_of_graph(G)
        G = nx.Graph(G)  # SciAgents traverses an undirected view
        print(f"      giant component: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

        # 3) Node embeddings — cached to .pkl, regenerated only if missing.
        embed_pkl = data_dir / f"embeddings-{graph_path.stem}-bge_large_en_v1.5.pkl"
        try:
            node_embeddings = load_embeddings(str(embed_pkl))
            print(f"[3/4] Loaded cached node embeddings: {embed_pkl}")
        except Exception:
            print(f"[3/4] Generating node embeddings (BGE, ~5-10 min for thousands of nodes)...")
            node_embeddings = generate_node_embeddings(G, embedding_tokenizer, embedding_model)
            save_embeddings(node_embeddings, str(embed_pkl))
            print(f"      saved -> {embed_pkl}")

        # 4) Build the agent flow and run.
        path_eval_weights = _build_path_eval_weights(args)
        print(f"[4/4] Running SciAgentsGraphFlow (domain='{args.domain}')")
        print(f"      query: {args.query}")
        print(f"      path_eval_weights: "
              f"{path_eval_weights if path_eval_weights else '(default: logic=0.2 relevance=0.2 novelty=0.3 surprise=0.3)'}")
        sci_agents = SciAgentsGraphFlow(
            G=G,
            embedding_tokenizer=embedding_tokenizer,
            embedding_model=embedding_model,
            node_embeddings=node_embeddings,
            target_domain=args.domain,
            path_eval_weights=path_eval_weights,
        )

        # Timestamp the run name so prior hypothesis runs are preserved.
        run_name = args.run_name
        if not args.no_timestamp:
            from datetime import datetime
            run_name = f"{run_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        save_to = out_dir / f"{run_name}.txt"
        await sci_agents.run_with_console(message=args.query, save_to=save_to)
        print(f"\nDone. Run saved to: {save_to} (+ .md / .pdf report)")

        _emit_metrics(time.time() - t0, extra={
            "graph": graph_name, "domain": args.domain,
            "path_eval_weights": path_eval_weights or {"logic": 0.2, "relevance": 0.2, "novelty": 0.3, "surprise": 0.3},
        })
    except Exception:
        # Still report timing/whatever tokens were captured before the failure,
        # so a failed run isn't a total blank in the metrics history.
        _emit_metrics(time.time() - t0, extra={"failed": True})
        raise


if __name__ == "__main__":
    asyncio.run(main())







# """
# SAGE downstream hypothesis-generation pipeline (runnable script form).

# Mirrors notebooks/new_graph_flow_pipeline.ipynb, but wired to the KG built by
# build_kg.py and corrected to the actual SciAgentsGraphFlow API.

# Flow:
#   load GraphML  ->  giant component  ->  node embeddings (BGE-large, cached .pkl)
#   ->  SciAgentsGraphFlow (path-gen -> ontologist -> scientist -> critics)
#   ->  run_with_console(query)  ->  hypothesis run saved to outputs/

# Usage:
#   python run_pipeline.py
#   python run_pipeline.py --query "Discover research idea between prostate cancer||Disease and Gene"
#   python run_pipeline.py --graph outputs/sage_kg/prostate_cancer_kg.graphml --domain "prostate cancer"

# Metrics:
#   Prints a "SAGE_METRICS: {json}" line to stdout when the run finishes, with
#   wall-clock duration and (best-effort) LLM token/call counts. server.py's
#   job runner watches for this exact prefix and captures it automatically —
#   see METRICS_PREFIX in server.py. Token counts come from monkey-patching
#   the OpenAI client's chat.completions.create (see _install_usage_tracker
#   below), since SciAgentsGraphFlow's internal LLM calls (in the
#   HypothesisGeneration package) aren't otherwise instrumented. This patches
#   known internal call sites; if your installed openai package's internal
#   module layout differs, or if HypothesisGeneration uses something other
#   than the openai SDK directly (e.g. litellm, langchain), token counts may
#   come back as 0 even though duration_seconds is still accurate. Check the
#   "SAGE_METRICS" line after a run — if total_tokens is 0, tell me and I'll
#   adjust the patch target once I can see how HypothesisGeneration actually
#   calls the model.
# """

# import argparse
# import asyncio
# import json
# import os
# import time
# from pathlib import Path

# import networkx as nx
# from transformers import AutoModel, AutoTokenizer

# import patch_pathfinding  # noqa: F401 — injects missing GraphReasoning path fns into utils

# from GraphReasoning.graph_tools import (
#     load_graph_with_text_as_JSON,
#     load_embeddings,
#     generate_node_embeddings,
#     save_embeddings,
# )
# from GraphReasoning.graph_analysis import return_giant_component_of_graph
# from GraphReasoning.utils import make_dir_if_needed
# from HypothesisGeneration import SciAgentsGraphFlow

# EMBED_MODEL = "BAAI/bge-large-en-v1.5"

# METRICS_PREFIX = "SAGE_METRICS:"
# _usage = {"llm_calls": 0, "llm_errors": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


# def _install_usage_tracker() -> None:
#     """Monkey-patch the OpenAI SDK's chat.completions.create (sync + async)
#     so every call SciAgentsGraphFlow makes internally is counted, without
#     needing to modify the HypothesisGeneration package itself. Best-effort:
#     if the package doesn't go through these exact call sites (e.g. it uses
#     litellm or a custom HTTP client instead of the openai SDK), this won't
#     catch anything and total_tokens will read 0 in the final metrics line.
#     """
#     try:
#         import openai
#     except ImportError:
#         return

#     def _record(resp):
#         usage = getattr(resp, "usage", None)
#         if usage:
#             _usage["prompt_tokens"] += getattr(usage, "prompt_tokens", 0) or 0
#             _usage["completion_tokens"] += getattr(usage, "completion_tokens", 0) or 0
#             _usage["total_tokens"] += getattr(usage, "total_tokens", 0) or 0
#         _usage["llm_calls"] += 1

#     # Async client — most likely path, since run_with_console is awaited.
#     try:
#         from openai.resources.chat.completions import AsyncCompletions
#         orig_async_create = AsyncCompletions.create

#         async def patched_async_create(self, *args, **kwargs):
#             try:
#                 resp = await orig_async_create(self, *args, **kwargs)
#                 _record(resp)
#                 return resp
#             except Exception:
#                 _usage["llm_errors"] += 1
#                 raise

#         AsyncCompletions.create = patched_async_create
#     except Exception as e:
#         print(f"[metrics] could not patch AsyncCompletions.create: {e}")

#     # Sync client — in case any internal calls aren't async.
#     try:
#         from openai.resources.chat.completions import Completions
#         orig_sync_create = Completions.create

#         def patched_sync_create(self, *args, **kwargs):
#             try:
#                 resp = orig_sync_create(self, *args, **kwargs)
#                 _record(resp)
#                 return resp
#             except Exception:
#                 _usage["llm_errors"] += 1
#                 raise

#         Completions.create = patched_sync_create
#     except Exception as e:
#         print(f"[metrics] could not patch Completions.create: {e}")


# def _emit_metrics(duration_seconds: float, extra: dict | None = None) -> None:
#     """Print the SAGE_METRICS line server.py's job runner watches for."""
#     payload = {"duration_seconds": round(duration_seconds, 1), **_usage}
#     if duration_seconds and _usage["total_tokens"]:
#         payload["tokens_per_second"] = round(_usage["total_tokens"] / duration_seconds, 1)
#     if extra:
#         payload.update(extra)
#     print(f"{METRICS_PREFIX} {json.dumps(payload)}")


# def load_env():
#     if Path(".env").exists():
#         for line in open(".env"):
#             if "=" in line and not line.startswith("#"):
#                 k, v = line.strip().split("=", 1)
#                 os.environ.setdefault(k, v)


# async def main():
#     t0 = time.time()
#     _install_usage_tracker()

#     ap = argparse.ArgumentParser()
#     ap.add_argument("--graph", default="outputs/sage_kg/prostate_cancer_kg.graphml")
#     ap.add_argument("--domain", default="prostate cancer",
#                     help="target domain for path novelty/relevance scoring")
#     ap.add_argument("--query",
#                     default="Discover research idea between prostate cancer||Disease and Gene",
#                     help="hypothesis-generation prompt; use 'entity||Type' to pin an entity")
#     ap.add_argument("--out-dir", default="outputs/sage_kg")
#     ap.add_argument("--run-name", default="hypothesis_run")
#     ap.add_argument("--no-timestamp", action="store_true",
#                     help="don't append a timestamp to the run name (overwrites prior run)")
#     args = ap.parse_args()

#     load_env()
#     if not os.environ.get("OPENAI_API_KEY"):
#         raise SystemExit("OPENAI_API_KEY not set (put it in .env)")

#     graph_path = Path(args.graph)
#     data_dir = graph_path.parent
#     graph_name = graph_path.name
#     out_dir = Path(args.out_dir)
#     make_dir_if_needed(out_dir)

#     try:
#         # 1) Embedding model (BGE-large) — same model the KG fusion used.
#         print(f"[1/4] Loading embedding model {EMBED_MODEL} ...")
#         embedding_tokenizer = AutoTokenizer.from_pretrained(EMBED_MODEL)
#         embedding_model = AutoModel.from_pretrained(EMBED_MODEL)

#         # 2) Load graph -> giant component (path reasoning needs one connected graph).
#         print(f"[2/4] Loading graph {graph_path} ...")
#         G = load_graph_with_text_as_JSON(data_dir=str(data_dir), graph_name=graph_name)
#         G = return_giant_component_of_graph(G)
#         G = nx.Graph(G)  # SciAgents traverses an undirected view
#         print(f"      giant component: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

#         # 3) Node embeddings — cached to .pkl, regenerated only if missing.
#         embed_pkl = data_dir / f"embeddings-{graph_path.stem}-bge_large_en_v1.5.pkl"
#         try:
#             node_embeddings = load_embeddings(str(embed_pkl))
#             print(f"[3/4] Loaded cached node embeddings: {embed_pkl}")
#         except Exception:
#             print(f"[3/4] Generating node embeddings (BGE, ~5-10 min for thousands of nodes)...")
#             node_embeddings = generate_node_embeddings(G, embedding_tokenizer, embedding_model)
#             save_embeddings(node_embeddings, str(embed_pkl))
#             print(f"      saved -> {embed_pkl}")

#         # 4) Build the agent flow and run.
#         print(f"[4/4] Running SciAgentsGraphFlow (domain='{args.domain}')")
#         print(f"      query: {args.query}")
#         sci_agents = SciAgentsGraphFlow(
#             G=G,
#             embedding_tokenizer=embedding_tokenizer,
#             embedding_model=embedding_model,
#             node_embeddings=node_embeddings,
#             target_domain=args.domain,
#         )

#         # Timestamp the run name so prior hypothesis runs are preserved.
#         run_name = args.run_name
#         if not args.no_timestamp:
#             from datetime import datetime
#             run_name = f"{run_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
#         save_to = out_dir / f"{run_name}.txt"
#         await sci_agents.run_with_console(message=args.query, save_to=save_to)
#         print(f"\nDone. Run saved to: {save_to} (+ .md / .pdf report)")

#         _emit_metrics(time.time() - t0, extra={"graph": graph_name, "domain": args.domain})
#     except Exception:
#         # Still report timing/whatever tokens were captured before the failure,
#         # so a failed run isn't a total blank in the metrics history.
#         _emit_metrics(time.time() - t0, extra={"failed": True})
#         raise


# if __name__ == "__main__":
#     asyncio.run(main())