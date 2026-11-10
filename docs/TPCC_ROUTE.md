# TPC-C 路线纠正：按业务事务选择工具

2026-11-03，按用户要求重新核查。**TPC-C 不要求 GraphQL，也不要求 REST；Saleor 的接口限制不是本项目的必要前置问题。** 前一轮“继续 Saleor 补 GraphQL，还是换 REST 应用”的选择已撤回，不再等待该选择。

## 核查依据

- TPC-C 规范定义业务事务、数据、终端模拟、ACID 及测量要求。第 6.4 节描述模拟终端发送事务请求，第 6.5 节约束通信接口，没有要求 GraphQL/REST；附录 A 给出嵌入式 SQL 示例。正式合规测试仍需满足完整规范，不能因使用直接数据库连接就宣称合规。[TPC-C Revision 5.11](https://www.tpc.org/TPC_Documents_Current_Versions/pdf/tpc-c_v5.11.0.pdf)
- TPC 官方概览描述每个终端可使用五类事务。因此，“多用户”可以首先建模为多个独立终端/会话；先前模拟器中的财务、仓库、审计员及授权撤销是我们自定的扩展，不是这套终端模型的必需前置。[TPC 官方概览](https://tpc.org/tpcc/detail5.asp)
- BenchBase 提供 TPC-C 工作负载；其 PostgreSQL 配置直接指定 JDBC 驱动、数据库地址、终端数和事务混合，NewOrder 实现使用 JDBC 连接和 SQL。这给出了不经过 Web API 的现成实现路径。[官方 README](https://github.com/cmu-db/benchbase)、[PostgreSQL 配置](https://github.com/cmu-db/benchbase/blob/main/config/postgres/sample_tpcc_config.xml)、[NewOrder 源码](https://github.com/cmu-db/benchbase/blob/main/src/main/java/com/oltpbenchmark/benchmarks/tpcc/procedures/NewOrder.java)

## 当前执行方向

先复现业务能力，后优化数据库，顺序不变；不再以 HTTP/HAR 或某个框架的输入格式限定实验。

1. 优先复用现成 TPC-C 事务实现及数据装载器，当前核查候选为 BenchBase + PostgreSQL；正式引入前固定版本并检查运行环境。用户要的是类似 TPC-C 的业务场景，不以完成官方认证为本轮目标。
2. 从隔离数据库、少量终端和受控事务序列开始。小型功能测试与标准规模、持续吞吐测试分开标注。
3. 记录业务输入、terminal/session ID、事务边界、提交/回滚、输出及数据库状态变化。首先验证五类事务的功能和多会话一致性，不先加角色授权、Web 服务或协议适配。
4. 把参考实现及这些观测作为复现验收依据：新输入是否能执行、事务效果是否正确、失败是否回滚。并发比较应检查允许的结果及一致性约束，不强求调度顺序和逐步输出完全相同。
5. REA 只在适合它的分析环节使用；现有 HAR 环境生成器不适配 SQL/JDBC 工作负载，不据此改造业务去迎合它。REA 对该目标的具体可用能力和收益需另行验证，不能承诺自动导入 SQL trace 或自动重建事务。

可执行的基本结构是：**模拟终端 → 事务代码 → PostgreSQL**。观测与复现验证围绕这条业务执行链展开。后续已固定 BenchBase 版本并完成隔离 PostgreSQL 真实功能运行：4 个终端覆盖五类事务，受控提交/回滚及一致性检查通过。详见[功能基线报告](TPCC_BASELINE.md)。下一步建立显式业务输入的复现对照，尚未报告 REA 副本成功。

## 现有产物如何处理

- Paperless/REA CRUD 的真实运行证据保留，作为已验证的独立案例。
- 手写订单模拟器保留为自定义场景草案；不作为 TPC-C 参考实现，也不以其自测代替真实数据库实验。
- GraphQL 合成实验保留为工具局限记录；适配开发停止，不再视为当前主线的门槛或研究创新来源。
- 原来以权限恢复为主的扩展暂不作为起步任务，后续是否加入由业务要求决定。
- 本地先运行小型验证；负载确实过大时再使用已获用户许可的远程服务器。资源选择按 CPU、内存、I/O 和实际模型需求决定。
