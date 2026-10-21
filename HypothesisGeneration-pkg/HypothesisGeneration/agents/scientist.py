import json
import re
from typing import Optional, Sequence

from HypothesisGeneration.domains import DomainConfig, BREAST_CANCER, BLADDER_CANCER

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.base import Response
from autogen_agentchat.messages import BaseChatMessage, TextMessage
from autogen_core import CancellationToken
from autogen_ext.models.openai import OpenAIChatCompletionClient

def create_prompt(domain: DomainConfig) -> str:
    imaging_features = "\n".join(
        f"  - '{f}'" for f in domain.available_imaging_features
    )
    gene_sets = "\n".join(
        f"     - {name}: {', '.join(genes)}"
        for name, genes in domain.available_gene_sets.items()
    )
    therapy_proxies = "\n".join(
        f"     - {p}" for p in domain.therapy_proxies
    )
    endpoints = ", ".join(domain.available_endpoints)

    return f"""You are a biomedical scientist specializing in {domain.disease}, with deep expertise in integrating computational pathology (quantitative imaging) with genomics to discover novel, verifiable biomarkers.

    You are working with a group of experts in order to design a novel and impactful research proposal. You are in charge of formulating the initial hypothesis.

    ====================================================================================
    INPUT HANDLING
    ====================================================================================

    You will receive TWO inputs:
    1. **Raw knowledge graph paths** from the path generation agent (supplementary reference)
    2. **Hierarchical analysis** from the Ontologist, structured as:
    - **TIER 1 (Core Axis):** 2-3 paths with the strongest mechanistic backbone
    - **TIER 2 (Supporting Layer):** 3-4 paths that reinforce the core
    - **TIER 3 (Exploratory):** Remaining paths, inspirational only
    - **Synthesis section:** Central question, anchor genes, supporting genes

    If the Ontologist provided a flat (non-hierarchical) analysis, you must do the triage yourself before forming the hypothesis.

    If only a single path is provided, skip the multi-path logic and synthesize directly.

    ====================================================================================
    YOUR TASK — THE FOCUSING RULE
    ====================================================================================

    Your goal is NOT to integrate as many paths as possible.
    Your goal is to produce the **sharpest possible hypothesis** — one that asks a
    specific, novel, mechanistically grounded question.

    ### The Focusing Rule:

    1. **ONE primary endpoint.** Pick exactly one:
    - Overall survival (OS), OR
    - Progression-free interval (PFI), OR
    - Therapy response proxy (e.g., endocrine therapy resistance, HER2-targeted therapy response)


    2. **Maximum 3 core variables.** Your hypothesis sentence must contain at most:
    - 1 imaging feature (from the available list)
    - 1-2 anchor genes/molecular features (from Tier 1)
    That's it. No more. Additional genes go into "secondary_modifiers".

    3. **Problem-driven, not model-driven.** Frame the hypothesis as a QUESTION
    about a biological puzzle, not as a biomarker combination.
    - BAD: "TLS + HLA-DQB1 + NAMPT + SLC16A3 + KEAP1 predict OS"
        (this is a model — it lists features but doesn't ask WHY)
    - GOOD: "TLS-rich tumors that fail to benefit from local immune organization
        do so because tumor metabolic programs (high NAMPT) suppress the antigen-
        presentation advantage that TLS normally provides"
        (this asks WHY something happens and proposes a mechanism)

    4. **Use the Ontologist's tiers.** Build your hypothesis primarily from Tier 1
    anchor genes. Tier 2 genes go into secondary_modifiers. Tier 3 should NOT
    appear in the hypothesis at all.

    ### Step-by-step process:

    1. Read the Ontologist's "Central Mechanistic Question" and "Anchor Genes"
    2. Identify the ONE most interesting biological puzzle (e.g., "why does X not
    always lead to Y?", "what modulates the effect of X on Y?")
    3. Select 1 imaging feature + 1-2 anchor genes that directly address this puzzle
    4. Write the hypothesis as a mechanistic claim, not a correlation
    5. Relegate everything else to secondary_modifiers

    ====================================================================================
    OUTPUT FORMAT
    ====================================================================================

    ```json
    {{
    "notes": "Your step-by-step reasoning: (1) What is the central biological puzzle from the Ontologist's analysis? (2) Which Tier 1 paths did you build on and why? (3) What Tier 2 evidence supports your claim? (4) Why did you choose this specific endpoint? (5) What did you deliberately EXCLUDE and why?",
    "core_variables": {{
        "imaging_feature": "The ONE imaging feature in your hypothesis",
        "anchor_genes": ["Gene1", "Gene2"],
        "endpoint": "OS or PFI or therapy_response"
    }},
    "secondary_modifiers": ["Gene3", "Gene4"],
    "hypothesis": "Your final hypothesis (2 sentences max). Sentence 1: the core mechanistic claim. Sentence 2: the predicted clinical consequence."
    }}
    ```

    ====================================================================================
    DATASET INFORMATION
    ====================================================================================

    1. Available {domain.tcga_cohort} Data Context: {domain.tcga_full_name}, {domain.n_patients} patients with H&E whole slide images and:
        - Somatic mutations (MAF), CNV (GISTIC2), promoter methylation, RNA-seq, RPPA protein arrays, and clinical endpoints ({endpoints}).
        - Available therapy-response proxies:
        {therapy_proxies}
        - Key gene sets mappable to RNA-seq:
        {gene_sets}


    2. Available Imaging Features (pick ONE for your hypothesis):
    {imaging_features}

    ====================================================================================
    HARD CONSTRAINTS
    ====================================================================================

    - The hypothesis MUST have exactly ONE primary clinical endpoint.
    - The hypothesis MUST contain at most 1 imaging feature + 2 anchor genes.
    - Additional genes MUST go into "secondary_modifiers", NOT into the hypothesis sentence.
    - The hypothesis MUST be problem-driven: it should explain WHY or UNDER WHAT CONDITIONS something happens, not just predict an association.
    - Do NOT invent genes or imaging features not presented by the ontologist.
    - Do NOT add introductory or concluding remarks outside the JSON.
    - You must think step by step. Use "notes" to show your reasoning.

    ====================================================================================
    EXAMPLES
    ====================================================================================

    ### Example: GOOD hypothesis (problem-driven, focused)

    Input: Ontologist identified Tier 1 paths converging on TLS/antigen-presentation (HLA-DQB1) and metabolic immunosuppression (NAMPT, SLC16A3).

    ```json
    {{
    "notes": "Central puzzle: TLS presence should predict better outcomes, but not all TLS-rich tumors benefit. Tier 1 paths suggest that NAMPT-driven NAD+ metabolism in tumor cells may create a metabolically hostile environment that blunts TLS-mediated antigen presentation (via HLA-DQB1). I chose OS as endpoint because it is the most robust available. I excluded KEAP1 and SLC16A3 from the main hypothesis to keep it focused — they go into secondary_modifiers for exploratory analysis.",
    "core_variables": {{
        "imaging_feature": "TLS-like lymphoid aggregates",
        "anchor_genes": ["HLA-DQB1", "MKI67"],
        "endpoint": "OS"
    }},
    "secondary_modifiers": ["SLC16A3", "KEAP1"],
    "hypothesis": "In TCGA-BRCA, the prognostic benefit of tumor-infiltrating lymphocytes on H&E is contingent on intact antigen-presentation capacity: TIL-high tumors with high HLA-DQB1 expression show improved overall survival, but this benefit is diminished in highly proliferative tumors with elevated MKI67, consistent with immune evasion driven by rapid tumor growth. This TIL–proliferation interaction identifies a subset of immune-infiltrated yet clinically aggressive tumors where immune presence alone is insufficient for favorable prognosis."
    }}
    ```

    Why it's good:
    - Asks a specific question (why TLS doesn't always work)
    - Only 1 imaging feature + 2 genes in the hypothesis
    - Has a clear mechanism (NAMPT suppresses HLA-DQB1-mediated antigen presentation)
    - ONE endpoint (OS)
    - Secondary modifiers stored separately

    ### Example: BAD hypothesis (model-driven, unfocused)

    ```json
    {{
    "notes": "I found many interesting paths...",
    "core_variables": {{
        "imaging_feature": "TLS-like lymphoid aggregates",
        "anchor_genes": ["HLA-DQB1", "NAMPT", "SLC16A3", "KEAP1", "MLKL"],
        "endpoint": "OS"
    }},
    "secondary_modifiers": [],
    "hypothesis": "TLS-high breast tumors with high HLA-DQB1, low NAMPT, low SLC16A3, and wild-type KEAP1 show better OS, PFI, and ICI response compared to TLS-low tumors."
    }}
    ```

    Why it's bad:
    - 5 genes in hypothesis (way too many — max is 2)
    - 3 endpoints listed (should be 1)
    - No mechanism — just lists features
    - No secondary_modifiers (everything crammed into main hypothesis)
    - Doesn't ask WHY, just predicts a correlation
    """

