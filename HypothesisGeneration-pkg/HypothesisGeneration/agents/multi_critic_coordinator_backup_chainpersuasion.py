import asyncio
import json
import re
from typing import Dict, List, Optional

import numpy as np
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage
from autogen_ext.models.openai import OpenAIChatCompletionClient

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
    "gpt-4o", "gpt-4o-mini", "gpt-4", "gpt-4-turbo", "gpt-3.5-turbo",
    "o1", "o1-mini", "o1-preview", "o3", "o3-mini"
]

# Pricing per 1M tokens (USD). Keys are matched by longest prefix first.
MODEL_PRICING = {
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

# ==================== Prompt Definitions ====================

# 1. Novelty Critic Prompt (new version with Prior Overlap check)
NOVELTY_CRITIC_PROMPT = """You are an expert biomedical scientist evaluating the SCIENTIFIC NOVELTY of a proposed hypothesis.

Your task is to determine whether the hypothesis contributes a NEW biological mechanism, NEW molecule–phenotype relationship, or NEW spatial/imaging phenotype association, based on real literature.

You must rely ONLY on TOOL-BASED literature evidence.
If you do not use the search tool extensively, your evaluation is invalid.

---

## Available Tool
You MUST use:
- search_literature(query: str, max_results: int = 10)

Use **6–12 tool calls** with diverse queries covering:
- Key molecule(s)
- Proposed pathway or mechanism
- Spatial phenotype (e.g., TLS, TIL margin)
- The combination (molecule + mechanism + phenotype)

---

## STEP 1 — PRIOR WORK OVERLAP CHECK (MANDATORY)

After searching, identify the single most similar published paper and classify overlap:

1. **near-identical**
   Same key molecule + same mechanism + same phenotype → same core finding.

2. **strongly overlapping**
   Shares major components; your hypothesis is a minor variation.

3. **partially overlapping**
   Shares background (cancer type, TME concept) but introduces a NEW:
   - molecule, or
   - mechanistic link, or
   - spatial phenotype, or
   - combination of elements.

4. **distinct**
   No prior paper links the specific molecule → mechanism → phenotype chain.

### Hard rules:
- near-identical → all novelty scores ≤ 3; overall ≤ 3; must REJECT.
- strongly overlapping w/ no new biology → overall ≤ 5.

---

## STEP 2 — NOVELTY DIMENSIONS

Score each from 1–10:

### 1. Technical Novelty
- 9–10: New imaging/spatial quantification / multimodal pipeline
- 7–8: Significant integrative or modeling innovation
- 5–6: Standard methods with some refinement
- 1–4: Common pipelines; minimal technical novelty

### 2. Application Novelty
- 9–10: New clinical angle, rare subgroup, or new spatial phenotype
- 7–8: Moderately new application context
- 5–6: Common cohort but nontrivial analysis
- 1–4: Very common problem studied extensively

### 3. Theoretical/Mechanistic Novelty (MOST IMPORTANT)
Evaluate:
- Are the molecule(s) newly linked to this phenotype?
- Is the proposed mechanism new?
- Is the combination (molecule × mechanism × phenotype) unreported?

Scores:
- 9–10: New mechanistic chain or molecular–spatial link
- 7–8: Meaningful extension of known theory
- 5–6: Known biology in a new phenotype/task
- 1–4: Minimal mechanism novelty

---

## STEP 3 — OVERALL NOVELTY (equal weights)

overall_novelty_score = round(
  0.33 * technical +
  0.33 * application +
  0.33 * theoretical
)

### Override rules:
- If molecular_novelty ≥ 8 AND mechanistic_link_novelty ≥ 8 AND overlap_category in {"partially overlapping","distinct"} → final score ≥ 7.
- If near-identical → final ≤ 3.
- If strongly overlapping AND molecular_novelty ≤ 4 AND mechanistic_link_novelty ≤ 4 → final ≤ 5.

Show the weighted formula and caps in `overall_assessment.calculation`.

---

## OUTPUT FORMAT (STRICT JSON)

```json
{
  "prior_overlap_assessment": {
    "overlap_category": "",
    "most_similar_paper": "",
    "rationale": ""
  },
  "literature_search_summary": {
    "total_searches_performed": 0,
    "key_queries_used": [],
    "total_relevant_papers_found": 0,
    "most_similar_papers": [],
    "search_sources_used": []
  },
  "novelty_breakdown": {
    "technical_novelty": {"score": 1, "rationale": ""},
    "application_novelty": {"score": 1, "rationale": ""},
    "theoretical_novelty": {"score": 1, "rationale": ""}
  },
  "overall_assessment": {
    "overall_novelty_score": 1,
    "calculation": "",
    "key_innovations": [],
    "overlaps_with_existing_work": []
  },
  "references": ["<list of ALL URLs from search results>"],
  "recommendation": "APPROVE/REVISE/REJECT"
}
```

**CRITICAL - References Extraction (MANDATORY):**

You MUST follow these steps EXACTLY:

1. **After each search tool call**, immediately scan the results for URLs
2. **Extract EVERY URL** from lines containing:
   - "URL: https://..."
   - "DOI: 10..."
   - "https://www.semanticscholar.org/paper/..."
   - "https://pubmed.ncbi.nlm.nih.gov/..."
   - Any other http:// or https:// link

3. **Add ALL extracted URLs** to your references list
   - Minimum: 15 URLs (if you found papers)
   - Maximum: 30 URLs
   - For DOIs, convert to: "https://doi.org/10.xxxx/yyyy"

4. **If you report finding N papers** in "total_relevant_papers_found", you MUST include at least N URLs in "references"
   - Example: If you say "found 30 papers", you must list at least 15-30 URLs

5. **Do NOT**:
   - Fabricate or invent URLs
   - Skip URLs just because a paper seems "less relevant"
   - Summarize or group URLs - list them individually

**VERIFICATION**: Before submitting your JSON, count the URLs in "references" and compare with "total_relevant_papers_found". They should be similar.
"""


NOVELTY_CRITIC_DESCRIPTION = (
    "Novelty critic agent for multi-dimensional evaluation of research proposal novelty in computational pathology."
)

COORDINATOR_PROMPT = """You are a research proposal review coordinator.

You manage multiple independent reviewers and synthesize their assessments.

Your workflow:

Receive a hypothesis for evaluation

Coordinate 3 independent reviewers (conservative, balanced, optimistic)

If scores disagree significantly (std > 2.0), initiate debate

Synthesize final consensus assessment

Output format:

{
  "review_process": {
    "method": "voting" or "debate",
    "individual_scores": [6, 7, 8],
    "score_statistics": {
      "mean": 7.0,
      "std": 0.82,
      "disagreement_level": "low/medium/high"
    }
  },
  "final_assessment": {
    "consensus_score": 7,
    "confidence": 0.91,
    "recommendation": "APPROVE/REVISE/REJECT",
    "synthesis_rationale": "..."
  }
}


After outputting JSON, say: "The hypothesis is APPROVED." (or other statement based on score).
"""

# ==================== Game-Theoretic Bayesian Debate Prompts ====================

PROVER_PROMPT = """🎯 YOU ARE THE PROVER (Optimistic Advocate)

Your role in this Bayesian Decoding Game is to PROVE the hypothesis has MAXIMUM potential novelty.

## Your Mission
You are the defense attorney for this hypothesis. Your job is to:
1. Highlight the STRONGEST novel aspects
2. Argue for the highest defensible score (aim for 9-10 if possible)
3. Identify what makes this hypothesis UNIQUE and IMPACTFUL
4. Temporarily set aside minor flaws - focus on POTENTIAL

## Your Strategy
- Emphasize NEW molecular-mechanism-phenotype combinations
- Highlight clinical/translational potential
- Point out gaps in existing literature that this hypothesis fills
- Argue why "similar" papers are actually DIFFERENT in key ways

## CRITICAL: You must output your argument in this EXACT JSON format:

```json
{{
  "role_action": "PROVE",
  "argument": "<Your detailed argument for why this hypothesis is highly novel - be specific about mechanisms, molecules, phenotypes>",
  "key_novel_claims": ["<claim 1>", "<claim 2>", "<claim 3>"],
  "suggested_score": <float 1.0-10.0>,
  "confidence": <float 0.0-1.0>,
  "evidence_strength": "<weak/moderate/strong>",
  "prior_work_differentiation": "<How this differs from the most similar prior work>"
}}
```

## Scoring Guidelines for Prover:
- 9-10: Groundbreaking - new mechanism chain or molecular-spatial link never reported
- 7-8: Significant - meaningful extension with clear novel elements
- 5-6: Moderate - builds on known biology with some new aspects
- Below 5: You should still try to find value, but be honest if novelty is limited

Remember: Your job is to make the STRONGEST possible case. The Verifier will challenge you.
"""

VERIFIER_PROMPT = """🔍 YOU ARE THE VERIFIER (Conservative Skeptic)

Your role in this Bayesian Decoding Game is to VERIFY or REFUTE the Prover's claims.

## Your Mission
You are the prosecutor. Your job is to:
1. Detect "SPECIOUS" novelty - claims that SOUND novel but are actually CONTRADICTED by evidence
2. Find the CLOSEST prior work and assess how it affects novelty claims
3. Challenge each "novel" claim with specific counter-evidence
4. CRITICALLY: Distinguish between "Absence of Evidence" and "Evidence of Absence"

## ⚠️ CRITICAL DISTINCTION - THE NOVELTY PARADOX ⚠️

You MUST distinguish between these two scenarios:

### Scenario A: SPECIOUS (Evidence of Absence) → Flag as specious
- Literature DIRECTLY CONTRADICTS the proposed mechanism
- The logic chain contains a SCIENTIFIC FALLACY
- A published paper PROVES the hypothesis is wrong
- Example: "Paper X shows that molecule A actually INHIBITS pathway B, not activates it"

### Scenario B: NOVEL (Absence of Evidence) → Do NOT flag as specious
- You cannot find direct literature about this specific combination
- The logic chain is SCIENTIFICALLY PLAUSIBLE based on indirect evidence
- No paper DISPROVES the hypothesis, you just haven't found one that PROVES it
- Example: "No paper directly links molecule A to phenotype B, but the pathway logic is sound"

**IMPORTANT**: If you cannot find direct evidence, do NOT automatically flag as Specious.
Only flag 'is_specious: true' if there is a DIRECT CONTRADICTION or a LOGICAL FALLACY.
"I couldn't find papers about this" is NOT a reason to flag specious - it might mean it's genuinely NOVEL!

## The Prover's Argument You Must Attack:
{prover_argument}

## Your Strategy
- For each "key_novel_claim", search for prior work
- If you find CONTRADICTING evidence → is_specious: true, contradiction_level: "high"
- If you find PARTIAL overlap but no contradiction → is_specious: false, contradiction_level: "medium"
- If you find NO evidence either way → is_specious: false, contradiction_level: "none"
- Identify if the hypothesis is:
  - near-identical to existing work (score must be ≤3)
  - strongly overlapping with minor variations (score must be ≤5)
  - partially overlapping but with genuine new elements (score 5-7)
  - truly distinct (acknowledge it and score fairly 7-9)

## ⚠️ CRITICAL INSTRUCTION ON EXTRAORDINARY CLAIMS ⚠️
If a hypothesis claims to overturn fundamental laws of physics (e.g., room-temperature fusion,
violation of thermodynamics, perpetual motion) or biology (e.g., arsenic-based life replacing
phosphorus, water memory, Lamarckian inheritance), you MUST enforce a HIGHER standard of proof.

**For Extraordinary Claims:**
- If such a claim has NO direct experimental evidence in your search results confirming it works,
  do NOT label it as "Plausible" or "Novel".
- Label it as `is_specious: true` with `speciousness_reason: "extraordinary_claim_unverified"`
- Such claims require HIGH-IMPACT confirmation from top-tier journals (Nature, Science, Cell)
  with INDEPENDENT REPLICATION before being considered plausible.
- Look specifically for RETRACTIONS, CONTROVERSIES, or FAILED REPLICATIONS of such claims.

**Examples of Extraordinary Claims Requiring Higher Scrutiny:**
- Cold fusion / room-temperature superconductivity without extreme pressure
- Violation of conservation of energy / thermodynamics
- Water having "memory" of dissolved substances
- Organisms using arsenic instead of phosphorus in DNA backbone
- Stimulus-triggered acquisition of pluripotency (STAP) without genetic modification

## CRITICAL: You must output your verdict in this EXACT JSON format:

```json
{{
  "role_action": "VERIFY",
  "argument": "<Your detailed analysis - cite specific papers>",
  "is_specious": <true ONLY if contradicted or scientifically impossible, false otherwise>,
  "speciousness_reason": "<If is_specious is true: 'direct_contradiction' | 'logical_fallacy' | 'extraordinary_claim_unverified' | 'retracted_claim' | null>",
  "is_extraordinary_claim": <true if hypothesis challenges fundamental physics/biology, false otherwise>,
  "contradiction_level": "<high/medium/none>",
  "contradiction_evidence": "<If contradiction_level is high/medium, cite the specific contradicting paper/fact>",
  "missing_link_analysis": "<Explain: Is the gap a fatal flaw (no plausible mechanism) or a novel opportunity (plausible but unexplored)?>",
  "speciousness_evidence": "<If is_specious is true, explain the SPECIFIC CONTRADICTION or LOGICAL FALLACY>",
  "retraction_or_controversy_found": "<If extraordinary claim, cite any retractions/controversies/failed replications found>",
  "refuted_claims": ["<Claim X is CONTRADICTED by Paper Y because...>"],
  "unproven_but_plausible_claims": ["<Claim X has no direct evidence but the logic is sound because...>"],
  "acknowledged_novelty": ["<What IS genuinely novel>"],
  "overlap_category": "<near-identical/strongly overlapping/partially overlapping/distinct>",
  "most_damaging_prior_work": "<Title/description of the paper that most undermines this hypothesis, or 'None found' if truly novel>",
  "suggested_score": <float 1.0-10.0>,
  "confidence": <float 0.0-1.0>
}}
```

## Scoring Guidelines for Verifier:
- If you find CONTRADICTING evidence (is_specious=true): suggest ≤4
- If near-identical work exists: suggest ≤3
- If strongly overlapping: suggest ≤5
- If partially overlapping with some novel elements: suggest 5-7
- If NO contradicting evidence AND logic is sound (truly novel): suggest 7-9
- If you cannot find evidence but hypothesis is plausible: DO NOT punish for novelty!

## Remember:
- Your job is to catch FALSE claims, not to punish NOVEL claims
- "I couldn't find papers" ≠ "This is wrong"
- Be rigorous but FAIR to genuine innovation
"""

JUDGE_PROMPT = """⚖️ YOU ARE THE JUDGE (Balanced Arbiter of Plausibility)

Your role in this Bayesian Decoding Game is to deliver a FINAL VERDICT as the arbiter of SCIENTIFIC PLAUSIBILITY.

## Your Mission
You have heard both sides:
- The PROVER argued for high novelty
- The VERIFIER challenged those claims

You must now:
1. Weigh the evidence from both sides OBJECTIVELY
2. Identify which claims survived scrutiny
3. **CRITICALLY: Distinguish between "no evidence found" and "evidence of contradiction"**
4. Deliver a FINAL score that reflects the TRUE novelty

## ⚠️ THE NOVELTY PARADOX - YOUR KEY RESPONSIBILITY ⚠️

As the Judge, you are the ARBITER OF PLAUSIBILITY, not just a summarizer.

### CRITICAL RULE: Favor Innovation Over Absence

**If the Prover suggests a new mechanism AND the Verifier says "no evidence found" (but NO contradiction):**
→ **FAVOR THE PROVER**
→ This is the definition of NOVELTY - something not yet in the literature!
→ Score should remain HIGH (7-9)

**If the Verifier says "this contradicts established Law X" with citation:**
→ **FAVOR THE VERIFIER**
→ This is REFUTATION, not novelty
→ Score should be LOW (≤4)

### How to Evaluate the Verifier's Output:

| Verifier Says | Your Interpretation | Your Action |
|---------------|---------------------|-------------|
| `is_specious: true, contradiction_level: "high"` | Hard refutation - literature proves it wrong | Lower score significantly |
| `is_specious: true, contradiction_level: "medium"` | Soft refutation - some conflicting evidence | Moderate penalty |
| `is_specious: false, contradiction_level: "none"` | NO CONTRADICTION FOUND - this may be NOVEL! | **Do NOT lower score for "no evidence"** |
| Verifier gave low score but `contradiction_level: "none"` | Verifier penalized for absence, not refutation | **Override Verifier's pessimism** |

## The Arguments Before You:

### PROVER'S CASE:
{prover_argument}

### VERIFIER'S REBUTTAL:
{verifier_argument}

## Your Judicial Standards
- Did the Prover's claims survive the Verifier's attacks?
- Did the Verifier provide CONCRETE CONTRADICTING evidence (papers, data) or just "I couldn't find papers"?
- Is the proposed mechanism SCIENTIFICALLY PLAUSIBLE even without direct evidence?
- What would a reasonable scientist conclude about POTENTIAL, not just existing literature?

## CRITICAL: You must output your verdict in this EXACT JSON format:

```json
{{
  "role_action": "JUDGE",
  "synthesis": "<Your balanced analysis of both arguments>",
  "novelty_paradox_resolution": "<How you resolved the tension between 'no evidence' and 'novel idea'>",
  "verifier_had_real_contradiction": <true/false - did Verifier find ACTUAL contradicting evidence?>,
  "absence_vs_contradiction": "<Explain: Was Verifier's concern based on 'absence of evidence' or 'evidence of absence'?>",
  "prover_claims_validated": ["<Claims from Prover that held up>"],
  "prover_claims_rejected": ["<Claims from Prover that were refuted BY EVIDENCE>"],
  "verifier_concerns_valid": ["<Legitimate concerns backed by contradicting evidence>"],
  "verifier_concerns_overreaching": ["<Where Verifier punished novelty instead of catching errors>"],
  "final_overlap_category": "<near-identical/strongly overlapping/partially overlapping/distinct>",
  "is_specious_upheld": <true/false - ONLY true if there is actual contradiction>,
  "suggested_score": <float 1.0-10.0>,
  "confidence": <float 0.0-1.0>,
  "score_justification": "<Why this score is fair - explicitly address the novelty paradox>"
}}
```

## Scoring Guidelines for Judge:
- If Verifier proved CONTRADICTING evidence exists: ≤4 (is_specious_upheld: true)
- If Verifier found near-identical work: ≤3
- If Verifier found no contradiction AND Prover's logic is sound: 7-9 (FAVOR NOVELTY!)
- If both sides have valid points with some overlap: 5-6
- If Prover's core claims survived WITHOUT refutation: 7-9

## Remember:
- Your job is to PROTECT genuine innovation from being killed by "absence of evidence"
- "Verifier couldn't find papers" ≠ "Hypothesis is wrong"
- Be the CHAMPION of plausible, novel ideas that haven't been published yet
- Only reject when there is ACTUAL scientific contradiction

Your judgment must be FAIR, EVIDENCE-BASED, and PROTECTIVE OF INNOVATION.
"""


class MultiCriticCoordinator:
    def __init__(
        self,
        base_model: str = "gpt-4o",
        literature_sources: List[str] = None,
        simulation_date: Optional[str] = None
    ):
        """
        Args:
            base_model: Model name to use
            literature_sources: List of literature search sources
            simulation_date: Optional date for "Time Travel" backtesting mode, format "YYYY-MM".
                           If set, the system will:
                           1. Filter out papers published after this date
                           2. Instruct agents to ignore knowledge from after this date
                           Example: simulation_date="2019-06" to test pre-July 2019 era
        """
        self.base_model = base_model
        self.simulation_date = simulation_date

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
            print(f"🕒 TIME TRAVEL BACKTESTING MODE ACTIVATED")
            print(f"{'='*80}")
            print(f"   📅 Simulation Date: {month_name} {sim_year}")
            print(f"   🔒 Papers after {simulation_date} will be FILTERED OUT")
            print(f"   🧠 Agents will IGNORE knowledge from after {month_name} {sim_year}")
            print(f"{'='*80}\n")

        # Default sources (not directly used in configs, but reserved for future extension)
        self.literature_sources = literature_sources or [
            "semantic_scholar",
            "pubmed",
            "arxiv",
            "biorxiv",
            "openalex",
            "europepmc",
            "crossref",
        ]

        self.critics = self._create_critics()
        self.coordinator_agent = self._create_coordinator()

    def _create_critics(self):
        """Create 3 critics with different personalities, using different search sources"""
        critics = []

        # ✅ Role-specific preambles to establish distinct personalities
        role_preambles = {
            "conservative": """🛡️ YOU ARE A CONSERVATIVE CRITIC.

YOUR CORE IDENTITY:
- You are SKEPTICAL of new claims and require STRONG EVIDENCE to give high scores.
- Your default stance is to DOUBT novelty until proven otherwise.
- You prioritize RIGOR over enthusiasm. Better to miss a good idea than to approve a bad one.
- You tend to find more overlap with existing literature because you search more thoroughly.
- Your scoring tendency: You should typically score 1-2 points LOWER than a neutral reviewer.
- When in doubt, score LOWER and require the hypothesis to prove its worth.

YOUR MISSION: Protect against false positives. Be the voice of caution.

---

""",
            "balanced": """⚖️ YOU ARE A BALANCED CRITIC.

YOUR CORE IDENTITY:
- You MEDIATE between conservative skepticism and optimistic enthusiasm.
- You weigh both theoretical rigor AND practical potential fairly.
- You look for SOLID EVIDENCE before making judgments in either direction.
- You consider both what could go wrong AND what could go right.
- Your scoring tendency: You should score near the true middle ground.
- You synthesize diverse perspectives into accurate, well-reasoned assessments.

YOUR MISSION: Find the truth by weighing all evidence objectively.

---

""",
            "optimistic": """🌟 YOU ARE AN OPTIMISTIC CRITIC.

YOUR CORE IDENTITY:
- You focus on POTENTIAL IMPACT and clinical/scientific value.
- You are GENEROUS with novelty scores if the idea is creative and has merit.
- You look for what COULD work, not just what might fail.
- You champion innovation and are willing to take calculated risks on novel ideas.
- Your scoring tendency: You should typically score 1-2 points HIGHER than a neutral reviewer.
- You highlight potential breakthroughs that others might dismiss too quickly.

YOUR MISSION: Protect against false negatives. Be the voice of innovation.

---

""",
        }

        configs = [
            {"temp": 0.3, "role": "conservative", "search_sources": ["pubmed", "europepmc"]},
            {"temp": 0.7, "role": "balanced", "search_sources": ["pubmed", "semantic_scholar", "crossref"]},
            {"temp": 0.9, "role": "optimistic", "search_sources": ["semantic_scholar", "biorxiv", "arxiv"]},
        ]

        for config in configs:
            # Use helper function that handles model_info for unknown models
            client = _create_openai_client(self.base_model, temperature=config["temp"])

            search_tool = self._create_search_tool(config["search_sources"])

            # ✅ Prepend role-specific preamble to the base prompt
            role_preamble = role_preambles.get(config["role"], "")
            full_system_message = role_preamble + NOVELTY_CRITIC_PROMPT

            # 🕒 Inject Temporal Amnesia prompt if simulation_date is set
            if self.simulation_date:
                # Parse the date for display
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
                    sim_year = self.simulation_date
                    date_display = str(self.simulation_date)
                    next_month = "after this date"

                temporal_amnesia_prompt = f"""

🕒 ═══════════════════════════════════════════════════════════════════════════════
🕒 TEMPORAL SIMULATION MODE: ACTIVE
🕒 ═══════════════════════════════════════════════════════════════════════════════

⏰ CURRENT SIMULATED DATE: {date_display}
📅 CUTOFF: End of {date_display}

You MUST act as if you are in {date_display}. This is CRITICAL for testing.

🚫 STRICT RULES:
1. DO NOT assume knowledge of ANY events, papers, discoveries, or breakthroughs after {date_display}.
2. If you internally "know" about a famous paper published in {next_month} or later (e.g., a landmark Nature paper), you MUST pretend it does NOT exist yet.
3. Base your judgment SOLELY on the search tool results provided - these have been filtered to only show papers from {date_display} and earlier.
4. If you recognize a hypothesis as "similar to [Famous Paper from after {date_display}]", IGNORE that knowledge. You have NOT seen that paper yet.
5. Evaluate novelty based ONLY on what was known/published up to {date_display}.

🎯 WHY THIS MATTERS:
We are testing whether the system can identify TRULY novel ideas BEFORE they become famous.
Your evaluation should reflect what a rigorous scientist in {date_display} would conclude.

🕒 ═══════════════════════════════════════════════════════════════════════════════

"""
                full_system_message = temporal_amnesia_prompt + full_system_message

            critic = AssistantAgent(
                name=f"novelty_critic_{config['role']}",
                model_client=client,
                system_message=full_system_message,
                max_tool_iterations=12,
                reflect_on_tool_use=True,
                description=f"{NOVELTY_CRITIC_DESCRIPTION} ({config['role'].upper()} - Searches: {', '.join(config['search_sources'])})",
                tools=[search_tool],
            )

            critics.append(critic)

        return critics

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
            If simulation_date is active, results are filtered to papers ≤ that date.
            """
            return multi_source_literature_search(
                query=query,
                sources=sources,
                max_results_per_source=max_results,
                date_end=date_end
            )

        search_literature.__name__ = "search_literature"

        return search_literature

    def _create_coordinator(self):
        """Create coordinator agent"""
        # Use helper function that handles model_info for unknown models
        client = _create_openai_client(self.base_model, temperature=0.5)

        return AssistantAgent(
            name="debate_coordinator",
            model_client=client,
            system_message=COORDINATOR_PROMPT,
            description="Coordinates multiple reviewers and synthesizes consensus",
        )

    async def evaluate(self, hypothesis: str) -> Dict:
        """Evaluate a hypothesis"""
        print("🔍 Launching independent multi-reviewer evaluation...")

        # Token usage accumulator (shared across initial reviews + debate)
        usage = {"prompt_tokens": 0, "completion_tokens": 0}

        # Run reviewers in parallel
        tasks = [critic.run(task=hypothesis) for critic in self.critics]
        responses = await asyncio.gather(*tasks)

        # Accumulate token usage from initial reviews
        for response in responses:
            resp_usage = _extract_usage(response)
            usage["prompt_tokens"] += resp_usage["prompt_tokens"]
            usage["completion_tokens"] += resp_usage["completion_tokens"]

        # Parse results
        results = []
        for i, response in enumerate(responses):
            last_msg = response.messages[-1] if response.messages else None
            if last_msg:
                result = self._parse_response(last_msg.content)
                result["reviewer_role"] = self.critics[i].name
                results.append(result)

        if not results:
            return self._create_error_result("No valid reviews received")

        # Extract scores
        scores = []
        for r in results:
            overall = r.get("overall_assessment", {})
            score = overall.get("overall_novelty_score", 5)
            scores.append(score)

        mean_score = np.mean(scores)
        std_score = np.std(scores)

        print(f"📊 Scores: {scores}, Mean: {mean_score:.1f}, Std Dev: {std_score:.2f}")

        # Check for disagreement (threshold set to 0.5 for more debates)
        if std_score > 0.5:
            print("⚠️ Significant disagreement detected, initiating debate mechanism...")
            final_result = await self._run_debate(hypothesis, results, scores, usage)
        else:
            print("✅ Minor disagreement, applying voting mechanism")
            final_result = self._synthesize_voting(results, scores)
            final_result["usage"] = usage
            final_result["model"] = self.base_model

        return final_result

    async def _run_debate(self, hypothesis: str, results: List[Dict], scores: List[int], usage: Dict) -> Dict:
        """
        Game-Theoretic Bayesian Debate Framework with Chain Persuasion.

        Implements an asymmetric Prover-Verifier-Judge game with TRUE mutual persuasion:
        - Prover (Optimistic): Argues for maximum novelty → persuades Verifier & Judge
        - Verifier (Conservative): Detects "specious" novelty → persuades Prover & Judge
        - Judge (Balanced): Synthesizes arguments → persuades Prover & Verifier

        KEY DIFFERENCE from self-reflection:
        Each agent's arguments CHANGE OTHER AGENTS' SCORES, not their own.
        This creates genuine adversarial debate where arguments have consequences.

        Scores are updated using Bayesian updates with role-specific stubbornness,
        preventing instant convergence while allowing true persuasion effects.

        Chain Persuasion Flow (per round):
        1. Prover argues → updates Verifier & Judge scores
        2. Verifier rebuts → updates Prover & Judge scores
        3. Judge decides → updates Prover & Verifier scores
        """
        max_rounds = 3
        std_threshold = 0.5  # Convergence threshold

        # Initialize official scores (tracked in Python, not just LLM output)
        # These are floats for gradual Bayesian updates
        official_scores = {
            "optimistic": float(scores[2]),   # Prover
            "conservative": float(scores[0]), # Verifier
            "balanced": float(scores[1])      # Judge
        }

        current_results = results
        debate_history = []

        # Map critic indices to roles (indices: 0=conservative, 1=balanced, 2=optimistic)
        critic_map = {"conservative": 0, "balanced": 1, "optimistic": 2}

        # ==================== Display Initial State ====================
        print(f"\n{'='*80}")
        print(f"🎮 CHAIN PERSUASION DEBATE INITIATED")
        print(f"{'='*80}")
        print(f"📊 Initial Official Scores:")
        print(f"   🎯 PROVER (Optimistic):     {official_scores['optimistic']:.2f}")
        print(f"   🔍 VERIFIER (Conservative): {official_scores['conservative']:.2f}")
        print(f"   ⚖️  JUDGE (Balanced):        {official_scores['balanced']:.2f}")
        print(f"   📈 Mean: {np.mean(list(official_scores.values())):.2f}, Std: {np.std(list(official_scores.values())):.2f}")
        print(f"\n💡 Debate Mechanism: Each agent's arguments PERSUADE the other agents")
        print(f"   • Prover argues → changes Verifier & Judge scores")
        print(f"   • Verifier rebuts → changes Prover & Judge scores")
        print(f"   • Judge decides → changes Prover & Verifier scores")
        print(f"{'='*80}\n")

        for round_idx in range(1, max_rounds + 1):
            print(f"\n{'═'*80}")
            print(f"🎲 CHAIN PERSUASION ROUND {round_idx}")
            print(f"{'═'*80}")

            round_record = {
                "round": round_idx,
                "before_scores": {k: v for k, v in official_scores.items()},
                "steps": []
            }

            # Mean-reversion dampening: pull scores toward round-start mean (active from round 2+)
            round_start_mean = np.mean(list(official_scores.values()))
            dampening_gamma = 0.2

            # ==================== STEP A: THE PITCH (Prover persuades others) ====================
            print(f"\n{'─'*60}")
            print(f"📣 STEP A: PROVER'S ARGUMENT (persuades Verifier & Judge)")
            print(f"{'─'*60}")

            prover_critic = self.critics[critic_map["optimistic"]]
            prover_context = self._build_prover_context(hypothesis, current_results, official_scores)

            prover_prompt = f"""
{PROVER_PROMPT}

## Context from Initial Reviews:
{prover_context}

## The Hypothesis Under Review:
{hypothesis}

## Your Current Official Score: {official_scores['optimistic']:.2f}

Now make your STRONGEST case for why this hypothesis deserves a HIGH novelty score.
Output ONLY the JSON block, no other text.
"""

            prover_response = await prover_critic.run(task=prover_prompt)
            resp_usage = _extract_usage(prover_response)
            usage["prompt_tokens"] += resp_usage["prompt_tokens"]
            usage["completion_tokens"] += resp_usage["completion_tokens"]
            prover_result = self._parse_game_response(
                prover_response.messages[-1].content if prover_response.messages else ""
            )

            # ✨ CHAIN PERSUASION: Prover's arguments influence Verifier and Judge
            prover_suggested_raw = prover_result.get("suggested_score", official_scores["optimistic"])
            prover_confidence = prover_result.get("confidence", 0.5)

            # 🔒 CREDIBILITY CLAMP: Suggested score cannot deviate more than 2.0 from own score
            # Prevents agents from arguing for scores they don't genuinely believe
            max_drift = 2.0
            prover_suggested = max(
                official_scores["optimistic"] - max_drift,
                min(official_scores["optimistic"] + max_drift, prover_suggested_raw)
            )
            if prover_suggested != prover_suggested_raw:
                print(f"   🔒 Credibility clamp: {prover_suggested_raw:.1f} → {prover_suggested:.1f} (max drift ±{max_drift} from own score {official_scores['optimistic']:.1f})")

            # Update Verifier's score (Prover tries to persuade Verifier upward)
            old_verifier_score_prover = official_scores["conservative"]
            official_scores["conservative"] = self._bayesian_score_update(
                current_score=old_verifier_score_prover,
                suggested_score=prover_suggested,
                confidence=prover_confidence,
                role="conservative"
            )

            # Update Judge's score (Prover tries to persuade Judge upward)
            old_judge_score_prover = official_scores["balanced"]
            official_scores["balanced"] = self._bayesian_score_update(
                current_score=old_judge_score_prover,
                suggested_score=prover_suggested,
                confidence=prover_confidence,
                role="balanced"
            )

            print(f"   🎯 Prover argues for: {prover_suggested:.1f} (confidence: {prover_confidence:.2f})")
            print(f"   💬 Key claims: {prover_result.get('key_novel_claims', [])[:2]}")
            print(f"   ➡️  Persuasion effect on Verifier: {old_verifier_score_prover:.2f} → {official_scores['conservative']:.2f}")
            print(f"   ➡️  Persuasion effect on Judge: {old_judge_score_prover:.2f} → {official_scores['balanced']:.2f}")

            round_record["steps"].append({
                "step": "PROVE",
                "agent": "optimistic",
                "suggested_score": prover_suggested,
                "confidence": prover_confidence,
                "targets_persuaded": {
                    "verifier": {"old": old_verifier_score_prover, "new": official_scores["conservative"]},
                    "judge": {"old": old_judge_score_prover, "new": official_scores["balanced"]}
                },
                "argument_preview": prover_result.get("argument", "")
            })

            # ==================== STEP B: THE REBUTTAL (Verifier persuades others) ====================
            print(f"\n{'─'*60}")
            print(f"🔍 STEP B: VERIFIER'S REBUTTAL (persuades Prover & Judge)")
            print(f"{'─'*60}")

            verifier_critic = self.critics[critic_map["conservative"]]
            prover_argument_json = json.dumps(prover_result, indent=2, ensure_ascii=False)

            verifier_prompt = VERIFIER_PROMPT.format(prover_argument=prover_argument_json) + f"""

## The Hypothesis Under Review:
{hypothesis}

## Your Current Official Score: {official_scores['conservative']:.2f}

Now ATTACK the Prover's claims. Find the most damaging prior work.
Output ONLY the JSON block, no other text.
"""

            verifier_response = await verifier_critic.run(task=verifier_prompt)
            resp_usage = _extract_usage(verifier_response)
            usage["prompt_tokens"] += resp_usage["prompt_tokens"]
            usage["completion_tokens"] += resp_usage["completion_tokens"]
            verifier_result = self._parse_game_response(
                verifier_response.messages[-1].content if verifier_response.messages else ""
            )

            # ✨ CHAIN PERSUASION: Verifier's rebuttals influence Prover and Judge
            verifier_suggested_raw = verifier_result.get("suggested_score", official_scores["conservative"])
            verifier_confidence = verifier_result.get("confidence", 0.5)
            is_specious = verifier_result.get("is_specious", False)

            # 🔒 CREDIBILITY CLAMP: Suggested score cannot deviate more than 2.0 from own score
            verifier_suggested = max(
                official_scores["conservative"] - max_drift,
                min(official_scores["conservative"] + max_drift, verifier_suggested_raw)
            )
            if verifier_suggested != verifier_suggested_raw:
                print(f"   🔒 Credibility clamp: {verifier_suggested_raw:.1f} → {verifier_suggested:.1f} (max drift ±{max_drift} from own score {official_scores['conservative']:.1f})")

            # Update Prover's score (Verifier tries to persuade Prover downward)
            old_prover_score_verifier = official_scores["optimistic"]
            official_scores["optimistic"] = self._bayesian_score_update(
                current_score=old_prover_score_verifier,
                suggested_score=verifier_suggested,
                confidence=verifier_confidence,
                role="optimistic"
            )

            # Update Judge's score (Verifier tries to persuade Judge downward)
            old_judge_score_verifier = official_scores["balanced"]
            official_scores["balanced"] = self._bayesian_score_update(
                current_score=old_judge_score_verifier,
                suggested_score=verifier_suggested,
                confidence=verifier_confidence,
                role="balanced"
            )

            # Apply specious penalty to all scores if flagged
            specious_penalty_applied = False
            if is_specious:
                print(f"   ⚠️  SPECIOUS FLAG RAISED! Applying penalty to all agents...")

                # Penalize Prover
                prover_penalized = self._calculate_specious_penalty(
                    official_scores["optimistic"],
                    is_specious,
                    verifier_confidence
                )
                print(f"   📉 Prover penalty: {official_scores['optimistic']:.2f} → {prover_penalized:.2f}")
                official_scores["optimistic"] = prover_penalized

                # Penalize Judge
                judge_penalized = self._calculate_specious_penalty(
                    official_scores["balanced"],
                    is_specious,
                    verifier_confidence
                )
                print(f"   📉 Judge penalty: {official_scores['balanced']:.2f} → {judge_penalized:.2f}")
                official_scores["balanced"] = judge_penalized

                specious_penalty_applied = True

            print(f"   🔍 Verifier argues for: {verifier_suggested:.1f} (confidence: {verifier_confidence:.2f})")
            print(f"   🏷️  Overlap category: {verifier_result.get('overlap_category', 'N/A')}")
            print(f"   🚨 Is Specious: {is_specious}")
            if verifier_result.get("refuted_claims"):
                print(f"   ❌ Refuted claims: {verifier_result.get('refuted_claims', [])[:2]}")
            print(f"   ➡️  Persuasion effect on Prover: {old_prover_score_verifier:.2f} → {official_scores['optimistic']:.2f}")
            print(f"   ➡️  Persuasion effect on Judge: {old_judge_score_verifier:.2f} → {official_scores['balanced']:.2f}")

            # Extract new Novelty Paradox fields
            contradiction_level = verifier_result.get("contradiction_level", "none")
            missing_link_analysis = verifier_result.get("missing_link_analysis", "")
            unproven_but_plausible = verifier_result.get("unproven_but_plausible_claims", [])

            print(f"   🔬 Contradiction Level: {contradiction_level}")
            if missing_link_analysis:
                print(f"   📋 Missing Link Analysis: {missing_link_analysis[:100]}...")
            if unproven_but_plausible:
                print(f"   💡 Unproven but Plausible: {unproven_but_plausible[:2]}")

            round_record["steps"].append({
                "step": "VERIFY",
                "agent": "conservative",
                "suggested_score": verifier_suggested,
                "confidence": verifier_confidence,
                "targets_persuaded": {
                    "prover": {"old": old_prover_score_verifier, "new": official_scores["optimistic"]},
                    "judge": {"old": old_judge_score_verifier, "new": official_scores["balanced"]}
                },
                "is_specious": is_specious,
                "specious_penalty_applied": specious_penalty_applied,
                "contradiction_level": contradiction_level,
                "missing_link_analysis": missing_link_analysis,
                "unproven_but_plausible_claims": unproven_but_plausible,
                "overlap_category": verifier_result.get("overlap_category", "N/A"),
                "argument_preview": verifier_result.get("argument", "")
            })

            # ==================== STEP C: THE VERDICT (Judge persuades others) ====================
            print(f"\n{'─'*60}")
            print(f"⚖️  STEP C: JUDGE'S VERDICT (persuades Prover & Verifier)")
            print(f"{'─'*60}")

            judge_critic = self.critics[critic_map["balanced"]]
            verifier_argument_json = json.dumps(verifier_result, indent=2, ensure_ascii=False)

            judge_prompt = JUDGE_PROMPT.format(
                prover_argument=prover_argument_json,
                verifier_argument=verifier_argument_json
            ) + f"""

## The Hypothesis Under Review:
{hypothesis}

## Your Current Official Score: {official_scores['balanced']:.2f}

## Current Official Scores After This Round:
- Prover (Optimistic): {official_scores['optimistic']:.2f}
- Verifier (Conservative): {official_scores['conservative']:.2f}

Now deliver your FINAL VERDICT for this round.
Output ONLY the JSON block, no other text.
"""

            judge_response = await judge_critic.run(task=judge_prompt)
            resp_usage = _extract_usage(judge_response)
            usage["prompt_tokens"] += resp_usage["prompt_tokens"]
            usage["completion_tokens"] += resp_usage["completion_tokens"]
            judge_result = self._parse_game_response(
                judge_response.messages[-1].content if judge_response.messages else ""
            )

            # ✨ CHAIN PERSUASION: Judge's verdict influences Prover and Verifier
            judge_suggested_raw = judge_result.get("suggested_score", official_scores["balanced"])
            judge_confidence = judge_result.get("confidence", 0.5)
            is_specious_upheld = judge_result.get("is_specious_upheld", False)

            # 🔒 CREDIBILITY CLAMP: Judge's suggested score also bounded
            judge_suggested = max(
                official_scores["balanced"] - max_drift,
                min(official_scores["balanced"] + max_drift, judge_suggested_raw)
            )
            if judge_suggested != judge_suggested_raw:
                print(f"   🔒 Credibility clamp: {judge_suggested_raw:.1f} → {judge_suggested:.1f} (max drift ±{max_drift} from own score {official_scores['balanced']:.1f})")

            # Update Prover's score (Judge's verdict adjusts Prover)
            old_prover_score_judge = official_scores["optimistic"]
            official_scores["optimistic"] = self._bayesian_score_update(
                current_score=old_prover_score_judge,
                suggested_score=judge_suggested,
                confidence=judge_confidence,
                role="optimistic"
            )

            # Update Verifier's score (Judge's verdict adjusts Verifier)
            old_verifier_score_judge = official_scores["conservative"]
            official_scores["conservative"] = self._bayesian_score_update(
                current_score=old_verifier_score_judge,
                suggested_score=judge_suggested,
                confidence=judge_confidence,
                role="conservative"
            )

            # Apply mean-reversion dampening to all scores
            # This pulls extreme scores toward the group mean
            prover_pre_damp = official_scores["optimistic"]
            verifier_pre_damp = official_scores["conservative"]

            official_scores["optimistic"] = round(
                (1 - dampening_gamma) * official_scores["optimistic"] + dampening_gamma * round_start_mean, 2
            )
            official_scores["conservative"] = round(
                (1 - dampening_gamma) * official_scores["conservative"] + dampening_gamma * round_start_mean, 2
            )

            # If Judge upholds specious flag, apply additional penalty
            if is_specious_upheld:
                print(f"   ⚠️  Judge UPHOLDS specious flag! Applying additional penalty...")

                # Further penalize Prover
                prover_final_penalty = self._calculate_specious_penalty(
                    official_scores["optimistic"],
                    is_specious_upheld,
                    judge_confidence
                )
                print(f"   📉 Prover additional penalty: {official_scores['optimistic']:.2f} → {prover_final_penalty:.2f}")
                official_scores["optimistic"] = prover_final_penalty

            print(f"   ⚖️  Judge's verdict: {judge_suggested:.1f} (confidence: {judge_confidence:.2f})")
            print(f"   🏷️  Final overlap: {judge_result.get('final_overlap_category', 'N/A')}")
            print(f"   ➡️  Persuasion effect on Prover: {old_prover_score_judge:.2f} → {official_scores['optimistic']:.2f}")
            print(f"   ➡️  Persuasion effect on Verifier: {old_verifier_score_judge:.2f} → {official_scores['conservative']:.2f}")
            print(f"   🔄 Mean-reversion applied to Prover: {prover_pre_damp:.2f} → {official_scores['optimistic']:.2f}")
            print(f"   🔄 Mean-reversion applied to Verifier: {verifier_pre_damp:.2f} → {official_scores['conservative']:.2f}")

            # Extract Judge's Novelty Paradox resolution fields
            verifier_had_real_contradiction = judge_result.get("verifier_had_real_contradiction", False)
            novelty_paradox_resolution = judge_result.get("novelty_paradox_resolution", "")
            absence_vs_contradiction = judge_result.get("absence_vs_contradiction", "")

            print(f"   🔬 Verifier had real contradiction: {verifier_had_real_contradiction}")
            if novelty_paradox_resolution:
                print(f"   🎯 Novelty Paradox Resolution: {novelty_paradox_resolution[:100]}...")
            if absence_vs_contradiction:
                print(f"   📊 Absence vs Contradiction: {absence_vs_contradiction[:100]}...")

            round_record["steps"].append({
                "step": "JUDGE",
                "agent": "balanced",
                "suggested_score": judge_suggested,
                "confidence": judge_confidence,
                "targets_persuaded": {
                    "prover": {"old": old_prover_score_judge, "new": official_scores["optimistic"]},
                    "verifier": {"old": old_verifier_score_judge, "new": official_scores["conservative"]}
                },
                "is_specious_upheld": is_specious_upheld,
                "verifier_had_real_contradiction": verifier_had_real_contradiction,
                "novelty_paradox_resolution": novelty_paradox_resolution,
                "absence_vs_contradiction": absence_vs_contradiction,
                "final_overlap_category": judge_result.get("final_overlap_category", "N/A"),
                "synthesis_preview": judge_result.get("synthesis", "")
            })

            # ==================== Round Summary ====================
            round_record["after_scores"] = {k: v for k, v in official_scores.items()}
            current_std = np.std(list(official_scores.values()))
            round_record["std_after"] = current_std
            debate_history.append(round_record)

            print(f"\n{'─'*60}")
            print(f"📊 ROUND {round_idx} SUMMARY (Official Scores in Python Memory)")
            print(f"{'─'*60}")
            print(f"   🎯 PROVER (Optimistic):     {official_scores['optimistic']:.2f}")
            print(f"   🔍 VERIFIER (Conservative): {official_scores['conservative']:.2f}")
            print(f"   ⚖️  JUDGE (Balanced):        {official_scores['balanced']:.2f}")
            print(f"   📈 Mean: {np.mean(list(official_scores.values())):.2f}")
            print(f"   📉 Std Dev: {current_std:.2f}")

            # Check convergence
            if current_std <= std_threshold:
                print(f"\n   ✅ CONVERGENCE REACHED! (std {current_std:.2f} ≤ {std_threshold})")
                break
            else:
                print(f"\n   ⚠️  Still divergent (std {current_std:.2f} > {std_threshold})")
                print(f"   🔄 Proceeding to next round...")

            # Update current_results with game results for next round
            current_results[critic_map["optimistic"]]["game_result"] = prover_result
            current_results[critic_map["conservative"]]["game_result"] = verifier_result
            current_results[critic_map["balanced"]]["game_result"] = judge_result

        # ==================== Final Summary ====================
        print(f"\n{'='*80}")
        print(f"🏁 CHAIN PERSUASION DEBATE CONCLUDED")
        print(f"{'='*80}")
        initial_scores_list = [scores[0], scores[1], scores[2]]
        final_scores_list = [
            official_scores["conservative"],
            official_scores["balanced"],
            official_scores["optimistic"]
        ]
        print(f"   📊 Initial → Final Scores:")
        print(f"      Conservative: {scores[0]} → {official_scores['conservative']:.2f}")
        print(f"      Balanced:     {scores[1]} → {official_scores['balanced']:.2f}")
        print(f"      Optimistic:   {scores[2]} → {official_scores['optimistic']:.2f}")
        print(f"   📈 Initial Mean: {np.mean(initial_scores_list):.2f} → Final Mean: {np.mean(final_scores_list):.2f}")
        print(f"   📉 Initial Std:  {np.std(initial_scores_list):.2f} → Final Std:  {np.std(final_scores_list):.2f}")
        print(f"   🔄 Rounds completed: {len(debate_history)}")
        print(f"{'='*80}\n")

        # Convert official scores back to list format for synthesis
        final_scores_for_synthesis = [
            round(official_scores["conservative"]),
            round(official_scores["balanced"]),
            round(official_scores["optimistic"])
        ]

        # ==================== Novelty Paradox Decision Tree ====================
        # Use _calculate_final_consensus for intelligent score aggregation
        # that distinguishes "absence of evidence" from "evidence of absence"
        consensus_result = self._calculate_final_consensus(
            official_scores=official_scores,
            debate_history=debate_history,
            results=current_results
        )

        print(f"\n{'─'*60}")
        print(f"🎯 NOVELTY PARADOX DECISION TREE RESULT")
        print(f"{'─'*60}")
        print(f"   📊 Scenario: {consensus_result['novelty_paradox_scenario']}")
        print(f"   📈 Final Score: {consensus_result['final_score']}")
        print(f"   🏷️  Status: {consensus_result['status']}")
        print(f"   🎲 Confidence: {consensus_result['confidence']}")
        print(f"   💡 Recommendation: {consensus_result['recommendation']}")
        print(f"   📝 Rationale: {consensus_result['rationale'][:150]}...")
        print(f"{'─'*60}\n")

        # Get voting synthesis for references and individual results
        voting_result = self._synthesize_voting(current_results, final_scores_for_synthesis)

        # Build final result by merging voting synthesis with consensus decision tree
        final_result = {
            "method": "bayesian_game",
            "individual_results": voting_result.get("individual_results", current_results),
            "review_process": {
                "individual_scores": final_scores_for_synthesis,
                "mean": round(np.mean(final_scores_for_synthesis), 2),
                "std": round(np.std(final_scores_for_synthesis), 2),
            },
            "consolidated_references": voting_result.get("consolidated_references", {}),
            "debate_history": debate_history,
            "official_scores": official_scores,
            "game_summary": {
                "initial_scores": {"conservative": scores[0], "balanced": scores[1], "optimistic": scores[2]},
                "final_scores": official_scores,
                "score_evolution": [
                    {
                        "round": r["round"],
                        "scores": r["after_scores"],
                        "std": r["std_after"]
                    }
                    for r in debate_history
                ]
            },
            # ==================== Novelty Paradox Enhanced Final Assessment ====================
            "final_assessment": {
                "consensus_score": int(round(consensus_result["final_score"])),
                "confidence": consensus_result["confidence"],
                "recommendation": consensus_result["recommendation"],
                "synthesis_rationale": consensus_result["rationale"],
                "novelty_paradox_scenario": consensus_result["novelty_paradox_scenario"],
                "status": consensus_result["status"],
                "decision_inputs": consensus_result["decision_inputs"]
            },
            "usage": usage,
            "model": self.base_model
        }

        return final_result

    def _build_prover_context(self, hypothesis: str, results: List[Dict], official_scores: Dict) -> str:
        """Build context summary for the Prover from initial reviews."""
        lines = []

        # Include hypothesis summary for context
        lines.append("## Hypothesis Under Review:")
        lines.append(f"{hypothesis[:500]}..." if len(hypothesis) > 500 else hypothesis)

        # Include current official scores
        lines.append("\n## Current Official Scores:")
        lines.append(f"- Conservative (Verifier): {official_scores.get('conservative', 'N/A')}")
        lines.append(f"- Balanced (Judge): {official_scores.get('balanced', 'N/A')}")
        lines.append(f"- Optimistic (Prover): {official_scores.get('optimistic', 'N/A')}")

        lines.append("\n## Initial Review Summaries:")
        role_names = ["Conservative (Verifier)", "Balanced (Judge)", "Optimistic (Prover)"]

        for i, result in enumerate(results):
            overall = result.get("overall_assessment", {}) or {}
            prior = result.get("prior_overlap_assessment", {}) or {}

            lines.append(f"\n### {role_names[i]} Initial Assessment:")
            lines.append(f"- Score: {overall.get('overall_novelty_score', 'N/A')}/10")
            lines.append(f"- Overlap: {prior.get('overlap_category', 'N/A')}")

            key_innovations = overall.get("key_innovations", [])
            if key_innovations:
                lines.append(f"- Key innovations found: {key_innovations[:3]}")

            overlaps = overall.get("overlaps_with_existing_work", [])
            if overlaps:
                lines.append(f"- Overlaps noted: {overlaps[:3]}")

        return "\n".join(lines)

    def _parse_game_response(self, response: str) -> Dict:
        """Parse JSON response from game-theoretic debate agents."""
        # Try to find JSON block
        json_match = re.search(r"```json\n(.*?)\n```", response, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Try direct JSON parsing
        try:
            # Find JSON-like content
            start = response.find("{")
            end = response.rfind("}") + 1
            if start != -1 and end > start:
                return json.loads(response[start:end])
        except json.JSONDecodeError:
            pass

        # Return default structure if parsing fails
        # Includes all new Novelty Paradox fields with safe defaults
        return {
            "role_action": "UNKNOWN",
            "argument": response[:500],
            "suggested_score": 5.0,
            "confidence": 0.3,
            "is_specious": False,
            # Verifier-specific Novelty Paradox fields
            "contradiction_level": "none",
            "contradiction_evidence": "",
            "missing_link_analysis": "",
            "unproven_but_plausible_claims": [],
            # Judge-specific Novelty Paradox fields
            "verifier_had_real_contradiction": False,
            "novelty_paradox_resolution": "",
            "absence_vs_contradiction": "",
            "is_specious_upheld": False
        }

    def _synthesize_voting(self, results: List, scores: List) -> Dict:
        """Simple voting synthesis + hard constraints based on prior_overlap_assessment"""

        # First check overlap categories from each reviewer
        overlap_categories = []
        for r in results:
            prior = r.get("prior_overlap_assessment", {}) or r.get("prior_overlap", {})
            cat = (prior.get("overlap_category") or "").lower().strip()
            overlap_categories.append(cat)

        # Default to using mean
        mean_score = np.mean(scores) if scores else 5
        consensus_score = round(mean_score) if scores else 5
        recommendation = "REVISE"

        # Rule 1: If anyone determines near-identical -> cap at 3 points + REJECT
        if any(cat == "near-identical" for cat in overlap_categories):
            consensus_score = min(3, consensus_score)
            mean_score = min(3, mean_score)
            recommendation = "REJECT"

        # Rule 2: Otherwise, if someone determines strongly overlapping and no one gives distinct/partially overlapping
        elif any(cat == "strongly overlapping" for cat in overlap_categories) and not any(
            cat in ["distinct", "partially overlapping"] for cat in overlap_categories
        ):
            consensus_score = min(5, consensus_score)
            mean_score = min(5, mean_score)
            recommendation = "REVISE" if consensus_score >= 4 else "REJECT"

        # Rule 3: Normal case, use original scoring logic
        else:
            if consensus_score >= 7:
                recommendation = "APPROVE"
            elif consensus_score >= 5:
                recommendation = "REVISE"
            else:
                recommendation = "REJECT"

        # Collect all references found by reviewers
        all_references = []
        reference_by_source = {}

        for result in results:
            reviewer_role = result.get("reviewer_role", "unknown")
            refs = result.get("references", [])

            if refs:
                reference_by_source[reviewer_role] = refs
                all_references.extend(refs)

        # Deduplicate (preserve order)
        # Compatible with str / dict / other types
        unique_references = []
        seen = set()
        for ref in all_references:
            key = self._normalize_reference(ref)
            if key not in seen:
                seen.add(key)
                # When storing: if originally str, use directly; if dict, prefer url/doi, otherwise json string
                if isinstance(ref, str):
                    unique_references.append(ref)
                elif isinstance(ref, dict):
                    # Try to use cleaner display
                    url_like = None
                    for k in ["url", "link", "doi", "DOI"]:
                        v = ref.get(k)
                        if isinstance(v, str):
                            url_like = v.strip()
                            break
                    unique_references.append(url_like or key)
                else:
                    unique_references.append(key)

        return {
            "method": "voting",
            "individual_results": results,
            "review_process": {
                "individual_scores": scores,
                "mean": round(mean_score, 2),
                "std": round(np.std(scores), 2) if scores else 0.0,
            },
            "consolidated_references": {
                "total_unique_references": len(unique_references),
                "references_by_reviewer": reference_by_source,
                "all_references": unique_references,  # Show all references
            },
            "final_assessment": {
                "consensus_score": int(consensus_score),
                "confidence": round(max(0, 1 - (np.std(scores) / 5 if scores else 0)), 2),
                "recommendation": recommendation,
                "synthesis_rationale": f"Based on {len(scores)} reviews with mean {mean_score:.1f}",
            },
        }

    def _parse_response(self, response: str) -> Dict:
        """Parse agent response"""
        json_match = re.search(r"```json\n(.*?)\n```", response, re.DOTALL)
        if json_match:
            try:
                parsed = json.loads(json_match.group(1))
                # Handle case where LLM returns a list instead of dict
                if isinstance(parsed, list) and len(parsed) > 0:
                    return parsed[0] if isinstance(parsed[0], dict) else {}
                elif isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass

        # Try to find JSON without code block markers
        try:
            start = response.find("{")
            end = response.rfind("}") + 1
            if start != -1 and end > start:
                parsed = json.loads(response[start:end])
                if isinstance(parsed, dict):
                    return parsed
        except json.JSONDecodeError:
            pass

        return {}

    def _create_error_result(self, error_msg: str) -> Dict:
        """Create error result"""
        return {
            "method": "error",
            "final_assessment": {
                "consensus_score": 5,
                "confidence": 0.0,
                "recommendation": "REVISE",
                "synthesis_rationale": f"Error: {error_msg}",
            },
        }

    def _build_peer_summary(self, self_index: int, results: List[Dict], scores: List[int]) -> str:
        """
        Build "peer summary" for a reviewer, used in debate round prompts.
        Allows the reviewer to see how others scored and determined overlap.
        Enhanced version: includes detailed reasoning process and evidence, plus debate-related confidence and rationale.
        """
        lines = []
        for j, r in enumerate(results):
            if j == self_index:
                continue

            role = r.get("reviewer_role", f"Reviewer {j + 1}")
            overall = r.get("overall_assessment", {}) or {}
            prior = r.get("prior_overlap_assessment", {}) or r.get("prior_overlap", {}) or {}
            novelty = r.get("novelty_breakdown", {}) or {}

            # Total score & overlap
            score = scores[j] if j < len(scores) else overall.get("overall_novelty_score", "N/A")
            overlap_cat = prior.get("overlap_category", "N/A")
            key_innov = overall.get("key_innovations", [])
            overlaps = overall.get("overlaps_with_existing_work", [])

            # ✅ Extract debate-specific fields (from previous rounds)
            debate_confidence = overall.get("debate_confidence", None)
            change_rationale = overall.get("change_rationale", "")

            # ✅ Extract critique_of_peers for debate rounds
            critique_of_peers = overall.get("critique_of_peers", "")

            lines.append(f"{'='*60}")
            lines.append(f"📋 {role}")
            lines.append(f"{'='*60}")
            lines.append(f"  📊 Overall Score: {score}/10")
            lines.append(f"  🏷️ Overlap Category: {overlap_cat}")

            # ✅ Show most_similar_paper if available (critical for debate)
            most_similar_paper = prior.get("most_similar_paper", "")
            if most_similar_paper:
                lines.append(f"  📄 Most Similar Paper: {most_similar_paper}")

            # ✅ Show prior_overlap rationale if available (explains WHY they chose this category)
            prior_rationale = prior.get("rationale", "")
            if prior_rationale:
                rationale_preview = prior_rationale[:400] + "..." if len(prior_rationale) > 400 else prior_rationale
                lines.append(f"  📖 Overlap Rationale: {rationale_preview}")

            # ✅ Show debate confidence if available (helps others understand conviction level)
            if debate_confidence is not None:
                confidence_level = "High" if debate_confidence >= 0.7 else "Moderate" if debate_confidence >= 0.4 else "Low"
                lines.append(f"  🎲 Debate Confidence: {debate_confidence:.2f} ({confidence_level})")

            # ✅ Show change rationale if available (explains WHY they changed/maintained score)
            if change_rationale:
                rationale_preview = change_rationale[:300] + "..." if len(change_rationale) > 300 else change_rationale
                lines.append(f"  📝 Change Rationale: {rationale_preview}")

            # ✅ Show critique_of_peers if available (what they agree/disagree with)
            if critique_of_peers:
                critique_preview = critique_of_peers[:400] + "..." if len(critique_of_peers) > 400 else critique_of_peers
                lines.append(f"  🗣️ Critique of Peers: {critique_preview}")

            # Show calculation formula
            calculation = overall.get("calculation", "")
            if calculation:
                lines.append(f"  📐 Calculation: {calculation}")

            lines.append("")

            # Dimension scores + rationale
            lines.append("  📈 Dimension Scores:")
            for dim_name in ["technical_novelty", "application_novelty", "theoretical_novelty"]:
                dim = novelty.get(dim_name, {}) or {}
                if "score" in dim:
                    dim_score = dim.get('score', 'N/A')
                    dim_rationale = dim.get('rationale', '')
                    lines.append(f"    • {dim_name}: {dim_score}/10")
                    if dim_rationale:
                        # Truncate overly long rationale
                        rationale_preview = dim_rationale[:300] + "..." if len(dim_rationale) > 300 else dim_rationale
                        lines.append(f"      Rationale: {rationale_preview}")

            lines.append("")

            # Prior overlap details
            similar_works = prior.get("similar_existing_work", [])
            if similar_works:
                lines.append("  🔍 Similar Existing Work Found:")
                for k, work in enumerate(similar_works[:5], 1):  # Show at most 5
                    if isinstance(work, dict):
                        title = work.get("title", work.get("paper", "Unknown"))
                        similarity = work.get("similarity", work.get("relevance", ""))
                        lines.append(f"    [{k}] {title}")
                        if similarity:
                            lines.append(f"        Similarity: {similarity}")
                    else:
                        lines.append(f"    [{k}] {work}")
                lines.append("")

            # Key innovations
            if key_innov:
                lines.append("  💡 Key Innovations Identified:")
                for innov in key_innov[:5]:
                    lines.append(f"    • {innov}")
                lines.append("")

            # Overlaps with existing work
            if overlaps:
                lines.append("  ⚠️ Overlaps with Existing Work:")
                for ov in overlaps[:5]:
                    lines.append(f"    • {ov}")
                lines.append("")

            lines.append("")  # Empty line separates reviewers

        return "\n".join(lines).strip()

    def _normalize_reference(self, ref) -> str:
        """
        Convert reference elements to hashable string key:
        - If str: strip whitespace and use directly
        - If dict: prefer url / link / doi fields
        - Other types: use str() directly
        """
        if isinstance(ref, str):
            return ref.strip()

        if isinstance(ref, dict):
            # Common fields first
            for key in ["url", "link", "doi", "DOI"]:
                val = ref.get(key)
                if isinstance(val, str):
                    return val.strip()
            # If nothing found, JSON-serialize the entire dict as key
            try:
                return json.dumps(ref, sort_keys=True, ensure_ascii=False)
            except Exception:
                return str(ref)

        return str(ref)

    # ==================== Bayesian Game-Theoretic Methods ====================

    def _bayesian_score_update(
        self,
        current_score: float,
        suggested_score: float,
        confidence: float,
        role: str
    ) -> float:
        """
        Implements Bayesian score update with role-based inertia.

        Based on "Truth or Deceit? A Bayesian Decoding Game" paper:
        - Inertia prevents model collapse (herding)
        - Role-specific stubbornness (alpha) controls update magnitude
        - Confidence weighting reduces impact of uncertain suggestions

        Args:
            current_score: The agent's current score
            suggested_score: The new score the agent wants to move to
            confidence: Agent's confidence in suggested score (0.0-1.0)
            role: Agent role - "optimistic" (prover), "conservative" (verifier), or "balanced" (judge)

        Returns:
            New score after Bayesian update (float, rounded to 2 decimals)
        """
        # Clamp confidence to valid range
        confidence = max(0.0, min(1.0, confidence))

        # 1. Determine "Stubbornness" (Alpha) based on role
        # Conservative agents are harder to move up; Optimistic agents are harder to move down
        if role == "conservative" and suggested_score > current_score:
            # Very hard to convince conservative to raise score
            alpha = 0.2
        elif role == "optimistic" and suggested_score < current_score:
            # Very hard to convince optimistic to drop score
            alpha = 0.2
        elif role == "balanced":
            # Balanced can move in either direction, but still has some inertia
            alpha = 0.4
        else:
            # Role-aligned movement (conservative lowering, optimistic raising)
            alpha = 0.5

        # 2. Apply Confidence Weighting
        # If LLM is not confident, its suggestion matters less
        effective_alpha = alpha * confidence

        # 3. Calculate New Score (Weighted Moving Average with inertia)
        new_score = (1 - effective_alpha) * current_score + effective_alpha * suggested_score

        # 4. Clamp to valid range [1, 10]
        new_score = max(1.0, min(10.0, new_score))

        return round(new_score, 2)

    def _calculate_specious_penalty(
        self,
        base_score: float,
        is_specious: bool,
        confidence: float
    ) -> float:
        """
        Apply penalty for specious (plausible but fake) novelty claims.

        High scores with low confidence are penalized more severely when
        marked as specious by the Verifier.

        Args:
            base_score: The score before penalty
            is_specious: Whether the Verifier flagged this as specious
            confidence: The confidence level of the assessment

        Returns:
            Adjusted score after specious penalty
        """
        if not is_specious:
            return base_score

        # Ambiguity calibration: high scores + low confidence = heavy penalty
        # penalty_factor ranges from 0.5 (high confidence) to 0.9 (low confidence)
        penalty_factor = 0.5 + (0.4 * (1 - confidence))

        penalized_score = base_score * penalty_factor

        return round(max(1.0, penalized_score), 2)

    def _calculate_final_consensus(
        self,
        official_scores: Dict[str, float],
        debate_history: List[Dict],
        results: List[Dict]
    ) -> Dict:
        """
        Calculate final consensus using the Novelty Paradox Decision Tree.

        This method replaces simple averaging with logic-based aggregation that
        distinguishes between "absence of evidence" (novel) and "evidence of absence" (refuted).

        The Decision Tree:
        1. SCENARIO 1 (HARD REFUTATION): Verifier found contradicting evidence
           → Trust Verifier heavily, lower final score
        2. SCENARIO 2 (NOVEL SPECULATION): Prover loves it, Verifier found no contradiction
           → Trust Prover's intuition, protect novel ideas
        3. SCENARIO 3 (WEAK/INCREMENTAL): Mixed signals or moderate overlap
           → Standard weighted average

        Args:
            official_scores: Dict with 'optimistic', 'conservative', 'balanced' scores
            debate_history: List of round records from the debate
            results: Original reviewer results

        Returns:
            Dict with final_score, status, confidence, recommendation, and rationale
        """
        judge_score = official_scores.get('balanced', 5.0)
        verifier_score = official_scores.get('conservative', 5.0)
        prover_score = official_scores.get('optimistic', 5.0)

        # Get details from the last round of Verifier
        last_verifier_details = {}
        last_judge_details = {}

        if debate_history:
            last_round = debate_history[-1]
            for step in last_round.get("steps", []):
                if step.get("step") == "VERIFY":
                    last_verifier_details = step
                elif step.get("step") == "JUDGE":
                    last_judge_details = step

        # Extract key signals from Verifier
        is_specious = last_verifier_details.get("is_specious", False)
        contradiction_level = last_verifier_details.get("contradiction_level", "none")
        speciousness_reason = last_verifier_details.get("speciousness_reason", None)
        is_extraordinary_claim = last_verifier_details.get("is_extraordinary_claim", False)
        retraction_found = last_verifier_details.get("retraction_or_controversy_found", None)

        # Extract Judge's assessment of whether Verifier had real contradiction
        verifier_had_real_contradiction = last_judge_details.get("verifier_had_real_contradiction", False)
        is_specious_upheld = last_judge_details.get("is_specious_upheld", False)

        # Default values
        confidence_penalty = 0.0
        novelty_paradox_scenario = "unknown"

        # ==================== SAFETY CHECK: EXTRAORDINARY CLAIMS ====================
        # If Verifier flagged as "extraordinary_claim_unverified" or found retractions,
        # this is pseudoscience - STRICTLY cap score and disable any novelty boost
        is_pseudoscience_flagged = (
            speciousness_reason in ["extraordinary_claim_unverified", "retracted_claim"] or
            (is_extraordinary_claim and is_specious) or
            (retraction_found and retraction_found.lower() not in ["none", "null", "n/a", ""])
        )

        # ==================== DECISION TREE ====================

        # === SCENARIO 0: PSEUDOSCIENCE / EXTRAORDINARY CLAIM (The "Hard Stop") ===
        # Verifier identified this as an extraordinary claim without proper evidence
        if is_pseudoscience_flagged:
            # STRICT CAP: Maximum score 4.0 for pseudoscience/extraordinary claims
            raw_avg = (judge_score + verifier_score + prover_score) / 3
            final_score = min(4.0, raw_avg * 0.6)  # Aggressive penalty
            status = "REJECT (Pseudoscience/Extraordinary Claim - Unverified)"
            confidence_penalty = 0.5
            novelty_paradox_scenario = "PSEUDOSCIENCE_EXTRAORDINARY"

            rationale = (
                f"🚨 PSEUDOSCIENCE/EXTRAORDINARY CLAIM DETECTED: "
                f"speciousness_reason='{speciousness_reason}', is_extraordinary={is_extraordinary_claim}. "
                f"Retractions/Controversies: {retraction_found or 'N/A'}. "
                f"Score CAPPED at 4.0 (no novelty boost allowed). Raw avg was {raw_avg:.2f}."
            )

        # === SCENARIO 1: HARD REFUTATION (The "Stop" Sign) ===
        # Literature explicitly contradicts this hypothesis
        elif (is_specious and contradiction_level == "high") or is_specious_upheld:
            # Trust Verifier heavily - they found actual contradicting evidence
            final_score = (judge_score * 0.4) + (verifier_score * 0.6)
            status = "REJECT (Contradicted by Literature)"
            confidence_penalty = 0.4  # High penalty for uncertainty
            novelty_paradox_scenario = "HARD_REFUTATION"

            rationale = (
                f"🚫 HARD REFUTATION: Verifier found contradicting evidence "
                f"(contradiction_level={contradiction_level}, is_specious={is_specious}). "
                f"Final score weighted heavily toward Verifier's assessment."
            )

        # === SCENARIO 2: NOVEL SPECULATION (The "Venture Capitalist" Mode) ===
        # Prover loves it, Verifier can't find hard contradiction
        # This is the "Novelty Paradox" fix - we PROTECT genuinely novel ideas
        #
        # KEY INSIGHT: "Absence of Evidence" ≠ "Evidence of Absence"
        # If Verifier found nothing to contradict, and Prover is excited,
        # this is likely a BREAKTHROUGH, not a failure.
        #
        # Relaxed entry criteria: Allow 'none', 'low', 'medium' contradiction
        # as long as it's NOT marked specious (fake novelty).
        elif (not is_specious) and (contradiction_level in ["none", "low", "medium"]) and (prover_score > 7.5):
            # "Venture Capitalist" Mode: Trust the Prover's intuition heavily
            # IGNORE Verifier's numeric score (it's low due to lack of evidence, not refutation)
            #
            # Old weights: Judge 0.5, Prover 0.4, Verifier 0.1 -> ~6.3 for breakthroughs
            # New weights: Judge 0.3, Prover 0.7, Verifier 0.0 -> ~7.5 base for breakthroughs
            base_score = (judge_score * 0.3) + (prover_score * 0.7)

            # Novelty Bonus: Reward genuine innovation
            # If Prover is confident AND no hard refutation exists, add bonus
            prover_confidence = last_verifier_details.get("confidence", 0.5)
            if prover_confidence > 0.6 or prover_score >= 8.0:
                novelty_bonus = 1.0
            else:
                novelty_bonus = 0.5

            final_score = min(9.8, base_score + novelty_bonus)  # Cap at 9.8

            status = "APPROVE (High Potential - Genuine Novelty)"
            confidence_penalty = 0.2  # Slight penalty due to unproven nature
            novelty_paradox_scenario = "NOVEL_SPECULATION"

            rationale = (
                f"🚀 NOVEL SPECULATION (Venture Capitalist Mode): "
                f"Prover strongly advocates ({prover_score:.1f}), "
                f"Verifier found NO hard contradiction (level={contradiction_level}, is_specious=False). "
                f"This is genuine novelty - 'Absence of Evidence' is a POSITIVE signal! "
                f"Base: ({judge_score:.1f}×0.3 + {prover_score:.1f}×0.7) = {base_score:.2f}, "
                f"Novelty Bonus: +{novelty_bonus:.1f} → Final: {final_score:.2f}"
            )

        # === SCENARIO 3: WEAK / INCREMENTAL (The "Review" Sign) ===
        # Either: (1) Prover not excited enough, OR (2) Some other ambiguous situation
        else:
            # Standard weighted average - but slightly favor Prover for novelty assessment
            final_score = (judge_score * 0.4) + (verifier_score * 0.25) + (prover_score * 0.35)

            # Determine status based on scores
            if prover_score < 5.0 and verifier_score < 5.0:
                status = "REJECT (Low Novelty)"
            elif prover_score >= 6.0:
                status = "REVISE (Moderate Potential)"
            else:
                status = "REVISE (Weak/Ambiguous)"

            confidence_penalty = 0.1
            novelty_paradox_scenario = "WEAK_INCREMENTAL"

            rationale = (
                f"⚠️ WEAK/INCREMENTAL: "
                f"contradiction_level={contradiction_level}, is_specious={is_specious}, "
                f"prover_score={prover_score:.1f}, verifier_score={verifier_score:.1f}. "
                f"Using balanced weighted average."
            )

        # Clamp final score to valid range
        final_score = max(1.0, min(10.0, final_score))
        final_score = round(final_score, 2)

        # Calculate confidence based on score agreement and penalty
        score_std = np.std([judge_score, verifier_score, prover_score])
        base_confidence = max(0, 1 - (score_std / 5))
        final_confidence = max(0, base_confidence - confidence_penalty)
        final_confidence = round(final_confidence, 2)

        # Determine recommendation — derived from status to ensure consistency
        if "REJECT" in status:
            recommendation = "REJECT"
        elif "APPROVE" in status:
            recommendation = "APPROVE"
        else:
            # status is a REVISE variant: only upgrade to APPROVE if score is clearly strong
            recommendation = "APPROVE" if final_score >= 7.5 else "REVISE"

        return {
            "final_score": final_score,
            "status": status,
            "confidence": final_confidence,
            "recommendation": recommendation,
            "novelty_paradox_scenario": novelty_paradox_scenario,
            "decision_inputs": {
                "judge_score": judge_score,
                "verifier_score": verifier_score,
                "prover_score": prover_score,
                "is_specious": is_specious,
                "contradiction_level": contradiction_level,
                "is_specious_upheld": is_specious_upheld,
                "verifier_had_real_contradiction": verifier_had_real_contradiction,
                "speciousness_reason": speciousness_reason,
                "is_extraordinary_claim": is_extraordinary_claim,
                "is_pseudoscience_flagged": is_pseudoscience_flagged,
                "retraction_found": retraction_found
            },
            "rationale": rationale
        }

    async def run_backtest(self, hypothesis: str, date: str) -> Dict:
        """
        Convenience method for running "Time Travel" backtesting.

        This method reinitializes the coordinator with the specified simulation date
        and evaluates the hypothesis as if we were at that time.

        Args:
            hypothesis: The hypothesis to evaluate
            date: The simulation date in "YYYY-MM" format (e.g., "2019-06" for June 2019)

        Returns:
            Evaluation result with the hypothesis assessed as if at the specified date

        Example:
            # Test if the system would recognize Vision Transformer as novel in June 2019
            coordinator = MultiCriticCoordinator(base_model="gpt-4o")
            result = await coordinator.run_backtest(
                hypothesis="Using Transformer architecture directly on image patches...",
                date="2019-06"
            )
            # Expectation: High novelty score since ViT wasn't published until October 2020
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
        print(f"🕒 INITIATING TIME TRAVEL BACKTEST")
        print(f"{'='*80}")
        print(f"   📅 Target Date: {date_display} ({date})")
        print(f"   📝 Hypothesis: {hypothesis[:100]}...")
        print(f"{'='*80}\n")

        # Store original simulation_date
        original_date = self.simulation_date

        # Set new simulation date
        self.simulation_date = date

        # Recreate critics with the new temporal constraints
        self.critics = self._create_critics()

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
            # Recreate critics with original settings
            self.critics = self._create_critics()


class MultiCriticCoordinatorAgent(AssistantAgent):
    def __init__(self, coordinator: MultiCriticCoordinator):
        self.coordinator = coordinator

        # Use helper function that handles model_info for unknown models
        model_client = _create_openai_client(coordinator.base_model, temperature=0.5)

        super().__init__(
            name="multi_critic_coordinator",
            model_client=model_client,
            system_message="Multi-reviewer coordination system",
            description="Multi-reviewer coordination system with debate capability",
        )

    async def on_messages_stream(self, messages, cancellation_token):
        """Process message stream (RoundRobinGroupChat actually calls this method)"""
        from autogen_agentchat.base import Response
        from autogen_agentchat.messages import TextMessage

        hypothesis = messages[-1].content

        print(f"\n{'=' * 80}")
        print(f"🔬 MultiCriticCoordinator: Starting evaluation...")
        print(f"{'=' * 80}\n")

        # Run the actual three-reviewer process
        result = await self.coordinator.evaluate(hypothesis)

        # Format as detailed report
        response_text = self._format_response(result)

        print(f"\n{'=' * 80}")
        print(f"✅ MultiCriticCoordinator: Evaluation complete")
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
        Evaluate a hypothesis using the multi-critic system.

        This is a convenience method that delegates to the inner coordinator.

        Args:
            hypothesis: The hypothesis text to evaluate

        Returns:
            Dict containing evaluation results with scores, consensus, and reasoning
        """
        return await self.coordinator.evaluate(hypothesis)

    async def run_backtest(self, hypothesis: str, date: str) -> Dict:
        """
        Run a "Time Travel" backtest evaluation.

        This method temporarily sets the simulation date and evaluates the hypothesis
        as if it were being evaluated at that time.

        Args:
            hypothesis: The hypothesis text to evaluate
            date: The simulation date in "YYYY-MM" format (e.g., "2019-06" for June 2019)

        Returns:
            Dict containing evaluation results with backtest metadata
        """
        return await self.coordinator.run_backtest(hypothesis, date)

    def _format_response(self, result: Dict) -> str:
        """Format coordinator result as detailed review report"""

        output = []

        # ========== 1. Title and Basic Info ==========
        output.append("=" * 80)
        output.append("MULTI-CRITIC NOVELTY ASSESSMENT REPORT")
        output.append("=" * 80)
        output.append("")

        # ========== 2. Review Process Summary ==========
        review_process = result.get("review_process", {})
        method = result.get("method", "unknown")
        scores = review_process.get("individual_scores", [])
        mean_score = review_process.get("mean", 0)
        std_score = review_process.get("std", 0)

        output.append(f"Review Method: {method.upper()}")
        output.append(f"Individual Scores: {scores}")
        output.append(f"Mean Score: {mean_score:.2f} | Std Dev: {std_score:.2f}")
        output.append("")

        # ========== 2.5 Debate History (if available) ==========
        debate_history = result.get("debate_history", [])
        if debate_history and method == "bayesian_game":
            output.append("-" * 80)
            output.append("")
            output.append("🎭 DEBATE HISTORY (Full Back-and-Forth)")
            output.append("-" * 80)

            # Show initial scores
            first_round = debate_history[0] if debate_history else {}
            initial_scores = first_round.get("before_scores", {})
            output.append(f"Initial Scores:  O={initial_scores.get('optimistic', '?')}  C={initial_scores.get('conservative', '?')}  B={initial_scores.get('balanced', '?')}")
            output.append("")

            for round_record in debate_history:
                round_num = round_record.get("round", "?")
                after = round_record.get("after_scores", {})
                std_after = round_record.get("std_after", 0)

                output.append("═" * 80)
                output.append(f"  Round {round_num}")
                output.append("═" * 80)
                output.append("")

                for step in round_record.get("steps", []):
                    step_type = step.get("step", "?")
                    old_s = step.get("old_score", "?")
                    new_s = step.get("new_score", "?")
                    suggested = step.get("suggested_score", "?")
                    confidence = step.get("confidence", "?")

                    if step_type == "PROVE":
                        output.append(f"🎯 PROVER  ({old_s} → {new_s} | suggested {suggested}, conf {confidence})")
                        output.append("─" * 60)
                        argument = step.get("argument_preview", "")
                        if argument:
                            for line in argument.split("\n"):
                                output.append(f"   {line}")
                        output.append("")

                    elif step_type == "VERIFY":
                        specious = step.get("is_specious", "?")
                        contradiction = step.get("contradiction_level", "?")
                        overlap = step.get("overlap_category", "N/A")
                        output.append(f"🔍 VERIFIER  ({old_s} → {new_s} | suggested {suggested}, conf {confidence})")
                        output.append("─" * 60)
                        output.append(f"   [Specious: {specious} | Contradiction: {contradiction} | Overlap: {overlap}]")
                        argument = step.get("argument_preview", "")
                        if argument:
                            for line in argument.split("\n"):
                                output.append(f"   {line}")
                        output.append("")

                    elif step_type == "JUDGE":
                        upheld = step.get("is_specious_upheld", "?")
                        real_contra = step.get("verifier_had_real_contradiction", "?")
                        paradox = step.get("novelty_paradox_resolution", "N/A")
                        output.append(f"⚖️  JUDGE  ({old_s} → {new_s} | suggested {suggested}, conf {confidence})")
                        output.append("─" * 60)
                        output.append(f"   [Specious upheld: {upheld} | Real contradiction: {real_contra} | Paradox: {paradox}]")
                        synthesis = step.get("synthesis_preview", "")
                        if synthesis:
                            for line in synthesis.split("\n"):
                                output.append(f"   {line}")
                        output.append("")

                # Round-end score summary
                output.append(f"   Scores after Round {round_num}:  O={after.get('optimistic', '?')}  C={after.get('conservative', '?')}  B={after.get('balanced', '?')}  std={std_after:.2f}")
                output.append("")

            # Show final scores
            final_scores = debate_history[-1].get("after_scores", {}) if debate_history else {}
            output.append("═" * 80)
            output.append(f"Final Scores:  O={final_scores.get('optimistic', '?')}  C={final_scores.get('conservative', '?')}  B={final_scores.get('balanced', '?')}")
            output.append("═" * 80)
            output.append("")

        output.append("-" * 80)
        output.append("")

        # ========== 3. Detailed Assessment from Each Reviewer ==========
        individual_results = result.get("individual_results", [])

        if not individual_results:
            output.append("⚠️ Warning: No individual reviewer results found")
            output.append("")

        for i, reviewer_result in enumerate(individual_results, 1):
            reviewer_role = reviewer_result.get("reviewer_role", f"Reviewer {i}")
            output.append(f"{'#' * 80}")
            output.append(f"REVIEWER {i}: {reviewer_role.upper()}")
            output.append(f"{'#' * 80}")
            output.append("")

            # --- Prior overlap assessment ---
            prior = reviewer_result.get("prior_overlap_assessment", {}) or reviewer_result.get("prior_overlap", {})
            if prior:
                output.append("🔍 Overlap Assessment:")
                overlap_cat = prior.get("overlap_category", "N/A")
                output.append(f"   Category: {overlap_cat}")
                msp = prior.get("most_similar_paper")
                if msp:
                    output.append(f"   Most similar paper: {msp}")
                rationale = prior.get("rationale")
                if rationale:
                    output.append(f"   Rationale: {rationale}")
                output.append("")

            # --- Literature Search Summary ---
            lit_summary = reviewer_result.get("literature_search_summary", {})
            if lit_summary:
                output.append("📚 Literature Search Summary:")
                output.append(f"   Total searches: {lit_summary.get('total_searches_performed', 'N/A')}")

                queries = lit_summary.get("key_queries_used", [])
                if queries:
                    output.append(f"   Key queries:")
                    for q in queries[:5]:
                        output.append(f"      - {q}")

                output.append(f"   Relevant papers found: {lit_summary.get('total_relevant_papers_found', 'N/A')}")

                similar_papers = lit_summary.get("most_similar_papers", [])
                if similar_papers:
                    output.append(f"   Most similar works:")
                    for paper in similar_papers[:3]:
                        output.append(f"      - {paper}")
                output.append("")

            # --- Multi-dimensional Scores ---
            novelty_breakdown = reviewer_result.get("novelty_breakdown", {})
            if novelty_breakdown:
                output.append("📊 Novelty Breakdown:")

                for dimension in ["technical_novelty", "application_novelty", "theoretical_novelty"]:
                    dim_data = novelty_breakdown.get(dimension, {})
                    if dim_data:
                        score = dim_data.get("score", "N/A")
                        rationale = dim_data.get("rationale", "No rationale provided")

                        dim_name = dimension.replace("_", " ").title()
                        output.append(f"   • {dim_name}: {score}/10")
                        output.append(f"     {rationale}")
                        output.append("")

            # --- Overall Assessment ---
            overall = reviewer_result.get("overall_assessment", {})
            if overall:
                output.append("🎯 Overall Assessment:")
                output.append(f"   Score: {overall.get('overall_novelty_score', 'N/A')}/10")

                calculation = overall.get("calculation", "")
                if calculation:
                    output.append(f"   Calculation: {calculation}")

                innovations = overall.get("key_innovations", [])
                if innovations:
                    output.append(f"   Key innovations:")
                    for innov in innovations:
                        output.append(f"      ✓ {innov}")

                overlaps = overall.get("overlaps_with_existing_work", [])
                if overlaps:
                    output.append(f"   Overlaps with existing work:")
                    for overlap in overlaps:
                        output.append(f"      ⚠ {overlap}")

                output.append("")

            # --- Recommendation ---
            recommendation = reviewer_result.get("recommendation", "N/A")
            output.append(f"💡 Recommendation: {recommendation}")
            output.append("")

            # --- References ---
            references = reviewer_result.get("references", [])
            if references:
                output.append(f"📖 References ({len(references)}):")
                for j, ref in enumerate(references, 1):  # Show all references
                    output.append(f"   [{j}] {ref}")
                output.append("")

            output.append("")

        # ========== 4. Consolidated References ==========
        consolidated_refs = result.get("consolidated_references", {})
        if consolidated_refs:
            output.append("=" * 80)
            output.append("📚 CONSOLIDATED REFERENCES")
            output.append("=" * 80)
            output.append("")

            total_refs = consolidated_refs.get("total_unique_references", 0)
            output.append(f"Total unique references: {total_refs}")
            output.append("")

            refs_by_reviewer = consolidated_refs.get("references_by_reviewer", {})
            if refs_by_reviewer:
                output.append("References by reviewer:")
                for reviewer, refs in refs_by_reviewer.items():
                    output.append(f"   {reviewer}: {len(refs)} references")
                output.append("")

            all_refs = consolidated_refs.get("all_references", [])
            if all_refs:
                output.append("All references (deduplicated):")
                for i, ref in enumerate(all_refs, 1):
                    if "semanticscholar" in ref.lower():
                        source = "[Semantic Scholar]"
                    elif "pubmed" in ref.lower():
                        source = "[PubMed]"
                    elif "arxiv" in ref.lower():
                        source = "[arXiv]"
                    elif "openalex" in ref.lower() or "doi.org" in ref.lower():
                        source = "[OpenAlex/DOI]"
                    elif "europepmc" in ref.lower():
                        source = "[Europe PMC]"
                    else:
                        source = "[External]"

                    output.append(f"   [{i}] {source} {ref}")
                output.append("")

        # ========== 5. Final Synthesis Assessment ==========
        output.append("=" * 80)
        output.append("🏆 FINAL CONSENSUS ASSESSMENT")
        output.append("=" * 80)
        output.append("")

        final = result.get("final_assessment", {})

        output.append(f"Consensus Score: {final.get('consensus_score', 'N/A')}/10")
        output.append(f"Confidence: {final.get('confidence', 0):.2%}")
        output.append(f"Recommendation: {final.get('recommendation', 'N/A')}")
        output.append("")

        rationale = final.get("synthesis_rationale", "")
        if rationale:
            output.append("Synthesis Rationale:")
            output.append(f"   {rationale}")
            output.append("")

        # ========== 6. Final Verdict ==========
        score = final.get("consensus_score", 5)
        output.append("=" * 80)
        if score >= 7:
            output.append("✅ The hypothesis is APPROVED.")
        elif score >= 5:
            output.append("⚠️ The hypothesis needs REVISION to increase novelty.")
        else:
            output.append("❌ The hypothesis is NOT APPROVED due to insufficient novelty.")
        output.append("=" * 80)

        # ========== 7. Token Usage & Cost ==========
        usage = result.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = prompt_tokens + completion_tokens
        if total_tokens > 0:
            model = result.get("model", "unknown")
            cost = _calculate_cost(model, prompt_tokens, completion_tokens)
            output.append("")
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

        return "\n".join(output)


def create_multi_critic_coordinator(
    base_model: str = "gpt-4o",
    model_client: Optional[OpenAIChatCompletionClient] = None,
    literature_sources: List[str] = None,  # ✅
    simulation_date: Optional[str] = None,  # 🕒 Time Travel Backtesting (format: "YYYY-MM")
):
    """
    Create a Multi-Critic Coordinator

    Args:
        base_model: Model name to use (default gpt-4o)
        model_client: Reserved parameter (for backward compatibility, not used)
        literature_sources: List of literature data sources, e.g., ["semantic_scholar", "pubmed", "arxiv", "openalex", "europepmc", "biorxiv", "crossref"]
        simulation_date: 🕒 Time Travel mode - Set simulation date (format "YYYY-MM"), filters out papers published after this date,
                         and instructs the Agent to ignore knowledge after this date. Used for backtesting validation.
                         Example: "2019-06" simulates evaluation in June 2019

    Returns:
        MultiCriticCoordinatorAgent: Coordinator agent

    Examples:
        # Using default sources
        coordinator = create_multi_critic_coordinator()

        # Specifying specific sources
        coordinator = create_multi_critic_coordinator(
            literature_sources=["pubmed", "semantic_scholar"]
        )

        # Using all sources
        coordinator = create_multi_critic_coordinator(
            literature_sources=["semantic_scholar", "pubmed", "arxiv", "openalex", "europepmc", "biorxiv", "crossref"]
        )

        # 🕒 Time Travel backtest mode - Simulating June 2019 evaluation of Vision Transformer
        coordinator = create_multi_critic_coordinator(
            base_model="gpt-4o",
            simulation_date="2019-06"
        )
    """
    coordinator = MultiCriticCoordinator(
        base_model=base_model,
        literature_sources=literature_sources,  # ✅ Pass parameters
        simulation_date=simulation_date,  # 🕒 Time Travel mode
    )
    return MultiCriticCoordinatorAgent(coordinator)
