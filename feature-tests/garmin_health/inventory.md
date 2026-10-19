# Garmin Health 第三方源码功能拆解清单（vendor 本地参考，不入库）

> 本文件由构建脚本扫描 `raw/` 源文件 AST 自动生成，行号与 commit 版本源码一一对应。

## 1. 基本信息

| 项目 | 内容 |
|:---|:---|
| 仓库 | https://github.com/Jonathan-614/astrbot_plugin_garmin_health |
| 固定 commit | `9f2655cf5f6378651f6e25f3a517fb8eb61f90aa`（tag v1.0.3，master 分支） |
| 提交信息 | `release: v1.0.3`（2026-09-17 11:51:06 +0800） |
| 下载方式 | `git clone` + `git checkout 9f2655cf5f6378651f6e25f3a517fb8eb61f90aa`；raw/ 由 `git cat-file blob HEAD:<file>` 原样导出（LF，与 git blob 逐字节一致，已 sha256 复核） |
| 下载时间 | 2026-10-19 11:36 +0800（Asia/Shanghai） |
| 许可证 | **仓库无 LICENSE 文件 → 默认版权保留（All rights reserved）** |
| 使用限制 | 仅可存放于本 gitignored `vendor/` 目录本地参考；禁止提交入库、禁止复制到 vendor 外、禁止再分发 |
| 原始文件 | `vendor/raw/`（10 个核心文件 + COMMIT.txt，logo.png 按任务约定跳过） |
| 拆分文件 | `vendor/modules/`（42 个 .py，按 11 个功能目录 + 4 个配置副本归类） |
| 完整性 | `vendor/MANIFEST.sha256`（sha256 + 字节数 + 相对路径，按相对路径排序） |

## 2. 目录结构与文件数

| 目录 | 内容 | 文件数 |
|:---|:---|:--:|
| `modules/auth/` | Garmin 登录 / 会话 / tokenstore / 线程池调用 | 4 |
| `modules/health/` | 健康数据查询（今日概览、心率、睡眠、步数）与健康建议生成 | 7 |
| `modules/reports/` | 综合健康诊断报告 | 1 |
| `modules/activities/` | 活动筛选与列表展示 | 3 |
| `modules/volume/` | 运动量聚合统计与多类型报告 | 3 |
| `modules/personal_best/` | 个人最佳记录 | 1 |
| `modules/yearly/` | 年度运动报告 | 1 |
| `modules/rename/` | 活动重命名（写操作） | 1 |
| `modules/tools/` | 11 个 LLM FunctionTool 与工厂 | 12 |
| `modules/commands/` | AstrBot 命令路由（14 个 @filter.command） | 2 |
| `modules/utils/` | 公共工具函数（数值/日期/配速/参数解析） | 7 |
| `modules/config/` | `_conf_schema.json`、`metadata.yaml`、`requirements.txt`、`CHANGELOG.md` 副本 | 4 |
| `modules/` | 目录说明 `README.md` | 1 |
| `modules/commands/` | 命令索引 `COMMANDS.md` | 1 |
| `raw/` | 按 commit 下载的原始核心文件 + `COMMIT.txt` | 11 |
| vendor 根 | `MANIFEST.sha256`、`inventory.md` | 2 |

## 3. 核心功能清单（覆盖全部 42 个顶层符号）

> 类型说明：`class` / `def` / `async def` / `const/assign`；行号范围为原始符号（含装饰器行）在对应源文件中的起止行。

