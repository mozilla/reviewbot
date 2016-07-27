#  This Source Code Form is subject to the terms of the Mozilla Public
#  License, v. 2.0. If a copy of the MPL was not distributed with this
#  file, You can obtain one at http://mozilla.org/MPL/2.0/.

import codecs
import functools
import json
import re
from typing import List

from amqpy import AbstractConsumer, Connection, Message, Timeout
from irc3 import asyncio
import irc3
from irc3.plugins.command import command

import bugzilla
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

async def generate_content_text(id: int) -> str:
    """Generate an actionable text for reviews."""
    status = await reviewboard.get_review_request_status(id)
    if status == True:
        return 'r+ was granted'
    return '{} issues left'.format(status)

def get_requester(message: dict) -> str:
    return message['payload']['review_request_submitter']

async def get_reviewers(id: int) -> List[str]:
    return await reviewboard.get_reviewers_from_id(id)

async def get_bugzilla_components_from_msg(msg: dict) -> List[str]:
    """Get the bugzilla component that relates to the bug the review is for."""
    if msg['_meta']['routing_key'] == 'mozreview.review.published':
        bz_comps = set()
        for bug_id in msg['payload']['review_request_bugs']:
            bz_comp = await bugzilla.get_bugzilla_component(bug_id)
            bz_comps.add(bz_comp)
        return list(bz_comps)
    if msg['_meta']['routing_key'] == 'mozreview.commits.published':
        bug_id = await reviewboard.get_bugzilla_id(get_review_request_id(msg))
        bz_comp = await bugzilla.get_bugzilla_component(bug_id)
        return [bz_comp]

