# 沿用日常 Chrome 登录

v0.6.0 增加「日常 Chrome · 沿用现有登录」模式。它通过 Microsoft 官方 Playwright Extension 连接用户选择的一个 Apple 中国商店页面。软件的独立 Chrome 和内置浏览器仍可选择。

用户已确认旧版同一个独立 Chrome 窗口在开启回国 VPN 后重新输入密码成功。独立 Chrome 可继续作为购买会话；这条对照提示网络路径是关键变量，具体原因尚未细分。日常 Chrome 是另一个可选会话入口。回国 VPN 的切换曾同时中断 Codex 连接，软件的正常购买流程在本机执行，不依赖 Codex 在线；本软件不自动切换 VPN 或配置代理。

## 使用

1. 在日常 Chrome 普通窗口正常登录 Apple 中国商店。保留能够打开账户或订单列表的页面。
2. 在软件「官网浏览器」点「安装 Chrome 连接扩展…」。来源为 [Chrome Web Store 的 Playwright Extension](https://chromewebstore.google.com/detail/playwright-extension/mmlmfjhmonkocbjadbfplnigmagldckm)，开发者为 Microsoft。
3. 审阅 Chrome 的权限提示并自行决定安装。扩展使用 `debugger` 权限，连接页明确提示客户端将获得整个浏览器的调试访问，包括其他已登录会话、Cookie、标签页及窗口。浏览器授予的权限范围较大；程序业务只接入所选 Apple 页，这不缩小扩展自身的 Chrome 权限。
4. 点「接入日常 Chrome」，在扩展连接页仅选择一个 Apple 中国商店页面，并允许本次连接。软件先访问受保护的账户页面核实登录，再继续原有商品、门店与结账流程。
5. 保持这个连接到购买完成。点「关闭会话」只断开软件连接；日常 Chrome 和原有页面保留。Apple 仍可要求重新登录或验证码。

已选账户页或订单列表会使用其当前官方入口发起新的受保护读取；订单详情则使用唯一可见的同源「查看你的订单」链接。检查保留原页，不以购物袋姓名或缓存中的过期订单详情判定登录有效。

从 iPhone 商品页接入时，账户复核也沿该页购物袋菜单的实际账户入口进行，不直接换到通用账户地址。复核在临时页面进行，原商品页保持不变；受保护页面导航等待 15 秒，避免超过 5 秒的正常返回被提前关闭。未登录菜单仍须取得受保护页面的明确结果，不能凭记住的姓名确认登录。

软件不保存扩展的免确认令牌。每次新的连接由扩展显示选择和授权页。提前连接后，开售时复用同一个持续会话，无需重新授权。

## 实现边界

- 复用已固定的 Playwright 1.63.0 驱动及其官方扩展中继，不添加 npm 依赖，不实现另一套 Apple 登录接口。
- 本地应用通道只绑定 `127.0.0.1`，每次随机路径、随机端口、单个客户端。连接地址只在进程内传递，不保存进配置或日志。
- 默认不对日常 Chrome 启动额外的登录诊断采样；主动点击「记录登录问题」才开始，并单独保存脱敏的 daily_chrome 诊断。
- 不复制、导出或解密日常 Chrome 的 Cookie、密码、信任令牌。不改代理，不改认证请求，不更改浏览器指纹。
- 仅接受扩展提供的唯一中国商店页面；其他网站或多个页面会拒绝接入。软件的后续业务仍受 Apple 中国站 URL 校验及已有下单、付款授权约束。
- 不保证 Apple 永久信任会话，不保证发售访问成功，也不把购物袋显示姓名或「退出登录」当成账户认证成功。

## 外部线索

[icloud_photos_downloader #1256](https://github.com/icloud-photos-downloader/icloud_photos_downloader/issues/1256) 报告 `idmsa.apple.com.cn` 的 `signin/complete` 返回 HTML 503，而全球 `idmsa.apple.com` 能进行双重认证。其 iCloud 下载器修改认证根地址后恢复，但维护者指出不能未经地区验证就通用应用；评论也涉及 VPN 路由及不一致的版本结果。

这与本项目实测的中国商店 auth_init 200 JSON / auth_complete 503 HTML 具有相同失败位置，是区域认证路径的线索。它没有证明 Apple Store 的商店 OAuth 配置允许直接换域名。本项目因此保留官网自己选择的认证流程，接入已能正常登录的日常 Chrome 会话。

参考：[Playwright 官方扩展说明](https://github.com/microsoft/playwright/tree/main/packages/extension#readme)、[Chrome debugger API](https://developer.chrome.com/docs/extensions/reference/api/debugger)、[Chrome 136 默认配置的远程调试限制](https://developer.chrome.com/blog/remote-debugging-port)。

## 验证状态

运输协议、唯一页面校验、安装缺失提示、连接与关闭行为使用临时合成会话测试。用户自行安装并授权后的真实扩展连接已成立；只有软件通过受保护账户页面的新请求检查，才可标记实际登录复用成功，结果在 [验证清单](VERIFICATION.md) 更新。独立会话的中国区认证 503 尚未因本改动得到修复。