| # | 核心功能 | 源文件 | 原始符号（类型） | 行号范围 | 依赖（astrbot / garminconnect / 标准库） | 备注（风险 / 可借鉴） |
|:--:|:---|:---|:---|:---|:---|:---|
| 1 | TypeVar('T') 泛型占位，用于 call() 的返回类型标注 | `client_manager.py` | `T` (const/assign) | L17-L17 | 标准库 typing.TypeVar | 仅类型标注用途，保留以完整覆盖顶层符号 |
| 2 | Garmin 客户端管理器：账号密码登录、tokenstore 会话持久化/复用、线程池串行调用、活动分页拉取 | `client_manager.py` | `GarminClientManager` (class) | L20-L113 | garminconnect(Garmin)、astrbot.api.logger、asyncio、os、datetime、utils.clamp_int | 最核心可借鉴模块；tokenstore.json + _request_lock 串行化；未处理 MFA/429/网络重试 |
| 3 | 返回本地当天日期字符串 YYYY-MM-DD | `client_manager.py` | `_today_str` (def) | L116-L117 | 标准库 datetime | 纯函数，无外部依赖，可直接借鉴 |
| 4 | 秒数 → 'XhYmin' 可读时长 | `client_manager.py` | `_format_duration` (def) | L120-L128 | 无（内置实现） | 纯函数；被 services.build_activity_line 复用 |
| 5 | dailySleepDTO 起止毫秒时间戳 → 12 小时制时间范围（Local→GMT→裸字段 3 级 fallback） | `services.py` | `_sleep_time_range` (def) | L22-L43 | 标准库 datetime(timezone) | v1.0.3 修复时区问题；3 级 fallback 策略可借鉴 |
| 6 | 今日健康概览（步数/距离/卡路里/心率/睡眠） | `services.py` | `health_today_data` (def) | L48-L103 | client_manager.get_client/call、client_manager._today_str、utils.safe_float、astrbot.api.logger、datetime | 工厂函数返回 async 闭包 _run；距离缺失时用 步数×0.7 估算 |
| 7 | 近 N 天心率趋势（avg/max/min，N clamp 1..30） | `services.py` | `health_heart_rate_days` (def) | L106-L130 | client_manager.get_client/call、utils.clamp_int、datetime/timedelta | 每天 1 次 get_heart_rates；无 429 退避 |
| 8 | 近 N 天睡眠报告（时长/评分/深睡/浅睡/REM/起止时间） | `services.py` | `health_sleep_days` (def) | L133-L162 | client_manager.*、_sleep_time_range、utils.clamp_int、datetime | 每天 1 次 get_sleep_data；N 天 = N 次 API 调用 |
| 9 | 近 N 天步数/距离/卡路里 | `services.py` | `health_steps_days` (def) | L165-L188 | client_manager.*、utils.safe_float/clamp_int、datetime | 距离缺失时按 0.7m/步 估算 |
| 10 | AI/规则健康建议：采集今日+近7天明细 → 模板格式化 → context.get_provider_by_id().text_chat()，异常回退规则 | `services.py` | `_generate_health_tips` (async def) | L196-L313 | astrbot Provider(text_chat)、astrbot.api.logger、_rule_based_tips、client_manager.call、datetime/timedelta | 风险：健康明细发送给第三方 AI；模板 str.format 注入；异常自动回退规则模式 |
| 11 | 规则建议（步数<8000/≥10000、睡眠<7h/≥8h、睡眠评分<70） | `services.py` | `_rule_based_tips` (def) | L316-L329 | 无（内置实现） | 纯函数，无外部依赖，易复用 |
| 12 | 综合健康诊断报告：今日数据 + 近7天均值 + AI/规则健康建议 | `services.py` | `detailed_health_report` (def) | L333-L448 | client_manager.*、_generate_health_tips、_today_str、utils.safe_float、astrbot.api.logger、datetime/timedelta | 无 AI 时约 24 次 Garmin API 调用（3+7×3）；配置 AI 后 _generate_health_tips 再查约 24 次，合计约 48 次，限流风险最高；AI 建议依赖入参 context/ai_provider_name |
| 13 | 活动筛选：关键词/日期范围/子类型；函数内 SUBTYPE_TO_PARENT 先映射父类型查 API，再在 Python 层按子类型过滤 | `services.py` | `get_filtered_activities` (def) | L453-L502 | client_manager.get_activities/get_client、内置 SUBTYPE_TO_PARENT（函数内字典） | SUBTYPE_TO_PARENT 定义于函数内 L463-476，随函数整体拆分；max_count 默认 200 |
| 14 | 单条活动格式化（日期/名称/距离/时长/心率/爬升/负重/步数/卡路里/配速） | `services.py` | `build_activity_line` (def) | L505-L544 | client_manager._format_duration、utils.safe_float、utils.pace_from_speed | 展示层逻辑；no_elev_types 按 typeKey 白名单隐藏爬升；可借鉴输出格式 |
| 15 | 活动列表报告（表头 + 编号 + 最多 200 条 + 省略提示） | `services.py` | `activities_report` (def) | L547-L563 | build_activity_line | 纯字符串格式化 |
| 16 | 按 typeKey 集合+日期范围聚合 10 项指标（dist/count/elev/duration/calories/steps/weight/hr_weighted/hr_dur/strokes） | `services.py` | `compute_volume` (def) | L568-L602 | utils.safe_float | 核心统计纯函数；重量取最大值不累加；心率按 duration 加权 |
| 17 | 活动 typeKey 权威映射表：父类 → (typeKey, 图标标签, {typeKey})；running 9 / hiking 2 / walking 3 / swimming 3 / cycling 17 | `services.py` | `TYPEKEY_MAP` (const/assign) | L607-L652 | 无（纯常量） | 与 main.SUBTYPE_ALIAS 信息重叠、双份维护；可借鉴类型体系 |
| 18 | 多类型运动统计报告生成器：跑步/徒步/步行/游泳/骑行/单类型/旧混合 7 分支 | `services.py` | `build_volume_report` (def) | L656-L906 | 仅内置实现（内部 8 个格式化辅助闭包） | 约 250 行超长函数，分支按 kwargs 判断；排版/统计输出值得借鉴 |
| 19 | 个人最佳记录：最长距离/最快配速/最大爬升/最长时长 + 对应活动 | `services.py` | `personal_best_report` (def) | L911-L967 | client_manager.get_activities/get_client、utils.format_pace、astrbot.api.logger | 遍历全量活动；配速依赖 averageSpeed>0，活动拉取上限可能漏算 |
| 20 | 年度报告：总活动/距离/时长/爬升/消耗/均次距离/月度分布 | `services.py` | `yearly_report` (def) | L972-L1023 | client_manager.get_activities/get_client、utils.safe_float、astrbot.api.logger | 按 startTimeLocal 字符串比较过滤年份；count==0 提前返回 |
| 21 | 按活动名关键词匹配首条活动并调用 set_activity_name 重命名（写操作） | `services.py` | `rename_activity` (def) | L1028-L1053 | client_manager.get_client/call/get_activities、astrbot.api.logger | 唯一写 Garmin 云端操作；仅改首条匹配；确认逻辑在 tools 层 |
| 22 | 活动子类型中文别名 → Garmin typeKey 映射（跑步/徒步/步行/骑行/游泳五类） | `main.py` | `SUBTYPE_ALIAS` (const/assign) | L39-L83 | 无（纯常量） | 与 services.TYPEKEY_MAP 双份维护且存在冗余（公路车/公路自行车同值）；供 /活动 子类型筛选 |
| 23 | AstrBot Star 插件类（@register）：初始化 GarminClientManager、注册 11 个 LLM 工具，定义 14 个 @filter.command 命令 | `main.py` | `GarminHealthPlugin` (class) | L86-L496 | astrbot.api.star(register/Star/Context)、astrbot.api.event(filter/AstrMessageEvent)、astrbot.api.logger、client_manager、tools.create_all_tools、services 全部、utils.parse_volume_arg、calendar/os/re/datetime | 命令路由层；/活动 多级解析优先级（裸all→子类型→4位年份→N条→月/日→关键词）；@register 版本 1.0.2 与 metadata 1.0.3 不一致 |
| 24 | LLM 工具 #1 garmin_health_today：今日健康概览（无参数） | `tools.py` | `GarminHealthTodayTool` (class) | L39-L56 | astrbot.api.FunctionTool、dataclasses.field、services.health_today_data | 工具层仅参数定义 + 转发，无业务逻辑 |
| 25 | LLM 工具 #2 garmin_heart_rate：近 N 天心率（days 默认 7，服务层 clamp 1..30） | `tools.py` | `GarminHeartRateTool` (class) | L63-L82 | FunctionTool、dataclasses.field、services.health_heart_rate_days | 参数属性描述供 LLM 理解 |
| 26 | LLM 工具 #3 garmin_sleep：近 N 天睡眠报告 | `tools.py` | `GarminSleepTool` (class) | L89-L108 | FunctionTool、dataclasses.field、services.health_sleep_days | 参数属性描述供 LLM 理解 |
| 27 | LLM 工具 #4 garmin_steps：近 N 天步数数据 | `tools.py` | `GarminStepsTool` (class) | L115-L134 | FunctionTool、dataclasses.field、services.health_steps_days | 参数属性描述供 LLM 理解 |
| 28 | LLM 工具 #5 garmin_detailed_report：综合健康诊断报告 | `tools.py` | `GarminDetailedReportTool` (class) | L141-L158 | FunctionTool、dataclasses.field、services.detailed_health_report | 缺陷：run() 未传 context/ai_provider_name，工具链模式永远走规则建议；AI 建议仅 /报告 命令可用 |
| 29 | LLM 工具 #6 garmin_query_activities：关键词/日期范围/活动类型查询活动 | `tools.py` | `GarminQueryActivitiesTool` (class) | L165-L219 | FunctionTool、services.get_filtered_activities/build_activity_line、utils.normalize_date_range/clamp_int、astrbot.api.logger | limit 经 clamp_int 限制 1..200；日期非法返回 ValueError 文本 |
| 30 | LLM 工具 #7 garmin_running_volume：跑步九类细分统计 | `tools.py` | `GarminRunningVolumeTool` (class) | L226-L280 | FunctionTool、services.compute_volume/build_volume_report、utils.normalize_date_range、client_manager._today_str、datetime | 默认近 7 天；调用 get_activities() 全量分页拉取 |
| 31 | LLM 工具 #8 garmin_activity_volume：通用活动量统计（徒步/步行/骑行/游泳） | `tools.py` | `GarminActivityVolumeTool` (class) | L287-L351 | FunctionTool、services.compute_volume/build_volume_report、utils.normalize_date_range、client_manager._today_str、datetime | 内联 aliases 中英文映射；未知类型直接当 typeKey 使用 |
| 32 | LLM 工具 #9 garmin_personal_best：个人最佳记录（无参数） | `tools.py` | `GarminPersonalBestTool` (class) | L358-L375 | FunctionTool、dataclasses.field、services.personal_best_report | 无参数 |
| 33 | LLM 工具 #10 garmin_yearly_report：单年年度报告（默认今年） | `tools.py` | `GarminYearlyReportTool` (class) | L382-L408 | FunctionTool、services.yearly_report、datetime、astrbot.api.logger | 仅支持单年，不支持跨年 |
| 34 | LLM 工具 #11 garmin_rename_activity：修改 Garmin 活动名（需 confirm=true） | `tools.py` | `GarminRenameActivityTool` (class) | L415-L449 | FunctionTool、services.rename_activity | 破坏性写操作；activity_id 参数被忽略（service 只按 keyword 定位），仅改首条匹配 |
| 35 | 工厂函数：实例化并返回 11 个 FunctionTool（顺序即注册顺序） | `tools.py` | `create_all_tools` (def) | L456-L470 | 11 个 Tool 类 | 无类型注解；由 main.GarminHealthPlugin._register_llm_tools 调用 |
| 36 | None/空串/N/A/非法值 → float 安全转换 | `utils.py` | `safe_float` (def) | L9-L16 | 无（内置实现） | 纯函数，LLM/API 脏数据防护基础 |
| 37 | int 转换 + 区间钳制（非法值用 default） | `utils.py` | `clamp_int` (def) | L19-L25 | 无（内置实现） | 纯函数；LLM 数字参数防越界关键 |
| 38 | 十进制配速分钟 → 跑者习惯 M'SS"/km | `utils.py` | `format_pace` (def) | L28-L39 | 无（内置实现） | 纯函数；pace<=0 或 >=60 返回 N/A |
| 39 | Garmin averageSpeed(m/s) → 配速字符串 | `utils.py` | `pace_from_speed` (def) | L42-L47 | safe_float、format_pace | 纯函数 |
| 40 | 日期规范化 YYYY-MM-DD（支持 YYYY、YYYY-MM；end_of_period 补月末/年末） | `utils.py` | `normalize_date` (def) | L50-L76 | 标准库 datetime、calendar | 纯函数；非法格式抛 ValueError |
| 41 | 起止日期规范化 + 单边补全 + 先后顺序校验 | `utils.py` | `normalize_date_range` (def) | L79-L105 | normalize_date、标准库 re/calendar | 纯函数；start>end 抛 ValueError |
| 42 | 固定命令时间参数解析 → (start_date, end_date, label)，支持 空/all/YYYY/YYYY-MM/日期/近N天 | `utils.py` | `parse_volume_arg` (def) | L108-L137 | 标准库 datetime/timedelta/calendar/re、clamp_int | 纯函数；空参默认近 7 天 |

