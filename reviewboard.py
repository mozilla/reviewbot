#  This Source Code Form is subject to the terms of the Mozilla Public
#  License, v. 2.0. If a copy of the MPL was not distributed with this
#  file, You can obtain one at http://mozilla.org/MPL/2.0/.

import requests

def get_review_request_from_id(id: int) -> dict:
    """Returns the decoded JSON payload for any id."""
    resp = requests.get(
                'http://reviewboard.mozilla.org/api/review-requests/{}/'
                .format(id))
    return resp.json()

def get_reviewers_from_id(id: int) -> str:
    try:
        resp = get_review_request_from_id(id)
        return [person['title'] for person in
                resp['review_request']['target_people']]
    except KeyError:
        return None

def get_summary_from_id(id: int) -> str:
    try:
        resp = get_review_request_from_id(id)
        return resp['review_request']['summary']
    except KeyError:
        return ''

def build_review_request_url(review_board_url: str,
        review_request_id: int) -> str:
    """Build a review request url based on the review board url and 
    review request id. Needed so we can infer the review request's url from the
    message.
    """
    return '{}r/{}'.format(review_board_url, review_request_id)

