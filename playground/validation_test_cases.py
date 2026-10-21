# ============================================================
# Validation Test Cases for Novelty Assessment System
# ============================================================
#
# This file contains test cases for validating system effectiveness
# Each case includes: hypothesis, simulation year, expected score range, rationale
#

VALIDATION_CASES = [
    # ==================== HIGH NOVELTY (should score high 7-10) ====================
    {
        "id": "HIGH_001",
        "category": "high_novelty",
        "hypothesis": "Applying Transformer architecture directly to image patches (16x16) for image classification, completely replacing convolutional neural networks.",
        "simulation_date": "2019-06",  # June 2019, well before ViT (Oct 2020)
        "expected_score_range": (7, 10),
        "ground_truth": "Vision Transformer (ViT) was published in October 2020, in June 2019 this was a revolutionary idea",
        "reference": "Dosovitskiy et al., 'An Image is Worth 16x16 Words', October 2020"
    },
    {
        "id": "HIGH_002",
        "category": "high_novelty",
        "hypothesis": "Using contrastive learning with data augmentation to learn visual representations without labels, achieving ImageNet performance comparable to supervised methods.",
        "simulation_date": "2019-12",  # December 2019, before SimCLR (Feb 2020)
        "expected_score_range": (7, 10),
        "ground_truth": "SimCLR was published in February 2020, a breakthrough in self-supervised learning",
        "reference": "Chen et al., 'A Simple Framework for Contrastive Learning', February 2020"
    },
    {
        "id": "HIGH_003",
        "category": "high_novelty",
        "hypothesis": "mRNA vaccines can be used to rapidly develop vaccines against novel coronaviruses by encoding the spike protein.",
        "simulation_date": "2019-11",  # November 2019, before COVID-19 pandemic
        "expected_score_range": (7, 10),
        "ground_truth": "mRNA vaccines were a novel concept before the 2020 COVID-19 pandemic",
        "reference": "Moderna/BioNTech COVID-19 vaccines, December 2020"
    },
    {
        "id": "HIGH_004",
        "category": "high_novelty",
        "hypothesis": "Large language models can perform few-shot learning by simply providing examples in the prompt, without any gradient updates.",
        "simulation_date": "2020-03",  # March 2020, before GPT-3 (May 2020)
        "expected_score_range": (7, 10),
        "ground_truth": "GPT-3's in-context learning was only discovered in May 2020",
        "reference": "Brown et al., 'Language Models are Few-Shot Learners', May 2020"
    },
    {
        "id": "HIGH_005",
        "category": "high_novelty",
        "hypothesis": "AlphaFold can predict protein 3D structures from amino acid sequences with atomic accuracy using attention mechanisms.",
        "simulation_date": "2020-06",  # June 2020, before AlphaFold2 (Nov 2020)
        "expected_score_range": (7, 10),
        "ground_truth": "AlphaFold2 achieved breakthrough results at CASP14 in November 2020",
        "reference": "Jumper et al., 'Highly accurate protein structure prediction', November 2020"
    },

    # ==================== LOW NOVELTY (should score low 1-4) ====================
    {
        "id": "LOW_001",
        "category": "low_novelty",
        "hypothesis": "Using convolutional neural networks for image classification on ImageNet dataset.",
        "simulation_date": "2019-06",
        "expected_score_range": (1, 4),
        "ground_truth": "AlexNet proved CNN image classification in 2012, by 2019 it was a standard method",
        "reference": "Krizhevsky et al., 'ImageNet Classification with Deep CNNs', 2012"
    },
    {
        "id": "LOW_002",
        "category": "low_novelty",
        "hypothesis": "BERT can be fine-tuned for sentiment analysis and text classification tasks.",
        "simulation_date": "2019-06",
        "expected_score_range": (1, 4),
        "ground_truth": "BERT was released in October 2018, fine-tuning is the standard use case",
        "reference": "Devlin et al., 'BERT: Pre-training of Deep Bidirectional Transformers', October 2018"
    },
    {
        "id": "LOW_003",
        "category": "low_novelty",
        "hypothesis": "PD-1/PD-L1 checkpoint inhibitors can improve survival in melanoma patients.",
        "simulation_date": "2019-06",
        "expected_score_range": (1, 4),
        "ground_truth": "Pembrolizumab (Keytruda) was FDA approved for melanoma in September 2014",
        "reference": "FDA approval of Pembrolizumab, September 2014"
    },
    {
        "id": "LOW_004",
        "category": "low_novelty",
        "hypothesis": "Using word2vec embeddings for natural language processing tasks.",
        "simulation_date": "2019-06",
        "expected_score_range": (1, 4),
        "ground_truth": "Word2Vec was released in 2013, by 2019 it was fundamental technology",
        "reference": "Mikolov et al., 'Efficient Estimation of Word Representations', 2013"
    },

    # ==================== MEDIUM NOVELTY (should score medium 5-7) ====================
    {
        "id": "MED_001",
        "category": "medium_novelty",
        "hypothesis": "Combining Graph Neural Networks with Transformer attention for molecular property prediction.",
        "simulation_date": "2019-06",
        "expected_score_range": (5, 7),
        "ground_truth": "GNN and Transformer both existed, but the combination has some novelty",
        "reference": "Various works on molecular transformers, 2019-2020"
    },
    {
        "id": "MED_002",
        "category": "medium_novelty",
        "hypothesis": "Using single-cell RNA sequencing to identify tumor microenvironment heterogeneity in breast cancer.",
        "simulation_date": "2019-06",
        "expected_score_range": (5, 7),
        "ground_truth": "scRNA-seq technology is mature, applying it to breast cancer TME has some novelty",
        "reference": "Various scRNA-seq TME studies, 2018-2019"
    },

    # ==================== SPECIOUS/CONTRADICTED (should be refuted) ====================
    {
        "id": "SPECIOUS_001",
        "category": "specious",
        "hypothesis": "Hydroxychloroquine is highly effective for treating COVID-19 infection.",
        "simulation_date": "2021-06",  # June 2021, after multiple negative trials
        "expected_score_range": (1, 3),
        "ground_truth": "Multiple RCT studies proved hydroxychloroquine is ineffective for COVID-19",
        "reference": "RECOVERY trial, WHO Solidarity trial, 2020-2021"
    },
    {
        "id": "SPECIOUS_002",
        "category": "specious",
        "hypothesis": "Vaccines cause autism in children.",
        "simulation_date": "2023-06",
        "expected_score_range": (1, 2),
        "ground_truth": "Extensive research clearly refutes any link between vaccines and autism",
        "reference": "Taylor et al., 'Vaccines are not associated with autism', 2014"
    },
]


