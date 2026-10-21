import asyncio
import json
import re
from typing import Dict, List, Optional

import numpy as np
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage
from autogen_ext.models.openai import OpenAIChatCompletionClient
import openai

from HypothesisGeneration.utils import (
    arxiv_search,
    multi_source_literature_search,
    openalex_search,
    pubmed_search,
    semanticscholar_search,
)

# ==================== Helper Functions ====================

# Known models that autogen recognizes - no model_info needed
KNOWN_OPENAI_MODELS = [
    "gpt-5", "gpt-5-mini", "gpt-5-nano",
    "gpt-4o", "gpt-4o-mini", "gpt-4", "gpt-4-turbo", "gpt-3.5-turbo",
    "o1", "o1-mini", "o1-preview", "o3", "o3-mini"
]

# Pricing per 1M tokens (USD). Keys are matched by longest prefix first.
MODEL_PRICING = {
    "gpt-5-nano":    {"input": 0.10,  "output": 0.40},
    "gpt-5-mini":    {"input": 0.25,  "output": 2.00},
    "gpt-5":         {"input": 1.25,  "output": 10.0},
    "gpt-4o-mini":   {"input": 0.15,  "output": 0.60},
    "gpt-4o":        {"input": 2.50,  "output": 10.0},
    "gpt-4-turbo":   {"input": 10.0,  "output": 30.0},
    "gpt-4":         {"input": 30.0,  "output": 60.0},
    "gpt-3.5-turbo": {"input": 0.50,  "output": 1.50},
}

def _extract_usage(response) -> Dict[str, int]:
    """Sum prompt/completion tokens across all messages in an autogen TaskResult."""
    prompt_tokens = 0
    completion_tokens = 0
    for msg in (response.messages or []):
        usage = getattr(msg, 'models_usage', None)
        if usage:
            prompt_tokens += getattr(usage, 'prompt_tokens', 0) or 0
            completion_tokens += getattr(usage, 'completion_tokens', 0) or 0
    return {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}

def _calculate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> Optional[float]:
    """Return estimated cost in USD, or None if model pricing is unknown."""
    pricing = None
    for model_key in sorted(MODEL_PRICING.keys(), key=len, reverse=True):
        if model.lower().startswith(model_key):
            pricing = MODEL_PRICING[model_key]
            break
    if pricing is None:
        return None
    return (prompt_tokens / 1_000_000) * pricing["input"] + (completion_tokens / 1_000_000) * pricing["output"]


async def _run_agent_with_retry(agent, task: str, max_retries: int = 3, base_delay: float = 5.0):
    """Run agent.run(task) with exponential backoff on OpenAI connection/SSL errors."""
    for attempt in range(max_retries):
        try:
            return await agent.run(task=task)
        except (openai.APIConnectionError, openai.APITimeoutError) as e:
            if attempt < max_retries - 1:
                wait = base_delay * (2 ** attempt)
                print(f"   [Retry {attempt + 1}/{max_retries}] {type(e).__name__}. Retrying in {wait:.0f}s...")
                await asyncio.sleep(wait)
            else:
                print(f"   [Retry] All {max_retries} attempts failed. Giving up.")
                raise


def _create_openai_client(model: str, temperature: Optional[float] = None) -> OpenAIChatCompletionClient:
    """
    Create OpenAIChatCompletionClient with model_info for unknown models.
    This handles newer models like gpt-5.2 that autogen doesn't recognize yet.
    """
    # Check if model is known to autogen
    is_known_model = any(known in model.lower() for known in KNOWN_OPENAI_MODELS)

    # Model info for unknown models (gpt-5, gpt-5.2, etc.)
    model_info = None
    if not is_known_model:
        model_info = {
            "family": "gpt",
            "vision": True,
            "function_calling": True,
            "json_output": True,
        }

    # GPT-5 and some other models don't support custom temperature
    model_supports_temperature = not any(
        x in model.lower() for x in ["gpt-5", "gpt5", "o1", "o3"]
    )

    if temperature is not None and model_supports_temperature:
        if model_info:
            return OpenAIChatCompletionClient(model=model, temperature=temperature, model_info=model_info)
        else:
            return OpenAIChatCompletionClient(model=model, temperature=temperature)
    else:
        if model_info:
            return OpenAIChatCompletionClient(model=model, model_info=model_info)
        else:
            return OpenAIChatCompletionClient(model=model)


# ==================== Liang MAD System Prompts ====================

