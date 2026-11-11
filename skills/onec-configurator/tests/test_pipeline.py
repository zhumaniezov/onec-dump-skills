"""CLI contract tests: real child processes, no 1C installed or emulated."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).parents[1] / 'scripts' / 'pipeline.py'
OK_XML = '<testsuite tests="1"><testcase name="business-rule"/></testsuite>'


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / 'run'
        self.env = dict(os.environ)

    def step(self, code='', **extra):
        return {'id': 'first', 'kind': 'check',
                'command': [sys.executable, '-c', code], **extra}

    def invoke(self, steps, apply=True, **extra):
        profile = self.root / 'pipeline.json'
        profile.write_text(json.dumps({'schema': 1, 'steps': steps, **extra}), encoding='utf-8')
        cmd = [sys.executable, str(SCRIPT), '--config', str(profile), '--run-dir', str(self.run)]
        if apply:
            cmd += ['--apply']
        return subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', timeout=15, env=self.env)

    def report(self):
        return json.loads((self.run / 'pipeline-report.json').read_text(encoding='utf-8'))

    def writer(self, content, path='${run}/results.xml', **extra):
        code = 'from pathlib import Path; import sys; Path(sys.argv[1]).write_text(sys.argv[2],encoding="utf-8")'
        return self.step(code, command=[sys.executable, '-c', code, path, content], **extra)

    def test_plan_does_not_run_or_create_files(self):
        r = self.invoke([self.writer('payload')], apply=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)['status'], 'planned')
        self.assertFalse(self.run.exists())

    def test_build_and_real_junit_evidence_pass(self):
        build = self.writer('binary-placeholder', '${run}/app.cf', kind='build',
                            outputs=[{'path': '${run}/app.cf', 'kind': 'file'}])
        test = self.writer(OK_XML, id='unit', kind='test',
                           outputs=[{'path': '${run}/results.xml', 'kind': 'junit'}])
        r = self.invoke([build, test])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.report()['status'], 'passed')
        self.assertEqual(self.report()['steps'][1]['evidence'][0]['tests'], 1)
        self.assertTrue((self.run / 'pipeline-junit.xml').exists())

    def test_nonzero_stops_next_step(self):
        r = self.invoke([self.step('raise SystemExit(7)'), self.writer('bad', id='second')])
        self.assertEqual(r.returncode, 1)
        self.assertEqual([x['status'] for x in self.report()['steps']], ['failed', 'blocked'])
        self.assertFalse((self.run / 'results.xml').exists())

    def test_missing_test_report_fails_even_with_exit_zero(self):
        r = self.invoke([self.step(kind='test', outputs=[{'path': '${run}/missing.xml', 'kind': 'junit'}])])
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.report()['status'], 'failed')

    def test_failure_in_junit_fails_even_with_exit_zero(self):
        r = self.invoke([self.writer('<testsuite><testcase><failure/></testcase></testsuite>', kind='test',
                                    outputs=[{'path': '${run}/results.xml', 'kind': 'junit'}])])
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.report()['status'], 'failed')

    def test_zero_tests_and_all_skipped_cannot_pass(self):
        for xml in ['<testsuite tests="0"/>', '<testsuite><testcase><skipped/></testcase></testsuite>']:
            with self.subTest(xml=xml):
                self.run = self.root / ('run-' + str(len(xml)))
                r = self.invoke([self.writer(xml, kind='test', outputs=[{'path': '${run}/results.xml', 'kind': 'junit'}])])
                self.assertEqual(r.returncode, 1)

    def test_suite_level_error_cannot_be_hidden(self):
        r = self.invoke([self.writer('<testsuite errors="1"><testcase/></testsuite>', kind='test',
                                    outputs=[{'path': '${run}/results.xml', 'kind': 'junit'}])])
        self.assertEqual(r.returncode, 1)

    def test_previous_stage_report_is_not_reused(self):
        r = self.invoke([self.writer(OK_XML), self.step(id='second', kind='test',
                              outputs=[{'path': '${run}/results.xml', 'kind': 'junit'}])])
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.report()['steps'][1]['status'], 'failed')

    def test_outputs_cannot_escape_run_directory(self):
        outside = self.root / 'outside.txt'
        r = self.invoke([self.writer('bad', str(outside), outputs=[{'path': str(outside), 'kind': 'file'}])])
        self.assertEqual(r.returncode, 2)
        self.assertFalse(outside.exists())
        self.assertFalse(self.run.exists())

    def test_existing_run_directory_is_not_erased(self):
        self.run.mkdir()
        keep = self.run / 'keep.txt'; keep.write_text('keep')
        r = self.invoke([self.step()])
        self.assertEqual(r.returncode, 2)
        self.assertEqual(keep.read_text(), 'keep')

    def test_unresolved_variable_is_rejected(self):
        r = self.invoke([self.step(command=['${unknown}/app'])])
        self.assertEqual(r.returncode, 2)
        self.assertFalse(self.run.exists())

    def test_duplicate_ids_empty_steps_and_test_without_evidence_are_invalid(self):
        for steps in [[], [self.step(), self.step()], [self.step(kind='test')]]:
            with self.subTest(steps=steps):
                r = self.invoke(steps)
                self.assertEqual(r.returncode, 2)

    def test_timeout_is_failed_and_later_stage_blocked(self):
        r = self.invoke([self.step('import time; time.sleep(10)', timeout=0.15), self.step(id='next')])
        self.assertEqual(r.returncode, 1)
        self.assertIn('timeout', self.report()['steps'][0]['detail'].lower())
        self.assertEqual(self.report()['steps'][1]['status'], 'blocked')

    def test_unicode_and_shell_characters_are_literal_argv(self):
        value = 'Тест $(echo unsafe) ; & spaces'
        r = self.invoke([self.writer(value, outputs=[{'path': '${run}/results.xml', 'kind': 'file'}])])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.run / 'results.xml').read_text(), value)

    def test_prepared_json_is_expanded_before_command(self):
        code = 'import json,sys; from pathlib import Path; v=json.loads(Path(sys.argv[1]).read_text()); assert v["report"] == sys.argv[2]'
        r = self.invoke([self.step(command=[sys.executable, '-c', code, '${run}/test-config.json', '${run}/unit.xml'])],
                        prepare=[{'path': '${run}/test-config.json', 'json': {'report': '${run}/unit.xml'}}])
        self.assertEqual(r.returncode, 0, r.stderr)

    @unittest.skipIf(sys.platform == 'win32', 'Symlink permission is installation dependent')
    def test_symlink_output_cannot_be_evidence(self):
        target = self.root / 'secret'; target.write_text('outside')
        code = 'from pathlib import Path; import sys; Path(sys.argv[1]).symlink_to(sys.argv[2])'
        r = self.invoke([self.step(command=[sys.executable, '-c', code, '${run}/result', str(target)],
                                  outputs=[{'path': '${run}/result', 'kind': 'file'}])])
        self.assertEqual(r.returncode, 1)

    def test_junit_dtd_is_rejected(self):
        xml = '<!DOCTYPE x [<!ENTITY t "hello">]><testsuite><testcase name="&t;"/></testsuite>'
        r = self.invoke([self.writer(xml, kind='test', outputs=[{'path': '${run}/results.xml', 'kind': 'junit'}])])
        self.assertEqual(r.returncode, 1)

    def test_partial_json_check_is_not_success(self):
        r = self.invoke([self.step('print(\'{"status":"partial"}\')', stdout_json_status=['ok'])])
        self.assertEqual(r.returncode, 1)

    def test_junit_directory_aggregates_distinct_reports(self):
        code = 'from pathlib import Path; import sys; d=Path(sys.argv[1]); (d/"a.xml").write_text(sys.argv[2]); (d/"b.xml").write_text(sys.argv[2])'
        r = self.invoke([self.step(kind='test', command=[sys.executable, '-c', code, '${run}/reports', OK_XML],
                          outputs=[{'path': '${run}/reports', 'kind': 'junit-dir', 'min_tests': 2}])])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.report()['steps'][0]['evidence'][0]['tests'], 2)

    def test_run_directory_can_isolate_tool_settings(self):
        code = 'from pathlib import Path; import sys; assert str(Path.cwd()) == sys.argv[1]'
        r = self.invoke([self.step(command=[sys.executable, '-c', code, '${run}'], cwd='${run}')])
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_framework_environment_defaults_can_be_removed(self):
        self.env['VRUNNER_POLLUTION'] = 'wrong-database'
        r = self.invoke([self.step('import os; assert "VRUNNER_POLLUTION" not in os.environ')],
                        env_unset_prefixes=['VRUNNER_'])
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_incomplete_junit_counts_are_rejected(self):
        r = self.invoke([self.writer('<testsuite tests="5"><testcase/></testsuite>', kind='test',
                                    outputs=[{'path': '${run}/results.xml', 'kind': 'junit'}])])
        self.assertEqual(r.returncode, 1)

    def test_declared_skips_cannot_disappear(self):
        r = self.invoke([self.writer('<testsuite tests="1" skipped="1"><testcase/></testsuite>', kind='test',
                                    outputs=[{'path': '${run}/results.xml', 'kind': 'junit'}])])
        self.assertEqual(r.returncode, 1)

    def test_string_false_cannot_allow_empty_build_artifact(self):
        r = self.invoke([self.writer('', '${run}/empty.cf', kind='build',
                                    outputs=[{'path': '${run}/empty.cf', 'kind': 'file', 'allow_empty': 'false'}])])
        self.assertEqual(r.returncode, 2)
        self.assertFalse(self.run.exists())

    def test_future_logs_are_reserved_before_any_side_effect(self):
        r = self.invoke([self.writer('conflict', '${run}/next.stdout.log',
                                    outputs=[{'path': '${run}/next.stdout.log', 'kind': 'file'}]), self.step(id='next')])
        self.assertEqual(r.returncode, 2)
        self.assertFalse(self.run.exists())

    def test_prepare_cannot_claim_a_log_path(self):
        r = self.invoke([self.step()], prepare=[{'path':'${run}/first.stderr.log', 'json':{}}])
        self.assertEqual(r.returncode, 2)
        self.assertFalse(self.run.exists())

    def test_output_file_cannot_be_parent_of_another_output(self):
        r = self.invoke([self.writer('conflict', '${run}/folder',
                                    outputs=[{'path': '${run}/folder', 'kind': 'file'}]),
                         self.step(id='next', outputs=[{'path':'${run}/folder/x', 'kind':'file'}])])
        self.assertEqual(r.returncode, 2)
        self.assertFalse(self.run.exists())

    def test_relative_inputs_use_step_working_directory(self):
        project = self.root / 'project'; project.mkdir()
        (project / 'input.txt').write_text('data')
        r = self.invoke([self.step(cwd=str(project), inputs=['input.txt'])])
        self.assertEqual(r.returncode, 0, r.stderr)

    @unittest.skipIf(sys.platform == 'win32', 'POSIX executable fixture')
    def test_relative_executable_uses_step_working_directory(self):
        project = self.root / 'project'; project.mkdir()
        exe = project / 'tool'
        exe.write_text('#!' + sys.executable + '\nprint("ok")\n'); exe.chmod(0o755)
        r = self.invoke([self.step(cwd=str(project), command=['./tool'])])
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_json_field_gate_rejects_roundtrip_differences(self):
        r = self.invoke([self.step('print(\'{"status":"compared","changes_total":3}\')',
                         stdout_json_status=['compared'], stdout_json_equals={'changes_total': 0})])
        self.assertEqual(r.returncode, 1)

    def test_json_field_gate_accepts_identical_roundtrip(self):
        r = self.invoke([self.step('print(\'{"status":"compared","changes_total":0}\')',
                         stdout_json_status=['compared'], stdout_json_equals={'changes_total': 0})])
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == '__main__':
    unittest.main()
