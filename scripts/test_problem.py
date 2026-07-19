import json

from solution import load_solver
from solution.api import GenerationInput
from utils.benchmark import solver_metadata
from utils.uoj_api import SubmissionRequest, Client

__all__ = 'TestProblemAgent'

def TestProblem(solver, problem_id, problem_statement, chinese=False, metadata=None):
    client = Client()
    task = GenerationInput(problem_id, problem_statement, chinese=chinese, metadata=metadata or {})
    session = solver.start_generation(task)
    message = session.initial_request
    turn = session.next()
    full_msg, usage = turn.message, turn.usage
    if turn.candidate is None:
        return 0, message, turn.error, full_msg, usage
    code = turn.candidate.source

    sub = SubmissionRequest(problem_id=problem_id, type='normal')
    sub.addSourceCodeText("answer", code, language="C++20")
    result = client.makeBackgroundSubmission(sub)

    score = result.get('result', {}).get('score', 0)

    return score, message, result, full_msg, usage

if __name__ == '__main__':    
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--model', type=str, default="gpt-oss-120b", help='Model to use')
    parser.add_argument('--solver', default='prompt', metavar='NAME', help='Solver directory under solution/')
    parser.add_argument('--problem_idx', type=int, default=0, help='The index of problem that will be tested.')
    parser.add_argument('--chinese', action='store_true', help='Use chinese input.')
    args = parser.parse_args()

    with open('dataset/problems.json', 'r', encoding='utf-8') as pf:
        problems = json.load(pf)

    problem = problems[args.problem_idx]
    problem_id = problem['problem_id']
    problem_statement = problem['statement_en'] if not args.chinese else problem['statement_zh']

    solver = load_solver(args.solver, args.model)
    score, message, result, full_msg, usage = TestProblem(solver, problem_id, problem_statement,
                                                          args.chinese, solver_metadata(problem))

    print(json.dumps({
        'score': score,
        'result': result,
        'prompt': message,
        'return_message': full_msg,
        'usage': usage,
    }, indent=2))
