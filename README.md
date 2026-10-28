# FamilyShelf

Steam 家庭贡献、好友游戏库价值与 SteamDB 价格榜单。

前端使用 GitHub Pages，Python API 和临时 Steam 登录浏览器运行在 Render。
GitHub Pages 仅提供静态页面；把 Python 改写成 JavaScript 并不能让 Pages 运行后端。

## 功能与数据口径

- 家庭可共享游戏、独有收藏、按持有人数均摊的价值贡献。
- 好友公开游戏库、价格覆盖率、游玩时间、对比和共同收藏。
- Steam 商店原价/当前价估值；未返回报价保持未知，不当作免费。
- SteamDB 独立榜单支持手动录入、CSV/JSON 导入。没有抓取 SteamDB，也不代表实际消费。
- 手机扫码登录 Steam；自动读取账号已有的 API Key。尚未申请 Key 时需在官方页面自行申请，再手动连接好友。

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
