"""Track local administrator tabs so the desktop-launched service exits with them."""
import re
import threading
import time

class OwnerLifecycle:
    def __init__(self, shutdown, grace=6, expiry=180, interval=.5):
        self.shutdown=shutdown
        self.grace=grace
        self.expiry=expiry
        self.interval=interval
        self.lock=threading.Lock()
        self.tabs={}
        self.deadline=None
        self.closed=False
        self.started=False

    def update(self, tab, action, instance=None):
        if not isinstance(tab,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}',tab) or action not in ('open','ping','close') or (instance is not None and (not isinstance(instance,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}',instance))):
            raise ValueError('无效页面状态')
        with self.lock:
            if self.closed: return
            now=time.monotonic()
            if action=='close':
                if tab in self.tabs and self.tabs[tab][0] == instance:
                    self.tabs.pop(tab,None)
                    if not self.tabs:self.deadline=now+self.grace
            elif action == 'open':
                self.tabs[tab]=(instance,now)
                self.deadline=None
            elif tab in self.tabs and self.tabs[tab][0] == instance:
                self.tabs[tab]=(instance,now)
            if not self.started:
                self.started=True
                threading.Thread(target=self._watch,daemon=True,name='owner-lifecycle').start()

    def _watch(self):
        while True:
            time.sleep(self.interval)
            with self.lock:
                if self.closed:return
                now=time.monotonic()
                stale=[tab for tab,(_,seen) in self.tabs.items() if now-seen>self.expiry]
                for tab in stale:self.tabs.pop(tab,None)
                if stale and not self.tabs and self.deadline is None:self.deadline=now+self.grace
                if self.deadline is None or self.tabs or now<self.deadline:continue
                self.closed=True
            self.shutdown()
            return
