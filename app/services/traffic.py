"""Persist server cooldowns without account data, URLs or network overrides."""
import json
import math
from pathlib import Path
import time
from app.utils.files import write_private_json


class SiteCooldown:
    def __init__(self, root:Path):
        self.path=Path(root)/'website-cooldown.json'

    def load(self):
        try:
            state=json.loads(self.path.read_text(encoding='utf-8'))
            result = {key:float(state.get(key,0)) for key in ('until','last_block','failures','busy_until')}
            result['episode'] = str(state.get('episode', ''))
            return result
        except (OSError,ValueError,TypeError):return {'until':0.,'last_block':0.,'failures':0.,'busy_until':0.,'episode':''}

    def remaining(self, *, now=None):
        return max(0,math.ceil(self.load()['until']-(time.time() if now is None else now)))

    def block(self, requested, *, now=None, episode=''):
        now=time.time() if now is None else now;state=self.load()
        # Many rejected resources in one page load count as one episode.
        if state['until']<=now and (not episode or episode != state['episode']):
            state['failures']=min(5,state['failures']+1) if now-state['last_block']<21600 else 1
            state['last_block']=now
        if episode:state['episode']=episode
        # This is an application fallback, not an Apple-published IP-ban rule.
        # One abnormal response no longer locks the whole launch for five minutes.
        # Repeated failed navigations back off; any server wait takes precedence.
        default=min(60,5*2**max(0,int(state['failures'])-1))
        state['until']=max(state['until'],now+max(float(requested),default))
        write_private_json(self.path, state)
        return max(0,math.ceil(state['until']-now))

    def busy_remaining(self, *, now=None):
        return max(0,math.ceil(self.load()['busy_until']-(time.time() if now is None else now)))

    def busy(self, requested, *, now=None):
        now=time.time() if now is None else now;state=self.load()
        state['busy_until']=max(state['busy_until'],now+max(0,float(requested)))
        write_private_json(self.path, state)
        return max(0,math.ceil(state['busy_until']-now))
