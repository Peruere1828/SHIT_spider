from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import time
import os
import re
import time
import base64
import json
from dotenv import load_dotenv

load_dotenv()

ZONE = ["latrine", "septic", "stone", "sediment"]
PROXY = os.getenv("PROXY", "http://localhost:7897")

LIST_CACHE_FILE = "articles_list.json"


def handle_user_notice_popup(page):
    """专门处理 '年满 18 周岁' 的确认弹窗"""
    print("  🔎 检查是否存在免责声明弹窗...")

    # 尝试定位那个包含特定文字的弹窗遮罩层
    # 根据源码，弹窗最外层有 z-[99999] 类
    popup_locator = page.locator("div.z-\\[99999\\]")

    # 等待一小会儿，看弹窗是否出现 (如果网页加载快，弹窗会立刻出现，所以不需要等太久)
    try:
        popup_locator.wait_for(state="visible", timeout=3000)
    except Exception:
        print("  ✅ 未检测到免责声明弹窗，继续执行。")
        return  # 如果 3 秒内没出现，说明没有弹窗，直接返回

    print("  🚧 发现免责声明弹窗，正在尝试关闭...")
    try:
        # 定位并点击复选框 (Type="checkbox")
        checkbox = page.locator('input[type="checkbox"]')
        if checkbox.count() > 0:
            # 强制点击，以防有其他不可见元素遮挡
            checkbox.first.click(force=True)
            print("    - 已勾选 '我已年满 18 周岁'")
            time.sleep(0.5)

        # 定位并点击“同意并进入”按钮
        enter_btn = page.locator("button", has_text="同意并进入")
        if enter_btn.count() > 0:
            enter_btn.click(force=True)
            print("    - 已点击 'ENTER S.H.I.T. JOURNAL / 同意并进入'")

            # 等待弹窗消失
            popup_locator.wait_for(state="hidden", timeout=5000)
            print("  ✅ 弹窗已成功关闭！")
            time.sleep(1)  # 额外等待一小会儿，让底层页面恢复交互
        else:
            print("    ❌ 找不到确认按钮。")

    except Exception as e:
        print(f"  ❌ 处理弹窗时发生错误: {e}")
        # 如果常规点击失败，使用暴力 JS 移除法作为备选方案 (Fallback)
        print("  ⚠️ 尝试使用 JS 暴力移除弹窗...")
        page.evaluate(
            "document.querySelectorAll('.z-\\\\[99999\\\\]').forEach(el => el.remove())"
        )


def scrape_directory(base_url="https://shitjournal.org/preprints", zone="latrine"):
    """
    利用 Playwright 渲染目录页，利用 bs4 提取所有文章的 title 和 url
    """
    # 针对不同的 zone 使用不同的缓存文件
    cache_file = f"articles_list_{zone}.json"

    # 【断点续爬】如果存在缓存，直接读取跳过爬取
    if os.path.exists(cache_file):
        print(f"发现本地目录缓存 {cache_file}，直接读取...")
        with open(cache_file, "r", encoding="utf-8") as f:
            return json.load(f)

    print(f"未发现 {zone} 区缓存，开始爬取目录页...")

    base_url = base_url + "?zone=" + zone
    all_articles = []
    page_num = 1

    # 启动 Playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, proxy={"server": PROXY})
        page = browser.new_page()

        while True:
            # 构造当前页面的 URL
            current_url = f"{base_url}&page={page_num}"
            print(f"正在提取 {zone} 目录第 {page_num} 页: {current_url}")

            try:
                page.goto(current_url, wait_until="networkidle", timeout=30000)
            except Exception as e:
                print(f"❌ 页面加载超时或出错: {e}")
                break

            soup = BeautifulSoup(page.content(), "html.parser")

            # 1. 提取当前页面的所有文章列表
            # 过滤条件：寻找 a 标签，href 包含 /preprints/ 且不包含 zone=（排除导航栏链接）
            article_nodes = soup.find_all(
                "a",
                href=lambda href: href
                and href.startswith("/preprints/")
                and "zone=" not in href,
            )

            if not article_nodes:
                print("⚠️ 本页未找到任何文章节点，可能已到达末尾或页面结构发生变化。")
                break

            for node in article_nodes:
                # 提取并拼接完整的 URL
                relative_url = node.get("href")
                full_url = f"https://shitjournal.org{relative_url}"

                # 提取标题 (优先获取 h4 的 title 属性，如果没有则获取文本)
                title_tag = node.find("h4")
                if title_tag and title_tag.has_attr("title"):
                    title = title_tag["title"]
                else:
                    title = title_tag.text.strip() if title_tag else "未知标题"

                # 保存到结果列表中
                all_articles.append({"title": title, "url": full_url, "zone": zone})
                print(f"  ✅ 提取成功: {title[:20]}...")

            # 2. 检查是否还有下一页 (提取翻页逻辑)
            # 寻找包含 "Next" 文本的 button
            next_button = soup.find(
                "button", string=lambda text: text and "Next" in text
            )

            # 如果 Next 按钮存在，并且带有 disabled 属性，说明到底了
            if next_button and next_button.has_attr("disabled"):
                print(f"\n 第 {page_num} 页的 Next 按钮已禁用，{zone} 目录提取完毕！")
                break
            else:
                page_num += 1
                time.sleep(2.0)  # 礼貌性延时，避免给目标服务器造成太大压力

        # 关闭浏览器
        browser.close()

    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(all_articles, f, ensure_ascii=False, indent=4)

    return all_articles


