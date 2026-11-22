"""Check privacy rules using synthetic strings, without touching private files."""
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from check_public import inspect_bytes, scan

class PublicationChecks(unittest.TestCase):
    def test_private_paths_and_headers_are_flagged_without_echoing_values(self):
        value = b"reader" + b"@" + b"gmail.com"
        result = inspect_bytes("README.md", value)
        self.assertEqual(result, [{"path":"README.md","rule":"personal-mail-address"}])
        self.assertEqual(inspect_bytes("data/mail.sqlite3",b"binary")[0]["rule"],"private-data-file")
        self.assertEqual(inspect_bytes("scripts/example.py",b"reader@example.test"),[])

    def test_clean_source_zip_does_not_require_git(self):
        with tempfile.TemporaryDirectory(prefix="jop-public-test-") as tmp:
            root=Path(tmp)
            (root/"README.md").write_text("# A fictional demo\n")
            (root/"data").mkdir()
            (root/"data"/"mail.sqlite3").write_text("private fixture, never a release input")
            result=scan(root)
            self.assertEqual(result["files_checked"],1)
            self.assertEqual(result["findings"],[])
            self.assertTrue(scan(root,history=True)["history_unavailable"])

if __name__=="__main__":
    unittest.main()
