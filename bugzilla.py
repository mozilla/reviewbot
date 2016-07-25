from irc3 import asyncio
import requests

async def get_bugzilla_component(bug_id: str) -> str:
    """Return the bugzilla product:component for the bug id."""
    loop = asyncio.get_event_loop()
    fut = loop.run_in_executor(None, requests.get, 'https://bugzilla.mozilla.org/rest/bug/{}'.format(bug_id))
    resp = await fut
    bug = resp.json()['bugs'][0]
    return '{} :: {}'.format(bug['product'], bug['component'])
