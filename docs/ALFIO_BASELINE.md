# alf.io 独立系统：原版基线与反编译可行性

后续进展：五个目标的未优化重编译版本已完成固定流程差分，见[差分报告](ALFIO_ROUNDTRIP.md)。下文保留本轮原版基线阶段的原始结论与边界。

2026-11-06。第二系统已进入有限迁移验证阶段。官方发布产物的原版服务流程跑通，五个关键目标的字节码反编译及未经修改的重编译全部成功。本轮建立可行性证据，尚未执行重编译版本的行为差分，也没有优化或性能结果。

## 固定输入与执行方式

- 项目：alf.io，发布标签 `2.0-M5-2609`，提交 `d44407944e5875bfaa3dd0cff6f4e42214f34931`。
- 官方 JAR：`alfio-2.0-M5-2609-boot.jar`，83,405,439 字节，SHA-256 `c769aa49fbd25ce8c348d78e4946b759dfa318bb7bffc0521378b78189176f39`，与 GitHub 发布资产提供的 digest 一致。
- 使用项目目录内的 Temurin `17.0.21+9`，未修改系统 Java。官方 JAR 声明 Build-Jdk-Spec=17；源码 Gradle wrapper 为 8.14.4。本轮直接使用发布产物，没有运行完整源码构建。
- 运行前校验解包后的全部 classes、资源及依赖均与官方 JAR 字节一致。执行使用官方 `BOOT-INF/classpath.idx` 中的 157 项依赖及原顺序，业务类来源检查通过。
- 数据库复用本地锁定镜像 PostgreSQL 18.6；隔离容器 2 CPU、1.5 GB，命名磁盘卷，端口仅绑定 localhost。Java 最大堆 1 GB、可用处理器数 2。
- 应用角色 `alfio_user` 为 NOSUPERUSER、NOBYPASSRLS。`fsync`、`synchronous_commit`、`full_page_writes`、`row_security` 均保持开启，UTC。
- 原版 Spring 配置启动完整上下文；探针直接调用其服务 Bean，保留原事务代理、Repository 代理和事件监听器。额外访问原版 `/api/v2/info`，返回 HTTP 200。业务流程本身不是 HTTP 层回放。

资产来源与哈希见 [assets.lock.json](../prototype/alfio/assets.lock.json)。测试夹具改编自固定上游 `IntegrationTestUtil` 和预订集成测试；源码只用于配置审计和夹具构造，反编译器读取字节码及二进制依赖。

## 原版基线

原版自动完成 **179 项迁移，全部成功**，public schema 共 **63 张表，其中 43 张启用 RLS**。创建一个组织、一个活动、一类票和20张票的合成夹具；这是首次建立的数据集，没有裁剪既有数据。记录每个检查点全部 public 表的行数及完整行哈希，并归档最终数据库完整 SQL 导出。

固定顺序共12个操作：预订3张、查询；尝试预订21张；再预订2张；取消第一单、处理释放票券；查询第二单；取消第二单、处理释放票券；再次预订1张、取消、处理释放票券。后三次处理均调用原版 `WaitingQueueSubscriptionProcessor.handleWaitingTickets()`。

| 检查点 | FREE | PENDING | RELEASED | 仍关联预订 |
|---|---:|---:|---:|---:|
| 初始 | 20 | 0 | 0 | 0 |
| 预订3张 | 17 | 3 | 0 | 3 |
| 库存不足尝试后 | 17 | 3 | 0 | 3 |
| 再预订2张 | 15 | 5 | 0 | 5 |
| 取消第一单，任务处理前 | 15 | 2 | 3 | 2 |
| 第一次处理释放票券后 | 18 | 2 | 0 | 2 |
| 取消第二单，任务处理前 | 18 | 0 | 2 | 0 |
| 第二次处理后 | 20 | 0 | 0 | 0 |
| 再次预订1张 | 19 | 1 | 0 | 1 |
| 取消第三单，任务处理前 | 19 | 0 | 1 | 0 |
| 最终处理后 | 20 | 0 | 0 | 0 |

**30项检查通过**，包括：

- 3张票的订单摘要为30.00 CHF，取消第一单后第二单仍保有2张票。
- 库存不足抛出原版 `NotEnoughTicketsException`，失败前后63张表的完整行哈希相等。这里校验表行回滚，不声称 PostgreSQL 序列值也回退。
- 票券总数始终为20，最终无遗留预订，取消后可再预订。审计记录等历史数据继续保留，最终数据库不要求回到初始逐字节状态。
- 应用角色不是超级用户，也没有 BYPASSRLS；显式设置组织上下文时，RLS隐藏其他组织的活动、允许本组织活动。此项验证SQL策略，不证明HTTP认证上下文传播完整。
- 实际业务 Bean 是 `TicketReservationManager$$SpringCGLIB$$0`，Repository 为 `jdk.proxy2.$Proxy112`，业务目标类来自官方产物。

