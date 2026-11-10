# 独立上下文 agent 语义审计

2026-11-05。两阶段只读审计完成。发现一处时间传值编码缺陷；常规业务反例已由现有候选处理；能力目录仍依赖人工信任声明。本轮没有修改生成器、冻结产物或数据库，没有新增性能结果。

后续状态：时间编码缺陷已修复并通过真实数据库对照，见[修复与验证报告](TIMESTAMP_ABI_FIX_20261105.md)。以下保留审计当时的发现与证据边界。

## 审计协议与独立性

第一阶段使用不继承主对话的新上下文 agent，只提供冻结 NewOrder、Payment、TpccApp、AppRuntime、JdbcTrace、Observation 源码，以及同包 reference.jar。禁止读取既有优化方案、生成器和结论。它先提交八项义务，然后才收到候选 SQL / Java、契约目录、放置器及已有验证文档。

第二阶段按已经约定的正常串行执行范围对照候选，保留合法业务中止、事务原子性与完整数据；没有要求任意故障时刻、外部并发或任意触发器等价。审计 agent 提出时间 ABI 疑点后，主 agent 提供了检查 pgJDBC infinity 哨兵的线索；随后双方通过纯 Java 探针确认。因此第一阶段是上下文隔离盲审，第二阶段包含协作复核，不是完全独立的双模型验证，也不是形式化证明。

第三方审计技能只做了[资料调研](OPEN_SOURCE_AUDIT_TOOLS.md)，没有安装、运行或被当作本次审计结果的来源。

## 第一阶段冻结的八项义务

| 义务 | 原程序要求与区分性情景 |
| --- | --- |
| 合法入口决定能力范围 | 未采样的地区、客户、商品仍须处理；重复 item 合法；当前入口的 warehouse=1、按 ID 付款等限制来自代码，不来自样本 |
| NewOrder 身份与完整写集 | 订单号来自持久化 district；保留输入行位置、完整订单/订单行与默认字段，不能重排后重新编号 |
| 重复 item 的阶段次序 | 全部库存读取早于批量库存写入；quantity 是按原读值计算的逐次绝对赋值，YTD 和次数逐次累加 |
| 数值转换链 | NewOrder 的 getFloat→float 乘法→setDouble；Payment 客户 float 算术及仓库/地区 BigDecimal 绑定均须保留 |
| Payment 的地区与副作用 | 付款地区和客户地区可不同；客户更新、仓库/地区更新与追加 history 不能混同或去重 |
| BC 文本语义 | 精确 BC 判断、Java float 文本、UTF-16 截断与 GC 保留 C_DATA；合法 NULL/字符域需结合 schema 确认 |
| 中止与错误分类 | 合法但不存在的 item 导致 INVALID_ITEM 和完整回滚；一般 SQL 异常不能全部吞成业务中止 |
| 事务、时钟与输出 | 同一外层事务；命令时钟经 JDBC Timestamp 绑定；保留完整结果；实现 trace 不要求逐条查询不变 |

来源根：[`tpcc-roundtrip` 冻结源码](../evidence/tpcc-roundtrip/20261103-9954559e225c/bundle/sources/reference/com/oltpbenchmark/benchmarks/tpcc/procedures/)，以及 [`tpcc-observation` 冻结入口与运行时](../evidence/tpcc-observation/20261103-8729d2283fdc/run/inputs/app/tpcc/src/)。Customer 字段和 JDBC getter 类型还通过同包 reference.jar 的 javap 核对。

例如五行合法订单中，前两行商品相同、数量分别为 3、9，原库存 quantity=20：原实现最终 quantity=11、YTD+=12、order_cnt+=2；错误的逐行读写实现会产生 quantity=99。该反例约束代码阶段，不要求删除或限制重复商品数据。

## 第二阶段结论

### 1. 时间编码缺陷：合法输入不一定等价

