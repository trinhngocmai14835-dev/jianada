"""Interactive console menu for R2 account whitelist management."""
import json
import os
import re
import shutil
import subprocess

import account_whitelist_admin as admin


def clear():
    os.system("cls")


def pause():
    input("\n按回车返回菜单...")


def parse_accounts(text):
    parts = re.split(r"[\s,，]+", str(text or "").strip())
    return admin._clean_accounts(parts)


def ask_machine_id():
    mid = input("请输入机器码：").strip().upper()
    if not mid:
        print("机器码不能为空。")
        return None
    if len(mid) != 16 or not mid.isalnum():
        print("机器码格式不对，应该是 16 位字母/数字。")
        return None
    return mid


def print_record(record):
    print(json.dumps(record, ensure_ascii=False, indent=2))


def load_record(mid):
    record = admin._download(mid)
    record["machine_id"] = mid
    record["accounts"] = admin._clean_accounts(record.get("accounts") or [])
    return record


def save_record(mid, record):
    if not (shutil.which("npx.cmd") or shutil.which("npx")):
        raise RuntimeError("未找到 npx，请先安装 Node.js，或确认 npx 已加入 PATH。")
    return admin._upload(mid, record)


def query_record():
    clear()
    print("========== 查询机器码白名单 ==========\n")
    mid = ask_machine_id()
    if not mid:
        pause()
        return
    print()
    print_record(load_record(mid))
    pause()


def add_accounts():
    clear()
    print("========== 添加账号 ==========\n")
    mid = ask_machine_id()
    if not mid:
        pause()
        return
    accounts = parse_accounts(input("请输入要添加的账号（多个用空格分开）："))
    if not accounts:
        print("账号不能为空。")
        pause()
        return
    record = load_record(mid)
    record["accounts"] = sorted(set(admin._clean_accounts(record.get("accounts") or [])) | set(accounts))
    print("\n正在写入 R2...")
    print_record(save_record(mid, record))
    pause()


def remove_accounts():
    clear()
    print("========== 删除账号 ==========\n")
    mid = ask_machine_id()
    if not mid:
        pause()
        return
    accounts = parse_accounts(input("请输入要删除的账号（多个用空格分开）："))
    if not accounts:
        print("账号不能为空。")
        pause()
        return
    record = load_record(mid)
    current = set(admin._clean_accounts(record.get("accounts") or []))
    for account in accounts:
        current.discard(account)
    record["accounts"] = sorted(current)
    print("\n正在写入 R2...")
    print_record(save_record(mid, record))
    pause()


def set_accounts():
    clear()
    print("========== 覆盖设置账号 ==========\n")
    print("注意：这个操作会替换该机器码原来的全部账号。\n")
    mid = ask_machine_id()
    if not mid:
        pause()
        return
    accounts = parse_accounts(input("请输入最终保留的账号（多个用空格分开，可留空清空）："))
    confirm = input("确认覆盖？输入 YES 继续：").strip().upper()
    if confirm != "YES":
        print("已取消。")
        pause()
        return
    record = load_record(mid)
    record["accounts"] = accounts
    print("\n正在写入 R2...")
    print_record(save_record(mid, record))
    pause()


def main():
    while True:
        clear()
        print("============================================")
        print("        R2 账号白名单管理")
        print("============================================")
        print()
        print("说明：")
        print("  机器码从客户软件『授权管理』页面复制。")
        print("  账号可以一次输入多个，用空格分开。")
        print()
        print("1. 查询机器码白名单")
        print("2. 添加账号")
        print("3. 删除账号")
        print("4. 覆盖设置账号")
        print("5. 退出")
        print()
        choice = input("请选择 1-5：").strip()
        try:
            if choice == "1":
                query_record()
            elif choice == "2":
                add_accounts()
            elif choice == "3":
                remove_accounts()
            elif choice == "4":
                set_accounts()
            elif choice == "5":
                return 0
            else:
                print("\n输入无效，请重新选择。")
                pause()
        except SystemExit as exc:
            print(f"\n操作失败：{exc}")
            pause()
        except subprocess.CalledProcessError as exc:
            print(f"\n写入 R2 失败，wrangler 返回码：{exc.returncode}")
            pause()
        except Exception as exc:
            print(f"\n操作失败：{exc}")
            pause()


if __name__ == "__main__":
    raise SystemExit(main())
