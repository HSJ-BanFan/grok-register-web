from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class StaticSecurityTest(unittest.TestCase):
    def test_settings_page_does_not_render_api_data_through_html_sinks(self):
        source = (ROOT / 'static/js/pages/settings.js').read_text(encoding='utf-8')

        for sink in ('innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write'):
            self.assertNotIn(sink, source)
        self.assertIn('new DOMParser()', source)
        self.assertIn('container.replaceChildren', source)
        self.assertIn('function esc(value)', source)


if __name__ == '__main__':
    unittest.main()
