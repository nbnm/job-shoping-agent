"""Only fabricated email data is used here; no private exports are read."""
import email.message
import json
import mailbox
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

@unittest.skipUnless(shutil.which("duckdb"), "DuckDB CLI is needed for the synthetic import test")
class MailPipeline(unittest.TestCase):
    def test_rebuild_groups_order_and_flags_return(self):
        with tempfile.TemporaryDirectory(prefix="jop-mail-test-") as tmp:
            root = Path(tmp)
            source = root / "synthetic.mbox"
            box = mailbox.mbox(source)
            cases = [
                ("Order #DEMO1001 confirmed", "Order #DEMO1001\nOrder summary\nMerino Crewneck Sweater × 1\nNavy / M\nCAD $80.00\nSubtotal\nCAD $80.00", "one"),
                ("Your order #DEMO1001 has shipped", "Order #DEMO1001\nYour order has shipped.\nMerino Crewneck Sweater × 1\nNavy / M\nCAD $80.00", "two"),
                ("Return received for order #DEMO1001", "Order #DEMO1001\nWe received your return.\nMerino Crewneck Sweater × 1\nNavy / M\nCAD $80.00", "three"),
                ("Order #DEMO1002 confirmed", "Order #DEMO1002\nOrder summary\nWomen's Running Jacket × 1\nBlack / S\nCAD $120.00\nSubtotal\nCAD $120.00", "four"),
            ]
            for i,(subject,body,key) in enumerate(cases):
                msg = email.message.EmailMessage()
                msg["From"] = "Tracksmith <orders@tracksmith.com>"
                msg["To"] = "Demo Reader <reader@example.test>"
                msg["Subject"] = subject
                msg["Date"] = f"0{i+2} Feb 2026 15:00:00 +0000"
                msg["Message-ID"] = f"<{key}@example.test>"
                msg["X-Gmail-Labels"] = "Purchases"
                msg.set_content(body)
                box.add(msg)
            box.close()
            imported = subprocess.run(["python3",str(ROOT/"scripts/import_takeout_duckdb.py"),str(source),"--output-dir",str(root/"mail")],capture_output=True,text=True)
            self.assertEqual(imported.returncode,0,imported.stderr)
            target = root/"app/jop.db"
            cmd=["python3",str(ROOT/"scripts/rebuild_wardrobe_history.py"),"--source",str(root/"mail/mail.sqlite3"),"--target",str(target)]
            rebuilt=subprocess.run(cmd,capture_output=True,text=True)
            self.assertEqual(rebuilt.returncode,0,rebuilt.stderr)
            with sqlite3.connect(target) as db:
                rows=db.execute("SELECT name,return_status,reported_price,currency FROM wardrobe_purchase_timeline ORDER BY name").fetchall()
            self.assertEqual(len(rows),2,rows)
            self.assertEqual(rows[0][0],"Merino Crewneck Sweater")
            self.assertEqual(rows[0][1],"returned")
            self.assertEqual(rows[0][2:],(80.0,"CAD"))
            # Default source list survives a rerun; audience selection is opt-in.
            again=subprocess.run(["python3",str(ROOT/"scripts/rebuild_wardrobe_history.py"),"--target",str(target),"--audience","men"],capture_output=True,text=True)
            self.assertEqual(again.returncode,0,again.stderr)
            with sqlite3.connect(target) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM wardrobe_purchase_timeline").fetchone()[0],1)
            refused=subprocess.run(["python3",str(ROOT/"scripts/import_takeout_duckdb.py"),str(source),"--output-dir",str(root/"mail")],capture_output=True,text=True)
            self.assertNotEqual(refused.returncode,0)

if __name__=="__main__":
    unittest.main()