## 4. 功能模块 → 拆分文件映射

### `modules/auth/` —— Garmin 登录 / 会话 / tokenstore / 线程池调用

| 拆分文件 | 原始符号 | 源文件行号 |
|:---|:---|:---|
| `T.py` | `T` (const/assign) | L17-L17 |
| `GarminClientManager.py` | `GarminClientManager` (class) | L20-L113 |
| `_today_str.py` | `_today_str` (def) | L116-L117 |
| `_format_duration.py` | `_format_duration` (def) | L120-L128 |

### `modules/health/` —— 健康数据查询（今日概览、心率、睡眠、步数）与健康建议生成

| 拆分文件 | 原始符号 | 源文件行号 |
|:---|:---|:---|
| `_sleep_time_range.py` | `_sleep_time_range` (def) | L22-L43 |
| `health_today_data.py` | `health_today_data` (def) | L48-L103 |
| `health_heart_rate_days.py` | `health_heart_rate_days` (def) | L106-L130 |
| `health_sleep_days.py` | `health_sleep_days` (def) | L133-L162 |
| `health_steps_days.py` | `health_steps_days` (def) | L165-L188 |
| `_generate_health_tips.py` | `_generate_health_tips` (async def) | L196-L313 |
| `_rule_based_tips.py` | `_rule_based_tips` (def) | L316-L329 |

