"""Settlement helpers for draw/issue based betting services."""
import re
import time


def issue_num(issue):
    """Return the numeric issue id, or None when it cannot be parsed."""
    if issue is None:
        return None
    m = re.search(r"\d+", str(issue))
    return int(m.group(0)) if m else None


def issue_gap(current_issue, anchor_issue):
    """Return current - anchor when both issues are parseable."""
    current = issue_num(current_issue)
    anchor = issue_num(anchor_issue)
    if current is None or anchor is None:
        return None
    return current - anchor


def is_newer_issue(current_issue, anchor_issue):
    """True only when current_issue is strictly newer than anchor_issue."""
    gap = issue_gap(current_issue, anchor_issue)
    return gap is not None and gap > 0


def _normalize_draw(raw):
    if not raw or len(raw) != 2:
        return None
    issue, nums = raw
    if issue_num(issue) is None:
        return None
    if not isinstance(nums, (list, tuple)) or len(nums) != 3:
        return None
    try:
        balls = [int(n) for n in nums]
    except (TypeError, ValueError):
        return None
    if any(n < 0 or n > 9 for n in balls):
        return None
    return str(issue), tuple(balls)


def read_stable_draw(page, reader, samples=2, delay=0.25):
    """Read latest draw repeatedly and accept it only when it is stable."""
    first = None
    for idx in range(max(1, int(samples))):
        try:
            current = _normalize_draw(reader(page))
        except Exception:
            return None
        if current is None:
            return None
        if first is None:
            first = current
        elif current != first:
            return None
        if idx < samples - 1 and delay > 0:
            time.sleep(delay)
    return first[0], list(first[1])
