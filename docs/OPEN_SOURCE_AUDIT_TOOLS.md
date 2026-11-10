# 软件分析与审计 agent / skill 调研

调研日期：2026-11-05。读取官方仓库与技能说明；本轮没有安装第三方插件、运行其审计流程或上传项目代码。以下适用性判断是针对 ReverseDB 的评估，不是项目作者对 Java / PostgreSQL 等价验证的承诺。

## 选择建议

优先借鉴 Trail of Bits 的源码理解、差分审查和属性测试技能，配合我们已有的冻结源码、生成物、完整状态差分与事务回滚证据。先验证一个具体契约能否被独立恢复和质疑，再决定是否引入更大的 agent 框架。

| 项目 | 官方提供的能力 | 对本项目的用途与限制 | 优先级 |
| --- | --- | --- | --- |
| [Trail of Bits skills](https://github.com/trailofbits/skills) | 面向安全分析、测试和开发的技能集合 | 最接近我们需要的审计工作方法；需补充 Java/JDBC/SQL 语义义务，不能直接当等价证明器 | 首选 |
| [PR-Agent](https://github.com/The-PR-Agent/pr-agent) | 社区维护的 AI 代码审查 agent，主要围绕 PR 工作 | 可用于后续变更回归审查；对冻结二进制、反编译产物及跨层契约的独立恢复，需要另外设计任务 | 次选 |
| [Joern](https://github.com/joernio/joern) | 源码、字节码、二进制分析与代码属性图查询 | 可作为 agent 的调用／数据／控制关系取证后端；它本身不是审计 agent，也不会自动补齐数据库语义 | 后续按需 |
| [Semgrep MCP](https://github.com/semgrep/semgrep/tree/develop/cli/src/semgrep/mcp) | 将扫描能力提供给 agent 的工具接口 | 适合具体模式和规则检查；匹配规则不能代替跨层行为等价验证 | 后续按需 |

Semgrep 旧独立 MCP 仓库已声明弃用，更新已转入主仓库和官方二进制；接入时应使用当前入口，不照搬旧仓库里的安装命令。[官方迁移说明](https://github.com/semgrep/mcp)

Joern 当前 README 指定 JDK 21，其他版本未充分测试；本轮仅确认能力说明，没有验证本机兼容性。[官方要求](https://github.com/joernio/joern#requirements)

## 最相关的技能

- **audit-context-building**：先重建函数的假设、保证、依赖和跨函数关系，要求结论有源码依据；它的目标是理解代码，不直接输出漏洞结论。适合我们的“先看原程序，再看候选”第一阶段。[技能说明](https://github.com/trailofbits/skills/blob/main/plugins/audit-context-building/skills/audit-context-building/SKILL.md)
- **differential-review**：审查变更及其影响范围，结合调用者、测试和可触发情景提出问题。适合第二阶段对照原版与生成物；本仓库目前大量文件未纳入 Git，应使用冻结产物对照，不能假装拥有完整 Git 演进证据。[技能说明](https://github.com/trailofbits/skills/blob/main/plugins/differential-review/skills/differential-review/SKILL.md)
- **property-based-testing**：从不变量和参考实现构造性质，生成并缩小反例；列出了 Java 的 jqwik 等工具。适合数值、重复商品、文本和事务边界的有界验证。固定种子及预算，避免无限扩展测试。[技能说明](https://github.com/trailofbits/skills/blob/main/plugins/property-based-testing/skills/property-based-testing/SKILL.md)
- **fp-check**：针对已经提出的安全漏洞复核完整路径和证据，抑制误报；原技能明确不用于一般非安全任务。可以借鉴“先证明触发条件，再报告”的方法，但不能直接宣称该技能支持我们的业务等价审计。[技能说明](https://github.com/trailofbits/skills/blob/main/plugins/fp-check/skills/fp-check/SKILL.md)

仓库当前许可分别为 Trail of Bits 技能内容的 [CC BY-SA 4.0](https://github.com/trailofbits/skills/blob/main/LICENSE)、Joern 的 [Apache 2.0](https://github.com/joernio/joern/blob/master/LICENSE)、PR-Agent 的 [MIT](https://github.com/The-PR-Agent/pr-agent/blob/main/LICENSE)。这只是本次读取的仓库许可记录；采用时应固定版本。PR-Agent 的历史移交文章与当前 LICENSE 不同，本记录以当前 LICENSE 为准。

## 与研究主线的衔接

建议让 agent 输出可检查的义务条目：输入和状态域、原版源码位置、候选位置、控制／数据依赖、数值与编码规则、事务边界、证据、反例和未决项。每一项结论区分“具体不等价”“已落实”“证据不足”“不在约定范围”。

agent 可以减少人工阅读和契约整理，但多个 agent 的一致意见也不是证明。最终仍由确定性检查或可复现的反例约束结论；未知能力保留原路径，不能依据测试未覆盖而删除数据或功能。

本轮独立审计使用新上下文 agent 和项目自定的两阶段协议，没有安装或调用上述第三方审计技能。其结果应与本调研分开归因。

检索过程使用 agent-reach 的路由说明；本机 doctor 因配置目录权限失败，GitHub CLI 网络和 Exa 配置不可用，最终通过内置网页检索读取官方资料。更新检查因 DNS 失败未完成，没有修改全局配置。
