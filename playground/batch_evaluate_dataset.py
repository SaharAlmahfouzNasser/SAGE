# ============================================================
# Batch Evaluation Script for novelty_validation_dataset.xlsx
# ============================================================
#
# Batch score hypotheses from Excel dataset using MultiCritic system
# simulation_date is set to December of Simulation Year (~2 months before publication)
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

# Load API keys from .env file (must be set before importing coordinator)
from dotenv import load_dotenv
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / '.env')

# Validate required keys
for key in ["OPENAI_API_KEY", "SEMANTIC_SCHOLAR_API_KEY", "NCBI_API_KEY"]:
    if not os.environ.get(key):
        print(f"WARNING: {key} not set. Please configure it in .env file.")

# Add project paths
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / 'HypothesisGeneration'))

from HypothesisGeneration.agents import create_multi_critic_coordinator


async def evaluate_single_hypothesis(coordinator, hypothesis_id: str, hypothesis: str,
                                     simulation_date: str, expected_label: str) -> dict:
    """
    Evaluate a single hypothesis.

    Returns:
        Dict with evaluation results
    """
    print(f"\n{'='*70}")
    print(f"Evaluating: {hypothesis_id}")
    print(f"Simulation Date: {simulation_date}")
    print(f"Expected Label: {expected_label}")
    print(f"Hypothesis: {hypothesis[:100]}...")
    print(f"{'='*70}")

    try:
        result = await coordinator.run_backtest(hypothesis, simulation_date)

        # Extract from nested final_assessment structure
        final_assessment = result.get('final_assessment', {})
        final_score = final_assessment.get('consensus_score',
                                 result.get('review_process', {}).get('mean', 5.0))
        recommendation = final_assessment.get('recommendation', 'N/A')
        status = final_assessment.get('status', 'N/A')

        # Map score to predicted label
        if final_score >= 7.0:
            predicted_label = 'high_novelty'
        elif final_score <= 4.0:
            predicted_label = 'low_novelty_or_specious'
        else:
            predicted_label = 'incremental'

        print(f"\n>>> Final Score: {final_score:.2f}")
        print(f">>> Predicted: {predicted_label}")
        print(f">>> Expected: {expected_label}")
        print(f">>> Match: {'✅' if predicted_label == expected_label or (expected_label == 'specious' and predicted_label == 'low_novelty_or_specious') else '❌'}")

        return {
            'id': hypothesis_id,
            'simulation_date': simulation_date,
            'expected_label': expected_label,
            'final_score': final_score,
            'predicted_label': predicted_label,
            'recommendation': recommendation,
            'status': status,
            'success': True,
            'error': None,
            'full_result': result
        }

    except Exception as e:
        print(f">>> ERROR: {str(e)}")
        return {
            'id': hypothesis_id,
            'simulation_date': simulation_date,
            'expected_label': expected_label,
            'final_score': None,
            'predicted_label': None,
            'recommendation': None,
            'status': None,
            'success': False,
            'error': str(e),
            'full_result': None
        }


