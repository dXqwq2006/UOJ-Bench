"""Prompt text preserved from upstream UOJ-Bench."""

prompt_generation = """
You are an expert C++20 programmer. You will be given a question (problem specification) and will generate a correct C++20 program that matches the specification and gets as many points as possible you can.

### Question:
{problem}

Read the inputs from stdin solve the problem and write the answer to stdout (do not directly test on the sample inputs). Enclose your code within delimiters as follows. Ensure that when the C++ program runs, it reads the inputs, runs the algorithm and writes output to STDOUT.
```cpp
# YOUR CODE HERE
```

### Answer: (use the provided format with backticks)


"""

prompt_generation_chinese = """
你是一位精通算法竞赛的专家。你将拿到一道题目的题面，你需要为这个题目输出一份正确的C++20代码，完成题目的要求。

### 问题:
{problem}

你必须从 stdin 读入，从 stdout 输出，不要在样例输入上进行测试。按照如下格式输出你的代码。你需要确保你的程序直接从 stdin 读入输入数据，运行算法，并在 stdout 输出结果。
```cpp
# 你的代码
```

### 回答: (使用给定的带反引号的格式)


"""

prompt_hacking = """
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

prompt_hacking_chinese = """
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

try_again_prompt_hacking = "\nTry again! Output a new python code which would generate the correct hack data."

prompt_repair = """
You are an expert at fixing bugs in code. You will be given a buggy code and the complete description of the problem it intends to solve. Your job is to modify the code to make it correct while making as few changes as possible. The change must be expressed as a patch file that can be directly applied to the code using the patch command. Do not add any comments or explanations in the patch. Make sure your patch is minimal, i.e., the number of lines of code added or deleted is as small as possible. Enclose your patch within delimiters as follows.

```patch
# YOUR PATCH HERE
```

Here is an example of a patch file. It consists of changes to somean example code. It specifies the line numbers of each change, and the removed and added lines.

```patch
@@ -6,6 +6,6 @@
     int sum = 0;
\x20\x20\x20\x20\x20
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

prompt_repair_chinese = """
你是一位精通算法竞赛的专家。你将拿到一道题目的题面以及对应的一份有错误的代码，你需要给出一份能直接作用在给定代码上的 patch，使得 patch 的内容尽量少。依照如下格式把你的 patch 包括在反引号中，且不要在 patch 中添加注释：

```patch
# 你的 patch
```

以下是一份 patch 文件的例子，其描述了对一份简单代码的修改。你输出的 patch 文件必须指出修改的开始行号，以及删除添加的行数。

```patch
@@ -6,6 +6,6 @@
     int sum = 0;
\x20\x20\x20\x20\x20
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

try_again_prompt_repair = "\nTry again! Output a new patch which would be directly applied to the code given for the first time."


def generation(task) -> str:
    template = prompt_generation_chinese if task.chinese else prompt_generation
    return template.format(problem=task.problem_statement)


def hacking(task) -> str:
    template = prompt_hacking_chinese if task.chinese else prompt_hacking
    return template.format(problem=task.problem_statement, code=task.submission_code)


def repair(task) -> str:
    template = prompt_repair_chinese if task.chinese else prompt_repair
    return template.format(problem=task.problem_statement, code=task.submission_code)
