# 验证清单

更新：2026-10-12。每项按证据区分，不使用模拟订单冒充 Apple 订单。历史版本保留当时结果。

## 环境与可运行产物

### v0.7.0 平台整理与隐私确认修复

- 回退基线为 v0.6.5 的 **`d39bb45`**。系统目录、原生安全存储、Chrome 查找和进程锁集中到平台接口；账户与取货人共用安全读写，保留原有服务名与类型。GUI/CLI 共用本地场景运行器，四处 JSON 写入共用临时文件和原子替换。Windows 的 `fcntl` 导入障碍移除，新增原生凭据、电源状态、Chrome 路径、时区依赖和 PowerShell 构建入口。
- 主窗口 **946 → 756** 行，Apple 主适配 **1137 → 920** 行；分别减少 **20.1% / 19.1%**，主要是职责拆分。`app/` + Python 开发脚本总量 **6950 → 6851** 行，净减少 **99 行 / 1.4%**，计入新增平台支持与诊断，不包含测试、文档或 PowerShell。删除两个共 280 行的旧 Chrome 实验脚本；登录语言和浏览器启动参数保留。详见 [Windows 迁移准备](WINDOWS_PREPARATION.md)。
- 用户实际 v0.6.5 试跑日志显示隐私选择未被自动使用，人工继续后控件或跳转超时；随后商品选择出现 AssertionError。本轮确认两项提前同意已保存，未抓取到当次已消失弹窗的 DOM，因此不把日志直接归因于唯一官网原因。修复覆盖声明弹窗范围、关闭控件与隐藏模板、原生标签/ARIA 控件、Qt 隐藏页面恢复，以及勾选成功后的按钮可用状态。确认尝试只在真正提交前记录，勾选失败保留尚未提交的状态；提示给出具体阶段，订单提交仍不重复。
- 首轮回归 **351 passed / 3 failed**；新增夹具中的可见额外符号按声明变化停止、Cookie 按钮过早隐藏、合成身份的 `.invalid` 限制分别修正。隐私、平台和真实 Qt 用例补跑 **33 passed / 13.56 秒**。最终完整回归 **354 passed / 127.66 秒**，记录 `pytest-v070-build27.txt`、`tests-v070-build27.xml`；最后补上整个窗口隐藏时的恢复后，相关 GUI/生命周期检查 **36 passed / 10.50 秒**，记录 `pytest-v070-hidden-window.txt`、`tests-v070-hidden-window.xml`。
- Qt 子进程两次打开同一私有测试 profile，先保存合成 Cookie，再重启复读；每次在完全拦截的 Apple 结构声明中隐藏整个窗口，软件自动恢复页面、勾选两项、启用并点击确认一次。覆盖提前授权重启保留、关闭按钮、覆盖输入、ARIA 标签、背景结账勾选框、声明新增/改变及单次确认。没有实际 Apple 隐私提交或新订单；新版本真实现货的完整自动隐私确认尚待官网复验。
- Windows 路径与原生后端选择、锁跨进程竞争、异常释放、私有 JSON 写入失败保留旧文件，以及线程电源状态恢复在本机夹具验证。Windows 真机、Windows 安装包与 Apple 登录未验证，macOS 构建不能替代这些验收。

- 最终 macOS bundle 为 **v0.7.0 / build 27**，构建、严格签名及安装检查通过。打包版 **GUI_SMOKE_OK / EMBEDDED_SMOKE_OK**，均退出 0；独立临时目录运行全部 **14** 种合成场景，状态和提交次数均符合预期，用时 **27.20 秒**。负向场景保留 FAILED/PAUSED/ORDER_UNCERTAIN，CLI 汇总退出 1 是预期结果。记录 `build-v070-build27.log`、`packaged-v070-gui.log`、`packaged-v070-embedded.log`、`packaged-v070-local-all.json/.log`、`install-v070-build27.log`。
- 实际已安装并打开 v0.7.0，安装前后所有配置完全一致，两项提前隐私同意、六家上海门店、薰衣草紫色 iPhone 17 256GB 及招商银行 24 期均保留。原 v0.6.5 应用正常退出并保留可回退副本。新安装版复核原内置 profile，账户 GET **200**，软件在北京时间 **10-13 02:54:39** 确认登录；认证提交 **0**，没有重新输入密码。私有记录 `apple-login-v070-build27-embedded-restored.json`（0600，Git 忽略）。软件当前保持已登录会话，可由用户重新开始官网模拟下单。

### v0.6.5 单独精简与回归

- 以已推送的内置登录修复提交 **`1aef13b`** 为回退基线。精简合并借用浏览器连接的退出清理和任务 adapter 初始化，移除 17 个未使用的导入名称、未读取的库存缓存、旧商品控件字段及别名。动态读取的刷新时间、浏览器种类、当前 adapter 和 Qt 窗口引用经搜索确认保留。精简是单独提交，可仅撤销该提交，保留语言修复与用户资料。
- Python 包、界面及 macOS bundle 统一读取 `app/__init__.py` 的版本信息；实际 setuptools metadata 与 bundle Info.plist 均为 **0.6.5 / build 26**。记录 `package-v065-build26-metadata.txt` 与 `build-v065-build26.log`。
- 精简后完整回归 **339 passed / 123.98 秒**，记录 `pytest-v065-build26-full.txt`、`tests-v065-build26-full.xml`。既有检查覆盖独立 Chrome 正常关闭/重连、内置 profile 保存与断开、日常 Chrome 不关闭用户 context、取货与付款流程、提交锁及脱敏。未出现回归，无需回撤。
- 打包 GUI 与内置 renderer 自检 **GUI_SMOKE_OK / EMBEDDED_SMOKE_OK，均退出 0**；已检查 GUI 截图，版本正确。严格签名及安装核验通过，实际安装版显示 **v0.6.5 / build 26**，安装前后全部保存的配置完全一致。记录 `packaged-v065-build26-gui-smoke.log`、`packaged-v065-build26-embedded-smoke.log`、`install-v065-build26.log`。
- 最终安装版沿用原内置 profile，新账户文档 **18:12:16 UTC / GET 200**，软件 **18:12:17 UTC** 显示“当前浏览器登录已确认”、北京时间 10-13 02:12:17。认证提交数 **0**、诊断捕获错误数 **0**，记录 `apple-login-v065-build26-embedded-restored.json`，权限 0600、Git 忽略。源码精简与软件正常更新均未丢失该会话。

### v0.6.4 内置浏览器语言与实际登录

