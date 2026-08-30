# COMMIT-SHEET — 门锁观察簿 V0.0.1

## 1. Moment of care

事件详情使用一条横向“证据带”：录像画面、最佳人脸、模型决定、人工修正和备份回执按发生顺序并列。它解决“为什么被这样识别”，不制造与任务无关的视觉高潮。

## 2. Color

- tier: restrained
- background: `oklch(0.965 0.009 245)`，目标平均 L≈0.94；用户多在日常手机或电脑环境查看，采用日光下的冷白记录纸，而不是夜间监控黑屏。
- ink: `oklch(0.27 0.035 248)`；primary: `oklch(0.43 0.11 251)`；accent stamp: `oklch(0.58 0.19 29)`。
- 状态绿、琥珀、朱红只用于语义，文字与形状共同表达。
- 拒绝安全产品常见的近黑 + 霓虹绿，也拒绝反向的米白编辑风；冷白纸与蓝黑墨来自家庭记录簿，朱红只像人工盖章一样出现。

## 3. Type

中文界面以 `Noto Sans SC`/系统中文无衬线回退；数字与事件时间使用同一家族的 tabular figures。标题通过字重、紧凑字距和蓝黑墨色建立层级，不用等宽字体扮演“技术感”，不使用装饰性衬线影响快速扫描。

## 4. Grid break

桌面事件页采用 5/7 非对称分栏，右侧证据详情越过普通内容列延伸至视口右边；移动端回到单列全屏详情。其余页面保持稳定网格。

## 5. Motion budget

1. 详情抽屉进入 220ms、退出 150ms，`cubic-bezier(0.23, 1, 0.32, 1)`；用于空间定位。
2. 状态刷新在原位置使用 160ms 颜色/透明度过渡；用于反馈。
3. Toast 使用 200ms 位移与透明度；用于确认操作。

无滚动编舞；所有移动效果在 `prefers-reduced-motion` 下改为仅透明度或直接更新。

## 6. Reflex check

a) 家庭安防管理端的常见答案是近黑背景、绿色在线点、四张 KPI 卡和监控墙。
b) 刻意反向后常见答案是大面积暖米白、衬线标题和松散卡片。
c) 本项目采用冷白证据纸、蓝黑记录墨、朱红人工盖章；核心是高密度事件账页与一条可解释证据带，不是监控墙，也不是生活方式杂志。

## 7. House tells broken

1. **Near-black by default** → 采用平均 L≈0.94 的日光冷白表面，风险靠语义色与文字呈现。
2. **Mono service type** → 不用角落等宽标签；状态直接写成自然中文。
3. **Status bar** → 顶部不做居中在线点结构；系统状态归入“运行”页面，壳层只保留当前任务。

## Direction contract

**THESIS:** 像一本可核对、可盖章、可撤销的门口观察簿；拒绝监控墙与 KPI 卡片拼盘。
**OWN-WORLD:** 冷白记录纸、蓝黑墨、朱红人工章；细线分栏、密集表格、真实缩略图与无嵌套卡片。
**STORY:** 先看今天发生了什么，再看系统为什么这样判断，最后完成一次可撤销的人物确认。
**FIRST VIEWPORT:** 左侧窄导航；中间约 42% 是事件账页；右侧约 58% 是选中事件的证据带与人物决定，主操作位于决定区底部。
**FORM:** 多屏运维系统的“证据账簿”方向；工作排序第一；种子由产品事实与系统表冻结。
**FINISH:** unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
