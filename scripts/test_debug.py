import json
from solution import load_solver
from solution.api import RepairInput
from utils.benchmark import solver_metadata
from utils.uoj_api import SubmissionRequest, Client
from utils.patch import *

import Levenshtein

def similarity(a: str, b: str) -> float:
    dist = Levenshtein.distance(a, b)
    max_len = max(len(a), len(b))
    return 1 - dist / max_len

def TestDebug(solver, problem_id, problem_statement, submission_code, submission_language='C++20',
              chinese=False, metadata=None):
    submission_code = submission_code.replace('\r', '')

    client = Client()
    task = RepairInput(problem_id, problem_statement, submission_code,
                       submission_language=submission_language, chinese=chinese,
                       metadata=metadata or {})
    session = solver.start_repair(task)
    message = session.initial_request
    turn = session.next()
    full_msg, usage = turn.message, turn.usage
    if turn.candidate is None:
        return 0, message, turn.error, full_msg, usage
    patch = turn.candidate.patch

    # apply patch to code
    new_code = apply_patch_to_code(submission_code, patch)
    if similarity(new_code, submission_code) < 0.9:
        return 0, message, "similarity is too low", full_msg, usage
    
    sub = SubmissionRequest(problem_id=problem_id, type='normal')
    sub.addSourceCodeText("answer", new_code, language=submission_language)

    result = client.makeBackgroundSubmission(sub)
    if 'result' in result and 'score' in result['result'] and result['result']['score'] == 100:
        return 1, message, result, full_msg, usage
    else:
        return 0, message, result, full_msg, usage

if __name__ == '__main__':    
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--file', type=str, default='dataset/small_submission_pairs.json', help='dataset file')
    parser.add_argument('--model', type=str, default="gpt-oss-120b", help='Model to use')
    parser.add_argument('--solver', default='prompt', metavar='NAME', help='Solver directory under solution/')
    parser.add_argument('--debug_idx', type=int, default=0, help='The index of debugging task that will be tested.')
    parser.add_argument('--chinese', action='store_true', help='Use chinese input.')
    args = parser.parse_args()

    with open(args.file, 'r', encoding='utf-8') as f:
        similar_codes = json.load(f)
    with open('dataset/problems.json', 'r', encoding='utf-8') as pf:
        _problems = json.load(pf)
        problems_by_id = {}
        for p in _problems:
            pid = p['problem_id']
            problems_by_id[pid] = p['statement_zh' if args.chinese else 'statement_en']

    similar_code = similar_codes[args.debug_idx]
    problem_id = similar_code['problem_id']
    submission_id = similar_code['wrong_id']
    submission_code = similar_code['wrong_code']
    problem_statement = problems_by_id[problem_id]
    submission_language = similar_code['language']

    solver = load_solver(args.solver, args.model)
    score, message, result, full_msg, usage = TestDebug(solver, problem_id, problem_statement,
                                                        submission_code, submission_language,
                                                        args.chinese, solver_metadata(similar_code))

    print(json.dumps({
        'debug_score': score,
        'result': result,
        'prompt': message,
        'return_message': full_msg,
        'usage': usage,
    }, indent=2))
