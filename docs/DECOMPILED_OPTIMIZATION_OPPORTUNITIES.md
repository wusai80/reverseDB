# 反编译产物中的数据库交互优化机会

日期：2026-11-04。结论：**优先验证 StockLevel 的两次只读查询合并，其次是 OrderStatus 的跨类重复订单读取。** 本轮完成机会识别和既有调用记录复核；没有修改事务实现，没有新的数据库运行或提速结论。

**后续范围调整：** 用户要求不推进工程类优化，先形式化应用—数据库结合的问题。因此本文的逐事务排序保留为实例线索，不再直接作为实施计划；当前以[两个研究问题及验证协议](APP_DB_RESEARCH_PROBLEMS.md)为准。

## 证据与口径

分析对象是[已重建并实际运行的反编译 Java](../evidence/tpcc-roundtrip/20261103-9954559e225c/bundle/sources/candidate/reversedb/tpcc/)，不是把原源码直接称为反编译结果。真实反编译由 Vineflower 完成；REA 提供制品取证，不能据此声称 REA 自动完成了恢复或优化。重建来源、类加载及与冻结原版的对照见[重建报告](TPCC_DECOMPILATION_ROUNDTRIP.md)。

重新统计该归档中 baseline、holdout、transaction_edges、delivery_edges 四组候选端记录，并对所读 21 个输入文件核验归档 SHA-256。62 条命令中有 50 条提交、4 条中止、8 条输入拒绝；54 条进入业务执行并有 JDBC trace。逐命令计数及分组见 [counts.json](../evidence/decompile-opportunities/20261104/counts.json)，从仓库根目录可重现：

```bash
python3 evidence/decompile-opportunities/20261104/inspect.py
```

下文的“调用”只指业务 `executeQuery`、`executeUpdate`、`executeBatch`。包含应用为返回响应而执行的读取，不含观察探针、状态快照和提交/回滚。一次 batch 不是一条 SQL，也不能直接当成一次网络往返。没有用开启监控的耗时估计收益。

| 优先级 | 区域 | 已观察的提交路径 | 拟验证的变化 | 主要约束 |
|---|---|---|---|---|
| 1 | StockLevel | 6 次事务，每次 2 次查询 | 2 → 1 次查询 | 缺失与空集、去重、窗口、整数与快照 |
| 2 | OrderStatus | 7 次事务，每次 4 次查询 | 4 → 3 次查询 | 跨类数据流、完整响应、缺失实体语义 |
| 3 | Payment | 18 次事务，每次 8 或 9 次调用 | 两组 UPDATE/SELECT 各合并，拟为 6 或 7 次 | 行数检查、触发器、返回值与写效果 |
| 4 | NewOrder | 10 次事务，订单行数 n 为 5～15；2n+5 次读、3 次更新、2 次 batch | 仅价格读取 n → 1，拟少 n−1 次读 | 行序、重复商品、错误顺序、浮点计算 |
| 5 | Delivery | 9 次事务；10、16 或 70 次调用 | 先识别局部 RETURNING 机会，整批融合暂缓 | 队列、写顺序、锁与金额转换 |

所有箭头都是候选设计的调用数推导，尚未实现或验证。排序按语义边界和验证成本，不按未经测量的预期加速比。

## 1. StockLevel：消除标量返回应用后再次传入数据库

[StockLevelService.java](../evidence/tpcc-roundtrip/20261103-9954559e225c/bundle/sources/candidate/reversedb/tpcc/StockLevelService.java) 第 12～29 行先读 `d_next_o_id`，通过 JDBC 转成 Java int，再把 `next−20`、`next` 作为后续聚合查询参数。该标量只服务于后续关系过滤，适合保持在数据库内计算。

候选形状是以 district 为驱动关系，关联其订单窗口与库存，再计算 `count(DISTINCT s_i_id)`。使用保留 district 行的关联或相关聚合设计，可以同时表达“地区存在但结果为空”和“地区不存在”。不能直接将第一条查询塞入一个无分组的 count 子查询后就认为等价。

需要逐项建立的条件：

- 地区缺失时保留错误；地区存在、窗口内没有匹配库存时返回 0。接口前置验证能否排除缺失，需要证据，不能从已有输入推断。
- 窗口保持 `[next−20, next)`，库存判断保持严格 `< threshold`；多条订单行引用同一商品仍只计一次。
- 核对 district 唯一键、列的 NULL 条件、库存连接键，以及 JDBC `getInt` 与 SQL 类型转换。Java int 减法和数据库整数减法的溢出行为不同，必须限定并核验合法取值域，或显式保留原语义。
- 保持原只读事务和快照边界。现有基线为 SERIALIZABLE、固定串行调度；这不是并发透明性证明。
- 合并查询会改变语句级故障位置。旧 B1 的“正常执行基线”豁免不自动移植；新实验需单独写明异常契约，不能报告任意故障下完全等价。

这里的通用识别单位是“SQL 标量生产者 → Java 参数计算 → SQL 消费者”，不依赖具体地区、商品或测试数据值。

## 2. OrderStatus：跨方法的重复实体读取

