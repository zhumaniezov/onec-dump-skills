#!/usr/bin/env python3
"""Read-only inspection of Designer exports. Python 3.10+, standard library.

No 1C runtime, network, shell execution, source rewriting or XSD validation.
JSON goes to stdout. Redirect it outside the inspected source directory.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import xml.etree.ElementTree as ET

TEXT_SUFFIXES = {'.xml', '.bsl', '.os', '.query'}
EXCLUDED = {'.git', '.claude', '.codex', '.agents', '.idea', 'node_modules',
            '__pycache__', '.venv', 'venv'}
UUID = re.compile(r'^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$')
CONFLICT = re.compile(r'(?m)^(?:<{7}(?: .*)?|={7}|>{7}(?: .*)?|\|{7}(?: .*)?)\r?$')


def local(tag):
    return tag.rsplit('}', 1)[-1]


def is_link(path):
    if path.is_symlink() or bool(getattr(path, 'is_junction', lambda: False)()):
        return True
    # Path.is_junction was added after our minimum Python version. On Windows,
    # skip all reparse points rather than walking junctions in older Python.
    attributes = getattr(os.lstat(path), 'st_file_attributes', 0)
    return bool(attributes & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 1024))


def walk(root, findings):
    root = Path(root).absolute()
    if not root.exists() or not root.is_dir() or is_link(root):
        raise ValueError('Root must be an existing real directory, not a link: ' + str(root))
    def failed(error):
        raise error
    for parent, dirs, files in os.walk(root, followlinks=False, onerror=failed):
        keep = []
        for name in sorted(dirs):
            p = Path(parent) / name
            if is_link(p):
                findings.append({'path': p.relative_to(root).as_posix(), 'kind': 'symlink_skipped', 'level': 'warning'})
            elif name not in EXCLUDED:
                keep.append(name)
        dirs[:] = keep
        for name in sorted(files):
            p = Path(parent) / name
            if is_link(p):
                findings.append({'path': p.relative_to(root).as_posix(), 'kind': 'symlink_skipped', 'level': 'warning'})
            else:
                yield p, p.relative_to(root).as_posix()


def decode(raw):
    if raw.startswith(b'\xef\xbb\xbf'):
        return raw.decode('utf-8-sig'), 'utf-8', 'UTF-8'
    if raw.startswith((b'\xff\xfe\x00\x00', b'\x00\x00\xfe\xff')):
        return raw.decode('utf-32'), 'utf-32', 'UTF-32'
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        return raw.decode('utf-16'), 'utf-16', 'UTF-16'
    text = raw.decode('utf-8')
    if '\x00' in text:
        raise ValueError('NUL in text: binary or unsupported encoding without BOM')
    return text, 'utf-8', 'none'


def text_info(raw):
    text, encoding, bom = decode(raw)
    crlf = text.count('\r\n')
    lf = text.count('\n') - crlf
    cr = text.count('\r') - crlf
    kinds = [name for name, n in [('CRLF', crlf), ('LF', lf), ('CR', cr)] if n]
    return text, {'encoding': encoding, 'bom': bom,
                  'eol': kinds[0] if len(kinds) == 1 else ('mixed' if kinds else 'none'),
                  'line_endings': {'crlf': crlf, 'lf': lf, 'cr': cr}}


def parse_xml(raw):
    text, _, _ = decode(raw)
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)\b', text, flags=re.I):
        raise ValueError('DTD/entity declarations are not supported')
    return ET.fromstring(raw)


def read_limited(path, max_mib):
    if is_link(path):
        raise ValueError('Refusing a symlink/junction: ' + str(path))
    size = path.stat().st_size
    maximum = max_mib * 1024 * 1024
    if size > maximum:
        raise OverflowError(f'{size} bytes exceeds limit {maximum}')
    with path.open('rb') as source:
        raw = source.read(maximum + 1)
    if len(raw) > maximum:
        raise OverflowError('File grew beyond size limit while reading')
    return raw


def xml_ids(root):
    # Review hints, not uniqueness assertions: extension and reference IDs may repeat.
    found = []
    for el in root.iter():
        for name, value in el.attrib.items():
            if local(name).lower() in {'uuid', 'id'} and UUID.fullmatch(value):
                found.append(local(el.tag) + '/@' + local(name) + '=' + value.lower())
        value = (el.text or '').strip()
        if UUID.fullmatch(value):
            found.append(local(el.tag) + '=' + value.lower())
    return sorted(found)


def finish_findings(findings, counts, limit):
    errors = sum(x['level'] == 'error' for x in findings)
    counts['errors'] = errors
    counts['warnings'] = len(findings) - errors
    status = 'errors' if errors else ('partial' if counts.get('skipped') else
             ('no_input' if not counts.get('checked') else ('warnings' if findings else 'ok')))
    return {'status': status, 'counts': counts, 'findings': findings[:limit],
            'findings_total': len(findings), 'truncated': len(findings) > limit,
            'platform_validation': 'not_run', 'xsd_validation': 'not_run',
            'bsl_validation': 'not_run'}, 1 if errors else 0


def inventory(args):
    findings = []
    counts = Counter()
    configs = []
    edt = []
    for p, rel in walk(args.root, findings):
        counts[p.suffix.lower() or '(none)'] += 1
        if p.suffix.lower() in {'.mdo', '.form', '.dcs'}:
            edt.append(rel)
        if p.name.lower() == 'configuration.xml':
            try:
                root = parse_xml(read_limited(p, args.max_mib))
                obj = next((e for e in root if local(e.tag) == 'Configuration'), root)
                props = next((e for e in obj if local(e.tag) == 'Properties'), None)
                values = {local(e.tag): (e.text or '').strip() for e in props} if props is not None else {}
                keys = ['Name', 'Version', 'CompatibilityMode', 'DefaultRunMode',
                        'ConfigurationExtensionPurpose', 'NamePrefix', 'Vendor']
                configs.append({'path': rel, 'format_version': root.get('version'),
                                'uuid': obj.get('uuid'),
                                'properties': {k: values[k] for k in keys if k in values}})
            except (ValueError, OSError, OverflowError, ET.ParseError) as error:
                findings.append({'path': rel, 'kind': 'config_read', 'level': 'error', 'detail': str(error)})
    return {'counts_by_suffix': dict(sorted(counts.items())), 'configs': configs[:args.limit],
            'configs_total': len(configs), 'edt_markers': edt[:args.limit],
            'edt_markers_total': len(edt), 'findings': findings[:args.limit],
            'findings_total': len(findings), 'platform_validation': 'not_run',
            'note': 'Configuration version is not the installed platform build. Paths are discovery hints.'}, 1 if any(x['level'] == 'error' for x in findings) else 0


def inspect_xml(args):
    p = Path(args.file)
    raw = read_limited(p, args.max_mib)
    root = parse_xml(raw)
    _, info = text_info(raw)
    rows = []
    total = 0
    # Iterative traversal avoids Python recursion limits on deep XML.
    pending = [(root, '/' + local(root.tag))]
    while pending:
        el, path = pending.pop()
        total += 1
        if len(rows) < args.limit:
            value = (el.text or '').strip()
            rows.append({'path': path, 'attributes': dict(el.attrib),
                         'text': value[:args.text_limit],
                         'text_truncated': len(value) > args.text_limit})
        children = []
        indexes = Counter()
        for child in el:
            tag = local(child.tag); indexes[tag] += 1
            children.append((child, path + '/' + tag + '[' + str(indexes[tag]) + ']'))
        pending.extend(reversed(children))
    return {'file': str(p), 'root': local(root.tag), 'namespace': root.tag.split('}')[0][1:] if '}' in root.tag else '',
            'text_info': info, 'nodes': rows, 'nodes_total': total, 'truncated': total > args.limit,
            'xml_parse': 'ok', 'xsd_validation': 'not_run', 'platform_validation': 'not_run'}, 0


def scan(args, snapshot=False):
    findings = []; files = {}; checked = 0; skipped = 0
    for p, rel in walk(args.root, findings):
        if p.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            raw = read_limited(p, args.max_mib)
        except OverflowError as error:
            skipped += 1
            findings.append({'path': rel, 'kind': 'size_skipped', 'level': 'warning', 'detail': str(error)})
            continue
        checked += 1
        record = {'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)}
        files[rel] = record
        try:
            text, info = text_info(raw); record.update(info)
        except (UnicodeError, ValueError) as error:
            findings.append({'path': rel, 'kind': 'encoding', 'level': 'error', 'detail': str(error)})
            continue
        if CONFLICT.search(text):
            findings.append({'path': rel, 'kind': 'merge_conflict', 'level': 'error'})
        if info['eol'] == 'mixed':
            findings.append({'path': rel, 'kind': 'mixed_eol', 'level': 'warning'})
        if p.suffix.lower() == '.xml':
            try:
                tree = parse_xml(raw)
                record['xml_ids'] = xml_ids(tree)
                record['xml_root'] = local(tree.tag)
            except (ET.ParseError, ValueError) as error:
                findings.append({'path': rel, 'kind': 'xml_parse', 'level': 'error', 'detail': str(error)})
    skipped += sum(x['kind'] == 'symlink_skipped' for x in findings)
    result, code = finish_findings(findings, {'checked': checked, 'skipped': skipped}, args.limit)
    if snapshot:
        result.update({'manifest_version': 1, 'root': str(Path(args.root).absolute()),
                       'suffixes': sorted(TEXT_SUFFIXES), 'files': files})
    return result, code


def compare(args):
    manifests = []
    for filename in [args.before, args.after]:
        data = json.loads(read_limited(Path(filename), args.max_mib).decode('utf-8-sig'))
        if not isinstance(data, dict) or data.get('manifest_version') != 1 or not isinstance(data.get('files'), dict):
            raise ValueError('Expected a version 1 snapshot manifest: ' + filename)
        if any(not isinstance(v, dict) or not isinstance(v.get('sha256'), str) for v in data['files'].values()):
            raise ValueError('Invalid file record in manifest: ' + filename)
        manifests.append(data)
    left, right = [x['files'] for x in manifests]
    changes = []
    for name in sorted(left.keys() | right.keys()):
        if name not in left:
            changes.append({'path': name, 'kind': 'added', 'review': []})
        elif name not in right:
            changes.append({'path': name, 'kind': 'deleted', 'review': []})
        elif left[name]['sha256'] != right[name]['sha256']:
            fields = [k for k in ['encoding', 'bom', 'eol', 'xml_ids', 'xml_root'] if left[name].get(k) != right[name].get(k)]
            changes.append({'path': name, 'kind': 'modified', 'review': fields})
    incomplete = any(x.get('status') in {'partial', 'errors', 'no_input'} for x in manifests)
    return {'status': 'partial' if incomplete else 'compared', 'changes': changes[:args.limit],
            'changes_total': len(changes), 'truncated': len(changes) > args.limit,
            'platform_validation': 'not_run',
            'note': 'UUID deltas need semantic review. Reordering or references may explain them. No semantic equivalence is asserted.'}, 0


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError('Must be positive')
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ['inventory', 'inspect', 'audit', 'snapshot', 'compare']:
        p = sub.add_parser(command)
        p.add_argument('--limit', type=positive, default=100, help='Max displayed findings/nodes; snapshots keep all file records')
        p.add_argument('--max-mib', type=positive, default=16, help='Maximum bytes read per file, MiB')
        if command == 'inspect':
            p.add_argument('file'); p.add_argument('--text-limit', type=positive, default=240)
        elif command == 'compare':
            p.add_argument('before'); p.add_argument('after')
        else:
            p.add_argument('root')
    args = parser.parse_args()
    try:
        if args.command == 'inventory': result, code = inventory(args)
        elif args.command == 'inspect': result, code = inspect_xml(args)
        elif args.command == 'compare': result, code = compare(args)
        else: result, code = scan(args, args.command == 'snapshot')
    except (OSError, ValueError, OverflowError, ET.ParseError) as error:
        result, code = {'status': 'error', 'detail': str(error), 'platform_validation': 'not_run'}, 2
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == '__main__':
    sys.exit(main())
