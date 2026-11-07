"""Offline checks for the app's explicit Docker build-context allowlist."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent.parent


class ContainerPackagingTests(unittest.TestCase):
    def setUp(self):
        self.rules = [line.strip() for line in (ROOT / '.dockerignore').read_text().splitlines()
                      if line.strip() and not line.lstrip().startswith('#')]

    def test_copy_sources_are_explicitly_allowed(self):
        # COPY patterns and context rules must agree. New root modules are
        # picked up without updating either file's list of individual modules.
        self.assertEqual(self.rules[0], '**')
        for line in (ROOT / 'Dockerfile').read_text().splitlines():
            if not line.startswith('COPY '):
                continue
            for source in line.split()[1:-1]:
                with self.subTest(source=source):
                    self.assertTrue(list(ROOT.glob(source)))
                    self.assertIn('!' + source, self.rules)

    def test_new_python_modules_need_no_manifest_change(self):
        self.assertIn('!*.py', self.rules)
        self.assertIn('COPY *.py ./', (ROOT / 'Dockerfile').read_text().splitlines())

    def test_service_code_and_browser_scripts_are_allowed(self):
        self.assertIn('!ha_service/', self.rules)
        for extension in ('py', 'js'):
            self.assertIn('!ha_service/*.' + extension, self.rules)


if __name__ == '__main__':
    unittest.main()
