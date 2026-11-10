# alf.io 未优化重编译版本的差分验证

2026-11-06。**本轮五个目标类的未优化重编译版本通过固定流程差分：两版各执行12个操作、29项检查，53份比较文件一致。** 官方原版与重编译版在12个数据库检查点的63张表完整数据、行哈希及序列值均一致。未修改反编译输出、未做融合或删减，没有性能结论。

实验末尾发生清理脚本变量名冲突，原始运行状态因此保留为 `failed`；业务运行与差分在此前已经通过。随后按核实的准确容器ID和卷名完成清理，并单独归档恢复记录。本轮没有为了取得绿色运行状态重复执行业务。

## 对照的具体内容

使用上轮锁定的官方发布JAR、Java17、PostgreSQL18.6和官方依赖顺序。重编译臂直接复用 [基线归档](../evidence/alfio-baseline/20261106-788b831dd41c/run.json) 中的class文件，逐一核验原manifest，不重新生成或修补源码。

替换范围是：

- `TicketReservationManager`
- `TicketRepository`
- `TicketReservationRepository`
- `DataSourceConfiguration`
- `RoleAndOrganizationsTransactionPreparer`

含嵌套类共8个class文件，其余应用模块和依赖继续使用官方产物。两臂分别核验五个目标类的实际加载来源；测试插桩记录的管理器输入字节码哈希也分别匹配官方版和重编译版，排除了只测到原版的情况。

仍保留Spring事务代理、NPJT动态Repository、事件监听器、原版等待队列处理器，以及非超级用户、NOBYPASSRLS、行级访问控制和原数据库持久化设置。定时触发器按既有夹具关闭，三次必要任务通过原处理器显式执行。

## 相同起点和非确定值

先由官方原版创建完整数据库与20张票的夹具，完成一次HTTP info检查，再导出整个数据库。两臂各自在新建数据库中恢复这份相同导出，随后启动相同Spring上下文。迁移历史、用户密码散列、会话、初始票券UUID等起始数据没有重建、删除或字段过滤。

业务执行的UUID与旧式时间调用通过两臂共用的Java agent控制：

1. 只在 `alfio/` 应用字节码中替换 `UUID.randomUUID()`、无参 `new Date()` 和 `System.currentTimeMillis()` 调用点；不改JDK、SQL、数据库结果或业务返回值。
2. 只在12个固定操作期间启用；UUID由固定种子、操作标签、调用方类名和该类调用计数生成，保留version 4／variant 2格式，保证同一操作内不同调用产生不同ID。该来源是确定性测试提供者，不代表生产随机源的安全属性。
3. 旧式时间固定为 `2026-11-06T10:00:00Z`；可注入 `ClockProvider` 继续使用上轮固定时钟。测试agent记录源调用，意外的跨线程源调用会失败，不将其静默归一化。
4. 核对两臂完整调用记录：12次操作起点、23次UUID、9次时间调用，共44条，全部一致。实际调用涉及预订管理器、事件预订管理器及扩展服务。

两臂加载过程中各有38个应用类包含这些插桩点；仅受控操作期间启用确定值。因此这是一项明确披露的共同测试干预，不是未经插桩的生产运行。单独的字节码插桩见证验证了UUID的可重复性、区分性、格式，以及无参Date构造的固定时间。

## 比较范围与结果

12步保持既有顺序：预订3张、查询、超额预订失败、再预订2张、取消第一单、处理释放票券、查询第二单、取消第二单、处理释放票券、再次预订1张、取消、处理释放票券。

| 比较内容 | 数量 | 结果 |
|---|---:|---|
| 全表行数和原始行哈希 | 12份 | 一致 |
| 63张表的完整JSON行数据 | 12份 | 一致 |
| public schema全部序列的last_value | 12份 | 一致 |
| 票券库存及预订关联 | 10份 | 一致 |
| 两次完整订单摘要、异常类型、角色、检查结果、总记录、源调用记录 | 7份 | 一致 |
| **总计** | **53份** | **一致** |

JSON按值比较，对象键顺序无关、数组顺序保留；所有业务字段参与比较。数据库行哈希不做时间、UUID或字段归一化，原始行数据也完整保存。仅排除两份实验元数据文件：`loaded-beans.json`的臂特有加载位置、`transformations.json`的不同字节码哈希；这些来源另行验证。

两版各29项业务／权限检查通过，较上轮少的一项HTTP info检查移到公共夹具准备阶段。库存不足时表行全部回滚；跨臂序列值也一致，但未要求失败事务的序列递增在同一臂内回退。取消后的RELEASED中间状态和后续FREE状态都参与比较。最终票券仍有20张、全部可售，无遗留预订；审计等历史记录保持完整。

另有四个**比较器负向对照**：改变金额、关联ID、时间，或增加重复行，均被比较器检出。这些是在合成JSON见证上检查比较器，不是给真实应用制造四个缺陷，也不计作额外业务覆盖。

## 清理故障与恢复

负向对照循环原先复用了保存容器名的变量，导致收尾尝试用错误名称清理。原始日志与manifest保持不变；修复仅把循环变量改为独立的 `control_name`，并检查资源标识变量只在初始化时赋值。

恢复时先核对本次准确容器ID、名称及实验标签，归档实际数据库日志，然后删除该容器和命名卷。按实验标签只读查询确认无残留资源。恢复目录保留修复diff、修复后的脚本、原实验manifest哈希和清理记录，未重跑业务。

- [原始运行记录：比较通过、清理失败](../evidence/alfio-roundtrip/20261106-17b16f1a21eb/run.json)
- [清理恢复与完成记录](../evidence/alfio-roundtrip/20261106-17b16f1a21eb-cleanup/recovery.json)

## 结论与下一步边界

本轮支持“这五个反编译目标可放回原框架，并在该固定流程中保持观察一致”。它不证明整个alf.io均已反编译等价，不覆盖所有输入、真正并发争用、完整HTTP权限传播、真实支付或自动任务调度。仅有一个固定流程对照，不报告统计泛化率。

预订流程仍显示出三个需要纳入分析的执行边界：Spring事务代理、NPJT到SQL的绑定、事件监听与任务处理。下一步限定为恢复一条预订创建链的这些关系，形成可审查的融合候选；取消和权限等必要效果继续保留，不以低调用频率、未使用返回值或静态不可达直接授权删除。

TPC-C成果与风险模型保持冻结。尚未为alf.io优化，也没有测量速度。

## 复现与证据

```sh
python3 -B scripts/test_alfio_roundtrip.py
```

- [脚本](../scripts/test_alfio_roundtrip.py)、[共同测试源控制](../prototype/alfio/ReplayEntropy.java)、[插桩见证](../prototype/alfio/ReplayEntropyWitness.java)
- [固定协议](../evidence/alfio-roundtrip/20261106-17b16f1a21eb/protocol.json)、[比较结果](../evidence/alfio-roundtrip/20261106-17b16f1a21eb/comparison.json)
- [公共完整夹具](../evidence/alfio-roundtrip/20261106-17b16f1a21eb/fixture.sql)、[原版完整最终状态](../evidence/alfio-roundtrip/20261106-17b16f1a21eb/original/09-final-rows.json)、[重编译版完整最终状态](../evidence/alfio-roundtrip/20261106-17b16f1a21eb/recompiled/09-final-rows.json)
- [177文件实验manifest](../evidence/alfio-roundtrip/20261106-17b16f1a21eb/manifest.json)、[清理恢复manifest](../evidence/alfio-roundtrip/20261106-17b16f1a21eb-cleanup/manifest.json)