PROVER_SYSTEM = """\
You are the PROVER in a sequential scientific debate about research hypothesis novelty.

YOUR ROLE: Make the strongest possible case for why this hypothesis is BOTH novel and biologically/scientifically plausible.

YOUR STRATEGY:
1. Use your literature search tool (1-3 targeted queries) to find SUPPORTING evidence.
2. THE "NOVELTY GAP" RULE (CRITICAL): If you cannot find direct literature connecting the components of your hypothesis, you are STRICTLY FORBIDDEN from simply arguing "this represents a research gap therefore it is novel."
3. PROVE PLAUSIBILITY: When direct evidence is absent, you MUST defend the biological/mechanistic plausibility of your idea. Use indirect evidence, parallel pathways, homologous structures, or established principles from related fields to logically deduce why your proposed mechanism would work.
4. Distinguish this hypothesis from the closest prior work.
5. Counter the Verifier's challenges with specific, deduced evidence, not just rhetoric.

Output: Plain argumentative text (no JSON). Be specific, cite indirect/direct search results, and clearly map out your logical deductions.
"""

VERIFIER_SYSTEM = """\
You are the VERIFIER in a sequential scientific debate about research hypothesis novelty.

YOUR ROLE: Rigorously challenge both the novelty and the PLAUSIBILITY of the Prover's claims.

YOUR STRATEGY:
1. DECOMPOSE & SEARCH: Do not just search for the exact, full hypothesis. Use your literature search tool (1-3 targeted queries) to decompose it into pairs (e.g., Molecule X + Mechanism Y, or Mechanism Y + Phenotype Z).
2. ATTACK THE MISSING LINK (CRITICAL): If there is no direct prior work, DO NOT just say "I found no studies." Instead, evaluate the biological/physical plausibility of the Prover's deduction. Are there known behaviors of these individual components that logically CONTRADICT the proposed connection?
3. Scenarios:
   A) SPECIOUS/IMPLAUSIBLE: Literature on isolated components strongly suggests they would NOT interact as proposed -> challenge the biological viability strongly.
   B) NOVEL BUT PLAUSIBLE: No direct evidence, but the underlying physics/biology of the separate parts could theoretically support the link -> acknowledge plausibility but point out the lack of empirical proof.
4. Identify the most similar existing work and explain precisely how the new hypothesis overlaps or diverges.

Output: Plain argumentative text (no JSON). Be specific, cite your decomposed search results, and attack weak logical leaps.
"""

JUDGE_SYSTEM = """\
You are the JUDGE in a sequential scientific debate about research hypothesis novelty.

YOUR ROLE: Deliver an impartial, rigorously comprehensive verdict based on the quality of arguments from both sides.

YOUR STANDARDS & RULES:
1. WEIGH EVIDENCE: Judge based on evidence quality and biological plausibility, not rhetorical force.
2. NOVELTY PARADOX:
   - If Verifier found a DIRECT CONTRADICTION with citation -> lower score (<=4).
   - If Verifier found only "absence of evidence" (no papers) AND Prover successfully defended its biological plausibility -> protect novelty (7-9).
   - If Prover failed to provide a plausible logical deduction for the missing link -> lower score (<=5).
3. COMPREHENSIVENESS CHECK (CRITICAL):
   - You MUST extract all distinct components of the original hypothesis (e.g., specific molecules, multiple mechanisms like migration/immunity, spatial phenotypes, or physics concepts).
   - The Prover is NOT allowed to cherry-pick. They must defend the novelty and plausibility of the ENTIRE hypothesis, not just a fragment of it.
4. PENALTY RULE:
   - MISSING COMPONENTS: If the Prover dodges or fails to address core components of the hypothesis, you MUST cap the final score at 5 (REVISE).
   - ANTI-EVASION (SEVERE): If the Prover silently drops, ignores, or alters the most controversial/implausible components of the original hypothesis (e.g., ignoring "quantum entanglement" to focus only on standard neuroscience) to make it easier to defend, this is considered scientific misconduct. You MUST immediately issue a REJECT verdict with a score of <=3.
5. SCORE RANGE: 1-10 (>=6: APPROVE, 4-5.9: REVISE, <4: REJECT).

OUTPUT FORMAT -- always output exactly one JSON block:

If debate should CONTINUE (arguments still evolving, or Prover missed components):
```json
{"final_verdict": "", "continue_reason": "<Why more debate is needed. Explicitly list any hypothesis components the Prover ignored and command them to address them>"}
```

If you can make a FINAL DECISION (max rounds reached or consensus achieved):
```json
{
  "final_verdict": "APPROVE" | "REVISE" | "REJECT",
  "score": <int 1-10>,
  "confidence": <float 0-1>,
  "rationale": "<Your reasoning, specifically noting if the Prover attempted to evade absurd claims>",
  "unaddressed_components": ["<List any original hypothesis claims the Prover completely ignored or dropped>"],
  "prover_wins": ["<claims that survived>"],
  "verifier_wins": ["<claims that were refuted>"],
  "verifier_had_real_contradiction": <true|false>
}
```
"""