### `modules/reports/` —— 综合健康诊断报告

| 拆分文件 | 原始符号 | 源文件行号 |
|:---|:---|:---|
| `detailed_health_report.py` | `detailed_health_report` (def) | L333-L448 |

### `modules/activities/` —— 活动筛选与列表展示

| 拆分文件 | 原始符号 | 源文件行号 |
|:---|:---|:---|
| `get_filtered_activities.py` | `get_filtered_activities` (def) | L453-L502 |
| `build_activity_line.py` | `build_activity_line` (def) | L505-L544 |
| `activities_report.py` | `activities_report` (def) | L547-L563 |

### `modules/volume/` —— 运动量聚合统计与多类型报告

| 拆分文件 | 原始符号 | 源文件行号 |
|:---|:---|:---|
| `compute_volume.py` | `compute_volume` (def) | L568-L602 |
| `TYPEKEY_MAP.py` | `TYPEKEY_MAP` (const/assign) | L607-L652 |
| `build_volume_report.py` | `build_volume_report` (def) | L656-L906 |

### `modules/personal_best/` —— 个人最佳记录

| 拆分文件 | 原始符号 | 源文件行号 |
|:---|:---|:---|
| `personal_best_report.py` | `personal_best_report` (def) | L911-L967 |

### `modules/yearly/` —— 年度运动报告

