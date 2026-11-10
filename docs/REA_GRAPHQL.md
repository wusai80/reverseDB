# REA 下一步：GraphQL 接入前置实验

**状态更新：此路线已撤回。** 用户指出 TPC-C 不需要 GraphQL；官方规范及现成 JDBC 实现核查支持这一判断。以下保留历史实验和当时方案，不再作为当前主线的前置条件，也不再等待 Saleor/REST 二选一。[当前路线](TPCC_ROUTE.md)。

2026-11-03。用户要求开始多人订单场景的 REA 下一步。先检查现有 Saleor 候选目标与 REA 的协议兼容性，再选择接入方式。

## 结论与证据范围

**REA 6.8.0 当前的 HAR → environment-build 路线没有在本实验中分离 GraphQL 操作。**
13 条合成记录包含列表、详情、修改三种操作，全部发送到同一 `POST /graphql/` 地址。
REA 将其归为一个 `post_graphql`，报告 0 个读取工具、0 个可用写工具；写操作 withheld 原因为 `no-state-target`。
实际启动生成的本地 MCP 服务，`tools/list` 返回空列表，随后服务已停止。

这是一个明确标注的**合成协议实验**：请求/响应正文、时间及业务数据均为人工构造，不是 Saleor 抓包，没有执行或校验 Saleor schema。它验证当前工具分组和覆盖结果，不能代替 Saleor 的真实业务测试，也不能证明 REA 所有功能均不支持 GraphQL。

首次输入遗漏 HAR 必需的 `redirectURL` 字段，解析器拒绝。已修正夹具并保留失败记录；上述结论来自修正后解析成功的独立结果，不把格式错误作为协议能力不足的证据。

## 为什么这会影响订单场景

- 锁定 Saleor 源码的 `saleor/urls.py` 将 `graphql/` 路由接入 `GraphQLView`；上游订单测试通过 GraphQL 请求体传入具体操作和变量。
- 当前 REA `EnvironmentManifestAccumulator` 使用 method、origin、path template、template rules 构造分组键，不包含请求体里的 GraphQL 操作。它把 POST 放在写方法集合中；即使请求体是 query，本轮也没有生成读取工具。
- 因此，单纯补更多同地址 POST 的读回记录，并不能保证把列表、详情和修改识别为不同能力。这里没有继续盲目重采同一种输入。
- GraphQL 操作还可能在 HTTP 200 中返回错误。夹具包含一个这种响应，但本轮没有可用工具可调用，故**尚未验证 REA 对此类失败的运行时处理**；不能宣称已经发现错误执行或越权。

本问题属于输入协议及操作身份表达，不是本机 CPU/GPU 不足。此次没有安装 Saleor 运行依赖、启动 Saleor 或连接远程服务器。

## 建议方案与边界

建议继续使用现有 Saleor，在 REA 之前建立显式 GraphQL 适配。已向用户提出此路线与“改用 REST 订单应用”的选择；适配层尚未实现。

第一步只做可核验的操作索引与证据映射：

1. 用 GraphQL 解析器识别实际选中的 operation、query/mutation、字段、变量及 alias；不能仅按可随意命名的 `operationName` 或正则匹配。
2. 多操作文档必须按 operationName 选择；缺少选择而有歧义、persisted query 无正文、batch、subscription 等未支持形式明确拒绝或列为未知，不能悄悄选第一项。
3. 每条记录保留原始 HAR 哈希和序号，派生视图另存，不改写原抓包方法或 URL 来伪装成 REST。
4. 调用者身份由受控采集会话的外部映射提供，不能从请求体的 owner/userId 推断身份，也不把认证 token 放入分析产物。
5. 分开保存 HTTP 状态、GraphQL 顶层 errors、已知业务 payload 的 errors 和数据字段；不把 HTTP 200 自动视为业务成功。

第二步才接入真实应用，先验证订单列表、详情及一种小型写操作：

- 使用真实会话、合成数据和实际返回的对象 ID；写后立即由相同用户读取详情与列表，再由其他用户读取。
- 操作索引、实体投影和工具执行需要分别验收。操作分离成功不代表 REA 已恢复跨实体事务、授权或副作用。
- 若需要给 REA 提供派生 REST 视图或实时转接服务，必须明确标注适配来源，并逐条保留请求/响应映射；原始 GraphQL 始终保留作核验依据。
- 再扩展建单、手动记账和履约。Saleor 的 `orderMarkAsPaid` 表示手动标记已付，不等同于实际支付网关收款；它与 `orderFulfill` 都使用 `MANAGE_ORDERS`，不能把模拟器中的财务/仓库职责分离当成已存在的应用权限。

代价是增加解析、错误分类和实体映射。改用 REST 应用可减少协议适配，但需要另选、固定和搭建真实订单应用，多用户授权与跨实体状态问题仍需验证。两者都不要求现在开展数据库优化。

这是一项具体工程障碍及候选方案，不是已确认的新颖性贡献。后续只有形成可复用的操作/身份/因果恢复方法并经过对照，才讨论研究贡献。

## 可复查产物

- [证据汇总](../evidence/rea-graphql/20261103/summary.json)：输入性质、构建覆盖、实际工具目录、生命周期、版本及限制。
- [合成夹具生成器](../prototype/rea_graphql/build_fixture.py)：生成三个操作及读回记录。
- [原始构建结果](../evidence/rea-graphql/20261103/protocol-check/build.json)与[实际 MCP 目录](../evidence/rea-graphql/20261103/protocol-check/live-catalog.json)。

生成器仅需 Python 标准库。使用已经安装的 REA 6.8.0 和兼容 Node：

```sh
python3 prototype/rea_graphql/build_fixture.py --output /tmp/new-graphql-fixture.har
rea environment-build /tmp/new-graphql-fixture.har /tmp/new-graphql-bundle \
  --name graphql-protocol-check --format json
```

输出文件/目录需尚不存在。预期“构建成功但可用工具为 0”；不能仅检查退出码判断复现成功。诊断 bundle 不作为用户可用的订单副本交付。
