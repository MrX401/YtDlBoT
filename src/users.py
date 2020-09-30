from cloudant.adapters import Replay429Adapter
from cloudant.client import Cloudant
from cloudant.document import Document
from requests.exceptions import HTTPError
import os
import asyncio
from enum import Enum


class VideoFormat(Enum):
    LOW = 360
    MED = 720
    HIGH = 1080


class DefaultMediaType(Enum):
    Video = 0
    Audio = 1


class User:
    def __init__(self):
        self.settings = None

    @staticmethod
    async def init(id, username=None,is_group=False, force_create=False):
        user = User()
        user_id = ('user' if not is_group else 'chat') + str(id)
        user_settings = await get_user_no_read(user_id)
        if user_settings is not None and not force_create:
            user.settings = user_settings
            # change = await get_changes(user.settings['_id'])
            # if change['changes'][-1]['rev'] != user.settings['_rev']:
            #     # update doc from _changes request to eliminate reading operation
            #     user.settings.update(change['doc'])
            #     # await user.sync_with_db()
            if user.banned:
                raise Exception('You are BANNED!')
            return user

        user_settings = {
            '_id': user_id,
            'default_media_type': DefaultMediaType.Video.value,
            'video_format': VideoFormat.MED.value,
            'audio_caption': False if not is_group else True,
            'video_caption': False if not is_group else True
        }
        if username:
            user_settings['username'] = str(username)
        user_settings = await create_user(user_settings)
        user.settings = user_settings

        return user

    @property
    def default_media_type(self):
        return self.settings['default_media_type']

    async def set_default_media_type(self, m_type):
        self.settings['default_media_type'] = m_type.value
        await asyncio.get_event_loop().run_in_executor(None, self.settings.save)

    @property
    def video_format(self):
        return self.settings['video_format']

    async def set_video_format(self, vid_format):
        self.settings['video_format'] = vid_format.value
        await asyncio.get_event_loop().run_in_executor(None, self.settings.save)

    @property
    def audio_caption(self):
        return self.settings['audio_caption']

    async def set_audio_caption(self, toggle):
        self.settings['audio_caption'] = toggle
        await asyncio.get_event_loop().run_in_executor(None, self.settings.save)

    @property
    def video_caption(self):
        return self.settings['video_caption']

    async def set_video_caption(self, toggle):
        self.settings['video_caption'] = toggle
        await asyncio.get_event_loop().run_in_executor(None, self.settings.save)

    @property
    def donator(self):
        return self.settings.get('donator', 0) == 1

    @property
    def banned(self):
        return self.settings.get('banned', 0) == 1

    async def set_donator(self, toggle):
        self.settings['donator'] = toggle
        await asyncio.get_event_loop().run_in_executor(None, self.settings.save)

    async def sync_with_db(self):
        await asyncio.get_event_loop().run_in_executor(None, self.settings.fetch)


client = Cloudant(os.environ['CLOUDANT_USERNAME'],
                  os.environ['CLOUDANT_PASSWORD'],
                  url=os.environ['CLOUDANT_URL'],
                  adapter=Replay429Adapter(retries=10),
                  connect=True)
db = client['ytbdownbot']


async def is_user_sane(id):
    user_id = 'user' + str(id)
    user_settings = await get_user_no_read(user_id)
    if user_settings is not None:
        if user_settings.get('banned', 0) == 1:
            return False

    return True


async def get_user_no_read(id):
    try:
        ch_feed = await get_changes(id)
    except HTTPError:
        return None
    try:
        if '_deleted' in ch_feed['doc'] and ch_feed['doc']['_deleted']:
            return None
        doc = Document(db, ch_feed['doc']['_id'])
        doc.update(ch_feed['doc'])
        return doc
    except:
        return None

async def get_user(id):
    return await asyncio.get_event_loop().run_in_executor(None, _get_user, id)


def _get_user(id):
    user_id = 'user' + str(id)
    if user_id in db:
        return db[user_id]
    else:
        return None


async def create_user(user):
    return await asyncio.get_event_loop().run_in_executor(None, _create_user, user)


def _create_user(user):
    return db.create_document(user)


async def get_changes(doc_id):
    return await asyncio.get_event_loop().run_in_executor(None, _get_changes, doc_id)


def _get_changes(doc_id):
    changes = db.changes(doc_ids=[doc_id], filter='_doc_ids', include_docs=True)
    for change in changes:
        return change