- 安装 **v0.6.4 / build 25** 后，内置浏览器由 `zh-DE` 改为 `zh-CN,zh`。用户在同一软件内完成真实认证和双重验证，商店回调成功；随后账户认证返回 **18:03:20 UTC / 200**、账户文档 **18:03:23 UTC / GET 200**，新账户复核 **18:03:59 UTC / GET 200**，软件显示登录已确认。409 / hsa2 仅作为进入双重认证，未当作登录成功。记录 `apple-login-v064-build25-embedded-actual.json`，权限 0600、Git 忽略。
- 真实窗口正常 Cmd-Q 退出后重开，原内置 profile 的新账户文档 **18:04:45 UTC / GET 200**，软件确认原会话有效；这次认证提交数 **0**、诊断捕获错误数 **0**。记录 `apple-login-v064-build25-embedded-restored.json`。没有复制日常 Chrome 资料、重发密码、改网络或新建订单。
- 最终完整源码回归 **339 passed / 124.53 秒**，记录 `pytest-v064-build25-full.txt`、`tests-v064-build25-full.xml`。内置集成检查新增主页面与辅助页面的实际语言偏好，并保留 profile 持久性、断开重连与正常退出检查。最初新增的拦截请求头断言失败：CDP 拦截点未提供浏览器后续生成的 Accept-Language，不能作为实际发出请求头的证据，已删除该无效断言；未更改实现来迎合它。
- 构建、安装和严格签名核验成功；打包内置 renderer 自检输出 **EMBEDDED_SMOKE_OK / 退出 0**，记录 `build-v064-build25.log`、`install-v064-build25.log`、`packaged-v064-build25-embedded-smoke.log`。安装前后的购买配置相同；本次测试主动把浏览器模式从独立 Chrome 改为内置。账户仍未保存到应用钥匙串，没有把聊天密码写入配置。
- 此次只有标准 profile 语言偏好发生实现变化，当前成功认证与此前内置 503 的对端摘要和 TLS / HTTP 版本相同。结果支持错误语言偏好是本次内置失败的重要因素；未再次改回错误语言提交密码，不能推断所有历史 503 / 541 的唯一原因或保证永久免验证。

### v0.6.3 慢账户跳转

- build 23 实际更新后登录保留：账户文档 **17:45:32 UTC / GET 200**，再次普通账户复核 **17:47:15 UTC / GET 200**，软件在 **17:47:16 UTC** 显示“当前浏览器登录已确认”，时间展示为北京时间 10-13 01:47:16。没有重新提交密码。记录 `apple-login-v062-build23-restored-actual.json`，权限 0600、Git 忽略。
- 首次复核在正常账户页晚到前暂停，新增 5.5 秒响应的完全离线夹具；旧代码 **1 failed / 6.20 秒**，明确为账户链接已点击后的五秒导航等待超时。记录 `slow-account-navigation-before-fix.txt`。调整为同一次点击允许 15 秒内完成；不自动重发点击或密码。
- 调整后的登录相关源码检查 **50 passed / 18.78 秒**，覆盖新的慢响应、官网入口、受保护订单页面、认证状态恢复及登录界面。记录 `pytest-v063-build24-login-final.txt`、`tests-v063-build24-login-final.xml`。本轮完整回归的 338 项结果在 build 23 节，最终导航等待调整通过专项验收，没有将两组结果相加为一轮完整测试。
- 已安装 **v0.6.3 / build 24**，构建、严格签名核验及打包 GUI / 内置 renderer 自检通过，分别输出 `GUI_SMOKE_OK` / `EMBEDDED_SMOKE_OK`，退出 0。记录 `build-v063-build24.log`、`packaged-v063-build24-gui-smoke.log`、`packaged-v063-build24-embedded-smoke.log`、`install-v063-build24.log`。空闲旧窗口正常 Cmd-Q 退出后安装，安装前后最新保存的配置完全一致。
- 最终真实窗口点击“独立 Chrome 登录”，复用软件自己的 Chrome 资料：**17:53:43 UTC 账户 GET / 200**、**17:53:44 UTC 自动确认原登录仍有效**；界面显示北京时间 10-13 01:53:44，没有早暂停或新的密码提交，捕获错误数 0。Chrome 模式说明不会显示内置旧页面。记录 `apple-login-v063-build24-restored-actual.json`，权限 0600、Git 忽略；本次测试主动将模式从内置改为独立 Chrome，其他所有配置仍与安装前相同。当前软件会话保留，无新订单或付款。

### v0.6.2 登录检查与浏览器页面同步

- 用户实际在 build 22 软件独立 Chrome 完成登录后反馈检测错误。窗口中的“检查登录状态”因预登录任务仍等待而被拦截；通过“继续检查”在 **2026-10-12 17:34:29 UTC** 确认同一会话已登录。新版本统一预登录等待中的检查入口，不继续购买或协议暂停，不跳过官网等待；Chrome 活跃时隐藏旧内置失败页，保留原视图。确认时间显示北京时间。
- 最终完整源码 **338 passed / 118.30 秒**，记录 `pytest-v062-build23-final.txt`、`tests-v062-build23-final.xml`。新增界面检查覆盖控制台/浏览器页的实际检查按钮、登录诊断完成按钮、两类购买暂停、官网等待期限、切换后旧内置视图隐藏但保留，以及 UTC 观察到北京时间展示的换算。官网真实认证与上述离线检查分开记录。
- build 23 构建、严格签名核验及打包自检通过，`GUI_SMOKE_OK` / `EMBEDDED_SMOKE_OK`，退出码均为 0。记录 `build-v062-build23.log`、`packaged-v062-build23-gui-smoke.log`、`packaged-v062-build23-embedded-smoke.log`。已正常关闭旧软件会话并 Cmd-Q 退出，进程结束后安装 **v0.6.2 / build 23**；安装前后全部配置一致，当前模式为软件独立 Chrome。实际窗口确认原 Duo、六家门店、招商银行 24 期及北京时间。记录 `install-v062-build23.log`、私有 `build23-config-before-install.json`。

### v0.6.1 独立浏览器登录入口与网络对照

