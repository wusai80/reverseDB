# TPC-C 风格真实事务功能基线

2026-11-03：固定版本 BenchBase + PostgreSQL 的首轮功能基线通过。执行链为 **4 个模拟终端 → 原版 JDBC 事务 → PostgreSQL**，没有 GraphQL/REST。此结果验证参考实现可运行，尚不是 REA 生成副本的验收。

## 实际结果

运行标识 `c615d3357989`，1 仓库、10 地区、30,000 客户、100,000 商品及库存记录。初始 30,000 订单、300,040 订单行、9,000 待配送订单。使用 SERIALIZABLE，混合负载 30 秒，配置总速率 10 次/秒、五类事务各 20%。这是便于覆盖的功能配置，不是标准事务比例、认证测试或性能结果。

| 事务 | 成功提交 |
|---|---:|
| NewOrder | 44 |
| Payment | 63 |
| OrderStatus | 62 |
| Delivery | 64 |
| StockLevel | 66 |
| 合计 | 299 |

另有 1 次 NewOrder 用户中止、3 次 Payment 序列化冲突重试，意外 SQL 错误为 0。四条实际数据库会话分别提交 75、75、74、75 次；事务日志中共 303 次 BEGIN、299 次 COMMIT、4 次 ROLLBACK，与工作负载直方图核对一致。最终订单数 30,045，包含独立受控探针提交的 1 单；history 从 30,000 增到 30,063，与成功 Payment 数一致。

独立 NewOrder 探针调用固定版本原业务方法，显式控制 JDBC 提交/回滚，并使用另一条连接观察：

- 最后一件商品无效时，原方法抛出预期 UserAbortException。事务内已改变地区订单编号并插入订单和待配送记录；回滚后所观测状态恢复。
- 此失败发生在批量库存更新及订单行写入执行之前，因此没有证明“已写入库存的回滚”。库存始终未变。
- 有效输入提交后增加 1 订单、1 待配送记录、5 订单行；5 项库存按原方法规则变化。另一条连接在提交前看不到修改。
- 探针的事务边界由测试代码控制；混合负载使用上游 Worker 的真实事务处理。两者分别提供证据，不混为完整隔离性证明。

混合负载前后，五项检查的违反数均为 0：仓库与地区累计收款、地区下一个订单号、订单行数量、待配送记录引用、待配送编号连续性。这不是完整 TPC-C ACID 验证。

## 固定输入与证据

- BenchBase commit：`33c00473807ebd49304d114a6d769d2d2b2bbb34`。GitHub 源码归档哈希和 978 个原文件未修改的核验记录已保存。
- PostgreSQL 18.6，使用既有 arm64 镜像锁，digest `sha256:3a4264520e121176619f54ff867d12774bc81e95e7b84db58d63bccdb6144b81`。
- 本机 OpenJDK 26.0.1，编译 release 23；Maven 依赖隔离存放在 `work/tpcc`。上游构建测试被跳过，不能据此报告其测试通过。
- 数据库容器限制 2 CPU、1536 MiB 内存，JVM 堆上限 1024 MiB；未使用远程服务器。独立网络、数据卷和容器已全部清理，端口仅绑定本机回环地址。
- [完整证据目录](../evidence/tpcc/20261103-c615d3357989/manifest.json)、[独立审核结果](../evidence/tpcc/20261103-c615d3357989/run/verification.json)、[原始运行记录](../evidence/tpcc/20261103-c615d3357989/run/run.json)。原运行记录保留待审核状态，审核结果单独保存，避免改写历史。
- 装载后、探针前的 `initial.dump`（约 40 MiB）和构建分发包保留在 `work/tpcc`；路径及哈希见 [retained-assets.json](../evidence/tpcc/20261103-c615d3357989/retained-assets.json)。证据目录本身不包含这两个大文件。

首次构建被 git 元数据插件阻断，使用插件的 skip 参数后构建成功，没有修改上游源文件。首次启动因 Docker Desktop 在 internal 网络上未发布请求的端口而停止，资源已清理；改用独立 bridge 网络及回环端口后成功。这两次工程故障均保留记录，不作为研究创新。

## 重跑与审核

当前工作区已准备源码与构建包。重新构建所用命令如下（首次依赖获取需要网络）：

```sh
cd /Users/wusai/Documents/code/reverseDB/work/tpcc/source
JAVA_HOME=/opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home \
MAVEN_SKIP_RC=1 \
MAVEN_USER_HOME=/Users/wusai/Documents/code/reverseDB/work/tpcc/maven-home \
MAVEN_OPTS='-Xmx1024m -XX:ActiveProcessorCount=2' \
./mvnw -B -ntp \
  -Dmaven.repo.local=/Users/wusai/Documents/code/reverseDB/work/tpcc/maven-repository \
  -Dmaven.gitcommitid.skip=true -Dfmt.skip=true \
  -Dmaven.compiler.release=23 -DskipTests package -P postgres
```

新运行需要本机 Docker Desktop 和 PATH 中的 Java/Javac 23+。脚本创建新的唯一资源，不复用已有数据库：

```sh
cd /Users/wusai/Documents/code/reverseDB
PATH=/opt/homebrew/opt/openjdk/bin:$PATH python3 scripts/tpcc_baseline.py
python3 scripts/verify_tpcc_baseline.py work/tpcc/runs/<新运行编号>
```

审核脚本要求五类事务均成功、日志事务计数与直方图一致、五项不变量完整且为零、探针通过及资源清理完成。另有离线负例测试，验证缺失事务类型、遗漏检查、残留资源、额外数据库错误和丢失 COMMIT 证据会被拒绝。

## 下一步的难点与判别实验

后续更新：已按用户要求完成[固定顺序回放](TPCC_REPLAY.md)，同一快照、逐事务种子与固定写入时间，两次执行一致。下述随机负载缺口仍适用于原 Worker 并发运行；串行对照已有稳定起点。

固定种子不足以重放原负载：该版本 TPCCWorker 另行使用未固定种子的 `new Random()`，并发调度及时间戳也会变化。当前日志有数据库 session/事务/参数，但没有完整的业务输入、输出及 terminal ID 对照记录。不能用“同一种子重跑”作为副本等价证据。

建议先从 NewOrder 建立显式输入的事务契约：同一数据库快照、保留的新输入、成功提交及业务中止，比较返回结果和相关状态差量。之后再对并发冲突比较允许结果及一致性约束，而不是逐条 SQL 或执行顺序。是否需要扩展成通用事务取证/恢复方法，应先用这个最小切片比较普通实现与 REA 可用能力，再讨论研究贡献。

另一个已确认的语义差异：真实 NewOrder 库存采用 TPC-C 的回补计算，不是旧手写场景的“最后一件库存只能一人买到”。后续验收以参考事务能力为准。REA 对 JDBC 场景的直接生成能力仍未验证；本轮没有生成 TPC-C 副本，也未做数据库优化。