# ==================== Debate Engine Helpers ====================

def _parse_judge(text: str) -> Dict:
    """Extract the JSON block from Judge's response."""
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Fallback: bare JSON
    try:
        s = text.find("{")
        e = text.rfind("}") + 1
        if s != -1 and e > s:
            return json.loads(text[s:e])
    except json.JSONDecodeError:
        pass
    return {"final_verdict": "", "continue_reason": "parse_failed"}


def _build_prover_task(hypothesis: str, round_idx: int,
                       prover_history: List[str], verifier_history: List[str]) -> str:
    if round_idx == 0:
        return (
            f"## Hypothesis:\n{hypothesis}\n\n"
            "## Your Task:\n"
            "Search the literature and make the strongest possible case for why this hypothesis "
            "is novel and scientifically significant. Be specific about mechanisms and cite evidence."
        )
    verifier_latest = verifier_history[-1][:1500] if verifier_history else "(none yet)"
    own_history = "\n\n".join(
        f"[Round {i}]:\n{t[:600]}" for i, t in enumerate(prover_history)
    )
    return (
        f"## Hypothesis:\n{hypothesis}\n\n"
        f"## Your Previous Arguments:\n{own_history}\n\n"
        f"## Verifier's Latest Challenge (Round {round_idx - 1}):\n{verifier_latest}\n\n"
        "## Your Task:\n"
        "Rebut the Verifier's challenge with specific evidence. "
        "Search for new supporting literature if needed."
    )


def _build_verifier_task(hypothesis: str, round_idx: int,
                         prover_history: List[str], verifier_history: List[str]) -> str:
    prover_latest = prover_history[-1][:1500] if prover_history else "(none yet)"
    own_history = "\n\n".join(
        f"[Round {i}]:\n{t[:600]}" for i, t in enumerate(verifier_history)
    )
    history_block = f"## Your Previous Challenges:\n{own_history}\n\n" if own_history else ""
    return (
        f"## Hypothesis:\n{hypothesis}\n\n"
        f"{history_block}"
        f"## Prover's Latest Argument (Round {round_idx}):\n{prover_latest}\n\n"
        "## Your Task:\n"
        "Challenge the Prover's claims. Search for prior work or direct contradictions. "
        "Be rigorous but fair -- distinguish real contradictions from mere absence of evidence."
    )


def _build_judge_task(hypothesis: str, round_idx: int,
                      prover_history: List[str], verifier_history: List[str],
                      force_continue: bool = False) -> str:
    prover_block = "\n\n".join(f"[Round {i}]:\n{t[:800]}" for i, t in enumerate(prover_history))
    verifier_block = "\n\n".join(f"[Round {i}]:\n{t[:800]}" for i, t in enumerate(verifier_history))

    if force_continue:
        verdict_instruction = (
            "WARNING: MANDATORY: This is Round 0. The Prover has NOT yet had a chance to rebut the "
            "Verifier's challenges. You MUST output the 'continue' format -- do NOT issue a final verdict yet:\n"
            '```json\n{"final_verdict": "", "continue_reason": "<what unresolved points need a rebuttal round>"}\n```'
        )
    else:
        verdict_instruction = (
            "Deliver your verdict. If the debate is clearly resolved, output a final decision. "
            "If key points remain unresolved and another round would help, continue. "
            "Always output exactly one JSON block as specified in your system prompt."
        )

    return (
        f"## Hypothesis:\n{hypothesis}\n\n"
        f"## Prover's Arguments (all rounds):\n{prover_block}\n\n"
        f"## Verifier's Arguments (all rounds):\n{verifier_block}\n\n"
        f"## Round {round_idx} Assessment:\n"
        f"{verdict_instruction}"
    )


# ==================== MultiCriticCoordinator ====================

