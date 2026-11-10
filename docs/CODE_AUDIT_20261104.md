# 当前原型代码审计

后续状态：三项发现已完成修复及回归，见[修复报告](CODE_AUDIT_FIXES_20261104.md)。以下保留审计当时的发现与证据，不代表当前仍未修复；原始归档不变。

2026-11-04。范围为 `prototype/aggregate_reattach` 的识别、绑定、生成、执行与验证机制，相关运行脚本及近期证据链；不覆盖仓库内全部历史 Java/REA 实验或上游应用。**确认 1 项 P1 正确性问题、2 项 P2 接口/健壮性问题。实现未修改，建议先修复 P1 再将此阶段标为审计通过。**

## P1：主键投影比较会漏掉影响最终输出的 extra 选择项

位置：[tag_query_binding.py:210](../prototype/aggregate_reattach/tag_query_binding.py#L210)。绑定器比较 `target.values("pk")` 与规范查询，而该投影会移除未选择的 extra 项，不能证明原 queryset 的最终输出语义一致。

可复现输入：

```python
target = Model.objects.order_by("pk").extra(
    select={"document_count": "999"},
)
```

Tag、CustomField 均被准入，路由为 candidate。原 helper 最终 `.values_list("pk", "document_count")` 取到额外选择项，计数为 999；候选返回实际计数。例如 Tag 原值为 `[999, 999, 999, 999, 999, 999]`，候选为 `[2, 5, 4, 4, 5, 1]`。两端都成功执行，数据固定、身份及返回整数对协议满足，因此不是故障行为或并发范围争议。

原契约明确未建模的 raw/extra 应被拒绝；当前检查没有落实这个边界。已有数据场景都从标准 `.order_by("pk")` 构造输入，无法暴露此漏洞。

修复方向：在投影前显式拒绝未准入的 extra/注解等查询结构，或保留足够的结构信息证明最终消费者表达式一致；不能只靠丢弃信息后的 SQL 相等。加入两个关系的回归用例，要求拒绝该输入并保留原结果。

## P2：执行前失效时绕过调用方传入的 fallback

位置：[callsite_binding.py:125](../prototype/aggregate_reattach/callsite_binding.py#L125)，以及 [tag_query_binding.py:276](../prototype/aggregate_reattach/tag_query_binding.py#L276)。外层部分拒绝分支调用参数 `fallback()`，但正常进入底层执行器后，底层重新定义了自己的原 helper 回退。

绑定后改变目标查询或消费者标识，两种关系均返回 `route: original`，但调用方 fallback 被调用的次数为 0。用可观测返回标记的回调即可复现；返回的仍是底层 helper 的整数对。实际调用方若需要自己的原始消费流程、结果处理或观测，失效路径会跳过它。当前 API 的回调文档将其定义为调用方原始区域操作，不能静默替换。

现有测试传入的 fallback 都与内部 `original_for` 等价，因此只比较结果不能发现回调被绕过。

修复方向：让底层在执行候选业务 SQL 前的拒绝分支使用同一个回调，或返回明确的“未执行”结果由外层统一回退；业务 SQL 已执行后的错误仍须直接传播，不允许事后重试。

## P2：无效审计配置未被归一为拒绝状态

位置：[tag_query_binding.py:201](../prototype/aggregate_reattach/tag_query_binding.py#L201)，以及同文件异常处理分支。配置读取直接执行 `json.loads`，异常处理只覆盖模式、准入和空结果异常。

候选创建后将 profile 的读取结果改为不完整 JSON `{`，两种关系的执行均抛 `JSONDecodeError`，fallback 调用次数为 0。实际配置损坏或非原子更新时，原业务 helper 仍可运行，但优化层会新增可用性故障。

这项属于配置失败的健壮性问题，不是对数据库故障等价的额外要求，也没有导致错误候选继续执行。现有 profile 变化测试只覆盖合法 JSON。

修复方向：对配置内容与必要字段作显式验证，将预期的配置读取/解析失败归为 `keep_original`，保留诊断原因。不要用宽泛异常捕获吞掉业务 SQL 错误。

## 复现、验证与限制

- 在冻结 Docker 环境中执行了 8 个缺陷复现用例：错误结果 2 个、回调绕过 4 个、配置异常 2 个。用例通过表示确认缺陷存在，不表示产品验收通过。
- 两组离线检查共 20 项通过。核对近期四组归档，317 个文件摘要全部一致；没有发现这些归档被修改。
- 未修改生产原型、现有测试或已有证据。只新增审计夹具、运行结果和本报告。隔离容器、网络及数据库卷已清理。
- 本轮没有重跑全部 110 项或任何性能测量；新发现来自针对性反例。此前通过的固定矩阵不能排除本次查询结构和回退接口漏洞。
- 固定读取状态、固定运行时绑定、成功执行及无可观察读取副作用仍是明确的调用方前提；本审计没有把范围外的并发透明性或完整 API 等价当作实现已承诺的功能。

证据：

- [复现数据与路由](../evidence/code-audit/20261104-b35ed0c85768/audit-findings.json)
- [复现测试](../evidence/code-audit/20261104-b35ed0c85768/inputs/test_audit_findings.py)、[日志](../evidence/code-audit/20261104-b35ed0c85768/probe.log)
- [审计摘要](../evidence/code-audit/20261104-b35ed0c85768/validation.json)、[输入文件摘要](../evidence/code-audit/20261104-b35ed0c85768/manifest.json)、[运行与清理](../evidence/code-audit/20261104-b35ed0c85768/run.json)
