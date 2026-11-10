# 真实反编译模块：按姓名查询的入口限定删减

2026-11-05。本轮将已有调用图中的真实候选落到可运行 JAR：删除反编译 `OrderStatus.getCustomerByName` 方法，保留现有应用的五种事务、输入校验和全部数据库数据。候选只适用于 `TpccApp.main` 应用入口，不是完整 BenchBase 的替代品，也不是新的自动分析算法。

## 删除依据与粒度

当前应用的 OrderStatus 接口使用客户 ID。冻结适配器对 OrderStatus 的反射调用名称只有 `getCustomerById`、`getOrderDetails`、`getOrderLines`；名称是代码常量，不来自客户数据。按姓名查询的普通调用者是原 BenchBase 的 `OrderStatus.run`。因此候选来自入口约束，而不是“测试没有出现某个姓名”。

既有调用图已展示这一入口差异，见[初始化与入口分析](MODULE_INITIALIZATION_DAG.md)。本轮补充实际构建和执行证据，采用人工限定的应用画像；此前提取器的 unknown 不被改写成自动删除许可。

两个边界需要分别处理：

1. `run` 是抽象父类要求实现的方法，且原方法引用姓名查询。为构建应用专用版本，将它替换为明确抛出 `UnsupportedOperationException` 的实现，再单独删除姓名查询方法。三组实验把这两个变化分开。
2. `customerByNameSQL` 即使没有查询消费者，仍由构造方法创建，并被 `Procedure.initialize → getStatements` 按字段反射注册。因此保留该字段、SQL 文本及全部初始化代码；本轮不推断其初始化无效果。

冻结字节码中的适配器反射桥按方法名称和参数个数选择唯一目标，不依赖方法枚举顺序。删除一个不匹配这些调用名称的方法，不改变已有调用的匹配目标。框架初始化扫描的是字段，保留四个 SQL 字段可维持其注册表。以上是本候选的人工代码论证，不是对任意反射、插件或外部调用的全程序证明。

保留能力集合为现有应用接口的全部合法输入，数据域沿用应用原有约束。回归样本只负责寻找反例，不定义该集合。若保留入口增加原终端、任意外部方法调用或方法元数据观察者，此删减不适用。

## 固定三组实验

| 版本 | 原终端 `run` | 姓名查询方法 | 四项 SQL 初始化 |
|---|---|---|---|
| original | 原实现 | 保留 | 保留 |
| profile | 明确拒绝非应用入口 | 保留 | 保留 |
| pruned | 同 profile | 删除 | 保留 |

适配器和其他事务类使用同一批冻结字节码；两个新 JAR 只替换 `OrderStatus.class`。每次 JVM 实际类加载来源均检查指向相应 JAR，编译产生的松散类目录不进入运行 classpath。

每组使用已有四份冻结场景，共 62 条命令，顺序、时间和终端分配固定；没有搜索或生成有利于候选的新输入。每个场景、每个版本都从同一完整快照独立恢复。delivery_edges 使用既有、三组完全相同的场景设置；未为本候选裁剪数据。

普通快照包含 30,000 个客户、100,000 个商品、100,000 条库存、30,000 个订单、300,040 条订单明细，以及其余四张业务表。完整行哈希包含所有字段和重复行，不只比较参与请求的客户或订单。

比较内容为完整响应、逐命令业务观察、初始/最终九表完整行数与内容哈希、五项数据库一致性约束、完整 JDBC 记录。事务期的 JDBC 时钟沿用既有冻结机制；不启用耗时采集，不进行故障注入、并发或性能测试。

另设两个范围见证：原版和 profile 可对真实数据库执行姓名查询，pruned 中该方法不存在；profile 和 pruned 的原终端入口均明确拒绝。它们用于展示被排除的能力确实发生变化，不能把应用内一致推广到完整系统。

## 结果与停止条件

固定实验已通过，见[运行记录](../evidence/module-name-pruning/20261105-b14245195231/run.json)。三组共执行 **186 条应用命令**；每组均为 50 条提交、4 条业务回滚、8 条输入拒绝。四个场景 × 六类产物的 **24 组比较全部三方一致**，包括完整 JDBC 记录；三组 SQL 注册表完全相同。原版/profile 的姓名查询均成功，pruned 中方法确实不存在；两个应用专用版本均拒绝原终端入口。实验容器已清理，无清理错误。

三组 `OrderStatus.class` 未压缩大小为 9,349 / 7,540 / 6,688 字节。姓名查询方法的增量删减是 profile → pruned 的 **852 字节（约 11.3%）**，不能把排除终端代码的大小收益也归给该方法。此数字不是整个应用的缩减比例、内存节省或运行加速。

本轮的新增内容是一个真实反编译方法被物理删除、构建依赖被显式处理、初始化边界被保留，并接受完整应用回放对照。它没有解决一般能力推断，也没有带来新的已证实加速点。本方法原本就不在应用的业务执行链上；即使方法枚举等附带开销可能变化，也没有测量或声称性能收益。

到此停止这一个候选的实验，不继续增加样本或重跑相同矩阵。若之后研究加速，重点应转为仍被执行、但对保留输出和必要效果没有贡献的模块；需要独立关闭其效果义务，不能借本次不可达方法案例直接授权删除。

## 产物

- [实验实现](../scripts/test_name_lookup_pruning.py)、[初始化与范围探针](../prototype/module_pruning/NameLookupBoundaryProbe.java)
- [固定协议](../evidence/module-name-pruning/20261105-b14245195231/protocol.json)、[姓名方法的独立删除差异](../evidence/module-name-pruning/20261105-b14245195231/profile-to-pruned.diff)
- [候选 JAR](../evidence/module-name-pruning/20261105-b14245195231/pruned/reference.jar)、[候选字节码](../evidence/module-name-pruning/20261105-b14245195231/pruned-bytecode.txt)
- [冻结框架字节码](../evidence/module-name-pruning/20261105-b14245195231/procedure-bytecode.txt)、[冻结适配器字节码](../evidence/module-name-pruning/20261105-b14245195231/adapter-bytecode.txt)
- [范围反例与 SQL 注册表](../evidence/module-name-pruning/20261105-b14245195231/baseline_pruned/boundary.json)、[归档清单](../evidence/module-name-pruning/20261105-b14245195231/manifest.json)

第一次准备运行在探针编译阶段发现 `initialize` 为 protected，未启动数据库；修正探针访问后执行上述实验。失败记录保留在 `evidence/module-name-pruning/20261105-508a276cf191`，不计作一次通过的实验。

复现命令（会新建并清理一个隔离的本地 PostgreSQL 容器）：

```sh
python3 -B scripts/test_name_lookup_pruning.py
```
