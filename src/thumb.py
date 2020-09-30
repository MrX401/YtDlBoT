
from aiohttp import ClientSession, TCPConnector
import io
from PIL import Image
from math import floor
import av_source
import av_utils
from datetime import timedelta

async def get_thumbnail(thumb_url, entry):
    img_data = None
    if thumb_url is None or thumb_url == 'none':
        img_data = await get_image_from_video(entry['url'], entry['http_headers'])
    else:
        async with ClientSession(connector=TCPConnector(verify_ssl=False)) as session:
            async with session.get(thumb_url) as resp:
                if resp.status != 200:
                    return None
                img_data = await resp.read()

    if img_data:
        thumb = io.BytesIO(img_data)
        thumb.seek(0)
        return resize_thumb(thumb)
    else:
        return None


def resize_thumb(thumb):
    try:
        image = Image.open(thumb)
    except Exception as e:
        print('failed open image ' + str(e))
        return None

    width, height = image.size

    n_width = n_height = None
    if width > height:
        n_width = 320
        n_height = floor(n_width / (width / height))
    else:
        n_height = 320
        n_width = floor(n_height / (height / width))

    image.thumbnail((n_width, n_height))
    new_image = io.BytesIO()
    image.save(new_image, format="JPEG", quality=99)
    new_image.seek(0)

    return new_image


async def get_image_from_video(url, headers=None):
    vinfo = await av_utils.av_info(url, headers)
    _format = vinfo.get('format')
    if _format:
        duration = int(float(vinfo.get('format', {}).get('duration', 0)))
        duration = int(duration / 3)
    else:
        duration = 5
    time = timedelta(seconds=duration)
    return await av_source.video_screenshot(url, headers, screen_time=time)


