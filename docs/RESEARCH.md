# 官网购买与首发策略研究

更新：2026-10-12。以下公开资料与本次实际页面观察分开记录。

## 官方资料

- [Apple 账户购买帮助](https://www.apple.com.cn/shop/help/your_account)：账户支持保存购物袋、查看订单；未公布本站登录一定能维持多久。因此提前登录有用，但不能承诺隔天绝不要求重新登录。
- [Apple 2026 年预购准备说明](https://www.apple.com.cn/newsroom/2026/09/get-ready-to-experience-iphone-18-pro-the-new-apple-watch-lineup-and-airpods-5/)：该次机型预购支持先选机型、配件、分期、折抵并保存购物袋，以减少开放时操作。仅说明该场发布提供了此功能，不能推断所有未来商品都提供相同入口。
- [Duo 当前购买页](https://www.apple.com.cn/shop/buy-iphone/iphone-duo)：当前显示 10 月 16 日晚 8 点接受预购、10 月 23 日发售。页面公开价格和配置不等于服务器已经允许创建订单。
- [自提购买帮助](https://www.apple.com.cn/shop/help/shipping_delivery)：商品配置后查询自提，结账选择「我要取货」。必须服从最终取货通知及证件要求。
- [付款购买帮助](https://www.apple.com.cn/shop/help/payments)：实际方式、银行及分期方案仍须在结账页确认，不预设所有商品都显示所有方式。
- [Playwright 持久浏览器上下文](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context)：独立 user data directory 保存正常会话状态，关闭该 context 会关闭浏览器，同一目录不能被多个进程同时占用。
- [Playwright 上游关于会话 Cookie 跨重启的报告](https://github.com/microsoft/playwright-python/issues/2869)：报告中无到期时间的会话 Cookie 在 Chromium 重启后丢失。它不是 Apple 登录失效时长的保证；工程因此优先保持同一浏览器进程，而不是反复关闭/重开或导出 Cookie。
- [MDN Retry-After](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Retry-After)：服务器可以用秒数或 HTTP 日期指定等待时间，软件解析并遵守该值。
- [Chrome 支持的本机调试连接](https://developer.chrome.com/docs/devtools/remote-debugging/local-server)：Chrome 提供指定端口连接另一实例的正常开发方式。当前软件只连接自己启动的本机实例。
- [Chrome 调试资料目录要求](https://developer.chrome.com/blog/remote-debugging-port)：新版本不允许使用默认资料目录进行此连接；独立目录隔离于日常 Chrome。软件使用自己的 `chrome-profile/`，不复制个人目录。
- [Playwright CDP 连接](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp)：可连接现有 Chromium 系浏览器及其默认 context；`no_defaults` 保留浏览器的正常默认设置。该连接功能的兼容性有限，实际购物步骤仍需逐项验证。

参考研究包括 [woke/apple-store](https://github.com/woke/apple-store)、[samblighty/apple-pickup-stock-monitor](https://github.com/samblighty/apple-pickup-stock-monitor) 的库存监控思路。库存监控项目不能证明首发购买或订单提交成功。本工程不沿用非公开下单接口、并发账户、Cookie 导出或反机器人绕过方法。

## 采用的策略与工程判断

### v0.4.0：刷新与等待的区别

用户强调首发前拥堵不应被当成封禁。查阅的[首发讨论](https://www.reddit.com/r/apple/comments/16j7m42/apple_store_down_ahead_of_iphone_15_preorders/)同时包含连续刷新后进入、进入延迟和结账超时；另一[首发讨论](https://www.reddit.com/r/apple/comments/1ffqc4v/apple_store_down_ahead_of_iphone_16_preorders/)包含 App 自动刷新与重开的相反经验。它们是未经控制的个人经历，不是某个刷新频率更优的实验。App 的自动刷新也不能直接推到网站。

采用页面状态策略：完成加载的维护页继续有节奏检查；正在进行的请求先观察，真正排队的页面保留等待；普通 503 临时繁忙不会标为 541 或 IP 封禁。服务器指定 Retry-After 时至少等待其期限。[RFC 9110 10.2.3](https://httpwg.org/specs/rfc9110.html#field.retry-after)允许秒数与 HTTP 日期；[RFC 6585 4](https://www.rfc-editor.org/rfc/rfc6585#section-4)定义 429，但未规定按 IP、账户或 Cookie 的哪一种计数。541 是当前观察到的非标准错误，原因仍未知。

平时默认 10 秒起、逐步到 60 秒；预约开售后前两分钟采用 5 秒完成后间隔。队列和未完成导航只看页面状态，不额外发送刷新。关键拒绝保守暂停至少 5 分钟，重复回合加长并保存本机期限。上述数值是可调整的工程默认值，没有高频攻击官网做压力实验，也不能保证首发一定进入或永不受限。加购和订单提交不套用浏览 GET 的重复检查，符合 [RFC 9110 的非幂等请求重试限制](https://httpwg.org/specs/rfc9110.html#idempotent.methods)。

下面 1–8 为早期实现记录；当前节奏以本节与代码为准。

1. 前一天在软件浏览器登录，配置商品、候选门店和付款方式，优先完成官网提供的预购准备。
2. 同一浏览器进程跨任务保持，减少重新登录、重建浏览器和重复下载资源的开销。是否保持登录最终由 Apple 决定。
3. 开售前检查登录。前一天一直刷新账户不是确保会话有效的办法，因此不做无意义的账户心跳请求。
4. 开售时立即刷新已有商品页，维护初期按默认 5 秒检查；12 次后逐步退避至默认 60 秒上限。这个频率是工程默认值，不是 Apple 宣布的最佳抢购参数，也不保证某个刷新时点必然成功。
5. 普通连接超时允许在开放检测阶段继续，避免一次失败终止整个计划；结账变更、加购和提交不套用这种盲目重试。
6. HTTP 429、关键访问拒绝、安全验证暂停。非关键图片、字体或分析资源失败单独记录，不把任何一个资源 403 都宣称为整个账户被限制。
7. 商品配置、门店可选、订单编号和完成付款是不同证据。一次点击后只核对确认页，不能为了加载慢再点击下单。
8. 官方 App 可作为本人操作的备用渠道；多个渠道同时提交可能产生重复订单，软件不会自动进行跨渠道并发下单。

## 本次真实观察

iPhone 17 白色 256GB，数量 1，RMB 6,799；上海南京东路为自提目标，上海环贸 iapm 为候选。路径为商品 → 查看自提 → 加购 → 购物袋 → Fulfillment → PickupContact → Billing → Review → status → thankyou。

- 登录后取货人表单接受姓氏、名字、邮箱、手机和证件后四位；访客路径曾出现额外手机号验证及数据隐私知情同意书。
- Billing 的银行标签为图片 alt，选择招商银行后实际显示 1/3/6/12/24 期；检查订单按钮为「检查订单」。
- Review 确認商品、数量、RMB 6,799、招商银行以及 24 个月；其取货日期显示「待付款完成后确定」，不显示门店名称。因此程序不会把先前的自提选择当作最终日期保证。
- 在用户知情授权后只点击一次「立即下单」，等待官网处理；thankyou 显示真实订单号、掌上生活二维码和「请在 30 分钟内完成付款」。这次 30 分钟是实际页面观察，不是所有 Apple 订单的固定期限。
- 真实二维码截图经本机 Apple Vision 成功识别为一个 QR；未输出/保存解码的付款链接。
- 2026-10-11 v0.2.2 的独立桌面软件试跑可打开商品页，关键购买 fetch 返回 HTTP 541，按当时至少 60 秒等待暂停。后续 v0.3.0 已恢复实际供货读取及正常认证；当前结果按 [验证清单](VERIFICATION.md) 记录，不覆盖早期失败证据。

## v0.2.2：同机浏览器与登录对照

以下全部在 2026-10-11 本机完成。公开商品试验没有加购、进入结账、提交新订单或银行付款；没有输出 IP 地址、Cookie、密码、验证码或登录 URL 参数。

| 对照 | 结果 | 证据能说明什么 |
|---|---|---|
| 软件独立 Chrome for Testing 登录中国站；获用户授权后一次填写并提交登录 | `/shop/signIn/idms/…` 返回 541；可见控制台同时报告 HTML 不能解析为 JSON，页面停留“正在载入” | 登录尚未确认；后台请求失败与前端解析错误可解释转圈，但服务器拒绝原因未知。`apple-login-diagnostic.json` |
| 系统代理设置与公共网络诊断，21:38 UTC | 系统 HTTP/HTTPS/SOCKS/自动代理均未启用；公共诊断服务返回国家 DE | 德国出口是线索；诊断服务与 Apple、浏览器扩展的路由可能不同，不能证明 Apple 的具体出口或拒绝原因。`network-diagnostic.json` |
| 已安装正式 Chrome，Playwright 启动、临时独立目录，21:34 UTC | 20.08 秒；供货 fetch 返回 541，成功读取 0 次 | 改用正式 Chrome 本身未解决问题；配置目录也变化，不能据此单独归因于浏览器版本。`apple-stock-chrome-compat.json` |
| 已安装正式 Chrome，正常窗口、可见控件操作 | 上海 12 家门店供货可读；南京东路与环贸 iapm 显示今天可取货 | 同机正常浏览器路径可用；这是公开库存，不是独立软件成功或首发性能证据。现有会话、扩展、启动方式和时间仍是变量。`apple-stock-normal-chrome.json/.png` |
| 正式 Chrome 独立自动化目录，允许 Service Worker，21:41 UTC | 18.20 秒；供货 fetch 仍返回 541，成功读取 0 次 | 允许这项正常网站功能没有恢复查询；不将这个尝试修改为生产配置。`apple-stock-sw-compat.json` |

网站始终是 `www.apple.com.cn` / `secure10.www.apple.com.cn`。界面的 Europe/Berlin 是时区，既不更换官网地区，也不更换网络出口。普通窗口成功读取与独立自动化失败之间尚缺控制变量完整的因果实验，不能宣称某一种 IP 或某一个 Cookie 一定解决问题。

## 进入策略：减少开售后的工作，再约束失败等待

### v0.3.0：本机 541 诊断与可用运行方式

2026-10-12 客户端本地日期；报告时间使用 UTC。本轮没有更换网络出口。原软件和几个独立 Chrome 对照的 `/shop/fulfillment-messages` 返回 541，正文是 127,974 字符的 HTML，标题为 `Page Not Found - Apple`。这解释了网页将响应当作 JSON 解析时一直转圈，不证明 Apple 的具体拒绝规则。

等待 70 秒、采用系统语言/时区、允许 Service Worker、先访问正常商店首页等单独对照没有恢复查询；不将这些尝试作为修复宣传。一次正常加购点击也得到文档 541，未确认加购、没有重复点击或新订单。证据为 `apple-natural-chrome-20261012.json`、`apple-settled-chrome-20261012.json`、`apple-native-chrome-settled-20261012.json`、`apple-store-entry-20261012.json`。

采用正式 Chrome 的受支持本机连接、独立资料目录及正常默认设置后，程序在 7.10 秒内读到 12 家门店，没有 541；南京东路 R359 和上海环贸 iapm R401 显示今天可取货。证据为 `apple-local-cdp-20261012.json/.png`。这是一个有效运行配置，不把其中某个端口数值说成 Apple 的通用通过条件，也不据此承诺所有未来限制均消失。它已接入软件的 `BrowserSession`，真实连续检测、登录和试跑继续按验证清单记录。

进一步检查实际页面发现两个独立的程序问题：供货弹窗刚出现时只是加载骨架，不能立即当成未知弹窗；第二次展开区域选择时停在区县页，必须先回到可见省份标签。只有取得新的浏览器响应才发布新的检测时间，沿用旧列表不会冒充刷新成功。

**选择直接商品入口**。软件提前打开保存的中国站商品链接，开售时刷新同一页，省去首页、导航目录和重新选机型的操作。链接必须是官网当前有效入口；可从已选配置的实际页面保存链接，不猜测未公开 SKU。官方如果提供「提前准备 / 保存购物袋」，先完成这个流程。[Apple 2026 年预购说明](https://www.apple.com.cn/newsroom/2026/09/get-ready-to-experience-iphone-18-pro-the-new-apple-watch-lineup-and-airpods-5/) 在“做好准备，升级设备”明确介绍了提前选机型、配件、分期和折抵后保存购物袋的做法；该场活动的准备截止时间不能套用到 Duo。

**保持同一进程与会话**。前一天完成软件浏览器登录，检查候选门店、取货人与付款配置；保持浏览器打开并接电。开售前约十分钟做一次账户检查，开售时仅刷新准备好的商品页。Apple 仍可要求新认证；保持浏览器不能保证 TCP 连接跨夜不失效，也不能让一条旧结账永久有效。软件不会为“保活”每几秒请求账户页。

**等待可操作页面，缩短慢资源造成的阻塞**。[Playwright 的导航说明](https://playwright.dev/python/docs/api/class-page#page-reload) 区分 DOMContentLoaded 与完整 load；商品操作不需要等所有图片、字体及后续资源加载。此前公开页导航沿用浏览器 30 秒默认超时；v0.2.2 对准备/刷新设 8 秒上限，仍需实际 DOM 与选项校验。慢脚本、初次连接失败、关键 541 在超时期间到达的三种情况已用完全拦截网络的真实浏览器验证：慢资源不会一直阻塞，连接错误可重试，限流期间不会再发刷新。这些是程序契约测试，不是发售当天测速。

**把维护与拒绝分开**。维护页或连接失败继续正常检查，默认在检查结束后等待 5 秒；持续未开放后逐步退避。关键 403/429/541 则暂停并按当前实现遵守对应 `Retry-After`。服务器明确要求等待时，以响应为准；没有官网依据把每秒刷新、多个标签页或某个每小时阈值称为“最快”。HTTP 标准也明确以该头部指示后续请求的等待时间，见 [Retry-After 文档](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Retry-After)。

**把“进入”与“下单成功”分别计时**。当前能验证正常浏览器供货可读，以及程序减少了不必要的导航等待；Duo 首发拥堵、开放偏差、账户认证与最终下单速度尚未发生。未来实际比较应记录首次可访问、配置就绪、候选门店可选和订单确认四个时间，不能只看首页加载快。官方 Apple Store app 可由本人作为备用入口；一个购买计划只在一个渠道提交，结果不明先核对订单。

当前 [Duo 官方发布说明](https://www.apple.com.cn/newsroom/2026/09/apple-unveils-iphone-duo/) 标注 2026-10-16 20:00 中国标准时间开始预购、10 月 23 日发售。软件会按时区换算等待，开售前仍应以该次官方页面为准。本轮没有进行高频官网压力测试，也没有足够证据宣布某个入口或刷新间隔在首发日绝对最快。
