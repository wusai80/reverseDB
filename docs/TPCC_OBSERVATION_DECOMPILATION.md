# 行为监控与字节码反编译

2026-11-03：已实际监控参考/候选应用，并对五类事务相关的编译产物进行 Java 反编译。监控运行 `8729d2283fdc`，反编译批次 `20261103T125711-a3efc54a`。

后续更新：已完成[选定类的重建运行验证](TPCC_DECOMPILATION_ROUNDTRIP.md)，八次数据库执行与冻结原版逐一一致。下文的“尚未重建运行”描述保留为本轮监控取证当时的边界。

## 运行中观察到了什么

八个隔离数据库按原四组固定清单运行，参考与候选各收到 62 条命令。每个实现有 50 次提交、4 次业务中止、8 次输入拒绝；合计 **108 次进入数据库的业务命令**完成关联，16 条拒绝输入没有业务 SQL。

关联依据是明确标识，不靠相似 SQL 或时间排序猜测：

1. `runtime.json` 记录命令 ID、事务类型、逻辑终端、JVM PID、PostgreSQL PID、开始/结束时间和结果。
2. 每笔事务用 `set_config(..., true)` 设置局部 `application_name` 标签，提交/回滚后恢复。观察用查询保留原连接标签，业务 SQL 使用独立标签。
3. `jdbc-trace.json` 保存绑定参数、读取值、SQL 操作、批次和 JDBC 执行耗时；按命令 ID 分组。
4. PostgreSQL 原始日志按数据库与事务标签对应，要求同一命令仅关联一个会话，且最终 COMMIT/ROLLBACK 与应用结果吻合。
5. 每个 JVM 另保留原生 `runtime.jfr`、所选事件 JSON 与类加载日志。JFR 记录执行采样、Socket I/O、GC 等；它不构成完整方法调用计数，也未将每个 Socket 事件逐一对应到 SQL。

八次受监控执行的业务响应、逐步写入状态及九表摘要与冻结的未监控结果一致。业务服务字节码未变；新增探针位于共享运行框架和 JDBC 记录器。探针增加元数据查询、标签和采样开销，因此耗时只用于这次运行的观察，不能当作性能基准或加速比。

可核查例子：参考与候选各 10 次成功 NewOrder，共执行 20 次 `executeBatch`；各 4 次无效商品中止均未执行批次，服务器记录了回滚。这与反编译中“先校验商品、最后执行订单行和库存批次”的结构相符。空配送用例在候选端实际执行 10 次地区查询、没有更新，随后提交。

## 反编译做了什么

