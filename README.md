# FamilyShelf

Steam 家庭贡献、好友游戏库价值与 SteamDB 价格榜单。

看板：https://lrgkat.github.io/familyshelf/

后端：https://familyshelf-api.onrender.com/health

前端使用 GitHub Pages，Python API 和临时 Steam 登录浏览器运行在 Render。
GitHub Pages 仅提供静态页面；把 Python 改写成 JavaScript 并不能让 Pages 运行后端。

## 功能与数据口径

- 家庭可共享游戏、独有收藏、按持有人数均摊的价值贡献。
- 好友公开游戏库、价格覆盖率、游玩时间、对比和共同收藏。
- Steam 商店原价/当前价估值；未返回报价保持未知，不当作免费。
- SteamDB 独立榜单支持手动录入、CSV/JSON 导入。没有抓取 SteamDB，也不代表实际消费。
- 支持手机扫码与手动令牌两种 Steam 登录方式；等待二维码时也可切换到令牌登录。
- 令牌登录支持完整 `webapi_token`、Bearer 格式或官方页面 JSON；先校验格式与有效期，再由 Steam API 验证权限并同步家庭库。输入框默认隐藏，提交或关闭后清空。
- 扫码可自动读取账号已有的 API Key。令牌登录只同步家庭库；好友榜单需填写 SteamID 与 API Key，或改用扫码。尚未申请 Key 时需在官方页面自行申请。

## 公开部署

1. Render 通过仓库根目录的 `render.yaml` 创建免费 Docker Web Service。
2. 将 `web/config.js` 中的 `apiBase` 设为 Render 返回的实际 HTTPS 服务地址。
3. 将 web 内容复制到 docs，GitHub Pages 从 main 分支 /docs 目录发布。
4. 后端 `ALLOWED_ORIGINS` 只填写实际前端的 origin（不含路径），多个 origin 以逗号分隔。

免费服务会休眠，冷启动可能需要约一分钟。默认最多 12 个临时访客会话、1 个并行扫码浏览器，不适合高并发。
Steam 可能限制机房 IP 或请求频率，扫码和上游 API 的可用性以实际请求为准。

## 隐私与会话

每次打开页面建立独立随机会话。令牌、Key、库快照不写入应用数据文件或浏览器存储，不会包含在公开仓库中。
每个访客的库与价格数据独立。浏览器仅使用临时上下文；结束登录后关闭，不导出 cookie。
闲置 20 分钟或创建满 3 小时后会清除服务端会话，网页刷新需重新登录。
公开部署时应信任自己选择的后端运营方；扫码将建立云端 Steam 会话。请在 Steam 手机应用核对此次登录。

## 本地开发云端版本

```sh
pip install -r backend/requirements.txt
python -m playwright install chromium
ALLOWED_ORIGINS=http://127.0.0.1:8766 PORT=10000 python backend/cloud_server.py
```

将开发用 config.js 的 apiBase 设置为 `http://127.0.0.1:10000`，在 web 目录执行 `python -m http.server 8766 --bind 127.0.0.1`。
原有本地看板与本目录的云端部署相互独立。

### 统一登录和导航

扫码或令牌登录后，复用同一会话同步家庭库、自动补全家庭估值，并加载全部好友。好友库自动同步与估值不再要求先切换到好友页面。扫码会在现有登录浏览器中尝试读取已有 Key；没有 Key 时使用同一 access_token，若 Steam 拒绝权限，需补充 Key（无需再次登录）。令牌登录不会启动浏览器。

SteamDB 共用家庭与好友名单，已有同币种价格自动排名。SteamDB 无可用的公开计算器查询 API，服务器请求会遇到验证拦截，因此金额仍需录入 / 导入，不宣称自动读取价格或真实消费。

侧栏仅保留家庭贡献、好友收藏、SteamDB价格。家庭功能放在家庭页内导航，好友功能放在好友页内导航，SteamDB 不再作为好友子标签重复显示。所有认证和数据仍仅在会话内存中保留。

回归检查：`python3 tests/check_unified_login.py`；安装 Playwright 和 Chromium 后可运行 `python3 tests/check_unified_ui.py`，覆盖停留家庭页的后台同步、205 人分批估值、导航切换与移动端布局。测试仅使用合成数据。
