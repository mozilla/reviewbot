#  This Source Code Form is subject to the terms of the Mozilla Public
#  License, v. 2.0. If a copy of the MPL was not distributed with this
#  file, You can obtain one at http://mozilla.org/MPL/2.0/.

# 1) need to verify usernames actually exist on IRC
# 2) please add the summary line to give more context
# 3) the message needs to be more actionable. "it is ready to land" or "r+ was granted" or "5 review issues left"
import json
import logging

from amqpy import AbstractConsumer, Connection, Message, Timeout
from irc3 import asyncio
import irc3

import reviewboard

# TODO: Make this part of the config
PULSE_HOST = 'pulse.mozilla.org'
PULSE_PORT = 5671
PULSE_USERID = 'reviewbot'
PULSE_PASSWORD = ''
PULSE_SSL = {}
PULSE_TIMEOUT = 20
PULSE_VHOST = '/'
PULSE_EXCHANGE = 'exchange/mozreview/'
PULSE_QUEUE = 'queue/reviewbot/ircbot'
PULSE_ROUTING_KEY = '#'

irc_channel = '#reviewbot'

def get_review_request_url(message: dict) -> str:
    """Return the review request url associated with the message."""
    review_request_id = get_review_request_id(message)
    return reviewboard.build_review_request_url(
            message['payload']['review_board_url'], review_request_id)

def get_review_request_id(message: dict) -> int:
    """Return the review request id associated with the message."""
    if message['_meta']['routing_key'] == 'mozreview.commits.published':
        return message['payload']['parent_review_request_id']
    elif message['_meta']['routing_key'] == 'mozreview.review.published':
        return message['payload']['review_request_id']

def get_requester(message: dict) -> str:
    return message['payload']['review_request_submitter']

def get_reviewer(id: int) -> str:
    return reviewboard.get_reviewer_from_id(id)

def wants_messages(recipient: str) -> bool:
    """Check some sort of long-term store of people who have opted in to being
    notified of new review requests and reviews.
    """
    return True

def handle_reviewed(bot, message: dict):
    recipient = get_requester(message)
    if wants_messages(recipient):
        bot.privmsg(irc_channel, '{}: New review: {}'.format(
            recipient, get_review_request_url(message)))
        #bot.privmsg(recipient, 'New review: {}'.format(
        #    get_review_request_url(message)))

def handle_review_requested(bot, message: dict):
    print('Review request:', message)
    reviewer_to_request = {}
    for commit in message['payload']['commits']:
        print('commit:', commit)
        id = commit['review_request_id']
        recipient = get_reviewer(id)
        if recipient in reviewer_to_request:
            reviewer_to_request[recipient] = get_review_request_url(message)
        else:
            reviewer_to_request[recipient] = reviewboard.build_review_request_url(
                    message['payload']['review_board_url'], id)
    for reviewer, request in reviewer_to_request.items():
        bot.privmsg(irc_channel, '{}: New review request: {}'.format(
            reviewer, request))
        
def get_review_messages(bot, exchange_name, queue_name, routing_key):
    class Consumer(AbstractConsumer):
        def run(self, message: Message):
            """Dispatch the message to the correct handler."""
            msg = json.loads(message.body)
            
            if msg['_meta']['routing_key'] == 'mozreview.commits.published':
                handle_review_requested(bot, msg)
                message.ack()
            elif msg['_meta']['routing_key'] == 'mozreview.review.published':
                pass
                # handle_reviewed(bot, msg)

    conn = Connection(host=PULSE_HOST, port=PULSE_PORT, ssl=PULSE_SSL,
            userid=PULSE_USERID, password=PULSE_PASSWORD,
            virtual_host=PULSE_VHOST)
    channel = conn.channel()
    channel.queue_declare(queue=queue_name, durable=True,
            auto_delete=False)
    channel.queue_bind(queue_name, exchange=exchange_name,
            routing_key=routing_key)
    consumer = Consumer(channel, queue_name)
    consumer.declare()

    while True:
        if getattr(bot, 'protocol', None) and irc_channel in bot.channels: break # Check if connected to IRC
        else: yield

    while True:
        try:
            conn.drain_events(timeout=0.5)
        except Timeout:
            yield

@irc3.plugin
class ReviewBot(object):
    """Forwards review requests to the person who needs to review it."""
    def __init__(self, bot):
        self.bot = bot
        self.bot.include('irc3.plugins.userlist')
        self.bot.create_task(get_review_messages(bot, PULSE_EXCHANGE,
            PULSE_QUEUE, PULSE_ROUTING_KEY))
