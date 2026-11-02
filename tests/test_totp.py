import os
import re
from unittest.mock import patch

import totp
from pg_support import DatabaseTestCase

ENV = {'STUDENT_LOGIN': '1', 'ADMIN_PASSWORD': 'admin-pass', 'COORDINATOR_PASSWORD': 'coord-pass', 'ADMIN_CAPTCHA': '1', 'ADMIN_TOTP': '1'}
CREDS = {'username': 'Admin', 'password': 'admin-pass'}
RFC_SECRET = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'          # "12345678901234567890", the RFC 6238 test key


class TotpUnitTests(DatabaseTestCase):
    def test_rfc_6238_sha1_vectors(self):
        for moment, expected in ((59, '287082'), (1111111109, '081804'), (1111111111, '050471'), (1234567890, '005924'), (2000000000, '279037')):
            with self.subTest(moment=moment):
                self.assertEqual(totp.code_at(RFC_SECRET, totp.counter_for(moment)), expected)

    def test_a_code_is_accepted_one_step_either_side_but_not_further(self):
        now = 1_700_000_000
        step = totp.counter_for(now)
        for offset, ok in ((-2, False), (-1, True), (0, True), (1, True), (2, False)):
            with self.subTest(offset=offset):
                code = totp.code_at(RFC_SECRET, step + offset)
                self.assertEqual(totp.verify(RFC_SECRET, code, 0, now) is not None, ok)

    def test_a_used_step_cannot_be_replayed(self):
        now = 1_700_000_000
        code = totp.code_at(RFC_SECRET, totp.counter_for(now))
        used = totp.verify(RFC_SECRET, code, 0, now)
        self.assertIsNotNone(used)
        self.assertIsNone(totp.verify(RFC_SECRET, code, used, now))

    def test_garbage_is_refused(self):
        for bad in ('', None, 'abcdef', '12345', '1234567'):
            with self.subTest(bad=bad):
                self.assertIsNone(totp.verify(RFC_SECRET, bad, 0, 1_700_000_000))

    def test_spaces_and_dashes_in_a_typed_code_are_ignored(self):
        now = 1_700_000_000
        code = totp.code_at(RFC_SECRET, totp.counter_for(now))
        self.assertIsNotNone(totp.verify(RFC_SECRET, f'{code[:3]} {code[3:]}', 0, now))

    def test_new_secrets_are_random_valid_base32(self):
        one, two = totp.new_secret(), totp.new_secret()
        self.assertNotEqual(one, two)
        self.assertEqual(len(one), 32)
        totp.code_at(one, 1)

    def test_uri_names_the_issuer_and_account(self):
        link = totp.uri('ABC', 'Admin', 'School ID Admin')
        self.assertTrue(link.startswith('otpauth://totp/School%20ID%20Admin%3AAdmin?secret=ABC'))
        self.assertIn('issuer=School%20ID%20Admin', link)

    def test_recovery_codes_work_once_and_are_never_stored_as_text(self):
        codes = totp.new_recovery_codes()
        self.assertEqual(len(set(codes)), totp.RECOVERY_COUNT)
        self.assertTrue(all(re.fullmatch(r'[0-9a-f]{4}-[0-9a-f]{4}', c) for c in codes))
        hashes = [totp.recovery_hash(c, 'k') for c in codes]
        self.assertFalse(set(codes) & set(hashes))
        ok, rest = totp.use_recovery(codes[0].upper(), hashes, 'k')
        self.assertTrue(ok)
        self.assertEqual(len(rest), len(hashes) - 1)
        self.assertFalse(totp.use_recovery(codes[0], rest, 'k')[0])
        self.assertFalse(totp.use_recovery(codes[1], hashes, 'other-key')[0])
        self.assertFalse(totp.use_recovery('abcd', hashes, 'k')[0])


class AdminTotpFlowTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        patcher = patch.dict(os.environ, ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = self.make_app()

    def picture(self, client):
        with patch('captcha.new_code', return_value='AB7KM'):
            client.get('/admin/captcha.png')

    def admin(self):
        client = self.app.test_client()
        client.post('/admin/login', data=CREDS)
        self.picture(client)
        client.post('/admin/verify', data={'captcha': 'AB7KM'})
        return client

    def enable(self, client):
        begin = client.post('/api/admin/totp/begin', json={})
        self.assertEqual(begin.status_code, 200)
        secret = begin.json['secret'].replace(' ', '')
        code = totp.code_at(secret, totp.counter_for())
        done = client.post('/api/admin/totp/enable', json={'code': code})
        self.assertEqual(done.status_code, 200, done.json)
        return secret, done.json['recovery'], totp.counter_for()

    def stepped_code(self, secret, ahead):
        return totp.code_at(secret, totp.counter_for() + ahead)

    def test_off_by_default_so_nobody_is_locked_out_of_an_existing_install(self):
        client = self.admin()
        self.assertEqual(client.get('/api/admin/totp').json['enabled'], False)
        self.assertNotIn(b'name="code"', self.app.test_client().get('/admin/verify').data)

    def test_setup_needs_a_correct_code_and_hands_out_recovery_codes_once(self):
        client = self.admin()
        client.post('/api/admin/totp/begin', json={})
        self.assertEqual(client.post('/api/admin/totp/enable', json={'code': '000000'}).status_code, 400)
        self.assertFalse(client.get('/api/admin/totp').json['enabled'])
        qr = client.get('/api/admin/totp/qr.png')
        self.assertEqual((qr.status_code, qr.data[:4]), (200, b'\x89PNG'))
        client2 = self.admin()
        secret, recovery, _ = self.enable(client2)
        status = client2.get('/api/admin/totp').json
        self.assertEqual((status['enabled'], status['recovery_left']), (True, totp.RECOVERY_COUNT))
        self.assertNotIn('recovery', status)
        self.assertEqual(client2.get('/api/admin/totp/qr.png').status_code, 404)        # the picture goes away after setup

    def test_the_secret_is_not_stored_as_plain_text(self):
        client = self.admin()
        secret, recovery, _ = self.enable(client)
        with self.db() as db:
            raw = db.execute("SELECT value FROM app_settings WHERE key = 'admin_totp'").fetchone()[0]
        self.assertNotIn(secret, raw)
        for code in recovery:
            self.assertNotIn(code, raw)

    def sign_in_with(self, code):
        client = self.app.test_client()
        client.post('/admin/login', data=CREDS)
        self.picture(client)
        return client, client.post('/admin/verify', data={'captcha': 'AB7KM', 'code': code})

    def test_sign_in_then_needs_the_picture_and_the_code(self):
        secret, recovery, used = self.enable(self.admin())
        page = self.app.test_client()
        page.post('/admin/login', data=CREDS)
        self.assertIn(b'name="code"', page.get('/admin/verify').data)
        client, response = self.sign_in_with('')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(client.get('/api/admin/overview').status_code, 401)
        client, response = self.sign_in_with('123456')
        self.assertEqual(response.status_code, 401)
        good = totp.code_at(secret, used + 1)
        with patch('totp.counter_for', return_value=used + 1):
            client, response = self.sign_in_with(good)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(client.get('/api/admin/overview').status_code, 200)

    def test_a_code_cannot_be_used_twice(self):
        secret, recovery, used = self.enable(self.admin())
        good = totp.code_at(secret, used + 1)
        with patch('totp.counter_for', return_value=used + 1):
            self.assertEqual(self.sign_in_with(good)[1].status_code, 302)
            self.assertEqual(self.sign_in_with(good)[1].status_code, 401)

    def test_the_right_code_with_the_wrong_picture_is_refused_and_does_not_spend_the_code(self):
        secret, recovery, used = self.enable(self.admin())
        good = totp.code_at(secret, used + 1)
        with patch('totp.counter_for', return_value=used + 1):
            client = self.app.test_client()
            client.post('/admin/login', data=CREDS)
            self.picture(client)
            self.assertEqual(client.post('/admin/verify', data={'captcha': 'WRONG', 'code': good}).status_code, 401)
            self.assertEqual(self.sign_in_with(good)[1].status_code, 302)

    def test_a_recovery_code_signs_in_once(self):
        secret, recovery, used = self.enable(self.admin())
        self.assertEqual(self.sign_in_with(recovery[0])[1].status_code, 302)
        self.assertEqual(self.sign_in_with(recovery[0])[1].status_code, 401)
        self.assertEqual(self.sign_in_with(recovery[1])[1].status_code, 302)

    def test_five_wrong_codes_end_the_attempt(self):
        self.enable(self.admin())
        client = self.app.test_client()
        client.post('/admin/login', data=CREDS)
        for _ in range(totp_tries()):
            self.picture(client)
            response = client.post('/admin/verify', data={'captcha': 'AB7KM', 'code': '111111'})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(client.get('/admin/verify').status_code, 302)

    def test_the_environment_switch_is_the_way_back_in_after_losing_the_phone(self):
        self.enable(self.admin())
        with patch.dict(os.environ, {'ADMIN_TOTP': '0'}):
            app = self.make_app()
            client = app.test_client()
            client.post('/admin/login', data=CREDS)
            with patch('captcha.new_code', return_value='AB7KM'):
                client.get('/admin/captcha.png')
            self.assertNotIn(b'name="code"', client.get('/admin/verify').data)
            self.assertEqual(client.post('/admin/verify', data={'captcha': 'AB7KM'}).status_code, 302)
            self.assertTrue(client.get('/api/admin/totp').json['switched_off_by_environment'])

    def test_turning_it_off_needs_a_current_code(self):
        client = self.admin()
        secret, recovery, used = self.enable(client)
        self.assertEqual(client.post('/api/admin/totp/disable', json={'code': '000000'}).status_code, 400)
        self.assertTrue(client.get('/api/admin/totp').json['enabled'])
        with patch('totp.counter_for', return_value=used + 1):
            off = client.post('/api/admin/totp/disable', json={'code': totp.code_at(secret, used + 1)})
        self.assertEqual(off.status_code, 200)
        self.assertFalse(client.get('/api/admin/totp').json['enabled'])

    def test_new_recovery_codes_replace_the_old_ones(self):
        client = self.admin()
        secret, recovery, used = self.enable(client)
        self.assertEqual(client.post('/api/admin/totp/recovery', json={'code': recovery[0]}).status_code, 400)   # a recovery code is not enough
        with patch('totp.counter_for', return_value=used + 1):
            fresh = client.post('/api/admin/totp/recovery', json={'code': totp.code_at(secret, used + 1)})
        self.assertEqual(fresh.status_code, 200)
        self.assertEqual(len(fresh.json['recovery']), totp.RECOVERY_COUNT)
        self.assertEqual(self.sign_in_with(recovery[2])[1].status_code, 401)
        self.assertEqual(self.sign_in_with(fresh.json['recovery'][0])[1].status_code, 302)

    def test_the_management_routes_need_an_admin_session(self):
        anonymous = self.app.test_client()
        for method, path in (('get', '/api/admin/totp'), ('post', '/api/admin/totp/begin'), ('post', '/api/admin/totp/enable'),
                             ('post', '/api/admin/totp/disable'), ('post', '/api/admin/totp/recovery'), ('get', '/api/admin/totp/qr.png')):
            with self.subTest(path=path):
                self.assertEqual(getattr(anonymous, method)(path).status_code, 401)

    def test_setup_cannot_be_started_twice(self):
        client = self.admin()
        self.enable(client)
        self.assertEqual(client.post('/api/admin/totp/begin', json={}).status_code, 409)

    def test_without_the_picture_the_code_alone_is_the_second_step(self):
        with patch.dict(os.environ, {'ADMIN_CAPTCHA': '0'}):
            app = self.make_app()
            admin = app.test_client()
            self.assertEqual(admin.post('/admin/login', data=CREDS).headers['Location'], '/')
            # turn it on through the API of the signed-in admin
            begin = admin.post('/api/admin/totp/begin', json={}).json['secret'].replace(' ', '')
            self.assertEqual(admin.post('/api/admin/totp/enable', json={'code': totp.code_at(begin, totp.counter_for())}).status_code, 200)
            client = app.test_client()
            self.assertEqual(client.post('/admin/login', data=CREDS).headers['Location'], '/admin/verify')
            page = client.get('/admin/verify').data
            self.assertIn(b'name="code"', page)
            self.assertNotIn(b'name="captcha"', page)


def totp_tries():
    import admin_panel
    return admin_panel.CAPTCHA_TRIES
