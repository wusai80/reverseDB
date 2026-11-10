# 三项代码审计问题的修复

2026-11-04。[审计报告](CODE_AUDIT_20261104.md)中的 1 项 P1、2 项 P2 已修复。**154 项运行测试、20 项离线检查通过。** 本轮只修复已确认问题及必要的输入边界，不进行性能测量或扩展应用范围。

## 修复内容

| 审计问题 | 修改后的行为 |
|---|---|
| 主键投影隐藏 extra 选择项，错误准入并返回不同计数 | 在 `values("pk")` 前检查原始查询，拒绝 extra、注解、别名/filtered relations 和非标准投影；保留原查询执行路径 |
| 执行前失效绕过调用方 fallback | 底层执行器接受可选的零参数 fallback，外层原样传递；缺少/已用候选及实时核验拒绝均使用该回调 |
| profile JSON 损坏直接抛异常 | 独立读取与验证 profile，将文件读取、编码、JSON 解析及必要字段格式问题转为 `rejected / keep_original`，保留诊断原因 |

实现位于 [tag_query_binding.py](../prototype/aggregate_reattach/tag_query_binding.py) 和 [callsite_binding.py](../prototype/aggregate_reattach/callsite_binding.py)。

P1 的原反例是 `extra(select={"document_count": ...})`：原 helper 返回 999，候选曾返回实际计数。现在在候选创建时以及绑定后输入变化时都拒绝该形状，回退后仍返回原实现的 999。拒绝规则刻意保持保守；没有宣称所有注解或别名都不能等价改写。

P2 回调采用零参数约定。未传回调的底层调用者继续使用原有 helper；现有接口调用方式仍兼容。传入回调时，返回值不经内部 helper 替换，回调异常直接传播且不重试。候选业务 SQL 开始执行后的错误也继续传播，不通过宽泛捕获改为回退。

profile 检查覆盖对象形状、运行时版本类型、源码/模型 SHA-256 以及转换文件路径/摘要映射的格式。捕获范围限定于 profile 读取和解析，未包围数据库执行。这不是对所有文件系统、数据库或并发故障的等价承诺，也没有新增证据缓存。

## 验证

[新增正式回归](../prototype/aggregate_reattach/test_audit_fixes.py)共 48 项：

- 16 项查询形状检查：两个关系 × 创建/执行阶段 × extra、注解、别名和投影，均回退并保留完整原结果。
- 6 项回调检查：两个关系在绑定后改变查询、消费者或声明，必须执行调用方回调一次，并返回回调的原值。
- 24 项 profile 检查：两个关系 × 创建/执行阶段 × 非法 JSON、缺失字段、错误类型、错误转换文件配置形状、文件缺失和无效编码。创建阶段同时检查直接绑定器入口，执行阶段检查拒绝原因与回调调用。
- 2 项回调自身异常检查：直接传播，不重复调用。

同时运行既有 106 项正确性与失效检查：78 项原接入/关系变换测试、14 项执行失效/业务 SQL 故障、8 项语法缓存边界及 6 项实际 DDL 失效。合计 154 项全部通过；所有历史性能模块的成本记录均为空，本轮未重跑计时。

离线提取与变换契约各 10 项通过。由于绑定器源码版本改变，离线提取入口默认审计路径已指向本次匹配的归档，旧版本证据仍会被拒绝。两项配置与新归档元数据一致。历史审计反例与此前归档保持不变。

## 复现与证据

```sh
python3 scripts/tag_binding_audit.py --audit-fixes
python3 -m unittest discover -s tests -p test_callsite_extraction.py -v
python3 -m unittest discover -s tests -p '*contract*.py' -v
python3 scripts/inspect_count_callsites.py
```

- [48 项修复回归结果](../evidence/code-audit-fixes/20261104-13435f1b4859/tag-audit-fixes.json)
- [154 项运行测试日志](../evidence/code-audit-fixes/20261104-13435f1b4859/probe.log)
- [离线提取检查](../evidence/code-audit-fixes/20261104-13435f1b4859/offline-extraction.log)、[变换契约检查](../evidence/code-audit-fixes/20261104-13435f1b4859/offline-contract.log)
- [运行与清理记录](../evidence/code-audit-fixes/20261104-13435f1b4859/run.json)、[版本和输入摘要](../evidence/code-audit-fixes/20261104-13435f1b4859/manifest.json)

隔离容器、网络和数据库卷已清理，`cleanup_errors=[]`。本次关闭的是三项已确认审计发现，不把回归通过扩展为任意 QuerySet、完整 API 或全系统无缺陷保证。
