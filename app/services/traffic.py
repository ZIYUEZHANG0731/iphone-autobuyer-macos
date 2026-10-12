"""Persist server cooldowns without account data, URLs or network overrides."""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time


class SiteCooldown:
    def __init__(self, root:Path):
        self.path=Path(root)/'website-cooldown.json'

    def load(self):
        try:
            state=json.loads(self.path.read_text())
            return {key:float(state.get(key,0)) for key in ('until','last_block','failures','busy_until')}
        except (OSError,ValueError,TypeError):return {'until':0.,'last_block':0.,'failures':0.,'busy_until':0.}

    def remaining(self, *, now=None):
        return max(0,math.ceil(self.load()['until']-(time.time() if now is None else now)))

    def block(self, requested, *, now=None):
        now=time.time() if now is None else now;state=self.load()
        # Many rejected resources in one page load count as one episode.
        if state['until']<=now:
            state['failures']=min(4,state['failures']+1) if now-state['last_block']<21600 else 1
            state['last_block']=now
        default=min(1800,300*2**max(0,int(state['failures'])-1))
        state['until']=max(state['until'],now+max(float(requested),default))
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.path.write_text(json.dumps(state),encoding='utf-8');self.path.chmod(0o600)
        return max(0,math.ceil(state['until']-now))

    def busy_remaining(self, *, now=None):
        return max(0,math.ceil(self.load()['busy_until']-(time.time() if now is None else now)))

    def busy(self, requested, *, now=None):
        now=time.time() if now is None else now;state=self.load()
        state['busy_until']=max(state['busy_until'],now+max(0,float(requested)))
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.path.write_text(json.dumps(state),encoding='utf-8');self.path.chmod(0o600)
        return max(0,math.ceil(state['busy_until']-now))
