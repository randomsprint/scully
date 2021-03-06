import json
import logging
import os
import random
import re
import requests
import schedule
import subprocess
import tempfile
from os.path import dirname, join
from time import sleep
from twython import Twython, TwythonError
from .core import HELP_REGISTRY, Post, register
from .interfaces import GetTickerPrice, Interface
from .mulder_model import fit_bayes
from .utils import db_to_dataframe

CACHE_FILE = os.environ.get('SCULLY_EMOJI_CACHE')


class Response(Post):

    def reply(self, msg):
        raise NotImplementedError

    def _reply(self, stream):
        if stream:
            for msg in stream:
                self.reply(msg)

    def __call__(self, stream):
        self._reply(stream)


@register()
class Twitter(Response):

    def __init__(self, *args, twitter_client=Twython, **kwargs):
        super().__init__(*args, **kwargs)
        self.twitter = twitter_client(os.environ.get('SCULLY_API_KEY'),
                               os.environ.get('SCULLY_API_SECRET'))

    def reply(self, msg):
        hashtag = re.compile('#\S+(\s|$)')
        mentioned = hashtag.search(msg.get('text', ''))
        if mentioned:
            query = mentioned.group().strip()
            self.log.info(mentioned)
            self.log.info(query)
            count = 0
            while count < 3:
                try:
                    tweets = self.twitter.search(q=query, count=15, lang="en", result_type='mixed')
                    break
                except TwythonError:
                    count += 1
                    sleep(0.5)
            try:
                url = 'http://twitter.com/statuses/' + random.choice(tweets['statuses'])['id_str']
                self.say(url, **msg)
            except:
                self.say('Ugh sorry Twitter is being annoying for me right now.', **msg)


@register()
class Monday(Response):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        schedule.every().monday.at("9:00").do(self.do)

    def reply(self, *args):
        pass

    def do(self):
        self.say("Monday, amiright?? :coffeeparrot:", channel='C5AE0R325')


@register()
class AtMentions(Response):

    def reply(self, msg):
        text = msg.get('text', '')
        if self.AT in text:
            self.say('I WANT TO BELIEVE', **msg)


class DanielVerCheck(Response):

    ticker = 'VER'
    pinned_at = 8.015
    success_msgs = ['Daniel is raking in the money!',
                    'Daniel, buy me a boat!']
    fail_msgs = ["Men are like bank accounts. Without a lot of money, they don't generate much interest.",
                 "Daniel, if you need any financial assistance, your parents seem like nice people."]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        schedule.every().tuesday.at("16:30").do(self.do)
        schedule.every().thursday.at("16:30").do(self.do)

    def reply(self, *args):
        pass

    def do(self):
        try:
            current, prev_close = GetTickerPrice.get_stock_info(self.ticker)
            perc_change = 100 * (float(current) - self.pinned_at) / self.pinned_at
            if perc_change > 0:
                msg = random.choice(self.success_msgs)
                msg += ' :money_mouth_face: :money_with_wings:\n'
                msg += 'VER is up {:.2f}%!!'.format(perc_change)
                out = self.say(msg, channel='C5AE0R325')
                self.react('moneybag', **out)
                self.react('chart_with_upwards_trend', **out)
            if perc_change <= 0:
                msg = random.choice(self.fail_msgs)
                msg += ' :hankey:\n'
                msg += 'VER is down {:.2f}%...'.format(perc_change)
                out = self.say(msg, channel='C5AE0R325')
                self.react('-1', **out)
                self.react('chart_with_downwards_trend', **out)
        except:
            self.log.exception('{0}: Daniel scheduled stock pull failed for ticker {1}'.format(self.name, self.ticker))


