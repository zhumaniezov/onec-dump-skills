"""Bridge tests use a local Git fixture; upstream PowerShell is not executed."""
import importlib.util
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.checkout = self.root / 'upstream'
        self.checkout.mkdir()
        spec = importlib.util.spec_from_file_location('generator_bridge', SCRIPTS / 'generator_bridge.py')
        self.bridge = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.bridge)
        self.git('init', '-q')
        self.script = self.checkout / 'generator.ps1'; self.script.write_bytes(b'# fixture\n')
        self.git('add', 'generator.ps1')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture')
        self.commit = self.git('rev-parse', 'HEAD')
        self.blob = self.git('hash-object', '--no-filters', 'generator.ps1')
        self.manifest = {'repository': 'fixture', 'commit': self.commit,
                         'scripts': {'cf-init': {'path': 'generator.ps1', 'blob': self.blob}}}

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.checkout), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def request(self, **extra):
        value = {'schema': 1, 'powershell': 'pwsh',
                 'parameters': {'Name': 'Пример', 'FormatVersion': '2.20', 'CompatibilityMode': 'Version8_3_27'}}
        value.update(extra); return value

    def test_verify_pinned_clean_local_checkout(self):
        p = self.bridge.verify_checkout(self.checkout, 'cf-init', self.manifest)
        self.assertEqual(p.resolve(), self.script.resolve())  # на Windows временный каталог бывает в коротком виде

    def test_changed_script_is_rejected(self):
        self.script.write_text('# changed')
        with self.assertRaises(ValueError): self.bridge.verify_checkout(self.checkout, 'cf-init', self.manifest)

    def test_wrong_commit_is_rejected(self):
        self.manifest['commit'] = '0' * 40
        with self.assertRaises(ValueError): self.bridge.verify_checkout(self.checkout, 'cf-init', self.manifest)

    def test_untracked_file_is_rejected(self):
        (self.checkout / 'extra.ps1').write_text('# new')
        with self.assertRaises(ValueError): self.bridge.verify_checkout(self.checkout, 'cf-init', self.manifest)

    def test_manifest_path_cannot_escape(self):
        self.manifest['scripts']['cf-init']['path'] = '../generator.ps1'
        with self.assertRaises(ValueError): self.bridge.verify_checkout(self.checkout, 'cf-init', self.manifest)

    def test_cf_requires_explicit_xml_and_compatibility(self):
        request = self.request(); del request['parameters']['FormatVersion']
        with self.assertRaises(ValueError): self.bridge.validate_request('cf-init', request, self.root)

    def test_cfe_requires_base_export(self):
        request = self.request(parameters={'Name': 'Расширение'})
        with self.assertRaises(ValueError): self.bridge.validate_request('cfe-init', request, self.root)

    def test_version_sensitive_payloads_require_native_context(self):
        for action in ['form-compile', 'mxl-compile']:
            with self.subTest(action=action):
                request = self.request(parameters={}, definition={'name': 'Payload'})
                with self.assertRaises(ValueError): self.bridge.validate_request(action, request, self.root)

    def test_form_payload_targets_new_file_in_copied_base(self):
        base = self.root / 'base'; base.mkdir()
        (base / 'Configuration.xml').write_text('<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses" version="2.20"><Configuration><Properties><Name>Base</Name></Properties></Configuration></MetaDataObject>')
        request = self.request(parameters={}, definition={'name': 'Form'}, base_source=str(base),
                               expected_artifact='NewPayload/Form.xml')
        clean = self.bridge.validate_request('form-compile', request, self.root)
        argv, artifact = self.bridge.command_for('form-compile', clean, self.script, self.root / 'output')
        self.assertEqual(argv[argv.index('-OutputPath')+1], str(self.root / 'output/generated/NewPayload/Form.xml'))
        self.assertEqual(artifact, self.root / 'output/generated/NewPayload/Form.xml')

    def test_path_parameter_injection_rejected(self):
        request = self.request(); request['parameters']['OutputDir'] = '/outside'
        with self.assertRaises(ValueError): self.bridge.validate_request('cf-init', request, self.root)

    def test_new_name_cannot_contain_path(self):
        request = self.request(); request['parameters']['Name'] = '../escape'
        with self.assertRaises(ValueError): self.bridge.validate_request('cf-init', request, self.root)

    def test_literal_pipeline_substitution_cannot_change_request(self):
        request = self.request(); request['parameters']['Synonym'] = '${run}'
        with self.assertRaises(ValueError): self.bridge.validate_request('cf-init', request, self.root)

    def test_profile_is_dry_and_accepted_by_pipeline(self):
        request = self.request()
        with patch.object(self.bridge, 'MANIFEST', self.manifest):
            profile = self.bridge.make_profile(self.checkout, 'cf-init', request, self.root)
        p = self.root / 'profile.json'; p.write_text(json.dumps(profile))
        import pipeline
        plan = pipeline.load_profile(p, self.root / 'run', self.root)
        self.assertFalse((self.root / 'run').exists())
        self.assertEqual(len(plan['steps']), 2)
        self.assertEqual(Path(plan['steps'][0]['outputs'][0]['path']), self.root / 'run/generator/generated/Configuration.xml')

    def test_copy_preserves_bytes_and_refuses_reuse(self):
        source = self.root / 'base'; source.mkdir()
        (source / 'Configuration.xml').write_bytes(b'\xef\xbb\xbf<root/>\r\n')
        target = self.root / 'copy'
        self.bridge.copy_source(source, target)
        self.assertEqual((target / 'Configuration.xml').read_bytes(), (source / 'Configuration.xml').read_bytes())
        with self.assertRaises(ValueError): self.bridge.copy_source(source, target)

    def test_copy_rejects_links_before_creating_destination(self):
        source = self.root / 'base'; source.mkdir()
        (source / 'linked').symlink_to(self.script)
        target = self.root / 'copy'
        with self.assertRaises(ValueError): self.bridge.copy_source(source, target)
        self.assertFalse(target.exists())

    def test_copy_dotdot_alias_cannot_put_output_inside_source(self):
        source = self.root / 'base'; source.mkdir()
        (source / 'keep.xml').write_bytes(b'<keep/>')
        alias = self.root / 'alias'; alias.mkdir()
        destination = alias / '..' / 'base' / 'nested'
        with self.assertRaises(ValueError): self.bridge.copy_source(source, destination)
        self.assertEqual(list(source.iterdir()), [source / 'keep.xml'])

    def test_erf_build_command_preserves_single_argument_and_switch(self):
        request = self.request(parameters={'Name': 'Отчет', 'Synonym': 'Отчет по продажам',
                                          'FormatVersion': '2.20', 'WithSKD': True})
        clean = self.bridge.validate_request('erf-init', request, self.root)
        argv, artifact = self.bridge.command_for('erf-init', clean, self.script, self.root / 'output')
        self.assertIn('Отчет по продажам', argv)
        self.assertIn('-WithSKD', argv)
        self.assertEqual(artifact.name, 'Отчет.xml')

    def test_generation_without_apply_writes_nothing(self):
        request = self.root / 'request.json'; request.write_text(json.dumps(self.request()))
        run = subprocess.run([sys.executable, str(SCRIPTS / 'generator_bridge.py'), 'generate',
            '--checkout', str(self.checkout), '--action', 'cf-init', '--request', str(request),
            '--output', str(self.root / 'generated')], capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertFalse((self.root / 'generated').exists())

    def test_invalid_base_xml_is_a_controlled_error(self):
        base = self.root / 'base'; base.mkdir()
        (base / 'Configuration.xml').write_text('<broken>')
        request = self.request(parameters={'Name': 'Расширение'}, base_source=str(base))
        with self.assertRaises(ValueError): self.bridge.validate_request('cfe-init', request, self.root)

    @unittest.skipIf(os.name == 'nt', 'POSIX fake executable; not a PowerShell test')
    def test_real_process_creates_fresh_artifact_and_logs(self):
        program = self.root / 'fake-powershell'
        program.write_text('#!' + sys.executable + '\nimport sys,pathlib\np=pathlib.Path(sys.argv[sys.argv.index("-OutputDir")+1])\n(p/"Configuration.xml").write_text("<fixture/>")\nprint("fixture process ran")\n')
        program.chmod(0o755)
        request = self.root / 'request.json'
        request.write_text(json.dumps(self.request(powershell=str(program))))
        out = self.root / 'generated'
        args = SimpleNamespace(apply=True, request=str(request), checkout=str(self.checkout),
                               action='cf-init', output=str(out))
        with patch.object(self.bridge, 'MANIFEST', self.manifest):
            data = self.bridge.generate(args)
            with self.assertRaises(ValueError): self.bridge.generate(args)
        self.assertEqual(data['platform_validation'], 'not_run')
        self.assertEqual((out / 'generated/Configuration.xml').read_text(), '<fixture/>')
        self.assertIn('fixture process ran', (out / 'generator.stdout.log').read_text())
        self.assertEqual(self.script.read_bytes(), b'# fixture\n')

    @unittest.skipIf(os.name == 'nt', 'POSIX fake executable; not a PowerShell test')
    def test_generated_profile_executes_through_actual_pipeline(self):
        program = self.root / 'fake-powershell'
        program.write_text('#!' + sys.executable + '\nimport sys,pathlib\np=pathlib.Path(sys.argv[sys.argv.index("-OutputDir")+1])\n(p/"Configuration.xml").write_text("<fixture/>")\n')
        program.chmod(0o755)
        kit = self.root / 'kit'; (kit / 'scripts').mkdir(parents=True)
        (kit / 'assets/toolchains').mkdir(parents=True)
        for name in ['generator_bridge.py', 'pipeline.py', 'dump_tool.py']:
            shutil.copyfile(SCRIPTS / name, kit / 'scripts' / name)
        (kit / 'assets/toolchains/shirokov.json').write_text(json.dumps(self.manifest))
        with patch.object(self.bridge, 'MANIFEST', self.manifest):
            profile = self.bridge.make_profile(self.checkout, 'cf-init', self.request(powershell=str(program)), self.root)
        p = self.root / 'profile.json'; p.write_text(json.dumps(profile))
        run = subprocess.run([sys.executable, str(kit / 'scripts/pipeline.py'), '--config', str(p),
             '--project', str(self.root), '--run-dir', str(self.root / 'run'), '--apply'], capture_output=True, text=True)
        report = json.loads((self.root / 'run/pipeline-report.json').read_text())
        self.assertEqual(run.returncode, 0, json.dumps(report))
        self.assertEqual([s['status'] for s in report['steps']], ['passed', 'passed'])
        self.assertEqual((self.root / 'run/generator/generated/Configuration.xml').read_text(), '<fixture/>')

    @unittest.skipIf(os.name == 'nt', 'POSIX fake executable; not a PowerShell test')
    def test_generate_dotdot_alias_cannot_modify_base(self):
        source = self.root / 'base'; source.mkdir()
        xml = '<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses"><Configuration><Properties><Name>Base</Name></Properties></Configuration></MetaDataObject>'
        (source / 'Configuration.xml').write_text(xml)
        alias = self.root / 'alias'; alias.mkdir()
        self.manifest['scripts']['cfe-init'] = self.manifest['scripts']['cf-init']
        program = self.root / 'fake-powershell'
        program.write_text('#!' + sys.executable + '\nimport sys,pathlib\np=pathlib.Path(sys.argv[sys.argv.index("-OutputDir")+1])\n(p/"Configuration.xml").write_text("<fixture/>")\n')
        program.chmod(0o755)
        request = self.root / 'request.json'
        request.write_text(json.dumps(self.request(powershell=str(program), parameters={'Name': 'Ext'}, base_source=str(source))))
        args = SimpleNamespace(apply=True, request=str(request), checkout=str(self.checkout),
                  action='cfe-init', output=str(alias / '..' / 'base' / 'nested'))
        with patch.object(self.bridge, 'MANIFEST', self.manifest):
            with self.assertRaises(ValueError): self.bridge.generate(args)
        self.assertEqual(list(source.iterdir()), [source / 'Configuration.xml'])
        self.assertEqual((source / 'Configuration.xml').read_text(), xml)


if __name__ == '__main__': unittest.main()