| 拆分文件 | 原始符号 | 源文件行号 |
|:---|:---|:---|
| `yearly_report.py` | `yearly_report` (def) | L972-L1023 |

### `modules/rename/` —— 活动重命名（写操作）

| 拆分文件 | 原始符号 | 源文件行号 |
|:---|:---|:---|
| `rename_activity.py` | `rename_activity` (def) | L1028-L1053 |

### `modules/tools/` —— 11 个 LLM FunctionTool 与工厂

| 拆分文件 | 原始符号 | 源文件行号 |
|:---|:---|:---|
| `GarminHealthTodayTool.py` | `GarminHealthTodayTool` (class) | L39-L56 |
| `GarminHeartRateTool.py` | `GarminHeartRateTool` (class) | L63-L82 |
| `GarminSleepTool.py` | `GarminSleepTool` (class) | L89-L108 |
| `GarminStepsTool.py` | `GarminStepsTool` (class) | L115-L134 |
| `GarminDetailedReportTool.py` | `GarminDetailedReportTool` (class) | L141-L158 |
| `GarminQueryActivitiesTool.py` | `GarminQueryActivitiesTool` (class) | L165-L219 |
| `GarminRunningVolumeTool.py` | `GarminRunningVolumeTool` (class) | L226-L280 |
| `GarminActivityVolumeTool.py` | `GarminActivityVolumeTool` (class) | L287-L351 |
| `GarminPersonalBestTool.py` | `GarminPersonalBestTool` (class) | L358-L375 |
| `GarminYearlyReportTool.py` | `GarminYearlyReportTool` (class) | L382-L408 |
| `GarminRenameActivityTool.py` | `GarminRenameActivityTool` (class) | L415-L449 |
| `create_all_tools.py` | `create_all_tools` (def) | L456-L470 |

