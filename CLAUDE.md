# AI Monitor — 项目约定

## 代码规范

### 注释：默认不写，只在三种情况下写

1. **默认不写注释**
2. 仅在这三种情况写：**非显而易见的边界处理**、**绕过某个 bug 的 hack**、**有性能权衡的地方**
3. 注释只写 **WHY**，不写 WHAT
4. 禁止：参数说明式的 docstring、逐行解释、TODO 占位、分隔线注释
5. 变量和函数名要能自解释 —— 靠命名表达意图，不靠注释
6. **例外：每个 `.py` 文件开头用一句模块 docstring 说明这个文件是干什么的**，一句话就够

示例（这条注释合规，因为它解释的是 hack 而非代码在做什么）：

```python
def _warn(message: str) -> None:
    # 中文 Windows 控制台是 GBK，emoji 会让 print() 抛 UnicodeEncodeError，
    # 被 pydantic 包成 ValidationError —— 一句本意是提醒的警告能把应用搞得启动不了
    try:
        print(f"\n[警告] {message}\n")
    except UnicodeEncodeError:
        print("\n[WARNING] JWT_SECRET_KEY is weak or still a placeholder.\n")
```

文件头那一句长这样，只写「是什么」，不写设计理由：

```python
"""用户隔离的依赖链：从 JWT 取当前用户，再校验项目与 API Key 的归属。"""

import jwt
from fastapi import Depends, HTTPException, status
```

设计原因、踩过的坑、选型对比一律写进 `docs/design/`，不写进代码。
Code Review 时会检查：注释数量是否超标、文件头有没有那一句话。

------

## 项目上下文

| 内容 | 位置 |
|---|---|
| 需求总纲 | `docs/轻量级大模型使用监控与智能分析平台.md` |
| 模块需求（含验收清单） | `docs/requirements/` |
| 后端代码 | `ai-monitor-backend/` |

**技术栈**：Python 3.11+ / FastAPI / Pydantic / SQLAlchemy / PostgreSQL / Redis / Vue 3 / LangChain / Docker

------

## 工作模式

- 用户是**编程学习者**，通过这个项目学习工程化开发。
  - 代码可以由 AI 代写，但每段代码要**解释清楚逻辑**，让用户看懂
  - 代码**不要写得过于精简或炫技**，优先可读性；能用直白写法就不用高级技巧
  - 关键步骤、非直观的写法要加注释说明，注释服务于"让入门者看懂"
  - 需要什么学什么（项目驱动），不过度展开理论
  - 技术选型有不同意见时直接指出，但最终由用户决定
  - 不引入过度设计（V1 是模块化单体，不上微服务 / K8s / Kafka）

## 五条不可违反的设计约束

1. **项目隔离** —— 所有监控数据通过 `project_id` 归属，查询必须带过滤
2. **用户隔离** —— 硬边界是 `user_id`，通过 `Project.owner_id` 强制校验
3. **AI 不猜数据** —— 所有数字必须来自数据库查询工具，包括项目 ID
4. **AI 不越权** —— 工具参数的权限校验写在**代码**里，不写在提示词里
5. **上报不阻塞业务** —— 监控平台不可用时，接入方的 AI 项目必须照常运行