class MultiCriticCoordinator:
    def __init__(
        self,
        base_model: str = "gpt-5-mini",
        literature_sources: List[str] = None,
        simulation_date: Optional[str] = None
    ):
        """

        Uses a sequential Prover -> Verifier -> Judge debate to assess hypothesis novelty.

        Args:
            base_model: Model name to use
            literature_sources: List of literature search sources (reserved for future use)
            simulation_date: Optional date for "Time Travel" backtesting mode, format "YYYY-MM".
                           If set, the system will:
                           1. Filter out papers published after this date
                           2. Instruct agents to ignore knowledge from after this date
                           Example: simulation_date="2019-06" to test pre-July 2019 era
        """
        self.base_model = base_model
        self.simulation_date = simulation_date
        self.references_retrieved = 0
        self.reference_search_calls = 0

        # Parse simulation_date for display
        if simulation_date:
            try:
                sim_year, sim_month = simulation_date.split('-')
                sim_year = int(sim_year)
                sim_month = int(sim_month)
                month_names = ['January', 'February', 'March', 'April', 'May', 'June',
                              'July', 'August', 'September', 'October', 'November', 'December']
                month_name = month_names[sim_month - 1]
            except (ValueError, IndexError):
                sim_year = simulation_date
                sim_month = 12
                month_name = "December"

            print(f"\n{'='*80}")
            print(f"TIME TRAVEL BACKTESTING MODE ACTIVATED")
            print(f"{'='*80}")
            print(f"   Simulation Date: {month_name} {sim_year}")
            print(f"   Papers after {simulation_date} will be FILTERED OUT")
            print(f"   Agents will IGNORE knowledge from after {month_name} {sim_year}")
            print(f"{'='*80}\n")

        # Default sources (reserved for future extension)
        self.literature_sources = literature_sources or [
            "semantic_scholar",
            "pubmed",
            "arxiv",
            "biorxiv",
            "openalex",
            "europepmc",
            "crossref",
        ]

        # Create Liang MAD agents
        self.prover, self.verifier, self.judge = self._create_liang_agents()

    def _create_liang_agents(self):
        """Create the 3 debate agents: Prover, Verifier, Judge."""

        # Build temporal amnesia prompt if simulation_date is set
        temporal_prefix = ""
        if self.simulation_date:
            try:
                sim_year, sim_month = self.simulation_date.split('-')
                sim_year = int(sim_year)
                sim_month = int(sim_month)
                month_names = ['January', 'February', 'March', 'April', 'May', 'June',
                              'July', 'August', 'September', 'October', 'November', 'December']
                month_name = month_names[sim_month - 1]
                date_display = f"{month_name} {sim_year}"
                next_month = f"{month_names[sim_month] if sim_month < 12 else 'January'} {sim_year if sim_month < 12 else sim_year + 1}"
            except (ValueError, IndexError):
                date_display = str(self.simulation_date)
                next_month = "after this date"

            temporal_prefix = f"""
TEMPORAL SIMULATION MODE: ACTIVE

CURRENT SIMULATED DATE: {date_display}
CUTOFF: End of {date_display}

You MUST act as if you are in {date_display}. This is CRITICAL for testing.

STRICT RULES:
1. DO NOT assume knowledge of ANY events, papers, discoveries, or breakthroughs after {date_display}.
2. If you internally "know" about a famous paper published in {next_month} or later, you MUST pretend it does NOT exist yet.
3. Base your judgment SOLELY on the search tool results provided - these have been filtered to only show papers from {date_display} and earlier.
4. If you recognize a hypothesis as "similar to [Famous Paper from after {date_display}]", IGNORE that knowledge. You have NOT seen that paper yet.
5. Evaluate novelty based ONLY on what was known/published up to {date_display}.

"""

        agent_configs = [
            {
                "name": "Prover",
                "system": PROVER_SYSTEM,
                "sources": ["semantic_scholar", "biorxiv", "arxiv"],
                "temp": 0.9,
            },
            {
                "name": "Verifier",
                "system": VERIFIER_SYSTEM,
                "sources": ["pubmed", "europepmc"],
                "temp": 0.3,
            },
            {
                "name": "Judge",
                "system": JUDGE_SYSTEM,
                "sources": ["pubmed", "semantic_scholar", "crossref"],
                "temp": 0.5,
            },
        ]

        agents = []
        for cfg in agent_configs:
            client = _create_openai_client(self.base_model, temperature=cfg["temp"])
            tool = self._create_search_tool(cfg["sources"])
            system_msg = temporal_prefix + cfg["system"] if temporal_prefix else cfg["system"]

            agent = AssistantAgent(
                name=cfg["name"],
                model_client=client,
                system_message=system_msg,
                max_tool_iterations=10,
                reflect_on_tool_use=True,
                tools=[tool],
            )
            agents.append(agent)

        return agents[0], agents[1], agents[2]

    def _create_search_tool(self, sources: List[str]):
        """
        Create search tool function for specified data sources.
        If simulation_date is set, papers after that date will be filtered out.
        """
        # Capture simulation_date from self for the closure
        date_end = self.simulation_date

        def search_literature(query: str, max_results: int = 10) -> str:
            """
            Search literature (multi-source).
            If simulation_date is active, results are filtered to papers <= that date.
            """
            result = multi_source_literature_search(
                query=query,
                sources=sources,
                max_results_per_source=max_results,
                date_end=date_end
            )
            self.reference_search_calls += 1

            ref_count = len(re.findall(r"\bTitle:", result, re.IGNORECASE))

            self.references_retrieved += ref_count

            return result
    
        search_literature.__name__ = "search_literature"

        return search_literature
    
    def _calculate_consensus_score(self, verdict: Dict) -> float:
        """
        Estimate inter-reviewer agreement from debate outcome.

        Higher score = stronger agreement between reviewers.
        """
        score = verdict.get("score", 5)
        confidence = verdict.get("confidence", 0.5)
        verifier_had_real = verdict.get("verifier_had_real_contradiction", False)

        prover_wins = len(verdict.get("prover_wins", []))
        verifier_wins = len(verdict.get("verifier_wins", []))

        total_claims = prover_wins + verifier_wins

        if total_claims == 0:
            balance_agreement = 0.5
        else:
            dominant = max(prover_wins, verifier_wins)
            balance_agreement = dominant / total_claims

        contradiction_penalty = 0.15 if verifier_had_real else 0.0

        consensus = (0.6 * confidence) + (0.4 * balance_agreement) - contradiction_penalty

        return round(max(0.0, min(1.0, consensus)), 3)

    async def evaluate(self, hypothesis: str) -> Dict:
        """Evaluate a hypothesis using the sequential debate."""
        print("Starting novelty evaluation (Prover -> Verifier -> Judge)...")
        return await self.run_liang_debate(hypothesis)

    async def run_liang_debate(
        self,
        hypothesis: str,
        max_rounds: int = 3,
        min_rounds: int = 2,
        verbose: bool = True,
    ) -> Dict:
        """
        Sequential Prover -> Verifier -> Judge debate

        min_rounds: minimum number of full Prover->Verifier->Judge cycles before
                    the Judge is allowed to issue a final verdict (default=2,
                    so Prover always gets at least one rebuttal round).
        """
        prover_history:   List[str] = []
        verifier_history: List[str] = []
        debate_log:       List[Dict] = []
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        verdict = {}

        self.references_retrieved = 0
        self.reference_search_calls = 0

        def _log(msg):
            if verbose:
                print(msg)

        _log("")
        _log("=" * 80)
        _log("Prover -> Verifier -> Judge")
        _log("=" * 80)
        _log(f"Hypothesis: {hypothesis[:120]}..." if len(hypothesis) > 120 else f"Hypothesis: {hypothesis}")
        _log(f"Max rounds: {max_rounds}  |  Min rounds before verdict: {min_rounds}")

        for round_idx in range(max_rounds):
            _log("")
            _log("=" * 80)
            _log(f"ROUND {round_idx}")
            _log("=" * 80)
            round_record = {"round": round_idx, "prover": "", "verifier": "", "judge": {}, "verdict": ""}

            # -- Step 1: Prover --
            _log("\n[Prover] arguing...")
            prover_task = _build_prover_task(hypothesis, round_idx, prover_history, verifier_history)
            prover_resp = await _run_agent_with_retry(self.prover, prover_task)
            prover_text = prover_resp.messages[-1].content if prover_resp.messages else ""
            prover_history.append(prover_text)
            round_record["prover"] = prover_text
            tok = _extract_usage(prover_resp)
            usage["prompt_tokens"] += tok.get("prompt_tokens", 0)
            usage["completion_tokens"] += tok.get("completion_tokens", 0)
            _log(f"[Prover] ({len(prover_text)} chars)")
            _log("-" * 40)
            _log(prover_text[:600])

            # -- Step 2: Verifier --
            _log("\n[Verifier] challenging...")
            verifier_task = _build_verifier_task(hypothesis, round_idx, prover_history, verifier_history)
            verifier_resp = await _run_agent_with_retry(self.verifier, verifier_task)
            verifier_text = verifier_resp.messages[-1].content if verifier_resp.messages else ""
            verifier_history.append(verifier_text)
            round_record["verifier"] = verifier_text
            tok = _extract_usage(verifier_resp)
            usage["prompt_tokens"] += tok.get("prompt_tokens", 0)
            usage["completion_tokens"] += tok.get("completion_tokens", 0)
            _log(f"[Verifier] ({len(verifier_text)} chars)")
            _log("-" * 40)
            _log(verifier_text[:600])

            # -- Step 3: Judge --
            # Before min_rounds, Judge is forced to continue (Prover needs rebuttal chance)
            force_continue = (round_idx < min_rounds - 1)
            _log(f"\n[Judge] deliberating... {'(forced continue -- min_rounds not reached)' if force_continue else ''}")
            judge_task = _build_judge_task(hypothesis, round_idx, prover_history, verifier_history,
                                           force_continue=force_continue)
            judge_resp = await _run_agent_with_retry(self.judge, judge_task)
            judge_text = judge_resp.messages[-1].content if judge_resp.messages else ""
            verdict = _parse_judge(judge_text)

            # Code-level enforcement: override any early verdict before min_rounds
            if force_continue and verdict.get("final_verdict", ""):
                _log(f"   Judge tried to decide early -- overriding (min_rounds={min_rounds})")
                verdict = {"final_verdict": "", "continue_reason": f"min_rounds={min_rounds} not reached"}

            round_record["judge"] = verdict
            round_record["verdict"] = verdict.get("final_verdict", "")
            tok = _extract_usage(judge_resp)
            usage["prompt_tokens"] += tok.get("prompt_tokens", 0)
            usage["completion_tokens"] += tok.get("completion_tokens", 0)
            debate_log.append(round_record)

            _log(f"[Judge] verdict={verdict.get('final_verdict', '(continue)')}  "
                 f"score={verdict.get('score', '?')}  "
                 f"confidence={verdict.get('confidence', '?')}")

            if verdict.get("final_verdict", ""):
                _log(f"   CONVERGENCE at Round {round_idx}")
                break
            else:
                _log(f"   Continue reason: {verdict.get('continue_reason', '?')}")

        # -- Ultimate fallback --
        if not verdict.get("final_verdict", ""):
            _log("")
            _log("=" * 80)
            _log("ULTIMATE FALLBACK -- Final Judge call")
            _log("=" * 80)
            fallback_task = (
                _build_judge_task(hypothesis, max_rounds, prover_history, verifier_history)
                + "\n\nThis is the FINAL round. You MUST output a definitive verdict (no empty final_verdict)."
            )
            fallback_resp = await self.judge.run(task=fallback_task)
            fallback_text = fallback_resp.messages[-1].content if fallback_resp.messages else ""
            verdict = _parse_judge(fallback_text)
            tok = _extract_usage(fallback_resp)
            usage["prompt_tokens"] += tok.get("prompt_tokens", 0)
            usage["completion_tokens"] += tok.get("completion_tokens", 0)
            debate_log.append({"round": "fallback", "judge": verdict, "verdict": verdict.get("final_verdict", "REVISE")})
            _log(f"[Fallback Judge] verdict={verdict.get('final_verdict')}  score={verdict.get('score')}")

        # -- Summary --
        final_verdict = verdict.get("final_verdict", "REVISE")
        final_score = verdict.get("score", 5)
        final_conf = verdict.get("confidence", 0.5)
        consensus_score = self._calculate_consensus_score(verdict)

        _log("")
        _log("=" * 80)
        _log("DEBATE CONCLUDED")
        _log(f"   Verdict:    {final_verdict}")
        _log(f"   Score:      {final_score}/10")
        _log(f"   Confidence: {final_conf}")
        _log(f"   Rounds run: {len(debate_log)}")
        _log(f"   Rationale:  {verdict.get('rationale', '')[:200]}")
        _log("=" * 80)

        return {
            "method": "mad_sequential",
            "final_verdict": final_verdict,
            "score": final_score,
            "confidence": final_conf,
            "rationale": verdict.get("rationale", ""),
            "unaddressed_components": verdict.get("unaddressed_components", []),
            "prover_wins": verdict.get("prover_wins", []),
            "verifier_wins": verdict.get("verifier_wins", []),
            "verifier_had_real_contradiction": verdict.get("verifier_had_real_contradiction", False),
            "rounds": len(debate_log),
            "debate_log": debate_log,
            "usage": usage,
            "model": self.base_model,
            "consensus_score": consensus_score,
            "references_retrieved": self.references_retrieved,
            "reference_search_calls": self.reference_search_calls,
        }

    async def run_backtest(self, hypothesis: str, date: str) -> Dict:
        """
        Convenience method for running "Time Travel" backtesting.

        Args:
            hypothesis: The hypothesis to evaluate
            date: The simulation date in "YYYY-MM" format (e.g., "2019-06" for June 2019)

        Returns:
            Evaluation result with the hypothesis assessed as if at the specified date
        """
        # Parse date for display
        try:
            sim_year, sim_month = date.split('-')
            month_names = ['January', 'February', 'March', 'April', 'May', 'June',
                          'July', 'August', 'September', 'October', 'November', 'December']
            month_name = month_names[int(sim_month) - 1]
            date_display = f"{month_name} {sim_year}"
        except (ValueError, IndexError):
            date_display = date

        print(f"\n{'='*80}")
        print(f"INITIATING TIME TRAVEL BACKTEST")
        print(f"{'='*80}")
        print(f"   Target Date: {date_display} ({date})")
        print(f"   Hypothesis: {hypothesis[:100]}...")
        print(f"{'='*80}\n")

        # Store original simulation_date
        original_date = self.simulation_date

        # Set new simulation date
        self.simulation_date = date

        # Recreate Liang agents with the new temporal constraints
        self.prover, self.verifier, self.judge = self._create_liang_agents()

        try:
            # Run evaluation
            result = await self.evaluate(hypothesis)

            # Add backtest metadata
            result["backtest_metadata"] = {
                "simulation_date": date,
                "simulation_date_display": date_display,
                "hypothesis_preview": hypothesis[:200] + "..." if len(hypothesis) > 200 else hypothesis,
                "mode": "time_travel_backtest"
            }

            return result

        finally:
            # Restore original simulation_date
            self.simulation_date = original_date
            # Recreate agents with original settings
            self.prover, self.verifier, self.judge = self._create_liang_agents()