### `modules/commands/` —— AstrBot 命令路由（14 个 @filter.command）

| 拆分文件 | 原始符号 | 源文件行号 |
|:---|:---|:---|
| `SUBTYPE_ALIAS.py` | `SUBTYPE_ALIAS` (const/assign) | L39-L83 |
| `GarminHealthPlugin.py` | `GarminHealthPlugin` (class) | L86-L496 |

### `modules/utils/` —— 公共工具函数（数值/日期/配速/参数解析）

| 拆分文件 | 原始符号 | 源文件行号 |
|:---|:---|:---|
| `safe_float.py` | `safe_float` (def) | L9-L16 |
| `clamp_int.py` | `clamp_int` (def) | L19-L25 |
| `format_pace.py` | `format_pace` (def) | L28-L39 |
| `pace_from_speed.py` | `pace_from_speed` (def) | L42-L47 |
| `normalize_date.py` | `normalize_date` (def) | L50-L76 |
| `normalize_date_range.py` | `normalize_date_range` (def) | L79-L105 |
| `parse_volume_arg.py` | `parse_volume_arg` (def) | L108-L137 |

### `modules/config/` —— 配置 / 元数据副本（逐字复制，未改动）

| 副本文件 | 说明 |
|:---|:---|
| `_conf_schema.json` | 插件配置 schema（含敏感项 `garmin_password`、AI 模型/提示词配置） |
| `metadata.yaml` | 插件元数据（name/display_name/version 1.0.3/repo 等） |
| `requirements.txt` | 唯一 pip 依赖：`garminconnect>=0.3.4` |
| `CHANGELOG.md` | 变更日志（v1.0.1 → v1.0.3，含架构与安全相关变更） |

