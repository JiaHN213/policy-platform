# Agent 第一阶段契约

状态：P0 设计冻结材料；尚未接入业务运行时或系统配置页面。

`v1/` 保存工具参数、证据和预算 Schema、权限元数据及全关闭默认配置。
不能仅把 enabled 改为 true 就启动 Agent；P1 需要实现鉴权、调度、持久化和工具网关。

工具参数由模型提供；身份、组织、角色、许可材料、版本、租约和预算由服务端注入。
Schema 能检查结构，不能代替权限、证据真实性或语义校验。

验证契约与离线基线（在工程根目录运行）：

```powershell
.venv\Scripts\python.exe scripts/agent_stage0/baseline.py --split all --output output/agent-stage0/baseline.json
```

脚本使用独立内存数据库和合成材料，禁止使用生产数据库，不调用模型或网络，不创建正式研究任务。
结果分别记录当前代码输出、升级目标和暂不具备的能力，不把未实现用例算作通过。

样本：`tests/fixtures/agent_stage0/cases.json`。开发集用于调试；保留集可运行旧流程建立基线，后续不要用它调整提示词。
这些是工程师编写的合成参考答案，不是客户标注集；上线前需另行收集经过授权的真实材料进行盲评。

内部详细说明和运行快照位于被 Git 忽略的 `docs/Agent第一阶段_基线与契约.md`、`output/agent-stage0/`。
未授权上传内部文档、生产快照或私有数据。
