#!/usr/bin/env python3
"""
Creality Cloud login helper for the CrealityBridge.

Replaces the manual "download the .tk key file and paste it" step:

    sign in  ->  find your printer  ->  fetch its key
    ->  importDevice  ->  save jwt_token + tb_token into config.json

Two ways to sign in:
  * Browser (works with Google, Apple, Facebook, X or email accounts):
    the script prints a link, you sign in on any computer or phone, then
    paste back the address the browser ends up on.
  * Email + password, typed at a hidden prompt, used once, never saved.
Only the tokens are written to config.json (file mode 600, .bak made first).

The key (jwt_token) is short-lived (about 7 days). That is fine: the MQTT
token (tb_token) it provisions is what the bridge keeps using. If the printer
ever shows offline in the Creality app, just run this again:

    python3 creality_login.py --config ~/CrealityBridge/bridge/config.json
    sudo systemctl restart creality_bridge.service

Standard library only (Python 3.7+).
"""

from __future__ import annotations

import argparse
import base64
import datetime
import getpass
import json
import os
import platform
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

WWW = "https://www.crealitycloud.com"
API = "https://api.crealitycloud.com"
ID_LOGIN = "https://id.creality.com/api/cxy/account/v2/loginV2"
OAUTH_LOGIN = WWW + "/api/cxy/account/v2/oauthLogin"
WEB_CLIENT_ID = "f9c302ecc29c59a0a6e921ff39a073ca"
BROWSER_BACK = "http://localhost:18765/login"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


class _Key(str):
    """Keep header names exactly as written (the Creality gateway is picky)."""

    def capitalize(self):
        return self

    def title(self):
        return self


def _exact(headers):
    return {_Key(k): v for k, v in headers.items()}


def _request(url, payload, headers):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=_exact(headers), method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} from {url.split('/api/')[-1]}")
    try:
        return json.loads(body)
    except ValueError:
        raise RuntimeError(f"unreadable reply from {url.split('/api/')[-1]}")


def _api(base, path, payload, uid, token, duid):
    headers = {
        "Content-Type": "application/json; charset=UTF-8",
        "Accept": "application/json, text/plain, */*",
        "__CXY_APP_ID_": "creality_model",
        "__CXY_APP_VER_": "7.3.28",
        "__CXY_OS_LANG_": "0",
        "__CXY_DUID_": duid,
        "__CXY_OS_VER_": platform.system(),
        "__CXY_PLATFORM_": "14",
        "__CXY_REQUESTID_": str(uuid.uuid4()),
        "__CXY_UID_": str(uid),
        "__CXY_TOKEN_": token,
        "Origin": WWW,
        "Referer": WWW + "/",
        "User-Agent": UA,
        "Cookie": f"cre-token={token}; model_token={token}; model_user_id={uid}",
    }
    data = _request(base + path, payload, headers)
    if data.get("code") != 0:
        raise RuntimeError(f"{path.rsplit('/', 1)[-1]} failed: code {data.get('code')} {data.get('msg')}")
    return data.get("result")


def login(email, password, duid):
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "Accept": "application/json, text/plain, */*",
        "__cxy_app_ch_": "Chrome 157.0.0.0",
        "__cxy_app_id_": "cxy-gen2",
        "__cxy_app_ver_": "0.0.1",
        "__cxy_duid_": duid,
        "__cxy_os_lang_": "0",
        "__cxy_os_ver_": platform.system(),
        "__cxy_platform_": "2",
        "__cxy_requestid_": str(uuid.uuid4()),
        "__cxy_timezone_": str(int(datetime.datetime.now().astimezone().utcoffset().total_seconds())),
        "Origin": "https://id.creality.com",
        "Referer": "https://id.creality.com/",
        "User-Agent": UA,
    }
    data = _request(ID_LOGIN, {"type": 2, "account": email, "password": password}, headers)
    if data.get("code") not in (0, None):
        raise RuntimeError(data.get("msg") or f"login rejected (code {data.get('code')})")
    res = data.get("result") or {}
    uid = str(res.get("userId") or res.get("uid") or "")
    token = str(res.get("token") or res.get("accessToken") or "")
    if not uid or not token:
        raise RuntimeError("login succeeded but no user id / token came back")
    # confirm the session is accepted by the main API before going further
    _api(WWW, "/api/cxy/v2/common/getOssInfo", {}, uid, token, duid)
    return uid, token


def browser_redirect_uri():
    return WWW + "/oauth?back_url=" + urllib.parse.quote(BROWSER_BACK, safe="")


def browser_login_link():
    r = browser_redirect_uri()
    return "https://id.creality.com/?" + urllib.parse.urlencode({
        "lang": "en-US",
        "client_id": WEB_CLIENT_ID,
        "app_id": "cxy-gen2",
        "redirect_uri": r,
        "platform": "-100",
        "back_url": r,
        "response_type": "code",
    })


