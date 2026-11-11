#!/usr/bin/env python3
"""Plan or execute a trusted 1C build/test profile. Python 3.10+, stdlib only.

Commands run only with --apply. This orchestrates installed tools; it is not a
1C compiler, security sandbox, deployment authorization or transaction manager.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

from dump_tool import is_link, local, parse_xml, read_limited

TOKEN = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}')
# ${env:NAME} в command подставляется из окружения только при запуске и не попадает в план и отчёт.
ENV_TOKEN = re.compile(r'\$\{env:([A-Za-z_][A-Za-z0-9_]*)\}')
KINDS = {'check', 'build', 'deploy', 'test', 'run'}
RESERVED = {'pipeline-report.json', 'pipeline-junit.xml'}


def expand(value, variables):
    if isinstance(value, str):
        def replace(match):
            name = match.group(1)
            if name not in variables:
                raise ValueError('Unknown variable: ' + name)
            return variables[name]
        return TOKEN.sub(replace, value)
    if isinstance(value, list):
        return [expand(v, variables) for v in value]
    if isinstance(value, dict):
        return {k: expand(v, variables) for k, v in value.items()}
    return value


def keys(obj, allowed, label):
    if not isinstance(obj, dict) or set(obj) - set(allowed):
        raise ValueError('Invalid or unknown fields: ' + label)


def confined(path, run):
    p = Path(path).absolute()
    if not p.is_relative_to(run) or p == run:
        raise ValueError('Output must be below run directory: ' + str(p))
    if not p.resolve().is_relative_to(run.resolve()):
        raise ValueError('Output resolves outside run directory: ' + str(p))
    for item in [p, *p.parents]:
        if item.exists() or item.is_symlink():
            if is_link(item):
                raise ValueError('Output path contains link/reparse point: ' + str(item))
        if item == run:
            break
    return p


def load_profile(path, run, project):
    data = json.loads(read_limited(path, 4).decode('utf-8-sig'))
    keys(data, {'schema', 'name', 'variables', 'prepare', 'steps', 'env_unset_prefixes'}, 'profile')
    if data.get('schema') != 1:
        raise ValueError('Expected schema 1')
    builtin = {'project': str(project), 'run': str(run),
               'skill': str(Path(__file__).resolve().parents[1]), 'python': sys.executable}
    raw = data.get('variables', {})
    if not isinstance(raw, dict) or set(raw) & set(builtin):
        raise ValueError('Variables must be an object and cannot override builtins')
    for k, v in raw.items():
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', k) or not isinstance(v, str) or not v:
            raise ValueError('Variable names/values must be nonempty strings')
    pending = dict(raw)
    while pending:
        done = []
        for k, v in pending.items():
            if all(n in builtin for n in TOKEN.findall(v)):
                builtin[k] = expand(v, builtin); done.append(k)
        if not done:
            raise ValueError('Unresolved or cyclic variables: ' + ', '.join(pending))
        for k in done:
            del pending[k]
    data = expand(data, builtin)
    if not isinstance(data.get('steps'), list) or not data['steps']:
        raise ValueError('At least one step is required')
    if not isinstance(data.get('prepare', []), list):
        raise ValueError('prepare must be an array')
    prefixes = data.get('env_unset_prefixes', [])
    if not isinstance(prefixes, list) or any(not isinstance(p, str) or not p for p in prefixes):
        raise ValueError('env_unset_prefixes must be an array of nonempty strings')
    ids, destinations = set(), set()
    for item in data.get('prepare', []):
        keys(item, {'path', 'json'}, 'prepare item')
        if 'json' not in item or not isinstance(item.get('path'), str):
            raise ValueError('prepare item requires path and json')
        p = confined(item['path'], run)
        if p.name in RESERVED or p in destinations:
            raise ValueError('Duplicate or reserved destination')
        destinations.add(p)
    for step in data['steps']:
        keys(step, {'id', 'kind', 'command', 'cwd', 'timeout', 'inputs', 'outputs', 'stdout_json_status', 'stdout_json_equals'}, 'step')
        sid = step.get('id', '')
        if not isinstance(sid, str) or not re.fullmatch(r'[a-zA-Z0-9_-]+', sid) or sid in ids:
            raise ValueError('Step IDs must be unique simple names')
        ids.add(sid)
        if step.get('kind') not in KINDS:
            raise ValueError('Invalid step kind: ' + sid)
        command = step.get('command')
        if not isinstance(command, list) or not command or any(not isinstance(v, str) or '\0' in v for v in command) or not command[0]:
            raise ValueError('command must be a nonempty argv array')
        if Path(command[0]).suffix.lower() in {'.bat', '.cmd', '.ps1', '.sh'}:
            raise ValueError('Call an explicit interpreter executable, not a shell wrapper')
        timeout = step.setdefault('timeout', 600)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('timeout must be positive and finite')
        step.setdefault('cwd', str(project))
        if isinstance(step['cwd'], str) and not Path(step['cwd']).is_absolute():
            step['cwd'] = str((project / step['cwd']).absolute())
        if not isinstance(step['cwd'], str) or (not Path(step['cwd']).is_dir() and Path(step['cwd']) != run):
            raise ValueError('cwd must be an existing directory')
        for field in ['inputs', 'stdout_json_status']:
            if field in step and (not isinstance(step[field], list) or not step[field] or any(not isinstance(v, str) or not v for v in step[field])):
                raise ValueError(field + ' must be a nonempty string array')
        if 'inputs' in step:
            step['inputs'] = [str(Path(p) if Path(p).is_absolute() else Path(step['cwd']) / p) for p in step['inputs']]
        if 'stdout_json_equals' in step:
            expected = step['stdout_json_equals']
            if not isinstance(expected, dict) or not expected or any(not isinstance(k, str) or isinstance(v, (list, dict)) for k, v in expected.items()):
                raise ValueError('stdout_json_equals must contain scalar fields')
        outputs = step.setdefault('outputs', [])
        if not isinstance(outputs, list):
            raise ValueError('outputs must be an array')
        for output in outputs:
            keys(output, {'path', 'kind', 'min_tests', 'max_skipped', 'allow_empty', 'create_parent'}, 'output')
            if not isinstance(output.get('path'), str) or output.get('kind') not in {'file', 'junit', 'junit-dir', 'exitcode'}:
                raise ValueError('Output requires path and supported kind')
            if 'allow_empty' in output and not isinstance(output['allow_empty'], bool):
                raise ValueError('allow_empty must be a boolean')
            if 'create_parent' in output and not isinstance(output['create_parent'], bool):
                raise ValueError('create_parent must be a boolean')
            p = confined(output['path'], run)
            if p.name in RESERVED or p in destinations or p.name in {sid + '.stdout.log', sid + '.stderr.log'}:
                raise ValueError('Duplicate or reserved output path')
            destinations.add(p)
            for field, default in [('min_tests', 1 if step['kind'] == 'test' else 0), ('max_skipped', 0)]:
                value = output.setdefault(field, default)
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError(field + ' must be a nonnegative integer')
            if step['kind'] == 'test' and output['kind'] in {'junit', 'junit-dir'} and output['min_tests'] < 1:
                raise ValueError('Test steps require at least one executed test')
        if step['kind'] == 'test' and not any(o['kind'] in {'junit', 'junit-dir'} for o in outputs):
            raise ValueError('Test step needs JUnit evidence')
        if step['kind'] == 'build' and not any(o['kind'] == 'file' for o in outputs):
            raise ValueError('Build step needs a file artifact')
    protected = {run / name for name in RESERVED}
    protected.update(run / (sid + suffix) for sid in ids for suffix in ['.stdout.log', '.stderr.log'])
    if destinations & protected:
        raise ValueError('Output/prepare conflicts with a reserved pipeline log')
    paths = destinations | protected
    if any(p != q and p in q.parents for p in paths for q in paths):
        raise ValueError('Overlapping file/directory destinations are not supported')
    return data


def read_junit(path):
    root = parse_xml(read_limited(path, 32))
    if local(root.tag) not in {'testsuite', 'testsuites'}:
        raise ValueError('Expected JUnit testsuite/testsuites root')
    nodes = list(root.iter())
    cases = [n for n in nodes if local(n.tag) == 'testcase']
    failures = sum(local(n.tag) in {'failure', 'error'} for n in nodes)
    for node in nodes:
        if local(node.tag) in {'testsuite', 'testsuites'}:
            nested_cases = [n for n in node.iter() if local(n.tag) == 'testcase']
            if 'tests' in node.attrib and int(node.get('tests')) != len(nested_cases):
                raise ValueError('JUnit declared test count differs from testcase count')
            nested_skips = sum(any(local(c.tag) == 'skipped' for c in case) or case.get('status', '').lower() in {'skipped', 'disabled', 'notrun'} for case in nested_cases)
            if 'skipped' in node.attrib and int(node.get('skipped')) != nested_skips:
                raise ValueError('JUnit declared skip count differs from testcases')
            for key in ['errors', 'failures']:
                count = int(node.get(key, '0'))
                if count < 0:
                    raise ValueError('Negative JUnit count')
                failures = max(failures, count)
    skipped = sum(any(local(c.tag) == 'skipped' for c in case) or case.get('status', '').lower() in {'skipped', 'disabled', 'notrun'} for case in cases)
    if failures:
        raise ValueError('JUnit contains failures/errors: ' + str(failures))
    return {'tests': len(cases), 'executed': len(cases) - skipped, 'skipped': skipped}


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def evidence(output, run):
    path = confined(output['path'], run)
    kind = output['kind']
    result = {'path': str(path), 'kind': kind}
    if kind == 'junit-dir':
        if not path.is_dir():
            raise ValueError('Missing JUnit directory: ' + str(path))
        files = sorted(path.rglob('*.xml'))
        if not files:
            raise ValueError('No JUnit reports found')
    else:
        files = [path]
    total = {'tests': 0, 'executed': 0, 'skipped': 0}
    details = []
    for file in files:
        file = confined(str(file), run)
        if not file.is_file():
            raise ValueError('Missing artifact: ' + str(file))
        if not file.stat().st_size and not output.get('allow_empty', False):
            raise ValueError('Empty artifact: ' + str(file))
        if kind in {'junit', 'junit-dir'}:
            counts = read_junit(file)
            for k in total:
                total[k] += counts[k]
        elif kind == 'exitcode':
            raw = read_limited(file, 1)
            value = raw.decode('utf-16') if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else raw.decode('utf-8-sig')
            if value.strip() != '0':
                raise ValueError('Result file does not contain zero')
        details.append({'path': str(file), 'bytes': file.stat().st_size, 'sha256': sha256(file)})
    if kind in {'junit', 'junit-dir'}:
        if total['executed'] < output['min_tests']:
            raise ValueError('Too few executed tests: ' + str(total['executed']))
        if total['skipped'] > output['max_skipped']:
            raise ValueError('Too many skipped tests: ' + str(total['skipped']))
        result.update(total)
    result['files'] = details
    return result


def stop_process(process):
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=15)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=15)


def save_report(report, run):
    report_path = confined(str(run / 'pipeline-report.json'), run)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    suite = ET.Element('testsuite', name=report['name'], tests=str(len(report['steps'])),
                       failures=str(sum(s['status'] == 'failed' for s in report['steps'])),
                       skipped=str(sum(s['status'] in {'blocked', 'pending', 'running'} for s in report['steps'])))
    for step in report['steps']:
        case = ET.SubElement(suite, 'testcase', name=step['id'], classname='pipeline.' + step['kind'], time=str(step.get('seconds', 0)))
        if step['status'] == 'failed':
            ET.SubElement(case, 'failure', message=step.get('detail', 'failed'))
        elif step['status'] != 'passed':
            ET.SubElement(case, 'skipped', message=step['status'])
    confined(str(run / 'pipeline-junit.xml'), run).write_bytes(ET.tostring(suite, encoding='utf-8', xml_declaration=True))


def resolve_env(profile):
    """Secrets stay out of the profile: ${env:NAME} is read from the environment at execution."""
    missing = sorted({name for step in profile['steps'] for arg in step['command']
                      for name in ENV_TOKEN.findall(arg) if not os.environ.get(name)})
    if missing:
        raise ValueError('Environment variable is not set: ' + ', '.join(missing))
    return {step['id']: [ENV_TOKEN.sub(lambda m: os.environ[m.group(1)], arg) for arg in step['command']]
            for step in profile['steps']}


def execute(profile, run):
    # Preflight executable availability before any output or preparation writes.
    commands = resolve_env(profile)
    for step in profile['steps']:
        exe = step['command'][0]
        if not Path(exe).is_absolute() and ('/' in exe or '\\' in exe):
            exe = str((Path(step['cwd']) / exe).absolute())
            step['command'][0] = commands[step['id']][0] = exe
        if not shutil.which(exe):
            raise ValueError('Executable not found: ' + exe)
    run.mkdir(parents=True, exist_ok=False)
    prefixes = tuple(p.upper() for p in profile.get('env_unset_prefixes', []))
    environment = {k: v for k, v in os.environ.items() if not k.upper().startswith(prefixes)}
    for item in profile.get('prepare', []):
        path = confined(item['path'], run); path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8') as out:
            json.dump(item['json'], out, ensure_ascii=False, indent=2)
    report = {'schema': 1, 'name': profile.get('name', '1C pipeline'), 'status': 'running',
              'scope': 'Only the configured steps; see individual evidence.',
              'steps': [{'id': s['id'], 'kind': s['kind'], 'status': 'pending'} for s in profile['steps']]}
    failed = False
    for step, result in zip(profile['steps'], report['steps']):
        if failed:
            result['status'] = 'blocked'; continue
        started = time.monotonic(); process = None
        result['status'] = 'running'; save_report(report, run)
        try:
            for source in step.get('inputs', []):
                if not Path(source).exists():
                    raise ValueError('Missing input: ' + source)
            for output in step['outputs']:
                p = confined(output['path'], run)
                if p.exists():
                    raise ValueError('Artifact already exists before this step: ' + str(p))
                if output.get('create_parent', True):
                    p.parent.mkdir(parents=True, exist_ok=True)
                if output['kind'] == 'junit-dir':
                    p.mkdir()
            stdout = confined(str(run / (step['id'] + '.stdout.log')), run)
            stderr = confined(str(run / (step['id'] + '.stderr.log')), run)
            with stdout.open('xb') as out, stderr.open('xb') as err:
                process = subprocess.Popen(commands[step['id']], cwd=step['cwd'], stdout=out, stderr=err, env=environment,
                                           shell=False, start_new_session=os.name != 'nt',
                                           creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
                try:
                    code = process.wait(timeout=step['timeout'])
                except subprocess.TimeoutExpired:
                    stop_process(process)
                    raise ValueError('timeout after ' + str(step['timeout']) + ' seconds')
            result.update({'returncode': code, 'stdout': str(stdout), 'stderr': str(stderr)})
            if code != 0:
                raise ValueError('Process returned ' + str(code))
            if 'stdout_json_status' in step or 'stdout_json_equals' in step:
                data = json.loads(read_limited(stdout, 32).decode('utf-8-sig'))
                if not isinstance(data, dict):
                    raise ValueError('stdout JSON must be an object')
                if 'stdout_json_status' in step and data.get('status') not in step['stdout_json_status']:
                    raise ValueError('stdout JSON status is not accepted')
                for key, expected in step.get('stdout_json_equals', {}).items():
                    if key not in data or type(data[key]) is not type(expected) or data[key] != expected:
                        raise ValueError('stdout JSON field does not match: ' + key)
            result['evidence'] = [evidence(o, run) for o in step['outputs']]
            result['status'] = 'passed'
        except KeyboardInterrupt:
            if process is not None:
                stop_process(process)
            result.update(status='failed', detail='Interrupted; external changes are not rolled back')
            failed = True
        except (OSError, ValueError, OverflowError, ET.ParseError, subprocess.SubprocessError) as error:
            result.update(status='failed', detail=str(error)); failed = True
        result['seconds'] = round(time.monotonic() - started, 3)
        save_report(report, run)
    report['status'] = 'failed' if failed else 'passed'
    save_report(report, run)
    return report, 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--run-dir', required=True, help='New, nonexistent output directory')
    parser.add_argument('--project', help='Project root; default: directory containing config')
    parser.add_argument('--apply', action='store_true', help='Execute configured commands')
    args = parser.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    try:
        config = Path(args.config).absolute()
        run = Path(args.run_dir).absolute()
        project = Path(args.project).absolute() if args.project else config.parent
        if not project.is_dir() or run.exists() or run.is_symlink():
            raise ValueError('Project must exist; run directory must not exist')
        # Check paths inside run; a caller-selected parent may itself be a link.
        confined(str(run / 'probe'), run)
        profile = load_profile(config, run, project)
        if args.apply:
            result, code = execute(profile, run)
        else:
            result, code = {'status': 'planned', 'run_dir': str(run), 'profile': profile}, 0
    except (OSError, ValueError, OverflowError) as error:
        result, code = {'status': 'error', 'detail': str(error)}, 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == '__main__':
    sys.exit(main())