- build 22 首轮完整回归 **326 passed / 122.04 秒**。补充缓存账户页不能解除新认证失败的检查后，第二轮 **328 passed / 1 failed / 121.55 秒**：新增夹具使用了 Playwright `browser.new_page()` 所属的单页上下文，无法创建辅助复核页，改为 `new_context()` / `context.new_page()`；原失败保护已在该夹具前半段得到验证。记录 `pytest-v061-build22-initial.txt` 与 `pytest-v061-build22-fixture-failure.txt`。
- 修正夹具后的完整回归 **329 passed / 118.03 秒**，记录 `pytest-v061-build22-final.txt`、`tests-v061-build22-final.xml`。随后本机查询发现 VPN 命令须用 `scutil --nc list`，该常量与专项夹具同步修正；新增真实 macOS 只读查询检查后的网络专项 **8 passed / 1.21 秒**，记录 `pytest-v061-build22-network-final.txt`、`tests-v061-build22-network-final.xml`。这里分别报告完整与专项结果，不重复累计。
- 正确系统命令重新查询：系统代理开关与代理环境变量均未配置、接口类型为一个物理接口、IPv4 可用、IPv6 未报告、标准 VPN 连接数 0，所有查询成功。旧现场脚本的 VPN 数量因错误命令已作废。Chrome 软件资料与日常 Default/Profile 1 的白名单设置对照，均未发现显式代理服务器/PAC 或专用 Secure DNS 模板；这不能排除网络扩展及服务器对不同会话的判断。
- 构建与严格签名核验通过；打包 GUI 和内置 renderer 离线自检分别输出 `GUI_SMOKE_OK`、`EMBEDDED_SMOKE_OK`，均退出 0。记录 `build-v061-build22.log`、`packaged-v061-build22-gui-smoke.log`、`packaged-v061-build22-embedded-smoke.log`。截图只来自临时离线配置。
- 已安装 **v0.6.1 / build 22**，安装前后全部配置一致。实际窗口已确认 Duo、六家上海自提店、招商银行 24 期及两个直接登录按钮。旧版会话正常关闭、Cmd-Q 退出，进程结束；新安装版实际验证单独记录，合成通过不等于 Apple 认证成功。记录 `install-v061-build22.log`、私有 `build22-config-before-install.json`。
- 实际新版本内置入口自动选择正确个人资料登录链接；单次认证初始化 **17:32:11 UTC / 200 JSON**，提交 **17:32:15 UTC / 503 HTML**，界面仍无法验证身份。随后用户在软件独立 Chrome 登录：初始化 **17:32:46 UTC / 200 JSON**、提交 **17:32:56 UTC / 200 JSON / hsa2**、商店回调 **17:32:57 UTC / 200 JSON**、账户页 **17:32:58 UTC / 200 HTML**，代理继续检查确认成功。两种浏览器捕获错误数均为 0，记录 `apple-login-v061-build22-embedded-actual.json`、`apple-login-v061-build22-chrome-actual.json`，权限 0600、Git 忽略。不保存密码、不复制日常 Cookie、不创建订单。
- 两场认证均为同一对端摘要、IPv4、TLS 1.3 / HTTP 1.1，网络样本相同；原生 Chrome 为 155，Qt 内置 Chromium 为 140。不能将此次成功解释为唯一根因已解决、强制更换对端或完全取消官网风控；也不把初始空白页 secure_context / WebCrypto 取值当成认证框架能力。

### v0.6.0 日常 Chrome 连接

- build 20 完整源码 **298 passed / 109.74 秒**；构建、严格签名核验、打包 GUI 和内嵌 renderer 自检均通过。记录 `pytest-v060-build20-final.txt`、`tests-v060-build20-final.xml`、`build-v060-build20.log`、`packaged-v060-build20-smoke.log`、`install-v060-build20.log`。已实际安装，看到 v0.6.0、原 Duo、六家自提店和招商银行 24 期计划。安装脚本不改应用数据；用户操作期间浏览器模式发生变化，其他配置与此前快照一致，未覆盖用户的新选择。
- 用户自行安装官方 Playwright Extension 并授权选定 Apple 页面，实际连接建立；日常 Chrome 显示客户端标签组和连接图标。这是扩展握手实测，不是合成连接冒充真实 Chrome。
- build 20 新打开通用账户入口后提示登录，但已选页面可以打开真实订单列表。订单列表没有可见退出链接，原判断无法确认；新离线复现 **2 failed**，记录 `order-session-before-fix-build21.txt`。build 21 修改检查入口和明确订单列表的认证证据，真实安装版结果另行补充。没有提交新订单、付款或再次提交密码。
- 连接运输协议的离线测试使用固定 Playwright 1.63.0 的真实服务端及 Python 客户端、临时合成浏览器。必须使用共享服务模式才暴露既有默认上下文；正常连接保留选定页面，拒绝无安装、多个页面、非中国站、无效本地端点，停止待授权连接只结束软件自己的 Node。此测试与扩展握手、Apple 认证单独记录。
- 系统代理开关关闭但 Surge 网络扩展激活的观察仅本机保存于 `network-extension-state-v060.json`；不能据此判定实际 Apple 路由或宣称根因确定。
- build 21 首次完整回归 **306 passed / 1 failed / 117.95 秒**：新增失效会话夹具使用服务器 302，而 Playwright 仅拦截重定向链首个请求；改为完全拦截的独立文档导航。修正后的订单会话检查 **9 passed / 4.24 秒**，分别确认当前节点的新 GET、无退出链接的订单列表、真正失效、非法/不同源/相似路径链接、明确登录表单优先及保存登录的返回识别。记录 `order-session-after-fix-build21.txt`，完整最终回归另记。
- build 21 最终完整源码 **307 passed / 113.49 秒**，记录 `pytest-v060-build21-final-fixed.txt`、`tests-v060-build21-final-fixed.xml`。合成测试全部拦截浏览器请求；真实账户复核在实际安装版另记。
- build 21 构建、严格签名核验及打包自检通过，`GUI_SMOKE_OK` / `EMBEDDED_SMOKE_OK`，退出码均为 0。记录 `build-v060-build21.log`、`packaged-v060-build21-smoke.log`、`install-v060-build21.log`；已实际安装确认版本 0.6.0 / build 21，安装前后全部配置一致。旧版正常关闭软件会话后经 Cmd-Q 退出，进程结束；日常 Chrome 原有订单列表仍打开。当前新连接页已准备，浏览器规则要求在此敏感访问授权时当场确认，尚未由代理代点或完成 build 21 的真实账户复核。
- 用户明确确认旧版同一个软件 Chrome 在开启回国 VPN 后进行新的密码验证并成功，随后关闭 VPN 恢复 Codex 连接。这是用户完成的同窗口网络对照，没有本次成功请求链，不能记作 build 21 自动登录测试或据此保证 541 已解决。本次未改动 VPN/代理；软件代码未配置代理或另一套 Apple 私有认证接口。

### v0.5.3 中国区认证诊断与旧版对照

