# alf.io 有序批量写入的契约验证

后续进展：[实验入口已实现并通过限定差分验证](ALFIO_ORDERED_FUSION.md)，尚非通用准入。以下保留原路径契约阶段的结论。

2026-11-06。原版契约测试通过：**8个数据库场景、58项检查；原版与未修改的反编译重编译版有5份离线契约输出完全一致。** 58项包含18项离线语义检查与40项数据库检查。本轮刻画原路径的必要行为，没有实现融合版本，候选仍未准入。它不是候选等价性或性能证明。

## 对融合边界的实际修正

不能把 `reserveTickets` 看作“给若干列赋值”。它还依赖参会人供应函数、`AttendeeData.getFullName → CustomerName` 校验、元数据容器与JSON序列化。这些在数据库写入前全部完成。

第一轮测试把两个空字符串姓名当成可接受输入，原版实际抛出 `IllegalArgumentException: firstName must not be null`。这是姓名拼接调用了校验所致。当前将其作为预期失败保留；不能在数据库中简单拼接姓名后跳过校验。一个姓名为NULL时，原实现不会走同一拼接分支，这个区别也保留。

元数据为NULL时绑定 `{}`；元数据为空映射时，原Java代码仍生成包含 `metadataMap/general/attributes` 等字段的完整容器。二者不能合并。完整Unicode、引号和换行数据保留，字段不因当前应用路径未使用而裁剪。

因此，最小变换边界位于**原Java参数构造和校验完成之后、数据库逐行效果开始之前**。只研究固定SQL的通用绑定／调度边界；不删除上述业务依赖，不移动后续附加字段处理或首票读取。

## 可检验的形式化条件

设原输入为 \(x\)，Java参数构造为 \(M(x)=P=(p_1,\ldots,p_n)\)。

1. 若 \(M\) 在第 \(k\) 行求值失败，数据库写入轨迹为空；不能边构造参数边提前写前面的行。
2. 若 \(M\) 成功，按原顺序执行 \(U(p_1),\ldots,U(p_n)\)。重复ID保留，数据库转换／约束失败不能任意提前或推后。
3. 成功时结果为 \(\operatorname{JavaInt}(\sum_i \operatorname{JavaLong}(c_i))\)。失败时传播原异常，而不是把失败批次计数求和当作成功返回。
4. 候选除保留完整状态与返回／异常，还须保留外部可观察的失败前效果及事务状态。仅比较回滚后的表不足以证明等价。

第四点有实际反例证据：在隔离数据库增加观测用 `AFTER UPDATE` 触发器，将行效果记录到临时表，并推进临时序列。第二行邮箱过长失败时，第一行已经推进序列一次；回滚恢复了业务表和临时表，却没有撤销这次序列推进。如果把第二行的数据库校验提前到第一行更新前，最终业务表可能相同，但这项效果就不同。

**该触发器是测试观察器，不是alf.io原有业务触发器。** 它用于展示“只看最终数据”遗漏的语义。原有触发器、约束与RLS定义另行完整记录，没有删除或禁用。

## 测试结果

数据库使用锁定PostgreSQL镜像、官方JAR内Spring/JDBC依赖、既有完整夹具；连接角色非superuser且无BYPASSRLS。每个用例由测试外层事务回滚，并比较所有63张public表的完整行哈希和36个public序列状态。

| 场景 | 实际结果 | 保留的语义 |
|---|---|---|
| 同一ID按顺序出现两次，共3行 | `[1,1,1]`，返回3 | 更新顺序／次数保留，最后一次更新生效 |
| 中间ID不存在 | `[1,0,1]`，返回2 | 不把输入行数当作受影响行数 |
| ID为NULL | `[0]`，返回0 | 保留原SQL的NULL匹配行为 |
| 空输入 | `[]`，返回0，无SQL执行 | 不求值供应函数、类别或VAT |
| 组织不可见 | `[0,0]`，返回0 | 原RLS生效；外层业务计数校验保留 |
| 第二次参会人求值抛错 | `IllegalArgumentException`，无批次调用 | Java构造完成前不写入 |
| 第二行邮箱超过列长度 | `DataIntegrityViolationException`，SQLSTATE `22001` | 第一行效果已发生；事务进入失败状态 |
| 预订外键不存在 | `DataIntegrityViolationException`，SQLSTATE `23503` | 外键检查与事务失败保留 |

