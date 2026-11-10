# ResultSet消费结构与局部返回线索

2026-11-06。新增基于带类型归属Java AST的消费结构提取：从PreparedStatement的查询结果连接到ResultSet游标操作、列读取，以及局部返回表达式或返回对象字段的候选关系。既有概率模型、权重、阈值和SQL解析器均未改动。

52项检查通过，包含13种静态游标控制例，以及28次新编译的真实反编译方法调用。实测区分了只读取首行的三个方法与遍历提供行的明细方法；尚未完成通往最终应用API输出的完整关系恢复。

## 提取范围

查询、语句对象和游标对象通过javac符号及声明身份连接，不根据变量名字猜测。收集所有直接ResultSet调用，保留列名／序号、源码位置、循环上下文和getter的直接使用位置。

游标描述有五类：

- `single_advance`：局部查询结果只有一个直接next调用位置，该调用不位于会重复使用此游标的循环中，且没有检测到不支持操作、显式逃逸或重赋值。
- `iteration_capable`：next位于该游标的循环中。循环内部可能提前return或break，因此不能宣布“必定消费全部行”。
- `multiple_advances`：存在多个next调用位置。
- `no_advance`：没有直接next调用。
- `unknown`：查询来源、别名／逃逸、捕获、重赋值或游标操作超出范围。

这些是可追溯的结构描述，不是风险类别或等价证明。每次循环重新创建的局部ResultSet，与同一个ResultSet在循环中反复next作了区分；外部隐式别名、驱动实现和跨方法行为仍未证明完备。

SQL列连接复用已有值／效果IR：显式投影列名／别名匹配命名getter，投影位置匹配序号getter。不能解析SQL、列名动态或对应不唯一时保持未知。getter的类型解码只记录API，不假定不同getter语义相同。

局部使用区分直接return表达式、字段赋值、局部赋值／初始化和嵌套表达式／调用。对`o.field = rs.getX(...)`随后`return o`记录同一个声明对象的返回线索，但明确标记为候选关系：未证明字段后来没有覆盖、对象没有别名、finally没有改变返回或调用方实际使用这些字段。

## 真实源码覆盖

| 模块 | ResultSet声明 | single_advance | iteration_capable | unknown | 已识别直接getter中连接到SQL投影 |
| --- | ---: | ---: | ---: | ---: | ---: |
| NewOrder | 5 | 5 | 0 | 0 | 13/13 |
| Payment | 5 | 3 | 0 | 2 | 15/15 |
| Delivery | 3 | 3 | 0 | 0 | 3/3 |
| OrderStatus | 4 | 1 | 1 | 2 | 11/11 |
| StockLevel | 2 | 2 | 0 | 0 | 1/2 |

共19个ResultSet声明、44个已识别直接getter位置，其中43个可连接投影。这不是程序全部列读取的覆盖率：传入其他辅助方法后的读取尚未追踪。

Payment和OrderStatus的客户查询各有两个游标传入辅助处理代码，保留为unknown。StockLevel有一个getter来自此前未支持的多表查询，游标的直接结构可记录，但SQL投影对应仍未知，没有借getter名称猜回整条SQL。

OrderStatus的`getOrderDetails`得到single_advance，三个getter分别连接订单ID、承运人和时间投影，并记录写入返回对象字段的候选线索。`getOrderLines`得到iteration_capable；StringBuilder、格式转换及集合返回没有被伪装成已证明的完整值传播链。

## 新执行验证

将冻结的OrderStatus、Delivery、StockLevel反编译源码重新编译，确认实际加载这批类。使用严格JDBC边界替身提供有序行，不执行或模拟PostgreSQL。未支持的JDBC调用直接报错，不返回万能默认值。

实际调用四个方法：OrderStatus.getOrderDetails、OrderStatus.getOrderLines、Delivery.getOrderId、StockLevel.getOrderId。每个方法提供7组输入：空、1行、2行、5行、修改2行输入的后续行、修改5行输入的后续行、交换前两行，共28次调用。

| 方法消费类型 | 多行输入下实际next次数 | 后续行变化 | 首行交换 |
| --- | --- | --- | --- |
| 三个首行方法 | 每次1次，只读取行索引0 | 返回不变 | 返回改变 |
| 订单明细格式化方法 | n行时n+1次，读取索引0到n−1 | 返回改变 | 返回顺序改变 |

例如订单详情输入`[101,202]`和`[101,999]`都返回订单101；输入`[202,101]`返回202。明细方法则将第二行202或999分别写入不同的返回字符串。

空输入也保留实际差别：订单详情及库存订单号查询抛出RuntimeException，Delivery订单号查询返回null，明细方法返回空列表。不能把这些方法统一成“无行时返回同一个值”。

若将局部消费表示为C(R)，本轮执行见证了若干首行消费者的`C([r1,r2]) = C([r1,r2′])`，以及明细消费者在相同改动下的不等。有限见证不推出对全部R或所有程序都成立，也不授权删除任何数据库记录。

## 对上一轮反例的意义

现在可以从源码结构获得一个新的区别：排序影响首行选择，增加第二行未必影响只读取首行的方法。这个区别可以成为候选评估的输入，但没有写成“LIMIT变大安全”的规则；SQL错误、驱动取数、其他副作用和应用契约仍可能影响结果。

本轮没有用已知反例重新训练模型，也没有把这些已见模块称为新的独立迁移测试。下一条尚未闭合的关系是局部方法结果经调用者到最终API输出的实际使用；对象字段候选边、方法返回值或SQL投影均不能直接当作最终可观察性。

## 证据与停止范围

- [类型化提取器](../prototype/module_pruning/ResultConsumptionExtract.java)、[真实方法边界探针](../prototype/module_pruning/ResultConsumptionProbe.java)、[固定验证入口](../scripts/test_result_consumption.py)。复现：`python3 -B scripts/test_result_consumption.py`。
- [协议](../evidence/result-consumption/20261106-1857da9685fb/protocol.json)、[检查报告](../evidence/result-consumption/20261106-1857da9685fb/report.json)、[静态汇总](../evidence/result-consumption/20261106-1857da9685fb/static-summary.json)。
- [OrderStatus消费事实](../evidence/result-consumption/20261106-1857da9685fb/OrderStatus.json)、[SQL到getter及返回候选](../evidence/result-consumption/20261106-1857da9685fb/OrderStatus-links.json)、[28次调用及逐游标事件](../evidence/result-consumption/20261106-1857da9685fb/probe.json)。
- [41个归档文件的哈希清单](../evidence/result-consumption/20261106-1857da9685fb/manifest.json)。

固定检查完成后停止。无新数据库执行、完整端到端回放、性能测试或模块删除；未模拟事务、约束和并发，未裁剪原始业务数据。
