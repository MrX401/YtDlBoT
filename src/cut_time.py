
import re
from datetime import datetime


def parse_time(msg):
    time_re = re.compile(
        ' ((2[0-3]|[01]?[0-9]):)?(([0-5]?[0-9]):)?([0-5]?[0-9])(-((2[0-3]|[01]?[0-9]):)?(([0-5]?[0-9]):)?([0-5]?[0-9]))? ')
    time_match = time_re.search(msg)
    if time_match is None:
        raise Exception('Wrong time format')
    time_match = time_match.group()

    cut_time_start = cut_time_end = None
    if '-' in time_match:
        cut_time_start, cut_time_end = time_match.split('-')
    else:
        cut_time_start = time_match

    cut_time_start = to_isotime(cut_time_start)
    if cut_time_end is not None:
        cut_time_end = to_isotime(cut_time_end)
        if time_to_seconds(cut_time_end) - time_to_seconds(cut_time_start) <= 0:
            raise Exception('Start time must be less then end, command example /c 10:23-1:12:4 youtube.com')

    return cut_time_start, cut_time_end


def to_isotime(time: str):
    time = time.strip()
    for f in ['%S', '%M:%S', '%H:%M:%S', '%H:%M:%S.%f', '%M:%S.%f', '%S.%f']:
        try:
            return datetime.strptime(time, f).time()
        except ValueError:
            continue
    else:
        raise Exception('Incorrect time')


def time_to_seconds(t: datetime.time):
    return t.hour * 3600 + t.minute * 60 + t.second