使用官方 [Vineflower 1.12.0 发行包](https://github.com/Vineflower/vineflower/releases/tag/1.12.0)，按照其[命令行接口](https://vineflower.org/usage/)处理 `.class` 输入。下载文件与 GitHub 发行元数据的 SHA256 一致：`1dfcfe974395734fa467ce620661c7623d05ba83670de0529b1fbd63ff548b9d`。工具仅保留在项目 work 目录，没有修改全局配置。

- 参考：NewOrder、Payment、OrderStatus、Delivery、StockLevel、TPCCProcedure，共 6 个类。
- 候选：五个 Service、Sql、CandidateApp 及相关嵌套类，共 10 个类。
- 产出 **13 个 Java 文件**；每个选定 class 均有 `javap -c -p -s` 字节码检查输出。
- 未手工修改任何反编译文件。参考、候选业务类、包含 CandidateApp 的候选三次重新编译均成功；候选存在 unchecked 编译提示，原始日志保留。
- 16 个选定 class 的快照 SHA256 与监控运行实际加载的对应字节码一致，加载来源也已核对。

这些 Java 文件来自编译产物，已有 Java 源码不作为反编译器输入。不过我们此前已经阅读过源码，因此这不是无源码盲测。重新编译成功只证明语法/类型兼容性；**尚未将反编译后重建的应用重新执行同一组差分**，不能据此报告反编译语义等价完成。

REA 沿用此前已完成的制品身份取证。本次真正的 Java 反编译由 Vineflower 执行，不能归因为 REA/JEB 自动恢复。

## 静态结果与运行证据如何互相核对

| 事务 | 从字节码反编译可见 | 对应的运行验证 |
|---|---|---|
| NewOrder | 地区/库存锁、订单编号递增、两组延后执行的批次、商品缺失抛出异常 | 成功执行两批；无效商品停止批次并回滚，相关状态恢复 |
| Payment | 仓库/地区累计收款、客户余额、BC 备注截断、history 写入、float 转换 | BC/GC、跨地区、重复付款和 500 字符截断用例一致 |
| OrderStatus | Service 选最新订单 ID；CandidateApp 再读取订单和订单行 | 实际返回新建及已配送订单，并在只读事务执行 |
| Delivery | 各地区取最早待配送单、删除待配送记录、更新承运人/时间及客户余额 | 部分和全空队列、新单配送均实际执行并核对状态 |
| StockLevel | 最近 20 单、不同商品去重、库存阈值 | 阈值边界用例结果一致，并在只读事务执行 |

反编译不能单独证明回滚；它发生在本次未反编译的事务运行框架内，由数据库日志和状态证据补足。类已加载不代表每个方法和分支都执行。JFR 部分短事务没有采到业务方法栈，不能把缺少采样当作方法未执行。

本轮一个具体提醒是：若只反编译 OrderStatusService，会看到它仅返回订单 ID，容易误判缺少订单行能力。加入 CandidateApp 的实际字节码后，才能定位应用层投影。这说明后续恢复业务契约需要沿调用关系划定观察范围，不能把单个类当成完整业务边界。

## 产物与复跑

- [监控关联汇总](../evidence/tpcc-observation/20261103-8729d2283fdc/run/observation-summary.json)
- [编译产物与实际加载身份核对](../evidence/tpcc-observation/20261103-8729d2283fdc/run/artifact-binding.json)
- [候选 NewOrder 反编译代码](../evidence/tpcc-observation/20261103-8729d2283fdc/decompilation/sources/candidate/reversedb/tpcc/NewOrderService.java)
- [候选应用层反编译代码](../evidence/tpcc-observation/20261103-8729d2283fdc/decompilation/sources/candidate-app/reversedb/tpcc/CandidateApp.java)
- [反编译发现及定位](../evidence/tpcc-observation/20261103-8729d2283fdc/decompilation/findings.json)
- [完整归档清单](../evidence/tpcc-observation/20261103-8729d2283fdc/manifest.json)

```sh
cd /Users/wusai/Documents/code/reverseDB
python3 scripts/tpcc_app.py verify --observe
python3 scripts/decompile_tpcc.py
# 使用上一条命令输出的反编译目录，补入应用层：
python3 scripts/decompile_tpcc.py --extend-candidate-app <反编译目录>
python3 scripts/summarize_tpcc_observation.py <监控运行目录> \
  --decompilation <反编译目录>
```

反编译脚本默认使用已冻结的业务 class；如业务实现发生变化，应通过 `--candidate` 指向本次运行的 `standalone-candidate`，重新绑定哈希。摘要脚本拒绝字节码不匹配或找不到实际加载来源的情况。

归档保留原生 JFR；体积较大的事件 JSON 使用 gzip 无损压缩，摘要脚本可直接读取。反编译器及依赖 JAR 留在 work，对应路径和哈希列在 `retained-assets.json`。原始反编译 manifest 中的绝对路径保持不变，指向仍保留的工作目录。没有篡改旧证据来适配新目录。

62 项本地检查通过，包括遗漏事务标签、错误提交状态、缺失 JDBC 计时和缺失命令的负例，以及反编译检查失败时不能报告通过。专用数据库、容器、网络和数据卷均已清理。当前尚未恢复通用调用图、自动推导业务契约或开展数据库优化。