应用可注入时钟固定为 `2026-11-06T10:00:00Z`，指令顺序固定。原版遗留 `new Date()`、UUID生成仍保持原样并记录实际值；尚未实现不同执行间的全值确定性比较。

## 字节码可行性

沿用已锁定的 Vineflower 1.12.0，目标及嵌套类从官方 JAR 提取；不把原源码作为反编译输入。原样输出分别以 `javac --release 17 -proc:none -implicit:none` 和空 sourcepath 编译，依赖来自官方二进制。

| 目标 | 反编译输出 | 原样重编译 |
|---|---|---|
| TicketReservationManager | 成功 | 成功 |
| TicketRepository | 成功 | 成功 |
| TicketReservationRepository | 成功 | 成功 |
| DataSourceConfiguration | 成功 | 成功 |
| RoleAndOrganizationsTransactionPreparer | 成功 | 成功 |

输出中保留了 `@Transactional`、`@QueryRepository`、SQL绑定，以及事务开始时调用权限上下文准备器的关系。每个目标另有 `javap -p -v` 证据。这里使用的是既有 Vineflower 路径，不是新执行的 REA/JEB 任务。

**重编译成功仅说明语法／类型可用，不是行为等价。** 尚未把重编译类替换到服务中运行，也未声称已自动恢复完整调用图。

## 已发现的迁移难点

1. **代理产生的执行边。** 事务与SQL调用经过Spring／NPJT代理；只读取普通Java直接调用边会漏掉框架效果。需要把运行时代理、字节码注解和SQL绑定连接起来。
2. **事件与任务跨越单次调用。** 取消路径通过 `CleanupReservations` 事件触发监听器，把票券变为 RELEASED；可售状态还依赖后续等待队列处理。两条路径都应保留，不能因为普通方法调用图中不连通而删除。
3. **非确定值与访问上下文。** UUID、遗留墙钟、按请求设置的组织权限尚未形成统一的差分契约。这些不能简单从最终结果中忽略；后续需要保留关联关系并控制来源。

本轮仅为固定调度关闭定时触发器，使用上游 `disable-jobs` profile，并显式执行所需原版任务；未删除任务实现。这种夹具不覆盖自动到期、有人排队时的分配、支付、邮件发送、完整业务HTTP权限或并发争用。

## 尝试记录与限制

- [首次尝试](../evidence/alfio-baseline/20261106-9817762bc8ba/run.json)：反编译／重编译成功，原版业务运行在取消后的断言处失败。原因是测试错误预期取消后立即FREE；记录保留，未修改原业务。
- [修正生命周期后的尝试](../evidence/alfio-baseline/20261106-f13fb37792db/run.json)：加入原版任务处理步骤，30项检查通过。此时依赖按文件名排序，仅作为初步基线保留。
- [最终基线](../evidence/alfio-baseline/20261106-788b831dd41c/run.json)：对齐官方classpath.idx的依赖选择与顺序，相同12步与30项检查再次通过；后续以这次为准。

Flyway提示本版本测试支持上限为PostgreSQL17，本轮18.6虽完成全部迁移与检查，仍只作为已观测的本地组合。原版还启用out-of-order迁移；本轮每次均从空库初始化，不能据此证明任意历史库升级可复现。正式论文基线前应单独锁定并验证合适的数据库组合，不在此通过静默升级依赖掩盖问题。

没有新的性能结论，没有新模块删除，没有更新风险模型。TPC-C已有实现和证据保持冻结。下一步限定为：以本次官方产物为对照，建立**未经优化的反编译重编译版本的差分验证**，先明确非确定值及代理／事件／任务边界，之后才讨论候选融合。

## 复现与归档

在已取得锁定资产的工作区运行：

```sh
python3 -B scripts/alfio_baseline.py
```

- [执行脚本](../scripts/alfio_baseline.py)、[服务探针](../prototype/alfio/AlfioBaselineProbe.java)
- [最终协议](../evidence/alfio-baseline/20261106-788b831dd41c/protocol.json)、[30项检查](../evidence/alfio-baseline/20261106-788b831dd41c/original/summary.json)
- [最终全表状态](../evidence/alfio-baseline/20261106-788b831dd41c/original/09-final-state.json)、[数据库导出](../evidence/alfio-baseline/20261106-788b831dd41c/final-database.sql)
- [97文件manifest](../evidence/alfio-baseline/20261106-788b831dd41c/manifest.json)

实验容器和卷均自动清理；源码、官方JAR、项目内JDK和证据保留供后续复现。该基线不依赖外部真实支付账户，也未触发真实支付交易。
