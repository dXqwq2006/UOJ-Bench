import json

from solution import load_solver
from solution.api import HackingInput
from utils.benchmark import solver_metadata
from utils.uoj_api import SubmissionRequest, Client

def TestHack(solver, problem_id, problem_statement, submission_code, submission_language='C++20',
             chinese=False, metadata=None):
    # Initialize UOJ client
    client = Client()
    task = HackingInput(problem_id, problem_statement, submission_code,
                        submission_language=submission_language, chinese=chinese,
                        metadata=metadata or {})
    session = solver.start_hacking(task)
    message = session.initial_request
    turn = session.next()
    full_msg, usage = turn.message, turn.usage
    if turn.candidate is None:
        return 0, message, turn.error, full_msg, usage
    code = turn.candidate.generator

    sub = SubmissionRequest(problem_id=problem_id, type='hack')
    sub.addSourceCodeText("answer", submission_code, language=submission_language)
    sub.addHackInputText(code, language='Python3')
    sub.flagFormatInputFile() # auto-remove extra spaces in the input file

    result = client.makeBackgroundSubmission(sub)
    
    if 'result' in result and 'score' in result['result'] and result['result']['score'] == 1:
        return 1, message, result, full_msg, usage
    else:
        return 0, message, result, full_msg, usage


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--file', type=str, default='dataset/hacks.json', help='dataset file')
    parser.add_argument('--model', type=str, default="gpt-oss-120b", help='Model to use')
    parser.add_argument('--solver', default='prompt', metavar='NAME', help='Solver directory under solution/')
    parser.add_argument('--hack_idx', type=int, default=0, help='The index of hack that will be tested.')
    parser.add_argument('--chinese', action='store_true', help='Use chinese input.')
    args = parser.parse_args()

    with open(args.file, 'r', encoding='utf-8') as f:
        hacks = json.load(f)
    with open('dataset/problems.json', 'r', encoding='utf-8') as pf:
        _problems = json.load(pf)
        problems_by_id = {}
        for p in _problems:
            if p['hackable']:
                pid = p['problem_id']
                problems_by_id[pid] = p['statement_zh' if args.chinese else 'statement_en']

    hack = hacks[args.hack_idx]
    hack_id = hack['hack_id']
    submission_id = hack['submission_id']
    problem_id = hack['problem_id']
    submission_code = hack['wrong_code']
    submission_language = hack['language']
    problem_statement = problems_by_id[problem_id]

    solver = load_solver(args.solver, args.model)
    score, message, result, full_msg, usage = TestHack(solver, problem_id, problem_statement,
                                                       submission_code, submission_language,
                                                       args.chinese, solver_metadata(hack))

    print(json.dumps({
        'hack_score': score,
        'result': result,
        'prompt': message,
        'return_message': full_msg,
        'usage': usage,
    }, indent=2))
