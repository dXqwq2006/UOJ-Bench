import requests
import json
import time

from solution import load_solver
from solution.api import FeedbackKind, HackingInput, SolverFeedback
from utils.benchmark import solver_metadata
from utils.uoj_api import SubmissionRequest, Client

def TestHackAgent(solver, problem_id, problem_statement, submission_code, submission_language='C++20',
                  max_trials=10, metadata=None):
    # Initialize UOJ client
    client = Client()
    results=[]
    full_msgs=[]
    usages=[]
    counted_trials = 0
    task = HackingInput(problem_id, problem_statement, submission_code,
                        submission_language=submission_language, metadata=metadata or {})
    session = solver.start_hacking(task)
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

            sub = SubmissionRequest(problem_id=problem_id, type='hack')
            sub.addSourceCodeText("answer", submission_code, language=submission_language)
            sub.addHackInputText(turn.candidate.generator, language='Python3')
            sub.flagFormatInputFile() # auto-remove extra spaces in the input file

            result = client.makeBackgroundSubmission(sub)
            results.append(result)
            if 'result' in result and 'score' in result['result'] and result['result']['score'] == 1:
                return 1, session.transcript, results, full_msgs, usages

            session.record_feedback(SolverFeedback(FeedbackKind.JUDGE_REJECTED, result))
            counted_trials += 1
        except requests.exceptions.RequestException as e:
            print(f"Trial {counted_trials + 1} failed with request error: {e}")
            time.sleep(20)
            continue
        except Exception as e:
            print(f"Trial {counted_trials + 1} failed with unknown error: {e}")
            counted_trials += 1
            session.record_feedback(SolverFeedback(FeedbackKind.RUNTIME_ERROR, str(e)))
            continue
    # If we get here, all trials failed
    return 0, session.transcript, results, full_msgs, usages


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--file', type=str, default='dataset/hacks.json', help='dataset file')
    parser.add_argument('--model', type=str, default="gpt-oss-120b", help='Model to use')
    parser.add_argument('--solver', default='prompt', metavar='NAME', help='Solver directory under solution/')
    parser.add_argument('--hack_idx', type=int, default=0, help='The index of hack that will be tested.')
    parser.add_argument('--max_trials', type=int, default=5, help='Max agent rounds.')
    args = parser.parse_args()

    with open(args.file, 'r', encoding='utf-8') as f:
        hacks = json.load(f)
    with open('dataset/problems.json', 'r', encoding='utf-8') as pf:
        _problems = json.load(pf)
        problems_by_id = {}
        for p in _problems:
            if p['hackable']:
                pid = p['problem_id']
                problems_by_id[pid] = p['statement_en']

    hack = hacks[args.hack_idx]
    hack_id = hack['hack_id']
    submission_id = hack['submission_id']
    problem_id = hack['problem_id']
    submission_code = hack['wrong_code']
    submission_language = hack['language']
    problem_statement = problems_by_id[problem_id]

    solver = load_solver(args.solver, args.model)
    score, message, results, full_msgs, usages = TestHackAgent(solver, problem_id, problem_statement,
                                                               submission_code, submission_language,
                                                               args.max_trials, solver_metadata(hack))

    print(json.dumps({
        'hack_score': score,
        'results': results,
        'prompt': message,
        'full_msgs': full_msgs,
        'usages': usages,
    }, indent=2))