def sanitize_filename(filename):
    """清理文件名中的非法字符，用于创建文件夹"""
    return re.sub(r'[\\/*?:"<>|]', "", filename).strip()


def scrape_article_images(page, article, base_save_dir="./downloads"):
    """
    访问文章详情页，提取 Canvas 图像并翻页保存
    """
    article_url = article["url"]
    article_title = article["title"]

    article_uuid = article_url.split("/")[-1]
    save_dir = os.path.join(base_save_dir, article_uuid)
    os.makedirs(save_dir, exist_ok=True)

    mark_file = os.path.join(save_dir, "completed.mark")
    if os.path.exists(mark_file):
        print(f"⏭️  跳过已完成的文章: {article_uuid} ({article_title[:20]}...)")
        return

    print(f"\n正在处理文章: {article_uuid} | {article_title[:20]}...")

    metadata = {
        "id": article_uuid,
        "title": article_title,
        "url": article_url,
        "zone": article.get("zone", "unknown"),
        "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(os.path.join(save_dir, "info.json"), "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=4)

    try:
        page.goto(article_url, wait_until="networkidle", timeout=30000)
    except Exception as e:
        print(f"❌ 页面加载失败: {e}")
        return

    handle_user_notice_popup(page)

    page_idx = 1

    while True:
        # 1. 等待 canvas 元素出现 (react-pdf 渲染需要时间)
        try:
            page.wait_for_selector(
                "canvas.react-pdf__Page__canvas", state="visible", timeout=10000
            )
            time.sleep(1.5)  # 额外给一点时间让 PDF 页面在画布上完全绘制完毕
        except Exception:
            print("⚠️ 未能找到 Canvas 渲染区域，可能该文章为空或加载超时。")
            break

        # 2. 注入 JavaScript 提取 Canvas 的图像数据
        # toDataURL() 会返回类似 "data:image/png;base64,iVBORw0KGgo..." 的字符串
        data_url = page.evaluate(
            """() => {
            const canvas = document.querySelector('canvas.react-pdf__Page__canvas');
            return canvas ? canvas.toDataURL('image/png') : null;
        }"""
        )

        if data_url:
            # 剥离 "data:image/png;base64," 前缀
            base64_str = data_url.split(",")[1]
            image_data = base64.b64decode(base64_str)

            # 保存图片
            img_path = os.path.join(save_dir, f"page_{page_idx}.png")
            with open(img_path, "wb") as f:
                f.write(image_data)
            print(f"  ✅ 成功保存第 {page_idx} 页 -> {img_path}")
        else:
            print(f"  ❌ 第 {page_idx} 页提取图片数据失败。")

        # 3. 处理翻页逻辑
        # 定位包含“下一页”文字的按钮
        next_btn = page.locator("button", has_text="下一页")

        # 检查按钮是否存在且未被禁用
        if next_btn.count() > 0 and not next_btn.is_disabled():
            next_btn.click()
            page_idx += 1
            # 点击后稍微等待一下，让上一页的 canvas 消失，新 canvas 开始渲染
            time.sleep(1.0)
        else:
            print(f"🛑 已到达文章末尾，共下载 {page_idx} 页。")
            with open(mark_file, "w") as f:
                f.write("Done.")
            break


if __name__ == "__main__":
    os.makedirs("./downloads", exist_ok=True)

    # 获取所有分区的文章列表
    all_articles_data = []
    for zone in ZONE:
        articles_data = scrape_directory(zone=zone)
        print("\n" + "=" * 50)
        print(f"汇总：共成功提取 {zone} 部分 {len(articles_data)} 篇文章入口！")
        print("=" * 50)
        all_articles_data.extend(articles_data)

    # 统一进行文章详情页的抓取
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, proxy={"server": PROXY})
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()

        for idx, article in enumerate(all_articles_data, 1):
            print(f"\n[总体进度: {idx}/{len(all_articles_data)}]", end="")
            scrape_article_images(page, article)

        browser.close()

    print("\n🎉 全部区块爬取任务完美收官！")
