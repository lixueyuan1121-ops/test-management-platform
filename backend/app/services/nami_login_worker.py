"""Isolated browser login worker. Credentials never travel in argv, stdout or logs.

Requires the separate Selenium runtime. Exit codes: 20 interactive verification,
21 rejected login, 22 runtime, 23 timeout after submit, 24 consent, 25 origin.
"""
import json
import hashlib
import logging
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import urlopen

LOGIN_URL = 'https://work.n.cn/launcher'
NAMI_URL = 'https://www.n.cn/download'
TRUSTED_HOSTS = {'work.n.cn', 'namiwork.n.cn', 'www.n.cn'}
CONFIRMED_TERMS = {
    'https://work.n.cn/agreement/aprotocol.html',
    'https://work.n.cn/agreement/aprivacy.html',
}


class LoginStopped(Exception):
    def __init__(self, code):
        self.code = code


def terms_snapshot():
    """Hash only the two public documents explicitly confirmed by the operator."""
    documents = {}
    for url in sorted(CONFIRMED_TERMS):
        with urlopen(url, timeout=10) as response:
            content = response.read(2 * 1024 * 1024 + 1)
            if response.geturl() != url or not 100 < len(content) <= 2 * 1024 * 1024:
                raise LoginStopped(24)
            documents[url] = hashlib.sha256(content).hexdigest()
    return documents


def terms_account(username):
    return hashlib.sha256(username.encode('utf-8')).hexdigest()


def may_reuse_terms(config, links):
    if links != CONFIRMED_TERMS:
        return False
    if config.get('_accept_current_terms_once') is True:
        return True
    approved = config.get('approved_terms') or {}
    if approved.get('account') != terms_account(config['username']):
        return False
    return approved.get('documents') == terms_snapshot()


def _trusted(url):
    parsed = urlsplit(url)
    return (parsed.scheme == 'https' and parsed.netloc in TRUSTED_HOSTS)


def _find(driver, xpath):
    """Find visible controls on the observed page or its Nami iframe only."""
    from selenium.webdriver.common.by import By
    driver.switch_to.default_content()
    if not _trusted(driver.current_url):
        raise LoginStopped(25)
    visible = [x for x in driver.find_elements(By.XPATH, xpath) if x.is_displayed()]
    if visible:
        return visible[0]
    for frame in driver.find_elements(By.TAG_NAME, 'iframe'):
        if not _trusted(frame.get_attribute('src') or ''):
            continue
        driver.switch_to.frame(frame)
        visible = [x for x in driver.find_elements(By.XPATH, xpath) if x.is_displayed()]
        if visible:
            return visible[0]
        driver.switch_to.default_content()
    return None


def _wait_control(driver, xpath, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        element = _find(driver, xpath)
        if element:
            return element
        time.sleep(0.2)
    raise LoginStopped(22)


def login(config):
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.common.exceptions import TimeoutException
    if config.get('terms_acknowledged') is not True:
        raise LoginStopped(24)
    options = webdriver.ChromeOptions()
    options.binary_location = config['browser_binary']
    for arg in ('--headless=new', '--lang=zh-CN', '--window-size=1365,900', '--no-first-run', '--no-default-browser-check', '--disable-dev-shm-usage'):
        options.add_argument(arg)
    options.add_experimental_option('prefs', {'intl.accept_languages': 'zh-CN,zh'})
    submitted = False
    # Parent owns this directory too, so timeout cleanup removes browser cookies.
    with tempfile.TemporaryDirectory(prefix='qalab-nami-browser-', dir=config.get('_profile_parent')) as profile:
        options.add_argument('--user-data-dir=' + profile)
        service = Service(config['driver_binary'], log_output=os.devnull)
        driver = webdriver.Chrome(service=service, options=options)
        try:
            driver.set_page_load_timeout(20)
            driver.get(LOGIN_URL)
            _wait_control(driver, "//button[normalize-space(.)='立即登录' or normalize-space(.)='Log in']").click()
            _wait_control(driver, "//a[normalize-space(.)='账号登录']").click()
            account = _wait_control(driver, "//input[@placeholder='手机号/用户名/邮箱' or @aria-label='手机号/用户名/邮箱']")
            agreement = _find(driver, "//label[contains(.,'已仔细阅读') or contains(.,'用户服务条款')]")
            if agreement:
                checkbox = agreement.find_elements(By.CSS_SELECTOR, "input[type='checkbox']")
                if checkbox and not checkbox[0].is_selected():
                    links = {x.get_attribute('href') for x in agreement.find_elements(By.TAG_NAME, 'a')}
                    if not may_reuse_terms(config, links):
                        # An unchecked consent is a human step, not a credential failure.
                        raise LoginStopped(24)
                    checkbox[0].click()
                    if not checkbox[0].is_selected():
                        raise LoginStopped(24)
            _wait_control(driver, "//input[@placeholder='手机号/用户名/邮箱' or @aria-label='手机号/用户名/邮箱']").send_keys(config['username'])
            _wait_control(driver, "//input[@type='password']").send_keys(config['password'])
            submit = _wait_control(driver, "//input[@type='submit'] | //button[normalize-space(.)='登录']")
            submitted = True
            submit.click()
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                driver.switch_to.default_content()
                if not _trusted(driver.current_url):
                    raise LoginStopped(25)
                password_field = _find(driver, "//input[@type='password']")
                # Login errors/challenges are rendered inside the Nami iframe.
                body = driver.find_element(By.TAG_NAME, 'body').text
                if any(x in body for x in ('拖动滑块', '请完成验证', '短信验证码', '请输入验证码', '安全验证', '验证身份')):
                    raise LoginStopped(20)
                if any(x in body for x in ('账号或密码错误', '帐号或密码错误', '密码错误', '登录失败', '账号已锁定')):
                    raise LoginStopped(21)
                driver.switch_to.default_content()
                cookies = {x['name']: x['value'] for x in driver.get_cookies()}
                if cookies.get('Q') and cookies.get('T') and password_field is None:
                    break
                time.sleep(0.3)
            else:
                raise LoginStopped(23)
            driver.switch_to.default_content()
            driver.get(NAMI_URL)
            if urlsplit(driver.current_url).hostname != 'www.n.cn':
                raise LoginStopped(25)
            cookies = driver.get_cookies()
            values = {x['name']: x['value'] for x in cookies if x.get('domain', '').lstrip('.') in ('n.cn', 'www.n.cn')}
            if not values.get('Q') or not values.get('T'):
                raise LoginStopped(21)
            return '; '.join(f'{name}={value}' for name, value in values.items())
        except TimeoutException:
            raise LoginStopped(23 if submitted else 22) from None
        except LoginStopped:
            raise
        except Exception:
            # Once credentials were submitted, do not retry an unknown outcome.
            raise LoginStopped(23 if submitted else 22) from None
        finally:
            driver.quit()


def main():
    logging.disable(logging.CRITICAL)
    try:
        path, output = map(Path, sys.argv[1:])
        info = path.stat()
        if stat.S_IMODE(info.st_mode) & 0o077 or info.st_uid != os.getuid():
            return 22
        config = json.loads(path.read_text(encoding='utf-8'))
        config['_profile_parent'] = str(output.parent)
        cookie = login(config)
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump({'cookie': cookie}, stream)
        return 0
    except LoginStopped as error:
        return error.code
    except Exception:
        return 22


if __name__ == '__main__':
    sys.exit(main())
