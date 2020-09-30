#!/bin/python3

import sys, os
from telethon import TelegramClient, Button
from telethon.tl.types import DocumentAttributeVideo, DocumentAttributeAudio, DocumentAttributeFilename
from telethon.errors import AuthKeyDuplicatedError, BadRequestError
import traceback
import asyncio
import logging
import logaugment
import youtube_dl
from aiohttp import web, ClientSession
from urlextract import URLExtract
import re
import av_utils
import av_source
import users
import cut_time
import zip_file
import thumb
import io
import inspect
import mimetypes
from datetime import time, timedelta
from requests.exceptions import HTTPError as CloudantHTTPError
from urllib.error import HTTPError
from urllib.parse import urlparse, urlunparse
import signal
import functools
import fast_telethon
import aiofiles
from extractor.tiktok import TikTokIE
from extractor.pinterest import PinterestIE


def get_client_session():
    if 'CLIENT_SESSION' in os.environ:
        return os.environ['CLIENT_SESSION']

    try:
        from cloudant import cloudant
        from cloudant.adapters import Replay429Adapter
    except:
        raise Exception('Couldn\'t find client session nor in os.environ or cloudant db')

    with cloudant(os.environ['CLOUDANT_USERNAME'],
                  os.environ['CLOUDANT_PASSWORD'],
                  url=os.environ['CLOUDANT_URL'],
                  adapter=Replay429Adapter(retries=10),
                  connect=True) as client:
        db = client['ytbdownbot']
        instance_id = '0'
        # in case of multi instance architecture
        if 'INSTANCE_INDEX' in os.environ:
            instance_id = os.environ['INSTANCE_INDEX']
        return db['session' + instance_id]['session']


def sizeof_fmt(num, suffix='B'):
    for unit in ['', 'K', 'M', 'G', 'T', 'P', 'E', 'Z']:
        if abs(num) < 1024.0:
            return "%3.1f%s%s" % (num, unit, suffix)
        num /= 1024.0
    return "%.1f%s%s" % (num, 'Yi', suffix)