def code_from_pasted(text):
    """Accept the full address the browser ended on, or just the code."""
    text = (text or "").strip().strip('"').strip("'")
    if "code=" in text:
        query = urllib.parse.urlparse(text).query or text.split("?", 1)[-1]
        return (urllib.parse.parse_qs(query).get("code") or [""])[0].strip()
    if text and " " not in text and "/" not in text:
        return text
    return ""


def browser_login(code, duid):
    headers = {
        "Content-Type": "application/json; charset=UTF-8",
        "Accept": "application/json, text/plain, */*",
        "__CXY_PLATFORM_": "6",
        "__CXY_APP_ID_": "creality_model",
        "__CXY_OS_LANG_": "0",
        "__CXY_TIMEZONE_": "0",
        "__CXY_REQUESTID_": str(uuid.uuid4()),
        "User-Agent": UA,
    }
    r = browser_redirect_uri()
    data = _request(OAUTH_LOGIN, {"clientId": WEB_CLIENT_ID, "code": code,
                                  "redirecturi": r, "redirectUri": r}, headers)
    if data.get("code") not in (0, None):
        raise RuntimeError(data.get("msg") or f"browser sign-in rejected (code {data.get('code')})")
    res = data.get("result") or {}
    uid = str(res.get("userId") or (res.get("user_info") or {}).get("userId") or "")
    token = str(res.get("token") or res.get("access_token") or "")
    if not uid or not token:
        raise RuntimeError("browser sign-in worked but no user id / token came back")
    _api(WWW, "/api/cxy/v2/common/getOssInfo", {}, uid, token, duid)
    return uid, token


def ask_browser_code():
    print()
    print("  1. Open this link in a web browser on any computer or phone:")
    print()
    print("     " + browser_login_link())
    print()
    print("  2. Sign in (Google, Apple, Facebook, X or email all work).")
    print("  3. The browser then shows an error like \"can't reach localhost\". That is expected.")
    print("     Copy the whole address from the address bar (it starts with http://localhost:18765/login?code=)")
    print()
    while True:
        code = code_from_pasted(input("Paste the address here: "))
        if code:
            return code
        print("That doesn't contain a code. Paste the full address that starts with http://localhost:18765/login?code=")


def list_printers(uid, token, duid):
    result = _api(
        WWW, "/api/rest/print/cluster/ctl/getAvailableDevices",
        {"page": 1, "pageSize": 50, "includeMatched": True}, uid, token, duid,
    )
    items = []
    if isinstance(result, dict):
        for k in ("list", "deviceList", "records", "items"):
            if isinstance(result.get(k), list):
                items = result[k]
                break
    elif isinstance(result, list):
        items = result
    printers = []
    for it in items:
        if not isinstance(it, dict):
            continue
        nid = ""
        for v in (it.get("oldId"), it.get("deviceId")):
            if str(v or "").strip().isdigit() and len(str(v).strip()) >= 5:
                nid = str(v).strip()
                break
        if not nid:
            continue
        name = ""
        for k in ("aliasName", "nickName", "customName", "name"):
            if it.get(k):
                name = str(it[k])
                break
        printers.append({"name": name or "(unnamed)", "id": nid, "model": str(it.get("model") or "")})
    return printers


def get_key_jwt(uid, token, duid, dev_id):
    result = _api(WWW, "/api/cxy/v2/device/user/getToken", {"id": str(dev_id)}, uid, token, duid)
    jwt = str((result or {}).get("keyFileToken") or "")
    if jwt.count(".") != 2:
        raise RuntimeError("the cloud did not return a key for that printer")
    return jwt


def jwt_expiry_days(jwt):
    try:
        p = jwt.split(".")[1]
        p += "=" * (-len(p) % 4)
        exp = json.loads(base64.urlsafe_b64decode(p)).get("exp")
        return (exp - time.time()) / 86400 if exp else None
    except Exception:
        return None


def import_device(jwt, mac, model):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "__CXY_JWTOKEN_": jwt,
        "__CXY_OS_VER_": "v0.0.1",
        "_CXY_OS_LANG_": "1",
        "__CXY_PLATFORM_": "5",
        "__CXY_DUID_": "234",
        "__CXY_APP_ID_": "creality_model",
        "__CXY_REQUESTID_": "Raspberry" + str(int(time.time()) % 10000) + "10" + str(int(time.time() * 1000) % 89999 + 10000),
        "User-Agent": UA,
    }
    data = _request(
        API + "/api/cxy/v2/device/user/importDevice",
        {"mac": mac, "iotType": 1, "deviceName": model}, headers,
    )
    if data.get("code") != 0:
        raise RuntimeError(f"importDevice failed: code {data.get('code')} {data.get('msg')}")
    tb = str((data.get("result") or {}).get("tbToken") or "")
    if not tb:
        raise RuntimeError("importDevice worked but returned no MQTT token")
    return tb


