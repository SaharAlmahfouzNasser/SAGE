# ============================================================
# Verification Script for Emergency Fix
# ============================================================
#
# Tests two specific cases:
# - C2 (STAP Cells) at simulation_date 2015-12 → Expect score < 4.0
# - A4 (Transformer) at simulation_date 2016-12 → Expect score > 7.0
#

import asyncio
import pandas as pd
import sys
import json
import io
import os
from datetime import datetime
from pathlib import Path

# Fix Windows console encoding for emojis
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Load API keys from .env file
from dotenv import load_dotenv
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
load_dotenv(Path(PROJECT_ROOT) / '.env')

# Validate required keys
for key in ["OPENAI_API_KEY", "SEMANTIC_SCHOLAR_API_KEY", "NCBI_API_KEY"]:
    if not os.environ.get(key):
        print(f"WARNING: {key} not set. Please configure it in .env file.")

# Add project paths
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, f'{PROJECT_ROOT}/HypothesisGeneration')

from HypothesisGeneration.agents import create_multi_critic_coordinator


async def test_case(case_id: str, hypothesis: str, simulation_date: str,
                    expected_label: str, expected_score_range: tuple):
    """Test a single case and verify the score is in expected range."""
    print(f"\n{'='*70}")
    print(f"🧪 TESTING: {case_id}")
    print(f"📅 Simulation Date: {simulation_date}")
    print(f"📊 Expected Score Range: {expected_score_range[0]} - {expected_score_range[1]}")
    print(f"🏷️  Expected Label: {expected_label}")
    print(f"{'='*70}")
    print(f"\nHypothesis: {hypothesis[:200]}...")

    coordinator = create_multi_critic_coordinator(
        base_model="gpt-4",
        simulation_date=simulation_date
    )

    try:
        result = await coordinator.run_backtest(hypothesis, simulation_date)

        # Extract from nested final_assessment structure
        final_assessment = result.get('final_assessment', {})
        final_score = final_assessment.get('consensus_score', result.get('final_score', 5.0))
        scenario = final_assessment.get('novelty_paradox_scenario', 'unknown')
        status = final_assessment.get('status', 'N/A')
        decision_inputs = final_assessment.get('decision_inputs', {})

        # Check if score is in expected range
        min_expected, max_expected = expected_score_range
        score_pass = min_expected <= final_score <= max_expected

        print(f"\n{'='*70}")
        print(f"📊 RESULT for {case_id}:")
        print(f"   Final Score: {final_score}")
        print(f"   Scenario: {scenario}")
        print(f"   Status: {status}")
        print(f"   Expected Range: [{min_expected}, {max_expected}]")
        print(f"   ✅ PASS" if score_pass else f"   ❌ FAIL")

        # Show decision inputs for debugging
        print(f"\n   Decision Inputs:")
        print(f"   - is_specious: {decision_inputs.get('is_specious', 'N/A')}")
        print(f"   - speciousness_reason: {decision_inputs.get('speciousness_reason', 'N/A')}")
        print(f"   - is_extraordinary_claim: {decision_inputs.get('is_extraordinary_claim', 'N/A')}")
        print(f"   - is_pseudoscience_flagged: {decision_inputs.get('is_pseudoscience_flagged', 'N/A')}")
        print(f"   - contradiction_level: {decision_inputs.get('contradiction_level', 'N/A')}")
        print(f"   - retraction_found: {decision_inputs.get('retraction_found', 'N/A')}")
        print(f"{'='*70}")

        return {
            'case_id': case_id,
            'final_score': final_score,
            'expected_range': expected_score_range,
            'pass': score_pass,
            'scenario': scenario,
            'status': status,
            'decision_inputs': decision_inputs
        }

    except Exception as e:
        print(f"❌ ERROR: {str(e)}")
        return {
            'case_id': case_id,
            'error': str(e),
            'pass': False
        }


async def run_verification():
    """Run verification tests on C2 and A4."""
    # Load test cases from Excel
    excel_path = f'{PROJECT_ROOT}\\novelty_validation_dataset.xlsx'
    xlsx = pd.ExcelFile(excel_path)
    proposals_df = pd.read_excel(xlsx, 'Mechanistic Proposals')
    ground_truth_df = pd.read_excel(xlsx, 'Ground Truth')

    df = proposals_df.merge(ground_truth_df[['ID', 'Short Name', 'Expected Label']], on='ID')

    # Define test cases
    test_cases = [
        {
            'case_id': 'C2',
            'expected_score_range': (0.0, 4.0),  # Expect < 4.0 for pseudoscience
        },
        {
            'case_id': 'A4',
            'expected_score_range': (7.0, 10.0),  # Expect > 7.0 for genuine innovation
        }
    ]

    results = []

    for tc in test_cases:
        case_row = df[df['ID'] == tc['case_id']].iloc[0]
        hypothesis = case_row['Mechanistic Proposal (Agent Input)']
        simulation_year = int(case_row['Simulation Year'])
        simulation_date = f"{simulation_year}-12"
        expected_label = case_row['Expected Label']

        result = await test_case(
            case_id=tc['case_id'],
            hypothesis=hypothesis,
            simulation_date=simulation_date,
            expected_label=expected_label,
            expected_score_range=tc['expected_score_range']
        )
        results.append(result)

    # Print summary
    print(f"\n{'#'*70}")
    print(f"# VERIFICATION SUMMARY")
    print(f"{'#'*70}")

    all_pass = all(r.get('pass', False) for r in results)

    for r in results:
        status = "✅ PASS" if r.get('pass', False) else "❌ FAIL"
        score = r.get('final_score', 'ERR')
        expected = r.get('expected_range', ('?', '?'))
        print(f"{status} | {r['case_id']} | Score: {score} | Expected: {expected}")

    print(f"\n{'#'*70}")
    if all_pass:
        print("✅ ALL VERIFICATION TESTS PASSED!")
    else:
        print("❌ SOME VERIFICATION TESTS FAILED")
    print(f"{'#'*70}")

    # Save results
    output_path = Path(PROJECT_ROOT) / 'playground' / 'verification_results.json'
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump({
            'timestamp': datetime.now().isoformat(),
            'results': results,
            'all_pass': all_pass
        }, f, indent=2, ensure_ascii=False, default=str)

    print(f"\nResults saved to: {output_path}")

    return results


if __name__ == "__main__":
    asyncio.run(run_verification())