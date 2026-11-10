# 合并实时目录读取

2026-11-04。本轮将每次绑定中三张表的列信息与 RLS 状态合并读取，保留完整约束、索引和上下文核验。**110 项运行测试、10 项离线检查通过，完整调用 SQL 从 27 条降至 17 条；本机测量未观察到明确的耗时收益。** 本轮结束，不继续追加调优。

## 实现与边界

[绑定器](../prototype/aggregate_reattach/tag_query_binding.py)将目标模型、Document 和关联模型的表名作为参数传入一次目录查询，同时读取列名、可空性、类型、关系 OID 与 RLS 标志。表名仍通过 regclass 按当前 search_path 解析，没有把候选文本拼成标识符 SQL。

每个表原有的 Django `get_constraints` 调用保持不变，完整约束与索引信息仍进入指纹；事务、连接、search_path 等上下文仍单独读取。每次绑定均访问当前数据库，不复用历史 schema，也不缩减参与核验的字段。业务 SQL 和结果不变。

| 每次完整调用 | 上一阶段版本 | 本轮版本 |
|---|---:|---:|
| 实时绑定次数 | 2 | 2 |
| 每次绑定的目录/上下文 SQL | 13 | 8 |
| 变换后的业务 SQL | 1 | 1 |
| SQL 总数 | 27 | 17 |

合并的是每次绑定的 6 条列/RLS 查询，变为 1 条；6 条约束/索引查询与 1 条上下文查询保留。减少往返次数不代表传输字节或 CPU 成本同比下降。

对照基线同时载入上一阶段归档的适配器和绑定器。基线适配器显式使用归档绑定器，避免旧、新路径误用同一个优化后读取器；原文件摘要经过核验。两版语法缓存均预热，不缓存数据库状态。

## 正确性与真实失效检查

110 项运行测试由 78 项原有回归、14 项执行失效/故障、8 项语法缓存边界、6 项实际 DDL 失效与 4 项成本测量组成。先前两个性能阶段只回归其正确性检查，本轮不重复旧成本测量。

对两个关系分别验证：

- 关联 FK 列改为可空：两版目录读取均拒绝，旧候选保留原路径。
- 目标表启用 RLS：两版均拒绝，旧候选保留原路径。
- 增加索引：两版完整目录事实一致，均反映新增索引；旧候选因指纹变化回退。

DDL 在隔离测试事务内执行，由测试结束回滚。初始及索引变化后的完整目录事实逐项对照一致，四个成本场景也核对目录事实、业务 SQL/参数和完整输出。这里没有测试并发 DDL，也没有增加其透明等价承诺；业务范围仍是固定数据、成功执行、已准入身份与主键排序整数对。

首轮 106 项通过、4 项 DDL 夹具检查失败，原因是插入夹具后仍存在延迟 FK trigger events，PostgreSQL 拒绝执行对应 DDL。测试在写入完成后增加 `SET CONSTRAINTS ALL IMMEDIATE`，先完成约束检查，再创建候选和修改 schema。目录读取实现未改，重跑相同验收得到 110 项通过。首轮日志、输入和测量原样保存在 `attempts/initial-deferred-trigger-failure`，未覆盖或删除失败状态。

绑定器源码版本已改变，原始历史元数据不能继续作为当前版本的匹配依据。离线提取入口新增 `--audit` 并默认使用本轮归档；10 项检查通过，同时验证旧版本元数据仍被拒绝。人工 RelationSpec 的声明内容未修改。

## 同轮固定测量

复用 Tag、CustomField 各自的 mixed/dense 四个场景。每场景对照上一阶段版本、本轮版本与原 helper；每方法预热 2 次，5 个时间区块各 5 轮，顺序种子 20261104，共 300 个无 trace 样本。计时包含新 QuerySet、候选创建、实时核验、数据库访问与完整结果返回；结果断言和 SQL trace 在计时外。

下表采用最终验收通过的同一次运行。单位 ms，完整调用中位数；负的降低比例表示变慢。

| 场景 | 上一阶段版本 | 本轮版本 | 耗时降低比例 | 原 helper |
|---|---:|---:|---:|---:|
| Tag mixed | 23.970 | 23.693 | 1.15% | 1.586 |
| Tag dense | 23.779 | 23.576 | 0.85% | 3.175 |
| CustomField mixed | 23.841 | 23.754 | 0.36% | 1.622 |
| CustomField dense | 25.280 | 25.516 | -0.93% | 3.831 |

新版更快的时间区块分别为 3/5、4/5、4/5、3/5。差异很小且方向不完全一致，不能认定有稳定时延改善。区块共享同一进程与热数据库，不是独立部署复验，也没有控制所有宿主负载。首次失败运行的测量保留为失败尝试的一部分，不择优合并样本。

本轮确认的是：可以保持目录事实与失效检查，减少 SQL 往返。单凭查询数无法推出收益，当前数据也不足以精确归因剩余时间。完整路径仍比原 helper 慢，不宣称总体查询优化成功；没有重跑全部常规强基线。

## 证据与复现

```sh
python3 scripts/tag_binding_audit.py --catalog-costs
python3 -m unittest discover -s tests -p test_callsite_extraction.py -v
python3 scripts/inspect_count_callsites.py
```

- [DDL 检查与固定测量](../prototype/aggregate_reattach/test_callsite_catalog_costs.py)
- [最终原始样本、查询与 DDL 证据](../evidence/callsite-catalog-costs/20261104-7861060cd7e1/tag-catalog-costs.json)
- [统计汇总](../evidence/callsite-catalog-costs/20261104-7861060cd7e1/summary.json)、[运行测试日志](../evidence/callsite-catalog-costs/20261104-7861060cd7e1/probe.log)
- [离线检查日志](../evidence/callsite-catalog-costs/20261104-7861060cd7e1/offline-tests.log)、[离线对照](../evidence/callsite-catalog-costs/20261104-7861060cd7e1/offline-candidates.json)
- [运行与清理记录](../evidence/callsite-catalog-costs/20261104-7861060cd7e1/run.json)、[来源与输入摘要](../evidence/callsite-catalog-costs/20261104-7861060cd7e1/manifest.json)

两次隔离运行的容器、网络与数据库卷均已清理。上游源码、已有证据及业务 SQL 生成器未修改。本次按固定范围结束。
