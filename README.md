# iyf.tv 弹幕自动下载脚本

使用 Playwright 自动打开 iyf.tv 播放页，拦截 `getBarrage` 接口响应并保存为 JSON，避免手工开 F12 抠数据。

## 环境准备
- Python 3.9+（建议新建虚拟环境）
- 安装依赖：
  ```bash
  pip install playwright
  python -m playwright install chromium
  ```

## 使用方式
单个或多个播放页 URL：
```bash
python download_barrage.py \
  --urls https://iyf.tv/play/xxxx https://iyf.tv/play/yyyy \
  --output-dir barrage_output
```

从剧集列表页自动提取所有播放链接（例如一季多集）：
```bash
python download_barrage.py \
  --playlist-urls https://www.iyf.tv/play/kpJAtmMX7X4 \
  --connect-over-cdp http://localhost:9222 \
  --accept-language "zh-CN,zh;q=0.9,en;q=0.8" \
  --user-agent "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36" \
  --series-name "你的剧名"
```
脚本会先解析列表页中的 `/play/<同一剧集>` 链接（过滤掉推荐区的其他剧），再逐个抓取弹幕。`--series-name` 可自定义输出文件名前缀：带剧名时文件名形如 `剧名_ep01_barrage.json` 或 `剧名_本集标题_barrage.json`；不带剧名时形如 `01_ep01_barrage.json`。

从文件读取 URL（每行一个），可与 `--urls` 混用去重：
```bash
python download_barrage.py --url-file urls.txt
```

常用参数：
- `--timeout` 等待首个 `getBarrage` 响应的秒数，默认 15。
- `--extra-wait` 拿到首个后继续等多久收集剩余请求，默认 3 秒。
- `--headed` 需调试时显示浏览器窗口。
- `--user-data-dir PATH` 使用持久化用户目录，贴近真实浏览器（绕过 Cloudflare 较有效），会忽略 storage-state。
- `--executable-path PATH` 指定本机 Chrome/Chromium 可执行文件，避免内置内核被风控。
- `--accept-language "..."` 自定义 Accept-Language（默认 zh-CN,zh;q=0.9,en;q=0.8）。
- `--storage-state PATH` 复用登录/验证后的 cookies；配合 `--save-storage-state` 可在手动通过验证后保存。
- `--user-agent` 自定义 UA（如遇风控可尝试常见桌面 UA）。

输出会放在 `--output-dir`（默认 `barrage_output`），文件名格式如 `01_iyf.tv_play_xxxx_barrage.json`，内容包含：
- `source_page`：播放页 URL
- `captured_at`：UTC 时间戳
- `count`：捕获到的 `getBarrage` 请求数量
- `requests`：每个请求的 `api_url`/`status`/`headers`/`body`（即弹幕数据）

## 说明与建议
- 若页面需要登录或开启会员，可在显示模式下（`--headed`）手动登录一次，随后再跑脚本。
- 默认依赖页面自动请求 `getBarrage`。如果某些剧集需要点击播放后才出弹幕，在显示模式下点一次播放即可；也可以增大 `--timeout`。
- 脚本只读网络响应，不会模拟键盘/鼠标敏感操作。
- 如果遇到 Cloudflare 验证：
  - 优先用持久化 profile：`--headed --user-data-dir ./user-data --user-agent "<常见桌面 UA>"`，在弹出浏览器内手动验证/登录一次，后续同一路径复用；
  - 尝试用本机 Chrome/Chromium：在上面的基础上加 `--executable-path /path/to/chrome`，并保持 `--headed`，让它更像真实浏览器；
  - 若仍被挑战，可再配合 `--storage-state cf.json --save-storage-state`（仅非持久模式生效），先手动通过验证后保存 cookies；
  - Playwright 已加 `--disable-blink-features=AutomationControlled`，避免明显的自动化标记，但无法绕过需要人工操作的挑战，只能在可视模式下手动完成。

### 已验证可用的 Cloudflare 绕过流程（Windows 示例）
在某些环境下，直接用 Playwright 启动浏览器仍会循环验证。实测可用的方案是“先手动开启带调试端口的 Chrome，再让脚本连接”：

1. 先开一个真实 Chrome（新 profile，带远程调试）：
   ```powershell
   & "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\chrome-profile"
   ```
2. 在这个 Chrome 窗口里打开目标播放页，手动通过 Cloudflare 验证/登录，必要时点播放。
3. 运行脚本，连接到刚才的浏览器（保持窗口打开）：
   ```bash
   python download_barrage.py \
     --urls https://www.iyf.tv/play/kpJAtmMX7X4 \
     --connect-over-cdp http://localhost:9222 \
     --accept-language "zh-CN,zh;q=0.9,en;q=0.8" \
     --user-agent "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36" \
     --timeout 30 --extra-wait 5
   ```
4. 脚本会复用你在真实 Chrome 里的会话，拦截 `getBarrage` 响应并保存到 `barrage_output/`。