- build 19 构建、安装及严格签名核验通过；打包 GUI 与内嵌 renderer 自检退出 0，分别输出 `GUI_SMOKE_OK`、`EMBEDDED_SMOKE_OK`。记录 `build-v053-build19.log`、`packaged-v053-build19-smoke.log`、`install-v053-build19.log`。实际安装窗口已看到唯一北京时间标签及原 Duo、六家门店、招商银行 24 期计划；没有开始购买或新认证。配置只迁移显示时区，重开后的摘要哈希与迁移后相同。旧窗口辅助功能读取持续超时，更新前经系统退出列表关闭；配置和诊断已保留，本轮不把重开成功解释成所有卡死已修复。
- build 19 最终完整源码 **281 passed / 108.06 秒**，记录 `reports/pytest-v053-build19-final-release.txt`、`reports/tests-v053-build19-final-release.xml`。包括购物袋记住账户但认证过期时不误判、官网当次账户链接复核、旧预约换算并保存重读保持原时刻；界面时钟与记录只显示北京时间。
- build 19 中间检查仍有一次退出崩溃：基础及派生 QTimer 构造均可能触发，提前释放表格项目也没有可靠解决；这些尝试已撤回。进一步定位夹具保留已被 Qt 删除的辅助功能单元格包装，释放引用后连续 **20 次退出检查通过**，记录 `qt-lifecycle-interface-release-build19.txt`；随后完整检查通过。应用回调用父窗口持有的 QTimer，未禁用辅助功能；本轮不保证所有 Qt 崩溃都已解决。
- build 18 实际软件购物袋曾在 14:57 UTC 显示原登录有效，账户页紧接着明确要求登录，暴露误判；Codex 浏览器的记住账户菜单同样不能通过订单页复核。build 19 已要求受保护账户页确认，离线过期与有效两种场景分别通过。
- 新购物袋入口的实际软件 Chrome 认证仍失败：**15:03:27 UTC 初始化 200 / JSON，15:03:30 UTC 单次提交 503 / HTML**，捕获错误数为 0。私有证据 `login-store-entry-build18-user-attempt.json`。没有增加新密码尝试或创建新订单。
- 只读核对本机保留的 v0.1.0、v0.2.2、v0.3.0、v0.4.0 安装包：前两者使用 Playwright 持久 Chromium，后两者为本机正式 Chrome、软件私有资料及 localhost CDP；旧账户入口相同。切换当时改变了多项变量，无法确定哪个造成首次成功。当前恢复旧原生启动仍失败；详情见 [登录诊断](LOGIN_DIAGNOSIS.md)。
- build 18 源码集中检查 **280 passed / 108.21 秒**，记录 `reports/pytest-v053-build18-final.txt`、`reports/tests-v053-build18-final.xml`。新增购物袋延迟菜单、官网生成的登录上下文、已登录不跳转、返回商店的确认、当前诊断页保留及异常链接的检查；所有合成网站请求均在本机拦截。
- Chrome 会话 Cookie 原有验收漏掉无有效期类型。加入后先复现正常退出重开丢失，记录 `native-session-cookie-before-fix-v053.txt`；修正启动恢复后原生会话与重连检查 **4 passed / 3.71 秒**，记录 `native-session-cookie-after-fix-v053.txt`。两类 Cookie 保留、正常重启只有新空白页，旧结账页没有自动载入；不代表 Apple 认证已通过。
- build 18 首次集中检查 **278 passed / 2 failed**，记录 `pytest-v053-build18.txt`。一项旧入口夹具需更新为购物袋流程；另一项退出检查在 Python 闭包仍复现 PySide 静态 `QTimer.singleShot` 的空元对象崩溃，现场栈在 `SignalManager::retrieveMetaObject`。应用和夹具改为窗口持有的单次 QTimer，完整重跑通过；保留辅助功能及原退出流程。
- 用户补充日常 Chrome 的实际成功路径为商店购物袋菜单，软件此前直达账户页。现改为相同公开流程，补齐 `/shop/signIn?…` 识别，后台需新登录也从购物袋进入。Codex 商店页购物袋显示退出登录，但后续访问实际订单链接仍要求登录，只能证明记住账户；未注销会话。
- build 17 完整源码 **268 passed / 103.69 秒**，记录 `reports/pytest-v053-final.txt`、`reports/tests-v053-final.xml`。覆盖中国区与全球认证的单步、两步表单，认证 503 只提交一次、来源校验、被动 CDP 跨来源框架观察、脱敏及正常 Chrome 重连。前一完整检查 **267 passed / 1 failed**：退出夹具把 Qt 静态 quit 方法直接交给 QTimer，崩溃栈位于 PySide 回调绑定的空接收者；改为 Python 闭包，与应用现有自检一致。相关检查 **5 passed / 7.64 秒** 后完整重跑通过。未以禁用辅助功能作为修复。
- v0.5.3 / build 17 构建与严格签名核验成功；打包后的 GUI 与内嵌 renderer 离线自检均退出 0，分别输出 `GUI_SMOKE_OK`、`EMBEDDED_SMOKE_OK`。记录 `build-v053.log`、`packaged-v053-smoke.log`。这些检查不是官网登录成功的证明。
- 实际安装版 v0.5.2 诊断抓到：正常账户入口与登录页面返回 200，中国区认证初始化 200 / JSON，随后认证提交 503 / HTML；官网显示“无法验证你的身份”。原识别遗漏 `idmsa.apple.com.cn`，现已修复。真实 HTTPS 认证框架 Cookie 与 WebCrypto 均开启，初始空白页能力不能用于判断认证框架故障。
- 在同一 Codex 正常浏览器完成 iCloud 与中国商店对照：iCloud 单次密码验证到达条款页，未接受条款；中国商店单次提交仍显示无法验证身份。说明错误不局限于软件内置浏览器，不证明中国商店会话有效。详情见 [登录诊断](LOGIN_DIAGNOSIS.md)。本轮没有新订单或银行付款。
- 与 Git 中成功版本比对后恢复原 Chrome 非零 localhost 端口启动，保留既有资料和严格恢复规则。此前实际浏览器 webdriver 为 true；恢复后离线原生 Chrome 为 false。没有篡改浏览器属性；这项版本恢复未被宣传为已解决 Apple 的 503 或 541。
- 已安装 v0.5.3 / build 17，严格签名核验通过，实际窗口显示版本及原 Duo、六家门店、招商银行 24 期配置。只将诊断浏览器改为原软件 Chrome，没有清空原资料或保存聊天密码。14:24 UTC 的真实 Chrome 诊断记录 webdriver 为 false，正常账户入口跳转 secure11，登录文档 200；尚未据此宣称认证成功。用户补充其普通网页当前可登录，因此本轮优先对照会话，暂不要求切换热点。
- 用户在上述新版软件 Chrome 单次尝试后，记录认证初始化 200 / JSON、认证提交 503 / HTML，页面仍无法验证身份，`capture_errors=0`。证据仅本机 `reports/login-root-old-launcher-chrome-v053.json`。恢复旧启动方式未解决真实登录。
- 用户确认日常 Chrome 今天重新输入密码后成功，已只读看到中国商店的订单列表与当前登录账户提示，未更改订单或注销该会话。旧版官方账户入口的只读对照也跳转 `/shop/signIn/account`，不据此制造入口回退修复。软件会话与普通会话的差异仍待确定。

### v0.5.1 账户保存与登录恢复

- 最终完整源码 **252 passed / 103.07 秒**，记录 `reports/pytest-v051-release.txt`、`reports/tests-v051-release.xml`。登录集中检查修正后 **52 passed / 19.72 秒**。覆盖单步/两步登录、一次提交、失败与未知不重发、验证码保留、人工完成后信任选项、未知 iframe/修改密码/不同账户不填、关键 541 与加载不读取密码、账户优先于游客、配置无凭据及导入权限清除。
- 首次集中检查 **2 failed / 50 passed**：信任后父页跳转销毁 iframe 被误报为失败；后台检查的合成 302 夹具没有隔离重定向链。修正为只读复核父页成功、合成页面导航全部重新拦截。后一夹具意外访问的是未认证公开账户 GET，当时钥匙串读取和密码提交均为 0。中间记录保留，不当作官网认证通过。
- 安装版 **v0.5.1 / build 14** 构建、安装与严格 ad-hoc 签名核验成功。内嵌 renderer 离线自检 `EMBEDDED_SMOKE_OK`，隔离合成流程 `PAYMENT_PENDING`、提交 1 次、合成订单 1 个。记录 `build-v051.log`、`install-v051.log`、`packaged-embedded-v051.log`、`packaged-e2e-v051.json`。这些结果检查打包与流程，不代表 Apple 下单成功。
- 已直接打开实际安装版，确认版本号、Duo 星光白色 256GB、六家上海门店、招商银行 24 期与原购买计划保留。账号仍未保存，自动登录默认关闭；没有把聊天中的密码自动写入钥匙串。
- 实际内置浏览器显示 Apple 中国站登录表单，控件为「电子邮件或电话号码」「密码」「记住我的账户」「登录」，并重现用户报告的「无法验证你的身份。重试」。没有确认登录成功或可信浏览器成功；本次没有自动提交真实密码、验证码或新订单。界面操作发生变化时先暂停操作协调，不把合成验证替代真实登录。
- 原 v0.5.0 Chrome 登录已观察到关键 fetch 返回 541。官方帮助入口自行跳转 secure7 已核对，但服务器拒绝原因仍未确定。**本轮不能宣称 541 已解决，也不能承诺永久免验证码。**

