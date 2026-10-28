from __future__ import annotations

from eyeguide.domain.models import GpsFix


def format_gps_origin(fix: GpsFix) -> str:
    return f"{fix.latitude:.6f},{fix.longitude:.6f}"
