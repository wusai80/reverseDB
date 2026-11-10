# 已验证融合版的性能评测

2026-11-06。原版与 NewOrder＋Payment 融合版在固定混合负载下完成六对正式测量。**每轮业务执行及真实提交／回滚的累计耗时中位数从 86.28 ms 降至 46.97 ms，降低 45.6%，中位数之比为 1.84 倍。** 六对测量均观察到融合版耗时下降。完整响应、逐步状态及九表完整数据校验全部通过。

这是既有融合实现的性能验证，没有新增模块删除、索引、缓存或数据裁剪，也没有关闭事务、锁、WAL 或恢复机制。默认应用源码未改，计时插桩只在实验目录编译使用。

## 固定协议

- 原版为冻结反编译 JAR＋原适配器；融合版直接复用已通过端到端验证的 `guarded-value-e2e/20261106-2b184bc5dc73/generated/full` 三个 Java 文件与对应安装 SQL，逐文件核对已有 manifest。
- 使用冻结 `holdout.json`，每轮 17 条请求：10 次提交、3 次业务回滚、4 次输入拒绝。13 个进入业务执行的事务为 NewOrder 9 个（6 提交、3 回滚），Payment、OrderStatus、StockLevel、Delivery 各 1 个；Payment 为 GC 路径。该负载偏重新订单，不是标准 TPC-C 事务比例。
- 四个终端连接按固定 JSON 顺序串行执行，隔离级别为 SERIALIZABLE。每轮恢复相同的完整 599,051 行、九张业务表；两臂安装相同 SQL 函数、执行 ANALYZE、核对完整初始状态，并在业务回放前 CHECKPOINT。
- 一个持续运行的 JVM 内保留两组适配器类加载器，共享同一插桩运行器。每臂先预热两次，再测六次；八对运行顺序严格交替 AB、BA。每次回放创建新数据库连接。
- PostgreSQL 18.6，固定本地镜像、Docker 命名磁盘卷，容器限制 2 CPU、1.5 GB，shared_buffers=256 MB；JVM `-Xmx512m -XX:ActiveProcessorCount=2`。数据库与 Java 设置 UTC。`fsync`、`synchronous_commit`、`full_page_writes`、autovacuum 保持开启，wal_level=replica。
- 全程 272 条请求，正式测量 204 条，其中 156 个进入业务执行。按预定六对停止，没有追加样本追求更好的结果。

## 计时口径

实际调用两版 `TpccApp.main`，沿用应用输入检查、观察、结果构造及提交／回滚流程。核心指标是每次 `executor.execute` 开始至业务结果构造及 `commit` 或 `rollback` 完成的耗时，包含少量记录开销。

计时不包含 JVM 启动、连接建立、输入校验和拒绝、数据库恢复、ANALYZE、完整哈希、前后状态观察、最终文件输出。因此这里的“整体”指 **13 个被接受事务的业务区间累计时间**，不是 HTTP 延迟、整个应用进程耗时或包含所有观测工作的回放墙钟时间。

实验运行器保留原 JDBC 时钟替换条件，关闭逐参数值、逐行 getter 的详细事件记录，只记录 execute 次数／耗时及 commit／rollback 耗时。两版使用相同的轻量插桩，但仍有代理和计数开销；本轮没有独立量化完全关闭插桩后的速度。插桩源码与精确 diff 均已归档。

## 测量结果

下表时间均为六轮“该类事务在一轮中的累计耗时”的中位数，包含提交或回滚。各类中位数不能相加当作整体中位数。

| 范围 | 每轮事务数 | 原版 ms | 融合版 ms | 耗时变化 | 每轮 JDBC execute 次数 |
|---|---:|---:|---:|---:|---:|
| 整体混合负载 | 13 | 86.283 | 46.966 | 降低 45.6% | 267 → 89 |
| NewOrder，含 3 次回滚 | 9 | 57.810 | 18.641 | 降低 67.8% | 182 → 9 |
| Payment，GC | 1 | 2.701 | 1.978 | 降低 26.8% | 8 → 3 |
| OrderStatus | 1 | 1.727 | 1.817 | 增加 5.2% | 5 → 5 |
| Delivery | 1 | 21.448 | 21.698 | 增加 1.2% | 70 → 70 |
| StockLevel | 1 | 2.059 | 1.956 | 降低 5.0% | 2 → 2 |

