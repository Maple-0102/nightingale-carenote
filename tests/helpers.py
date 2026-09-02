import tempfile
import unittest
from pathlib import Path

from carenote.db import Database
from carenote.service import CareNoteService


class ServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="nightingale-tests-")
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.db = Database(self.db_path)
        self.db.initialize()
        self.db.seed_demo()
        self.service = CareNoteService(self.db)

    def tearDown(self):
        self.temp_dir.cleanup()
