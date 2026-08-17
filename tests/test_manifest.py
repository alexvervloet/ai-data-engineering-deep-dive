from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ai_data.manifest import load_manifest


class ManifestTests(unittest.TestCase):
    def test_course_manifest_loads_all_tenants(self) -> None:
        root = Path(__file__).resolve().parents[1]

        manifest = load_manifest(root / "corpus" / "manifest.json")

        self.assertEqual(manifest.managed_tenants, ("acme", "beta"))
        self.assertEqual(len(manifest.records), 3)
        self.assertEqual(
            {(record.tenant_id, record.external_id) for record in manifest.records},
            {
                ("acme", "engineering-handbook"),
                ("acme", "benefits-guide"),
                ("beta", "launch-plan"),
            },
        )

    def test_manifest_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corpus = root / "corpus"
            corpus.mkdir()
            (root / "outside.md").write_text("secret", encoding="utf-8")
            payload = {
                "managed_tenants": ["acme"],
                "documents": [
                    {
                        "tenant_id": "acme",
                        "external_id": "escape",
                        "path": "../outside.md",
                        "version": 1,
                        "readers": ["user:alex"],
                        "metadata": {},
                    }
                ],
            }
            path = corpus / "manifest.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "escapes the corpus"):
                load_manifest(path)


if __name__ == "__main__":
    unittest.main()
