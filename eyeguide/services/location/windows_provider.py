from __future__ import annotations

import asyncio
from typing import Optional

from eyeguide.domain.models import GpsFix

try:
    from winrt.windows.devices.geolocation import Geolocator, GeolocationAccessStatus
except ImportError:  # pragma: no cover
    Geolocator = None
    GeolocationAccessStatus = None


class WindowsLocationProvider:
    @property
    def is_available(self) -> bool:
        return Geolocator is not None and GeolocationAccessStatus is not None

    async def get_fix_async(self) -> Optional[GpsFix]:
        if not self.is_available:
            return None

        status = await Geolocator.request_access_async()
        if status != GeolocationAccessStatus.ALLOWED:
            return None

        geolocator = Geolocator()
        position = await geolocator.get_geoposition_async()
        coordinate = position.coordinate

        speed = float(coordinate.speed or 0.0)
        heading = float(coordinate.heading) if coordinate.heading is not None else None
        altitude = float(coordinate.altitude) if coordinate.altitude is not None else None

        return GpsFix(
            latitude=float(coordinate.latitude),
            longitude=float(coordinate.longitude),
            speed_mps=speed,
            accuracy_meters=float(coordinate.accuracy) if coordinate.accuracy is not None else None,
            heading_degrees=heading,
            altitude_meters=altitude,
            satellites=None,
            source="windows-location",
        )

    def get_fix(self) -> Optional[GpsFix]:
        return asyncio.run(self.get_fix_async())