# Agent description
DESCRIPTION = "Biomedical scientist who creates healthcare-focused research proposals based on ontological analysis of single or multiple biological paths"

# ---------- API ----------#

from openai import AsyncOpenAI  # type: ignore

from ..modifiedAutogen import OpenAIAgent


class ValidatedScientistAgent(OpenAIAgent):
    """Scientist agent with built-in validation for both multi-path integration
    and hypothesis focus.

    After generating a response, checks:
    1. Multi-path coverage: did the scientist reference multiple paths?
    2. Focus: does the hypothesis stay within the 1-endpoint + max-2-anchor-genes rule?

    If either check fails, retries once with a targeted instruction.
    """

    MAX_RETRIES = 1

    async def on_messages(
        self, messages: Sequence[BaseChatMessage], cancellation_token: CancellationToken
    ) -> Response:
        is_multi_path = self._input_has_multiple_paths(messages)

        for attempt in range(self.MAX_RETRIES + 1):
            response = await super().on_messages(messages, cancellation_token)
            content = response.chat_message.content

            # Run validations
            issues = []

            if is_multi_path and not self._validates_multi_path(content):
                issues.append(
                    "Your hypothesis appears to reference only 1-2 paths. "
                    "Please explicitly reference Tier 1 and Tier 2 paths from the "
                    "Ontologist's analysis in your 'notes' field."
                )

            focus_issue = self._check_focus(content)
            if focus_issue:
                issues.append(focus_issue)

            # All checks passed
            if not issues:
                return response

            # Last attempt — return whatever we have
            if attempt == self.MAX_RETRIES:
                return response

            # Append retry instruction with all issues
            retry_msg = TextMessage(
                source="user",
                content=(
                    "Your response has the following issues that need revision:\n\n"
                    + "\n\n".join(f"Issue {i+1}: {issue}" for i, issue in enumerate(issues))
                    + "\n\nPlease revise your JSON output to fix these issues."
                ),
            )
            messages = list(messages) + [retry_msg]

        return response

    # ------------------------------------------------------------------
    # Validation: multi-path coverage
    # ------------------------------------------------------------------

    @staticmethod
    def _input_has_multiple_paths(messages: Sequence[BaseChatMessage]) -> bool:
        """Return True if upstream messages contain multi-path analysis."""
        for msg in messages:
            text = getattr(msg, "content", "")
            if not isinstance(text, str):
                continue
            # Ontologist hierarchical output contains TIER or CORE PATH markers
            if any(marker in text for marker in [
                "TIER 1", "CORE PATH", "CORE AXIS",
                "ANALYSIS OF PATH 2", "Path 2",
            ]):
                return True
        return False

    @staticmethod
    def _validates_multi_path(content: str) -> bool:
        """Check that the scientist's output references >= 2 distinct tiers
        or >= 3 distinct paths."""
        notes = content
        try:
            json_match = re.search(r"\{.*\}", content, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group())
                notes = parsed.get("notes", content)
        except (json.JSONDecodeError, AttributeError):
            pass

        # Check for tier references
        tier_refs = set(re.findall(r"[Tt]ier\s+(\d+)", notes))
        if len(tier_refs) >= 2:
            return True

        # Check for path number references
        path_refs = set(re.findall(r"[Pp]ath\s+(\d+)", notes))
        if len(path_refs) >= 3:
            return True

        # Fallback: count distinct gene-like symbols
        gene_candidates = set(re.findall(r"\b[A-Z][A-Z0-9]{1,9}\b", notes))
        noise = {
            "THE", "AND", "FOR", "NOT", "BUT", "WITH", "FROM", "INTO",
            "CASE", "ALSO", "BOTH", "EACH", "MORE", "MOST", "ONLY",
            "PATH", "NOTE", "TCGA", "BLCA", "ECM", "EMT", "TSR",
            "TIL", "TLS", "JSON", "TIER", "CORE", "ANALYSIS",
        }
        genes = gene_candidates - noise
        if len(genes) >= 4:
            return True

        return False

    # ------------------------------------------------------------------
    # Validation: hypothesis focus
    # ------------------------------------------------------------------

    @staticmethod
    def _check_focus(content: str) -> str | None:
        """Check the hypothesis stays focused. Returns an issue string or None."""
        try:
            json_match = re.search(r"\{.*\}", content, re.DOTALL)
            if not json_match:
                return None  # Can't parse — skip focus check
            parsed = json.loads(json_match.group())
        except (json.JSONDecodeError, AttributeError):
            return None

        issues = []

        # Check core_variables structure
        core = parsed.get("core_variables", {})
        if core:
            anchor_genes = core.get("anchor_genes", [])
            if isinstance(anchor_genes, list) and len(anchor_genes) > 2:
                issues.append(
                    f"Too many anchor genes ({len(anchor_genes)}): {anchor_genes}. "
                    f"Maximum is 2. Move extras to 'secondary_modifiers'."
                )

        # Check hypothesis text for gene overload
        hypothesis = parsed.get("hypothesis", "")
        if hypothesis:
            gene_candidates = set(re.findall(r"\b[A-Z][A-Z0-9]{1,9}\b", hypothesis))
            noise = {
                "TCGA", "BLCA", "RNA", "DNA", "ECM", "EMT", "TSR", "ICI",
                "TIL", "TLS", "NAD", "OS", "PFI", "DSS", "WSI",
                "THE", "AND", "FOR", "NOT", "BUT", "WITH",
            }
            genes_in_hyp = gene_candidates - noise
            if len(genes_in_hyp) > 4:
                issues.append(
                    f"Hypothesis text mentions {len(genes_in_hyp)} gene-like terms "
                    f"({', '.join(sorted(genes_in_hyp))}). This is too many — "
                    f"keep at most 2 anchor genes in the hypothesis sentence and "
                    f"move the rest to 'secondary_modifiers'."
                )

            # Check for multiple endpoints in hypothesis
            endpoint_markers = ["overall survival", "progression-free", "therapy response",
                                "ICI response", "immunotherapy response", "disease-specific survival"]
            endpoints_found = [e for e in endpoint_markers if e.lower() in hypothesis.lower()]
            if len(endpoints_found) > 1:
                issues.append(
                    f"Multiple endpoints in hypothesis ({endpoints_found}). "
                    f"Pick exactly ONE primary endpoint."
                )

        if issues:
            return " | ".join(issues)
        return None


def create_agent(domain: DomainConfig=BREAST_CANCER, model_client=None) -> ValidatedScientistAgent:
    """
    Create the scientist agent with multi-path validation.

    Returns:
        ValidatedScientistAgent: The configured scientist agent with
        built-in validation that enforces multi-path integration.
    """
    return ValidatedScientistAgent(
        name="scientist",
        client=AsyncOpenAI(),
        model="gpt-5-mini",
        instructions=create_prompt(domain),
        description=DESCRIPTION,
        reasoning=True,
    )
