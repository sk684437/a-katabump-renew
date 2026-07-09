#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Katabump 多账户自动续期脚本
支持从环境变量 ACCOUNTS_JSON 或 accounts.json 读取多个账户
同一浏览器循环登录 + 续期
"""

import json
import os
import time
import subprocess
import requests
from seleniumbase import SB

# ===== 配置 =====

# TG 通知（全局配置）
TG_CHAT_ID   = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN") or ""

BASE_URL = "https://dashboard.katabump.com"  # 网站链接

# ===== 工具函数 =====

def load_accounts() -> list:
    """从环境变量 ACCOUNTS_JSON 或 accounts.json 加载账户列表"""

    # 优先从环境变量读取
    env_json = os.environ.get("ACCOUNTS_JSON", "").strip()
    if env_json:
        print("📦 从环境变量 ACCOUNTS_JSON 读取账户...")
        try:
            accounts = json.loads(env_json)
        except json.JSONDecodeError as e:
            print(f"❌ ACCOUNTS_JSON 格式错误: {e}")
            return []

        if not isinstance(accounts, list):
            print("❌ ACCOUNTS_JSON 应为 JSON 数组 []")
            return []

        if len(accounts) == 0:
            print("⚠️ ACCOUNTS_JSON 为空数组")
            return []

        return _validate_accounts(accounts)

    # 备用：从 accounts.json 文件读取
    acct_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "accounts.json")
    if os.path.isfile(acct_file):
        print(f"📁 从文件读取账户: {acct_file}")
        try:
            with open(acct_file, "r", encoding="utf-8") as f:
                accounts = json.load(f)
        except json.JSONDecodeError as e:
            print(f"❌ {acct_file} 格式错误: {e}")
            return []

        if not isinstance(accounts, list):
            print(f"❌ {acct_file} 应为 JSON 数组 []")
            return []

        if len(accounts) == 0:
            print(f"⚠️ {acct_file} 为空")
            return []

        return _validate_accounts(accounts)

    print("❌ 未找到账户配置。请设置环境变量 ACCOUNTS_JSON 或创建 accounts.json")
    print("   格式: [{\"email\": \"...\", \"password\": \"...\"}]")
    return []


def _validate_accounts(accounts: list) -> list:
    """校验并过滤账户列表"""
    # 全局 NODE_LINK 环境变量（对所有账户生效的兜底）
    global_node_link = os.environ.get("NODE_LINK", "").strip()

    valid = []
    for i, acct in enumerate(accounts):
        if not isinstance(acct, dict):
            print(f"⚠️ 第 {i+1} 个账户不是对象，已跳过")
            continue
        email = (acct.get("email") or "").strip()
        pwd   = (acct.get("password") or "").strip()
        if not email or not pwd:
            print(f"⚠️ 第 {i+1} 个账户缺少 email 或 password，已跳过")
            continue

        # node_link：优先取账户内配置，其次全局环境变量
        node_link = (acct.get("node_link") or "").strip() or global_node_link

        valid.append({"email": email, "password": pwd, "node_link": node_link})

    print(f"📋 共加载 {len(valid)} 个有效账户")
    return valid


def mask_email(email: str) -> str:
    """邮箱脱敏：保留用户名前2位和后2位，中间用****代替"""
    if '@' in email:
        name, domain = email.split('@', 1)
        if len(name) > 4:
            return f"{name[:2]}****{name[-2:]}@{domain}"
        else:
            return f"{name}@{domain}"
    else:
        return email[:2] + '****'


def send_tg_message(status_icon, status_text, time_left="", email=""):
    """Telegram 推送通知（email 用于脱敏显示）"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("ℹ️ 未配置 TG_BOT_TOKEN 或 TG_CHAT_ID，跳过 Telegram 推送。")
        return

    # 获取北京时间 (UTC+8)
    local_time = time.gmtime(time.time() + 8 * 3600)
    current_time_str = time.strftime("%Y-%m-%d %H:%M:%S", local_time)

    masked_email = mask_email(email) if email else "未知"

    text = (
        f"🇫🇷 katabump 续期通知\n\n"
        f"{status_icon} {status_text}\n"
        f"👤 续期账户: {masked_email}\n"
        f"⏱️ 续期时间: {current_time_str}"
    )

    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text
    }

    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            print("📩 Telegram 通知发送成功！")
        else:
            print(f"⚠️ Telegram 通知发送失败: {r.text}")
    except Exception as e:
        print(f"⚠️ Telegram 通知发送异常: {e}")


