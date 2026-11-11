#!/usr/bin/env python3
"""Guarded byte-preserving edits to local Designer text exports (stdlib only).

Plans are trusted local data, not authenticated commands or a security sandbox.
XML checks establish syntax only; no platform, schema, or compilation checks.
"""
import argparse
import base64
import difflib
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import sys
import tempfile
import xml.etree.ElementTree as ET
from xml.parsers import expat
from xml.sax.saxutils import escape

from dump_tool import is_link

SCHEMA = 1
VERSION = '1.0'
TOOL = 'onec-source-edit'
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_PLAN_BYTES = 64 * 1024 * 1024
MAX_REQUEST_BYTES = 16 * 1024 * 1024
MAX_OPERATIONS = 256
SUFFIXES = {'.xml', '.bsl', '.os', '.query'}
ENCODINGS = {'utf-8': ('utf-8', b''), 'utf-8-bom': ('utf-8', b'\xef\xbb\xbf'),
             'utf-16le-bom': ('utf-16le', b'\xff\xfe'),
             'utf-16be-bom': ('utf-16be', b'\xfe\xff')}
EOLS = {'LF': '\n', 'CRLF': '\r\n', 'CR': '\r'}
HASH = re.compile(r'^[0-9a-f]{64}$')


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def plan_digest(plan):
    return digest(canonical({k: v for k, v in plan.items() if k != 'integrity_sha256'}))


def fields(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise ValueError('Missing or unknown fields; required: ' + ', '.join(sorted(required)))


def string(value, label):
    if not isinstance(value, str):
        raise ValueError(label + ' must be a string')
    return value


def hash_value(value):
    if not isinstance(value, str) or not HASH.fullmatch(value):
        raise ValueError('expected SHA256 must be 64 lowercase hex characters')


def relative_name(name):
    string(name, 'path')
    win = PureWindowsPath(name)
    parts = name.split('/')
    if (not name or '\\' in name or ':' in name or '\x00' in name or name.startswith('/')
            or win.is_absolute() or win.drive or any(p in {'', '.', '..'} for p in parts)
            or any(p.casefold() == '.git' for p in parts)
            or any(p.endswith((' ', '.')) for p in parts)):
        raise ValueError('Unsafe relative POSIX path: ' + name)
    if Path(name).suffix.lower() not in SUFFIXES:
        raise ValueError('Only XML/BSL/OS/query targets are supported: ' + name)
    return name


def real_path(path, require_directory=False):
    """Reject links/reparse points in every existing ancestor before resolving."""
    path = Path(os.path.abspath(path))
    if any(part.casefold() == '.git' for part in path.parts):
        raise ValueError('.git internals are refused: ' + str(path))
    for part in reversed([path, *path.parents]):
        if os.path.lexists(part) and is_link(part):
            raise ValueError('Link/junction/reparse path refused: ' + str(part))
    if require_directory and not path.is_dir():
        raise ValueError('Project/parent must be an existing directory: ' + str(path))
    return path.resolve()


def project_root(project):
    return real_path(project, True)


def target_path(root, name):
    relative_name(name)
    path = root
    for part in name.split('/'):
        if not path.is_dir():
            raise ValueError('Target parents must already exist: ' + name)
        collisions = [p.name for p in path.iterdir() if p.name.casefold() == part.casefold() and p.name != part]
        if collisions:
            raise ValueError('Case-fold collision: ' + name)
        path = path / part
        if os.path.lexists(path) and is_link(path):
            raise ValueError('Link/junction/reparse target refused: ' + name)
    if os.path.lexists(path) and not path.is_file():
        raise ValueError('Target is not a regular file: ' + name)
    return path


def read_bytes(path, limit=MAX_FILE_BYTES):
    if path.stat().st_size > limit:
        raise ValueError('File exceeds byte limit: ' + str(limit))
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('File grew beyond byte limit')
    return raw


def decode(raw):
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError('Text exceeds file byte limit')
    if raw.startswith((b'\xff\xfe\x00\x00', b'\x00\x00\xfe\xff')):
        raise ValueError('UTF-32 is unsupported')
    for name in ['utf-8-bom', 'utf-16le-bom', 'utf-16be-bom']:
        codec, bom = ENCODINGS[name]
        if raw.startswith(bom):
            text = raw[len(bom):].decode(codec)
            break
    else:
        name = 'utf-8'
        text = raw.decode('utf-8')
    if '\x00' in text:
        raise ValueError('NUL/binary text or encoding without BOM refused')
    return text, name


def encode(text, encoding):
    codec, bom = ENCODINGS[encoding]
    raw = bom + text.encode(codec)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError('Result exceeds file byte limit')
    return raw


def normalize_eol(text, eol):
    return re.sub(r'\r\n|\r|\n', lambda match: eol, text)


def replacement_eol(text, new):
    kinds = set(re.findall(r'\r\n|\r|\n', text))
    if len(kinds) == 1:
        return normalize_eol(new, next(iter(kinds)))
    if len(kinds) > 1 and re.search(r'[\r\n]', new):
        raise ValueError('Cannot infer inserted EOL for mixed-EOL file')
    return new


def xml_valid(raw):
    text, _ = decode(raw)
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)\b', text, re.I):
        raise ValueError('DTD/entity declarations refused')
    ET.fromstring(raw)


