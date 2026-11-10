# 真实源码的构造选项—策略方法依赖提取

2026-11-04。已把 Paperless 命名策略的人工绑定分析变为受限自动提取：**从原始类源码识别构造选项、绑定属性、目标方法、继承/覆盖和入口调用，保留源码行证据。** 三种导出策略、四种选项上下文完成静态提取；20 项边界检查通过，与四条已冻结 Paperless 契约标签一致。

这是已知案例的源码一致性检查，不是新的预测准确率或泛化验证。旧标签、应用源码、模型和参数均未改变；没有新增应用执行、数据库运行、性能测试或删除授权。

实现：[strategy_bindings.py](../prototype/module_pruning/strategy_bindings.py)；固定范围：[strategy_binding_protocol.json](../prototype/module_pruning/strategy_binding_protocol.json)；入口：[extract_strategy_bindings.py](../scripts/extract_strategy_bindings.py)。

## 自动提取的关系

输入只包括原始源码、声明的具体类/入口，以及构造参数允许值。分析器不读取标签、原版/删除版结果或上一轮的可删论证，也不把程序改写为 `execute(x, flag, removed)` 夹具。

在此源码中自动识别到：

```text
follow_formatting
    true  → self.make_unique_filename = self._formatted_filepath
    false → self.make_unique_filename = self._filename_only

具体策略类的 add_document
    → self.make_unique_filename(...)
    → 当前构造上下文对应的具体方法定义
```

识别规则不写死这些字段和方法名。构造函数中“布尔形参 → 两侧向相同实例属性绑定方法”的 AST 结构提供参数、属性和目标；名称替换检查确认可识别另一套命名。具体入口类由契约提供，继承链与目标实现由源码解析。

图按“消费者 → 提供者”记录四类边：构造入口、构造期间的方法查找、业务入口调用、绑定属性调用/直接 self 方法调用。**构造查找与方法体调用分开记录**：被选择的方法即使尚未执行，也必须存在才能完成绑定。上一轮空文档列表的 `AttributeError` 反例正对应这一点。

## 上下文与默认值

| 构造参数上下文 | 可能被绑定的方法 | 构造查找边 / 绑定调用边 |
|---|---|---:|
| 明确固定 false | `_filename_only` | 3 / 5 |
| 明确固定 true | `_formatted_filepath` | 3 / 5 |
| 明确允许 false、true | 两者 | 6 / 10 |
| 调用方选项未知 | 两者 | 6 / 10 |

这里的边数是静态调用位置及其上下文，不是实际调用频次。文档是否有存档等内部条件尚未求解，方法体按结构上的可能调用追踪。

构造参数默认 false 会作为事实输出，但**不能代替未知调用方参数**。只有契约显式固定选项时才收窄目标；未知选项与显式允许两种布尔值的可达集合一致。

“入口”在本轮指三个策略类的构造与 `add_document`。没有自动追踪 HTTP 请求经过 serializer、视图和 ORM 的完整路径，也没有推断当前公开接口只能传 false。根集合和能力配置仍是明确的契约输入。

## 继承、其他消费者和失效条件

局部单继承查找会解析到实际定义；子类覆盖某个目标方法后，记录的是子类实现，而非继续把基类同名方法误判为被使用。新增直接消费者或额外声明入口会扩充可达依赖，不能只看构造绑定后就认定未选方法无人使用。

以下情况返回 unknown，不产生“未使用”结论：

- 构造选择变量重赋值、绑定属性在选择分支之外被改写、目标方法被实例属性遮蔽。
- receiver 被重赋值或逃逸、绑定方法通过别名逃逸、反射或动态属性字典访问。
- 描述符/装饰器、多继承、外部祖先类、继承环、额外动态模块初始化、有副作用的默认参数。
- 超出已支持形式的构造代码、嵌套可调用作用域、未解析的方法或参数事实。

外部祖先即使不参与当前方法名查找，也可能提供属性钩子或元类行为，因此检查完整祖先链，不能只检查被调用的方法链。当前语法范围较窄，拒绝可能过于保守；没有把它包装成通用 Python 调度分析器。

所有受支持结果也保留边界假设：精确本地类、指定入口、普通参数接口，无外部反射、动态替换、隐藏 receiver 回引用或并发修改。这些假设不是由静态工具证明的系统事实。

## 与四条既有标签核对

先封存 false/true/双值/未知四种上下文的自动事实，再读取旧标签并通过源码区域哈希关联目标定义。没有用标签决定方法名、绑定方向或可达性。

| 既有契约 | 自动关系 | 既有边界标签 |
|---|---|---:|
| formatted 方法、固定 false | 在声明入口和假设内未关联到该方法 | 条件可删 0 |
| formatted 方法、固定 true | 构造查找及绑定调用均关联到该方法 | 必要 1 |
| plain 方法、固定 true | 在声明入口和假设内未关联到该方法 | 条件可删 0 |
| plain 方法、固定 false | 构造查找及绑定调用均关联到该方法 | 必要 1 |

4/4 一致只说明本轮自动事实复现了已知绑定关系。结构上可能可达不等于执行频率或概率必要性；局部未关联也不等于全系统无消费者。Saleor 的另外四条记录本轮没有新增分析。

## 仍未闭合的部分

`ZipFile.write`、`namelist`、`doc.get_public_filename` 和路径构造等调用保留为外部效果未知。固定选项上下文各记录 11 个外部调用位置/根上下文；允许两种选项时为 17 个，不能把这些数量当运行负载。

每份输出均为 `effect_summary_complete=false`、`deletion_authorized=false`。四条关联记录保持 `probability=null`、`score_status=abstain`、`training_admitted=false`，完整应用标签仍未知。新增的是源程序依赖图结构，没有强行映射为旧有限夹具的十一项布尔特征。

这一步的进展是建立“源码 → 契约上下文 → 模块依赖 → 已有标签”的可追溯连接。它没有产生新的加速结果或把条件标签转为实际删除许可。

## 检查与归档

20 项检查覆盖：布尔域收窄、未知选项不套默认值、双值域、空业务入口仍有构造依赖、新直接消费者、子类覆盖、额外入口、选择变量重赋值、绑定属性改写、方法遮蔽、反射/receiver 别名、方法逃逸、多继承/装饰器、外部祖先、继承环、receiver 重赋值、动态模块初始化/默认参数、名称替换、标签/非布尔事实注入、外部效果保持未知。

初版 17 项检查归档 [aeab7ecd4fb7](../evidence/module-strategy-bindings/20261104-aeab7ecd4fb7/report.json) 保留。代码复核后补上外部祖先、继承环和 receiver 重赋值三项边界处理及检查，形成最终归档；两次静态提取不增加样本数，也不重算标签或概率。

- [最终报告](../evidence/module-strategy-bindings/20261104-74f5129ab98c/report.json)
- [自动绑定、调用图及外部未知](../evidence/module-strategy-bindings/20261104-74f5129ab98c/sealed_binding_facts.json)
- [逐契约源码关联](../evidence/module-strategy-bindings/20261104-74f5129ab98c/label_consistency.json)
- [检查日志](../evidence/module-strategy-bindings/20261104-74f5129ab98c/tests.log)、[输入冻结](../evidence/module-strategy-bindings/20261104-74f5129ab98c/freeze.json)、[文件哈希](../evidence/module-strategy-bindings/20261104-74f5129ab98c/manifest.json)

本轮到此停止。下一步可以限定为 Saleor 的“守卫字段 → 对象更新 → 待写入列表”关系，用另一个来源检验同类依赖表示的适用边界；数据库提交效果仍应单独保留为未知，不直接恢复概率拟合。
