#  This Source Code Form is subject to the terms of the Mozilla Public
#  License, v. 2.0. If a copy of the MPL was not distributed with this
#  file, You can obtain one at http://mozilla.org/MPL/2.0/.

import functools
import inspect

from irc3 import asyncio
import requests

def make_cached_by_frame(*args):
    def make_cached_decorator(fn):
        """Makes a function cache by frames of any function with a name in args. Useful so we don't call excess
        HTTP requests for the same handler.
        """
        cache = {}
        frame_names = args

        @functools.wraps(fn)
        async def cached(*args, **kwargs):
            # Find the handler frame
            frame = None
            for frame_info in inspect.stack():
                if frame_info.function in frame_names:
                    frame = frame_info.frame

            if frame is not None:
                key = (frame, args)
                if key in cache:
                    return cache[key]
                else:
                    cache[key] = await fn(*args, **kwargs)
                    return cache[key]
            return await fn(*args, **kwargs)

        return cached
    return make_cached_decorator

@make_cached_by_frame('handle_reviewed', 'handle_review_requested')
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

async def get_bugzilla_id(id: int) -> str:
    """Gets the RB commit id. In MozReview the commit id for parent review requests is in the form 'bz://id/user. """
    resp = await get_review_request_from_id(id)
    return resp['review_request']['commit_id'].split('/')[2]

def build_review_request_url(review_board_url: str, review_request_id: int) -> str:
    """Build a review request url based on the review board url and 
    review request id. Needed so we can infer the review request's url from the
    message.
    """
    return '{}r/{}'.format(review_board_url, review_request_id)

