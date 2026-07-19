import json
from solution import load_solver
from utils.uoj_api import SubmissionRequest, Client
from utils.patch import *
from utils.solver import RepairInput, resolve_solver, solver_metadata

prompt = """
You are an expert at fixing bugs in code. You will be given a buggy code and the complete description of the problem it intends to solve. Your job is to modify the code to make it correct while making as few changes as possible. The change must be expressed as a patch file that can be directly applied to the code using the patch command. Do not add any comments or explanations in the patch. Make sure your patch is minimal, i.e., the number of lines of code added or deleted is as small as possible. Enclose your patch within delimiters as follows.

```patch
# YOUR PATCH HERE
```

Here is an example of a patch file. It consists of changes to somean example code. It specifies the line numbers of each change, and the removed and added lines.

```patch
@@ -6,6 +6,6 @@
     int sum = 0;
     
-    for (int i = 0; i <= 5; i++) {{
+    for (int i = 0; i < 5; i++) {{
         sum += arr[i];
     }}
```


### Question:
{problem}

### Code:
{code}

### Answer: (use the provided format with backticks)


"""

prompt_chinese = """
你是一位精通算法竞赛的专家。你将拿到一道题目的题面以及对应的一份有错误的代码，你需要给出一份能直接作用在给定代码上的 patch，使得 patch 的内容尽量少。依照如下格式把你的 patch 包括在反引号中，且不要在 patch 中添加注释：

```patch
# 你的 patch
```

以下是一份 patch 文件的例子，其描述了对一份简单代码的修改。你输出的 patch 文件必须指出修改的开始行号，以及删除添加的行数。

```patch
@@ -6,6 +6,6 @@
     int sum = 0;
     
-    for (int i = 0; i <= 5; i++) {{
+    for (int i = 0; i < 5; i++) {{
         sum += arr[i];
     }}
```


### 题目描述:
{problem}

### 错误代码:
{code}

### 回答: (使用给定的带反引号的格式)


"""

try_again_prompt = "\nTry again! Output a new patch which would be directly applied to the code given for the first time."

import Levenshtein

def similarity(a: str, b: str) -> float:
    dist = Levenshtein.distance(a, b)
    max_len = max(len(a), len(b))
    return 1 - dist / max_len

def TestDebug(model, problem_id, problem_statement, submission_code, submission_language='C++20',
              chinese=False, metadata=None):
    submission_code = submission_code.replace('\r', '')

    client = Client()
    use_prompt = prompt_chinese if chinese else prompt
    message = use_prompt.format(problem=problem_statement, code=submission_code)

    task = RepairInput(problem_id, problem_statement, submission_code, message,
                       submission_language, metadata or {})
    turn = resolve_solver(model).start_repair(task).next()
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
    parser.add_argument('--solver', metavar='NAME', help='Solver directory under solution/')
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

    solver = load_solver(args.solver, args.model) if args.solver else args.model
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
