import json

from utils.uoj_api import SubmissionRequest, Client
from utils.solver import GenerationInput, resolve_solver

__all__ = 'TestProblemAgent'

prompt = """
You are an expert C++20 programmer. You will be given a question (problem specification) and will generate a correct C++20 program that matches the specification and gets as many points as possible you can.

### Question:
{problem}

Read the inputs from stdin solve the problem and write the answer to stdout (do not directly test on the sample inputs). Enclose your code within delimiters as follows. Ensure that when the C++ program runs, it reads the inputs, runs the algorithm and writes output to STDOUT.
```cpp
# YOUR CODE HERE
```

### Answer: (use the provided format with backticks)


"""

prompt_chinese = """
你是一位精通算法竞赛的专家。你将拿到一道题目的题面，你需要为这个题目输出一份正确的C++20代码，完成题目的要求。

### 问题:
{problem}

你必须从 stdin 读入，从 stdout 输出，不要在样例输入上进行测试。按照如下格式输出你的代码。你需要确保你的程序直接从 stdin 读入输入数据，运行算法，并在 stdout 输出结果。
```cpp
# 你的代码
```

### 回答: (使用给定的带反引号的格式)


"""


def TestProblem(model, problem_id, problem_statement, chinese=False, metadata=None):
    client = Client()
    use_prompt = prompt_chinese if chinese else prompt
    message = use_prompt.format(problem=problem_statement)

    task = GenerationInput(problem_id, problem_statement, message, metadata=metadata or {})
    turn = resolve_solver(model).start_generation(task).next()
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
    parser.add_argument('--problem_idx', type=int, default=0, help='The index of problem that will be tested.')
    parser.add_argument('--chinese', action='store_true', help='Use chinese input.')
    args = parser.parse_args()

    with open('dataset/problems.json', 'r', encoding='utf-8') as pf:
        problems = json.load(pf)

    problem = problems[args.problem_idx]
    problem_id = problem['problem_id']
    problem_statement = problem['statement_en'] if not args.chinese else problem['statement_zh']

    score, message, result, full_msg, usage = TestProblem(args.model, problem_id, problem_statement,
                                                          args.chinese, problem)

    print(json.dumps({
        'score': score,
        'result': result,
        'prompt': message,
        'return_message': full_msg,
        'usage': usage,
    }, indent=2))
