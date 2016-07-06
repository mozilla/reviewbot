#  This Source Code Form is subject to the terms of the Mozilla Public
#  License, v. 2.0. If a copy of the MPL was not distributed with this
#  file, You can obtain one at http://mozilla.org/MPL/2.0/.

# 1) need to verify usernames actually exist on IRC
# 2) the message needs to be more actionable. "it is ready to land" or "r+ was granted" or "5 review issues left"
import json
import logging
from typing import List

from amqpy import AbstractConsumer, Connection, Message, Timeout
from irc3 import asyncio
import irc3

import reviewboard

irc_channel = '#reviewbot' # This is for debug purposes.

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

def get_reviewers(id: int) -> List[str]:
    return reviewboard.get_reviewers_from_id(id)

def wants_messages(recipient: str) -> bool:
    """Check some sort of long-term store of people who have opted in to being
    notified of new review requests and reviews.
    """
    return True

def handle_reviewed(bot, message: dict):
    recipient = get_requester(message)
    if wants_messages(recipient):
        bot.privmsg(irc_channel, '{}: New review - {}: {}'.format(
            recipient,
            reviewboard.get_summary_from_id(get_review_request_id(message)),
            get_review_request_url(message)))
        #bot.privmsg(recipient, 'New review: {}'.format(
        #    get_review_request_url(message)))

def handle_review_requested(bot, message: dict):
    reviewer_to_request = {}
    for commit in message['payload']['commits']:
        id = commit['review_request_id']
        recipients = get_reviewers(id)
        for recipient in recipients:
            if recipient in reviewer_to_request:
                reviewer_to_request[recipient] = (id,
                        get_review_request_url(message))
            else:
                reviewer_to_request[recipient] = (id, reviewboard.build_review_request_url(
                        message['payload']['review_board_url'], id))
    for reviewer, (id, request) in reviewer_to_request.items():
        bot.privmsg(irc_channel, '{}: New review request - {}: {}'.format(
            reviewer, reviewboard.get_summary_from_id(id), request))


@irc3.plugin
class ReviewBot(object):
    """Forwards review requests to the person who needs to review it."""
    def __init__(self, bot):
        self.bot = bot
        self.bot.include('irc3.plugins.userlist')

        config = self.bot.config[__name__]
        self.host = config['pulse_host']
        self.port = config['pulse_port']
        self.userid = config['pulse_username']
        self.password = config['pulse_password']
        self.ssl = {} if config['pulse_ssl'] else None
        self.timeout = float(config['pulse_timeout'])
        self.vhost = config['pulse_vhost']
        self.exchange_name = config['pulse_exchange']
        self.queue_name = config['pulse_queue']
        self.routing_key = config['pulse_routing_key']

        self.bot.create_task(self.get_review_messages())

    async def get_review_messages(self):
        class Consumer(AbstractConsumer):
            def __init__(self, channel, queue_name, bot=None):
                self.bot = bot
                super().__init__(channel, queue_name)

            def run(self, message: Message):
                """Dispatch the message to the correct handler."""
                msg = json.loads(message.body)
                
                if msg['_meta']['routing_key'] == 'mozreview.commits.published':
                    handle_review_requested(self.bot, msg)
                elif msg['_meta']['routing_key'] == 'mozreview.review.published':
                    handle_reviewed(self.bot, msg)
                message.ack()

        conn = Connection(host=self.host, port=self.port, ssl=self.ssl,
                userid=self.userid, password=self.password,
                virtual_host=self.vhost)
        channel = conn.channel()
        channel.queue_declare(queue=self.queue_name, durable=True,
                auto_delete=False)
        channel.queue_bind(self.queue_name, exchange=self.exchange_name,
                routing_key=self.routing_key)
        consumer = Consumer(channel, self.queue_name, bot=self.bot)
        consumer.declare()

        while True:
            if getattr(self.bot, 'protocol', None) and irc_channel in self.bot.channels: break # Check if connected to IRC
            else: await asyncio.sleep(.001, loop=self.bot.loop)

        while True:
            try:
                conn.drain_events(timeout=self.timeout)
            except Timeout:
                await asyncio.sleep(.001, loop=self.bot.loop)
