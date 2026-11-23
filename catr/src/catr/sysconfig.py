"""Console XML config files (`catr config get/set`): parse, change one leaf, verify, back up, write.

Edits are textual: only the text of one element changes, so every other byte of the file stays
as the console wrote it (attribute order, odd whitespace, trailing spaces). Each leaf carries
its own type, e.g. <enable type="unsignedInt" length="4">0</enable>, and new values are checked
against it.
"""
import re
import xml.etree.ElementTree as ET

FILES = {'system': '/vol/system/config/system.xml',
         'rtc': '/vol/system/config/rtc.xml',
         'sys_prod': '/vol/system/config/sys_prod.xml'}

# Keys `set` changes without --unsafe: logging, standby and RAM-disk tuning, crash behaviour.
ALLOWED = {('system', 'log/enable'), ('system', 'log/max_size'), ('system', 'standby/enable'),
           ('system', 'reset_on_crash'), ('system', 'fast_relaunch_value'),
           ('system', 'ramdisk/cache_user_code'), ('system', 'ramdisk/max_file_size'),
           ('system', 'ramdisk/cache_delay_ms')}
# Never changed by `config set`, even with --unsafe.
REFUSED = {('system', 'default_title_id'): 'the default title only changes through catr maintenance',
           ('system', 'default_os_id'): 'boot-critical: a wrong OS ID stops the console booting',
           ('system', 'version'): 'file format version; MCP checks it at boot',
           ('sys_prod', '*'): 'production identity (region, serial, product area); not editable'}


def leaves(data):
    """{'log/enable': {'type', 'length', 'value'}, ...} for every element without children."""
    root = ET.fromstring(data)
    result = {}

    def walk(element, prefix):
        for child in element:
            key = prefix + child.tag
            if len(child):
                walk(child, key + '/')
            elif child.get('type') != 'complex':
                result[key] = {'type': child.get('type'), 'length': child.get('length'),
                               'value': (child.text or '').strip()}
    walk(root, '')
    return root.tag, result


def check_value(leaf, value):
    """Normalise value for the leaf's declared type, or raise ValueError."""
    kind, length = leaf['type'], int(leaf['length'] or 0)
    if kind == 'unsignedInt':
        number = int(value, 0)
        if not 0 <= number < 1 << (8 * (length or 4)):
            raise ValueError(f'{value} is out of range for a {length}-byte unsignedInt')
        return str(number)
    if kind == 'signedInt':
        number = int(value, 0)
        bits = 8 * (length or 4)
        if not -(1 << (bits - 1)) <= number < 1 << (bits - 1):
            raise ValueError(f'{value} is out of range for a {length}-byte signedInt')
        return str(number)
    if kind == 'hexBinary':
        text = value.lower().removeprefix('0x')
        if len(text) != 2 * length or any(c not in '0123456789abcdef' for c in text):
            raise ValueError(f'hexBinary needs exactly {2 * length} hex digits')
        return text
    if kind == 'string':
        if len(value.encode('utf-8')) >= max(length, 1) or any(c in value for c in '<>&\r\n'):
            raise ValueError(f'string must be shorter than {length} bytes, without <>& or line breaks')
        return value
    raise ValueError(f'type {kind!r} is not supported for set')


def policy(file, key, unsafe):
    for (f, k), reason in REFUSED.items():
        if f == file and k in ('*', key):
            raise ValueError(f'{file}:{key} is refused: {reason}')
    if (file, key) not in ALLOWED and not unsafe:
        raise ValueError(f'{file}:{key} is not on the allowlist; add --unsafe to change it anyway')


def _span(text, tag, start, end):
    """(open_end, close_start, close_end) of the first <tag ...>...</tag> inside text[start:end]."""
    match = re.compile(rf'<{re.escape(tag)}(\s[^>]*)?>').search(text, start, end)
    if not match:
        raise ValueError(f'<{tag}> not found')
    if match.group(0).endswith('/>'):
        raise ValueError(f'<{tag}> is self-closing; set does not create text')
    close = text.find(f'</{tag}>', match.end(), end)
    if close < 0:
        raise ValueError(f'</{tag}> not found')
    return match.end(), close, close + len(tag) + 3


def replace(data, key, value):
    """data with the text of leaf `key` set to value; every other byte unchanged."""
    text = data.decode('utf-8')
    start, end = 0, len(text)
    *parents, leaf = key.split('/')
    root = ET.fromstring(data).tag
    start, end, _ = _span(text, root, start, end)
    for tag in parents:
        start, end, _ = _span(text, tag, start, end)
    open_end, close_start, _ = _span(text, leaf, start, end)
    return (text[:open_end] + value + text[close_start:]).encode('utf-8')


def plan_set(data, file, key, value, unsafe=False):
    """Checked edit: (new_data, old_value, new_value). Raises before anything is written."""
    policy(file, key, unsafe)
    root, before = leaves(data)
    if key not in before:
        raise ValueError(f'{file}:{key} does not exist (see catr config get {file})')
    new_value = check_value(before[key], value)
    if new_value == before[key]['value']:
        return data, new_value, new_value       # nothing to change
    new_data = replace(data, key, new_value)
    root_after, after = leaves(new_data)          # must still parse, same root
    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    if root_after != root or changed != {key} or after[key]['value'] != new_value:
        raise ValueError(f'edit check failed: changed {sorted(changed)}')
    return new_data, before[key]['value'], new_value