def xml_set_property(text, property_name, expected_old, new):
    """Use parser byte offsets; change only scalar content, never serialize XML."""
    source = text.encode('utf-8')
    parser = expat.ParserCreate(encoding='utf-8', namespace_separator='}')
    stack = []
    nodes = []

    def opening_end(start):
        quote = None
        for offset in range(start, len(source)):
            char = source[offset]
            if quote is not None:
                if char == quote:
                    quote = None
            elif char in (34, 39):
                quote = char
            elif char == 62:
                return offset + 1
        raise ValueError('Unclosed XML tag')

    def start(name, attrs):
        begin = parser.CurrentByteIndex
        end = opening_end(begin)
        node = dict(name=name.rsplit('}', 1)[-1], start=begin, open_end=end,
                    parent=stack[-1] if stack else None, children=[], text='', comments=[],
                    self_close=source[begin:end].rstrip().endswith(b'/>'))
        if stack:
            stack[-1]['children'].append(node)
        stack.append(node)
        nodes.append(node)

    def end(name):
        node = stack.pop()
        node['end_start'] = parser.CurrentByteIndex

    def characters(value):
        if stack:
            stack[-1]['text'] += value

    def comment(value):
        if stack:
            begin = parser.CurrentByteIndex
            end = source.index(b'-->', begin) + 3
            stack[-1]['comments'].append(source[begin:end])

    def instruction(target, value):
        if len(stack) >= 4:
            raise ValueError('Processing instructions in scalar property refused')

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = characters
    parser.CommentHandler = comment
    parser.ProcessingInstructionHandler = instruction
    parser.Parse(source, True)
    root = nodes[0]
    if root['name'] != 'MetaDataObject' or len(root['children']) != 1:
        raise ValueError('Expected one top-level object inside MetaDataObject')
    obj = root['children'][0]
    props = [n for n in obj['children'] if n['name'] == 'Properties']
    if len(props) != 1:
        raise ValueError('Expected unique direct Properties')
    found = [n for n in props[0]['children'] if n['name'] == property_name]
    if len(found) != 1 or found[0]['children']:
        raise ValueError('Property must be a unique direct scalar: ' + property_name)
    node = found[0]
    if node['text'] != expected_old:
        raise ValueError('Decoded old property value differs: ' + property_name)
    # Character references preserve CR against XML EOL normalization.
    value = replacement_eol(text, escape(new).replace('\r', '&#13;')).encode('utf-8') + b''.join(node['comments'])
    if node['self_close']:
        opening = source[node['start']:node['open_end']]
        tag = re.match(rb'<([^\s/>]+)', opening).group(1)
        opening = re.sub(rb'/\s*>$', b'>', opening)
        source = source[:node['start']] + opening + value + b'</' + tag + b'>' + source[node['open_end']:]
    else:
        source = source[:node['open_end']] + value + source[node['end_start']:]
    return source.decode('utf-8')