## 5. 覆盖核对（源文件顶层符号对拍）

| 源文件 | 顶层符号数 | 已拆分文件数 | 未覆盖 | 备注 |
|:---|:--:|:--:|:--:|:---|
| `client_manager.py` | 4 | 4 | 无 | T / GarminClientManager / _today_str / _format_duration |
| `services.py` | 17 | 17 | 无 | 17 个函数+常量；含函数内 SUBTYPE_TO_PARENT（随 get_filtered_activities 拆分） |
| `main.py` | 2 | 2 | 无 | SUBTYPE_ALIAS（38 项）+ GarminHealthPlugin（含 14 个 @filter.command 方法） |
| `tools.py` | 12 | 12 | 无 | 11 个 FunctionTool + create_all_tools |
| `utils.py` | 7 | 7 | 无 | 7 个纯函数 |
| **合计** | **42** | **42** | **无** | 拆分块行数合计 2024 行 |

对拍方法：用 Python `ast` 解析 `raw/` 源文件，枚举全部顶层 `ClassDef` / `FunctionDef` / `AsyncFunctionDef` / `Assign` 节点；
与拆分配置逐项断言集合相等（脚本内 `assert`），并断言每个拆分块与源文件对应行逐字一致（去注释头后字符串相等）。

## 6. 依赖清单

### 6.1 运行平台 / Python 包

| 依赖 | 类型 | 用途 | 备注 |
|:---|:---|:---|:---|
| `astrbot`（宿主平台，非 pip 依赖） | AstrBot API | `logger`、`event.filter/AstrMessageEvent`、`star.register/Star/Context`、`FunctionTool`、`Context.add_llm_tools`、`Context.get_provider_by_id().text_chat` | 插件必须运行在 AstrBot 内；拆分文件不可独立运行 |
| `garminconnect>=0.3.4` | pip（requirements.txt 唯一项） | Garmin Connect 登录与健康/活动数据 API | 非官方逆向库（内部 garth/requests 同步 HTTP）；0.3.4 起支持国服登录 |
| `aiohttp` | — | — | **源码未直接使用**（任务清单中的 aiohttp 不成立）；异步化靠 `asyncio.to_thread` 包装同步库 |
| `asyncio` | 标准库 | 事件循环、`to_thread`、`Lock` 串行化 | Garmin SDK 为同步阻塞调用，必须线程池隔离 |
| `datetime` / `timedelta` / `timezone` | 标准库 | 日期计算、睡眠时间戳格式化 | — |
| `os` | 标准库 | tokenstore 路径、session 目录 | — |
| `re` | 标准库 | 命令参数日期/月份正则解析 | — |
| `calendar` | 标准库 | 月天数计算（月末补全） | — |
| `typing` | 标准库 | Optional/Callable/TypeVar | — |
| `dataclasses` | 标准库 | FunctionTool `@dataclass` + `field` | — |

### 6.2 外部服务

- **Garmin Connect**：通过 `garminconnect.Garmin` 登录，读取 stats / heart rate / sleep / activities，重命名活动时写入 activity name。
- **可选 LLM Provider**：由 AstrBot 配置的模型（`health_ai_provider`），仅 `/报告` 命令在配置后调用 `provider.text_chat(prompt=...)`。

### 6.3 环境要求

- Python ≥ 3.9（`asyncio.to_thread`、`tuple[...]` 内建泛型注解；AstrBot 实际要求更高版本）。

## 7. 风险清单

