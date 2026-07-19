import json

from solution import load_solver
from utils.uoj_api import SubmissionRequest, Client
from utils.solver import HackingInput, resolve_solver, solver_metadata

prompt = """
You are an expert at breaking buggy code. You will be given a buggy code and the complete description of the problem it intends to solve. Your task is to find a valid input, respecting the input format and constraints, that causes the code to fail (e.g., produces a Wrong Answer or exceeds the time limit).

Write a python program to print this failing test-case. Enclose your code within delimiters as follows.
```python
# YOUR CODE HERE
```

### Question:
{problem}

### Code:
{code}

### Answer: (use the provided format with backticks)

"""

prompt_chinese = """
你是一位精通算法竞赛的 hack 专家。你将拿到一道题目的题面以及对应的一份有错误的代码。你需要找到一份符合题面输入格式的合法输入，使得给定的代码不能通过这组测试数据（输出错误答案或超时）。

给出一份 python 代码输出这组输入数据。你需要把你的代码包在如下格式的反引号中：

```python
# 你的代码
```

### 题面:
{problem}

### 代码:
{code}

### 答案: (使用给定的带反引号的格式)

"""

try_again_prompt = "\nTry again! Output a new python code which would generate the correct hack data."

def TestHack(model, problem_id, problem_statement, submission_code, submission_language='C++20',
             chinese=False, metadata=None):
    # Initialize UOJ client
    client = Client()
    use_prompt = prompt_chinese if chinese else prompt
    message = use_prompt.format(problem=problem_statement, code=submission_code)

    task = HackingInput(problem_id, problem_statement, submission_code, message,
                        submission_language, metadata or {})
    turn = resolve_solver(model).start_hacking(task).next()
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
    parser.add_argument('--solver', metavar='NAME', help='Solver directory under solution/')
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

    solver = load_solver(args.solver, args.model) if args.solver else args.model
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