def handler(handler_fn):
    """Do common things we want with a handler like rate limit and ack the messages."""
    @functools.wraps(handler_fn)
    async def new_handler(self, message: Message):
        await self.messages_processed.acquire()
        await handler_fn(self, message)
        message.ack()
        self.messages_processed.release()
    return new_handler

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

        self.load_bz_to_channel_config(None, None, None)
        self.load_registered_nicks()

        # Limit the amount of messages that can be processed simultaneously. This keeps the bot from never processing
        # messages that take a long time to process.
        self.messages_processed = asyncio.Semaphore(value=32)

        self.bot.create_task(self.get_review_messages())

    async def get_review_messages(self):
        class Consumer(AbstractConsumer):
            def __init__(self, channel, queue_name, plugin):
                self.plugin = plugin
                super().__init__(channel, queue_name)

            def run(self, message: Message):
                """Dispatch the message to the correct handler."""
                msg = json.loads(message.body)

                if msg['_meta']['routing_key'] == 'mozreview.commits.published':
                    asyncio.ensure_future(self.plugin.handle_review_requested(message), loop=self.plugin.bot.loop)
                elif msg['_meta']['routing_key'] == 'mozreview.review.published':
                    asyncio.ensure_future(self.plugin.handle_reviewed(message), loop=self.plugin.bot.loop)

        conn = Connection(host=self.host, port=self.port, ssl=self.ssl, userid=self.userid, password=self.password,
                virtual_host=self.vhost)
        channel = conn.channel()
        channel.queue_declare(queue=self.queue_name, durable=True, auto_delete=False)
        channel.queue_bind(self.queue_name, exchange=self.exchange_name, routing_key=self.routing_key)
        consumer = Consumer(channel, self.queue_name, self)
        consumer.declare()

        while True:
            if getattr(self.bot, 'protocol', None) and irc_channel in self.bot.channels: break # Check if connected to IRC
            else: await asyncio.sleep(.001, loop=self.bot.loop)

        while True:
            try:
                conn.drain_events(timeout=self.timeout)
            except Timeout:
                await asyncio.sleep(.001, loop=self.bot.loop)

    async def update_channels(self, msg: dict, recipient: str, content: str, summary: str, url: str, bz_components: List[str]):
        """Message all the channels that are registered for the component related to review request."""
        channels = []
        for component in bz_components:
            channels.extend(self.bz_component_to_channels[component])
        for channel in channels:
            self.bot.privmsg(irc_channel, '{}: {}: {} - {}: {}'.format(channel, recipient, content, summary,
                                                                       get_review_request_url(msg)))

    @handler
    async def handle_reviewed(self, message: Message):
        msg = json.loads(message.body)
        recipient = get_requester(msg)
        bz_components = await get_bugzilla_components_from_msg(msg)
        if self.wants_messages(recipient) or any([comp in self.bz_component_to_channels for comp in bz_components]):
            id = get_review_request_id(msg)
            summary = await reviewboard.get_summary_from_id(id)
            content = await generate_content_text(id)
            url = get_review_request_url(msg)
            self.bot.privmsg(irc_channel, '{}: {} - {}: {}'.format(recipient, content, summary, url))
            await self.update_channels(msg, recipient, content, summary, url, bz_components)

    @handler
    async def handle_review_requested(self, message: Message):
        msg = json.loads(message.body)
        reviewer_to_request = {}
        for commit in msg['payload']['commits']:
            id = commit['review_request_id']
            recipients = await get_reviewers(id)
            for recipient in recipients:
                if recipient in reviewer_to_request:
                    reviewer_to_request[recipient] = (id, get_review_request_url(msg))
                else:
                    reviewer_to_request[recipient] = (id, reviewboard.build_review_request_url(
                        msg['payload']['review_board_url'], id))

        bz_components = await get_bugzilla_components_from_msg(msg)
        for reviewer, (id, request) in reviewer_to_request.items():
            if self.wants_messages(reviewer) or any([comp in self.bz_component_to_channels for comp in bz_components]):
                summary = await reviewboard.get_summary_from_id(id)
                self.bot.privmsg(irc_channel, '{}: New review request - {}: {}'.format(reviewer, summary, request))
                await self.update_channels(msg, reviewer, 'New review request', summary, request, bz_components)

    def load_registered_nicks(self):
        """Load the list of nicks that want messages."""
        self.registered_nicks = self.get_state('registered_users', [])

    def save_registered_nicks(self):
        self.save_state('registered_users', self.registered_nicks)

    @command(permission='all_permissions')
    def load_bz_to_channel_config(self, mask, target, args):
        """Loads the bugzilla_component_to_channel config into memory.

            %%load_bz_to_channel_config
        """
        self.bz_component_to_channel = self.get_state('bugzilla_component_to_channel', {})

    @command(permission='view')
    def register(self, mask, target, args):
        """Add to the list of people who want to be messaged.
            %%register
        """
        self.registered_nicks.append(mask.nick)
        self.save_registered_nicks()
        self.bot.privmsg(mask.nick, 'Registered.')

    @command(permission='view')
    def deregister(self, mask, target, args):
        """Remove from the list of people want to be messaged.
            %%deregister
        """
        self.registered_nicks.remove(mask.nick)
        self.save_registered_nicks()
        self.bot.privmsg(mask.nick, 'Deregistered.')

    def wants_messages(self, recipient: str) -> bool:
        """Check some sort of long-term store of people who have opted in to being
        notified of new review requests and reviews.
        """
        return recipient in self.registered_nicks

    def get_state(self, key, default):
        """Obtain persisted state.

        If no state is present, returns the value specified in ``default``.
        """
        verify_state_key(key)

        path = '%s.json' % key
        try:
            with codecs.open(path, 'rb', 'utf-8') as fh:
                return json.load(fh, encoding='utf-8')
        except FileNotFoundError:
            return default

    def save_state(self, key, value):
        """Save persisted state."""
        verify_state_key(key)
        path = '%s.json' % key
        with codecs.open(path, 'wb', 'utf-8') as fh:
            json.dump(value, fh, sort_keys=True, indent=4)


def verify_state_key(key):
    """Raise if a key used for state doesn't match expected format.

    We require keys have well-defined names to prevent things like path
    traversal exploits.
    """
    if not re.match('^[a-zA-Z0-9_-]+$', key):
        raise ValueError('state key must be alphanumeric + -+')
