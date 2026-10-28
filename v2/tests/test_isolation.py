from pathlib import Path
import unittest

import eyeguide


class PackageIsolationTests(unittest.TestCase):
    def test_imports_v2_package(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        package_path = Path(eyeguide.__file__).resolve()
        self.assertTrue(package_path.is_relative_to(project_root / "src"), package_path)
