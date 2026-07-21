# ICPC Light v3.3 × UOJ-Bench：zero-mount server handoff

本文只描述新 UOJ integration。不要复用其他实验的 run/state/preflight、容器、目录、端口、
private 数据或 credential，也不要执行包含 bind mount/Compose volume 的旧部署命令。

## 不可变规则

- 新根必须是个人目录下 mode 0700 的真实目录，位于审核允许的 Linux 系统文件系统；不用
  symlink、共享盘、Windows/guest/data mount。
- 服务器不执行 `git clone/pull`；在受信机器生成白名单 source archive、SHA-256 后传入。
- 不做宿主 apt、pip、npm、uv、conda 或系统配置修改；只用服务器已经审核存在的工具。
- 所有新容器在 create 后、start 前和终态都必须证明 `Mounts=[]`；镜像
  `Config.Volumes` 必须为空。
- 禁止 `-v`、`--volume`、`--mount`、`--tmpfs`、Compose volumes、Docker socket、DinD
  和 `--device`。输入输出只经 `docker cp` 与隔离 quarantine。
- 不读取、停止、删除、重建、连接网络或 retag 任何既有实验容器/镜像。
- UOJ key 只进入 evaluator；模型 key 只进入 credential relay stdin。二者不能进入对方的
  env、argv、容器、文件、日志或 receipt。

## 推荐布局

```text
<NEW_ROOT>/
  software/UOJ-Bench/              # 精确 commit 的白名单 archive
  control/                         # config、preflight、只读 identities
  jobs/                            # bridge 每题 0700 workspace
  quarantine/                      # docker cp 输出先落这里
  runs/                            # UOJ rollout/evaluation 结果
  supply-chain/                    # archive/image/config hashes
```

上述所有非凭据目录应由同一 operator 拥有。UOJ/model credential 由彼此独立、最小权限的
evaluator/relay principal 临时注入，不落到 `<NEW_ROOT>`。创建 job 前冻结并记录：UOJ commit、
integration manifest、skill bundle lock、bridge config canonical SHA、agent/relay image ID、模型
route/effort，以及目标文件系统 source/fstype/device。

## Keyless preflight

在任何模型或 UOJ 请求前：

1. 校验 source archive 与 `integrations/icpc_light_v33/MANIFEST.sha256`。
2. 校验 vendored bundle 自身 `MANIFEST.sha256` 和 `SKILL_BUNDLE.lock.json`。
3. 为 `workspace_root` 设置 0700，确认 owner、非 symlink、允许的 system filesystem 与
   config 中 `workspace_device` 一致。
4. 检查 bridge config 为 0600/不可被 group/other 写入；其中
   `expected_skill_bundle_sha256` 必须等于 lock。
5. 对将要使用的精确 image ID 检查 `Config.Volumes` 为空；不要读取现有容器来代替候选验证。
6. 先运行无 credential deterministic smoke。任何 mismatch、额外文件、symlink、hardlink、
   超时、日志/空间越界或 artifact contract 错误都停止。

## Production scheduler 的最低合同

仓库内 host bridge 会限制单文件、日志、workspace 总量并管理 agent process group，但它不是
物理隔离边界。零挂载 scheduler 还必须负责：

- 按题创建独立 container/cgroup，限制 CPU、RSS、PID、wall time、output 和网络；
- create 后先 inspect 空 mounts，再用 `docker cp` 写入 public surface 与 frozen skills；
- agent 只连接 credential relay 网络，不能访问 UOJ、互联网或宿主 Docker socket；
- wait 精确 container ID，终态 inspect 后 `docker cp` 到 quarantine；
- 拒绝 symlink、hardlink、special file、额外 artifact 与执行期间变化；
- 保存 image/container/network/copy/cleanup receipts，并只回收本 profile 拥有的资源。

integration 已提供 `bin/icpc-light-uoj-zero-mount-scheduler`。生产 config 必须把其绝对路径、
不可变 xhigh agent image ID、专用 relay 名称和不可变 relay image ID 全部写入
`agent_command`，并追加 `--integration-manifest-sha256`；这些值会进入 pipeline signature。
scheduler 会逐文件核验该 manifest，不接受只绑定入口脚本的配置。
`docker/agent-xhigh.Dockerfile` 从已审核
v3.3 agent image 派生，只替换冻结的 Codex provider gate，并把实际 API effort 固定为
`xhigh`。vendored v3.3 文档里的旧 `ultra` 字样属于上游发布物，不能作为实际调用参数；
正式 receipt、bridge request、Codex argv 和派生 image label 必须全部是 `xhigh`。

## 两阶段 Hacking

Hacking 应保持 credential 分离：

1. 无 UOJ key 的 rollout 进程调用 skills，生成并持久化 candidate；
2. 停止模型侧进程/relay 后，由无模型 credential 的 evaluator 读取 rollout，调用 UOJ；
3. `unavailable_problem` 是终态失败；quota/transport/API 故障保持 retryable，不转成模型反馈。

Generation 同样先持久化 candidate 与完整 pipeline identity，再由独立 UOJ normal submission
评分。任何 resume 都必须验证 adapter、bundle、config、image、model route 与 result manifest
完全一致；当前上游 batch manifest 尚未自动绑定全部这些字段，正式全量前必须由外层 launcher
补齐。
