# 数据库内核成本首轮观测

2026-11-05。已完成磁盘卷上的一次预热、三次固定测量，保留全部数据与原有事务保证。**本轮没有证据支持把事务提交、WAL 刷盘或锁阻塞认定为这个短串行负载的主要耗时来源；也没有证明这些模块可以删除。** 客户端 JDBC 调用与服务端已统计 SQL 阶段存在较大时间差，需要保持未归因状态，不能直接称为网络或协议开销。

## 实验条件与能力保持

- 使用原始冻结反编译 JAR 与适配器，不包含此前任何模块删减；仅给运行器增加业务统计范围控制，给 JDBC 提交/回滚增加计时。
- 在 Docker Desktop 的新建命名卷运行 PostgreSQL 18。实际文件系统为 VM 内 `/dev/vda1` 上的 ext4，挂载于 `/var/lib/postgresql`，不是 tmpfs。它仍包含虚拟化和宿主缓存层，本轮不验证宿主掉电持久性。
- `fsync=on`、`synchronous_commit=on`、`full_page_writes=on`、`wal_level=replica`、autovacuum 保持开启。业务连接继续使用 SERIALIZABLE，回滚逻辑未变。
- 固定已有 holdout 的 17 条命令，包含全部五类事务：10 次提交、3 次业务回滚、4 次输入拒绝。一个 JVM 内先预热一次，再测三次，每次使用独立恢复的相同完整数据库。
- 每个数据库恢复后执行 ANALYZE 和完整初始状态验证；每轮观测前执行 CHECKPOINT，计时窗口不包含该 CHECKPOINT。该设计是 checkpoint 后的短负载，不代表稳态 checkpoint 成本；首批页面修改会产生 full-page images，WAL 量不能直接外推为稳态平均值。

没有削弱持久化、关闭锁或更换事务隔离，没有改变 SQL 或生成新业务数据。一次预热不保证完全排除 JIT、缓存及测量噪声，三轮数据是有界诊断，不是吞吐基准或统计显著性实验。

## 测量边界

原观测只计 SQL 执行，没有单独计 COMMIT/ROLLBACK，且数据库统计可能混入完整状态验证。本轮补充了两处明确的源码插桩：

1. 服务器默认 `pg_stat_statements.track=none`，进入业务事务前 `SET LOCAL ...='top'`，事务结束自动恢复，避免把前后状态验证计入业务 SQL 统计；排除用于命令标签的 `set_config` 统计。顶层统计避免把内部触发器/SPI 子查询与父查询重复相加。
2. 对原 JDBC 代理的 commit/rollback 调用增加计时；已有 executeQuery/executeUpdate/executeBatch 计时继续使用。业务区间排除前后状态查询，但包含标签、应用对象处理和观测自身的部分开销。

另以请求间隔 10 ms 的独立连接采样业务会话状态、等待事件及阻塞者；实际查询与调度也耗时，间隔并非严格 10 ms。WAL/IO 统计采用整个回放窗口前后差值，覆盖观察查询和后台活动，不能冒充完全隔离的业务计时。