SQL失败后的查询返回 `25P02`，直到测试外层执行回滚。8个用例的业务表和原有序列均完全恢复。这里显式测试Repository层，不冒充新的服务端到端测试；真实Spring事务代理的既有证据仍来自前两轮固定回放。

失败批次的驱动计数是 `[-3,-3]`，不说明前面从未产生效果。原方法此时抛异常，没有正常返回。`SUCCESS_NO_INFO`、`EXECUTE_FAILED`及整数溢出另用合成模板返回值检查求和行为；这些合成向量**不代表本驱动成功返回过相同向量**。

## 参数接口

实际JDBC参数位置为：

`reservationId, categoryId, userLanguage, srcPriceCts, currencyCode, ticketMetadata, vatStatus, firstName, lastName, email, fullName, id`。

当前依赖下观测到：非NULL字符串使用 `setString`，Integer使用 `setObject`，测试中的NULL使用 `setNull(Types.NULL)`；SQL内保留 `::jsonb` 和 `::VAT_STATUS`。原Map参数未声明显式SQL类型。未来的类型化入口不能只按列类型猜测Java转换或NULL语义。

离线验证还覆盖原Java元数据编码、空字符串与NULL、供应函数顺序、姓名与JSON校验失败、VAT为空，以及模板异常原样传播。两版输入映射采用固定插入顺序；没有对输出JSON字符串做排序、过滤或归一化。

## 证据与当前决定

- [成功运行及输入哈希](../evidence/alfio-batch-contract/20261106-6b686908dc5e/run.json)、[49文件manifest](../evidence/alfio-batch-contract/20261106-6b686908dc5e/manifest.json)
- [数据库用例／JDBC绑定／效果记录](../evidence/alfio-batch-contract/20261106-6b686908dc5e/database/database-cases.json)、[58项检查](../evidence/alfio-batch-contract/20261106-6b686908dc5e/database/checks.json)
- [原约束、触发器与权限定义](../evidence/alfio-batch-contract/20261106-6b686908dc5e/database/schema-effects.json)、[离线两版比较](../evidence/alfio-batch-contract/20261106-6b686908dc5e/offline-comparison.json)
- [机器可读契约](../prototype/alfio/ordered_batch_contract.json)、[测试入口](../scripts/test_alfio_batch_contract.py)、[Java探针](../prototype/alfio/AlfioBatchContractProbe.java)

保留全部失败归档：`20261106-1c6d5124dc28`发现原姓名校验；`20261106-45df60c2f11d`是测试器对Java/Python文件URI表示差异的误判；`20261106-5e435c9acd96`发现测试输入 `Map.of` 跨JVM遍历顺序不同，导致JSON字符串键序不同。后两项通过解析URI与固定输入映射顺序修正，未修改原业务或忽略输出差异。这三个运行均未创建数据库资源。最初脚本的一处括号语法错误在任何实验启动前修复。成功运行后容器与卷均已清理。

**当前决定：原路径契约已形成可执行基线，但融合候选仍未准入。** 安全分流尚是要求，没有已经验证的分流实现。并发、死锁／取消、所有触发器配置和所有驱动版本不在本轮结论内。

下一步只制作一个实验性类型化入口，复用原Java构造／校验，保持同连接、调用者权限和逐行UPDATE，再与这里的原路径做差分；不能证明的调用在执行前留在旧路径，不允许候选部分写入后回退重试。通过局部契约及完整服务回放后再考虑性能；原实现已有JDBC batch，不预设收益，不扩大到其他模块。
