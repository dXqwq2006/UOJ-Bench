# Benchmark、任务与 Solver 清单

本文记录本仓库当前已经接入的 benchmark、任务、solver/competitor、评测后端和
可追溯出处。UOJ 兼容边界固定到 `ce1c006d`，ICPC Light bridge 的 reviewed base
固定到 `e31cc22c`；运行中的样本数、费用和 ETA 属于结果目录状态，不写进这份
长期清单。

## 术语与边界

- **Benchmark**：定义数据集、任务协议和评分方式，例如 UOJ-Bench。
- **Task**：benchmark 内的一种输入、输出和评分合同，例如 Hacking。
- **Solver / competitor**：`solution/<name>/` 下可替换的 LLM pipeline。本文中的
  competitor 指 pipeline，不指模型。
- **Model / deployment**：solver 调用的推理模型及路由，例如
  `gpt-5.6-sol@CODING_TATU` 或本地 `gpt-oss-120b`。这是独立实验变量。
- **Judge**：消费候选结果并计分的后端，例如 UOJ、Docker 或 LightCPVerifier；
  judge 不是 competitor。

仓库刻意只把 LLM pipeline 放在 `solution/`。数据准备、任务调度、评测、持久化
和统计仍由 benchmark 工具层负责。

## Benchmark 清单