class MultiCriticCoordinatorAgent(AssistantAgent):
    def __init__(self, coordinator: MultiCriticCoordinator):
        self.coordinator = coordinator

        # Use helper function that handles model_info for unknown models
        model_client = _create_openai_client(coordinator.base_model, temperature=0.5)

        super().__init__(
            name="multi_critic_coordinator",
            model_client=model_client,
            system_message="Multi-reviewer coordination system",
            description="Multi-reviewer coordination system with multi-agent debate capability",
        )

    async def on_messages_stream(self, messages, cancellation_token):
        """Process message stream (RoundRobinGroupChat actually calls this method)"""
        from autogen_agentchat.base import Response
        from autogen_agentchat.messages import TextMessage

        hypothesis = messages[-1].content

        print(f"\n{'=' * 80}")
        print(f"MultiCriticCoordinator: Starting multi-agent debate evaluation...")
        print(f"{'=' * 80}\n")

        # Run the Liang MAD debate
        result = await self.coordinator.evaluate(hypothesis)

        # Format as detailed report
        response_text = self._format_response(result)

        print(f"\n{'=' * 80}")
        print(f"MultiCriticCoordinator: Evaluation complete")
        print(f"{'=' * 80}\n")

        # Must yield Response object, not TextMessage
        yield Response(chat_message=TextMessage(content=response_text, source=self.name))

    async def on_messages(self, messages, cancellation_token):
        """Process messages (non-streaming version)"""
        from autogen_agentchat.base import Response

        response = None
        async for resp in self.on_messages_stream(messages, cancellation_token):
            response = resp

        if response is None:
            raise RuntimeError("No response generated")

        return response

    async def evaluate(self, hypothesis: str) -> Dict:
        """
        Evaluate a hypothesis using the debate.

        Args:
            hypothesis: The hypothesis text to evaluate

        Returns:
            Dict containing evaluation results with verdict, score, and reasoning
        """
        return await self.coordinator.evaluate(hypothesis)

    async def run_backtest(self, hypothesis: str, date: str) -> Dict:
        """
        Run a "Time Travel" backtest evaluation.

        Args:
            hypothesis: The hypothesis text to evaluate
            date: The simulation date in "YYYY-MM" format

        Returns:
            Dict containing evaluation results with backtest metadata
        """
        return await self.coordinator.run_backtest(hypothesis, date)

    def _format_response(self, result: Dict) -> str:
        """Format debate result as detailed review report."""

        output = []

        # ========== 1. Title ==========
        output.append("=" * 80)
        output.append("NOVELTY ASSESSMENT REPORT")
        output.append("=" * 80)
        output.append("")

        # ========== 2. Summary ==========
        final_verdict = result.get("final_verdict", "REVISE")
        score = result.get("score", 5)
        confidence = result.get("confidence", 0.5)
        rounds = result.get("rounds", 0)
        method = result.get("method", "mad_sequential")

        output.append(f"Method:     {method}")
        output.append(f"Verdict:    {final_verdict}")
        output.append(f"Score:      {score}/10")
        output.append(f"Confidence: {confidence}")
        output.append(f"Rounds:     {rounds}")
        output.append("")

        # ========== 3. Rationale ==========
        rationale = result.get("rationale", "")
        if rationale:
            output.append("-" * 80)
            output.append("RATIONALE")
            output.append("-" * 80)
            output.append(rationale)
            output.append("")

        # ========== 4. Debate Rounds ==========
        debate_log = result.get("debate_log", [])
        if debate_log:
            output.append("-" * 80)
            output.append("DEBATE HISTORY")
            output.append("-" * 80)
            output.append("")

            for record in debate_log:
                round_num = record.get("round", "?")
                output.append("=" * 60)
                output.append(f"  Round {round_num}")
                output.append("=" * 60)
                output.append("")

                # Prover
                prover_text = record.get("prover", "")
                if prover_text:
                    output.append("[PROVER] (Optimistic Advocate)")
                    output.append("-" * 40)
                    # Show first 800 chars
                    output.append(prover_text[:800])
                    if len(prover_text) > 800:
                        output.append(f"  ... ({len(prover_text)} chars total)")
                    output.append("")

                # Verifier
                verifier_text = record.get("verifier", "")
                if verifier_text:
                    output.append("[VERIFIER] (Conservative Skeptic)")
                    output.append("-" * 40)
                    output.append(verifier_text[:800])
                    if len(verifier_text) > 800:
                        output.append(f"  ... ({len(verifier_text)} chars total)")
                    output.append("")

                # Judge
                judge_data = record.get("judge", {})
                if judge_data:
                    round_verdict = judge_data.get("final_verdict", "")
                    output.append("[JUDGE] (Neutral Arbiter)")
                    output.append("-" * 40)
                    if round_verdict:
                        output.append(f"  Verdict: {round_verdict}")
                        output.append(f"  Score:   {judge_data.get('score', '?')}")
                        output.append(f"  Confidence: {judge_data.get('confidence', '?')}")
                    else:
                        output.append(f"  Decision: CONTINUE")
                        output.append(f"  Reason:   {judge_data.get('continue_reason', '?')}")
                    output.append("")

        # ========== 5. Prover wins vs Verifier wins ==========
        prover_wins = result.get("prover_wins", [])
        verifier_wins = result.get("verifier_wins", [])
        unaddressed = result.get("unaddressed_components", [])

        if prover_wins or verifier_wins or unaddressed:
            output.append("-" * 80)
            output.append("ARGUMENT OUTCOMES")
            output.append("-" * 80)
            output.append("")

            if prover_wins:
                output.append("Claims that SURVIVED (Prover wins):")
                for claim in prover_wins:
                    output.append(f"  + {claim}")
                output.append("")

            if verifier_wins:
                output.append("Claims that were REFUTED (Verifier wins):")
                for claim in verifier_wins:
                    output.append(f"  - {claim}")
                output.append("")

            if unaddressed:
                output.append("UNADDRESSED components:")
                for comp in unaddressed:
                    output.append(f"  ? {comp}")
                output.append("")

        verifier_had_real = result.get("verifier_had_real_contradiction", False)
        output.append(f"Verifier had real contradiction: {verifier_had_real}")
        output.append("")

        # ========== 6. Token Usage & Cost ==========
        usage = result.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = prompt_tokens + completion_tokens
        if total_tokens > 0:
            model = result.get("model", "unknown")
            cost = _calculate_cost(model, prompt_tokens, completion_tokens)
            output.append("=" * 80)
            output.append("TOKEN USAGE & COST")
            output.append("=" * 80)
            output.append(f"  Model:             {model}")
            output.append(f"  Prompt tokens:     {prompt_tokens:,}")
            output.append(f"  Completion tokens: {completion_tokens:,}")
            output.append(f"  Total tokens:      {total_tokens:,}")
            if cost is not None:
                output.append(f"  Estimated cost:    ${cost:.4f}")
            else:
                output.append(f"  Estimated cost:    N/A (pricing for '{model}' not in MODEL_PRICING)")
            output.append("=" * 80)

        output.append(f"Consensus: {result.get('consensus_score', 0)}")
        output.append(f"References retrieved: {result.get('references_retrieved', 0)}")
        output.append(f"Reference search calls: {result.get('reference_search_calls', 0)}")

        # ========== 7. Final Verdict ==========
        output.append("")
        output.append("=" * 80)
        if final_verdict == "APPROVE" or (isinstance(score, (int, float)) and score >= 6):
            output.append("The hypothesis is APPROVED.")
        elif final_verdict == "REVISE" or (isinstance(score, (int, float)) and score >= 4):
            output.append("The hypothesis needs REVISION to increase novelty.")
        else:
            output.append("The hypothesis is NOT APPROVED due to insufficient novelty.")
        output.append("=" * 80)

        return "\n".join(output)


def create_multi_critic_coordinator(
    base_model: str = "gpt-5-mini",
    model_client: Optional[OpenAIChatCompletionClient] = None,
    literature_sources: List[str] = None,
    simulation_date: Optional[str] = None,
):
    """
    Create a Multi-Critic Coordinator using Multi-Agent Debate.

    The coordinator uses a sequential Prover -> Verifier -> Judge debate
    to rigorously assess hypothesis novelty.

    Args:
        base_model: Model name to use (default gpt-4o)
        model_client: Reserved parameter (for backward compatibility, not used)
        literature_sources: List of literature data sources
        simulation_date: Time Travel mode - Set simulation date (format "YYYY-MM")

    Returns:
        MultiCriticCoordinatorAgent: Coordinator agent with debate
    """
    coordinator = MultiCriticCoordinator(
        base_model=base_model,
        literature_sources=literature_sources,
        simulation_date=simulation_date,
    )
    return MultiCriticCoordinatorAgent(coordinator)