async def batch_evaluate(excel_path: str, output_dir: str = None,
                         start_idx: int = 0, end_idx: int = None,
                         model: str = "gpt-4"):
    """
    Batch evaluate all hypotheses in the Excel dataset.

    Args:
        excel_path: Path to the Excel file
        output_dir: Output directory for results
        start_idx: Starting index (0-based)
        end_idx: Ending index (exclusive), None for all
        model: Model to use for evaluation
    """
    # Read Excel data
    xlsx = pd.ExcelFile(excel_path)
    proposals_df = pd.read_excel(xlsx, 'Mechanistic Proposals')
    ground_truth_df = pd.read_excel(xlsx, 'Ground Truth')

    # Merge data
    df = proposals_df.merge(ground_truth_df[['ID', 'Short Name', 'Expected Label']], on='ID')

    # Apply index range
    if end_idx is None:
        end_idx = len(df)
    df = df.iloc[start_idx:end_idx]

    print(f"\n{'#'*70}")
    print(f"# BATCH EVALUATION")
    print(f"# Total entries: {len(df)}")
    print(f"# Model: {model}")
    print(f"# Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'#'*70}")

    results = []

    for idx, row in df.iterrows():
        hypothesis_id = row['ID']
        simulation_year = int(row['Simulation Year'])
        hypothesis = row['Mechanistic Proposal (Agent Input)']
        expected_label = row['Expected Label']
        short_name = row['Short Name']

        # Create simulation_date as YYYY-12 (December of simulation year)
        simulation_date = f"{simulation_year}-12"

        # Create coordinator for this evaluation
        coordinator = create_multi_critic_coordinator(
            base_model=model,
            simulation_date=simulation_date
        )

        result = await evaluate_single_hypothesis(
            coordinator=coordinator,
            hypothesis_id=hypothesis_id,
            hypothesis=hypothesis,
            simulation_date=simulation_date,
            expected_label=expected_label
        )
        result['short_name'] = short_name
        results.append(result)

        # Save intermediate results
        if output_dir:
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)

            # Save JSON results
            with open(output_path / 'batch_results_intermediate.json', 'w', encoding='utf-8') as f:
                json.dump(results, f, indent=2, ensure_ascii=False, default=str)

    # Calculate metrics
    metrics = calculate_metrics(results)

    # Save final results
    if output_dir:
        output_path = Path(output_dir)

        # Save JSON results
        final_output = {
            'metadata': {
                'excel_path': excel_path,
                'model': model,
                'timestamp': datetime.now().isoformat(),
                'total_evaluated': len(results)
            },
            'metrics': metrics,
            'results': results
        }

        with open(output_path / 'batch_results_final.json', 'w', encoding='utf-8') as f:
            json.dump(final_output, f, indent=2, ensure_ascii=False, default=str)

        # Save CSV summary
        summary_df = pd.DataFrame([{
            'ID': r['id'],
            'Short Name': r.get('short_name', ''),
            'Simulation Date': r['simulation_date'],
            'Expected Label': r['expected_label'],
            'Final Score': r['final_score'],
            'Predicted Label': r['predicted_label'],
            'Status': r['status'],
            'Success': r['success'],
            'Error': r['error']
        } for r in results])
        summary_df.to_csv(output_path / 'batch_results_summary.csv', index=False, encoding='utf-8')

        print(f"\n>>> Results saved to: {output_path}")

    # Print summary
    print_summary(metrics, results)

    return results, metrics


def calculate_metrics(results: list) -> dict:
    """Calculate evaluation metrics."""
    successful = [r for r in results if r['success']]

    if not successful:
        return {'error': 'No successful evaluations'}

    # Overall accuracy
    correct = 0
    for r in successful:
        expected = r['expected_label']
        predicted = r['predicted_label']

        # Match logic:
        # - high_novelty should be predicted as high_novelty
        # - incremental should be predicted as incremental
        # - specious should be predicted as low_novelty_or_specious
        if expected == 'high_novelty' and predicted == 'high_novelty':
            correct += 1
        elif expected == 'incremental' and predicted == 'incremental':
            correct += 1
        elif expected == 'specious' and predicted == 'low_novelty_or_specious':
            correct += 1

    accuracy = correct / len(successful) if successful else 0

    # Per-category metrics
    categories = {}
    for category in ['high_novelty', 'incremental', 'specious']:
        cat_results = [r for r in successful if r['expected_label'] == category]
        if cat_results:
            cat_correct = sum(1 for r in cat_results if
                             (r['expected_label'] == 'high_novelty' and r['predicted_label'] == 'high_novelty') or
                             (r['expected_label'] == 'incremental' and r['predicted_label'] == 'incremental') or
                             (r['expected_label'] == 'specious' and r['predicted_label'] == 'low_novelty_or_specious'))

            scores = [r['final_score'] for r in cat_results if r['final_score'] is not None]
            categories[category] = {
                'total': len(cat_results),
                'correct': cat_correct,
                'accuracy': cat_correct / len(cat_results),
                'mean_score': sum(scores) / len(scores) if scores else None,
                'min_score': min(scores) if scores else None,
                'max_score': max(scores) if scores else None
            }

    return {
        'total_evaluated': len(results),
        'successful': len(successful),
        'failed': len(results) - len(successful),
        'overall_accuracy': accuracy,
        'correct_predictions': correct,
        'categories': categories
    }


