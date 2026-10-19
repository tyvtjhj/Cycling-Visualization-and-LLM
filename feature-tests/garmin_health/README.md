# 佳明健康看板 · 第三方参考源码（本地评估用）

> 来源：https://github.com/Jonathan-614/astrbot_plugin_garmin_health
> 固定版本：commit `9f2655cf5f6378651f6e25f3a517fb8eb61f90aa`（master，v1.0.3，2026-09-17）
> 获取日期：2026-10-19
> 许可证：仓库**没有 LICENSE 文件**，按默认版权保留处理

## 一、边界声明（先看）

1. 本目录只用于**开发期阅读、功能评估与对拍参考**，不属于 PROJECT_CONTEXT 6.6 的交付系统功能；按 3.1 范围排除，交付系统仍只走「平台导出 FIT 文件导入」。
2. 代码版权归原作者，**不随本仓库分发**：`vendor/` 已由同目录 `.gitignore` 排除，不会提交到 Gitee/GitHub。
3. 若将来要在本项目复用其代码，必须先联系原作者取得授权，或按本项目规范独立实现；不得直接复制进 `backend/`。
4. 不要把真实 Garmin 账号密码写进任何文件；评估登录行为时用环境变量并在结束后撤销会话。

## 二、目录结构

```
feature-tests/garmin_health/
├─ README.md           本文件（入库）
├─ .gitignore          排除 /vendor/（入库）
├─ fetch_source.py     按固定 commit 重新下载源码到 vendor/raw（入库，仅标准库）
├─ inventory.md        核心功能清单与拆分归类表（入库）
├─ standalone/         独立运行版：garmin_core.py + garmin_cli.py（无 AstrBot 依赖，入库）
└─ vendor/             第三方源码，本地生成、不入库
   ├─ raw/             按 commit 下载的完整原始文件 + COMMIT.txt
   ├─ MANIFEST.sha256  原始文件 sha256 与字节数
   ├─ modules/         按核心功能拆分归类的源码块（带来源行号注释）
   │  ├─ auth/         登录、tokenstore、会话、限流重试
   │  ├─ health/       今日概览、心率、睡眠、步数
   │  ├─ reports/      综合健康报告与 AI/规则建议
   │  ├─ activities/   活动查询、筛选、格式化
   │  ├─ volume/       运动量统计与活动类型映射
   │  ├─ personal_best/ 个人最佳
   │  ├─ yearly/       年度报告
   │  ├─ rename/       活动改名
   │  ├─ tools/        11 个 LLM FunctionTool
   │  ├─ commands/     命令路由与子类型别名
   │  ├─ utils/        配速、日期、数值工具函数
   │  └─ config/       插件配置与元数据副本
   └─ inventory.md     agent 生成的原始分析底稿
```

## 三、重新获取方法

```bash
cd <repo-root>
python feature-tests/garmin_health/fetch_source.py
```

脚本只用 Python 标准库，按固定 commit 下载 tarball，只提取核心文件到 `vendor/raw/`，并重建 `vendor/MANIFEST.sha256`。`vendor/modules/` 的拆分由人工/Agent 一次性生成，不随脚本重建。

## 四、核心功能与拆分归类

| 分类 | 主要源文件 | 核心内容 |
|---|---|---|
| auth | client_manager.py | `Garmin(email, password, is_cn)` 登录、tokenstore、会话复用、过期重登、串行化 |
| health | services.py | 今日概览、近 N 天心率/睡眠/步数 |
| reports | services.py | 综合健康报告 + AI/规则建议 |
| activities | services.py | 活动列表、分页、按类型/日期/关键词筛选、单条格式化 |
| volume | services.py | 跑量/骑行等运动量聚合、17 类骑行细分、TYPEKEY 映射 |
| personal_best | services.py | 最长距离、最快配速、最大爬升、最长时长 |
| yearly | services.py | 年度报告与月度分布 |
| rename | services.py | 活动改名（写操作） |
| tools | tools.py | 11 个 LLM FunctionTool 的参数 schema 与分发 |
| commands | main.py | `/健康` `/心率` 等命令路由、参数解析、SUBTYPE_ALIAS |
| utils | utils.py | 配速换算、日期规范化、参数解析、数值安全转换 |
| config | _conf_schema.json 等 | 账号、中国区开关、AI Provider、活动上限 |

更细的符号/行号清单见 [inventory.md](inventory.md)。

## 五、依赖与风险

- 依赖：`garminconnect>=0.3.4`（非官方 Garmin Connect Web 客户端）、`astrbot` 插件运行时；本项目 backend 不安装这些依赖。
- 风险：账号密码需交给插件配置；MFA 首次登录没有输入入口；非官方接口可能被限流/风控/改版；Token 文件落盘；活动列表大量分页请求。
- 结论：只适合做**本地评估与对拍参考**，不作为交付系统数据来源。

## 六、独立运行版（standalone）

`standalone/` 把核心能力独立实现为不依赖 AstrBot / Agent 的脚本（**不是**第三方源码复制）：

| 文件 | 说明 |
|---|---|
| `standalone/garmin_core.py` | 登录/会话、健康数据、活动、统计、报告、可选改名 |
| `standalone/garmin_cli.py` | 命令行入口（`--json` 输出、429 退避） |
| `standalone/README.md` | 环境、环境变量、命令示例、口径与风险 |

首次运行前需按 [standalone/README.md](standalone/README.md) 填写 `standalone/credentials.json`（本地隐私文件，已 gitignore，不入库）。注意它仍是开发期评估工具，按 PROJECT_CONTEXT 3.1 不进入交付系统。
