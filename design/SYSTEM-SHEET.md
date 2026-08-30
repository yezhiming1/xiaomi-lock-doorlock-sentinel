# SYSTEM-SHEET — 门锁观察簿

> 本表在正式页面代码之前冻结；新增变体必须先修改本表。

## Product

- **用途：** 查看门锁录像分析结果、校正人物学习、处理失败任务与核对备份回执。
- **使用者与频率：** 一名家庭管理员，通常每日或数日一次，手机与电脑均会使用。
- **栈：** FastAPI 服务静态 HTML/CSS/JavaScript；同一页面壳通过路由片段切换视图。
- **关照时刻：** 事件详情的“证据带”把原视频、最佳人脸、识别依据、跳过原因和人工历史排成一条可核对时间线。

## Route map

| route | job (one line) | layout family | in the shell? | traffic |
|---|---|---|---|---|
| `/` | 登录后转到事件页 | redirect | no | high |
| `/login` | 单密码安全登录 | focused form | no | medium |
| `/events` | 扫描近期录像、人物与处理状态 | dense list + detail drawer | yes | highest |
| `/people` | 管理已确认人物和未知人物簇 | gallery/list + detail | yes | high |
| `/operations` | 查看失败、重试、备份回执与系统健康 | status ledger | yes | medium |
| `/settings` | 控制训练期通知和安全会话 | form sections | yes | low |

**The shell**

- structure: 左侧窄导航、顶部当前视图标题与系统状态、主内容区；不显示技术堆栈。
- collapses to (mobile): 顶部标题 + 底部四项导航；详情以全屏抽屉出现。
- current-route indicator: 图标、文字和 3px 底部/侧边墨蓝标记同时表达。

**Build order**

1. `/events`
2. `/people`
3. `/operations`
4. `/settings`
5. `/login`

## Component inventory

| component | variants (name them all) | budget | where used |
|---|---|---:|---|
| button | primary / quiet / danger / icon | 4 | all routes |
| input | text-password / search | 2 | login, events, people |
| select | standard | 1 | filters, relationship |
| link | navigation / inline | 2 | shell, details |
| table | dense ledger | 1 | events, operations |
| toggle | standard | 1 | settings |

**Non-control components**

| component | variants | where used |
|---|---|---|
| status mark | healthy / pending / skipped / failed | all data routes |
| person tile | known / unknown / false-positive | people, event detail |
| evidence item | video / face / decision / audit | event detail |
| notice | info / warning / error | all routes |
| skeleton | row / detail | events, people |

## State matrix

| component | default | hover | focus-visible | active | disabled | loading | empty | error | selected |
|---|---|---|---|---|---|---|---|---|---|
| button | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | n/a | n/a | n/a |
| input | ✓ | ✓ | ✓ | ✓ | ✓ | n/a | n/a | ✓ | n/a |
| select | ✓ | ✓ | ✓ | ✓ | ✓ | n/a | n/a | ✓ | n/a |
| link | ✓ | ✓ | ✓ | ✓ | n/a | n/a | n/a | n/a | ✓ |
| toggle | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | n/a | ✓ | ✓ |
| table | ✓ | row ✓ | controls ✓ | n/a | n/a | skeleton | ✓ | ✓ | row ✓ |

| state | what it dims | measured contrast after dimming |
|---|---|---|
| error / stale | 不降正文透明度，状态底色改为浅朱红 | 正文目标 ≥ 7:1 |
| disabled | 控件使用实色灰蓝文字并增加禁用说明 | 文字目标 ≥ 4.5:1 |

**How each state is reachable**

- mechanism: 开发/验收使用独立测试容器和合成数据库；空状态由空库产生，加载状态由受控延迟响应产生，错误状态由仅作用于测试标签的网络故障注入产生。正式页面不接受 fixture 查询参数，避免测试入口进入生产面。
- routes: 登录后通过 `#events`、`#people`、`#operations`、`#settings` 切换；登录态与测试数据只存在于隔离验收环境。

| screen | empty state says | the one action that fills it |
|---|---|---|
| events | “还没有可查看的门锁录像。下载器写入第一段录像后会自动出现在这里。” | 查看系统状态 |
| people | “还没有形成可确认的人物。清晰人脸会在多次出现后进入这里。” | 查看跳过记录 |
| operations | “目前没有失败任务，系统会继续自动巡检。” | 刷新状态 |

## Density

- **Tables:** 桌面每屏约 12 行；表头在页面滚动容器内固定；时间、大小与次数使用等宽数字；时间和状态可排序。
- **Charts:** V0.0.1 不使用图表，以准确列表和状态汇总替代。
- **Truncation:** 文件名和错误摘要单行截断；点击行或键盘 Enter 可打开完整值。

## Gate checklist

- [x] every route has a one-line job and a layout family
- [x] the shell is described, including mobile collapse and current route
- [x] every component has a named variant budget
- [x] focus-visible is defined for every interactive component
- [x] empty / loading / error copy is written
- [x] build order is by traffic
- [x] no peak; only an evidence-oriented moment of care
