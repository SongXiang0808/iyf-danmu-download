import argparse
import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

from playwright.async_api import async_playwright, Page, BrowserContext


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="抓取 iyf.tv 播放页里 getBarrage 的响应并保存为 json"
    )
    parser.add_argument(
        "--urls",
        nargs="+",
        help="要抓取的剧集/视频播放页 url，空格分隔",
    )
    parser.add_argument(
        "--playlist-urls",
        nargs="+",
        help="包含多集列表的页面（如电视剧季页）；脚本会自动提取其中的 /play/ 链接",
    )
    parser.add_argument(
        "--url-file",
        type=Path,
        help="包含多个 url 的文件，按行分隔；会和 --urls 合并",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("barrage_output"),
        help="保存 json 的目录，默认 barrage_output",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=15,
        help="等待首个 getBarrage 响应的超时时间（秒），默认 15",
    )
    parser.add_argument(
        "--extra-wait",
        type=int,
        default=3,
        help="拿到首个 getBarrage 后再等多久收集剩余请求（秒），默认 3",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="显示浏览器窗口（便于排查），默认 headless",
    )
    parser.add_argument(
        "--user-data-dir",
        type=Path,
        help="使用持久化用户目录（绕过风控时建议，类似真实浏览器 profile），会忽略 storage-state",
    )
    parser.add_argument(
        "--executable-path",
        type=Path,
        help="指定本机已安装的 Chrome/Chromium 可执行文件，避免使用内置内核（有时风控更宽松）",
    )
    parser.add_argument(
        "--accept-language",
        default="zh-CN,zh;q=0.9,en;q=0.8",
        help="自定义 Accept-Language 头，默认 zh-CN 优先",
    )
    parser.add_argument(
        "--connect-over-cdp",
        help="连接到已开启 remote debugging 的浏览器（如手动启动的 Chrome），用于复用真实会话绕过验证",
    )
    parser.add_argument(
        "--storage-state",
        type=Path,
        help="Playwright storageState 文件，用于复用登录/验证后的 cookies（可先手动验证后保存）",
    )
    parser.add_argument(
        "--save-storage-state",
        action="store_true",
        help="跑完后保存 storageState 到 --storage-state 指定的路径（需要先指定路径）",
    )
    parser.add_argument(
        "--user-agent",
        help="自定义 User-Agent，某些站点对默认 UA 有风控，可尝试设置成常见桌面 UA",
    )
    return parser.parse_args()


def read_urls(args: argparse.Namespace) -> List[str]:
    urls: List[str] = []
    if args.urls:
        urls.extend(args.urls)
    if args.url_file and args.url_file.exists():
        urls.extend(
            line.strip()
            for line in args.url_file.read_text().splitlines()
            if line.strip()
        )
    deduped = []
    seen = set()
    for u in urls:
        if u not in seen:
            deduped.append(u)
            seen.add(u)
    return deduped


def slug_from_url(url: str) -> str:
    slug = re.sub(r"https?://", "", url)
    slug = slug.rstrip("/")
    slug = slug.replace("/", "_")
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", slug)
    return slug or "barrage"


async def collect_barrage_for_page(
    context: BrowserContext, url: str, timeout_s: int, extra_wait_s: int
) -> List[Dict[str, Any]]:
    page: Page = await context.new_page()
    collected: List[Dict[str, Any]] = []
    tasks: List[asyncio.Task] = []

    async def handle_response(response):
        if "getBarrage" not in response.url:
            return
        try:
            body = await response.json()
        except Exception:
            body = await response.text()
        collected.append(
            {
                "api_url": response.url,
                "status": response.status,
                "headers": dict(response.headers),
                "body": body,
            }
        )

    page.on("response", lambda r: tasks.append(asyncio.create_task(handle_response(r))))

    try:
        await page.goto(url, wait_until="networkidle")
    except Exception:
        await page.goto(url)

    try:
        await page.wait_for_response(lambda r: "getBarrage" in r.url, timeout=timeout_s * 1000)
        await page.wait_for_timeout(extra_wait_s * 1000)
    except Exception:
        pass

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    await page.close()
    return collected