def validate_request(request):
    fields(request, {'schema', 'operations'})
    if type(request['schema']) is not int or request['schema'] != SCHEMA:
        raise ValueError('Unsupported request schema')
    operations = request['operations']
    if not isinstance(operations, list) or not 1 <= len(operations) <= MAX_OPERATIONS:
        raise ValueError('operations must contain 1..256 entries')
    seen = {}
    for op in operations:
        if not isinstance(op, dict):
            raise ValueError('Operation must be an object')
        kind = op.get('op')
        common = {'op', 'path', 'expected_sha256'}
        if kind == 'replace-text':
            fields(op, common | {'old', 'new'}, {'expected_count'})
            string(op['old'], 'old')
            string(op['new'], 'new')
            count = op.get('expected_count', 1)
            if not op['old'] or type(count) is not int or count <= 0:
                raise ValueError('old must be nonempty; expected_count must be a positive integer')
        elif kind == 'xml-set-property':
            fields(op, common | {'property', 'expected_old', 'new'})
            for key in ['property', 'expected_old', 'new']:
                string(op[key], key)
            if not op['property']:
                raise ValueError('Property name must be nonempty')
        elif kind == 'create-text':
            fields(op, {'op', 'path', 'content', 'encoding', 'eol'})
            string(op['content'], 'content')
            if op['encoding'] not in ENCODINGS or op['eol'] not in EOLS:
                raise ValueError('Unsupported explicit create encoding/eol')
        else:
            raise ValueError('Unsupported operation: ' + str(kind))
        name = relative_name(op['path'])
        folded = name.casefold()
        if folded in seen and seen[folded] != name:
            raise ValueError('Case-fold target collision')
        seen[folded] = name
        if kind != 'create-text':
            hash_value(op['expected_sha256'])
        if kind == 'xml-set-property' and Path(name).suffix.lower() != '.xml':
            raise ValueError('xml-set-property requires XML suffix')


