"""Regression coverage for owner window lifecycle and multi-tab close behavior."""
import threading
import time
import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bridge.owner_lifecycle import OwnerLifecycle

class OwnerLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.stopped=threading.Event()
        self.life=OwnerLifecycle(self.stopped.set,grace=.12,expiry=.25,interval=.01)
        self.a='a'*32;self.b='b'*32
    def test_last_tab_close_stops_service(self):
        self.life.update(self.a,'open');self.life.update(self.a,'close')
        self.assertTrue(self.stopped.wait(1))
    def test_closing_one_of_two_tabs_does_not_stop_service(self):
        self.life.update(self.a,'open');self.life.update(self.b,'open');self.life.update(self.a,'close')
        self.assertFalse(self.stopped.wait(.16));self.life.update(self.b,'close');self.assertTrue(self.stopped.wait(1))
    def test_reload_registers_new_tab_within_grace(self):
        self.life.update(self.a,'open');self.life.update(self.a,'close');time.sleep(.04);self.life.update(self.b,'open')
        self.assertFalse(self.stopped.wait(.15));self.life.update(self.b,'close');self.assertTrue(self.stopped.wait(1))
    def test_late_close_from_old_document_does_not_close_reloaded_tab(self):
        old='c'*32;new='d'*32
        self.life.update(self.a,'open',old);self.life.update(self.a,'open',new);self.life.update(self.a,'ping',old);self.life.update(self.a,'close',old)
        self.assertFalse(self.stopped.wait(.15));self.life.update(self.a,'close',new);self.assertTrue(self.stopped.wait(1))
    def test_abandoned_tab_expires(self):
        self.life.update(self.a,'open');self.assertTrue(self.stopped.wait(1))
    def test_invalid_and_unknown_close_do_not_stop(self):
        with self.assertRaises(ValueError):self.life.update('bad','open')
        self.life.update(self.a,'open');self.life.update(self.b,'close');self.assertFalse(self.stopped.wait(.15))
        self.life.update(self.a,'close');self.assertTrue(self.stopped.wait(1))
if __name__=='__main__':unittest.main()
