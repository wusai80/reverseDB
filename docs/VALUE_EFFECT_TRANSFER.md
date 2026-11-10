# 共同值／效果表示的冻结迁移检查

2026-11-06。实现了一版不依赖业务模块名称的声明效果表示，并完成开发后冻结、再检查预留模块的流程。Delivery此前断开的UPDATE时间写入关系已恢复；冻结后OrderStatus的四处SQL均可表示，StockLevel仅一处可表示，多表查询保持未知。

这是**表示的局部迁移证据**，不是候选风险模型的迁移成功。没有训练模型、预测行为标签或证明全能力等价。

## 这次实现什么

从已有带类型归属的Java AST事实开始，连接：

`Java值来源 → JDBC绑定位置 → SQL表达式中的参数 → 写入值／投影／行选择／排序／数量限制`

统一表示保留三层事实：

1. **值与变换。** Java沿用原提取器的参数、时间构造等术语；SQL表达式保留列引用、参数、字面量、运算符和调用树。例如`amount=amount+?`保留旧列值和加法节点，不当成简单的参数直写。调用树只表示语法，SQL调用的值、异常和隐含效果仍未证明。
2. **明确效果。** INSERT和UPDATE共用`目标列 ← 值表达式`记录，同时保留操作类型，绝不认为二者本身等价。SELECT保留每个投影的位置，以及排序、LIMIT、锁定语法。没有JDBC参数的投影和常量写入也不会被省略。
3. **行控制。** WHERE中的参数单独进入行选择关系，再以控制边连接受影响的写入或投影；不会被错标成写入该列的业务值。排序和数量限制也连接查询投影。

例如，INSERT中的时间值与Delivery的UPDATE时间值现在都能进入写入列关系，无需传入业务表名、目标列名或槽位映射。所有setter类型一视同仁，不再只从`setTimestamp`建立切片。声明身份仍区分同一方法中不同PreparedStatement的同号参数。

如果Java字段来源不明，但SQL目的地明确，就同时保存“目的地已定位”和“值未知”。如果整个SQL形状不受支持，则整个语句保持未知，不只提取前半段后声称覆盖全部效果。语法识别不是手写风险分类规则，产物中没有安全分数或自动删除决定。

## 先开发、再冻结

开发范围为NewOrder、Payment，以及上轮已经暴露断点的Delivery。27项开发控制检查通过，涵盖：统一写入形式、WHERE与载荷区分、表达式优先级、字符串／注释中的问号、改名、投影与排序限制、图连通性，以及动态槽位、重复绑定和未支持SQL必须保留未知。

随后将实现、协议、开发结果及检查哈希写入`before-target-freeze.json`，再首次运行本轮OrderStatus和StockLevel的AST提取及共同表示恢复。冻结之后没有修改实现，也没有为StockLevel补多表解析再重测。

## 覆盖结果

| 模块 | 角色 | SQL声明可表示 | 参数槽位能定位到SQL用途 | 已定位用途中Java值可恢复 |
| --- | --- | ---: | ---: | ---: |
| NewOrder | 开发 | 10/10 | 35/35 | 21/35 |
| Payment | 开发 | 10/10 | 38/38 | 24/38 |
| Delivery | 开发 | 6/7 | 20/23 | 19/20 |
| OrderStatus | 预留评估 | 4/4 | 12/12 | 11/12 |
| StockLevel | 预留评估 | 1/2 | 2/8 | 2/2 |

以上是静态位置计数，不是请求数或分类准确率。两个预留模块合计：5/6处SQL可表示，14/20个槽位有明确SQL用途，13/14个用途的Java值可恢复。不能只报最后一个比例而隐去多表查询未覆盖的6个槽位。

具体发现：

- Delivery恢复`Timestamp(System.currentTimeMillis()) → JDBC第1位 → order_line.ol_delivery_d`。但DELETE仍不支持，相关3个槽位未纳入；`BigDecimal.valueOf(...)`来源仍未知。这里只解决先前的UPDATE结构断点，未解决整个Delivery能力恢复。
- OrderStatus包含40个投影端口、4个行选择端口、2个排序端口和1个数量限制端口；12个JDBC参数均用于行选择。一个参数来自客户对象字段，原Java提取器标记`unsupported_expression:MEMBER_SELECT`，因此来源保持未知。
- StockLevel的计数查询使用`FROM order_line, stock`。冻结解析器只支持单表结构，故记录`unsupported trailing clause ,`，整条查询及6个相关槽位保留未覆盖；没有将其错误解释为单表查询。

已核对预留目标图中每个已知／未知Java来源均能到达声明的SQL效果，SCC压缩保留全部节点；30个归档文件哈希核验通过。

## 能说明和不能说明的事

可以说明：同一个实现不依靠模块名或目标槽位提示，能够在预留的OrderStatus上重用值／用途结构；遇到StockLevel的新SQL结构会明确失败。它比只支持指定时间INSERT的局部切片覆盖更多，但仍有限。

不能说明：这些图足以判断某个模块可删除，或者某个改写会保持业务能力。当前仍缺：

- 跨方法实际参数与返回值对应，以及ResultSet解码到应用观察的关系；Java来源目前可停在方法参数边界，SQL投影不能直接等同于用户可见结果。
- 语句之间的完整执行次序、堆／别名、路径可达性和异常行为；局部图不能代替完整调用／效果DAG。
- SQL类型、NULL语义、基数、模式约束、触发器、隐含调用效果和并发语义；表达式树相同或可恢复不代表值有效。
- 候选相对原实现的共同特征、开发模块拟合、冻结目标预测及行为验证标签；`candidate_pair_features`、`risk_score`和`transfer_accuracy`仍为null。

OrderStatus和StockLevel只是未参与本轮表示开发，项目历史中已有源码和执行记录，不能称为独立未见软件。若后续根据它们的未知原因扩展表示，应将其转为开发材料，重新选择评估目标。本轮没有追求把覆盖率补满；固定检查到此结束。

下一研究应围绕这份冻结表示构造候选相对变化及模块留出评分实验，而不是立即针对StockLevel增加解析分支。仍需单独报告弃权和未覆盖效果，不能用低风险分数代替缺失关系。

## 实现与证据

- [共同表示实现](../scripts/value_effect_ir.py)、[开发控制](../scripts/test_value_effect_ir.py)、[冻结迁移入口](../scripts/test_value_effect_transfer.py)。
- [协议](../prototype/module_pruning/value_effect_transfer_protocol.json)。复现：`python3 -B scripts/test_value_effect_transfer.py`；仅需已有JDK、冻结JAR和源码，无Docker运行。
- [目标检查前快照](../evidence/value-effect-transfer/20261106-a0c82b14a2b0/before-target-freeze.json)、[执行报告](../evidence/value-effect-transfer/20261106-a0c82b14a2b0/report.json)、[开发检查](../evidence/value-effect-transfer/20261106-a0c82b14a2b0/development-controls.json)、[哈希清单](../evidence/value-effect-transfer/20261106-a0c82b14a2b0/manifest.json)。
- [OrderStatus图](../evidence/value-effect-transfer/20261106-a0c82b14a2b0/OrderStatus-graph.json)、[StockLevel图及未知](../evidence/value-effect-transfer/20261106-a0c82b14a2b0/StockLevel-graph.json)、[Delivery开发图](../evidence/value-effect-transfer/20261106-a0c82b14a2b0/Delivery-graph.json)。

本轮未修改默认应用、完整数据、原Java提取器或原跨层分析器；无新数据库执行、模型拟合、性能测试或模块删除。
