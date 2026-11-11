#!/usr/bin/env python3
"""Optional bridge to a reviewed, pinned cc-1c-skills checkout, not bundled code.

Planning does not run generators. Execution requires --apply and a fresh output
directory. External scripts are trusted tools, not sandboxed by this wrapper.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

from dump_tool import is_link, parse_xml, read_limited

MANIFEST = json.loads((Path(__file__).resolve().parents[1] / 'assets/toolchains/shirokov.json').read_text(encoding='utf-8'))
COMMON = {'Name', 'Synonym'}
PARAMETERS = {
    'cf-init': COMMON | {'Version', 'Vendor', 'CompatibilityMode', 'FormatVersion'},
    'cfe-init': COMMON | {'Version', 'Vendor', 'CompatibilityMode', 'NamePrefix', 'Purpose', 'NoRole'},
    'epf-init': COMMON | {'FormatVersion'}, 'erf-init': COMMON | {'FormatVersion', 'WithSKD'},
    'meta-compile': set(), 'role-compile': set(), 'form-compile': set(),
    'skd-compile': set(), 'mxl-compile': set(),
}
SWITCHES = {'NoRole', 'WithSKD'}
STAGED_ACTIONS = {'meta-compile', 'role-compile', 'form-compile', 'mxl-compile'}


def checked_xml(path):
    try:
        return parse_xml(read_limited(path, 32))
    except ET.ParseError as error:
        raise ValueError('Malformed XML: ' + str(error)) from error


def no_links(path):
    p = Path(path).absolute()
    for item in [p, *p.parents]:
        if (item.exists() or item.is_symlink()) and is_link(item):
            raise ValueError('Path contains a link/reparse point')
    # Check the lexical ancestors first (a link followed by '..' must not be
    # hidden by resolution), then use canonical paths for containment and I/O.
    return p.resolve()


def relative(value):
    if not isinstance(value, str) or not value or '\\' in value or ':' in value or '\0' in value:
        raise ValueError('Expected a relative slash-separated path')
    p = Path(value)
    if p.is_absolute() or PureWindowsPath(value).is_absolute() or any(x in {'.', '..', '.git'} for x in value.split('/')):
        raise ValueError('Path escapes its root or targets Git data')
    return p


def git(checkout, *args):
    result = subprocess.run(['git', '-C', str(checkout), *args], capture_output=True,
                            text=True, encoding='utf-8', errors='replace', timeout=30)
    if result.returncode: raise ValueError('Git inspection failed; use an existing local checkout')
    return result.stdout.strip()


def verify_checkout(checkout, action, manifest=None):
    manifest = MANIFEST if manifest is None else manifest
    checkout = no_links(checkout)
    if not checkout.is_dir(): raise ValueError('Checkout is missing')
    descriptor = manifest['scripts'][action]
    script = no_links(checkout / relative(descriptor['path']))
    if Path(git(checkout, 'rev-parse', '--show-toplevel')).resolve() != checkout.resolve():
        raise ValueError('Specify the repository root')
    if git(checkout, 'rev-parse', 'HEAD') != manifest['commit']:
        raise ValueError('Checkout must match the reviewed commit in the toolchain manifest')
    if git(checkout, 'status', '--porcelain', '--untracked-files=all', '--ignored'):
        raise ValueError('Generator checkout must be clean, including ignored/untracked files')
    raw = read_limited(script, 32)
    blob = hashlib.sha1(b'blob ' + str(len(raw)).encode('ascii') + b'\0' + raw).hexdigest()
    if blob != descriptor['blob']:
        raise ValueError('Generator bytes differ from the reviewed blob; disable Git EOL conversion for this checkout')
    return script


def reject_tokens(value):
    if isinstance(value, str) and ('${' in value or '\0' in value):
        raise ValueError('Request contains NUL or pipeline interpolation syntax')
    if isinstance(value, dict):
        for k, v in value.items(): reject_tokens(k); reject_tokens(v)
    if isinstance(value, list):
        for v in value: reject_tokens(v)


def validate_request(action, request, base):
    if action not in PARAMETERS: raise ValueError('Unknown action')
    if not isinstance(request, dict) or set(request) - {'schema', 'powershell', 'parameters', 'definition', 'base_source', 'expected_artifact'}:
        raise ValueError('Invalid request fields')
    if type(request.get('schema')) is not int or request['schema'] != 1:
        raise ValueError('Request schema must be integer 1')
    reject_tokens(request)
    request = json.loads(json.dumps(request))
    if not isinstance(request.get('powershell'), str) or not request['powershell']:
        raise ValueError('Select an explicit PowerShell executable')
    if Path(request['powershell']).suffix.lower() in {'.cmd', '.bat', '.ps1', '.sh'}:
        raise ValueError('PowerShell must be an executable, not a shell wrapper')
    parameters = request.setdefault('parameters', {})
    if not isinstance(parameters, dict) or set(parameters) - PARAMETERS[action]:
        raise ValueError('Unexpected generator parameter; output paths are controlled by the bridge')
    for k, v in parameters.items():
        if k in SWITCHES:
            if not isinstance(v, bool): raise ValueError('Switch values must be booleans')
        elif not isinstance(v, str) or not v:
            raise ValueError('Generator values must be nonempty strings')
    if action.endswith('-init'):
        if not re.fullmatch(r'[^\W\d]\w*', parameters.get('Name', '')):
            raise ValueError('Name must be a metadata identifier')
        if action != 'cfe-init' and not re.fullmatch(r'\d+\.\d+', parameters.get('FormatVersion', '')):
            raise ValueError('Set FormatVersion from an export made by the target platform')
        if action == 'cf-init' and 'CompatibilityMode' not in parameters:
            raise ValueError('Set CompatibilityMode explicitly')
        if 'definition' in request or 'expected_artifact' in request:
            raise ValueError('Initialization does not accept definition/expected_artifact')
    else:
        if not isinstance(request.get('definition'), (dict, list)) or not request['definition']:
            raise ValueError('Supply the upstream JSON DSL definition')
    needs_base = action == 'cfe-init' or action in STAGED_ACTIONS
    if needs_base:
        source = request.get('base_source')
        if not isinstance(source, str) or not source: raise ValueError('This action requires a base export')
        source = Path(source)
        if not source.is_absolute(): source = Path(base) / source
        source = no_links(source)
        root = checked_xml(source / 'Configuration.xml')
        q = '{http://v8.1c.ru/8.3/MDClasses}'
        if root.tag != q + 'MetaDataObject' or root.find(q + 'Configuration') is None:
            raise ValueError('base_source must contain a Designer Configuration.xml')
        request['base_source'] = str(source)
    elif 'base_source' in request: raise ValueError('Unexpected base_source for this action')
    if action in STAGED_ACTIONS:
        artifact = relative(request.get('expected_artifact'))
        if artifact.suffix.lower() != '.xml': raise ValueError('Expected artifact must be XML')
        if (Path(request['base_source']) / artifact).exists():
            raise ValueError('Compile creates a new object; expected artifact must not exist in the base')
    elif 'expected_artifact' in request: raise ValueError('Unexpected expected_artifact')
    return request


def command_for(action, request, script, output):
    generated = Path(output) / 'generated'
    argv = [request['powershell'], '-NoProfile', '-NonInteractive', '-File', str(script)]
    for key, value in request.get('parameters', {}).items():
        if key in SWITCHES:
            if value: argv.append('-' + key)
        else: argv.extend(['-' + key, value])
    if action.endswith('-init'):
        argv += ['-SrcDir' if action in {'epf-init', 'erf-init'} else '-OutputDir', str(generated)]
        if action == 'cfe-init': argv += ['-ConfigPath', str(Path(output) / 'base' / 'Configuration.xml')]
        artifact = generated / (request['parameters']['Name'] + '.xml' if action in {'epf-init', 'erf-init'} else 'Configuration.xml')
    else:
        argv += ['-DefinitionFile' if action == 'skd-compile' else '-JsonPath', str(Path(output) / 'definition.json')]
        if action in {'meta-compile', 'role-compile'}:
            argv += ['-OutputDir', str(generated)]; artifact = generated / request['expected_artifact']
        else:
            artifact = generated / (request['expected_artifact'] if action in STAGED_ACTIONS else 'Template.xml')
            argv += ['-OutputPath', str(artifact)]
    return argv, artifact


def copy_source(source, destination):
    source = no_links(source); destination = no_links(destination)
    if not source.is_dir() or destination.exists(): raise ValueError('Copy requires a real source and absent destination')
    if destination.is_relative_to(source): raise ValueError('Destination must be outside source')
    files = []; directories = []
    def failed(error): raise error
    for parent, dirs, names in os.walk(source, followlinks=False, onerror=failed):
        for name in dirs + names: no_links(Path(parent) / name)
        dirs[:] = [n for n in dirs if n != '.git']
        directories.extend(Path(parent) / name for name in dirs)
        files.extend(Path(parent) / name for name in names if name != '.git')
    destination.mkdir()
    for folder in directories: (destination / folder.relative_to(source)).mkdir()
    for file in files:
        no_links(file)
        if not file.is_file(): raise ValueError('Special files are not supported')
        target = destination / file.relative_to(source)
        with file.open('rb') as src, target.open('xb') as dst: shutil.copyfileobj(src, dst)


def make_profile(checkout, action, request, base):
    clean = validate_request(action, request, base)
    verify_checkout(checkout, action)
    _, artifact = command_for(action, clean, Path('unused'), Path('${run}/generator'))
    return {'schema': 1, 'name': 'generate-' + action,
            'prepare': [{'path': '${run}/generator-request.json', 'json': clean}],
            'steps': [
                {'id': 'generate', 'kind': 'build', 'cwd': '${run}', 'timeout': 600,
                 'command': ['${python}', '${skill}/scripts/generator_bridge.py', 'generate',
                             '--checkout', str(Path(checkout).absolute()), '--action', action,
                             '--request', '${run}/generator-request.json', '--output', '${run}/generator', '--apply'],
                 'outputs': [{'path': str(artifact).replace('\\', '/'), 'kind': 'file', 'create_parent': False}]},
                {'id': 'audit-generated', 'kind': 'check', 'cwd': '${run}', 'timeout': 120,
                 'command': ['${python}', '${skill}/scripts/dump_tool.py', 'audit', '${run}/generator/generated'],
                 'stdout_json_status': ['ok']} ]}


def generate(args):
    if not args.apply: raise ValueError('Generation requires --apply; use plan to review first')
    request_path = no_links(args.request)
    request = validate_request(args.action, json.loads(read_limited(request_path, 4).decode('utf-8-sig')), request_path.parent)
    script = verify_checkout(args.checkout, args.action)
    output = no_links(args.output)
    if output.exists() or not output.parent.is_dir(): raise ValueError('Output must be a fresh directory under an existing parent')
    if output.is_relative_to(Path(args.checkout).resolve()): raise ValueError('Output cannot be inside the generator checkout')
    if 'base_source' in request and output.is_relative_to(Path(request['base_source'])):
        raise ValueError('Output cannot be inside source')
    output.mkdir()
    generated = output / 'generated'
    if args.action in STAGED_ACTIONS: copy_source(Path(request['base_source']), generated)
    else: generated.mkdir()
    if args.action == 'cfe-init': copy_source(Path(request['base_source']), output / 'base')
    if 'definition' in request:
        (output / 'definition.json').write_text(json.dumps(request['definition'], ensure_ascii=False, indent=2), encoding='utf-8')
    command, artifact = command_for(args.action, request, script, output)
    # One last revision/byte check just before invoking the trusted external program.
    verify_checkout(args.checkout, args.action)
    with (output / 'generator.stdout.log').open('wb') as stdout, (output / 'generator.stderr.log').open('wb') as stderr:
        process = subprocess.run(command, cwd=output, stdout=stdout, stderr=stderr, timeout=540)
    if process.returncode: raise ValueError('Generator failed; inspect logs in the output directory')
    no_links(artifact)
    if not artifact.is_file() or artifact.stat().st_size == 0: raise ValueError('Generator did not produce the expected artifact')
    checked_xml(artifact)
    return {'status': 'ok', 'artifact': str(artifact), 'upstream_commit': MANIFEST['commit'],
            'platform_validation': 'not_run', 'source_modified_by_bridge': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    sub.add_parser('list')
    for mode in ('plan', 'generate'):
        command = sub.add_parser(mode)
        command.add_argument('--checkout', required=True)
        command.add_argument('--action', choices=PARAMETERS, required=True)
        command.add_argument('--request', required=True)
        if mode == 'plan': command.add_argument('--out', required=True)
        else:
            command.add_argument('--output', required=True)
            command.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        if args.mode == 'list': data = MANIFEST
        elif args.mode == 'generate': data = generate(args)
        else:
            path = no_links(args.request)
            request = json.loads(read_limited(path, 4).decode('utf-8-sig'))
            profile = make_profile(args.checkout, args.action, request, path.parent)
            out = no_links(args.out)
            with out.open('x', encoding='utf-8', newline='\n') as stream:
                json.dump(profile, stream, ensure_ascii=False, indent=2); stream.write('\n')
            data = {'status': 'planned', 'profile': str(out), 'programs_executed': ['git inspection only'],
                    'platform_validation': 'not_run'}
        code = 0
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        data, code = {'status': 'error', 'error': str(error), 'platform_validation': 'not_run'}, 2
    print(json.dumps(data, ensure_ascii=False, indent=2)); return code


if __name__ == '__main__': sys.exit(main())
