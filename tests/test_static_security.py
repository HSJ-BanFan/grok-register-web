from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class StaticSecurityTest(unittest.TestCase):
    def test_settings_page_does_not_render_api_data_through_html_sinks(self):
        source = (ROOT / 'static/js/pages/settings.js').read_text(encoding='utf-8')

        for sink in ('innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write'):
            self.assertNotIn(sink, source)
        self.assertIn('container.replaceChildren', source)
        self.assertIn('function esc(value)', source)
        self.assertIn('if (!res.success || !res.data)', source)
        self.assertIn("{ type: 'password', mono: true }", source)

    def test_table_renderer_never_interprets_column_strings_as_html(self):
        source = (ROOT / 'static/js/components/table.js').read_text(encoding='utf-8')

        self.assertNotIn('innerHTML', source)
        self.assertIn("td.textContent = String(content ?? '')", source)

    def test_results_api_fields_use_text_nodes(self):
        source = (ROOT / 'static/js/pages/results.js').read_text(encoding='utf-8')

        for unsafe_renderer in (
            'render: (r) => `<span class="font-medium">${r.email}</span>`',
            'render: (r) => `<span class="mono">${r.account_password}</span>`',
            '<span class="time-cell">${r.created_at.substring(0, 16)}</span>',
        ):
            self.assertNotIn(unsafe_renderer, source)
        self.assertIn("span.textContent = String(value ?? '')", source)
        self.assertIn('grid.replaceChildren(', source)


if __name__ == '__main__':
    unittest.main()
