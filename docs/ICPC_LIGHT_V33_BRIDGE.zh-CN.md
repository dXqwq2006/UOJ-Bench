# ICPC Light v3.3 solver bridge

## 接入结论

`solution/icpc_light_v33_bridge` 是 UOJ-Bench 原生 typed solver。它不修改
dataset、官方 prompt、UOJ client 或评分逻辑，而是把 Generation、Hacking、
Fault Coverage 与 Fault Exposure 输入转换成一个独立 JSON bridge job：

```text
UOJ / TestCase-Eval runner
  -> solution/icpc_light_v33_bridge
  -> integrations/icpc_light_v33/bin/icpc-light-uoj-bridge
  -> 每题独立 public-only workspace
  -> 冻结 skills + 配置的 task agent
  -> SolutionCandidate / HackCandidate / TestCaseCandidate
  -> 原生 UOJ / TestCase-Eval evaluator
```

能力固定为：

| Task | 状态 | Artifact |
| --- | --- | --- |
| Generation | one-shot | `output/main.cpp` |
| Hacking | one-shot | `output/candidate.in` 或 `output/generator.py` |
| Fault Coverage / TCE Task 1 / CC+V | one-shot；次数由原生 runner 控制 | `output/candidate.in` 或 `output/generator.py` |
| Fault Exposure / TCE Task 2 | one-shot | `output/candidate.in` 或 `output/generator.py` |
| Repair / feedback | 不支持 | fail closed |

模型合同是精确 `gpt-5.6-sol`、`reasoning_effort=ultra`。改变模型、effort、bundle、
config 或 agent command 都属于不同 pipeline identity。

## 目录

```text
solution/icpc_light_v33_bridge/       # UOJ typed adapter
integrations/icpc_light_v33/
  bin/                                # stdin/stdout bridge 与 reference agent
  contracts/                          # JSON Schema
  src/uoj_skill_bridge/               # public surface、artifact、receipt 控制面
  vendor/icpc-light-distilled-ver3.3.0/
  SKILL_BUNDLE.lock.json
scripts/smoke_icpc_light_v33_bridge.py
tests/fixtures/icpc_light_v33_bridge/ # 仅 deterministic smoke 使用
```

vendored bundle 只包含其 `MANIFEST.sha256` 白名单中的 106 个文件和 manifest 本身；
不包含嵌套 `.git`、`.DS_Store`、`__pycache__`、pyc 或运行状态。当前 lock：

```text
tree_sha256 = d6eef3006a438086adfc6c4695d2cd52d9262e929ab497ac4a8576671f283234
files       = 107
bytes       = 1637992
```

lock 同时保留原始 source manifest 与 `RELEASE.json` 的 SHA-256。发布副本只净化了
`RELEASE.json` 中一条宿主机专属 Docker socket 绝对路径，并在
`publication_redactions` 中记录；净化后的 manifest 另有独立 hash，不会冒充原始
release 字节。

## Bridge config

配置文件必须是当前用户拥有、不可被 group/other 写入的普通文件。示例中的 device 值必须在
目标机器上对精确目录调用 `stat().st_dev` 动态取得，不能复制别台机器的数字。

```json
{
  "schema_version": 1,
  "profile": "icpc-light-v33-uoj-production-v1",
  "workspace_root": "/absolute/system-filesystem/root/jobs",
  "workspace_device": 0,
  "skill_bundle_root": "/absolute/repo/integrations/icpc_light_v33/vendor/icpc-light-distilled-ver3.3.0",
  "skill_bundle_device": 0,
  "expected_skill_bundle_sha256": "d6eef3006a438086adfc6c4695d2cd52d9262e929ab497ac4a8576671f283234",
  "agent_command": ["/absolute/path/to/zero-mount-scheduler"],
  "timeout_seconds": 21600,
  "max_candidate_bytes": 4194304,
  "retain_workspaces": true
}
```

`expected_skill_bundle_sha256` 在启动 agent 前校验；receipt 同时记录实际 bundle SHA、复制后
skills tree SHA、config SHA、agent command 文件 SHA、request/surface/candidate SHA 和日志 SHA。
同一 benchmark 进程还会冻结第一份 config 与 pipeline signature，拒绝中途漂移。
TestCase-Eval 与 CC+V 的 SQLite manifest 会按 policy 绑定相同的稳定 signature，
跨进程恢复时也拒绝把另一套 bridge config、bundle 或 agent command 的完成结果混入
同一数据库。

环境变量：

```text
ICPC_LIGHT_UOJ_BRIDGE=/absolute/repo/integrations/icpc_light_v33/bin/icpc-light-uoj-bridge
ICPC_LIGHT_UOJ_BRIDGE_CONFIG=/absolute/control/bridge-config.json
ICPC_LIGHT_UOJ_BRIDGE_TIMEOUT_SECONDS=21630
PYTHONDONTWRITEBYTECODE=1
```

外层 timeout 必须至少比 config 内 agent timeout 多 30 秒。UOJ/model key 均不得放入 bridge
config、argv、workspace 或继承环境。

## Deterministic smoke

```bash
cd /absolute/path/to/UOJ-Bench
env -u UOJ_API_KEY -u TATU_API_KEY -u OPENAI_API_KEY \
  PYTHONDONTWRITEBYTECODE=1 \
  python -m scripts.smoke_icpc_light_v33_bridge \
  --uoj-root /absolute/path/to/UOJ-Bench \
  --output-root /absolute/system-filesystem/root/smoke-001
```

该 smoke 会机械验证 reviewed base 是当前 HEAD 的祖先、实际加载模块均来自当前 checkout，
并执行：

- Generation：v3.3 `build_sweep.py`、2 neutral + 2 deceptive `run_sweep.py`、独立
  `run_blind_review.py`，随后编译导出源码并跑 3 个语义用例；
- Hacking：public-only task slice，经原生 `run_hack_rollout_batch` 跑 Easy C++ 与
  Hard Python3 各一条，并用独立参考程序确认两条输入都能暴露错解；
- Fault Coverage：按 CC+V / TestCase-Eval Task 1 typed contract，仅暴露题面与公开
  metadata，确认工作区没有目标提交，并用隐藏在 evaluator 侧的错解/参考解做语义检查；
- Fault Exposure：按 TestCase-Eval Task 2 typed contract 产出原始输入，并用本地错误解和
  独立参考程序确认能够暴露目标错误提交；
- 5 个隔离 workspace、receipt、bundle/surface 不变性、非 C++ wrong source、无正确解泄漏。

CC+V 的完整运行仍由 `scripts.run_codecontests_plus` 完成：每题 20 个独立 bridge
job，随后使用数据集原生 validator、正确提交 oracle、checker 和 LightCP 执行层。
这些程序和评测资产不会进入 solver workspace。

fixture 是 test override，报告固定写
`deterministic-pipeline-smoke-test-override-no-model-no-uoj`。它证明 adapter、pipeline
脚本、artifact 与 receipt 接线，不证明真实模型质量，也不是 UOJ 官方得分。

## Production 边界

`icpc-light-uoj-codex-agent` 只是 public-only reference agent，不是安全沙箱。正式模型运行时，
config 的 `agent_command` 必须指向经过审核的零挂载 Docker/cgroup scheduler；输入输出只经
`docker cp`，agent 看不到完整 UOJ checkout、dataset、其他样本、UOJ key 或宿主文件系统。
完整服务器要求见 [zero-mount handoff](ICPC_LIGHT_V33_ZERO_MOUNT_HANDOFF.zh-CN.md)。