### v0.5.0 门店与内嵌会话基础检查

- 最终完整源码 **223 passed / 86.23 秒**，记录 `reports/pytest-v050-final.txt`、`reports/tests-v050-final.xml`。内嵌主页面/辅助页创建关闭、CDP 断开重连、正常重启后合成会话 Cookie 保存、原 Chrome 会话恢复、同城合并与跨城优先级、49 家门店目录、真实取货邮箱不误判登录均通过。
- 安装版 build 13 构建、安装与严格签名核验成功；内嵌离线启动与合成流程提交各通过。首次 renderer 诊断发现 Qt 排队的空白导航会覆盖过早导航，已增加初始页面就绪检查，新建辅助页同样等待就绪。原生 Apple 登录结果仍与离线机制分开记录。

### v0.4.3 稳定性修复

- 源码集中检查 **201 passed / 79.31 秒**，记录 `reports/pytest-v043-release.txt`、`reports/tests-v043-release.xml`。包含有数据的表格辅助功能单元格读取、行数变化、页面焦点切换、关闭窗口和退出事件循环两种路径；工作线程真实启动并正常结束后才能销毁父窗口。首次新增夹具使用了 Python 绑定未暴露的 `tableInterface()`，修正为公开子对象查询；中间失败记录保留在 `pytest-v043-fixture-api.txt`，不计为通过。
- 崩溃证据：用户附件和本机报告共三个版本，均使用 Qt 6.8.3；两个在退出时进入 `QAccessibleCache::removeAccessibleElement` 后无效内存访问，一个在切换列表焦点时进入原生辅助功能通知后崩溃。诊断摘要 `reports/crash-diagnosis-v043.json` 仅保存在本机，不发布原始崩溃报告。
- 用户随后提供的 13:18 Python 报告包含 `WaitingWorker` 与 `QThread::~QThread`，对应上述首次测试夹具接口错误后未及时清理测试线程导致的 `SIGABRT`，不是安装版应用的相同辅助功能崩溃。夹具已增加 `finally` 停止并等待线程、销毁窗口；修正后的两种退出路径和真实 GUI 入口检查均通过。后续验证同时检查应用和 Python 的新增崩溃记录。
- 已核对 Qt 官方 QTBUG-134784 修复与版本源码，升级 PySide6 / Qt 6.11.2。应用在事件循环退出时停止计时器、正常结束软件线程和浏览器，在 QApplication 仍存活时销毁主窗口。保留辅助功能、钥匙串、配置和订单锁。

### v0.4.2 集中修改与最终检查

- 最终集中检查 **198 passed / 74.55 秒**，记录 `reports/pytest-v042-release.txt`、`reports/tests-v042-release.xml`。安装版 **v0.4.2 / build 11** 构建、安装和严格 ad-hoc 签名核验成功；GUI 自检 `GUI_SMOKE_OK`，隔离本地端到端到达 `PAYMENT_PENDING`，合成订单 1 个、提交 1 次。证据 `build-v042.log`、`install-v042.log`、`packaged-gui-v042.log`、`packaged-e2e-v042.json`。
- 原生最终窗口已直接勾选并保存两项提前同意；界面截图包括两项选择及保存按钮。后四位表单没有证件类型输入，字段展开、按钮中文化；实际窗口已成功保存一个默认取货人。公开截图 [提前同意](images/privacy-ahead-v042.png)、[空白取货表单](images/pickup-v042.png) 均不含身份、登录参数或付款码。
- 最初取货修复全量 **180 passed / 69.29 秒**。取货表单当时新增 9 项回归检查，旧界面全部失败，修复后与相关检查合计 **61 passed / 1.33 秒**。证据 `pickup-profile-before-fix.txt`、`pickup-profile-fixed-v042.txt`。随后用户要求集中修改、最终统一验证；提前同意与界面调整后的结果另记。
- 中间安装版 **v0.4.2 / build 10** 构建、安装与严格 ad-hoc 签名核验成功。取货界面移除手填证件类型，后四位直接采用对应校验，不要求 18 位号码。编辑旧的完整证件只在保存时保留后四位，取消不改旧资料；粘贴长号码会报错，不静默保存前四位。最终提前同意版本为 build 11。
- 开售入口持续以五秒开始间隔尝试，无两分钟后减速及固定五分钟等待；实际队列、未完成导航、服务器等待和重复异常分开验证。一个页面中的多个失败资源只计一次异常回合。`541` 错误文档即使有页面标题也不会被视为已进入。
- 提前同意：付款页直接展示用户提供的当前 Apple 中国站声明，两项均选择并保存后，遇到相同可见声明自动勾选、继续，跨重启有效。版本和两项选择只保存在本机。整份可见声明、按钮之后新增段落、额外条款或选择变化时不自动同意；账户页不沿用，导入配置清除选择。另保留变化声明的当次选择弹窗；该临时确认绑定当前页面、两分钟内有效且仅使用一次。
- 重启官网试跑仅复用型号、配置、数量和总价全匹配的既有购物袋，不重复加购。真实购买仍要求人工处理已有购物袋；单次订单锁不变。

### v0.4.0 发布检查

