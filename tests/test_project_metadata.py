from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProjectMetadataTests(unittest.TestCase):
    def test_readme_has_security_and_unofficial_client_disclosures(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Unofficial community project", readme)
        self.assertIn("Windows DPAPI", readme)
        self.assertIn("not affiliated with Google", readme)
        self.assertIn("justdataplease.com", readme)
        self.assertNotIn("�", readme)

    def test_windows_metadata_uses_current_product_name(self) -> None:
        metadata = (ROOT / "assets" / "version_info.txt").read_text(encoding="utf-8")
        self.assertIn("StickyOmelet", metadata)
        self.assertIn("justdataplease.com", metadata)
        self.assertNotIn("JustNotes", metadata)

    def test_no_local_settings_file_is_part_of_the_source_tree(self) -> None:
        self.assertFalse((ROOT / "settings.json").exists())

    def test_claude_release_skill_enforces_portable_only_builds(self) -> None:
        skill = (ROOT / ".claude" / "skills" / "release-stickyomelet" / "SKILL.md").read_text(encoding="utf-8")
        script = (
            ROOT / ".claude" / "skills" / "release-stickyomelet" / "scripts" / "prepare_release.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("name: release-stickyomelet", skill)
        self.assertIn("one release artifact", skill)
        self.assertIn("PortableOnly = $true", script)
        self.assertIn("StickyOmelet-Setup-*.exe", script)


if __name__ == "__main__":
    unittest.main()