冻结 AppRuntime.java 第 41–43 行只要求 clock_ms 是非负 Integer/Long。取 `9223372036825200000`，其他字段保持为一条普通有效 NewOrder 或 Payment，即满足该时间输入检查。

原版 JdbcTrace.java 第 44 行保留 setTimestamp 调用，交给 pgJDBC 编码。候选却使用 Timestamp.toString 后 setString：

- [NewOrder 生成器](../scripts/generate_neworder_fusion.py)第 164 行，对应 [FusedNewOrder.java](../evidence/neworder-fusion/20261105-4c76a7feb013/generated/FusedNewOrder.java)第 16 行。
- [Payment 生成器](../scripts/generate_payment_segments.py)第 161 行，对应 [SegmentedPayment.java](../evidence/payment-segments/20261105-707221611457/generated/SegmentedPayment.java)第 49 行。

使用相同运行依赖 postgresql-42.7.4.jar，固定 UTC 的纯 Java 探针输出：

```text
clock_ms=9223372036825200000
Timestamp.toString=292278994-08-16 23:00:00.0
JDBC TimestampUtils.toString=infinity
```

agent 通过 javap 确认 PgPreparedStatement.setTimestamp 使用驱动的 TimestampUtils 编码；主 agent 独立运行了归档的[探针源码](../evidence/agent-semantic-audit/20261105/TimestampEncodingProbe.java)，[执行记录](../evidence/agent-semantic-audit/20261105/timestamp-encoding.json)包含命令、输出、JDK 版本及输入哈希，退出码为 0。

**已确认的是编码差异。** 原版 infinity 与候选巨大普通年份的 timestamp 转换预计导致数据库行为不同；本轮没有执行该请求的双臂数据库回放，因此不声称已观察到 SQL 错误或最终状态差异。这不否定已有普通时钟回放，但阻止我们声称覆盖全部合法 clock_ms。

建议下一步保持原 JDBC setTimestamp，或使用同版驱动的等价编码，再做这一反例的有界双臂验证；不要通过缩小输入域来掩盖问题。本轮仅报告，未修复。

### 2. 主要业务义务已经落实

NewOrder full.sql 第 28 行起保留完整读取暂存，再完成 order_line 和 stock 两个批次；浮点乘法、行身份和 SELECT * 输出仍在。Payment SegmentedPayment.java 第 28 行起保留原客户构造、同一对象上的 float 运算、BC 判断、原 getCData 和 Java UTF-16 操作；install.sql 分开使用付款地区与客户地区，追加 history，未自行提交。

已有[NewOrder 区分性负控](NEWORDER_CROSS_LAYER_FUSION.md)和[Payment 真实数据库边界验证](PAYMENT_SEGMENTED_FUSION.md)支持这些具体实现。审计没有发现按样本 ID 裁剪数据，也没有把已处理的大余额、重复 item、金额文本或 UTF-16 反例重复报告为新 bug。

### 3. 契约信任边界仍然存在

[fusion_placement_contracts.py](../scripts/fusion_placement_contracts.py)第 15–17 行直接设置 verified_source_contract，并将 requirements 复制成 guarantees；[fusion_placement.py](../scripts/fusion_placement.py)检查声明是否闭合，不独立检查候选是否兑现声明。时间 ABI 反例说明，声明 fixed_clock 和 timestamp 可传输仍可能遗漏实际编码语义。

这一限制已在[自动放置文档](AUTOMATIC_FUSION_PLACEMENT.md)明确披露，故不是新发现的 DP 错误。算法结论仍应表述为“给定可靠能力契约后的切点选择”。源码哈希和有限回放不能自动升级为全域语义证明。

后续 agent 审核应逐项产出源码位置、候选位置、输入/状态域、可复现证据和未决项，减少人工阅读；未决义务不能仅凭 agent 赞同或概率分数升级为 verified。当前发现的优先处理对象是时间 ABI，而非扩大性能测试或引入更多工具链。
