# 从拒绝式白名单守卫恢复动态调用目标

2026-11-05。NewOrder 配置选路的小范围分析完成。此前无法解析的函数名现可收窄为源码白名单允许的四个候选；并未默认只选 full。新增 48 项检查通过，原有 24 项图回归及 22 项边界 AST 回归通过。没有修改应用、生成代码、数据库或性能测试。

## 规则与依据

实际源码结构为：

```java
String mode = System.getProperty("reversedb.fusion.mode", "full");
if (!Set.of("full", "pruned", "early_write", "full_fault").contains(mode))
    throw new IllegalArgumentException(...);
// 随后使用 mode 拼接要调用的数据库函数名
```

对于能到达后续 SQL 构造的位置，mode 必须属于该集合。依据是之前的否定成员检查和直接抛出，不是默认值、运行日志或某个测试种子。

当前采用一条保守的局部控制规则：

1. 被检查值是类型已解析的局部／参数 String，且输入源文件中没有对它进行赋值、复合赋值或自增／自减。
2. 检查解析为真实 java.util.Set.contains，集合由真实 Set.of 的有限字符串字面量构成。
3. 条件是“不属于集合”，非法分支直接 throw，或只含该 throw 的代码块；暂不支持 else 或 return 拒绝形式。
4. 该 if 是使用位置所在语句之前、同一外层代码块中的直接语句，因此到达使用位置必先经过它。不会从相邻 try、if、循环、lambda 或另一个方法中借用检查。

这是受限的支配关系判定，不是完整 CFG 分析。对于合法但不在此模式内的控制结构，返回未知，不把模式不匹配误报成应用错误。

每项收窄结果附上守卫源码行、变量声明身份、允许值、初始化表达式及规则版本。既有“值来源→JDBC→SQL→写入列”图新增 finite_target_guard 控制节点；运行时究竟选择集合中的哪一项，仍保留为动态选择节点。

## 实际结果

自动恢复的 NewOrder 函数集合：

- reversedb_fusion.neworder_full
- reversedb_fusion.neworder_pruned
- reversedb_fusion.neworder_early_write
- reversedb_fusion.neworder_full_fault

两种函数目录得到不同且明确的结果：

| 可用 SQL 目录 | 依赖恢复结果 |
| --- | --- |
| 当前端到端实验的部署脚本 | NewOrder full 和 Payment 两个静态分支共 3 条可解析时间写入路径；另 3 个 NewOrder 目标保留“函数体缺失” |
| 已有归档中全部 NewOrder 定义，加 Payment 定义 | 恢复 6 条可能的时间写入路径；四个 NewOrder 分支均将 JDBC 第 5 位连接到 oorder.o_entry_d |

后者只是静态分析可用的实现目录，不表示这些诊断函数都已部署、应该执行或与原版等价。early_write 是已有的错误改写负控，full_fault 是故障诊断；恢复它们的调用边不等于批准它们。

原版与融合版的参数对应仍由真实 SQL 的使用关系推导，没有在图恢复器中写入“NewOrder 第 5 位对应第 5 位”的业务规则。Payment 既有映射不变。

## 固定验证

20 个带类型归属的 Java 控制例，各检查目标集合和守卫证据，共 40 项；真实案例及函数目录检查 8 项，总计 48 项。

- 直接 throw、单条 throw 块、局部变量改名、三元素集合、外层守卫约束嵌套使用：恢复相应有限集合。
- 无守卫、使用后才检查、不拒绝非法值、只在条件分支检查、异常被捕获后继续执行：保持未知。
- 检查了不同变量、检查前后重赋值、检查极性相反：保持未知。
- 可变集合、同名伪 contains 方法、lambda 或循环中的检查、return 拒绝、动态白名单元素：保持未知。

检查器使用类型解析确认方法身份，不根据 contains／Set 的文本名字判断。重赋值规则目前比必要条件更严格，即使赋值发生在守卫前也拒绝收窄；这是有意保留的精度边界。

原有跨层图测试现在明确作为冻结 v2 AST 的回归入口，验证旧未知事实不会因图算法升级而被擅自补全。当前 v3 的实际 AST 提取和新守卫行为由本轮测试独立覆盖。

## 产物

- [提取器](../prototype/module_pruning/JdbcBoundaryExtract.java)、[依赖图实现](../scripts/cross_layer_dependencies.py)、[新验证驱动](../scripts/test_guarded_dispatch.py)。
- [最终报告](../evidence/guarded-dispatch/20261105-2ef8f8b49ccc/run.json)、[控制例结论](../evidence/guarded-dispatch/20261105-2ef8f8b49ccc/control-summary.json)。
- [部署目录下的图](../evidence/guarded-dispatch/20261105-2ef8f8b49ccc/deployed-catalog-graph.json)、[全部归档目标的图](../evidence/guarded-dispatch/20261105-2ef8f8b49ccc/all-archived-targets-graph.json)、[参数对应结果](../evidence/guarded-dispatch/20261105-2ef8f8b49ccc/correspondence.json)。
- [22 项 AST 回归](../evidence/jdbc-boundary-contracts/20261105-149567965337/run.json)、[24 项冻结图回归](../evidence/cross-layer-dependencies/20261105-f01f3f9a7525/run.json)。

复现：`python3 -B scripts/test_guarded_dispatch.py`；无需 Docker。驱动校验真实 AST 事实与当前提取器版本一致，并重新编译／分析控制例。

## 尚未闭合的内容

局部配置守卫的目标集合已经闭合；它不解决具体模式选择、函数是否部署、过程中的异常／阶段次序、外部 SQLStmt 字段修改、完整别名与调用图分析。所有图仍不授权自动删除。

本轮结束后，不再围绕这条模式重复加测试。下一步应把“目标集合已知”“函数体可用”“效果已恢复”“仍依赖外部前提”分开看，选择一个实际影响融合判定的缺口；不因一个守卫可分析就扩建完整静态分析平台。