- 最终全量源码 **145 passed / 64.21 秒**，记录 `reports/pytest-v040-final.txt`、`reports/tests-v040-final.xml`。新增配置切换场景在旧代码复现 AssertionError；修复后相关浏览器测试 **22 passed / 34.04 秒**。证据 `product-transition-before-fix-v040.txt`、`product-transition-after-fix-v040.txt`。
- 安装版 **v0.4.0 / build 8**，构建、安装和严格 ad-hoc 签名核验成功；GUI 自检 `GUI_SMOKE_OK`，打包本地端到端 PAYMENT_PENDING、合成订单 1 个、提交 1 次。证据 `build-v040-final.log`、`install-v040-final.log`、`packaged-gui-v040-final.log`、`packaged-e2e-v040-final.json`。本地检查使用独立临时数据目录。
- 已从正常官网公开购买页读取 Duo、17、17e、18 Pro、18 Pro Max 五个型号。Pro 与 Max 分别实际选择官网机型控件，再读取对应容量价格；目录资源为 `app/services/official_catalog.json`。仅读取公开配置，没有提交订单或加载取货身份。
- 安装版真实 Apple 中国站登录在 **2026-10-12 01:16:12 UTC** 由软件确认成功；使用用户既有账号授权完成一次正常密码提交，没有保存密码、复制日常浏览器资料、重发验证码或创建订单。正常关闭、更新 build 8 后，软件在 **01:24:51 UTC** 再次确认已登录，没有再提交密码。这证明本次重启恢复成功，不能据此保证全天有效。
- 实际安装版现货试跑暴露了通用机型页在选容量后切换阶段的问题：原「继续」消失，程序仍等待原按钮，产生 AssertionError；当时未加购、结账或提交订单。新增完全离线的页面切换夹具先复现相同失败，再修复为观察当前「继续」或「添加到购物袋」阶段，不重复商品操作。失败与后续结果分别保留。
- **最终安装版真实官网试跑**：iPhone 17 白色 256GB、1 件、官网 RMB 6,799；4.76 秒完成商品选择，18.85 秒完成南京东路自提选择、加购及购物袋验证，20.27 秒进入结账并因 Apple 数据隐私知情同意书暂停。没有关键 541、新身份填写或新订单提交。正常浏览器与该结账页保留。点击软件「继续检查」而官网尚未同意时仍然暂停；未替用户签署。证据 `reports/apple-website-v040-final.json`、`reports/ui-v040-trial-native.png`。
- 因本轮协议需要本人处理，后续取货人、银行选择、最终核对尚未由这次安装版试跑确认。早期 Codex 浏览器的完整订单结果不替代这一缺口；Duo 尚未开售的结账也未验证。
- 原生窗口检查商品机型切换、中文选择、招商银行 24 期及关闭的授权开关。界面没有手填 URL、技术配置表或金额上限。截图 `ui-v040-product-native.png`、`ui-v040-payment-native.png`，详见 [UI_DESIGN.md](UI_DESIGN.md)。
- 「使用官网开售时间」在实际窗口把 Duo 的北京时间 **2026-10-16 20:00** 换算为柏林时间 **14:00**，保留原显示时区。检查后恢复在售商品试跑的原配置，没有启动预约购买。
- 维护、503 + Retry-After、队列自行放行、加载未完成、旧账户页、关键 429/541、持久冷却、价格确认范围、部分目录更新保留选择，以及单次订单锁均有独立验证。没有对 Apple 网站做高频压力实验；5 秒开售检查节奏是产品默认值，不是官网公布的最优间隔。

### v0.3.0 历史结果

- 全量源码 **130 passed / 59.05 秒**，记录 `reports/pytest-v030-final.txt` 和 `reports/tests-v030-final.xml`。之后根据实际官网地区面板完善夹具，旧代码的两种入口均失败；修复后库存相关 **9 passed / 14.48 秒**，记录 `remembered-region-before-fix.txt`、`remembered-region-after-fix.txt`。
- 正式 Chrome 的软件私有目录、本机连接、正常退出、有到期时间的 Cookie 重启恢复已用保留域名的离线页面验证；未读日常浏览器目录或导出 Cookie。GUI 中文颜色/容量仍收集为准确官网原值。
- 在软件实际 `BrowserSession` 中两次读取同一 iPhone 17 白色 256GB：每次 **12 家门店**，南京东路 R359、环贸 iapm R401 都显示今天可取货；间隔 60 秒，第二次正常查询用时 1.60 秒，总计 70.61 秒。没有 541、身份加载、加购、结账或新订单。证据 `reports/apple-stock-repeat-20261012.json/.png`，所有请求均由正常可见区域控件产生。
- 第一次接入后的模态骨架、第二次选择器保留区县页均在真实官网暴露并修复；失败时没有将旧库存更新时间写成成功。早期失败记录保留，不覆盖为成功。
- 安装版 **v0.3.0 / build 6**；构建及安装通过严格 ad-hoc 签名核验。GUI 自检为 `GUI_SMOKE_OK`；本地端到端为 PAYMENT_PENDING、合成订单 1 个、提交 1 次。证据 `build-v030-release.log`、`install-v030-release.log`、`packaged-gui-v030-release.log`、`packaged-e2e-v030-release.json`；后两项使用独立临时资料目录。
- 真实 macOS 窗口已检查帮助、准备清单、编辑配置和侧栏同步；安装版「诊断… → 检查环境」识别正式 Chrome 独立会话。完整视觉稿、窗口与尺寸比较见 [UI_DESIGN.md](UI_DESIGN.md)。
- 安装版软件打开中国站 Apple 登录页，使用用户既有授权完成一次密码提交，正常到达双重认证页面。本人随后完成验证；软件在 **2026-10-12 00:18:52 UTC** 确认登录。关闭了 Chrome 密码保存提示，没有保存密码、重发验证码或创建新订单。证据 `reports/apple-login-v030.json`。

这些结果证明当前本机软件可进行实际供货读取及正常认证流程，不证明未来限流不会发生、登录永久有效或首发一定下单成功。后续关键拒绝仍执行等待与暂停。

- macOS 15.7.1，Apple Silicon arm64，Python 3.13.3。
- PySide6 6.8.3，Playwright 1.63.0，keyring 25.7.0。
- Chromium 153.0.8010.12，Playwright 管理的独立缓存。
- 源码 GUI 启动、Retina 截图、六页表单与滚动界面已检查。
- macOS Keychain 的一次临时写入/读取/删除已成功；探针不含真实用户身份，已删除。
- 正式打包与安装的签名、GUI 和 Chromium 驱动测试均已通过，详见下方记录。

## 自动化测试

v0.2.2 `pytest -q`：**116 passed，47.26 秒**。此前 v0.2.1 为 105 passed / 43.34 秒，v0.2 为 97 passed，v0.1 为 80 passed。

覆盖配置严格校验、Apple 域名边界、金额/数量/商品/门店/付款授权、身份证后四位、日志脱敏、状态机、进程互斥、20 路竞争下唯一订单锁、已确认/未知订单重启不重下、停止响应、本地真实浏览器异常场景，以及基于实测 DOM 的图片银行标签/24 期/延迟 AppleCare 控件。

新增覆盖：