`pg_stat_statements` 提供规划和执行统计，不是完整后端 CPU 剖析。[官方说明](https://www.postgresql.org/docs/18/pgstatstatements.html) PostgreSQL 18 的 WAL I/O 时间在 `pg_stat_io` 中通过 `object='wal'` 读取，需开启 `track_wal_io_timing`；本轮按运行版本采集，未假设旧版本的 WAL 时间字段仍存在。[统计说明](https://www.postgresql.org/docs/18/monitoring-stats.html)

## 客户端业务时间

以下每列都是一轮 **13 个被接受事务**的累计值，另四条输入拒绝不执行业务 SQL。各行的执行、提交、回滚和剩余部分构成该行总时间。

| 测量轮次 | 业务总时间 ms | JDBC execute 合计 ms | 10 次 commit 合计 ms | 3 次 rollback 合计 ms | 剩余 ms |
|---|---:|---:|---:|---:|---:|
| trial1 | 115.20 | 89.76 | 6.92 | 0.76 | 17.76 |
| trial2 | 107.59 | 86.11 | 7.17 | 0.83 | 13.48 |
| trial3 | 108.35 | 91.26 | 5.10 | 0.86 | 11.13 |

每轮均有 184 次 executeQuery、71 次 executeUpdate、12 次 executeBatch。COMMIT 调用累计占业务区间 **4.7%～6.7%**；这不是纯刷盘占比，也不是“去掉事务模块”的收益上界，因为事务管理的其他工作还发生在语句内部。

预热轮累计 251.50 ms，已排除在上述测量和摘要之外。分项中位数不一定相加得到总时间中位数，报告不把它们当作一轮可相加的数据。

## 服务端与 WAL：独立视角，不叠加

| 测量轮次 | 业务 SQL 规划 ms | 业务 SQL 执行 ms | client backend WAL 写入 ms | client backend WAL fsync ms |
|---|---:|---:|---:|---:|
| trial1 | 3.79 | 7.75 | 1.90 | 2.30 |
| trial2 | 3.63 | 7.13 | 2.40 | 2.18 |
| trial3 | 3.63 | 7.02 | 0.35 | 2.12 |

服务端每轮统计到 309 次业务 SQL 执行。客户端 267 次 execute 调用与它不应一一对应，批处理会拆分/合并成不同数量的服务器语句。

每轮顶层业务语句统计的 WAL 为 1,010,055 字节；整个回放窗口为 1,013,499～1,091,995 字节，包含后台及观察影响。client backend 每轮有 8 次 WAL fsync；前两轮 walwriter 另有一次 fsync，时间约 0.22/0.37 ms。`wal_buffers_full` 增量均为零。

SQL 规划与执行合计约 10.65～11.54 ms，而客户端 execute 调用累计约 86～91 ms。差额可能包括客户端驱动、绑定/编码、协议往返、Docker 转发、调度、服务端未被这两个计时覆盖的处理和观测扰动。**本轮没有逐层计时或 CPU 调用栈，不能把差额直接指认为某个模块的可删除成本。** 同理，WAL 的 write/fsync 时间可能已包含在 SQL 或提交时间中，不得再与客户端总时间相加。

服务端较高的单项之一是 StockLevel 的计数查询：trial2 一次执行约 1.56 ms。但这个结果属于必需业务输出，本轮没有据此追加索引或 SQL 改写任务。

## 锁与并发控制的结论边界

三轮分别采样 20、18、19 次，合计仅 25 个处于业务标签下的会话状态观测。其中 16 个为 `ClientRead`，8 个 active 且没有等待事件，1 个为 `WalWrite`。没有采到 Lock/LWLock 等待或阻塞者；对应数据库 deadlock 增量均为零。

这只说明**该短串行回放没有观测到锁阻塞**。未等待的锁操作仍可能消耗 CPU；串行安排也天然弱化业务冲突。不能由此推断多用户争用不存在、并发控制免费，或整个锁管理器无用。采样线程本身每轮累计查询约 9～10 ms，且没有单独测量关闭观测后的基线，因此不将这些数字当作无扰动性能结果。

本轮没有采集内核 CPU 栈，也没有执行故障恢复、持续 checkpoint 或并发压力测试。恢复代码即使不在本次正常执行路径上，仍可能要求运行期间生成 WAL；其能力必要性不能靠本次动态观测取消。

## 正确性与产物

四轮共执行 68 条命令，其中三次测量共 51 条、39 个被接受事务。每轮的完整响应、逐步状态、九表初始/最终完整行哈希、一致性约束，以及去掉纯计时字段后的 JDBC 轨迹，都与原有冻结证据一致。插桩类与业务适配器的实际加载来源已检查。180 个归档文件的哈希已核验，实验容器和命名卷均已清理。

- [完整摘要](../evidence/kernel-cost/20261105-8e4a9554d877/summary.json)、[运行及验证记录](../evidence/kernel-cost/20261105-8e4a9554d877/run.json)
- [固定协议](../evidence/kernel-cost/20261105-8e4a9554d877/protocol.json)、[实际配置](../evidence/kernel-cost/20261105-8e4a9554d877/settings.json)、[实际文件系统](../evidence/kernel-cost/20261105-8e4a9554d877/filesystem.log)
- [服务端 SQL 统计示例](../evidence/kernel-cost/20261105-8e4a9554d877/trial2/statements.json)、[等待采样](../evidence/kernel-cost/20261105-8e4a9554d877/trial2/wait-samples.json)
- [实验脚本](../scripts/profile_database_kernel.py)、[采样探针](../prototype/module_pruning/KernelCostProbe.java)

本轮到此停止，没有内核删除候选准入。后续若继续，应先区分通用 SQL 请求处理链和真正的内核 CPU 工作，并在目标并发条件下讨论锁的成本；不直接关闭恢复或把当前串行调度提升为产品能力限制。配置关闭只能在明确改变能力契约的对照中讨论，不能当作当前任务已授权的优化。

复现命令：

```sh
python3 -B scripts/profile_database_kernel.py
```
