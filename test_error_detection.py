import unittest
from remote_probe import analyse_screen


def screen(body, working=''):
    return body + '\n' + working + '\n\n» Ask Codex to do anything\n\n GPT-6.1-Sol ultra · Main [default]\n ? for shortcuts'


class ErrorDetectionTests(unittest.TestCase):
    def test_security_content_banner_is_an_active_red_error(self):
        banner = 'ⓘ This content can’t be shown\n  We take extra care with some cybersecurity requests. If you’re doing authorized security work, apply for Daybreak to get broader access.\n  Learn more: https://help.openai.com/example'
        for heading in ('• Interacted with `/root/matrix_supervision`\n', ''):
            result = analyse_screen(screen(heading + banner))
            self.assertEqual(result['state'], 'error')
            self.assertEqual(result['error_kind'], 'safety')
            self.assertEqual(result['attention_level'], 'error')
        self.assertEqual(analyse_screen(screen(banner, '• Working (2s • esc to interrupt)'))['state'], 'running')
        self.assertEqual(analyse_screen(screen(banner + '\n• Successfully completed.'))['state'], 'idle')
        self.assertEqual(analyse_screen(screen('• Testing\n  └ This content can’t be shown\n  We take extra care with cybersecurity requests.'))['state'], 'idle')

    def test_http_failures_override_idle(self):
        for code in ('503', '401', '403', '429'):
            row = analyse_screen(screen('■ unexpected status ' + code + ' request failed, url: https://private.example, request id: abc'))
            self.assertEqual(row['state'], 'error')
            self.assertIn(code, row['error_summary'])
            self.assertNotIn('private', row['error_summary'])

    def test_new_work_and_history_clear_old_failure(self):
        body = '■ unexpected status 503 Service Unavailable'
        row = analyse_screen(screen(body, '• Working (3s • esc to interrupt)'))
        self.assertEqual(row['state'], 'running')
        self.assertEqual(row['error_summary'], '')
        row = analyse_screen(screen(body + '\n Back to bottom · esc'))
        self.assertEqual(row['state'], 'history')
        self.assertEqual(row['error_summary'], '')

    def test_regular_tool_errors_or_prose_not_fatal(self):
        for body in ('• Ran test\n  └ Error: request failed with status 503', '• Ran test\n  Error: request failed with status 503', '• The server returned 503 during yesterday’s test.'):
            self.assertEqual(analyse_screen(screen(body))['state'], 'idle')

    def test_successful_turn_after_old_failure_is_idle(self):
        for next_turn in ('» try again\n• Done.', '• Successfully completed.', '─ Worked for 5s ─\nResult saved.'):
            row = analyse_screen(screen('■ unexpected status 503 Service Unavailable\n' + next_turn))
            self.assertEqual(row['state'], 'idle')
            self.assertEqual(row['error_summary'], '')

    def test_retry_signal_remains_active_and_stable(self):
        a = analyse_screen(screen('output', '• Reconnecting... 1/5 (3s • esc to interrupt)'))
        b = analyse_screen(screen('output', '• Reconnecting... 2/5 (7s • esc to interrupt)'))
        self.assertEqual(a['state'], 'running')
        self.assertEqual(a['error_kind'], 'retrying')
        self.assertEqual(a['error_signature'], b['error_signature'])

    def test_request_id_changes_do_not_create_new_error_signature(self):
        a = analyse_screen(screen('■ unexpected status 503, request id: aaa'))
        b = analyse_screen(screen('■ unexpected status 503, request id: bbb'))
        self.assertEqual(a['error_signature'], b['error_signature'])


if __name__ == '__main__':
    unittest.main()