- 同一打开浏览器跨命令保持 session Cookie，空闲期间仍持有进程互斥锁；不会为下一任务重启浏览器。
- 非关键图片 403 与真正 429、关键购买请求拒绝分别处理，等待时间遵守 Retry-After。
- 付款码只裁取唯一可见图片；图片大小不包括联系人区；非 Apple 域名和多个二维码拒绝。
- 有已知提交时刻时按真实页面相对时限计算；未知时刻不编造倒计时；切换订单后重新打开不延长期限。
- 付款图片路径禁止目录穿越，存储目录 700、文件 600。
- 开启自动授权也不能越过未确认门店；在离线确认页控件测试中只执行一次提交点击。
- 打包版使用正常 Playwright 浏览器缓存，保留用户显式指定的缓存位置。
- 账户维护或暂时连接失败不关闭现有会话，也不把无法复核误报为已登录。
- Qt 付款窗口在期限到达后禁止保存与复制图片；创建新购买计划保留旧订单锁并要求重新授权。
- v0.2：停止任务后同一 Chromium 会话 Cookie 和进程锁仍保留，下一命令可使用；关闭会话才结束浏览器。
- v0.2：柏林/北京时间切换保留同一预约时刻；夏令时歧义、已过预约阻止新真实购买，旧提交仍可核对。
- v0.2：准备清单区分缺失、待检查和人工确认；真实运行的身份授权要求不影响人工填写的官网试跑。
- v0.2：限流倒计时期间禁止继续与新的浏览器请求；活跃任务参数与后续编辑隔离。
- v0.2：门店变化按商品配置分别去重保存；所选历史订单打开自身付款码，保持原有时限。
- v0.2：提交之后停止、服务器返回不明结果时订单锁为 UNKNOWN；恢复核对不产生第二次提交。
- v0.2.1：记住门店名后的查询入口、只有城市名的区域按钮、同名省份选项、连续查询从无货变有货均通过真实 Chromium 离线控件测试。每次都观察区域动作触发的浏览器请求完成后读取新列表。
- v0.2.1：未知查询控件、关键 HTTP 429/541、弹窗未打开时的拒绝不能把旧结果当作新的库存；限制期间不继续发查询。
- v0.2.1：无货时同一准备后的浏览器只检查供货并逐步退避；未有允许的门店前不开始结账。
- v0.2.1：macOS 辅助功能改变侧栏选择、鼠标点击与准备清单内部跳转均保持实际页面和选择一致。修复前已复现选中“运行记录”却仍显示商品页，记录为 `reports/navigation-before-fix.txt`。
- v0.2.2：登录恢复中的后台 541、临时账户标签页后台拒绝、延迟出现注销链接、持续加载的未知状态、错误域名及停止时保留会话全部通过。观察登录返回不会再发密码提交；敏感 URL 参数不进入诊断。
- v0.2.2：后台登录拒绝在人工等待期间立刻显示到控制台，等待期限内不能继续。
- v0.2.2：慢延迟脚本不能使公开页准备一直阻塞；首个连接错误产生的空白页或浏览器内部网络错误页可重试正常目标。导航失败不允许跳过关键 541 等待；限制期间没有第二个 GET。
- 上述开放检查修复前为 3 项失败；追加首次连接失败边界后再复现 1 项失败，修复后 4 项通过。证据为 `reports/opening-reading-before-fix.txt`、`reports/opening-recovery-before-fix.txt` 与 `reports/opening-reading-after-fix.txt`。

本地端到端测试场景：首店有货、次店有货、全部无货、库存变化、缺失选项、付款不可用、身份不全、维护、网络超时、提交超时、延迟成功、下单失败、限流、停止。全部使用合成身份和合成订单。

## 真正 Apple 官网验证

本次经用户知情授权完成 **1 次**真实下单点击，官网显示真实订单号、等待付款、掌上生活付款码和 30 分钟付款要求。产品是 iPhone 17 白色 256GB，1 件，RMB 6,799，招商银行 24 期；没有执行银行支付。

- 商品配置、实测价格与购物袋数量：已验证。
- 官网自提查询及南京东路选择：已验证，未宣称付款前已锁定库存。
- 登录与取货人字段、个人发票：已验证；账户由用户本人登录。
- 图片形式的银行标签、实际可用 24 期及最终核对：已验证。
- Review → status → thankyou、真实订单号与待付款状态：已验证。
- 唯一真实二维码裁取，并通过本机 Apple Vision QR 检测：已验证。
- 操作前持久单次提交锁、提交期间结果未知锁、确认后锁：本次实际记录已保存。

以上完整实测通过 Codex 右侧浏览器完成。桌面软件具有独立浏览器。早期软件公开商品试跑在 4.59 秒到达购买页可访问阶段，随后关键购买 fetch 请求返回 HTTP 541，5.81 秒暂停并要求至少等待 60 秒。该记录当时没有加载身份或创建订单；v0.4.0 的最新结果在本页首节。不能声称软件完整真实订单自动执行已经通过验证。

公开截图：`reports/order-confirmation.jpg` 只保留确认标题与付款要求；不含二维码支付令牌、联系人或证件字段。真实付款图片只在 Application Support 的私有付款目录保存。

### 2026-10-11 v0.2.1 供货查询专项

- 右侧 Codex 浏览器：打开 iPhone 17 白色 256GB 商品，发现供货按钮已显示记住的“Apple 南京东路”；弹窗区域按钮仅名“上海”。正常切换省份/区县后旧门店列表消失、新列表出现，并显示 12 家附近门店的可选情况。没有加购、进入结账或创建订单。公开截图 `reports/apple-stock-refresh.png`。
- 完全离线的真实 Chromium 测试：旧实现的两种入口场景均失败；修复后连续两次各触发一个新读取请求，第二次返回可取货状态；未调用真实 Apple 服务。修复前记录 `reports/stock-reading-before-fix.txt`。
- 软件实际 Chromium，正常显示窗口，使用其持久会话目录、仅公开商品设置：通常准备在 5.90 秒遇关键 fetch HTTP 541，0 次成功供货读取、0 次加购、0 次结账、0 个订单。报告 `reports/apple-stock-software.json`。
- 同一软件浏览器配置的等待完整 `load` 对比：商品配置完成，进入供货步骤仍遇关键 fetch HTTP 541，16.89 秒结束，0 次成功读取。报告 `reports/apple-stock-load-experiment.json`。拒绝时尚未打开弹窗，使当时的超时掩盖限制原因；随后已修复这个错误分类，并通过离线复现测试。
- 两次官网检查启动相隔约 113 秒，不通过高频重试验证第三方文档所称的阈值。等待加载未解决官网拒绝；不能认定“只是无头模式”，也不能由该 PDF 确定本机具体的 Cookie / 挑战故障原因。
- 最终安装版已通过“登录并保持会话”正常打开 Apple Store 登录页面，并进入人工登录等待。右侧 Codex 浏览器的账户不会复制到软件；登录后的供货对照尚待完成。这只证明登录页可访问，不证明购买关键请求已经恢复。

因此，本轮证明了当前官网控件的适配缺陷与代码修复契约，**仍没有证明独立软件已经成功读取官网库存或完成真实自动下单**。

### 2026-10-11 v0.2.2 登录与普通浏览器对照

