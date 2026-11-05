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
class LifecycleSwitch(unittest.TestCase):
    def test_matrix(self):
        from bridge.web import owner_lifecycle_enabled
        self.assertTrue(owner_lifecycle_enabled({}))
        self.assertTrue(owner_lifecycle_enabled({'ROUNDTABLE_OWNER_LIFECYCLE':'1'}))
        self.assertFalse(owner_lifecycle_enabled({'ROUNDTABLE_NO_BROWSER':'1'}))
        self.assertTrue(owner_lifecycle_enabled({'ROUNDTABLE_NO_BROWSER':'1','ROUNDTABLE_OWNER_LIFECYCLE':'1'}))

class LifecycleHeadless(unittest.TestCase):
    def test_headless_backend_exits_after_last_tab(self):
        import json,os,re,socket,subprocess,tempfile,urllib.request
        root=Path(__file__).resolve().parents[1]
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        with tempfile.TemporaryDirectory() as tmp:
            env=dict(os.environ,ROUNDTABLE_PORT=str(port),ROUNDTABLE_NO_BROWSER='1',
                     ROUNDTABLE_OWNER_LIFECYCLE='1',ROUNDTABLE_LOG_DIR=tmp,PYTHONIOENCODING='utf-8')
            proc=subprocess.Popen([sys.executable,str(root/'roundtable.py')],cwd=root,env=env,
                                  stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                base='http://127.0.0.1:%d'%port
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                info=None
                for _ in range(100):
                    try:
                        with opener.open(base+'/api/server/info',timeout=.2) as response:
                            info=json.loads(response.read())
                        break
                    except OSError:time.sleep(.05)
                self.assertIsNotNone(info,'backend did not start')
                with opener.open(base+'/',timeout=2) as response:
                    token=re.search(r'name="rt-token" content="([^"]+)"',response.read().decode('utf-8')).group(1)
                body={'tab':'t'*32,'instance':'i'*32}
                for action in ('open','close'):
                    request=urllib.request.Request(base+'/api/ui/session',
                        data=json.dumps(dict(body,action=action)).encode(),
                        headers={'Content-Type':'application/json','X-RT-Token':token})
                    with opener.open(request,timeout=2) as response:
                        self.assertEqual(response.status,200)
                self.assertEqual(proc.wait(timeout=15),0)
            finally:
                if proc.poll() is None:
                    proc.terminate();proc.wait(timeout=5)

if __name__=='__main__':unittest.main()
