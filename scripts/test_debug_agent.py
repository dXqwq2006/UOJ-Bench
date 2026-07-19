import requests
import json

from solution import load_solver
from solution.api import FeedbackKind, RepairInput, SolverFeedback
from utils.benchmark import solver_metadata
from utils.uoj_api import SubmissionRequest, Client
from utils.patch import *

import Levenshtein

def similarity(a: str, b: str) -> float:
    dist = Levenshtein.distance(a, b)
    max_len = max(len(a), len(b))
    return 1 - dist / max_len

def TestDebugAgent(solver, problem_id, problem_statement, submission_code, submission_language='C++20',
                   max_trials=10, metadata=None):
    submission_code = submission_code.replace('\r', '')
    # Initialize UOJ client
    client = Client()
    results=[]
    full_msgs=[]
    usages=[]
    counted_trials = 0
    task = RepairInput(problem_id, problem_statement, submission_code,
                       submission_language=submission_language, metadata=metadata or {})
    session = solver.start_repair(task)
    while counted_trials < max_trials:
        try:
            transcript = session.transcript
            if transcript:
                full_msgs.append(transcript[-1])
            turn = session.next()
            full_msgs.append(turn.message)
            usages.append(turn.usage)
            if turn.candidate is None:
                session.record_feedback(SolverFeedback(FeedbackKind.INVALID_OUTPUT))
                counted_trials += 1
                continue

            new_code = apply_patch_to_code(submission_code, turn.candidate.patch)
            if similarity(new_code, submission_code) < 0.9:
                session.record_feedback(SolverFeedback(FeedbackKind.SIMILARITY_REJECTION))
                counted_trials += 1
                continue

            sub = SubmissionRequest(problem_id=problem_id, type='normal')
            sub.addSourceCodeText("answer", new_code, language=submission_language)

            result = client.makeBackgroundSubmission(sub)
            results.append(result)
            if 'result' in result and 'score' in result['result'] and result['result']['score'] == 100:
                return 1, session.transcript, results, full_msgs, usages

            session.record_feedback(SolverFeedback(FeedbackKind.JUDGE_REJECTED, result))
            counted_trials += 1
        except requests.exceptions.RequestException as e:
            print(f"Trial {counted_trials + 1} failed with request error: {e}")
            continue
        except json.JSONDecodeError as e:
            print(f"Trial {counted_trials + 1} failed with JSON parse error: {e}")
            continue
        except ValueError as e:
            session.record_feedback(SolverFeedback(FeedbackKind.PATCH_ERROR, str(e)))
            counted_trials += 1
            continue
        except Exception as e:
            session.record_feedback(SolverFeedback(FeedbackKind.RUNTIME_ERROR, str(e)))
            counted_trials += 1
            continue
    # If we get here, all trials failed
    return 0, session.transcript, results, full_msgs, usages

if __name__ == '__main__':    
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--file', type=str, default='dataset/small_submission_pairs.json', help='dataset file')
    parser.add_argument('--model', type=str, default="gpt-oss-120b", help='Model to use')
    parser.add_argument('--solver', default='prompt', metavar='NAME', help='Solver directory under solution/')
    parser.add_argument('--debug_idx', type=int, default=0, help='The index of debugging task that will be tested.')
    parser.add_argument('--max_trials', type=int, default=5, help='Max agent rounds.')
    args = parser.parse_args()

    with open(args.file, 'r', encoding='utf-8') as f:
        similar_codes = json.load(f)
    with open('dataset/problems.json', 'r', encoding='utf-8') as pf:
        _problems = json.load(pf)
        problems_by_id = {}
        for p in _problems:
            pid = p['problem_id']
            problems_by_id[pid] = p['statement_en']

    similar_code = similar_codes[args.debug_idx]
    problem_id = similar_code['problem_id']
    submission_id = similar_code['wrong_id']
    submission_code = similar_code['wrong_code']
    problem_statement = problems_by_id[problem_id]
    submission_language = similar_code['language']

    solver = load_solver(args.solver, args.model)
    score, message, results, full_msgs, usages = TestDebugAgent(solver, problem_id, problem_statement,
                                                                submission_code, submission_language,
                                                                args.max_trials, solver_metadata(similar_code))

    print(json.dumps({
        'debug_score': score,
        'results': results,
        'prompt': message,
        'full_msgs': full_msgs,
        'usages': usages,
    }, indent=2))
