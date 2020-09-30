import m3u8
import asyncio
import json
import os, signal
from aiohttp import ClientSession, hdrs, TCPConnector
from http.client import responses
from urllib.parse import urlparse


# convert each key-value to string like "key: value"
def dict_to_list(_dict):
    ret = []
    for k, v in _dict.items():
        ret.append(k + ": " + v)

    return ret

async def av_info(url, http_headers=''):
    info = await _av_info(url, http_headers)
    if len(info.keys()) == 0:
        # some sites return error if headers was passed
        info = await _av_info(url)

    return info

async def _av_info(url, http_headers=''):
    # if use_m3u8:
    #     m3u8_obj = await asyncio.get_event_loop().run_in_executor(None, m3u8.load, url)
    #     url = m3u8_obj.segments[0].absolute_uri  # override for mediainfo call
    #     dur = 0
    #     for s in m3u8_obj.segments:
    #         if hasattr(s, 'duration'):
    #             dur += s.duration

    mediainf_args = None
    # if audio_info:
    #     mediainf_args = '--Inform=Audio;%Duration%'
    # else:
    #     mediainf_args = '--Inform=Video;%Width%\\n%Height%\\n%Duration%'
    if http_headers != '':
        http_headers = '\n'.join(dict_to_list(http_headers))

    ff_proc = await asyncio.create_subprocess_exec('ffprobe',
                                                   '-v',
                                                   'error',
                                                   '-show_entries',
                                                   'stream=width,height,codec_name,codec_type',
                                                   '-show_entries',
                                                   'format=duration,format_name',
                                                   '-show_entries',
                                                   'format_tags=title,artist,album',
                                                   '-of',
                                                   'json',
                                                   '-headers',
                                                   http_headers,
                                                   url,
                                                   stdout=asyncio.subprocess.PIPE)
    # mi_proc = subprocess.Popen(['mediainfo', mediainf_args, '2>', '/dev/null', url],
    #                            stdout=subprocess.PIPE,
    #                            stderr=subprocess.STDOUT)
    try:
        out = await asyncio.wait_for(ff_proc.stdout.read(), timeout=120)
    except asyncio.TimeoutError as e:
        os.kill(ff_proc.pid, signal.SIGKILL)
        print(e)
        return {}
    try:
        info = json.loads(out)
    except:
        return {}
    if 'format' in info and 'duration' in info['format']:
        info['format']['duration'] = int(float(info['format']['duration']))
    return info
    # out = out.split(b'\n')
    #
    # w = h = None
    # if not audio_info:
    #     w = int(out[0])
    #     h = int(out[1])
    # if use_m3u8:
    #     dur = int(dur)
    # else:
    #     dur = int(int(float(out[2 if not audio_info else 0]))/1000)
    #
    # if audio_info:
    #     return dur
    # else:
    #     return w, h, dur


async def media_size(url, session=None, http_headers=None):
    content_length = None
    try:
        content_length = await _media_size(url, session, http_headers)
    except Exception as e:
        print(e)

    if content_length is not None:
        return content_length

    return await _media_size(url, session)

async def _media_size(url, session=None, http_headers=None):
    _session = None
    if session is None:
        _session = await ClientSession(connector=TCPConnector(verify_ssl=False)).__aenter__()
    else:
        _session = session
    content_length = 0
    try:
        async with _session.head(url, headers=http_headers, allow_redirects=True) as resp:
            if resp.status != 200:
                print('Request to url {} failed: '.format(url) + responses[resp.status])
            else:
                content_length = int(resp.headers.get(hdrs.CONTENT_LENGTH, '0'))
    except Exception as e:
        print(e)

    # try GET request when HEAD failed
    try:
        if content_length < 100:
            async with _session.get(url, headers=http_headers) as get_resp:
                if get_resp.status != 200:
                    raise Exception('Request failed: ' + str(get_resp.status) + " " + responses[get_resp.status])
                else:
                    content_length = int(get_resp.headers.get(hdrs.CONTENT_LENGTH, '0'))
    finally:
        if session is None:
            await _session.__aexit__(exc_type=None, exc_val=None, exc_tb=None)

    return content_length
    # head_req = request.Request(url, method='HEAD', headers=http_headers)
    # try:
    #     with request.urlopen(head_req) as resp:
    #         return int(resp.headers['Content-Length'])
    # except:
    #     return None


async def media_mime(url, http_headers=None):
    try:
        async with ClientSession(connector=TCPConnector(verify_ssl=False)) as session:
            async with session.get(url, headers=http_headers) as get_resp:
                if get_resp.content_disposition and get_resp.content_disposition.filename:
                    return None, get_resp.content_disposition.filename
                _content_type = get_resp.headers.getall(hdrs.CONTENT_TYPE)
                for ct in _content_type:
                    _media_type = ct.split('/')[0]
                    if _media_type == 'audio' or _media_type == 'video':
                        return ct, None
                else:
                    if len(_content_type) > 0:
                        return _content_type[0], None
    except:
        return None, ''


def m3u8_parse_url(url):
    _url = urlparse(url)
    if not _url.path.endswith('m3u8'):
        return url
    else:
        return m3u8._parsed_url(url) + '/'


async def m3u8_video_size(url, http_headers=None):
    m3u8_data = None
    m3u8_obj = None
    async with ClientSession(connector=TCPConnector(verify_ssl=False)) as session:
        async with session.get(url, headers=http_headers) as resp:
            m3u8_data = await resp.read()
            m3u8_obj = m3u8.loads(m3u8_data.decode())
            m3u8_obj.base_uri = m3u8_parse_url(str(resp.url))
        size = 0
        for seg in m3u8_obj.segments:
            size += await media_size(seg.absolute_uri, session=session, http_headers=http_headers)

    return size
