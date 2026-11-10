# 时间传值修复与数据库验证

2026-11-05。独立审计发现的时间编码缺陷已修复，并完成固定 PostgreSQL 对照验证。没有缩小合法输入范围，没有新增性能结论。

## 修复

[NewOrder 生成器](../scripts/generate_neworder_fusion.py)和[Payment 生成器](../scripts/generate_payment_segments.py)将时间参数由 `Timestamp.toString()` 加 `setString` 改回 `setTimestamp`。因此仍由原版本 pgJDBC 负责时间编码，包括正无穷哨兵；无需手写特殊日期规则。

两处改动均在 Java 外层桥接。SQL、阶段次序、融合切点、数据和外层事务保持不变。历史冻结产物未覆盖；修复版重新生成、编译并归档。放置器回归中的历史 Java 字节比较已明确允许这项修复，SQL 与布局仍按旧基线严格比较。

## 固定验证结果

[最终运行报告](../evidence/timestamp-abi/20261105-f803874e717c/run.json)与[逐次结果](../evidence/timestamp-abi/20261105-f803874e717c/probe-results.json)：

| 检查 | 结果 |
| --- | --- |
| NewOrder、Payment GC、Payment BC，各用普通时钟和极大时钟，对照原版与修复版 | 6 对、12 次执行成功；完整返回值及写入后的九表完整状态一致 |
| 修复前版本，三个普通时钟用例 | 3 次成功，与原版一致 |
| 修复前版本，三个极大时钟用例 | 3 次均实际触发 SQLSTATE `22008`：timestamp out of range |
| 极大时钟、第五行商品不存在，原版与修复版 | 2 次均符合 INVALID_ITEM 中止映射，完整回滚 |
| 全部 20 次执行后的回滚 | 九张业务表恢复初始状态 |
| 独立静态放置回归 | 34 项通过，切点及 SQL 不变 |

普通时钟是 `1762300800123`，数据库实际存储 `2025-11-05 00:00:00.123`；极大时钟是合法非负 Long `9223372036825200000`，原版与修复版均存储 `infinity`。15 次成功执行均精确核对本次事务的时间记录。

原版／修复版 JDBC execute 数仍为 NewOrder 21→1（本次五行订单）、Payment GC 8→3、Payment BC 9→4。这里只检查调用数，没有重复计时或宣称新增加速。

完整冻结初始数据库在隔离 PostgreSQL 容器中恢复。固定 UTC、串行 SERIALIZABLE，保留 fsync、同步提交和完整 WAL 写入。九表状态检查覆盖全部行和全部字段，含重复行的数量；每次执行后回滚，没有裁剪数据。

## 取证修正与边界

首次运行 [20261105-c2aff9cc21b3](../evidence/timestamp-abi/20261105-c2aff9cc21b3/run.json)已通过业务返回值、完整状态差分、负控和回滚检查。但检查报告时发现 Payment 普通时钟的辅助查询使用“最新 history”，取到了初始数据中的较晚记录。修正为按本次客户与时钟精确查询，并要求恰好一条正确时间记录后，才形成上述最终归档。业务修复代码未再次修改；两次运行各固定 20 次执行，旧记录保留。

本验证直接调用冻结适配器的事务 helper。原版时间按冻结运行时规则替换，候选时间绑定原样透传，避免测试代理代为修复候选。原 helper 的 UserAbortException 与候选 BusinessAbort 按冻结 TpccApp.main 的既有映射核对；没有把 helper 层异常类名相同作为要求。

本轮验证固定普通时钟与已知反例、两种 Payment 信用分支及合法业务中止，不构成所有输入、时区、并发或故障时序的穷举证明。解决的是已确认的编码偏离；能力目录仍是来源绑定的信任边界。

## 产物与复现

- [数据库回归驱动](../scripts/test_timestamp_abi.py)：`python3 -B scripts/test_timestamp_abi.py`，需要本机 Docker；只使用已有固定镜像，结束后删除临时容器与卷。
- [Java 探针](../prototype/module_pruning/TimestampAbiProbe.java)：原版、修复版、历史版共享完整初始数据，逐次检查状态与回滚。
- [修复版 NewOrder](../evidence/timestamp-abi/20261105-f803874e717c/generated/new-fixed/FusedNewOrder.java)、[修复版 Payment](../evidence/timestamp-abi/20261105-f803874e717c/generated/payment-fixed/SegmentedPayment.java)。
- [静态回归报告](../evidence/fusion-placement/20261105-434b5e470cd7/run.json)：`python3 -B scripts/test_fusion_placement.py`，不需要 Docker。
- [最终哈希清单](../evidence/timestamp-abi/20261105-f803874e717c/manifest.json)：归档输入、编译产物、执行日志与结果；已逐项复核，资源清理无错误。

该缺陷关闭；本轮停止扩展测试和工具链。
