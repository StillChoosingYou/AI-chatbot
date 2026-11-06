import os
import unittest
from unittest.mock import patch

from campus_access import CampusAccess, DEFAULT_LAT, DEFAULT_LNG, distance_m, parse_networks
from pg_support import DatabaseTestCase

GATE = (DEFAULT_LAT, DEFAULT_LNG)


class CampusMathTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, {'GEO_FENCE': '1', 'CAMPUS_IPS': '203.0.113.0/24, 198.51.100.7, nonsense, 2001:db8::/32'})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.campus = CampusAccess()

    def test_distance_is_in_metres(self):
        self.assertAlmostEqual(distance_m(*GATE, *GATE), 0, places=3)
        self.assertAlmostEqual(distance_m(13.0, 121.0, 14.0, 121.0), 111195, delta=200)     # one degree of latitude

    def test_position_inside_the_circle_passes(self):
        self.assertTrue(self.campus.position_ok(*GATE, 20))
        self.assertTrue(self.campus.position_ok(DEFAULT_LAT + 0.001, DEFAULT_LNG, 50))     # about 111 m north

    def test_position_outside_the_circle_fails(self):
        self.assertFalse(self.campus.position_ok(DEFAULT_LAT + 0.01, DEFAULT_LNG, 20))      # about 1.1 km
        self.assertFalse(self.campus.position_ok(14.5995, 120.9842, 10))                    # Manila

    def test_a_fix_that_is_too_imprecise_fails_even_at_the_gate(self):
        self.assertFalse(self.campus.position_ok(*GATE, 5000))

    def test_bad_values_are_refused(self):
        for lat, lng, acc in [('13.88', 121.26, 10), (float('nan'), 121.26, 10), (True, 121.26, 10), (None, None, None),
                              (91, 121, 10), (13.88, 181, 10), (13.88, 121.26, -1), (13.88, 121.26, float('inf'))]:
            with self.subTest(values=(lat, lng, acc)):
                self.assertFalse(self.campus.position_ok(lat, lng, acc))

    def test_school_networks(self):
        self.assertEqual(len(parse_networks('203.0.113.0/24, 198.51.100.7, nonsense, ')), 2)
        self.assertTrue(self.campus.on_campus_network('203.0.113.55'))
        self.assertTrue(self.campus.on_campus_network('198.51.100.7'))
        self.assertTrue(self.campus.on_campus_network('2001:db8::5'))
        self.assertFalse(self.campus.on_campus_network('203.0.114.1'))
        self.assertFalse(self.campus.on_campus_network('not an address'))
        self.assertFalse(self.campus.on_campus_network(''))

    def test_off_by_default(self):
        with patch.dict(os.environ, {'GEO_FENCE': '0'}):
            self.assertFalse(CampusAccess().enabled)


class CampusGateTests(DatabaseTestCase):
    def start(self, **environment):
        env = {'STUDENT_LOGIN': '1', 'COORDINATOR_PASSWORD': 'cp-pass', 'GEO_FENCE': '1', 'CAMPUS_IPS': '203.0.113.0/24', **environment}
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = self.make_app()
        return self.app.test_client()

    def chat(self, client, **headers):
        return client.post('/api/chat', json={'message': 'hello', 'workspace_role': 'student'}, headers=headers)

    def test_off_campus_student_pages_and_chat_are_closed(self):
        client = self.start()
        page = client.get('/student')
        self.assertEqual(page.status_code, 403)
        self.assertIn('On campus only', page.get_data(as_text=True))
        reply = self.chat(client)
        self.assertEqual(reply.status_code, 403)
        self.assertFalse(reply.json['campus'])
        self.assertEqual(client.post('/student/login', data={'student_id': 'P202400101', 'pin': '1'}).status_code, 403)

    def test_school_network_is_let_in_without_a_check(self):
        client = self.start()
        self.assertEqual(client.get('/student', headers={'X-Forwarded-For': '203.0.113.9'}).status_code, 200)
        self.assertNotEqual(self.chat(client, **{'X-Forwarded-For': '203.0.113.9'}).status_code, 403)
        self.assertEqual(client.get('/student', headers={'X-Forwarded-For': '203.0.114.9'}).status_code, 403)

    def test_position_check_gives_a_pass_and_far_positions_do_not(self):
        client = self.start()
        far = client.post('/api/campus/check', json={'lat': 14.5995, 'lng': 120.9842, 'accuracy': 10})
        self.assertEqual(far.status_code, 403)
        self.assertEqual(client.get('/student').status_code, 403)
        vague = client.post('/api/campus/check', json={'lat': DEFAULT_LAT, 'lng': DEFAULT_LNG, 'accuracy': 4000})
        self.assertEqual(vague.status_code, 403)
        self.assertIn('precise', vague.json['error'])
        near = client.post('/api/campus/check', json={'lat': DEFAULT_LAT, 'lng': DEFAULT_LNG, 'accuracy': 25})
        self.assertEqual(near.status_code, 200)
        self.assertEqual(client.get('/student').status_code, 200)
        self.assertNotEqual(self.chat(client).status_code, 403)

    def test_bad_position_bodies_are_rejected(self):
        client = self.start()
        for body in ({}, {'lat': '13.88', 'lng': 121.26, 'accuracy': 5}, {'lat': 13.88, 'lng': 121.26}, {'lat': True, 'lng': 1, 'accuracy': 1}):
            with self.subTest(body=body):
                self.assertEqual(client.post('/api/campus/check', json=body).status_code, 400)
        self.assertEqual(client.post('/api/campus/check', data='not json', content_type='application/json').status_code, 400)

    def test_an_expired_pass_closes_the_door_again(self):
        client = self.start()
        client.post('/api/campus/check', json={'lat': DEFAULT_LAT, 'lng': DEFAULT_LNG, 'accuracy': 10})
        with client.session_transaction() as saved:
            saved['campus_until'] = 1
        self.assertEqual(client.get('/student').status_code, 403)

    def test_the_sticker_scan_page_the_coordinator_and_the_coordinator_chat_are_not_fenced(self):
        client = self.start()
        coordinator = self.app.test_client()
        coordinator.post('/coordinator/login', data={'username': 'Coordinator', 'password': 'cp-pass'})
        rows = coordinator.get('/api/inventory').json['records']
        self.assertEqual(client.get('/student/' + rows[0]['token']).status_code, 200)
        self.assertEqual(coordinator.get('/api/inventory').status_code, 200)
        self.assertEqual(coordinator.post('/api/chat', json={'message': 'hello', 'workspace_role': 'coordinator'}).status_code, 200)
        self.assertNotEqual(client.get('/api/live?role=student').status_code, 403)

    def test_naming_another_role_does_not_get_past_the_fence(self):
        client = self.start()
        for path in ('/api/chat', '/api/chat/request', '/api/chat/history'):
            with self.subTest(path=path):
                reply = client.post(path, json={'message': 'hello', 'workspace_role': 'coordinator'}) if path != '/api/chat/history' \
                    else client.get(path)
                self.assertEqual(reply.status_code, 403)

    def test_fence_off_changes_nothing(self):
        client = self.start(GEO_FENCE='0')
        self.assertEqual(client.get('/student').status_code, 200)
        self.assertEqual(client.post('/api/campus/check', json={}).json, {'ok': True})

    def test_the_page_may_ask_for_location(self):
        client = self.start()
        self.assertIn('geolocation=(self)', client.get('/student').headers['Permissions-Policy'])
