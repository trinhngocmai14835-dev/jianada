import sys
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))
sys.path.insert(0, os.path.join(_ROOT, "backend"))


def generate_license_for_machine(machine_id: str, days: int) -> str:
    from gen_license import generate_license
    from database import Database
    base_date = Database().get_latest_license_expiry(machine_id)
    return generate_license(machine_id.upper(), days, base_date)
