# 从实现恢复 JDBC 边界义务的小验证

2026-11-05。固定静态实验完成：22 项检查通过；从原版实际源码提取两项时间写入，从修复版实际源码提取对应绑定，在显式前提下匹配；两个历史错误版本得到不同结构。没有新数据库执行、计时、模块删除或数据裁剪。

## 问题与范围

现有能力目录把 requirements 复制成 guarantees，再设置 verified_source_contract。选择器可以检查声明闭合，却无法辨认一个声明正确、实现错误的转换。此前时间编码反例已经展示了这个缺口。

本轮先研究一个可收敛的问题：**能否从实际 Java 代码恢复某个跨层值的来源、转换与 JDBC 绑定，并用可检查的结构关系约束融合？** 不把有限测试中的 clock_ms 值作为允许范围，仍采用原入口的非负 Long 域。

这一方法检查被选定的传值边界，不恢复完整调用图，不证明 SQL 目标列／函数参数映射，也不分析所有别名、堆状态或异常路径。选择两个对应位置和确认冻结运行时语义仍是显式前提；没有把它们改称自动发现。

## 表示与判定

把单次绑定表示为 `B=(E, M, P)`：E 是产生值的表达式树，M 是带完整参数类型的 JDBC setter，P 是原版与候选 SQL 参数位置的对应关系。检查对象是转换后的边界值，而不是某个业务数据样本。

在输入映射、运行时、驱动及目标参数语义相同的前提下，若两边 E 的规范形式与 M 相同，便能确认这个选定绑定产生相同的驱动输入。该充分条件不要求识别所有等价程序；结构不同可能仍等价，应继续论证或提供反例，不能直接宣布语义不等价。

当前实际案例中的原源码为 `setTimestamp(..., new Timestamp(System.currentTimeMillis()))`。冻结 JdbcTrace 会在原 SQL 位置替换为命令时钟，因此有效的值表达式是：

```text
Timestamp(JSONObject.getLong(command, "clock_ms"))
  → PreparedStatement.setTimestamp(int, java.sql.Timestamp)
```

修复版直接产生相同表达式和 setter。旧版则增加 Timestamp.toString，并改用 setString，因此结构不同；已有真实数据库的 infinity／22008 反例进一步证明这两份历史实现确实不等价，而不是仅凭结构差异判断。

输出区分：

- `matched_under_premises`：选定绑定的值结构和 setter 匹配，所有列出的前提仍须成立。
- `different_structure`：支持的结构不同，不自动等于语义反例。
- `unknown`：提取不完整、绑定不唯一、控制／生命周期未闭合或重载不支持，不能据此允许转换。

## 实现

[Java 提取器](../prototype/module_pruning/JdbcBoundaryExtract.java)使用 javac 带类型归属的 AST，记录 setter 的实际方法签名、参数、来源行号、语句对象身份和执行位置。它支持当前需要的参数、字面量、未重赋值局部变量、Timestamp 构造、JSONObject.getLong、原始时钟和 Timestamp.toString。注释中的伪造调用不会成为证据；不支持的表达式标为未知。

[独立检查器](../scripts/jdbc_boundary_contracts.py)核对一次明确的绑定与执行、固定参数位置和表达式树，不读取放置目录中的 verified_source_contract 或 guarantees。它保守拒绝条件绑定、同槽多次绑定、动态参数位置、clearParameters、批处理生命周期及不支持的重载。

每项实际结果附带原版／候选 AST 事实、源码位置、冻结时钟运行时和输入验证文件的哈希、显式参数映射及未消除的前提。哈希仅绑定身份，不证明运行时规则。代理中的反射改写、SQL 参数对应关系及无外部别名变更仍由调用方保证，本轮没有自动证明它们。

这是一项独立静态分析小验证，尚未替换整个生产能力目录，也没有将剩余人工义务自动升级为已证明。对于未知或结构不匹配，只能保留原路径或继续论证；本轮未触发实际执行布局变更。

## 固定验证

| 检查对象 | 结果 |
| --- | --- |
| 原 NewOrder.insertOpenOrder、Payment.insertHistory | 自动恢复两处 setTimestamp 及值结构 |
| 已修复 FusedNewOrder、SegmentedPayment | 两处在冻结运行时和映射前提下匹配 |
| 两份历史融合实现 | 检出 timestamp→text→setString 差异；关联既有 22008 数据库反例 |
| 直接表达式、完全限定类型名、未重赋值的标量别名 | 匹配 |
| 文本编码、错误输入字段、系统时钟、注释伪装 | 得到不同结构 |
| 条件绑定、重复绑定、重赋值、动态槽位、Calendar 重载、清空参数、未知值转换 | 未知，不放行 |
| 原版值结构改变 | 不复用旧冻结时钟规则 |

总计 22 项检查，其中 14 个结构控制例。控制例是供静态分析的 Java 程序片段，未作为数据库业务执行；已有真实数据库结果也未重新计数为本轮运行。

[固定协议](../evidence/jdbc-boundary-contracts/20261105-464aad24d75c/protocol.json)、[最终报告](../evidence/jdbc-boundary-contracts/20261105-464aad24d75c/run.json)、[实际绑定对照](../evidence/jdbc-boundary-contracts/20261105-464aad24d75c/binding-comparisons.json)、[控制例判定](../evidence/jdbc-boundary-contracts/20261105-464aad24d75c/control-verdicts.json)均已归档。

复现：`python3 -B scripts/test_jdbc_boundary_contracts.py`，使用现有 JDK 与固定依赖，不需要 Docker。

## 研究意义与下一难点

当前推进是从“信任转换标签”走向“从实际实现恢复局部义务并检查”，为跨层融合提供可追踪依据。AST 提取和表达式结构比较本身是常规方法，不能单独声称算法创新。

真正尚未解决的连接问题是：如何从调用和 SQL 使用关系建立 P，而不是手工指定哪个原绑定对应哪个融合参数。该关系同时涉及输入角色、函数实参、SQL 参数与最终写入列，不能因为两个参数类型相同就判为等价。下一次应只选择一个实际写入链验证这种对应恢复；本轮按固定范围结束，不追加工具或性能矩阵。
