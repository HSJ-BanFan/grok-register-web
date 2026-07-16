import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


class StaticMimeTypesTest(unittest.TestCase):
    def test_app_overrides_polluted_static_mime_types(self):
        project_root = Path(__file__).resolve().parents[1]
        script = textwrap.dedent(
            """
            import mimetypes

            mimetypes.add_type('text/plain', '.js')
            mimetypes.add_type('text/plain', '.css')

            import app

            with app.app.test_client() as client:
                javascript = client.get('/static/js/app.js')
                stylesheet = client.get('/static/css/style.css')

            assert javascript.status_code == 200
            assert javascript.mimetype == 'application/javascript'
            assert stylesheet.status_code == 200
            assert stylesheet.mimetype == 'text/css'
            """
        )

        result = subprocess.run(
            [sys.executable, '-c', script],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(
            result.returncode,
            0,
            msg=f'child process failed:\n{result.stdout}\n{result.stderr}',
        )


if __name__ == '__main__':
    unittest.main()