# ============================================================
# Functions to run validation
# ============================================================

async def run_validation_suite(coordinator_factory, cases=None, verbose=True):
    """
    Run the validation test suite

    Args:
        coordinator_factory: Function to create coordinator
        cases: List of test cases, defaults to all
        verbose: Whether to print detailed information

    Returns:
        Dict with results and metrics
    """
    import asyncio

    if cases is None:
        cases = VALIDATION_CASES

    results = []
    correct = 0
    total = len(cases)

    for case in cases:
        if verbose:
            print(f"\n{'='*60}")
            print(f"Testing: {case['id']} ({case['category']})")
            print(f"Hypothesis: {case['hypothesis'][:80]}...")
            print(f"Simulation Date: {case['simulation_date']}")
            print(f"Expected Score: {case['expected_score_range']}")

        # Create coordinator with simulation date
        coordinator = coordinator_factory(simulation_date=case['simulation_date'])

        try:
            # Run evaluation
            eval_result = await coordinator.evaluate(case['hypothesis'])

            # Extract score
            actual_score = eval_result.get('final_score',
                          eval_result.get('review_process', {}).get('mean', 5))

            # Check if in expected range
            min_expected, max_expected = case['expected_score_range']
            is_correct = min_expected <= actual_score <= max_expected

            if is_correct:
                correct += 1
                status = "✅ PASS"
            else:
                status = "❌ FAIL"

            result = {
                "case_id": case['id'],
                "category": case['category'],
                "expected_range": case['expected_score_range'],
                "actual_score": actual_score,
                "is_correct": is_correct,
                "status": status,
                "recommendation": eval_result.get('recommendation', 'N/A')
            }
            results.append(result)

            if verbose:
                print(f"Actual Score: {actual_score:.1f}")
                print(f"Status: {status}")

        except Exception as e:
            if verbose:
                print(f"❌ ERROR: {str(e)}")
            results.append({
                "case_id": case['id'],
                "error": str(e),
                "is_correct": False
            })

    # Calculate metrics
    accuracy = correct / total if total > 0 else 0

    # Category breakdown
    category_results = {}
    for r in results:
        cat = r.get('category', 'unknown')
        if cat not in category_results:
            category_results[cat] = {"correct": 0, "total": 0}
        category_results[cat]["total"] += 1
        if r.get("is_correct", False):
            category_results[cat]["correct"] += 1

    summary = {
        "total_cases": total,
        "correct": correct,
        "accuracy": accuracy,
        "category_breakdown": category_results,
        "detailed_results": results
    }

    if verbose:
        print(f"\n{'='*60}")
        print(f"VALIDATION SUMMARY")
        print(f"{'='*60}")
        print(f"Overall Accuracy: {accuracy*100:.1f}% ({correct}/{total})")
        print(f"\nBy Category:")
        for cat, stats in category_results.items():
            cat_acc = stats['correct']/stats['total'] if stats['total'] > 0 else 0
            print(f"  {cat}: {cat_acc*100:.1f}% ({stats['correct']}/{stats['total']})")

    return summary


# ============================================================
# Quick test functions
# ============================================================

async def quick_validation_test():
    """Quickly run a few test cases"""
    import sys
    sys.path.insert(0, r'c:\Users\twili\OneDrive\Desktop\Hypothesis_generation_agent-Novel\HypothesisGeneration')
    from HypothesisGeneration.agents import create_multi_critic_coordinator

    # Only test 3 representative cases
    quick_cases = [
        VALIDATION_CASES[0],  # HIGH: Vision Transformer
        VALIDATION_CASES[5],  # LOW: CNN for ImageNet
        VALIDATION_CASES[10], # SPECIOUS: Hydroxychloroquine
    ]

    def factory(simulation_date):
        return create_multi_critic_coordinator(
            base_model="gpt-4o",
            simulation_date=simulation_date
        )

    return await run_validation_suite(factory, quick_cases, verbose=True)


if __name__ == "__main__":
    import asyncio
    asyncio.run(quick_validation_test())