# ===== 页面注入脚本 =====

_EXPAND_JS = """
(function() {
    var ts = document.querySelector('input[name="cf-turnstile-response"]');
    if (!ts) return 'no-turnstile';
    var el = ts;
    for (var i = 0; i < 20; i++) {
        el = el.parentElement;
        if (!el) break;
        var s = window.getComputedStyle(el);
        if (s.overflow === 'hidden' || s.overflowX === 'hidden' || s.overflowY === 'hidden')
            el.style.overflow = 'visible';
        el.style.minWidth = 'max-content';
    }
    document.querySelectorAll('iframe').forEach(function(f){
        if (f.src && f.src.includes('challenges.cloudflare.com')) {
            f.style.width = '300px'; f.style.height = '65px';
            f.style.minWidth = '300px';
            f.style.visibility = 'visible'; f.style.opacity = '1';
        }
    });
    return 'done';
})()
"""

_EXISTS_JS = """
(function(){
    return document.querySelector('input[name="cf-turnstile-response"]') !== null;
})()
"""

_SOLVED_JS = """
(function(){
    var i = document.querySelector('input[name="cf-turnstile-response"]');
    return !!(i && i.value && i.value.length > 20);
})()
"""

_COORDS_JS = """
(function(){
    var iframes = document.querySelectorAll('iframe');
    for (var i = 0; i < iframes.length; i++) {
        var src = iframes[i].src || '';
        if (src.includes('cloudflare') || src.includes('turnstile') || src.includes('challenges')) {
            var r = iframes[i].getBoundingClientRect();
            if (r.width > 0 && r.height > 0)
                return {cx: Math.round(r.x + 30), cy: Math.round(r.y + r.height / 2)};
        }
    }
    var inp = document.querySelector('input[name="cf-turnstile-response"]');
    if (inp) {
        var p = inp.parentElement;
        for (var j = 0; j < 5; j++) {
            if (!p) break;
            var r = p.getBoundingClientRect();
            if (r.width > 100 && r.height > 30)
                return {cx: Math.round(r.x + 30), cy: Math.round(r.y + r.height / 2)};
            p = p.parentElement;
        }
    }
    return null;
})()
"""

_WININFO_JS = """
(function(){
    return {
        sx: window.screenX || 0,
        sy: window.screenY || 0,
        oh: window.outerHeight,
        ih: window.innerHeight
    };
})()
"""

# ===== ALTCHA 相关脚本 =====

_ALTCHA_EXPAND_JS = """
(function() {
    var modal = document.querySelector('div.modal.show') || document;
    var iframes = modal.querySelectorAll('iframe');
    for (var i = 0; i < iframes.length; i++) {
        var r = iframes[i].getBoundingClientRect();
        if (r.width > 0 && r.height > 0) {
            iframes[i].style.width  = '300px';
            iframes[i].style.height = '150px';
            iframes[i].style.minWidth  = '300px';
            iframes[i].style.minHeight = '150px';
            iframes[i].style.visibility = 'visible';
            iframes[i].style.opacity = '1';
            var el = iframes[i];
            for (var j = 0; j < 10; j++) {
                el = el.parentElement;
                if (!el) break;
                el.style.overflow = 'visible';
            }
            var r2 = iframes[i].getBoundingClientRect();
            return { cx: Math.round(r2.x + 30), cy: Math.round(r2.y + r2.height / 2) };
        }
    }
    return null;
})()
"""

_ALTCHA_SOLVED_JS = """
(function(){
    var modal = document.querySelector('div.modal.show') || document;
    var inputs = modal.querySelectorAll('input[type="hidden"]');
    for (var i = 0; i < inputs.length; i++) {
        var n = (inputs[i].name || '').toLowerCase();
        if ((n.includes('altcha') || n.includes('captcha')) &&
            inputs[i].value && inputs[i].value.length > 20) return true;
    }
    var cbs = modal.querySelectorAll('input[type="checkbox"]');
    for (var j = 0; j < cbs.length; j++) {
        if (cbs[j].disabled) return true;
    }
    var w = modal.querySelector('[data-state="verified"],.altcha--verified,.altcha-verified');
    if (w) return true;
    return false;
})()
"""

