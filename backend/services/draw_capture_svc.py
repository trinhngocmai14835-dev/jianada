from __future__ import annotations

from typing import Any

from services.draw_analysis_svc import DrawRecord, normalize_draw_records


RESULT_FRAME_MARKER = "/ResultHistory/Index"


def _as_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_browser_rows(rows: Any, limit: int = 500) -> list[dict[str, Any]]:
    """Normalize raw rows extracted from the logged-in result-history page."""
    limit = min(2000, max(1, int(limit or 500)))
    if not isinstance(rows, list):
        return []

    parsed: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        issue = str(item.get("issue") or item.get("period") or "").strip()
        numbers = item.get("numbers") or []
        nums = [_as_int(v) for v in numbers[:3]] if isinstance(numbers, list) else []
        if len(nums) < 3 or any(v is None or v < 0 or v > 9 for v in nums):
            continue
        parsed.append(
            {
                "issue": issue,
                "draw_time": str(item.get("draw_time") or item.get("drawTime") or "").strip(),
                "numbers": [int(nums[0]), int(nums[1]), int(nums[2])],
            }
        )
        if len(parsed) >= limit:
            break

    records = normalize_draw_records(parsed)
    return [_record_with_extra(record, parsed) for record in records]


def _record_with_extra(record: DrawRecord, raw_records: list[dict[str, Any]]) -> dict[str, Any]:
    draw_time = ""
    for item in raw_records:
        if item.get("issue") == record.issue:
            draw_time = item.get("draw_time") or ""
            break
    return {
        "issue": record.issue,
        "draw_time": draw_time,
        "numbers": list(record.numbers),
        "total": record.total,
        "dx": record.dx,
        "ds": record.ds,
        "special": record.special,
    }


def capture_draw_records_from_chrome(port: int = 9333, limit: int = 500) -> dict[str, Any]:
    """Capture draw records from an already logged-in Chrome remote-debugging session."""
    port = int(port or 9333)
    limit = min(2000, max(20, int(limit or 500)))

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return {
            "ok": False,
            "message": f"Playwright 未就绪，无法连接浏览器：{exc}",
            "records": [],
        }

    endpoint = f"http://127.0.0.1:{port}"
    playwright = None
    frame_url = ""
    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.connect_over_cdp(endpoint)
        frame = _find_result_frame(browser)
        if frame is None:
            return {
                "ok": False,
                "message": f"未在 {port} 端口浏览器中找到“开奖记录”页面，请确认已登录并打开开奖结果。",
                "records": [],
            }
        frame_url = getattr(frame, "url", "") or ""
        try:
            frame.wait_for_selector("#betList tr", timeout=5000)
        except PlaywrightTimeoutError:
            return {
                "ok": False,
                "message": "已找到开奖记录页面，但表格还没有加载完成，请稍后重试。",
                "records": [],
            }
        rows = frame.evaluate(
            """() => {
                const clean = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                return Array.from(document.querySelectorAll('#betList tr')).map((tr, index) => {
                    const numbers = Array.from(tr.querySelectorAll('td.name span.ico'))
                        .slice(0, 3)
                        .map((el) => parseInt(clean(el.textContent), 10));
                    return {
                        index,
                        issue: clean(tr.querySelector('.period')?.textContent),
                        draw_time: clean(tr.querySelector('.drawTime')?.textContent),
                        numbers,
                    };
                });
            }"""
        )
    except PlaywrightError as exc:
        return {"ok": False, "message": f"连接浏览器失败：{exc}", "records": []}
    except Exception as exc:
        return {"ok": False, "message": f"抓取开奖记录失败：{exc}", "records": []}
    finally:
        # Do not call browser.close(); this is a customer's logged-in Chrome.
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass

    records = parse_browser_rows(rows, limit)
    if not records:
        return {
            "ok": False,
            "message": "没有解析到有效开奖记录，请确认当前页是加拿大28开奖结果列表。",
            "records": [],
        }
    return {
        "ok": True,
        "message": f"已从浏览器抓取 {len(records)} 条开奖记录",
        "records": records,
        "source": {"port": port, "url": frame_url},
    }


def _find_result_frame(browser: Any):
    for context in getattr(browser, "contexts", []) or []:
        for page in getattr(context, "pages", []) or []:
            for frame in getattr(page, "frames", []) or []:
                if RESULT_FRAME_MARKER in (getattr(frame, "url", "") or ""):
                    return frame
    return None