后三类实现没有优化，其小幅升降作为运行波动呈现，不认定为新的优化效果。融合版每轮 execute 次数减少 178 次（66.7%），其中 NewOrder 减少 173 次，Payment 减少 5 次。JDBC 调用次数不等于服务器内部 SQL 数量：融合函数内部仍执行必要查询与写入。

| 正式轮次 | 原版累计 ms | 融合版累计 ms | 配对耗时比 |
|---|---:|---:|---:|
| 1 | 90.181 | 46.394 | 1.94 |
| 2 | 84.801 | 50.260 | 1.69 |
| 3 | 87.765 | 47.607 | 1.84 |
| 4 | 93.117 | 47.537 | 1.96 |
| 5 | 82.773 | 43.949 | 1.88 |
| 6 | 81.942 | 43.353 | 1.89 |

原版范围 81.94–93.12 ms，融合版 43.35–50.26 ms。六个配对耗时比的中位数为 1.89；上文 1.84 是两个累计时间中位数之比，二者计算口径不同。

提交＋回滚时间每轮累计中位数为 **5.04 → 5.22 ms**；execute 调用累计中位数为 **74.50 → 37.68 ms**。收益集中在业务执行链缩短，与大量 JDBC 往返被跨层融合替代的结构变化一致。不能据此把全部差额归因于网络，或证明数据库事务／恢复模块无用。融合后 Delivery 占据较大的剩余时间，但本轮没有自动把它转为新的优化任务。

## 正确性与结论边界

16 次回放分别与冻结的原版证据比较，均满足：

1. 完整有序响应相等，每条请求前后状态观察相等。
2. 九表初始及最终完整行哈希、行数相等，没有只校验被访问的数据。
3. 五项数据库一致性约束均为零违例，提交／回滚次数及顺序符合实际响应。
4. 两个融合助手、原版／融合版适配器及实验运行器的加载来源已验证。

两次预热不保证彻底排除 JIT 或缓存影响；完整哈希会读取全表页面，CHECKPOINT 后短回放及新连接也不代表持续服务的稳态。Docker Desktop、宿主缓存和调度会影响绝对延迟。该实验只有六对正式测量，没有统计显著性或并发吞吐结论，不报告 TPC-C tpmC。

历史 NewOrder／Payment 探针侧重事务体、排除提交，负载也不同；不能直接用历史降幅与本轮结果推断回归。本轮支持的结论是：**在完整数据、原有安全机制及固定能力检查下，既有跨层融合已经取得可观的串行业务延迟改善，无需继续冒险删除边界模块。** 有限测试仍不构成全部能力等价证明。

## 证据与复现

- [固定协议](../evidence/fusion-performance/20261106-f4bb66d2aefc/protocol.json)、[汇总指标](../evidence/fusion-performance/20261106-f4bb66d2aefc/summary.json)、[每轮计时与校验](../evidence/fusion-performance/20261106-f4bb66d2aefc/run.json)
- [运行器插桩差异](../evidence/fusion-performance/20261106-f4bb66d2aefc/generated/AppRuntime.diff)、[JDBC 插桩差异](../evidence/fusion-performance/20261106-f4bb66d2aefc/generated/JdbcTrace.diff)
- [数据库配置](../evidence/fusion-performance/20261106-f4bb66d2aefc/settings.json)、[磁盘文件系统](../evidence/fusion-performance/20261106-f4bb66d2aefc/filesystem.log)、[573 个归档文件的 manifest](../evidence/fusion-performance/20261106-f4bb66d2aefc/manifest.json)
- [实验脚本](../scripts/benchmark_fusion.py)、[同 JVM 应用入口探针](../prototype/module_pruning/FusionPerformanceProbe.java)

归档文件哈希全部核验，实验容器与卷均已清理，并额外只读确认不存在。复现使用本地冻结资产：

```sh
python3 -B scripts/benchmark_fusion.py
```
