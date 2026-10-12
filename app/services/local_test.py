"""The isolated synthetic runner shared by GUI diagnostics and the CLI."""
import asyncio
from dataclasses import asdict
from app.adapters.simulation import SimulationAdapter
from app.automation.browser import BrowserSession
from app.automation.engine import Engine
from app.models.config import Identity, Mode
from app.services.simulation import SimStore, local_config


async def run_local(scenario, root, repo, control, *, headless=False, callback=None):
    config = local_config(scenario)
    with SimStore(config, scenario) as server:
        async with BrowserSession(root, headless=headless, isolated=True) as session:
            async def stop_later():
                await asyncio.sleep(.8)
                control.stop()
            stopper = asyncio.create_task(stop_later()) if scenario == 'stop' else None
            try:
                identity = None if scenario == 'identity_incomplete' else Identity.synthetic()
                result = asdict(await Engine(repo, control, callback=callback).run(
                    config, SimulationAdapter(session.page, server, control), Mode.LOCAL_TEST, identity))
                if stopper:await stopper
                return dict(result, scenario=scenario, submit_count=server.submit_count, synthetic_orders=len(server.orders))
            finally:
                if stopper and not stopper.done():
                    stopper.cancel()
                    await asyncio.gather(stopper, return_exceptions=True)