def print_summary(metrics: dict, results: list):
    """Print evaluation summary."""
    print(f"\n{'#'*70}")
    print(f"# EVALUATION SUMMARY")
    print(f"{'#'*70}")

    print(f"\nTotal Evaluated: {metrics.get('total_evaluated', 0)}")
    print(f"Successful: {metrics.get('successful', 0)}")
    print(f"Failed: {metrics.get('failed', 0)}")
    print(f"\nOverall Accuracy: {metrics.get('overall_accuracy', 0)*100:.1f}%")
    print(f"Correct Predictions: {metrics.get('correct_predictions', 0)}")

    print(f"\n--- Per Category ---")
    for category, stats in metrics.get('categories', {}).items():
        print(f"\n{category.upper()}:")
        print(f"  Accuracy: {stats['accuracy']*100:.1f}% ({stats['correct']}/{stats['total']})")
        if stats['mean_score'] is not None:
            print(f"  Mean Score: {stats['mean_score']:.2f}")
            print(f"  Score Range: [{stats['min_score']:.2f}, {stats['max_score']:.2f}]")

    # Print individual results
    print(f"\n--- Individual Results ---")
    for r in results:
        status = '✅' if r['success'] else '❌'
        score = f"{r['final_score']:.1f}" if r['final_score'] else 'ERR'
        match = ''
        if r['success']:
            expected = r['expected_label']
            predicted = r['predicted_label']
            if (expected == 'high_novelty' and predicted == 'high_novelty') or \
               (expected == 'incremental' and predicted == 'incremental') or \
               (expected == 'specious' and predicted == 'low_novelty_or_specious'):
                match = '✓'
            else:
                match = '✗'

        print(f"{status} {r['id']:4} | Score: {score:5} | Expected: {r['expected_label']:15} | Predicted: {str(r['predicted_label']):22} | {match}")


async def quick_test(n: int = 3):
    """Quick test with first n samples."""
    excel_path = str(PROJECT_ROOT / 'novelty_validation_dataset.xlsx')
    output_dir = str(PROJECT_ROOT / 'playground' / 'evaluation_results')

    return await batch_evaluate(
        excel_path=excel_path,
        output_dir=output_dir,
        start_idx=0,
        end_idx=n,
        model="gpt-4"
    )


async def run_full_evaluation():
    """Run full evaluation on all samples."""
    excel_path = str(PROJECT_ROOT / 'novelty_validation_dataset.xlsx')
    output_dir = str(PROJECT_ROOT / 'playground' / 'evaluation_results')

    return await batch_evaluate(
        excel_path=excel_path,
        output_dir=output_dir,
        model="gpt-4"
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Batch evaluate hypotheses')
    parser.add_argument('--quick', type=int, default=None,
                        help='Quick test with N samples')
    parser.add_argument('--start', type=int, default=0,
                        help='Start index (0-based)')
    parser.add_argument('--end', type=int, default=None,
                        help='End index (exclusive)')
    parser.add_argument('--model', type=str, default='gpt-4',
                        help='Model to use')

    args = parser.parse_args()

    if args.quick:
        asyncio.run(quick_test(args.quick))
    else:
        excel_path = str(PROJECT_ROOT / 'novelty_validation_dataset.xlsx')
        output_dir = str(PROJECT_ROOT / 'playground' / 'evaluation_results')

        asyncio.run(batch_evaluate(
            excel_path=excel_path,
            output_dir=output_dir,
            start_idx=args.start,
            end_idx=args.end,
            model=args.model
        ))
