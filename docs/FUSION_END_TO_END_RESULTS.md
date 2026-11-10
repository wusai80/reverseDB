# 两类融合事务的端到端验证

2026-11-05。最终固定 140 条应用请求验证通过：原版和同时启用 NewOrder、Payment 融合的应用各执行 70 条。完整响应、逐请求状态观察及真实提交后的九表完整数据一致。本轮没有计时或吞吐结论。

## 实际执行链

```text
固定 JSON 请求文件
  → TpccApp.main
  → 原 AppRuntime 输入校验与四个终端连接
  → 原版事务 / 同一应用内的两个融合实现
  → 真实 JDBC → PostgreSQL
  → 实际 commit / rollback
  → 应用响应文件
  → 应用进程退出后，独立 psql 连接核对完整数据库
```

这里的应用入口是当前小应用的命令行 JSON 接口，不是 HTTP 服务。本次没有直接调用内部事务 helper，也没有用统一回滚来代替成功事务提交。四个终端按固定文件顺序串行执行，不声称并发等价。

## 固定结果

| 场景 | 每臂请求数 | 提交 | 业务中止 | 输入拒绝 |
| --- | ---: | ---: | ---: | ---: |
| baseline | 11 | 10 | 1 | 0 |
| holdout | 17 | 10 | 3 | 4 |
| transaction_edges | 27 | 23 | 0 | 4 |
| delivery_edges | 7 | 7 | 0 | 0 |
| time_boundaries | 8 | 6 | 1 | 1 |
| 合计 | 70 | 56 | 5 | 9 |

原版和融合版的上述状态计数及每条请求的顺序、完整返回内容均相同。时间场景包含普通毫秒值、极大合法时钟、GC/BC 付款、付款地区与客户地区不同、重复商品、提交后的 OrderStatus 读取、无效商品回滚和负时钟拒绝。极大时钟实际提交后，两臂均有 1 条 infinity 订单和 2 条 infinity 付款历史。

每个场景、每个版本从同一冻结完整初始数据库恢复，应用执行后独立读取 warehouse、district、customer、item、stock、oorder、new_order、order_line、history 九张表。对每张表比较行数和全字段、全行的规范化哈希，保留重复行数量；没有按访问过的 ID 或抽样行缩减比较范围。基础库共 599,051 行。Delivery 和信用分支夹具按记录对两臂一致应用，不属于优化的数据删减。

每条有效请求的 trace 都有相应 commit 或 rollback；输入拒绝保持原入口行为。五项既有业务一致性检查全部通过。原版四组旧场景还逐项匹配此前冻结的响应、观察及初末九表状态，避免只比较两个共同偏离基线的版本。

类加载证据和 JDBC 次数检查确认两个融合类同时启用：成功 NewOrder 执行 1 次外层 JDBC，Payment GC/BC 分别执行 3/4 次。仅仅“最终结果相同但退回原实现”不算融合验收通过。

## 本次发现并修复的集成问题

1. **追踪器拒绝新的时间绑定。** 时间修复改回 setTimestamp 后，旧 JdbcTrace 只允许原始三类 SQL 时间写入。现在仅对两个精确匹配的融合函数签名及正确参数位置透传，不替换候选传入的时间。原版 SQL 仍按既有规则冻结时钟，其他未知位置仍拒绝。9 项代理检查覆盖原版替换、候选对象原样保留、错误槽位、未知 SQL 和非冻结模式；故意传入不同候选时间，确认追踪器不会替候选纠错。
2. **两个生成器组合时覆盖了 Payment 入口。** 原 NewOrder 适配器替换范围延伸到旧 procedure switch，把已插入的 Payment 分支一起覆盖。现在只替换 NewOrder 的原始代码块，保留其他融合入口。单独生成 NewOrder 的适配器仍与历史冻结产物一致；两个分支同时存在且在运行时实际加载。

首次运行 [20261105-3bfc35435b4d](../evidence/fusion-e2e/20261105-3bfc35435b4d/run.json)在 baseline 融合臂的调用次数检查中发现第二个问题，已保留失败记录。修复后仅重跑原定 70 请求／臂的范围，没有追加搜索或性能矩阵。

## 证据与边界

- [固定协议](../evidence/fusion-e2e/20261105-3516c48063a9/protocol.json)、[最终报告](../evidence/fusion-e2e/20261105-3516c48063a9/run.json)、[完整哈希清单](../evidence/fusion-e2e/20261105-3516c48063a9/manifest.json)。
- [合并应用源码](../evidence/fusion-e2e/20261105-3516c48063a9/generated/TpccApp.java)。
- [极大时钟原版响应](../evidence/fusion-e2e/20261105-3516c48063a9/time_boundaries_original/responses.json)与[融合版响应](../evidence/fusion-e2e/20261105-3516c48063a9/time_boundaries_combined/responses.json)。
- [极大时钟原版最终九表状态](../evidence/fusion-e2e/20261105-3516c48063a9/time_boundaries_original/final-state.json)与[融合版最终状态](../evidence/fusion-e2e/20261105-3516c48063a9/time_boundaries_combined/final-state.json)。
- [静态放置回归](../evidence/fusion-placement/20261105-f1306635a377/run.json)：34 项通过。
- [端到端驱动](../scripts/test_fusion_e2e.py)：`python3 -B scripts/test_fusion_e2e.py`；[追踪器边界检查](../app/tpcc/test/TraceClockBoundaryTest.java)。

PostgreSQL 使用已锁定的本机镜像，保留 UTC、SERIALIZABLE、fsync、同步提交和完整 WAL 写入。两轮临时容器与卷均已清理。原始冻结代码和历史证据未覆盖。

本结果支持当前固定串行场景的端到端行为及最终完整数据一致。它不能把有限请求升级为全输入或并发能力的形式化证明；静态语义义务继续作为补充。本轮以完整入口和最终结果为验收终点，不继续追逐 SQL 文本一致或新增性能实验。
