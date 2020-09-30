import typing
import ffmpeg
import asyncio
from aiohttp import ClientSession, ClientTimeout, TCPConnector
import cut_time
import av_utils
from datetime import datetime
import time
import os
import signal


class DumbReader(typing.BinaryIO):
    def write(self, s: typing.Union[bytes, bytearray]) -> int:
        pass

    def mode(self) -> str:
        pass

    def name(self) -> str:
        pass

    def close(self) -> None:
        pass

    def closed(self) -> bool:
        pass

    def fileno(self) -> int:
        pass

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        pass

    def readable(self) -> bool:
        pass

    def readline(self, limit: int = -1) -> typing.AnyStr:
        pass

    def readlines(self, hint: int = -1) -> typing.List[typing.AnyStr]:
        pass

    def seek(self, offset: int, whence: int = 0) -> int:
        pass

    def seekable(self) -> bool:
        pass

    def tell(self) -> int:
        pass

    def truncate(self, size: int = None) -> int:
        pass

    def writable(self) -> bool:
        pass

    def write(self, s: typing.AnyStr) -> int:
        pass

    def writelines(self, lines: typing.List[typing.AnyStr]) -> None:
        pass

    def __enter__(self) -> 'typing.IO[typing.AnyStr]':
        pass

    def __exit__(self, type, value, traceback) -> None:
        pass


class FFMpegAV(DumbReader):

    def __init__(self):
        self._buf = b''
        self.file_name = None

    @staticmethod
    async def create(vformat,
                     aformat=None,
                     audio_only=False,
                     headers='',
                     cut_time_range=None,
                     ext=None,
                     format_name='',
                     file_name=None,
                     restrict_size=True):
        if headers != '':
            headers = "\n".join(av_utils.dict_to_list(headers))
        ff = FFMpegAV()
        # _finput = ffmpeg.input(vformat['url'], **{"user-agent": user_agent, "loglevel": "error"})
        _finput = None

        if file_name:
            if len(file_name) > 100:
                file_name = file_name[:50] + file_name[-50:]
            ff.file_name = "'" + file_name.replace('/', '').replace('\'', '') + "'"

        cut_time_fix_args = []
        cut_time_start = cut_time_end = None
        if cut_time_range is not None:
            cut_time_fix_args = ['-avoid_negative_ts', 'make_zero']
            cut_time_start, cut_time_end = cut_time_range
            if cut_time_end is not None:

                diff_time = cut_time.time_to_seconds(cut_time_end) - cut_time.time_to_seconds(cut_time_start)
                if diff_time <= 0:
                    raise Exception('Cut end time is bigger than all media duration')
                cut_time_end = datetime.utcfromtimestamp(diff_time).time().isoformat()
            cut_time_start = cut_time_start.isoformat()

        if aformat:
            if cut_time_start is not None:
                _finput = ffmpeg.input(vformat['url'], headers=headers, **{'noaccurate_seek': None}, ss=cut_time_start,
                                       i=aformat['url'])
            else:
                _finput = ffmpeg.input(vformat['url'], headers=headers, i=aformat['url'])
        else:
            if cut_time_start is not None:
                _finput = ffmpeg.input(vformat['url'], headers=headers, **{'noaccurate_seek': None}, ss=cut_time_start)
            else:
                _finput = ffmpeg.input(vformat['url'], headers=headers)
        _fstream = None
        ff.format = None
        if audio_only:
            ff.format = 'mp3'
            acodec = None
            if 'acodec' in vformat and vformat['acodec'] is not None:
                # if vformat['acodec'].startswith('mp4a'):
                #     acodec = 'm4a'
                if vformat['acodec'].startswith('mp3'):
                    acodec = 'mp3'

                if acodec != None:
                    if not ff.file_name:
                        _fstream = _finput.output('pipe:',
                                                  format=acodec,
                                                  acodec='copy',
                                                  **{'vn': None})
                    else:
                        _fstream = _finput.output(ff.file_name,
                                                  format=acodec,
                                                  acodec='copy',
                                                  **{'vn': None})
            if not _fstream:
                if not ff.file_name:
                    _fstream = _finput.output('pipe:',
                                              format='mp3',
                                              acodec='mp3',
                                              **{'vn': None})
                else:
                    _fstream = _finput.output(ff.file_name,
                                              format='mp3',
                                              acodec='mp3',
                                              **{'vn': None})

        else:
            if format_name != '':
                _format = format_name
            else:
                _format = ext if ext else 'mp4'
            if _format == 'mp4':
                ff.format = _format
            audio_ext = aformat.get('ext', '') if aformat else ''
            if _format == 'mp4' and (audio_ext == '' or (audio_ext == 'mp3' or audio_ext == 'm4a')):
                acodec = 'copy'
            else:
                acodec = 'mp3'

            if not ff.file_name:
                _fstream = _finput.output('pipe:',
                                          format=_format,
                                          vcodec='copy',
                                          acodec=acodec,
                                          movflags='frag_keyframe')
            else:
                _fstream = _finput.output(ff.file_name,
                                          format=_format,
                                          vcodec='copy',
                                          acodec=acodec,
                                          movflags='faststart')

        cut_time_duration_arg = []
        if cut_time_end is not None:
            cut_time_duration_arg += ['-t', cut_time_end, "-shortest"]

        args = _fstream.compile()
        restrict_file_size_args = ['-fs', '2086666240'] if restrict_size else []
        if aformat:
            # args = _fstream.global_args('headers',
            #                             "\n".join(headers),
            #                             '-i',
            #                             aformat['url'],
            #                             '-map',
            #                             '0:v',
            #                             '-map',
            #                             '1:a').compile()
            if cut_time_start is not None:
                args = args[:3] + ['-noaccurate_seek', '-ss', cut_time_start] + args[3:5] + ['-headers', headers] + \
                       args[5:-1] + ['-map', '1:v', '-map', '0:a'] + cut_time_duration_arg + \
                       restrict_file_size_args + cut_time_fix_args + [args[-1]]
            else:
                args = args[:5] + ['-headers', headers] + args[5:-1] + ['-map', '1:v', '-map', '0:a'] + restrict_file_size_args + [args[-1]]

        else:
            args = args[:-1] + restrict_file_size_args + cut_time_duration_arg + cut_time_fix_args + [args[-1]]
            # if cut_time_start is not None and not audio_only:
            #     args[args.index('-acodec') + 1] = 'copy'  # copy audio if cutting due to music issue

        args = args[:1] + ["-loglevel",  "error", "-icy", "0", "-err_detect", "ignore_err", "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10"] + args[1:]
        if not ff.file_name:
            proc = await asyncio.create_subprocess_exec('ffmpeg',
                                                        *args[1:],
                                                        stdout=asyncio.subprocess.PIPE)
        else:
            proc = await asyncio.create_subprocess_exec('ffmpeg',
                                                        *args[1:])
        if headers != '':
            await asyncio.sleep(1)
            if proc.returncode is not None and proc.returncode != 0:
                return await FFMpegAV.create(vformat,
                                             aformat=aformat,
                                             audio_only=audio_only,
                                             headers='',
                                             cut_time_range=cut_time_range,
                                             ext=ext,
                                             format_name=format_name,
                                             file_name=file_name)
        ff.stream = proc

        return ff

    async def read(self, n: int = -1):
        buf = b''
        if len(self._buf) != 0:
            buf += self._buf
            self._buf = b''
        if n == -1:
            return await self.stream.stdout.read()

        while len(buf) < n:
            _data = await self.stream.stdout.read(n)
            if len(_data) == 0:
                break
            buf += _data
        if len(buf) > n != -1:
            self._buf = buf[n:]
            return buf[:n]
        else:
            return buf

    def close(self) -> None:
        # print('last data ', len(self.stream.stdout.read()))
        try:
            os.kill(self.stream.pid, signal.SIGTERM)
        except:
            pass

    def safe_close(self):
        self.close()
        time.sleep(2)
        # sometimes ffmpeg don't want to exit after any signal except SIGKILL
        os.kill(self.stream.pid, signal.SIGKILL)

    def __del__(self):
        try:
            os.kill(self.stream.pid, signal.SIGKILL)
        except:
            pass

    def __aiter__(self):
        return self

    async def __anext__(self):
        b = await self.read(512 * 1024)
        if len(b) == 0:
            raise StopAsyncIteration()
        else:
            return b