- 实际软件浏览器的 Apple Store 登录地址为 `secure10.www.apple.com.cn`，没有切换德国站。用户授权后代理仅进行一次凭据提交；没有出现能确认账户登录的正常账户页。
- 可见控制台报告 `/shop/signIn/idms/…` HTTP 541 以及 HTML 不能解析为 JSON，页面一直显示“正在载入”。这是实际登录返回故障证据，未将 HTTP 541 的服务器原因判为密码、IP 或某个 Cookie。脱敏记录 `reports/apple-login-diagnostic.json`。
- 21:38 UTC 本机系统 HTTP/HTTPS/SOCKS/自动代理均关闭；公共诊断服务返回国家 DE。只保留国家与配置开关，不保留 IP。它不能证明 Apple 或浏览器扩展的具体路由；记录 `reports/network-diagnostic.json`。
- 正式 Chrome 的临时独立 Playwright 目录：20.08 秒后供货 fetch HTTP 541，0 次成功查询。允许 Service Worker 的对照为 18.20 秒，同样 541。报告 `reports/apple-stock-chrome-compat.json`、`reports/apple-stock-sw-compat.json`；两次均无身份读取、加购或结账，不复制日常浏览器配置。
- 普通 Chrome 正常窗口通过可见控件选择上海/黄浦区，取得 12 家门店供货，南京东路与上海环贸 iapm 显示“今天可取货”。没有选择继续、加购或创建新订单。证据 `reports/apple-stock-normal-chrome.json/.png`；检查标签页已关闭。
- 正常窗口使用现有配置，自动化对照使用新独立配置；启动方式、扩展、会话与时刻仍是变量，不能把结果当作已找到单一根因。当前 **独立软件的真实库存读取和登录仍未确认成功**，完整自动消费验证仍未完成。

### v0.2.2 界面检查

- 控制台由大号时钟卡片改为紧凑状态栏，使用浅灰侧栏、系统字体、普通蓝色主按钮；诊断与开发异常测试移入独立窗口。
- 新配置默认北京时间；已有时区配置与预约时刻保留。时区选择明确仅用于显示和预约换算，官网固定中国大陆。
- 源码界面截图检查商品、门店、付款设置、控制台和记录页。1120×780 默认窗口同时呈现预约、登录、状态与日志；950×650 最小窗口允许纵向滚动，没有横向截断。
- 截图 `reports/gui-v022-console.png`、`reports/gui-v022-product.png` 和 `reports/gui-v022-minimum.png` 使用只有公开商品的临时配置，未访问官网或加载身份；它们是界面证据，不是官网验证。

## 实际限制与待验证

- 当前实测 Review 不显示门店名，取货日期在付款后确定。自动化在此暂停，仍须人工核对；不会以先前的库存查询替代最终验证。
- 真实自动提交适配限定招商银行 24 期，且必须通过完整最终摘要校验。代码的单次提交/确认契约经过离线控件测试，尚未用桌面软件独立 Chromium 再次实际消费测试。
- Duo 首发维护页、开放时间、拥堵/限流、预购准备入口及正式订单：待发售期间验证。
- 当前产品目录按用户要求仅展示 iPhone 17、18 系列与 Duo。此前全品类通用链接能力不作为本轮交付范围；未知必选项仍会暂停。
- 其他银行/支付宝/微信/Apple Pay 的最终订单与二维码格式：待实际出现的结账页验证；无二维码时应按官网付款指引处理。
- 前一天登录持续到开售的真实 24 小时 Apple 会话寿命：待长期验证。已验证本软件跨任务共用同一浏览器，Apple 仍可使会话过期或要求认证。
- 重启后的旧订单关联：保留锁，需人工在 Apple 正常订单页面核对；不会猜测订单属于本次意图。
- 支付完成、取货通知、银行分期授权：由用户完成，未验证或代操作。
- macOS 通知已接入 QSystemTrayIcon；是否显示由系统通知设置决定，未声明绕过用户设置。

## 本机应用打包验证

最终安装应用位于 `~/Applications/iPhone AutoBuyer.app`。

- 安装后的应用通过 `codesign --verify --deep --strict`；本机 ad-hoc 签名，arm64。
- 安装版 GUI 自检返回 `GUI_SMOKE_OK`；随后用 macOS 原生界面再次打开并点击「检查环境」，显示 Chromium 已安装、macOS Keychain 后端可用。
- 直接运行安装版可执行文件完成 Chromium 本地端到端测试，状态到达 PAYMENT_PENDING；合成订单 1 个、提交次数 1 次。该测试验证打包驱动和流程，不是 Apple 订单证据。
- 原生付款窗口读取本次真实订单图片记录；期限已过后显示到期提示，隐藏二维码，保存/复制图片按钮不可用。
- v0.2.1 最终安装版通过 GUI 自检与独立 Chromium 本地端到端检查：`GUI_SMOKE_OK`；PAYMENT_PENDING、合成订单 1 个、提交次数 1 次。
- 更新后用 macOS 原生界面切换“运行记录”及“控制台”，选择状态与实际内容一致；“订单与付款码”显示真实历史订单尾号 3443，状态为“已创建 · 付款待官网核对”，明确显示付款码已过记录时限。
- 原生窗口再次打开所选订单的码，付款图片已隐藏、保存与复制图片均禁用。公开截图 `reports/native-payment-v021.png` 不含付款码令牌或联系人资料。
- 最终安装版再次点击“检查环境”成功，显示 Chromium 已安装、macOS Keychain 后端可用；准备清单反映该检查结果，公开截图为 `reports/native-preparation-v021.png`。
- v0.1 测试报告为 `reports/pytest.txt`、`reports/tests.xml`、`reports/packaged-e2e.json`；GUI 截图为 `reports/packaged-gui.png` 与 `reports/native-gui.png`。
- v0.2 源码报告为 `reports/pytest-v02.txt` 与 `reports/tests-v02.xml`；安装版 GUI 与 Chromium 本地端到端均通过，报告 `reports/packaged-e2e-v02.json`，截图 `reports/packaged-gui-v02.png`。
- v0.2.1 源码报告为 `reports/pytest-v021.txt` 与 `reports/tests-v021.xml`；安装版报告为 `reports/packaged-e2e-v021.json`、`reports/packaged-gui-v021.log`，GUI 截图为 `reports/packaged-gui-v021.png`。构建和安装记录为 `reports/build-v021.log`、`reports/install-v021.log`。所有报告、日志和截图均已被 Git 忽略。
- v0.2.2 全量源码为 116 passed / 47.26 秒，报告 `reports/pytest-v022.txt`、`reports/tests-v022.xml`。最后校正文案后 GUI 相关 7 项再次通过，未扩大或重复官网查询。
- v0.2.2 安装版签名与版本 0.2.2 / build 4 已核对；GUI 自检通过，本地打包浏览器端到端为 PAYMENT_PENDING、合成订单 1 个、提交 1 次。报告 `reports/packaged-gui-v022.log`、`reports/packaged-e2e-v022.json`；这些使用独立临时数据目录。
- 用真实 macOS 窗口打开「诊断…」并执行环境检查，显示 Chromium 已安装、macOS Keychain 后端可用。侧栏切换能显示实际记录页；原有订单尾号 3443 与付款码原期限均保留，记录 `reports/native-records-v022.png` 不包含二维码图片。
- 最终文案包再次通过安装后的 GUI 自检及严格签名核验。最新真实窗口截图 `reports/native-gui-v022.png` 已逐项查看字体、侧栏、按钮、原生复选框、预约区和日志区域；最终程序保持打开，未启动新的购买任务。