[OrderStatusService.java](../evidence/tpcc-roundtrip/20261103-9954559e225c/bundle/sources/candidate/reversedb/tpcc/OrderStatusService.java) 先检查客户，再取最新订单 ID；[CandidateApp.java](../evidence/tpcc-roundtrip/20261103-9954559e225c/bundle/sources/candidate/reversedb/tpcc/CandidateApp.java) 第 11～14、44～45 行根据 ID 再读完整订单与明细。因此只分析 service 会漏掉后两次查询。

最小候选是在选最新订单时取得完整订单头，由调用方复用，保留客户检查与有序明细读取。预期从 4 次读降到 3 次，不必立即设计全量 JSON 聚合。必须保持全部返回字段、NULL、类型与时间表示、`ol_number` 排序，以及无客户/无订单的区别。共享 `order(...)` 还被 NewOrder 使用，应避免同时改变另一条事务路径。

这个例子支持以跨方法数据依赖划定优化区域：类边界过细会漏掉重复读取，整笔事务作为单位又会带入无关写效果。

## 3. Payment：写入后读取可考虑 RETURNING

[PaymentService.java](../evidence/tpcc-roundtrip/20261103-9954559e225c/bundle/sources/candidate/reversedb/tpcc/PaymentService.java) 先更新仓库再读名称，再更新地区并读名称。这两组可分别研究 `UPDATE ... RETURNING name`。GC 路径为 4 次读、4 次写；BC 路径多一次 c_data 读取。单独合并上述两组，拟少 2 次调用。

需要保留 `Sql.one` 的影响行数约束，并检查触发器、规则及更新后可见值。BC 的 c_data 也可随客户初次读取，但应作为另一个独立候选，不把额外载荷成本隐藏起来。

本轮不把金额算术与字符串拼接下推。现代码使用 float 转换、Java 字符串格式和 substring；直接改为数据库 decimal 运算或字符串截断，可能改变金额、历史文本或 Unicode 边界。

## 4. NewOrder：先分离只读价格循环与库存写效果

[NewOrderService.java](../evidence/tpcc-roundtrip/20261103-9954559e225c/bundle/sources/candidate/reversedb/tpcc/NewOrderService.java) 的每条订单行各读一次商品价格、一次带锁库存；服务之外还有两个响应查询。提交路径的 `2n+5` 次读取公式已逐条复核。

较小候选是批量读取商品价格，再按原订单行次序消费；暂不合并库存锁和更新。即便如此，读取提前仍可能改变缺失商品的错误顺序与之前已获取的锁，需要明确准入条件。不能把输入去重后连订单行也去掉。

尤其要注意现有库存更新是在循环结束后执行 batch：同商品多次出现时，多个读取可能见到相同的旧库存，最终数量与累计计数的语义不同。不能未经分析就替换为逐行即时更新或按商品合计数量。价格乘数量还经过 Java float 运算，SQL decimal 乘法并不天然等价。

循环外复用 PreparedStatement 是另一种工程优化，会减少 prepare 动作，但不减少这里统计的查询次数，也不足以单独构成研究贡献。

## 5. Delivery：调用密集，但当前不适合整体融合

[DeliveryService.java](../evidence/tpcc-roundtrip/20261103-9954559e225c/bundle/sources/candidate/reversedb/tpcc/DeliveryService.java) 固定访问 10 个地区；每个有待配送订单的地区增加 2 次读、4 次写。令 k 为非空地区数，总调用数为 `10+6k`：本次记录中 k 为 0、1、10，对应 10、16、70 次。

可记录订单更新返回客户 ID 等局部机会；整批集合化涉及各地区最旧订单选择、地区顺序、缺行错误、更新行数、锁及共同回滚。金额还通过 `SUM → getFloat → BigDecimal.valueOf` 才加回客户余额，直接把 SUM 放入 UPDATE 可能改变数值结果。70 次调用说明值得关注，但不足以支持安全合并或性能收益结论。

## 一个有终点的后续实验

建议只选择 StockLevel，作为既有反编译产物上的独立小实验；不扩展 Paperless 绑定器或写事务优化。

1. 固定原重建版本、调用点、数据库约束和正常执行/异常边界，写出标量数据流及类型、存在性、快照条件。
2. 在实验副本中实现一个手工关系改写，保存原产物。比较完整响应和只读状态，覆盖空窗口、重复商品、阈值相等、窗口两端及缺失地区；类型边界无法证明时拒绝准入，不用抽样通过代替。
3. 用去掉 DISTINCT、错置窗口端点、把缺失地区变成 0 的错误版本检验这些检查；固定命令顺序/种子。语义未过就结束并报告阻碍，不顺延扩大范围。
4. 语义通过后，用一次预先固定规模与轮数的无 trace 对照测量，同时报告执行计划、查询数与完整调用耗时。无论是否提速，归档结论后结束。

潜在研究贡献在于：从反编译的数据依赖中选出可变换区域，显式表达关系、类型和效果条件，并在条件未知时保持原路径。单个 SQL 合并本身属于常规优化；本轮没有实现自动识别，也没有完成新颖性比较。有限行为记录用于定位和反例检验，不能证明未观察数据或能力无用。