def save_config(path, updates):
    cfg = {}
    if os.path.exists(path):
        with open(path) as f:
            cfg = json.load(f)
        shutil.copy2(path, path + ".bak")
    cfg.update(updates)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, indent=4)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def main():
    ap = argparse.ArgumentParser(description="Sign in to Creality Cloud and provision this bridge (no .tk file needed).")
    ap.add_argument("--config", required=True, help="path to the bridge's config.json")
    ap.add_argument("--email", help="Creality Cloud email (asked if omitted)")
    ap.add_argument("--browser", action="store_true",
                    help="sign in through a web browser (Google, Apple, Facebook, X or email accounts)")
    ap.add_argument("--printer-name", help="cloud name of the printer (default: printer_name from config.json)")
    ap.add_argument("--device-id", help="numeric device id; skips printer discovery")
    ap.add_argument("--model", help="model string sent to the cloud (default: printer_model from config.json)")
    ap.add_argument("--no-restart-hint", action="store_true", help=argparse.SUPPRESS)  # used by install.sh
    args = ap.parse_args()

    cfg = {}
    if os.path.exists(args.config):
        try:
            with open(args.config) as f:
                cfg = json.load(f)
        except Exception as exc:
            print(f"[-] Could not read {args.config}: {exc}")
            return 1
    model = args.model or cfg.get("printer_model") or "Ender-3 S1 Pro"
    want_name = (args.printer_name or cfg.get("printer_name") or "").strip().lower()
    mac = uuid.UUID(int=uuid.getnode()).hex[-12:].upper()

    duid = str(uuid.uuid4())
    use_browser = args.browser
    if not use_browser and not args.email:
        print("How do you want to sign in to Creality Cloud?")
        print("  [1] Web browser  (Google, Apple, Facebook, X or email; works over SSH)")
        print("  [2] Email and password typed here")
        use_browser = input("Choose 1 or 2 [1]: ").strip() in ("", "1")

    try:
        if use_browser:
            code = ask_browser_code()
            print("[*] Signing in...")
            uid, token = browser_login(code, duid)
        else:
            email = (args.email or input("Creality Cloud email: ")).strip()
            password = getpass.getpass("Password (hidden, not saved): ")
            print("[*] Signing in...")
            uid, token = login(email, password, duid)
            password = ""
        print("[+] Signed in.")

        dev_id = (args.device_id or "").strip()
        if not dev_id:
            printers = list_printers(uid, token, duid)
            if not printers:
                print("[-] No printers found on this account. Add the device in the Creality Cloud app first")
                print("    (Workbench > + > Raspberry Pi), or pass --device-id.")
                return 2
            matches = [p for p in printers if want_name and p["name"].strip().lower() == want_name]
            if len(matches) == 1:
                chosen = matches[0]
                print(f"[+] Matched printer '{chosen['name']}'.")
            elif len(printers) == 1:
                chosen = printers[0]
                print(f"[+] Only one printer on the account: '{chosen['name']}'.")
            else:
                print("\nPrinters on your account:")
                for n, p in enumerate(printers, 1):
                    print(f"  [{n}] {p['name']}  {('(' + p['model'] + ')') if p['model'] else ''}")
                while True:
                    raw = input("Which one is this bridge for? Enter its number: ").strip()
                    if raw.isdigit() and 1 <= int(raw) <= len(printers):
                        chosen = printers[int(raw) - 1]
                        break
                    print("Please enter one of the numbers listed.")
            dev_id = chosen["id"]

        print("[*] Fetching the printer's key...")
        jwt = get_key_jwt(uid, token, duid, dev_id)
        days = jwt_expiry_days(jwt)
        if days is not None:
            print(f"[+] Got a key (valid about {days:.0f} days; only needed to provision).")

        print("[*] Registering this device with the cloud...")
        tb = import_device(jwt, mac, model)
        old_tb = str(cfg.get("tb_token") or "")
        save_config(args.config, {"jwt_token": jwt, "tb_token": tb})
        print("[+] Saved to config.json (no password is ever stored).")
        if args.no_restart_hint:
            pass
        elif old_tb and old_tb == tb:
            print("    The MQTT token is unchanged, so a running bridge needs no restart.")
        else:
            print("    New MQTT token saved. Restart the bridge to use it:")
            print("      sudo systemctl restart creality_bridge.service")
        return 0
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130
    except Exception as exc:
        print(f"[-] {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
