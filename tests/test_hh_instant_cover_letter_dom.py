"""Exercise real Playwright locators against a local instant-apply fixture."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from playwright.sync_api import sync_playwright
import apply_worker as worker


@unittest.skipUnless(os.getenv('HH_APPLY_DOM_TESTS') == '1', 'Browser checks run in CI')
class InstantLetterDOMTests(unittest.TestCase):
    def test_instant_response_attaches_letter_with_one_separate_submit(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.route('https://hh.test/vacancy/1', lambda route: route.fulfill(
                content_type='text/html', body='''
                <meta charset="utf-8">
                <button id="apply" data-qa="vacancy-response-link-top">Откликнуться</button>
                <div id="response"></div>
                <script>
                window.applies = 0; window.letters = 0;
                document.querySelector('#apply').onclick = () => {
                  window.applies++;
                  document.querySelector('#apply').remove();
                  document.querySelector('#response').innerHTML = `
                    <p>Отклик отправлен</p>
                    <button id="attach">Приложить сопроводительное письмо</button>
                    <form id="letter" hidden>
                      <textarea name="letter"></textarea>
                      <button type="submit">Приложить</button>
                    </form><div id="saved"></div>`;
                  document.querySelector('#attach').onclick = () => {
                    document.querySelector('#letter').hidden = false;
                  };
                  document.querySelector('#letter').onsubmit = event => {
                    event.preventDefault(); window.letters++;
                    document.querySelector('#saved').textContent = document.querySelector('textarea').value;
                    document.querySelector('#letter').remove();
                  };
                };
                </script>'''))
            application = SimpleNamespace(id=1, cover_letter='Здравствуйте!\nМой опыт подходит.\nАлександр Руденко')
            vacancy = SimpleNamespace(title='IT', company='Test', url='https://hh.test/vacancy/1')
            with patch.object(worker, 'set_status') as status:
                self.assertEqual(worker.process_application(page, vacancy, application), 'applied')
            self.assertEqual(page.evaluate('window.applies'), 1)
            self.assertEqual(page.evaluate('window.letters'), 1)
            self.assertEqual(page.locator('#saved').text_content(), application.cover_letter)
            status.assert_called_with(1, 'applied', applied=True)

    def test_submit_search_never_uses_vacancy_apply_button(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.set_content('<button>Откликнуться</button><form><textarea></textarea><button>Закрыть</button></form>')
            self.assertIsNone(worker.find_letter_submit(page.locator('textarea')))
