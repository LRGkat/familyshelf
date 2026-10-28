# FamilyShelf

Steam 家庭贡献与好友游戏库价值看板。

看板：https://lrgkat.github.io/familyshelf/

后端：https://familyshelf-api.onrender.com/health

前端使用 GitHub Pages，Python API 和临时 Steam 登录浏览器运行在 Render。
GitHub Pages 仅提供静态页面；把 Python 改写成 JavaScript 并不能让 Pages 运行后端。

## 功能与数据口径

- 家庭可共享游戏、独有收藏、按持有人数均摊的价值贡献。
- 好友公开游戏库、价格覆盖率、游玩时间、对比和共同收藏。
- Steam 商店原价/当前价估值；未返回报价保持未知，不当作免费。
- 支持手机扫码与手动令牌两种 Steam 登录方式；等待二维码时也可切换到令牌登录。
- 令牌登录支持完整 `webapi_token`、Bearer 格式或官方页面 JSON；先校验格式与有效期，再由 Steam API 验证权限并同步家庭库。输入框默认隐藏，提交或关闭后清空。
- 扫码或令牌共用同一次认证，后端自动完成好友列表、全部公开游戏库和商店估值。扫码尝试读取账号已有 API Key；令牌通过 FriendsList / Player 服务读取。若 Steam 拒绝权限，页面显示错误，可补充 API Key 后重试，无需再次登录。

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

### 统一登录和自动同步

扫码或令牌登录后，复用同一会话同步家庭库、自动补全家庭估值，并同步全部好友游戏库和估值。整个好友流程由后端连续执行，不依赖当前页面、标签页的 Web Lock 或前端分批请求。好友加载后默认全选；“同步好友榜单”重新读取好友并更新全部游戏库，勾选同步和补全估值也由后端执行。最多支持 2001 位成员，价格按 100 人分批，复用会话内的商店价格缓存。

家庭库不可用时仍尝试好友同步。头像或昵称查询失败不阻断游戏库；私密库、旧快照、缺失价格单独标记。任务失败会显示具体原因与重试按钮；限流时停止请求并保留进度。未知价格不当作零元，估值不代表实际消费。

Steam 令牌好友接口的原始列表也包含群组关系，后端只同步有效的个人好友，跳过群组、邀请和无效记录。缺失资料不阻断游戏库；异常游戏时长只影响对应成员。未知错误显示失败步骤与诊断码，Render 日志只记录异常类型和本地代码位置，不记录令牌、请求地址或上游异常文本。`python3 tests/check_friend_payloads.py` 使用原始 JSON 经过真实请求解析器验证混合关系、资料缺失、空库、私密库和重复同步。

侧栏和空间入口包含家庭贡献、好友收藏与近两周游玩；各自功能在页内展示。SteamDB 没有可用的公开计算器查询 API，已移除依赖手动录入的栏目。旧 #steamdb、#achievements 链接回到好友页。

回归检查：`python3 tests/check_unified_login.py`；安装 Playwright 和 Chromium 后运行 `python3 tests/check_unified_ui.py`。覆盖令牌 / Key 请求格式、后端完整流水线、单次登录、权限错误、手动同步和重试、另一标签页持锁时的同步、双栏目导航与手机布局。浏览器测试走真实本地 HTTP 后端，Steam 返回使用合成数据，不替代真实账号权限验证。
