# Windows 便携版使用

适用 Windows 11 x64。把 ZIP **完整解压**到本机中文或含空格的目录，再双击 `iPhone AutoBuyer.exe`。exe 必须与 `_internal` 保持相邻，不需要安装 Python 或 Node.js。当前版本未签名；若本机策略拦截，请记录具体提示。

内置浏览器随软件提供。独立 Chrome 模式使用本机正式 Google Chrome；未安装时可在软件中安装 Chromium。浏览器缓存按当前 Windows 用户保存。

第一次打开后，在「官网浏览器」填写 Apple 账号并正常完成官网验证，填写取货人，保存商品和允许门店。Windows 数据保存在 `%LOCALAPPDATA%\Apple AutoBuyer`，退出重开后自动载入本机加密资料；不要复制其他电脑的密钥或浏览器资料。

先用「官网模拟下单」检查商品、门店和付款方案，到核对页停止。真实购买需要单独开启自动提交并运行「开始抢购」。验证码和实际支付由本人完成；未知提交结果的订单锁会保留。

复用已有购物袋时，若官网尚未选择地区，build 46 会按首选允许门店的省市、区县设置地区，再核对门店编号、供货和优先级。地区保存失败会暂停并保留购物袋。若显示「需要手机验证」，这已是取货信息阶段；完成官网验证后再点「继续检查」。

会话开启时关闭窗口会隐藏到托盘，点击托盘图标恢复。托盘菜单提供「显示窗口」「关闭会话」「退出软件」。彻底退出使用托盘菜单。

## 离线自检

解压目录中的两个 PowerShell 脚本必须保持相邻。以下命令不会访问 Apple，报告及合成数据写入临时目录：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\self-test.ps1" -Distribution "."
```

如需验证独立 Chromium 与合成订单，并下载本机浏览器缓存：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\self-test.ps1" -Distribution "." -InstallChromium
```

自检通过只能确认环境、界面、内置渲染器及合成购买流程。真实官网登录、库存、付款选项和最终下单必须分别验收。