@register(register_help=True)
class AddReaction(Response, Interface):

    cmd = 'react'
    cli_doc = '$ react "new_pattern" :emoji: adds :emoji: reaction to all future occurences of "new_pattern"'

    call_signature = re.compile('scully.*react to ".+" with :.*:', re.IGNORECASE)
    ignore_pattern = re.compile('"+\s*"+')
    match_string = re.compile('".+"')
    emoji_string = re.compile(':.*:*.*:')

    def __init__(self, slack_client, fname=CACHE_FILE):
        super().__init__(slack_client)
        if fname is not None and os.path.exists(fname):
            self._cache = self.load(fname=fname)
            self.log.info('Loaded emoji reactions cache from {}'.format(fname))
        else:
            self._cache = {}
            self.log.info('Starting fresh emoji reactions cache.')

        self.fname = fname

    def load(self, fname=CACHE_FILE):
        with open(CACHE_FILE, 'r') as f:
            out = json.load(f)
        return out

    def save(self):
        if self.fname is not None:
            with open(self.fname, 'w') as f:
                json.dump(self._cache, f)

    def add_reaction(self, listen_for, react_with):
        self.log.info('{0}: Storing reaction {1} for pattern "{2}"'.format(self.name, react_with, listen_for))
        self._cache[listen_for] = react_with
        self.save()
        return listen_for, react_with

    def _compute_reaction(self, text):
        text = self.sanitize(text)
        listen_for = self.match_string.search(text).group().replace('"', '').strip().lower()
        matched = self.emoji_string.search(text)
        react_with = text[(matched.start() + 1):(matched.end() - 1)]
        return listen_for, react_with

    def reply(self, msg):
        self._interface(msg) # hack to allow multiple types of use
        text = self.sanitize(msg.get('text', ''))
        reactions = [emoji for t, emoji in self._cache.items() if t.lower() in text.lower()]
        if self.call_signature.search(text) and not self.ignore_pattern.search(text):
            listen_for, react_with = self._compute_reaction(text)
            self.add_reaction(listen_for, react_with)
            success_msg = self.say('--reaction added for "{}"--'.format(listen_for), **msg)
            self.react(react_with, **success_msg)

        if reactions:
            for emoji in reactions:
                self.react(emoji, **msg)

    def interface(self, *args, msg=None):
        p, e = args[:2]
        if not self.match_string.search(self.sanitize(p)):
            return
        if not self.emoji_string.search(e):
            return

        listen_for, react_with = self._compute_reaction(p + e)
        self.add_reaction(listen_for, react_with)
        success_msg = self.say('--reaction added for "{}"--'.format(listen_for), **msg)
        self.react(react_with, **success_msg)


@register()
class Aliens(Response):

    def reply(self, msg):
        text = msg.get('text', '')
        if 'alien' in text.lower():
            self.react('alien', **msg)
            self.react('telescope', **msg)


@register(skip_test=True)
class XFiles(Response):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.is_it_mulder = fit_bayes(db_to_dataframe())

    def reply(self, msg):
        text = msg.get('text', '')
        try:
            if self.is_it_mulder(text):
                self.react('xfiles', **msg)
        except Exception:
            self.log.exception('unable to score phrase "{}"'.format(text))


@register()
class ISpy(Response):

    token = os.environ.get('SCULLY_TOKEN')
    save_loc = join(dirname(__file__), '../tmp_img.jpg')

    def classify_image(self, fpath):
        # using https://raw.githubusercontent.com/tensorflow/models/master/tutorials/image/imagenet/classify_image.py
        script_path = join(dirname(__file__), '../classify_image.py')
        bash_cmd = 'python {0} --image_file={1}'.format(script_path, fpath)
        process = subprocess.Popen(bash_cmd.split(), stdout=subprocess.PIPE)
        output, error = process.communicate()
        return output

    def download_image(self, url, file_obj):
        header = {'Authorization': 'Bearer {}'.format(self.token)}
        jpg = requests.get(url, headers=header, stream=True)
        if jpg.ok:
            for chunk in jpg.iter_content(1024):
                file_obj.write(chunk)
        else:
            self.log.exception('unable to download image at {}'.format(url))

    def format_msg(self, ideas):
        nouns = [n.strip() for i in ideas.decode().split('\n') for n in i.split('(')[0].split(',')]
        return 'I spy the following things: ' + ', '.join([n for n in nouns if n != ''])

    def reply(self, msg):
        attached_url = msg.get('message', {}).get('attachments', [{}])[0].get('image_url')
        url = msg.get('file', {}).get('url_private') or attached_url
        if url is not None:
            with open(self.save_loc, 'wb') as img_file:
                self.download_image(url, img_file)
            ideas = self.classify_image(img_file.name)
            to_say = self.format_msg(ideas)
            self.say(to_say, **msg)
