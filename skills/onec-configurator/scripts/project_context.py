#!/usr/bin/env python3
"""Bounded, read-only project discovery. Python 3.10+, standard library.

Metadata index is structural, API/references are lexical, doctor never executes
or installs discovered programs. No output asserts 1C compatibility or compilation.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import sys
import xml.etree.ElementTree as ET

from dump_tool import decode, is_link, local, parse_xml, read_limited, walk

MD = 'http://v8.1c.ru/8.3/MDClasses'
Q = '{' + MD + '}'
PROPERTIES = {'Name', 'Version', 'Vendor', 'CompatibilityMode', 'DefaultRunMode',
              'ConfigurationExtensionPurpose', 'NamePrefix', 'ManagedApplication',
              'OrdinaryApplication', 'ClientManagedApplication', 'ClientOrdinaryApplication',
              'Server', 'ExternalConnection', 'Global', 'Privileged', 'ReuseReturnValues'}
TOOLS = {'powershell': ('pwsh', 'powershell'), 'oscript': ('oscript',),
         'java': ('java',), 'git': ('git',)}
DECLARATION = re.compile(r'(?im)^[ \t]*(Процедура|Функция|Procedure|Function)\s+'
                         r'([^\W\d]\w*)\s*\(([^)]*)\)[ \t]*(?:(Экспорт|Export)\b)?')


def masked_code(text):
    """Remove // comments and quoted strings, retaining offsets and line breaks.

    Does not evaluate conditional compilation or resolve names/types. A malformed
    unterminated string masks the rest of its file; use a BSL parser/compiler too.
    """
    result = list(text); i = 0
    while i < len(text):
        if text[i:i+2] == '//':
            end = text.find('\n', i)
            if end < 0: end = len(text)
            for j in range(i, end):
                if text[j] not in '\r\n': result[j] = ' '
            i = end
        elif text[i] == '"':
            result[i] = ' '; i += 1
            while i < len(text):
                if text[i] == '"':
                    result[i] = ' '; i += 1
                    if i < len(text) and text[i] == '"':
                        result[i] = ' '; i += 1
                    else: break
                else:
                    if text[i] not in '\r\n': result[i] = ' '
                    i += 1
        else: i += 1
    return ''.join(result)


def children_of(obj):
    result = []
    children = obj.find(Q + 'ChildObjects')
    if children is not None:
        for child in children:
            if not child.tag.startswith(Q): continue
            props = child.find(Q + 'Properties')
            name = props.findtext(Q + 'Name') if props is not None else child.text
            result.append({'kind': local(child.tag), 'name': (name or '').strip(),
                           'uuid': child.get('uuid')})
    return result


def index_objects(args):
    findings = []; rows = []; scanned = 0
    for path, rel in walk(args.root, findings):
        if path.suffix.lower() != '.xml': continue
        scanned += 1
        try:
            root = parse_xml(read_limited(path, args.max_mib))
            if root.tag != Q + 'MetaDataObject': continue
            for obj in root:
                if not obj.tag.startswith(Q): continue
                props = obj.find(Q + 'Properties')
                if props is None: continue
                values = {local(el.tag): (el.text or '').strip() for el in props
                          if el.tag.startswith(Q) and len(el) == 0 and local(el.tag) in PROPERTIES}
                rows.append({'path': rel, 'kind': local(obj.tag), 'name': values.get('Name'),
                             'uuid': obj.get('uuid'), 'format_version': root.get('version'),
                             'properties': values, 'children': children_of(obj)})
        except (ValueError, OSError, OverflowError, ET.ParseError) as error:
            findings.append({'path': rel, 'kind': 'xml_read', 'level': 'error', 'detail': str(error)})
    return rows, findings, scanned


def result(rows, key, findings, scanned, limit, analysis):
    # Skipped links or inaccessible text also make coverage incomplete, never silently green.
    status = 'partial' if findings else ('ok' if scanned else 'no_input')
    return {'status': status, key: rows[:limit], 'total': len(rows), 'truncated': len(rows) > limit,
            'files_scanned': scanned, 'findings': findings[:limit], 'findings_total': len(findings),
            'analysis': analysis, 'platform_validation': 'not_run'}, 0 if status == 'ok' else 1


def index(args):
    rows, findings, scanned = index_objects(args)
    return result(rows, 'objects', findings, scanned, args.limit, 'metadata_structure')


def bsl_files(args, findings):
    for path, rel in walk(args.root, findings):
        if path.suffix.lower() not in {'.bsl', '.os'}: continue
        try:
            text, encoding, bom = decode(read_limited(path, args.max_mib))
            yield rel, text, encoding, bom
        except (ValueError, OSError, OverflowError) as error:
            findings.append({'path': rel, 'kind': 'text_read', 'level': 'error', 'detail': str(error)})


def api(args):
    findings = []; rows = []; scanned = 0
    for rel, text, encoding, bom in bsl_files(args, findings):
        scanned += 1
        masked = masked_code(text)
        for match in DECLARATION.finditer(masked):
            kind, name, parameters, exported = match.groups()
            if args.name and name.casefold() != args.name.casefold(): continue
            if args.exported_only and not exported: continue
            line = text.count('\n', 0, match.start()) + 1
            preceding = masked[:match.start()].splitlines()
            source_preceding = text[:match.start()].splitlines()
            directives = []
            for previous, source_line in reversed(list(zip(preceding, source_preceding))):
                previous = previous.strip()
                if previous.startswith('&'): directives.insert(0, source_line.strip())
                elif previous: break
            rows.append({'path': rel, 'line': line, 'kind': kind, 'name': name,
                         'exported': bool(exported), 'signature': text[match.start():match.end()].strip(),
                         'directives': directives, 'encoding': encoding, 'bom': bom})
    return result(rows, 'definitions', findings, scanned, args.limit, 'lexical_not_semantic')


def references(args):
    findings = []; rows = []; scanned = 0
    if not args.symbol.strip(): raise ValueError('Symbol must be nonempty')
    pattern = re.compile(r'(?<![\w.])' + re.escape(args.symbol) + r'(?!\w)', re.I)
    for rel, text, _, _ in bsl_files(args, findings):
        scanned += 1
        original = text.splitlines()
        searched = text if args.include_text else masked_code(text)
        for n, line in enumerate(searched.splitlines(), 1):
            if pattern.search(line):
                rows.append({'path': rel, 'line': n, 'text': original[n-1][:500],
                             'text_truncated': len(original[n-1]) > 500})
    data, code = result(rows, 'matches', findings, scanned, args.limit, 'lexical_not_semantic')
    data['includes_comments_and_strings'] = args.include_text
    return data, code


def load_profile(path, maximum):
    if path is None: return {}
    profile = json.loads(read_limited(Path(path), maximum).decode('utf-8-sig'))
    if not isinstance(profile, dict) or set(profile) - {'schema', 'platform', 'tools'}:
        raise ValueError('Unknown profile fields or non-object profile')
    if type(profile.get('schema')) is not int or profile['schema'] != 1:
        raise ValueError('Profile schema must be integer 1')
    platform = profile.get('platform', {})
    if not isinstance(platform, dict) or set(platform) - {'path', 'version'}:
        raise ValueError('Unknown platform profile fields')
    if 'path' in platform and (not isinstance(platform['path'], str) or not platform['path']):
        raise ValueError('Platform path must be a nonempty string')
    if 'version' in platform and (not isinstance(platform['version'], str) or
            not re.fullmatch(r'\d+\.\d+\.\d+\.\d+', platform['version'])):
        raise ValueError('Declared platform version must contain four numeric components')
    configured = profile.get('tools', {})
    if not isinstance(configured, dict) or set(configured) - set(TOOLS):
        raise ValueError('Unknown tools profile fields')
    if any(not isinstance(value, str) or not value for value in configured.values()):
        raise ValueError('Tool paths must be nonempty strings')
    return profile


def locate(value, base):
    if not value: return None
    p = Path(value).expanduser()
    if not p.is_absolute():
        found = shutil.which(value)
        if found: return found
        p = base / p
    return str(p.absolute()) if p.is_file() else None


def doctor(args):
    profile = load_profile(args.profile, args.max_mib)
    base = Path(args.profile).absolute().parent if args.profile else Path(args.root).absolute()
    platform = profile.get('platform', {})
    found = locate(platform.get('path'), base)
    capabilities = {'platform': {'path': found, 'available': bool(found),
                    'version_declared': platform.get('version'), 'version_verification': 'not_run'}}
    configured = profile.get('tools', {})
    for key, names in TOOLS.items():
        value = locate(configured[key], base) if key in configured else next((shutil.which(n) for n in names if shutil.which(n)), None)
        capabilities[key] = {'path': value, 'available': bool(value), 'version_verification': 'not_run'}
    rows, findings, scanned = index_objects(args)
    missing = [key for key in args.require if not capabilities[key]['available']]
    status = 'missing_required' if missing else ('partial' if findings else 'ok')
    configurations = [row for row in rows if row['kind'] == 'Configuration']
    return {'status': status, **capabilities, 'python': sys.version.split()[0],
            'configurations': configurations[:args.limit], 'configurations_total': len(configurations),
            'truncated': len(configurations) > args.limit, 'findings': findings[:args.limit],
            'findings_total': len(findings), 'missing_required': missing,
            'platform_validation': 'not_run', 'programs_executed': [],
            'note': 'Availability is discovery only; declared build, compatibility, XML format and solution version are independent.'}, 0 if status == 'ok' else 1


def positive(value):
    number = int(value)
    if number <= 0: raise argparse.ArgumentTypeError('Must be positive')
    return number


def main(argv=None):
    for _stream in (sys.stdout, sys.stderr):  # консоль Windows не в UTF-8: без этого кириллица портится или падает
        if hasattr(_stream, 'reconfigure'):
            _stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    for name in ('index', 'api', 'references', 'doctor'):
        command = actions.add_parser(name)
        command.add_argument('root')
        command.add_argument('--limit', type=positive, default=200)
        command.add_argument('--max-mib', type=positive, default=16)
        if name == 'api':
            command.add_argument('--name')
            command.add_argument('--exported-only', action='store_true')
        if name == 'references':
            command.add_argument('--symbol', required=True)
            command.add_argument('--include-text', action='store_true')
        if name == 'doctor':
            command.add_argument('--profile')
            command.add_argument('--require', choices=['platform', *TOOLS], action='append', default=[])
    args = parser.parse_args(argv)
    try:
        data, code = globals()[args.action](args)
    except (ValueError, OSError, OverflowError, ET.ParseError) as error:
        data, code = {'status': 'error', 'error': str(error), 'platform_validation': 'not_run'}, 2
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return code


if __name__ == '__main__':
    sys.exit(main())