def new_logger(user_id, msg_id):
    logger = logging.Logger('')
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler(sys.stdout)
    instance_id = os.getenv('INSTANCE_INDEX', 0)
    formatter = logging.Formatter("%(levelname)s<%(id)s>[%(msgid)s](%(in_id)s): %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logaugment.set(logger, id=str(user_id), msgid=str(msg_id), in_id=str(instance_id))

    return logger


async def on_callback(callback):
    from_id = callback['from']['id']
    msg_id = callback['message']['message_id']
    data = callback['data']
    user = await users.User.init(from_id)
    log = new_logger(from_id, msg_id)
    # retry in case of update conflict
    for _ in range(15):
        try:
            await _on_callback(from_id, msg_id, data, user, log)
        except CloudantHTTPError as e:
            if e.response.status_code == 409:
                log.warning('document update conflict, trying sync with db...')
                try:
                    await user.sync_with_db()
                except CloudantHTTPError as e:
                    if e.response.status_code == 404:
                        user = await users.User.init(from_id, force_create=True)
                continue
            else:
                log.exception(e)
                break
        except Exception as e:
            log.exception(e)
            break
        break


async def _on_callback(from_id, msg_id, data, user, log):
    key, value = data.split(':')
    if key == 'default_media_type':
        if int(value) == users.DefaultMediaType.Video.value:
            log.info('set default media type to {}'.format(users.DefaultMediaType.Audio))
            await user.set_default_media_type(users.DefaultMediaType.Audio)
        else:
            log.info('set default media type to {}'.format(users.DefaultMediaType.Video))
            await user.set_default_media_type(users.DefaultMediaType.Video)
    elif key == 'video_format':
        value = int(value)
        if value == users.VideoFormat.LOW.value:
            log.info('set video format to {}'.format(users.VideoFormat.MED))
            await user.set_video_format(users.VideoFormat.MED)
        elif value == users.VideoFormat.MED.value:
            log.info('set video format to {}'.format(users.VideoFormat.HIGH))
            await user.set_video_format(users.VideoFormat.HIGH)
        elif value == users.VideoFormat.HIGH.value:
            log.info('set video format to {}'.format(users.VideoFormat.LOW))
            await user.set_video_format(users.VideoFormat.LOW)
    elif key == 'audio_caption':
        value = not (1 if value == 'True' else 0)
        log.info('set audio captions to {}'.format(value))
        await user.set_audio_caption(value)
    elif key == 'video_caption':
        value = not (1 if value == 'True' else 0)
        log.info('set video captions to {}'.format(value))
        await user.set_video_caption(value)
    elif key == '':
        log.info('delete settings menu')

        _msg = await client.get_messages(from_id, ids=msg_id)
        await client.delete_messages(from_id, _msg)
        return

    await send_settings(user, from_id, msg_id)


async def on_message(request):
    try:
        req_data = await request.json()

        if 'callback_query' in req_data:
            asyncio.get_event_loop().create_task(on_callback(req_data['callback_query']))
            return web.Response(status=200)

        message = req_data['message']

        msg_task = asyncio.get_event_loop().create_task(_on_message_task(message))
        asyncio.get_event_loop().create_task(task_timeout_cancel(msg_task, timemout=21600))
    except Exception as e:
        print(e)
        traceback.print_exc()

    return web.Response(status=200)


async def task_timeout_cancel(task, timemout=5):
    try:
        await asyncio.wait_for(task, timeout=timemout)
    except asyncio.TimeoutError:
        task.cancel()

async def _on_message_task(message):
    try:
        # async with bot.action(message['chat']['id'], 'file'):
        chat_id = message['chat']['id']
        msg_id = message['message_id']
        is_group = False
        if message['chat']['type'] != 'private':
            is_group = True
        log = new_logger(chat_id, msg_id)
        try:
            await _on_message(message, log, is_group)
        except HTTPError as e:
            # crashing to try change ip
            # otherwise youtube.com will not allow us
            # to download any video for some time
            log.exception(e)
            if not is_group:
                await client.send_message(chat_id, e.__str__(), reply_to=msg_id)
        except youtube_dl.DownloadError as e:
            # crashing to try change ip
            # otherwise youtube.com will not allow us
            # to download any video for some time
            log.exception(e)
            if not is_group:
                await client.send_message(chat_id, str(e), reply_to=msg_id)
        except Exception as e:
            log.exception(e)
            if 'ERROR' not in str(e):
                err_msg = 'ERROR: ' + str(e)
            else:
                err_msg = str(e)
            if not is_group:
                await client.send_message(chat_id, err_msg, reply_to=msg_id)
    except Exception as e:
        logging.error(e)


# extract telegram command from message
def cmd_from_message(message):
    cmd = None
    if 'entities' in message:
        for e in message['entities']:
            if e['type'] == 'bot_command':
                cmd = message['text'][e['offset'] + 1:e['length']]

    return cmd


async def extract_url_info(ydl, url):
    # data = {
    #     "url": url,
    #     **params
    # }
    # headers = {
    #     "x-ibm-client-id": YTDL_LAMBDA_SECRET
    # }
    # async with ClientSession() as session:
    #     async with session.post(YTDL_LAMBDA_URL, json=data, headers=headers, timeout=14400) as req:
    #         return await req.json()
    return await asyncio.get_event_loop().run_in_executor(None,
                                                          functools.partial(ydl.extract_info,
                                                                            download=False,
                                                                            force_generic_extractor=ydl.params.get(
                                                                                'force_generic_extractor', False)),
                                                          url)


async def send_settings(user, user_id, edit_id=None):
    if user.default_media_type == users.DefaultMediaType.Video.value:
        buttons = [[Button.inline('🎬⤵️',
                       data='default_media_type:' + str(users.DefaultMediaType.Video.value)),
         Button.inline(str(user.video_format) + 'p',
                       data='video_format:' + str(user.video_format))],
        [Button.inline('Video caption: ' + ('✅' if user.video_caption else '❎'),
                       data='video_caption:' + str(user.video_caption)),
         Button.inline('❌', data=':')]]
    else:
        buttons = [[Button.inline('🎧⤵️',
                       data='default_media_type:' + str(users.DefaultMediaType.Audio.value)),
         Button.inline('Audio caption: ' + ('✅' if user.audio_caption else '❎'),
                       data='audio_caption:' + str(user.audio_caption))],
        [Button.inline('❌', data=':')]]
    if edit_id is None:
        await client.send_message(user_id, '⚙SETTINGS', buttons=buttons)
    else:
        _msg = await client.get_messages(user_id, ids=edit_id)
        await client.edit_message(_msg, '⚙SETTINGS', buttons=buttons)


is_ytb_link_re = re.compile(
    '^((?:https?:)?\/\/)?((?:www|m|music)\.)?((?:youtube\.com|youtu.be))(\/(?:[\w\-]+\?v=|embed\/|v\/)?)([\w\-]+)(\S+)?$')
get_ytb_id_re = re.compile(
    '.*(youtu.be\/|v\/|embed\/|watch\?|youtube.com\/user\/[^#]*#([^\/]*?\/)*)\??v?=?([^#\&\?]*).*')
invidious_re = re.compile(r'https?://(?:www\.)?invidious\.snopyta\.org/watch\?v=(?P<id>[0-9A-Za-z_-]{11})')

single_time_re = re.compile(' ((2[0-3]|[01]?[0-9]):)?(([0-5]?[0-9]):)?([0-5]?[0-9])(\\.[0-9]+)? ')


async def send_screenshot(user_id, msg_txt, url, http_headers=None):
    time_match = single_time_re.search(msg_txt)
    pic_time = None
    if time_match:
        time_group = time_match.group()
        pic_time = cut_time.to_isotime(time_group)

    if pic_time:
        vinfo = await av_utils.av_info(url, http_headers)
        duration = int(float(vinfo['format'].get('duration', 0)))
        if cut_time.time_to_seconds(pic_time) >= duration:
            pic_time = None

    screenshot_data = await av_source.video_screenshot(url,
                                                       http_headers,
                                                       screen_time=str(pic_time) if pic_time else None,
                                                       quality=1)
    if not screenshot_data:
        return

    photo = io.BytesIO(screenshot_data)
    await client.send_file(user_id, photo, attributes=[DocumentAttributeFilename("default.jpg")])


def normalize_url_path(url):
    parsed = list(urlparse(url))
    parsed[2] = re.sub("/{2,}", "/", parsed[2])

    return urlunparse(parsed)


def youtube_to_invidio(url, quality='dash'):
    u = None
    if is_ytb_link_re.search(url):
        ytb_id_match = get_ytb_id_re.search(url)
        if ytb_id_match:
            ytb_id = ytb_id_match.groups()[-1]
            u = "https://invidious.snopyta.org/watch?v=" + ytb_id + f"&quality={quality}"
    return u

async def upload_multipart_zip(source, name, file_size, chat_id, msg_id):
    zfile = zip_file.ZipTorrentContentFile(source, name, file_size)

    async def upload_torrent_content(file, chat_id, msg_id):
        global TG_CONNECTIONS_COUNT
        global TG_MAX_PARALLEL_CONNECTIONS
        if 20 > TG_CONNECTIONS_COUNT and file.size > 100 * 1024 * 1024:
            TG_CONNECTIONS_COUNT += 2
            try:
                uploaded_file = await fast_telethon.upload_file(client,
                                                                file,
                                                                file_size=file.size,
                                                                file_name=file.name,
                                                                max_connection=2)
            finally:
                TG_CONNECTIONS_COUNT -= 2
        else:
            uploaded_file = await client.upload_file(file, file_size=file.size, file_name=file.name)
        for i in range(3):
            try:
                await client.send_file(chat_id, uploaded_file, reply_to=msg_id)
            except Exception as e:
                if i >= 2:
                    raise e
                print(e)
                await asyncio.sleep(1 * i)
                continue
            break

    try:
        for i in range(0, zfile.zip_parts):
            await upload_torrent_content(zfile, chat_id, msg_id)
            zfile.zip_num += 1
    except BadRequestError as e:
        logging.error(e)

    if source is not None:
        if inspect.iscoroutinefunction(source.close):
            await source.close()
        else:
            source.close()

            
async def ytb_playlist_to_invidious(url, range, quality="dash"):
    invid_urls = []
    playlist_id_r = re.compile(r'list=((?:PL|LL|EC|UU|FL|RD|UL|TL|PU|OLAK5uy_)[0-9A-Za-z-_]{10,})')
    pid = playlist_id_r.search(url).groups()[0]
    async with ClientSession() as session:
        async with session.get("https://invidious.snopyta.org/api/v1/playlists/"+pid) as req:
            invid_playlist = await req.json()
    for iv in invid_playlist['videos'][range[0]-1:range[1]]:
        invid_urls.append("https://invidious.snopyta.org/watch?v=" + iv['videoId'] + "&quality="+quality+("&raw=1" if quality != "dash" else ""))
    return invid_urls


def get_cookie_from_text(msg_txt):
    try:
        return msg_txt.split(' || ')[1].strip()
    except:
        return None


def get_user_prefs_from_text(msg_txt):
    try:
        msg_parts = [part.strip() for part in msg_txt.split(' | ')][1:]
        msg_parts[-1] = msg_parts[-1].split(' || ')[0]
    except:
        return []
    return msg_parts


def get_user_headers_from_text(msg_txt):
    try:
        parts = [p.strip() for p in msg_txt.split(' ||| ')][1:]
        headers = {kv.split(':')[0].strip(): kv.split(':')[1].strip() for kv in parts}
    except:
        return {}
    return headers


async def _on_message(message, log, is_group):
    global STORAGE_SIZE
    global YT_TOO_MANY_REQUEST
    if message['from']['is_bot']:
        log.info('Message from bot, skip')
        return

    msg_id = message['message_id']
    chat_id = message['chat']['id']
    if 'text' not in message:
        if not is_group:
            await client.send_message(chat_id, 'Please send me a video link', reply_to=msg_id)
        return
    msg_txt = message['text']

    log.info('message: ' + msg_txt)

    urls = url_extractor.find_urls(msg_txt)
    user_cookie = get_cookie_from_text(msg_txt)
    user_prefs = get_user_prefs_from_text(msg_txt)
    user_headers = get_user_headers_from_text(msg_txt)
    user_file_name = user_uname = user_passwd = None
    if len(user_prefs) != 0 and len(urls) > 1:
        urls = urls[:1]
    if len(user_prefs) == 1:
        user_file_name, = user_prefs
    elif len(user_prefs) == 2:
        user_uname, user_passwd = user_prefs
    elif len(user_prefs) == 3:
        user_file_name, user_uname, user_passwd = user_prefs
    cmd = cmd_from_message(message)
    playlist_start = None
    playlist_end = None
    y_format = None
    audio_mode = False

    user = None
    # check cmd and choose video format
    cut_time_start = cut_time_end = None
    if cmd is not None:
        if cmd not in available_cmds:
            await client.send_message(chat_id, 'Wrong command', reply_to=msg_id)
            return
        elif cmd == 'start':
            await client.send_message(chat_id, 'Send me a video links')
            return
        elif cmd == 'c':
            try:
                cut_time_start, cut_time_end = cut_time.parse_time(msg_txt)
            except Exception as e:
                if 'Wrong time format' == str(e):
                    await client.send_message(chat_id,
                                            'Wrong time format, correct example: `/c 10:23-1:12:4 youtube.com`',
                                            parse_mode='markdown')
                    return
                else:
                    raise
        elif cmd == 'ping':
            await client.send_message(chat_id, 'pong')
            return
        elif cmd == 'settings':
            if is_group:
                await client.send_message(chat_id,
                                          'Settings for groups/channels can be changed only by @pony0boy\n'
                                          'Ask him if you want:\n'
                                          '- Disable links in caption\n'
                                          '- Enable reply to messages\n'
                                          '- Disable caption\n'
                                          'Its cost 2$ per group/channel',
                                          reply_to=msg_id)
                return
            user = await users.User.init(chat_id, is_group=is_group)
            await send_settings(user, chat_id)
            return
        elif cmd == 'donate':
            await client.send_message(chat_id, os.getenv('DONATE_INFO', ''), parse_mode='markdown')
            return
        elif cmd in playlist_cmds:
            if is_group:
                await client.send_message(chat_id,
                                          'Command not available in chats',
                                          reply_to=msg_id)
                return
            user = await users.User.init(chat_id)
            if not user.donator:
                await client.send_message(chat_id,
                                        'Only <b>donators</b> can download playlists\n' +
                                        'Donate to me at least <b>5$</b> to use this feature\n'
                                        'Send /donate command to get info\n'
                                        'Notify @pony0boy after donation',
                                        reply_to=msg_id,
                                        parse_mode='html')
                return
            urls_count = len(urls)
            if urls_count != 1:
                await client.send_message(chat_id,
                                        'Wrong command arguments. Correct example: `/' + cmd + " 2-4 youtube.com/playlist`",
                                        reply_to=msg_id,
                                        parse_mode='markdown')
                # await bot.send_message(chat_id, 'Wrong command arguments. Correct example: /' + cmd + " 2-4 youtube.com", reply_to=msg_id)
                return
            range_match = playlist_range_re.search(msg_txt)
            if range_match is None:
                await client.send_message(chat_id,
                                        'Wrong message format, correct example: `/' + cmd + " 4-9 " + 'youtube.com/playlist`',
                                        reply_to=msg_id,
                                        parse_mode='markdown')
                # await bot.send_message(chat_id,
                #                        'Wrong message format, correct example: /' + cmd + " 4-9 " + urls[0],
                #                        reply_to=msg_id)
                return
            _start, _end = range_match.groups()
            playlist_start = int(_start)
            playlist_end = int(_end)
            if playlist_start >= playlist_end:
                await client.send_message(chat_id, 'Not correct format, start number must be less then end',
                                        reply_to=msg_id)
                # await bot.send_message(chat_id,
                #                        'Not correct format, start number must be less then end',
                #                        reply_to=msg_id)
                return
            elif playlist_end - playlist_start > 50:
                await client.send_message(chat_id, 'Too big range. Allowed range is less or equal 50 videos',
                                        reply_to=msg_id)
                # await bot.send_message(chat_id,
                #                        'Too big range. Allowed range is less or equal 50 videos',
                #                        reply_to=msg_id)
                return
            # cut "p" from cmd variable if cmd == "pa" or "pw"
            cmd = cmd if len(cmd) == 1 else cmd[-1]
        if cmd == 'a':
            # audio cmd
            audio_mode = True
            y_format = audio_format
        elif cmd == 'w':
            # wordst video cmd
            y_format = worst_video_format

    if len(urls) == 0:
        if cmd == 'a':
            await client.send_message(chat_id, 'Wrong command arguments. Correct example: `/a youtube.com`',
                                    reply_to=msg_id,
                                    parse_mode='markdown')
        elif cmd == 'w':
            await client.send_message(chat_id, 'Wrong command arguments. Correct example: `/w youtube.com`',
                                    reply_to=msg_id,
                                    parse_mode='markdown')
        elif cmd == 's':
            await client.send_message(chat_id, 'Wrong command arguments. Correct example: `/s 23:14 youtube.com`',
                                    reply_to=msg_id,
                                    parse_mode='markdown')
        elif cmd == 't':
            await client.send_message(chat_id, 'Wrong command arguments. Correct example: `/t youtube.com`',
                                    reply_to=msg_id,
                                    parse_mode='markdown')
        elif cmd == 'm':
            await client.send_message(chat_id, 'Wrong command arguments. Correct example: `/m nonyoutube.com`',
                                    reply_to=msg_id,
                                    parse_mode='markdown')
        elif cmd == 'z':
            await client.send_message(chat_id, 'Wrong command arguments. Correct example: `/z example.com/file.mp4`',
                                    reply_to=msg_id,
                                    parse_mode='markdown')
        else:
            if not is_group:
                await client.send_message(chat_id, 'Please send me link to the video', reply_to=msg_id)
        log.info('Message without url: ' + msg_txt)
        return

    if user is None:
        if is_group:
            group_username = message['chat']['username']
            _from_id = message['from']['id']
            is_user_sane = await users.is_user_sane(_from_id)
            if not is_user_sane:
                raise Exception('Bad user')
        else:
            group_username = None
        user = await users.User.init(chat_id, username=group_username, is_group=is_group)
    if user.default_media_type == users.DefaultMediaType.Audio.value:
        audio_mode = True

    preferred_formats = None
    if audio_mode == False:
        if y_format is not None:
            preferred_formats = [y_format]
        elif user.video_format == users.VideoFormat.HIGH.value:
            preferred_formats = [vid_fhd_format, vid_hd_format, vid_nhd_format]
        elif user.video_format == users.VideoFormat.MED.value:
            preferred_formats = [vid_hd_format, vid_nhd_format]
        elif user.video_format == users.VideoFormat.LOW.value:
            preferred_formats = [vid_nhd_format]
    else:
        if y_format is not None:
            preferred_formats = [y_format]
        else:
            preferred_formats = [audio_format]

    # await _bot.send_chat_action(chat_id, "upload_document")

    if YT_TOO_MANY_REQUEST and 'youtube.com/playlist?list=' in urls[0] and playlist_start is not None:
        try:
            if audio_mode:
                urls = await ytb_playlist_to_invidious(urls[0], (playlist_start,playlist_end))
            else:
                urls = await ytb_playlist_to_invidious(urls[0], (playlist_start, playlist_end), quality='hd720')
        except:
            pass
    if not is_group or user.settings.get('nonprivate_action', 0):
        action = await client.action(chat_id, "file").__aenter__()
    try:
        urls = set(urls)
        for iu, u in enumerate(urls):
            vinfo = None
            params = {'noplaylist': True,
                      'youtube_include_dash_manifest': False,
                      'is_group': True,
                      'no_color': True,
                      'nocheckcertificate': True,
                      'force_generic_extractor': True if invidious_re.search(u) else False
                      }
            if playlist_start != None and playlist_end != None: #and 'invidious.snopyta.org/watch' not in u:
                params['ignoreerrors'] = True
                if playlist_start == 0 and playlist_end == 0:
                    params['playliststart'] = 1
                    params['playlistend'] = 10
                else:
                    params['playliststart'] = playlist_start
                    params['playlistend'] = playlist_end
            else:
                params['playlist_items'] = '1'
            if user_uname and user_passwd:
                params['username'] = user_uname
                params['password'] = user_passwd
            ydl = youtube_dl.YoutubeDL(params=params)
            if user_cookie:
                if ydl._opener.addheaders is None:
                    ydl._opener.addheaders = []
                ydl._opener.addheaders.append(('Cookie', user_cookie))
                params['cookiefile'] = "some_cookies"
            if user_headers:
                if not ydl._opener.addheaders:
                    ydl._opener.addheaders = []
                for k, v in user_headers.items():
                    ydl._opener.addheaders.append((k, v))
            recover_playlist_index = None  # to save last playlist position if finding format failed
            for ip, pref_format in enumerate(preferred_formats):
                try:
                    params['format'] = pref_format
                    if recover_playlist_index is not None and 'playliststart' in params:
                        params['playliststart'] += recover_playlist_index
                    ydl.params = params
                    if vinfo is None:
                        for i_ in range(2):
                            try:
                                # use invidious.snopyta.org for youtube links from groups to prevent 429 err
                                if is_group and invidious_re.search(u):
                                    if audio_mode:
                                        u = youtube_to_invidio(u)
                                    else:
                                        u = youtube_to_invidio(u, quality='hd720')
                                    ydl.params['force_generic_extractor'] = True
                                vinfo = await extract_url_info(ydl, u)
                                if is_group and invidious_re.search(u):
                                    try:
                                        vinfo['entries'][0]['url'] = u + ('&raw=1' if not audio_mode else '')
                                    except:
                                        pass
                                if vinfo.get('age_limit') == 18 and is_ytb_link_re.search(vinfo.get('webpage_url', '')):
                                    raise youtube_dl.DownloadError('youtube age limit')
                            except youtube_dl.DownloadError as e:
                                # try to use invidious.snopyta.org youtube frontend to bypass 429 block
                                if (e.exc_info is not None and e.exc_info[0] is HTTPError and e.exc_info[
                                    1].file.code == 429) or \
                                        'video available in your country' in str(e) or \
                                        'youtube age limit' == str(e):
                                    if i_ == 1:
                                        raise
                                    if audio_mode:
                                        invid_url = youtube_to_invidio(u)
                                    else:
                                        invid_url = youtube_to_invidio(u, quality='hd720&raw=1')
                                    if invid_url:
                                        if e.exc_info[1].file.code == 429:
                                            YT_TOO_MANY_REQUEST = True
                                        u = invid_url
                                        ydl.params['force_generic_extractor'] = True
                                        continue
                                    raise
                                elif e.exc_info is not None and e.exc_info[0] is youtube_dl.utils.UnsupportedError:
                                    tk = TikTokIE()
                                    pn = PinterestIE()
                                    if tk.suitable(u) or (len(e.exc_info) > 1 and tk.suitable(e.exc_info[1].url)):
                                        # Tiktok inject
                                        ydl.add_info_extractor(tk)
                                        ydl._ies = [TikTokIE] + ydl._ies
                                        if 'tiktok.com/@' not in u:
                                            u = e.exc_info[1].url
                                        vinfo = await extract_url_info(ydl, u)
                                    elif pn.suitable(u) or (len(e.exc_info) > 1 and pn.suitable(e.exc_info[1].url)):
                                        # Pinterest inject
                                        ydl.add_info_extractor(pn)
                                        ydl._ies = [PinterestIE] + ydl._ies
                                        if 'pin.it' in u:
                                            u = e.exc_info[1].url
                                        vinfo = await extract_url_info(ydl, u)
                                    else:
                                        raise
                                elif e.exc_info is not None and e.exc_info[0] is youtube_dl.utils.RegexNotFoundError:
                                    # Temp fix for instagram.com
                                    iie = youtube_dl.extractor.instagram.InstagramIE()
                                    if not iie.suitable(u):
                                        raise
                                    mobj = re.match(iie._VALID_URL, u)
                                    u = mobj.group('url') + '/embed/'
                                    ydl.params['force_generic_extractor'] = True
                                    vinfo = await extract_url_info(ydl, u)
                                    if 'entries' in vinfo:
                                        for i, _u in enumerate(vinfo['entries']):
                                            vinfo['entries'][i]['url'] = vinfo['entries'][i]['url'].replace('\\u0026', '&')
                                    else:
                                        vinfo['url'] = vinfo['url'].replace('\\u0026', '&')
                                else:
                                    raise

                            break

                        log.debug('video info received')
                    else:
                        if '_type' in vinfo and vinfo['_type'] == 'playlist':
                            for i, e in enumerate(vinfo['entries']):
                                e['requested_formats'] = None
                                vinfo['entries'][i] = ydl.process_video_result(e, download=False)
                        else:
                            vinfo['requested_formats'] = None
                            vinfo = ydl.process_video_result(vinfo, download=False)
                        log.debug('video info reprocessed with new format')
                except Exception as e:
                    if "Please log in or sign up to view this video" in str(e):
                        if 'vk.com' in u and 'username' not in params:
                            params['username'] = os.environ['VIDEO_ACCOUNT_USERNAME']
                            params['password'] = os.environ['VIDEO_ACCOUNT_PASSWORD']
                            ydl = youtube_dl.YoutubeDL(params=params)
                            try:
                                vinfo = await extract_url_info(ydl, u)
                            except Exception as e:
                                log.error(e)
                                if not is_group:
                                    await client.send_message(chat_id, "ERROR: " + str(e), reply_to=msg_id)
                                break
                    if 'are video-only' in str(e):
                        params['format'] = 'bestvideo[ext=mp4]/bestvideo'
                        ydl = youtube_dl.YoutubeDL(params=params)
                        try:
                            vinfo = await extract_url_info(ydl, u)
                        except Exception as e:
                            log.error(e)
                            if not is_group:
                                await client.send_message(chat_id, "ERROR: " + str(e), reply_to=msg_id)
                            break
                    if iu < len(urls) - 1:
                        log.error(e)
                        if not is_group:
                            await client.send_message(chat_id, "ERROR: " + str(e), reply_to=msg_id)
                        break
                    if not vinfo:
                        raise

                entries = None
                if '_type' in vinfo and (vinfo['_type'] == 'playlist' or vinfo['_type'] == 'multi_video'):
                    entries = vinfo['entries']
                else:
                    entries = [vinfo]

                for ie, entry in enumerate(entries):
                    if entry is None:
                        try:
                            if not is_group:
                                await client.send_message(chat_id, f'WARN: #{params["playliststart"] + ie} was skipped due to error', reply_to=msg_id)
                        except:
                            pass
                        continue
                    formats = entry.get('requested_formats')
                    _file_size = None
                    chosen_format = None
                    ffmpeg_av = None
                    http_headers = None
                    if 'http_headers' not in entry:
                        if formats is not None and 'http_headers' in formats[0]:
                            http_headers = formats[0]['http_headers']
                    else:
                        http_headers = entry['http_headers']
                    if not entry.get('direct', False):
                        http_headers['Referer'] = u

                    http_headers['Connection'] = 'keep-alive'
                    http_headers.update(user_headers)

                    if user_cookie:
                        http_headers['Cookie'] = user_cookie
                    _title = entry.get('title', '')
                    if _title == '':
                        entry['title'] = str(msg_id)

                    if cmd == 's':
                        direct_url = entry.get('url') if formats is None else formats[0].get('url')
                        if 'invidious.snopyta.org' in direct_url:
                            direct_url = normalize_url_path(direct_url)

                        await send_screenshot(chat_id,
                                              msg_txt,
                                              direct_url,
                                              http_headers=http_headers)
                        return
                    if cmd == 't':
                        thumb_url = entry.get('thumbnail')
                        if thumb_url:
                            await client.send_file(chat_id, thumb_url, reply_to=msg_id if not is_group else None)
                        else:
                            await client.send_message(chat_id, 'Media doesn\'t contain thumbnail')

                        return

                    _cut_time = (cut_time_start, cut_time_end) if cut_time_start else None
                    try:
                        if formats is not None:
                            for i, f in enumerate(formats):
                                if f['protocol'] in ['rtsp', 'rtmp', 'rtmpe', 'mms', 'f4m', 'ism',
                                                     'http_dash_segments']:
                                    # await bot.send_message(chat_id, "ERROR: Failed find suitable format for: " + entry['title'], reply_to=msg_id)
                                    continue
                                if 'm3u8' in f['protocol']:
                                    _file_size = await av_utils.m3u8_video_size(f['url'], http_headers)
                                else:
                                    if 'filesize' in f and f['filesize'] != 0 and f['filesize'] is not None and f[
                                        'filesize'] != 'none':
                                        _file_size = f['filesize']
                                    else:
                                        try:
                                            direct_url = f['url']
                                            if 'invidious.snopyta.org' in direct_url:
                                                direct_url = normalize_url_path(direct_url)
                                            _file_size = await av_utils.media_size(direct_url, http_headers=http_headers)
                                        except Exception as e:
                                            if i < len(formats) - 1 and '404 Not Found' in str(e):
                                                break
                                            else:
                                                raise

                                # Dash video
                                if f['protocol'] == 'https' and \
                                        (True if ('acodec' in f and (
                                                f['acodec'] == 'none' or f['acodec'] == None)) else False):
                                    vformat = f
                                    mformat = None
                                    vsize = 0

                                    direct_url = vformat['url']
                                    if 'invidious.snopyta.org' in direct_url:
                                        vformat['url'] = normalize_url_path(direct_url)

                                    if 'filesize' in vformat and vformat['filesize'] != 0 and vformat[
                                        'filesize'] is not None and vformat['filesize'] != 'none':
                                        vsize = vformat['filesize']
                                    else:
                                        vsize = await av_utils.media_size(vformat['url'], http_headers=http_headers)
                                    msize = 0
                                    # if there is one more format than
                                    # it's likely an url to audio
                                    if len(formats) > i + 1:
                                        mformat = formats[i + 1]

                                        direct_url = mformat['url']
                                        if 'invidious.snopyta.org' in direct_url:
                                            mformat['url'] = normalize_url_path(direct_url)

                                        if 'filesize' in mformat and mformat['filesize'] != 0 and mformat[
                                            'filesize'] is not None and mformat['filesize'] != 'none':
                                            msize = mformat['filesize']
                                        else:
                                            msize = await av_utils.media_size(mformat['url'], http_headers=http_headers)
                                    # we can't precisely predict media size so make it large for prevent cutting
                                    _file_size = vsize + msize + 10 * 1024 * 1024
                                    if _file_size < TG_MAX_FILE_SIZE or cut_time_start is not None or cmd == 'z':
                                        file_name = None
                                        if not cut_time_start and STORAGE_SIZE > _file_size > 0:
                                            STORAGE_SIZE -= _file_size
                                            _ext = 'mp4' if audio_mode == False else 'mp3'
                                            file_name = str(chat_id) + ':' + str(msg_id) + ':' + entry[
                                                'title'] + '.' + _ext
                                        ffmpeg_av = await av_source.FFMpegAV.create(vformat,
                                                                                    mformat,
                                                                                    headers=http_headers,
                                                                                    cut_time_range=_cut_time,
                                                                                    file_name=file_name if cmd != 'z' else None,
                                                                                    restrict_size=False if cmd == 'z' else True)
                                        chosen_format = f
                                    break
                                # m3u8
                                if ('m3u8' in f['protocol'] and
                                        (_file_size <= TG_MAX_FILE_SIZE or cut_time_start is not None or cmd == 'z')):
                                    chosen_format = f
                                    acodec = f.get('acodec')
                                    if acodec is None or acodec == 'none':
                                        if len(formats) > i + 1:
                                            mformat = formats[i + 1]
                                            if 'filesize' in mformat and mformat['filesize'] != 0 and mformat[
                                                'filesize'] is not None and mformat['filesize'] != 'none':
                                                msize = mformat['filesize']
                                            else:
                                                msize = await av_utils.media_size(mformat['url'],
                                                                                  http_headers=http_headers)
                                            msize += 10 * 1024 * 1024
                                            if (msize + _file_size) > TG_MAX_FILE_SIZE and cut_time_start is None and cmd != 'z':
                                                mformat = None
                                            else:
                                                _file_size += msize

                                    file_name = None
                                    if not cut_time_start and STORAGE_SIZE > _file_size > 0:
                                        STORAGE_SIZE -= _file_size
                                        _ext = 'mp4' if audio_mode == False else 'mp3'
                                        file_name = str(chat_id) + ':' + str(msg_id) + ':' + entry['title'] + '.' + _ext
                                    ffmpeg_av = await av_source.FFMpegAV.create(chosen_format,
                                                                                aformat=mformat,
                                                                                audio_only=True if audio_mode == True else False,
                                                                                headers=http_headers,
                                                                                cut_time_range=_cut_time,
                                                                                file_name=file_name if cmd != 'z' else None,
                                                                                restrict_size=False if cmd == 'z' else True)
                                    break
                                # regular video stream
                                if (0 < _file_size <= TG_MAX_FILE_SIZE) or cut_time_start is not None or cmd == 'z':
                                    chosen_format = f

                                    direct_url = chosen_format['url']
                                    if 'invidious.snopyta.org' in direct_url:
                                        chosen_format['url'] = normalize_url_path(direct_url)

                                    if audio_mode == True and not (chosen_format['ext'] == 'mp3'):
                                        ffmpeg_av = await av_source.FFMpegAV.create(chosen_format,
                                                                                    audio_only=True,
                                                                                    headers=http_headers,
                                                                                    cut_time_range=_cut_time,
                                                                                    restrict_size=False if cmd == 'z' else True)
                                    break

                        else:
                            if entry['protocol'] in ['rtsp', 'rtmp', 'rtmpe', 'mms', 'f4m', 'ism',
                                                     'http_dash_segments']:
                                # await bot.send_message(chat_id, "ERROR: Failed find suitable format for : " + entry['title'], reply_to=msg_id)
                                # if 'playlist' in entry and entry['playlist'] is not None:
                                recover_playlist_index = ie
                                break
                            if 'm3u8' in entry['protocol']:
                                if cut_time_start is None and entry.get('is_live', False) is False and audio_mode == False:
                                    _file_size = await av_utils.m3u8_video_size(entry['url'], http_headers=http_headers)
                                else:
                                    # we don't know real size
                                    _file_size = 0
                            else:
                                if 'filesize' in entry and entry['filesize'] != 0 and entry['filesize'] is not None and \
                                        entry['filesize'] != 'none':
                                    _file_size = entry['filesize']
                                else:
                                    direct_url = entry['url']
                                    if 'invidious.snopyta.org' in direct_url:
                                        entry['url'] = normalize_url_path(direct_url)
                                    try:
                                        _file_size = await av_utils.media_size(direct_url, http_headers=http_headers)
                                    except:
                                        _file_size = TG_MAX_FILE_SIZE
                            if ('m3u8' in entry['protocol'] and
                                    (_file_size <= TG_MAX_FILE_SIZE or cut_time_start is not None or cmd == 'z')):
                                chosen_format = entry
                                if entry.get('is_live') and not _cut_time:
                                    if cmd != 'z':
                                        cut_time_start, cut_time_end = (time(hour=0, minute=0, second=0),
                                                                        time(hour=1, minute=0, second=0))
                                    else:
                                        cut_time_start, cut_time_end = (time(hour=0, minute=0, second=0),
                                                                        time(hour=5, minute=30, second=0))
                                    _cut_time = (cut_time_start, cut_time_end)
                                file_name = None
                                if not cut_time_start and STORAGE_SIZE > _file_size > 0:
                                    STORAGE_SIZE -= _file_size
                                    _ext = 'mp4' if audio_mode == False else 'mp3'
                                    file_name = str(chat_id) + ':' + str(msg_id) + ':' + entry['title'] + '.' + _ext
                                ffmpeg_av = await av_source.FFMpegAV.create(chosen_format,
                                                                            audio_only=True if audio_mode == True else False,
                                                                            headers=http_headers,
                                                                            cut_time_range=_cut_time,
                                                                            file_name=file_name if cmd != 'z' else None,
                                                                            restrict_size=False if cmd == 'z' else True)
                            elif (_file_size <= TG_MAX_FILE_SIZE) or cut_time_start is not None or cmd == 'z':
                                chosen_format = entry
                                direct_url = chosen_format['url']
                                if 'invidious.snopyta.org' in direct_url:
                                    chosen_format['url'] = normalize_url_path(direct_url)
                                if audio_mode == True and not (chosen_format['ext'] == 'mp3'):
                                    ffmpeg_av = await av_source.FFMpegAV.create(chosen_format,
                                                                                audio_only=True,
                                                                                headers=http_headers,
                                                                                cut_time_range=_cut_time,
                                                                                restrict_size=False if cmd == 'z' else True)

                        if chosen_format is None and ffmpeg_av is None and cmd != 'z':
                            if len(preferred_formats) - 1 == ip:
                                if _file_size > TG_MAX_FILE_SIZE:
                                    log.info('too big file ' + str(_file_size))
                                    if 'http' in entry.get('protocol', '') and 'unknown' in entry.get('format', '') and entry.get('ext', '') not in ['unknown_video', 'mp3', 'mp4', 'm4a', 'ogg', 'mkv', 'flv', 'avi', 'webm']:
                                        if not user.donator:
                                            if not is_group:
                                                await client.send_message(chat_id,
                                                                        f'File bigger than <b>{sizeof_fmt(TG_MAX_FILE_SIZE)}</b>\n' +
                                                                        'Only <b>donators</b> can download files above this limit\n' +
                                                                        'Donate to me at least <b>5$</b> to use this feature\n'
                                                                        'Send /donate command to get info\n'
                                                                        'Notify @pony0boy after donation',
                                                                        reply_to=msg_id,
                                                                        parse_mode='html')
                                            return
                                        source = await av_source.URLav.create(entry.get('url'), http_headers)
                                        await upload_multipart_zip(source,
                                                                   (entry['title']+'.'+entry['ext']) if user_file_name is None else user_file_name,
                                                                   _file_size,
                                                                   chat_id,
                                                                   msg_id)
                                    else:
                                        if not is_group:
                                            await client.send_message(chat_id,
                                                                    f'ERROR: Too big media file size <b>{sizeof_fmt(_file_size)}</b>,\n'
                                                                    f'Telegram allow only up to <b>{sizeof_fmt(TG_MAX_FILE_SIZE)}</b>\n'
                                                                    'you can try cut it by command like:\n <code>/c 0-10:00 ' + u + '</code>',
                                                                    reply_to=msg_id,
                                                                    parse_mode="html")
                                else:
                                    log.info('failed find suitable media format')
                                    if not is_group:
                                        await client.send_message(chat_id, "ERROR: Failed find suitable media format",
                                                                reply_to=msg_id)
                                # await bot.send_message(chat_id, "ERROR: Failed find suitable video format", reply_to=msg_id)
                                return
                            # if 'playlist' in entry and entry['playlist'] is not None:
                            recover_playlist_index = ie
                            break
                        if cmd == 'z':
                            if is_group:
                                await client.send_message(chat_id,
                                                          'Command not available in chats',
                                                          reply_to=msg_id)
                                return
                            if not user.donator:
                                await client.send_message(chat_id,
                                                        'Only <b>donators</b> can use multipart archiving\n' +
                                                        'Donate to me at least <b>5$</b> to use this feature\n'
                                                        'Send /donate command to get info\n'
                                                        'Notify @pony0boy after donation',
                                                        reply_to=msg_id,
                                                        parse_mode='html')
                                return
                            if 'unknown' in entry.get('ext', '') or 'php' in entry.get('ext', ''):
                                mime, cd_file_name = await av_utils.media_mime(entry['url'],
                                                                               http_headers=http_headers)
                                if cd_file_name:
                                    cd_splited_file_name, cd_ext = os.path.splitext(cd_file_name)
                                    if len(cd_ext) > 0:
                                        entry['ext'] = cd_ext[1:]
                                    else:
                                        entry['ext'] = 'bin'
                                    if len(cd_splited_file_name) > 0:
                                        entry['title'] = cd_splited_file_name
                                else:
                                    ext = mimetypes.guess_extension(mime)
                                    if ext is None or ext == '' or ext == '.bin':
                                        entry['ext'] = 'bin'
                                    else:
                                        ext = ext[1:]
                                        entry['ext'] = ext
                            upload_file = ffmpeg_av if ffmpeg_av is not None else await av_source.URLav.create(
                                chosen_format['url'],
                                http_headers)
                            await upload_multipart_zip(upload_file,
                                                       (entry['title'] + '.' + entry['ext']) if user_file_name is None else user_file_name,
                                                       _file_size,
                                                       chat_id,
                                                       msg_id)
                            return
                        if audio_mode == True and _file_size != 0 and (ffmpeg_av is None or ffmpeg_av.file_name is None):
                            # we don't know real size due to converting formats
                            # so increase it in case of real size is less large then estimated
                            _file_size += 10 * 1024 * 1024  # 10MB

                        log.debug('uploading file')

                        width = height = duration = video_codec = audio_codec = None
                        title = performer = None
                        format_name = ''
                        if audio_mode == True:
                            if entry.get('duration') is None and chosen_format.get('duration') is None:
                                # info = await av_utils.av_info(chosen_format['url'],
                                #                               use_m3u8=('m3u8' in chosen_format['protocol']))
                                info = await av_utils.av_info(chosen_format['url'], http_headers=http_headers)
                                duration = int(float(info.get('format', {}).get('duration', 0)))
                            else:
                                duration = int(chosen_format['duration']) if 'duration' not in entry else int(
                                    entry['duration'])

                        elif (entry.get('duration') is None and chosen_format.get('duration') is None) or \
                                (chosen_format.get('width') is None or chosen_format.get('height') is None):
                            # info =  await av_utils.av_info(chosen_format['url'],
                            #                                use_m3u8=('m3u8' in chosen_format['protocol']))
                            info = await av_utils.av_info(chosen_format['url'], http_headers=http_headers)
                            try:
                                streams = info['streams']
                                for s in streams:
                                    if s.get('codec_type') == 'video':
                                        width = s['width']
                                        height = s['height']
                                        video_codec = s['codec_name']
                                    elif s.get('codec_type') == 'audio':
                                        audio_codec = s['codec_name']
                                if video_codec is None:
                                    audio_mode = True
                                _av_format = info['format']
                                duration = int(float(_av_format.get('duration', 0)))
                                format_name = _av_format.get('format_name', '').split(',')[0]
                                av_tags = _av_format.get('tags')
                                if av_tags is not None and len(av_tags.keys()) > 0:
                                    title = av_tags.get('title')
                                    performer = av_tags.get('artist')
                                    if performer is None:
                                        performer = av_tags.get('album')
                                _av_ext = chosen_format.get('ext', '')
                                if _av_ext == 'mp3' or _av_ext == 'm4a' or _av_ext == 'ogg' or format_name == 'mp3' or format_name == 'ogg':
                                    audio_mode = True
                            except KeyError:
                                try:
                                    width = chosen_format.get('width', 0)
                                    height = chosen_format.get('height', 0)
                                    duration = int(float(chosen_format.get('duration', 0)))
                                except:
                                    width = 0
                                    height = 0
                                    duration = 0
                                format_name = ''
                        else:
                            width, height, duration = chosen_format['width'], chosen_format['height'], \
                                                      int(chosen_format[
                                                              'duration']) if 'duration' not in entry else int(
                                                          entry['duration'])
                        if 'm3u8' in chosen_format.get('protocol',
                                                       '') and duration == 0 and ffmpeg_av is not None and cut_time_start is None:
                            if cmd != 'z':
                                cut_time_start, cut_time_end = (time(hour=0, minute=0, second=0),
                                                                time(hour=1, minute=0, second=0))
                            else:
                                cut_time_start, cut_time_end = (time(hour=0, minute=0, second=0),
                                                                time(hour=5, minute=30, second=0))
                            _cut_time = (cut_time_start, cut_time_end)
                            ffmpeg_av.close()
                            ffmpeg_av = None

                        if 'mp4 - unknown' in chosen_format.get('format', '') and chosen_format.get('ext', '') != 'mp4':
                            chosen_format['ext'] = 'mp4'
                        elif 'unknown' in chosen_format['ext'] or 'php' in chosen_format['ext']:
                            mime, cd_file_name = await av_utils.media_mime(chosen_format['url'],
                                                                           http_headers=http_headers)
                            if cd_file_name:
                                cd_splited_file_name, cd_ext = os.path.splitext(cd_file_name)
                                if len(cd_ext) > 0:
                                    chosen_format['ext'] = cd_ext[1:]
                                else:
                                    chosen_format['ext'] = ''
                                if len(cd_splited_file_name) > 0:
                                    chosen_format['title'] = cd_splited_file_name
                            else:
                                ext = mimetypes.guess_extension(mime)
                                if ext is None or ext == '' or ext == '.bin':
                                    if format_name is None or format_name == '':
                                        chosen_format['ext'] = 'bin'
                                    else:
                                        if format_name == 'mov':
                                            if audio_mode == True:
                                                format_name = 'm4a'
                                            else:
                                                format_name = 'mp4'
                                        if format_name == 'matroska':
                                            format_name = 'mkv'
                                        chosen_format['ext'] = format_name
                                else:
                                    ext = ext[1:]
                                    chosen_format['ext'] = ext

                        # in case of video is live we don't know real duration
                        if cut_time_start is not None:
                            if not entry.get('is_live') and duration > 1:
                                if cut_time.time_to_seconds(cut_time_start) > duration:
                                    await client.send_message(chat_id,
                                                            'ERROR: Cut start time is bigger than media duration: <b>' + str(
                                                                timedelta(seconds=duration)) + '</b>',
                                                            parse_mode='html')
                                    return
                                elif cut_time_end is not None and (
                                        cut_time.time_to_seconds(cut_time_end) > duration != 0):
                                    await client.send_message(chat_id,
                                                            'ERROR: Cut end time is bigger than media duration: <b>' + str(
                                                                timedelta(seconds=duration)) + '</b>\n'
                                                                                               'You can eliminate end time if you want it to be equal to media duration\n'
                                                                                               'Like: <code>/c 1:24 youtube.com</code>',
                                                            parse_mode='html')
                                    return
                            if cut_time_end is None:
                                if duration == 0:
                                    duration = 20000
                                duration = abs(duration - cut_time.time_to_seconds(cut_time_start))
                            else:
                                duration = abs(
                                    cut_time.time_to_seconds(cut_time_end) - cut_time.time_to_seconds(cut_time_start))

                        if (cut_time_start is not None or (audio_mode == True and (
                                chosen_format.get('ext') not in ['mp3', 'm4a', 'ogg']))) and ffmpeg_av is None:
                            ext = chosen_format.get('ext')
                            ffmpeg_av = await av_source.FFMpegAV.create(chosen_format,
                                                                        headers=http_headers,
                                                                        cut_time_range=_cut_time,
                                                                        ext=ext,
                                                                        audio_only=True if audio_mode == True else False,
                                                                        format_name=format_name if ext != 'mp4' and format_name != '' else '')
                        if cmd == 'm' and chosen_format.get('ext') != 'mp4' and ffmpeg_av is None and (
                                video_codec == 'h264' or video_codec == 'hevc') and \
                                (audio_codec == 'mp3' or audio_codec == 'aac'):
                            file_name = str(chat_id) + ':' + str(msg_id) + ':' + entry.get('title', 'default') + '.mp4'
                            if STORAGE_SIZE > _file_size > 0:
                                STORAGE_SIZE -= _file_size
                                ffmpeg_av = await av_source.FFMpegAV.create(chosen_format,
                                                                            headers=http_headers,
                                                                            file_name=file_name)
                        upload_file = ffmpeg_av if ffmpeg_av is not None else await av_source.URLav.create(
                            chosen_format['url'],
                            http_headers)

                        ext = (
                            chosen_format['ext'] if ffmpeg_av is None or ffmpeg_av.format is None else ffmpeg_av.format)
                        file_name_no_ext = entry['title']
                        if not file_name_no_ext[-1].isalnum():
                            file_name_no_ext = file_name_no_ext[:-1] + '_'
                        file_name = file_name_no_ext + '.' + ext
                        if _file_size == 0:
                            log.warning('file size is 0')

                        file_size = _file_size if _file_size != 0 and _file_size < TG_MAX_FILE_SIZE else TG_MAX_FILE_SIZE

                        ffmpeg_cancel_task = None
                        if ffmpeg_av is not None:
                            cancel_time = 20000
                            if cut_time_start is not None:
                                cancel_time += duration + 300
                            ffmpeg_cancel_task = asyncio.get_event_loop().call_later(cancel_time, ffmpeg_av.safe_close)
                        global TG_CONNECTIONS_COUNT
                        global TG_MAX_PARALLEL_CONNECTIONS
                        try:
                            if ffmpeg_av and ffmpeg_av.file_name:
                                await ffmpeg_av.stream.wait()
                                file_size_real = os.path.getsize(ffmpeg_av.file_name)
                                STORAGE_SIZE += file_size - file_size_real
                                file_size = file_size_real
                                local_file = aiofiles.open(ffmpeg_av.file_name, mode='rb')
                                upload_file = await local_file.__aenter__()
                            # uploading piped ffmpeg file is slow anyway
                            # TODO проверка на то что ffmpeg_av имееет file_name
                            if (file_size > 20 * 1024 * 1024 and TG_CONNECTIONS_COUNT < TG_MAX_PARALLEL_CONNECTIONS) and \
                                    (isinstance(upload_file, av_source.URLav) or
                                     isinstance(upload_file, aiofiles.threadpool.binary.AsyncBufferedReader)):
                                try:
                                    connections = 2
                                    if TG_CONNECTIONS_COUNT < 12 and file_size > 100 * 1024 * 1024:
                                        connections = 4

                                    TG_CONNECTIONS_COUNT += connections
                                    file = await fast_telethon.upload_file(client,
                                                                           upload_file,
                                                                           file_size,
                                                                           file_name if user_file_name is None else user_file_name,
                                                                           max_connection=connections)
                                finally:
                                    TG_CONNECTIONS_COUNT -= connections
                            else:
                                file = await client.upload_file(upload_file,
                                                                file_name=file_name if user_file_name is None else user_file_name,
                                                                file_size=file_size,
                                                                http_headers=http_headers)
                        except AuthKeyDuplicatedError as e:
                            if not is_group:
                                await client.send_message(chat_id, 'INTERNAL ERROR: try again')
                            log.fatal(e)
                            os.abort()
                        except ConnectionError as e:
                            if 'Cannot send requests while disconnected' in str(e):
                                await client.connect()
                                continue
                            raise
                        finally:
                            if ffmpeg_av and ffmpeg_av.file_name:
                                STORAGE_SIZE += file_size
                                if STORAGE_SIZE > MAX_STORAGE_SIZE:
                                    log.warning('logic error, reclaimed storage size bigger then initial')
                                    STORAGE_SIZE = MAX_STORAGE_SIZE
                                if isinstance(upload_file, aiofiles.threadpool.binary.AsyncBufferedReader):
                                    await local_file.__aexit__(exc_type=None, exc_val=None, exc_tb=None)
                                try:
                                    os.remove(ffmpeg_av.file_name)
                                except Exception as e:
                                    log.exception(e)

                            if ffmpeg_cancel_task is not None and not ffmpeg_cancel_task.cancelled():
                                ffmpeg_cancel_task.cancel()

                            if upload_file is not None:
                                if inspect.iscoroutinefunction(upload_file.close):
                                    await upload_file.close()
                                else:
                                    upload_file.close()

                        attributes = None
                        if audio_mode == True:
                            if performer is None:
                                performer = entry['artist'] if ('artist' in entry) and \
                                                               (entry['artist'] is not None) else None
                            if title is None:
                                title = entry['alt_title'] if ('alt_title' in entry) and \
                                                              (entry['alt_title'] is not None) else entry['title']
                            attributes = DocumentAttributeAudio(duration, title=title, performer=performer)
                        elif ext == 'mp4':
                            supports_streaming = False if ffmpeg_av is not None and ffmpeg_av.file_name is None else True
                            attributes = DocumentAttributeVideo(duration,
                                                                width,
                                                                height,
                                                                supports_streaming=supports_streaming)
                        else:
                            attributes = DocumentAttributeFilename(file_name)
                        force_document = False
                        if ext != 'mp4' and audio_mode == False:
                            force_document = True
                        log.debug('sending file')
                        video_note = False if audio_mode == True or force_document else True
                        voice_note = True if audio_mode == True else False
                        attributes = ((attributes,) if not force_document else None)
                        caption = entry['title'] if (user.default_media_type == users.DefaultMediaType.Video.value
                                                     and user.video_caption and audio_mode == False) or \
                                                    (((user.default_media_type == users.DefaultMediaType.Audio.value) or
                                                      (audio_mode == True))
                                                     and user.audio_caption) else ''
                        if is_group and user.settings.get('addlink', 1):
                            chat_username = message['chat']['username']
                            if chat_username is None:
                                if str(chat_id).startswith('-100'):
                                    link = f'https://t.me/c/{str(chat_id)[4:]}/{msg_id}'
                                else:
                                    link = f'https://t.me/ytbdownbot'
                            else:
                                link = f'https://t.me/{chat_username}/{msg_id}'
                            caption = '['+caption+']' + f'({link})'
                        recover_playlist_index = None
                        _thumb = None
                        try:
                            _thumb = await thumb.get_thumbnail(entry.get('thumbnail'), chosen_format)
                        except Exception as e:
                            log.warning('failed get thumbnail: ' + str(e))

                        for i in range(3):
                            try:
                                await client.send_file(chat_id, file,
                                                       video_note=video_note,
                                                       voice_note=voice_note,
                                                       attributes=attributes,
                                                       caption=caption,
                                                       force_document=force_document,
                                                       supports_streaming=False if ffmpeg_av is not None else True,
                                                       thumb=_thumb,
                                                       reply_to=msg_id if not is_group or user.settings.get('force_reply', 0) else None,
                                                       silent=True if is_group else False)
                            except AuthKeyDuplicatedError as e:
                                if not is_group:
                                    await client.send_message(chat_id, 'INTERNAL ERROR: try again')
                                log.fatal(e)
                                os.abort()
                            except Exception as e:
                                if i >= 2:
                                    raise e
                                log.exception(e)
                                await asyncio.sleep(1*i)
                                continue

                            break
                    except AuthKeyDuplicatedError as e:
                        if not is_group:
                            await client.send_message(chat_id, 'INTERNAL ERROR: try again')
                        log.fatal(e)
                        os.abort()
                    except Exception as e:
                        if len(preferred_formats) - 1 <= ip:
                            # raise exception for notify user about error
                            raise
                        else:
                            log.warning(e)
                            recover_playlist_index = ie

                if recover_playlist_index is None:
                    break
    finally:
        if not is_group or user.settings.get('nonprivate_action', 0):
            await action.__aexit__()



# api_id = int(os.environ['API_ID'])
api_id = 6
# api_hash = os.environ['API_HASH']
api_hash = "eb06d4abfb49dc3eeb1aeb98ae0f581e"


# YTDL_LAMBDA_URL = os.environ['YTDL_LAMBDA_URL']
# YTDL_LAMBDA_SECRET = os.environ['YTDL_LAMBDA_SECRET']

client = TelegramClient("bot", api_id, api_hash).start(bot_token=os.environ['BOT_TOKEN'])

vid_format = '((best[ext=mp4,height<=1080]+best[ext=mp4,height<=480])[protocol^=http]/best[ext=mp4,height<=1080]+best[ext=mp4,height<=480]/best[ext=mp4]+worst[ext=mp4]/best[ext=mp4]/(bestvideo[ext=mp4,height<=1080]+(bestaudio[ext=mp3]/bestaudio[ext=m4a]))[protocol^=http]/bestvideo[ext=mp4]+(bestaudio[ext=mp3]/bestaudio[ext=m4a]/bestaudio[ext=mp4])/best)[protocol!=http_dash_segments]'
vid_fhd_format = '((best[ext=mp4][height<=1080][height>720])[protocol^=http]/best[ext=mp4][height<=1080][height>720]/  (bestvideo[ext=mp4][height<=1080][height>720]+(bestaudio[ext=mp3]/bestaudio[ext=m4a]/bestaudio[ext=mp4]/bestaudio))[protocol^=http]/(bestvideo[ext=mp4][height<=1080][height>720])[protocol^=http]+(bestaudio[ext=mp3]/bestaudio[ext=m4a]/bestaudio[ext=mp4]/bestaudio)/bestvideo[ext=mp4][height<=1080][height>720]+(bestaudio[ext=mp3]/bestaudio[ext=m4a]/bestaudio[ext=mp4]/bestaudio)/  (best[ext=mp4][height<=720][height>360])[protocol^=http]/best[ext=mp4][height<=720][height>360]/  (bestvideo[ext=mp4][height<=720][height>360]+(bestaudio[ext=mp3]/bestaudio[ext=m4a]/bestaudio[ext=mp4]/bestaudio))[protocol^=http]/(bestvideo[ext=mp4][height<=720][height>360])[protocol^=http]+(bestaudio[ext=mp3]/bestaudio[ext=m4a]/bestaudio[ext=mp4]/bestaudio)/bestvideo[ext=mp4][height<=720][height>360]+(bestaudio[ext=mp3]/bestaudio[ext=m4a]/bestaudio[ext=mp4]/bestaudio) /  (best[ext=mp4][height<=360])[protocol^=http]/best[ext=mp4][height<=360]/  (bestvideo[ext=mp4][height<=360]+(bestaudio[ext=mp3]/bestaudio[ext=m4a]/bestaudio[ext=mp4]/bestaudio))[protocol^=http]/(bestvideo[ext=mp4][height<=360])[protocol^=http]+(bestaudio[ext=mp3]/bestaudio[ext=m4a]/bestaudio[ext=mp4]/bestaudio)/bestvideo[ext=mp4][height<=360]+(bestaudio[ext=mp3]/bestaudio[ext=m4a]/bestaudio[ext=mp4]/bestaudio)/   best[ext=mp4]   /bestvideo[ext=mp4]+(bestaudio[ext=mp3]/bestaudio[ext=m4a]/bestaudio[ext=mp4]/bestaudio)/best)[protocol!=http_dash_segments][vcodec !^=? av01]'
vid_hd_format = '((best[ext=mp4][height<=720][height>360])[protocol^=http]/best[ext=mp4][height<=720][height>360]/  (bestvideo[ext=mp4][height<=720][height>360]+(bestaudio[ext=mp3]/bestaudio[ext=m4a]/bestaudio[ext=mp4]/bestaudio))[protocol^=http]/(bestvideo[ext=mp4][height<=720][height>360])[protocol^=http]+(bestaudio[ext=mp3]/bestaudio[ext=m4a]/bestaudio[ext=mp4]/bestaudio)/bestvideo[ext=mp4][height<=720][height>360]+(bestaudio[ext=mp3]/bestaudio[ext=m4a]/bestaudio[ext=mp4]/bestaudio) /  (best[ext=mp4][height<=360])[protocol^=http]/best[ext=mp4][height<=360]/  (bestvideo[ext=mp4][height<=360]+(bestaudio[ext=mp3]/bestaudio[ext=m4a]/bestaudio[ext=mp4]/bestaudio))[protocol^=http]/(bestvideo[ext=mp4][height<=360])[protocol^=http]+(bestaudio[ext=mp3]/bestaudio[ext=m4a]/bestaudio[ext=mp4]/bestaudio)/bestvideo[ext=mp4][height<=360]+(bestaudio[ext=mp3]/bestaudio[ext=m4a]/bestaudio[ext=mp4]/bestaudio)/   best[ext=mp4]   /bestvideo[ext=mp4]+(bestaudio[ext=mp3]/bestaudio[ext=m4a]/bestaudio[ext=mp4]/bestaudio)/best)[protocol!=http_dash_segments][vcodec !^=? av01]'
vid_nhd_format = '((best[ext=mp4][height<=360])[protocol^=http]/best[ext=mp4][height<=360]/  (bestvideo[ext=mp4][height<=360]+(bestaudio[ext=mp3]/bestaudio[ext=m4a]/bestaudio[ext=mp4]/bestaudio))[protocol^=http]/(bestvideo[ext=mp4][height<=360])[protocol^=http]+(bestaudio[ext=mp3]/bestaudio[ext=m4a]/bestaudio[ext=mp4]/bestaudio)/bestvideo[ext=mp4][height<=360]+(bestaudio[ext=mp3]/bestaudio[ext=m4a]/bestaudio[ext=mp4]/bestaudio)/   best[ext=mp4]   /bestvideo[ext=mp4]+(bestaudio[ext=mp3]/bestaudio[ext=m4a]/bestaudio[ext=mp4]/bestaudio)/best)[protocol!=http_dash_segments][vcodec !^=? av01]'
worst_video_format = vid_nhd_format
audio_format = '((bestaudio[ext=m4a]/bestaudio[ext=mp3])[protocol^=http]/bestaudio/best[ext=mp4,height<=480]/best[ext=mp4]/best)[protocol!=http_dash_segments]'

url_extractor = URLExtract()

playlist_range_re = re.compile('([0-9]+)-([0-9]+)')
playlist_cmds = ['p', 'pa', 'pw']
available_cmds = ['start', 'ping', 'donate', 'settings', 'a', 'w', 'c', 's', 't', 'm', 'z'] + playlist_cmds

TG_MAX_FILE_SIZE = 2000 * 1024 * 1024
TG_MAX_PARALLEL_CONNECTIONS = 20
TG_CONNECTIONS_COUNT = 0
MAX_STORAGE_SIZE = int(os.getenv('STORAGE_SIZE', 0)) * 1024 * 1024
STORAGE_SIZE = MAX_STORAGE_SIZE
YT_TOO_MANY_REQUEST = False

async def shutdown():
    await tg_client_shutdown()
    sys.exit(1)


async def tg_client_shutdown(_app=None):
    await client.disconnect()


def sig_handler():
    asyncio.run_coroutine_threadsafe(shutdown(), asyncio.get_event_loop())


if __name__ == '__main__':
    print('Allowed storage size: ', STORAGE_SIZE)
    app = web.Application()
    app.add_routes([web.post('/bot', on_message)])
    # asyncio.get_event_loop().create_task(bot._run_until_disconnected())
    asyncio.get_event_loop().add_signal_handler(signal.SIGABRT, sig_handler)
    asyncio.get_event_loop().add_signal_handler(signal.SIGTERM, sig_handler)
    asyncio.get_event_loop().add_signal_handler(signal.SIGHUP, sig_handler)
    app.on_shutdown.append(tg_client_shutdown)
    asyncio.get_event_loop().create_task(web.run_app(app))
    client.run_until_disconnected()