# ===== 底层输入工具 =====

def js_fill_input(sb, selector: str, text: str):
    safe_text = text.replace('\\', '\\\\').replace('"', '\\"')
    sb.execute_script(f"""
    (function(){{
        var el = document.querySelector('{selector}');
        if (!el) return;
        var nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
        if (nativeInputValueSetter) {{
            nativeInputValueSetter.call(el, "{safe_text}");
        }} else {{
            el.value = "{safe_text}";
        }}
        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
        el.dispatchEvent(new Event('change', {{ bubbles: true }}));
    }})()
    """)


def _activate_window():
    for cls in ["chrome", "chromium", "Chromium", "Chrome", "google-chrome"]:
        try:
            r = subprocess.run(["xdotool", "search", "--onlyvisible", "--class", cls], capture_output=True, text=True, timeout=3)
            wids = [w for w in r.stdout.strip().split("\n") if w.strip()]
            if wids:
                subprocess.run(["xdotool", "windowactivate", "--sync", wids[0]], timeout=3, stderr=subprocess.DEVNULL)
                time.sleep(0.2)
                return
        except Exception:
            pass
    try:
        subprocess.run(["xdotool", "getactivewindow", "windowactivate"], timeout=3, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _xdotool_click(x: int, y: int):
    _activate_window()
    try:
        subprocess.run(["xdotool", "mousemove", "--sync", str(x), str(y)], timeout=3, stderr=subprocess.DEVNULL)
        time.sleep(0.15)
        subprocess.run(["xdotool", "click", "1"], timeout=2, stderr=subprocess.DEVNULL)
    except Exception:
        os.system(f"xdotool mousemove {x} {y} click 1 2>/dev/null")


# ===== 人机验证处理 =====

def _click_turnstile(sb):
    try:
        coords = sb.execute_script(_COORDS_JS)
    except Exception as e:
        print(f"⚠️ 获取 Turnstile 坐标失败: {e}")
        return
    if not coords:
        print("⚠️ 无法定位 Turnstile 坐标")
        return
    try:
        wi = sb.execute_script(_WININFO_JS)
    except Exception:
        wi = {"sx": 0, "sy": 0, "oh": 800, "ih": 768}

    bar = wi["oh"] - wi["ih"]
    ax  = coords["cx"] + wi["sx"]
    ay  = coords["cy"] + wi["sy"] + bar
    print(f"🖱️ 点击验证框 Turnstile ({ax}, {ay})")
    _xdotool_click(ax, ay)


def handle_turnstile(sb) -> bool:
    print("🔍 处理 Cloudflare Turnstile 验证...")
    time.sleep(2)

    if sb.execute_script(_SOLVED_JS):
        print("✅ 已静默通过")
        return True

    for _ in range(3):
        try: sb.execute_script(_EXPAND_JS)
        except Exception: pass
        time.sleep(0.5)

    for attempt in range(6):
        if sb.execute_script(_SOLVED_JS):
            print(f"✅ Turnstile 通过（第 {attempt + 1} 次尝试）")
            return True
        try: sb.execute_script(_EXPAND_JS)
        except Exception: pass
        time.sleep(0.3)

        _click_turnstile(sb)

        for _ in range(8):
            time.sleep(0.5)
            if sb.execute_script(_SOLVED_JS):
                print(f"✅ Turnstile 通过（第 {attempt + 1} 次尝试）")
                return True
        print(f"⚠️ 第 {attempt + 1} 次未通过，重试...")

    print("  ❌ Turnstile 6 次均失败")
    return False


# ===== 登录 =====

def _wait_login_form(sb, timeout=15) -> bool:
    """等待登录表单的 email 输入框出现"""
    try:
        sb.wait_for_element('input[name="email"]', timeout=timeout)
        return True
    except Exception:
        try:
            sb.wait_for_element('input[name="Email"]', timeout=5)
            return True
        except Exception:
            return False


def _fill_and_submit(sb, email: str, password: str) -> None:
    """填写邮箱密码并提交（不判断是否成功）"""
    print("🍪 关闭可能的 Cookie 弹窗...")
    try:
        for btn in sb.find_elements("button"):
            if "Accept" in (btn.text or ""):
                btn.click()
                time.sleep(0.5)
                break
    except Exception:
        pass

    print(f"📧 填写邮箱...")
    js_fill_input(sb, 'input[name="email"]', email)
    time.sleep(0.3)

    print("🔑 填写密码...")
    js_fill_input(sb, 'input[name="password"]', password)
    time.sleep(1)

    # 检测 Turnstile 并处理
    if sb.execute_script(_EXISTS_JS):
        if not handle_turnstile(sb):
            print("❌ 登录界面的 Turnstile 验证失败")
            sb.save_screenshot("login_turnstile_fail.png")
            return
    else:
        print("ℹ️ 未检测到 Turnstile（可能后端静默验证）")

    print("🖱️ 敲击回车提交表单...")
    sb.press_keys('input[name="password"]', '\n')


def login(sb, email: str, password: str, max_retry: int = 3) -> bool:
    """登录账户，提交后若遇 Cloudflare captcha 错误自动重试"""
    for attempt in range(1, max_retry + 1):
        print(f"\n🌐 [尝试 {attempt}/{max_retry}] 打开登录页面: {BASE_URL}/auth/login")
        sb.uc_open_with_reconnect(BASE_URL + "/auth/login", reconnect_time=5)
        time.sleep(6)

        # 先等待 Cloudflare 验证通过（最多等 30 秒）
        print("⏳ 等待 Cloudflare 验证通过...")
        cf_passed = False
        for i in range(30):
            page_src = sb.get_page_source() or ""
            if 'input[name="email"]' in page_src.lower() or 'name="email"' in page_src.lower():
                cf_passed = True
                print(f"✅ Cloudflare 验证已通过（{i+1}s）")
                break
            time.sleep(1)
        if not cf_passed:
            print("⚠️ Cloudflare 验证可能未通过，继续尝试...")

        if not _wait_login_form(sb, timeout=15):
            print("❌ 页面未加载出登录表单")
            cur_url = sb.get_current_url()
            page_title = sb.get_title() or ""
            print(f"  当前 URL: {cur_url}")
            print(f"  当前标题: {page_title}")
            sb.save_screenshot("login_load_fail.png")
            continue  # 重试

        _fill_and_submit(sb, email, password)

        print("⏳ 等待登录跳转...")
        for _ in range(12):
            time.sleep(1)
            cur_url = sb.get_current_url().split('?')[0].lower()
            page_title = sb.get_title() or ""
            if cur_url.startswith(f"{BASE_URL}/dashboard") or "Dashboard | KataBump" in page_title.lower():
                break

        cur_url = sb.get_current_url().split('?')[0].lower()
        page_title = sb.get_title() or ""
        if cur_url.startswith(f"{BASE_URL}/dashboard") or "Dashboard | KataBump" in page_title.lower():
            print(f"✅ 登录成功！(URL: {sb.get_current_url()}, Title: {page_title})")
            return True

        # 登录失败：判断是否 captcha 错误
        full_url = sb.get_current_url().lower()
        if "error=captcha" in full_url or "captcha" in full_url:
            print(f"⚠️ 第 {attempt} 次登录被 Cloudflare captcha 拦截，清理状态后重试...")
            # 清 Cookie 让下一个重试拿到新验证会话
            try:
                sb.execute_cdp_cmd("Network.clearBrowserCookies", {})
            except Exception:
                try: sb.driver.delete_all_cookies()
                except Exception: pass
            time.sleep(2)
            continue
        else:
            print(f"❌ 登录失败（非 captcha 原因），页面未跳转到账户页。(URL: {sb.get_current_url()}, Title: {page_title})")
            sb.save_screenshot("login_failed.png")
            return False  # 账号/密码错误，重试无意义

    print("❌ 多次重试后仍登录失败")
    sb.save_screenshot("login_failed.png")
    return False


# ===== 登出 =====

def logout(sb):
    """清除当前账户的登录态（清 Cookie + Storage），准备切换下一个账户"""
    print("\n🚪 登出当前账户...")
    try:
        sb.execute_cdp_cmd("Network.clearBrowserCookies", {})
    except Exception:
        try:
            sb.driver.delete_all_cookies()
        except Exception:
            pass

    try:
        sb.execute_cdp_cmd("Storage.clearDataForOrigin", {
            "origin": BASE_URL,
            "storageTypes": "local_storage,session_storage"
        })
    except Exception:
        try:
            sb.execute_script("localStorage.clear(); sessionStorage.clear();")
        except Exception:
            pass

    # 用 uc_open_with_reconnect 重新走一遍 Cloudflare，
    # 让下一个账户拿到干净的验证会话（避免被判定为同设备多账户登录）
    try:
        sb.uc_open_with_reconnect(BASE_URL + "/auth/login", reconnect_time=5)
    except Exception:
        sb.open(BASE_URL + "/auth/login")
    time.sleep(4)

    # 确认已回到登录页
    try:
        sb.wait_for_element('input[name="email"]', timeout=10)
        print("✅ 已返回到登录页")
    except Exception:
        print("⚠️ 未检测到登录表单，强制刷新...")
        try:
            sb.open(BASE_URL + "/auth/login")
        except Exception:
            pass
        time.sleep(3)


# ===== 自动续期 =====

def _read_alert(sb):
    """读取页面第一个 Bootstrap alert 的文本，找不到返回空串"""
    try:
        el = sb.find_element("div.alert", timeout=4)
        return (el.text or "").strip()
    except Exception:
        return ""


def _goto_server_detail(sb, email: str, node_link: str = "") -> bool:
    """进入服务器详情页。

    如果 node_link 有值，直接导航到该链接；
    否则在 Dashboard 首页查找并点击 See 链接。
    """
    print("\n🖥️  正在进入服务器续期页...")
    time.sleep(5)

    # ===== 如果配置了 node_link，直接跳转 =====
    if node_link:
        print(f"🔗 使用 node_link: {node_link}")
        sb.open(node_link)
        time.sleep(5)

        # 跳转后检查是否提示"无法续期"
        alert_text = _read_alert(sb)
        if alert_text and "can't renew" in alert_text.lower():
            print(f"ℹ️  页面顶部提示: {alert_text}")
            send_tg_message("ℹ️", "⚠️ 未到续期时间", alert_text, email)
            return False

        print(f"📄 当前页面: {sb.get_current_url()}")
        return True

    # ===== 没有 node_link，走自动查找 =====
    selectors = [
        'a[href*="/servers/edit?id="]',
        'td a[href*="/servers/edit"]',
        'table a[href*="/servers/edit"]',
        'table td a',
    ]

    see_link = None
    for sel in selectors:
        try:
            see_link = sb.find_element(sel, timeout=8)
            print(f"✅ 通过选择器找到链接: {sel}")
            break
        except Exception:
            continue

    # 选择器全部失败，尝试通过文本内容查找
    if see_link is None:
        print("⚠️ 选择器未命中，尝试文本匹配...")
        try:
            for a in sb.find_elements("a"):
                if (a.text or "").strip().lower() == "see":
                    see_link = a
                    print("✅ 通过文本 'See' 找到链接")
                    break
        except Exception:
            pass

    if see_link is None:
        cur_url = sb.get_current_url()
        title = sb.get_title() or ""
        print(f"❌ 未找到 'See' 链接")
        print(f"当前 URL: {cur_url}")
        print(f"页面标题: {title}")
        try:
            links = sb.find_elements("a")
            print(f"     页面共 {len(links)} 个链接:")
            for a in links[:20]:
                href = a.get_attribute("href") or ""
                txt  = (a.text or "").strip()[:30]
                if href:
                    print(f"       - [{txt}] -> {href}")
        except Exception:
            pass
        sb.save_screenshot("servers_page_fail.png")
        return False

    print("🖱️  点击 'See' 进入服务器详情页...")
    see_link.click()
    time.sleep(5)
    print(f"📄 当前页面: {sb.get_current_url()}")
    return True


def _open_renew_modal(sb) -> bool:
    """滚动到 Renew 按钮并点击，打开模态框"""
    print("\n🔄 查找 Renew 按钮...")
    try:
        renew_btn = sb.find_element('button[data-bs-target="#renew-modal"]', timeout=10)
    except Exception:
        try:
            renew_btn = sb.find_element('button.btn.btn-outline-primary', timeout=5)
        except Exception:
            print("  ❌ 未找到 Renew 按钮")
            return False

    sb.execute_script("""
        (function(){
            var btn = document.querySelector('button[data-bs-target="#renew-modal"]')
                     || document.querySelector('button.btn.btn-outline-primary');
            if (btn) btn.scrollIntoView({behavior:'smooth',block:'center'});
        })()
    """)
    time.sleep(0.8)
    renew_btn.click()
    print("🖱️ 已点击 Renew 按钮，等待 ALTCHA 验证框...")
    time.sleep(3)

    try:
        sb.find_element('div.modal.show', timeout=5)
        print("✅ Renew 模态框已弹出")
        return True
    except Exception:
        print("⚠️ 模态框未弹出")
        return False


def _solve_altcha(sb) -> bool:
    """处理 ALTCHA 人机验证"""
    print("\n🔐 处理 ALTCHA 人机验证...")
    time.sleep(2)

    if sb.execute_script(_ALTCHA_SOLVED_JS):
        print("✅ ALTCHA 已自动通过")
        return True

    coords = None
    try:
        coords = sb.execute_script(_ALTCHA_EXPAND_JS)
    except Exception:
        pass

    if coords:
        print(f"  📍 找到模态框内 iframe 坐标: ({coords['cx']}, {coords['cy']})")

    for attempt in range(3):
        if sb.execute_script(_ALTCHA_SOLVED_JS):
            print(f"✅ ALTCHA 验证通过（第 {attempt + 1} 轮）")
            return True

        if coords:
            try:
                wi = sb.execute_script(_WININFO_JS)
            except Exception:
                wi = {"sx": 0, "sy": 0, "oh": 800, "ih": 768}
            bar = wi["oh"] - wi["ih"]
            ax  = coords["cx"] + wi["sx"]
            ay  = coords["cy"] + wi["sy"] + bar
            print(f"🖱️  ALTCHA点击复选框  ({ax}, {ay})")
            _xdotool_click(ax, ay)

        try:
            iframes = sb.find_elements('div.modal.show iframe')
            for iframe in iframes:
                try:
                    iframe.click()
                    print("🖱️  SeleniumBase 点击模态框 iframe")
                except Exception:
                    pass
        except Exception:
            pass

        sb.execute_script("""
            (function(){
                var modal = document.querySelector('div.modal.show');
                if (!modal) return;
                var iframes = modal.querySelectorAll('iframe');
                for (var i = 0; i < iframes.length; i++) {
                    iframes[i].click();
                    iframes[i].dispatchEvent(new MouseEvent('click', {bubbles:true}));
                }
                var labels = modal.querySelectorAll('label');
                for (var j = 0; j < labels.length; j++) {
                    var txt = (labels[j].textContent || '').toLowerCase();
                    if (txt.includes('robot') || txt.includes('captcha') || txt.includes('verify'))
                        labels[j].click();
                }
                var cbs = modal.querySelectorAll('input[type="checkbox"]');
                for (var k = 0; k < cbs.length; k++) {
                    if (!cbs[k].disabled) {
                        cbs[k].click();
                        cbs[k].dispatchEvent(new MouseEvent('click', {bubbles:true}));
                    }
                }
            })()
        """)

        for _ in range(6):
            time.sleep(1)
            if sb.execute_script(_ALTCHA_SOLVED_JS):
                print(f"✅ ALTCHA 验证通过（第 {attempt + 1} 轮）")
                return True

        print(f"  ⚠️ 第 {attempt + 1} 轮未通过，重试...")
        try:
            new_coords = sb.execute_script(_ALTCHA_EXPAND_JS)
            if new_coords:
                coords = new_coords
        except Exception:
            pass

    print("  ❌ ALTCHA 5 轮均失败")
    return False


def _submit_renew(sb):
    """点击模态框内的 Renew 提交按钮"""
    print("🖱️  点击模态框中的 Renew 按钮...")
    try:
        submit = sb.find_element('div.modal.show button.btn-primary', timeout=5)
        submit.click()
    except Exception:
        sb.execute_script("""
            (function(){
                var m = document.querySelector('div.modal.show');
                if (!m) return;
                var bs = m.querySelectorAll('button');
                for (var i = 0; i < bs.length; i++)
                    if (/renew/i.test(bs[i].textContent)) bs[i].click();
            })()
        """)
    time.sleep(3)


def _check_renew_result(sb, email: str):
    """读取页面 alert 提示，判断续期结果并推送 TG 通知"""
    print("\n📋 检查续期结果...")
    alert_text = _read_alert(sb)
    if not alert_text:
        time.sleep(3)
        alert_text = _read_alert(sb)

    if alert_text:
        print(f"📩 页面提示: {alert_text}")
        low = alert_text.lower()
        if "can't renew" in low or "unable" in low:
            send_tg_message("⏳", "未到续期时间", alert_text, email)
        elif any(kw in low for kw in ("renewed", "success", "extended")):
            send_tg_message("✅", "续期成功", alert_text, email)
        else:
            send_tg_message("ℹ️", "续期操作已执行", alert_text, email)
    else:
        print("ℹ️ 未检测到明确的提示框，可能续期操作未生效")
        send_tg_message("ℹ️", "续期操作已执行", "未检测到明确提示", email)


def renew_server(sb, email: str, node_link: str = ""):
    """登录成功后调用：自动进入详情页 -> Renew -> ALTCHA -> 提交"""
    print("\n" + "#" * 25)
    print(f"  开始自动续期: {mask_email(email)}")
    print("#" * 25)

    if not _goto_server_detail(sb, email, node_link):
        return

    if not _open_renew_modal(sb):
        return

    altcha_ok = _solve_altcha(sb)
    if not altcha_ok:
        print("⚠️ ALTCHA 验证未通过，仍尝试提交 Renew...")

    _submit_renew(sb)
    _check_renew_result(sb, email)


# ===== 脚本入口 =====

def main():
    print("#" * 30)
    print("   Katabump 多账户自动续期")
    print("#" * 30)

    # 加载账户列表
    accounts = load_accounts()
    if not accounts:
        print("❌ 没有有效账户，程序退出。")
        return

    # 代理配置（全局生效）
    IS_PROXY = os.environ.get("IS_PROXY", "false").lower() == "true"
    proxy_str = os.environ.get("PROXY_SERVER", "").strip() or "http://127.0.0.1:1081"

    # 统计
    total = len(accounts)
    success_count = 0
    fail_count = 0

    for idx, acct in enumerate(accounts, start=1):
        email    = acct["email"]
        password = acct["password"]
        node_link = acct.get("node_link", "")

        if node_link:
            print(f"   🔗 已配置 node_link")

        print("\n" + "=" * 40)
        print(f"📌 正在处理第 {idx}/{total} 个账户: {mask_email(email)}")
        print("=" * 40)

        # 每个账户独立浏览器上下文：
        # 账户1 用普通窗口，账户2起用隐身窗口(incognito)彻底隔离 Cloudflare 状态
        sb_kwargs = {"uc": True, "headless": False}
        if idx > 1:
            sb_kwargs["incognito"] = True
            print("🕶️ 使用隐身窗口（隔离 Cloudflare 会话）")
        if IS_PROXY:
            print(f"🔗 挂载代理: {proxy_str}")
            sb_kwargs["proxy"] = proxy_str
        else:
            print("🌐 未使用代理，直连访问")

        try:
            with SB(**sb_kwargs) as sb:
                # 显示出口 IP
                try:
                    sb.open("https://api.ip.sb/ip")
                    print(f"🌐 当前出口IP: {sb.get_text('body')}")
                except Exception:
                    pass

                # 登录
                if not login(sb, email, password):
                    print(f"\n❌ 账户 [{mask_email(email)}] 登录失败，跳过续期。")
                    send_tg_message("❌", "登录失败", "未知", email)
                    fail_count += 1
                    continue  # 下一账户会开新浏览器，无需登出

                # 续期
                renew_server(sb, email, node_link)

                success_count += 1

        except Exception as e:
            print(f"\n❌ 账户 [{mask_email(email)}] 处理异常: {e}")
            send_tg_message("❌", "运行异常", str(e)[:200], email)
            fail_count += 1
            continue

    # 最终统计
    print("\n" + "=" * 40)
    print(f"📊 执行完毕: 共 {total} 个账户")
    print(f"   ✅ 成功: {success_count}")
    print(f"   ❌ 失败: {fail_count}")
    print("=" * 40)


if __name__ == "__main__":
    main()