class URLav(DumbReader):
    def __init__(self):
        self._buf = b''

    @staticmethod
    async def create(url, headers=None):
        urlav = await URLav._create(url, headers)
        if urlav.request.status >= 400:
            await urlav.close()
            urlav = await URLav._create(url)
            urlav.request.raise_for_status()
        return urlav


    @staticmethod
    async def _create(url, headers=None):
        u = URLav()
        u.session = await ClientSession(timeout=ClientTimeout(total=10800,
                                                              connect=240,
                                                              sock_connect=200,
                                                              sock_read=600), connector=TCPConnector(verify_ssl=False)).__aenter__()
        u.request = await u.session.get(url, headers=headers)
        # u.request = await asks.get(url, headers=headers, stream=True, max_redirects=5)
        # u.body = u.request.body(timeout=14400)
        return u

    async def read(self, n: int = -1):
        buf = b''
        if len(self._buf) != 0:
            buf += self._buf
            self._buf = b''
        if n == -1:
            return await self.request.read()

        while len(buf) < n:
            _data = await self.request.content.read(n)
            if len(_data) == 0:
                break
            buf += _data
        if len(buf) > n != -1:
            self._buf = buf[n:]
            return buf[:n]
        else:
            return buf

    async def close(self) -> None:
        # self.request.release()
        await self.session.__aexit__(exc_type=None, exc_val=None, exc_tb=None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        b = await self.read(512 * 1024)
        if len(b) == 0:
            raise StopAsyncIteration()
        else:
            return b


async def video_screenshot(url, headers=None, screen_time=None, quality=5):
    image_data = await _video_screenshot(url, headers, screen_time=screen_time, quality=quality)
    if len(image_data) == 0:
        # some sites return error if headers was passed
        image_data = await _video_screenshot(url, screen_time=screen_time, quality=quality)

    return image_data

async def _video_screenshot(url, headers=None, screen_time=None, quality=5):
    if headers:
        headers = "\n".join(av_utils.dict_to_list(headers))

    args = {'icy': '0'}
    if screen_time:
        args['ss'] = screen_time
    if headers:
        args['headers'] = headers

    _finput = ffmpeg.input(url, **args)
    _fstream = _finput.output('pipe:',
                              format='image2pipe',
                              vcodec='mjpeg',
                              **{'q:v': quality,
                                 'vframes:v': 1})
    args = _fstream.compile()
    proc = await asyncio.create_subprocess_exec('ffmpeg',
                                                *args[1:],
                                                stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE)

    try:
        out = await asyncio.wait_for(proc.stdout.read(), timeout=360)
    except asyncio.TimeoutError as e:
        os.kill(proc.pid, signal.SIGKILL)
        raise e
    return out