async def extract_episode_urls(context: BrowserContext, playlist_url: str) -> List[str]:
    """从剧集列表页提取所有 /play/ 链接（去重保留顺序）"""
    page: Page = await context.new_page()
    try:
        await page.goto(playlist_url, wait_until="networkidle")
    except Exception:
        await page.goto(playlist_url)
    anchors = await page.eval_on_selector_all(
        "a[href*='/play/']",
        """els => {
            const seen = new Set();
            const results = [];
            for (const el of els) {
                const href = el.href;
                if (!href || seen.has(href)) continue;
                seen.add(href);
                results.push(href);
            }
            return results;
        }""",
    )
    await page.close()
    return anchors or []


async def main():
    args = parse_args()
    urls = read_urls(args)
    if not urls and not args.playlist_urls:
        print("没有 url 输入，请用 --urls / --url-file / --playlist-urls 指定播放页或列表页链接。")
        return

    args.output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    async with async_playwright() as p:
        browser = None
        context = None
        launch_args = {
            "headless": not args.headed,
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if args.executable_path:
            launch_args["executable_path"] = str(args.executable_path)

        if args.connect_over_cdp:
            # 连接到外部已开启调试端口的浏览器（例如手动启动的 Chrome）
            browser = await p.chromium.connect_over_cdp(args.connect_over_cdp)
            if browser.contexts:
                context = browser.contexts[0]
            else:
                context = await browser.new_context(
                    user_agent=args.user_agent,
                    locale=args.accept_language.split(",")[0],
                )
            print(f"已连接到现有浏览器: {args.connect_over_cdp}")
        elif args.user_data_dir:
            # 持久化 profile，尽量模拟真实浏览器
            args.user_data_dir.mkdir(parents=True, exist_ok=True)
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(args.user_data_dir),
                user_agent=args.user_agent,
                locale=args.accept_language.split(",")[0],
                **launch_args,
            )
            print(f"使用持久化用户目录: {args.user_data_dir}")
            if args.executable_path:
                print(f"使用指定浏览器内核: {args.executable_path}")
        else:
            browser = await p.chromium.launch(**launch_args)
            storage_state_arg = None
            if args.storage_state and args.storage_state.exists():
                storage_state_arg = args.storage_state
                print(f"加载 storageState: {args.storage_state}")

            context = await browser.new_context(
                storage_state=storage_state_arg,
                user_agent=args.user_agent,
                locale=args.accept_language.split(",")[0],
            )
            if args.executable_path:
                print(f"使用指定浏览器内核: {args.executable_path}")

        # 如果提供了剧集列表页，先解析得到所有剧集播放链接
        if args.playlist_urls:
            playlist_links: List[str] = []
            for playlist_url in args.playlist_urls:
                print(f"解析剧集列表页: {playlist_url}")
                try:
                    episodes = await extract_episode_urls(context, playlist_url)
                except Exception as exc:
                    print(f"  解析失败: {exc}")
                    episodes = []
                if episodes:
                    print(f"  获取到 {len(episodes)} 个播放链接")
                    playlist_links.extend(episodes)
                else:
                    print("  未获取到播放链接")
            urls.extend(playlist_links)

        if not urls:
            print("未获得任何播放页链接，退出。")
            return

        # 去重保持顺序
        seen_urls = set()
        unique_urls = []
        for u in urls:
            if u in seen_urls:
                continue
            seen_urls.add(u)
            unique_urls.append(u)

        for idx, url in enumerate(unique_urls, start=1):
            print(f"[{idx}/{len(urls)}] 访问: {url}")
            barrages = await collect_barrage_for_page(
                context, url, timeout_s=args.timeout, extra_wait_s=args.extra_wait
            )
            if not barrages:
                print("  未捕获到 getBarrage 响应，可能页面未加载或需要登录。")
                continue
            out_name = f"{idx:02d}_{slug_from_url(url)}_barrage.json"
            out_path = args.output_dir / out_name
            payload = {
                "source_page": url,
                "captured_at": timestamp,
                "count": len(barrages),
                "requests": barrages,
            }
            out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
            print(f"  已保存 {len(barrages)} 条到 {out_path}")
        if args.save_storage_state and args.storage_state and not args.user_data_dir:
            args.storage_state.parent.mkdir(parents=True, exist_ok=True)
            await context.storage_state(path=str(args.storage_state))
            print(f"已保存 storageState 到 {args.storage_state}")
        if browser:
            await browser.close()
        else:
            await context.close()


if __name__ == "__main__":
    asyncio.run(main())