def unified_diff(before, after, name):
    old = decode(before)[0] if before is not None else ''
    new = decode(after)[0]
    lines = difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                 fromfile='a/' + name if before is not None else '/dev/null',
                                 tofile='b/' + name)
    # Keep diff readable for files without a final EOL too.
    return ''.join(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n' for line in lines)


def build_changes(request, load):
    validate_request(request)
    staged = {}
    for op in request['operations']:
        name = op['path']
        if name not in staged:
            before = load(name)
            if before is not None:
                decode(before)
                if Path(name).suffix.lower() == '.xml':
                    xml_valid(before)
            staged[name] = {'before': before, 'after': before}
        state = staged[name]
        if op['op'] == 'create-text':
            if state['after'] is not None:
                raise ValueError('Create target already exists: ' + name)
            raw = encode(normalize_eol(op['content'], EOLS[op['eol']]), op['encoding'])
        else:
            if state['before'] is None or state['after'] is None or digest(state['before']) != op['expected_sha256']:
                raise ValueError('Baseline hash mismatch or target missing: ' + name)
            text, encoding = decode(state['after'])
            if op['op'] == 'replace-text':
                if text.count(op['old']) != op.get('expected_count', 1):
                    raise ValueError('Exact occurrence count differs: ' + name)
                text = text.replace(op['old'], replacement_eol(text, op['new']))
            else:
                text = xml_set_property(text, op['property'], op['expected_old'], op['new'])
            raw = encode(text, encoding)
        decode(raw)
        if Path(name).suffix.lower() == '.xml':
            xml_valid(raw)
        state['after'] = raw
    changes = []
    for name, state in staged.items():
        before, after = state['before'], state['after']
        changes.append(dict(path=name, kind='create' if before is None else 'modify',
                            before_sha256=digest(before) if before is not None else None,
                            after_sha256=digest(after),
                            before_b64=base64.b64encode(before).decode('ascii') if before is not None else None,
                            after_b64=base64.b64encode(after).decode('ascii'),
                            diff=unified_diff(before, after, name)))
    return changes


def make_plan(project, request):
    root = project_root(project)
    def load(name):
        path = target_path(root, name)
        return read_bytes(path) if path.exists() else None
    plan = dict(schema=SCHEMA, tool=TOOL, version=VERSION, project_root=str(root), request=request,
                changes=build_changes(request, load), platform_validation='not_run')
    plan['integrity_sha256'] = plan_digest(plan)
    if len(canonical(plan)) + 1 > MAX_PLAN_BYTES:
        raise ValueError('Plan exceeds byte limit')
    return plan


def payload(value):
    if not isinstance(value, str):
        raise ValueError('Payload must be base64 text')
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError('Invalid base64 payload') from error
    if len(raw) > MAX_FILE_BYTES or base64.b64encode(raw).decode('ascii') != value:
        raise ValueError('Noncanonical or oversized payload')
    return raw


def validate_plan(plan):
    fields(plan, {'schema', 'tool', 'version', 'project_root', 'request', 'changes',
                  'platform_validation', 'integrity_sha256'})
    if (type(plan['schema']) is not int or plan['schema'] != SCHEMA or plan['tool'] != TOOL
            or plan['version'] != VERSION or plan['platform_validation'] != 'not_run'):
        raise ValueError('Unsupported plan schema/tool/version')
    root = string(plan['project_root'], 'project_root')
    if not Path(root).is_absolute() or str(Path(root)) != root:
        raise ValueError('Plan root must be an absolute canonical path')
    if len(canonical(plan)) > MAX_PLAN_BYTES or plan_digest(plan) != plan['integrity_sha256']:
        raise ValueError('Plan integrity mismatch or size limit exceeded')
    if not isinstance(plan['changes'], list) or not 1 <= len(plan['changes']) <= MAX_OPERATIONS:
        raise ValueError('Invalid changes')
    baseline = {}
    folded = set()
    for change in plan['changes']:
        fields(change, {'path', 'kind', 'before_sha256', 'after_sha256', 'before_b64', 'after_b64', 'diff'})
        name = relative_name(change['path'])
        if name.casefold() in folded:
            raise ValueError('Duplicate/case-fold plan target')
        folded.add(name.casefold())
        before = None if change['before_b64'] is None else payload(change['before_b64'])
        after = payload(change['after_b64'])
        if ((digest(before) if before is not None else None) != change['before_sha256']
                or digest(after) != change['after_sha256']):
            raise ValueError('Payload/hash inconsistency')
        baseline[name] = before
    def load(name):
        if name not in baseline:
            raise ValueError('Request target missing from changes')
        return baseline[name]
    if build_changes(plan['request'], load) != plan['changes']:
        raise ValueError('Request/payload/diff inconsistency')
    return plan


def precondition(root, change, expected_hash):
    # Revalidate root and all ancestors as well as the target just before writes.
    if project_root(root) != root:
        raise ValueError('Project root changed')
    path = target_path(root, change['path'])
    if expected_hash is None:
        if os.path.lexists(path):
            raise ValueError('Create target appeared: ' + change['path'])
    elif not path.exists() or digest(read_bytes(path)) != expected_hash:
        raise ValueError('Stale target: ' + change['path'])
    return path


def commit_file(root, change, raw, expected_hash):
    """Atomic per-file publish. Create uses exclusive hard-link publication."""
    path = precondition(root, change, expected_hash)
    mode = stat.S_IMODE(path.stat().st_mode) if expected_hash is not None else 0o600
    fd, temp_name = tempfile.mkstemp(prefix='.source-edit-', dir=path.parent)
    temporary = Path(temp_name)
    published = False
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        precondition(root, change, expected_hash)
        if expected_hash is None:
            os.link(temporary, path)  # Atomic no-overwrite; never fall back to replace.
        else:
            os.replace(temporary, path)
        published = True
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError as error:
            # Publication may already have succeeded; the caller must still undo it.
            error.source_edit_published = published
            error.source_edit_temporary = str(temporary)
            raise


def write_report(journal, report):
    (journal / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def apply_plan(project, plan, journal, enabled=False):
    validate_plan(plan)
    root = project_root(project)
    if str(root) != plan['project_root']:
        raise ValueError('Plan is bound to a different resolved project root')
    for change in plan['changes']:
        precondition(root, change, change['before_sha256'])
    if not enabled:
        return dict(status='dry_run', writes=0, changes=len(plan['changes']), platform_validation='not_run')
    journal = real_path(journal)
    real_path(journal.parent, True)
    if os.path.lexists(journal):
        raise ValueError('Journal must be a new directory')
    targets = [root / c['path'] for c in plan['changes']]
    if any(journal == target or target in journal.parents or journal in target.parents for target in targets):
        raise ValueError('Journal overlaps a source target')
    journal.mkdir(mode=0o700, exist_ok=False)
    completed = []
    report = dict(status='in_progress', journal=str(journal), completed=[], rolled_back=[], rollback_conflicts=[],
                  platform_validation='not_run', atomicity='per_file_only', temporary_files=[])
    backups = {}
    try:
        (journal / 'backups').mkdir()
        for index, change in enumerate(plan['changes']):
            if change['before_b64'] is not None:
                backup = journal / 'backups' / (str(index).zfill(4) + '.bin')
                backup.write_bytes(payload(change['before_b64']))
                backups[change['path']] = str(backup)
        report['backups'] = backups
        write_report(journal, report)
        for change in plan['changes']:
            try:
                commit_file(root, change, payload(change['after_b64']), change['before_sha256'])
            except OSError as write_error:
                if getattr(write_error, 'source_edit_published', False):
                    completed.append(change)
                    report['completed'].append(change['path'])
                temporary = getattr(write_error, 'source_edit_temporary', None)
                if temporary:
                    report['temporary_files'].append(temporary)
                raise
            completed.append(change)
            report['completed'].append(change['path'])
            write_report(journal, report)
        report['status'] = 'applied'
        write_report(journal, report)
    except (OSError, ValueError) as error:
        report['status'] = 'failed'
        report['error'] = str(error)
        for change in reversed(completed):
            try:
                path = precondition(root, change, change['after_sha256'])
                if change['before_b64'] is None:
                    # Repeat immediately before deleting our own newly-created file.
                    precondition(root, change, change['after_sha256'])
                    path.unlink()
                else:
                    commit_file(root, change, payload(change['before_b64']), change['after_sha256'])
                report['rolled_back'].append(change['path'])
            except (OSError, ValueError) as conflict:
                temporary = getattr(conflict, 'source_edit_temporary', None)
                if temporary:
                    report['temporary_files'].append(temporary)
                if getattr(conflict, 'source_edit_published', False):
                    report['rolled_back'].append(change['path'])
                    continue
                report['rollback_conflicts'].append(dict(path=change['path'], error=str(conflict),
                                                        backup=backups.get(change['path']),
                                                        after_sha256=change['after_sha256']))
        try:
            write_report(journal, report)
        except OSError as report_error:
            report['report_write_error'] = str(report_error)
    return report


def no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key: ' + key)
        result[key] = value
    return result


def load_json(path, maximum):
    path = real_path(path)
    return json.loads(read_bytes(path, maximum).decode('utf-8-sig'), object_pairs_hook=no_duplicate_keys,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON number')))


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def main(argv=None):
    try:
        parser = Parser(description=__doc__)
        commands = parser.add_subparsers(dest='command', required=True)
        plan_cmd = commands.add_parser('plan')
        plan_cmd.add_argument('--project', required=True)
        plan_cmd.add_argument('--request', required=True)
        plan_cmd.add_argument('--out', required=True)
        show_cmd = commands.add_parser('show')
        show_cmd.add_argument('--plan', required=True)
        apply_cmd = commands.add_parser('apply')
        apply_cmd.add_argument('--project', required=True)
        apply_cmd.add_argument('--plan', required=True)
        apply_cmd.add_argument('--journal', required=True)
        apply_cmd.add_argument('--apply', action='store_true')
        args = parser.parse_args(argv)
        if args.command == 'plan':
            result = make_plan(args.project, load_json(args.request, MAX_REQUEST_BYTES))
            out = real_path(args.out)
            real_path(out.parent, True)
            if any(out == Path(result['project_root']) / change['path'] for change in result['changes']):
                raise ValueError('Plan output cannot overwrite a source target')
            # Exclusive output avoids clobbering unrelated files or an earlier review.
            with out.open('xb') as stream:
                stream.write(canonical(result) + b'\n')
            result = dict(status='planned', plan=str(out), changes=len(result['changes']), platform_validation='not_run')
        elif args.command == 'show':
            result = validate_plan(load_json(args.plan, MAX_PLAN_BYTES))
        else:
            result = apply_plan(args.project, load_json(args.plan, MAX_PLAN_BYTES), args.journal, args.apply)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result.get('status') == 'failed' else 0
    except (ValueError, OSError, ET.ParseError, expat.ExpatError, KeyError, TypeError, RecursionError) as error:
        print(json.dumps(dict(status='error', error=str(error)), ensure_ascii=False))
        return 2


if __name__ == '__main__':
    sys.exit(main())
