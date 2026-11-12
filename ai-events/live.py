"""Serve only the configured local camera relays through the preview origin."""
from contextlib import closing
from http.client import HTTPConnection, HTTPException
import re
from urllib.parse import quote, urlsplit

from camera_registry import Registry
from relay_manager import configured


MANIFEST = 'live.m3u8'
SEGMENT = re.compile(r'(?:segment|part)\d+\.ts')


def live_sources(source, records):
    relay_ports = {relay['port'] for relay in configured()}
    for camera in Registry(source, records).list(history=False, private=True):
        if not camera['enabled']:
            continue
        port = None
        try:
            url = urlsplit(camera['source_url'])
            if (url.scheme == 'http' and url.hostname in ('127.0.0.1', 'localhost')
                    and url.path == '/' + MANIFEST and url.port in relay_ports
                    and not (url.username or url.password or url.query or url.fragment)):
                port = url.port
        except ValueError:
            pass
        yield camera, port


def channel_inventory(source, records):
    return {'channels': [
        {'id': camera['channel_id'], 'name': camera['name'],
         'stream': f'/api/live/{quote(camera["channel_id"], safe="")}/{MANIFEST}' if port else None}
        for camera, port in live_sources(source, records)
    ]}


def relay_media(source, records, channel, name):
    if name != MANIFEST and not SEGMENT.fullmatch(name):
        raise ValueError('无效的直播文件')
    port = next((port for camera, port in live_sources(source, records) if camera['channel_id'] == channel), None)
    if port is None:
        raise ValueError('此通道尚未接入本机 HLS 中继')
    try:
        with closing(HTTPConnection('127.0.0.1', port, timeout=4)) as relay:
            relay.request('GET', '/' + name)
            response = relay.getresponse()
            if response.status != 200:
                return (404 if response.status == 404 and name != MANIFEST else 503), b'', 'text/plain'
            limit = 64 * 1024 if name == MANIFEST else 32 * 1024 * 1024
            data = response.read(limit + 1)
            if not data or len(data) > limit:
                return 503, b'', 'text/plain'
    except (OSError, HTTPException):
        return 503, b'', 'text/plain'
    mime = 'application/vnd.apple.mpegurl' if name == MANIFEST else 'video/mp2t'
    return 200, data, mime