| Benchmark | 当前状态 | 已接任务 | 评测位置 | 上游与固定版本 |
| --- | --- | --- | --- | --- |
| UOJ-Bench | 已接入，保持上游语义 | Generation、Hacking、Repair；Hacking/Repair 另有多轮 runner | UOJ API / UOJ 原生 judge | [官方仓库](https://github.com/hehezhou/UOJ-Bench)、[论文](https://arxiv.org/abs/2606.12864)、固定 [`ce1c006d`](https://github.com/hehezhou/UOJ-Bench/commit/ce1c006d9f6cf57670d15e62c3e63a08ea669adb) |
| TestCase-Eval | Task 1、Task 2 均已接入 | Fault Coverage、Fault Exposure | 下载数据后离线；Docker 或 LightCPVerifier | [官方仓库](https://github.com/FlowRays/TestCase-Eval)、[ACL 2025 论文](https://aclanthology.org/2025.acl-short.82/)、固定 [`45275c6f`](https://github.com/FlowRays/TestCase-Eval/commit/45275c6f838566e6e148a9eca18edc00be08a305) |
| CodeContests+ Verified | 已接入 Verified fault-coverage adapter | Fault Coverage | 下载数据后离线；原生 validator/checker/oracle + LightCPVerifier | [数据集](https://huggingface.co/datasets/ByteDance-Seed/Code-Contests-Plus)、[论文](https://arxiv.org/abs/2506.05817)、固定 revision `96c85054` |

## Task 清单

| Benchmark / task | Solver 输入 | Solver 输出 | 交互 | 数据规模 | 评分方式 | 入口 |
| --- | --- | --- | --- | --- | --- | --- |
| UOJ Generation | 题面 | C++20 完整解 | 单轮 | `problems.json`：672 题 | UOJ normal submission，分数 0--100 | `scripts.test_problem` |
| UOJ Hacking（单轮） | 题面、错误提交及语言 | 生成测试输入的 Python 3 程序 | 单轮 | 默认 Hard 1046 条；也可指定 Easy 数据文件 | UOJ hack submission，成功为 1 | `scripts.test_hack` |
| UOJ Hacking（agent） | 同上；每次失败后加入公开 judge/解析反馈 | 每轮一份新的 Python 3 generator | 多轮，共享本 session 上下文 | 论文 Easy 479 条有效样本、Hard 1046 条 | Pass@1 至 Pass@K；API 故障不消耗 trial | `scripts.test_hack_agent`、`scripts.run_hack_agent_batch` |
| UOJ Repair（单轮） | 题面、错误代码及语言 | 搜索/替换 patch | 单轮 | `small_submission_pairs.json`：216 对 | 修复前后 Levenshtein 相似度至少 0.9，且 UOJ 得分 100 | `scripts.test_debug` |
| UOJ Repair（agent） | 同上；每次失败后加入 patch、相似度或 judge 反馈 | 每轮一份新 patch | 多轮，共享本 session 上下文 | 同 Repair 数据 | 最终满足相似度门槛并通过 UOJ | `scripts.test_debug_agent` |
| TestCase-Eval Task 1 / Fault Coverage | 一道 Codeforces 题面 | 每次调用一个原始测试输入 | 20 次相互独立的单轮生成 | 500 题；`submission_all` 含 118,611 个错误提交 | Coverage@1/5/10/20：前 N 个测试杀死错误提交的覆盖率 | `scripts.test_testcase_eval_task1`、`scripts.run_testcase_eval_batch` |
| TestCase-Eval Task 2 / Fault Exposure | 题面、一个指定错误提交及语言 | 一个针对该提交的原始测试输入 | 每个提交一次单轮生成 | `submission_lite`：10,000 个错误提交 | Fault Exposure：生成输入杀死目标错误提交的比例，并按难度、语言、verdict 分组 | `scripts.test_testcase_eval_task2`、`scripts.run_testcase_eval_batch` |
| CodeContests+ Verified / Fault Coverage | 一道 Verified 题面与公开元数据 | 每次调用一个测试输入或 Python generator | 每题 20 次相互独立的单轮生成 | Verified population；正式实验确定性抽样 500 题 | validator 过滤输入；正确提交形成 oracle；checker 统计 valid rate、TPR、TNR | `scripts.run_codecontests_plus` |

UOJ 的 Easy 源文件 `sampled_large_submission_pairs.json` 有 500 条记录，官方
runner 按可 hack 条件筛选后实际为 479 条。`sampled_ac_submissions.json` 另含
5,060 个 AC 提交，属于上游 open-hack 数据资产；当前没有对应的完整批处理入口，
因此不把它列为已经接入的新 task。

TestCase-Eval 的 accepted submissions 用于 oracle 共识，不计作要杀死的目标。
完整官方两任务共需 20,000 次主模型调用；加入 `prompt` 的 Task 2 control 后为
30,000 次。调用量和 execution 数的详细预算见 `README_SOLVER.md`。

## Solver / competitor 清单

### `prompt`

- 出处：本仓库固定的 UOJ-Bench `ce1c006d` 原始 prompt、fence parser 和反馈文本。
- 定位：UOJ-Bench 官方 baseline solver，也是 TestCase-Eval Task 2 的跨框架 control。
- UOJ Generation/Hacking/Repair 的 prompt、解析和多轮反馈通过 differential tests
  与固定上游行为对齐。
- 在 TestCase-Eval Task 2 中仍先生成 Python generator，再由 adapter 物化为一个
  原始输入；因此可比较的是 harness/prompt 效果，但它不是 TestCase-Eval 论文方法。
- 支持 UOJ Hacking/Repair 的多轮反馈；不支持 Fault Coverage。

### `testcase_eval`

- 出处：TestCase-Eval 固定 commit `45275c6f` 及固定 Hugging Face prompt
  snapshots。
- 定位：TestCase-Eval 论文的一次调用 CoT pipeline。
- Task 1 与 Task 2 均使用论文 prompt、抽取正则、固定
  `gpt-4.1-mini` structured-output fallback、oracle 共识、比较器与统计流程。
- 只支持单轮，`hacking_feedback=False`；不支持 UOJ Generation、Repair 或
  agentic 多轮 Hacking。
- 技术上可经 `start_hacking` adapter 把原始输入包装成 Python generator，
  但这不构成其论文中的 UOJ-Bench 结果。

### `icpc_light_v33_bridge`

- 出处：仓库内 manifest/lock 固定的 ICPC Light v3.3 skill bundle 与独立 JSON
  bridge；配置固定精确 `gpt-5.6-sol`、`reasoning_effort=xhigh`。
- 支持 UOJ Generation/Hacking、TestCase-Eval Task 2 Fault Exposure，以及
  TestCase-Eval Task 1 / CodeContests+ Verified Fault Coverage 的 one-shot typed
  contract；不支持 Repair 或 judge feedback。
- Hacking pipeline 可以产出一个 raw input，adapter 按 UOJ-Bench 合同机械包装成
  Python 3 generator；最终评分仍由原 UOJ evaluator 完成。
- Task 2 保留 raw input / Python generator 的 TestCase-Eval candidate 格式，由
  TestCase-Eval 本地执行与评分，不调用 UOJ。结果库绑定 bridge pipeline signature。
- Fault Coverage 只接收公开题面与 allowlist metadata，不接收正确/错误提交、validator、
  checker 或 oracle。每次返回一个 candidate；20 次预算及评分完全由原生 runner 控制。
- deterministic smoke 已覆盖 Generation 的 2 neutral + 2 deceptive sweep/blind
  review、Easy C++ 与 Hard Python3 两条 Hacking rollout，以及 Fault Coverage 和
  Fault Exposure。该 smoke 注入测试 worker，不调用模型或 UOJ，不是 benchmark 成绩。
- 生产环境必须使用零挂载隔离 scheduler；reference agent 不是安全边界。详见
  [`ICPC_LIGHT_V33_BRIDGE.zh-CN.md`](ICPC_LIGHT_V33_BRIDGE.zh-CN.md)。

### 能力与可比性矩阵

| Task | `prompt` | `testcase_eval` | `icpc_light_v33_bridge` |
| --- | --- | --- | --- |
| UOJ Generation | **官方 baseline** | 不支持 | one-shot pipeline |
| UOJ Hacking，单轮 | **官方 baseline** | 技术可运行；非论文对照 | one-shot pipeline |
| UOJ Hacking，agent | **官方 baseline** | 不支持反馈 | 不支持反馈 |
| UOJ Repair，单轮/agent | **官方 baseline** | 不支持 | 不支持 |
| TestCase-Eval Task 1 | 不支持 | **论文流程** | one-shot pipeline |
| TestCase-Eval Task 2 | 跨框架 control | **论文流程** | one-shot pipeline |
| CodeContests+ Verified | 不支持 | Task 1 CoT baseline | one-shot pipeline |

`solution/llm/` 是共享的 TATU/OpenRouter/OpenAI-compatible transport，不是一个
solver，也不应作为 competitor 计数。模型名、reasoning effort、token limit、
temperature、deployer 和 endpoint 都应记录在每次结果的 manifest 中，不能用模型名
代替 solver 名。

新的论文方法只需新增 `solution/<paper_name>/` 并导出
`build_solver(model)`。若它仍遵守现有五种 typed task contracts，就复用现有
runner；只有任务协议或评分合同发生变化时才新增 `scripts/test_paper_xxx.py`。

## 评测后端

| 后端 | 用途 | 边界 |
| --- | --- | --- |
| [UOJ](https://uoj.ac/) | UOJ-Bench 三个核心任务 | 通过上游 `utils/uoj_api.py` 与 [API application](https://uoj.ac/api-application)；需要 UOJ quota |
| TestCase-Eval pinned Docker judge | TestCase-Eval Task 1/2 默认后端 | 网络隔离、非 root，按固定工具链执行提交 |
| [LightCPVerifier](https://github.com/YanagiOrigami/LightCPVerifier) | TestCase-Eval 可选加速后端 | 只替换执行层；结果 manifest 记录 backend 和 toolchain fingerprint |
| CodeContests+ LightCP profile | CodeContests+ validator、编译审计、oracle、checker 与提交执行 | 独立 evaluator fingerprint；不会把评测程序暴露给 solver |

LightCPVerifier 构建于 [go-judge](https://github.com/criyle/go-judge)。切换 judge
build 时必须使用新的结果目录，避免不同工具链的 execution rows 混合。

## 固定数据与 prompt 版本

所有 TestCase-Eval revision 都在
`utils/testcase_eval_benchmark.py::DATASETS` 中 fail-closed 校验：

| 角色 | Hugging Face dataset | Revision |
| --- | --- | --- |
| 题面 | [`TestCase-Eval/problem`](https://huggingface.co/datasets/TestCase-Eval/problem) | `b5cc0cc4589f5e38c1b010c24a4c5f513009278e` |
| Task 1 submissions | [`TestCase-Eval/submission_all`](https://huggingface.co/datasets/TestCase-Eval/submission_all) | `7de1bb5d7b3143418147a84d34594be162ef7821` |
| Task 2 submissions | [`TestCase-Eval/submission_lite`](https://huggingface.co/datasets/TestCase-Eval/submission_lite) | `96affb6b416002ed36bab881834e38a8c07b0647` |
| Task 1 CoT prompt | [`Raywithyou/TestCase-Eval-Task1`](https://huggingface.co/datasets/Raywithyou/TestCase-Eval-Task1) | `bd8b0e2e26e1e52225ca41537eaff592142cbc85` |
| Task 2 CoT prompt | [`Raywithyou/TestCase-Eval-Task2`](https://huggingface.co/datasets/Raywithyou/TestCase-Eval-Task2) | `ad6c3af216b088652b6f05d7df331b3858bf916d` |

UOJ-Bench 的 `dataset/*.json`、原始 prompt、UOJ client、patch helper、评分代码和
官方 `README.md` 均由 boundary test 对照 `ce1c006d`；不要在适配新 solver 时
修改这些对象。

## 当前仓库之外

- [CPIdeas-Compare](https://github.com/marine-reptile/CPIdeas-Compare) 仅作为外部
  实现对照阅读；本仓库继续以自己的固定上游复现为准，没有导入它的 prompt 或代码。

## 版本锚点

- `baseline/solver-abstraction-pre-testcase-eval`：只完成 UOJ-Bench solver
  抽象化、尚未接入 TestCase-Eval 的保底点。
- `milestone/testcase-eval-task2-integrated`：TestCase-Eval Task 2 接入里程碑。
- 本文描述的是当前 checkout 能力；tag 是历史回退/审计锚点，不代表当前运行进度。