1. **非官方接口**：`garminconnect`/`garth` 为社区逆向实现。Garmin 登录流程、Cloudflare 风控或字段结构变更可能导致登录/取数失败；requirements 仅 `>=0.3.4`，无版本上限锁定。
2. **账号密码明文配置**：`garmin_email` / `garmin_password` 存于 AstrBot 插件配置（schema 标 `sensitive`，但插件落盘仍为明文）。README 已提示仅私聊使用；群聊场景存在凭证与健康数据泄漏风险。
3. **MFA / 登录挑战未处理**：`GarminClientManager.get_client` 仅调用 `login(tokenstore=...)`，未处理 MFA 验证码、Cloudflare 挑战或交互式校验；启用 MFA 的账号可能无法登录，异常仅记日志后重试一次。
4. **限流 429 无退避**：代码无 429/限流识别、无指数退避与重试。`/报告` 无 AI 时约 24 次、配置 AI 后约 48 次 Garmin API 调用（_generate_health_tips 重复查询今日+7天）；年度报告/PB/统计会分页拉取最多 600 条（可配至 10000）活动，连续调用易触发限流或封禁。
5. **Token 文件明文**：会话 token 以 `tokenstore.json` 明文保存在插件目录 `.garmin_session/`，无加密与权限收紧；文件损坏时自动删除重建。
6. **数据准确性**：距离缺失时用 步数×0.7 估算；年度/PB 依赖 `garmin_max_activities` 上限（默认 600）可能漏算；负重取单次最大值、心率按活动时长加权。
7. **写操作风险**：`rename_activity` 会修改 Garmin 云端活动名；仅按关键词改**首条**匹配，且 tool 层 `activity_id` 参数被忽略、`confirm` 只是提示不是强校验，存在误改风险。
8. **AI / 隐私**：`/报告` 若配置 AI Provider，会把近 7 天健康明细拼进 prompt 发送给模型；`health_ai_prompt_template` 用 `str.format` 渲染，花括号需注意。
9. **许可证风险**：仓库无 LICENSE，默认版权保留；本 vendor 目录仅供本地评估，**禁止提交、复制到 vendor 外或再分发**。
10. **版本不一致**：`main.py` `@register` 声明 1.0.2，`metadata.yaml` 为 1.0.3（小瑕疵，不影响功能）。
11. **并发吞吐**：`GarminClientManager._request_lock` 全局串行 Garmin 调用，避免 session 冲突但多用户并发时吞吐低。

## 8. 可借鉴点（供本项目评估）

- **tokenstore 会话持久化**：`garminconnect.login(tokenstore=path)` 自动加载/保存/损坏自愈，省去手动维护 session。
- **同步 SDK 异步化**：`asyncio.to_thread` + `asyncio.Lock` 把阻塞调用移出事件循环并串行化，模式简单可复用。
- **分页拉取活动**：`get_activities(start, limit, typeKey)` 按配置上限分页，避免只取最近 200 条导致年度/PB 漏算。
- **typeKey 别名体系**：`TYPEKEY_MAP` + `SUBTYPE_ALIAS` + `SUBTYPE_TO_PARENT` 三层映射，把 Garmin 父子类型与中文别名解耦。
- **纯函数工具层**：`utils.py`（safe_float/clamp_int/日期解析/配速格式化）与 `compute_volume` 均为纯函数，易测试易移植。
- **LLM FunctionTool 封装**：`FunctionTool` dataclass + `name/description/parameters` 声明式定义，`create_all_tools` 一次注册。
- **睡眠时间戳 3 级 fallback**：Local → GMT → 裸字段，兼容不同账号/时区数据。

## 9. 校验说明

- `vendor/MANIFEST.sha256`：每行为 `<sha256>  <bytes>  <相对路径>`，按相对路径排序；覆盖 `raw/`、`modules/`、`inventory.md`（排除重建用的 `_tmp/` 与 MANIFEST 自身）。
- 拆分完整性：脚本断言「AST 顶层符号集合 == 拆分配置集合」，并逐文件断言拆分块与 `raw/` 对应行逐字相等。
- 语法完整性：全部拆分 `.py` 经 `ast.parse` 校验通过（拆分块保留原缩进/装饰器）。
- 入库隔离：`feature-tests/garmin_health/.gitignore` 的 `/vendor/` 规则使本目录全部文件被 git 忽略，`git status` 不出现 vendor 内容。
