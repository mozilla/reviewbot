#  This Source Code Form is subject to the terms of the Mozilla Public
#  License, v. 2.0. If a copy of the MPL was not distributed with this
#  file, You can obtain one at http://mozilla.org/MPL/2.0/.

from irc3 import asyncio
import requests

async def get_review_request_from_id(id: int) -> dict:
    """Returns the decoded JSON payload for any id."""
    loop = asyncio.get_event_loop()
    fut = loop.run_in_executor(None, requests.get, 'http://reviewboard.mozilla.org/api/review-requests/{}/'.format(id))
    resp = await fut
    return resp.json()

async def get_review_request_status(id: int):
    """Returns True if r+ has been granted, returns an integer with the amount of issues otherwise."""
    resp = await get_review_request_from_id(id)
    if resp['review_request']['approved'] == True:
        return True
    return resp['review_request']['issue_open_count']

async def get_reviewers_from_id(id: int) -> str:
    try:
        resp = await get_review_request_from_id(id)
        return [person['title'] for person in resp['review_request']['target_people']]
    except KeyError:
        return None

async def get_summary_from_id(id: int) -> str:
    try:
        resp = await get_review_request_from_id(id)
        return resp['review_request']['summary']
    except KeyError:
        return ''

def build_review_request_url(review_board_url: str, review_request_id: int) -> str:
    """Build a review request url based on the review board url and 
    review request id. Needed so we can infer the review request's url from the
    message.
    """
    return '{}r/{}'.format(review_board_url, review_request_id)

