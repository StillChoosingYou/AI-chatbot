import os
from datetime import date, datetime
from unittest.mock import patch

import seasonal_news
from pg_support import DatabaseTestCase

ENV = {'ADMIN_PASSWORD': 'admin-pass', 'AUTO_NEWS': '1', 'ADMIN_CAPTCHA': '0'}


class CalendarTests(DatabaseTestCase):
    def texts(self, day):
        return [item['text'] for item in seasonal_news.auto_news(day)]

    def test_october_shows_halloween_and_looks_at_november(self):
        texts = self.texts(date(2026, 10, 15))
        self.assertIn('Coming up: Halloween, October 31 (in 16 days).', texts)
        self.assertTrue(any(t.startswith('Next month: All Saints') for t in texts))

    def test_on_the_day_it_is_a_greeting_and_the_next_day_is_tomorrow(self):
        texts = self.texts(date(2026, 10, 31))
        self.assertEqual(texts[0], 'Happy Halloween!')
        self.assertIn("Coming up: All Saints' Day, November 1 (tomorrow).", texts)

    def test_november_looks_at_christmas_and_drops_halloween(self):
        texts = self.texts(date(2026, 11, 3))
        self.assertTrue(any('Christmas Day, December 25 (in 52 days)' in t for t in texts))
        self.assertFalse(any('Halloween' in t for t in texts))

    def test_december_looks_into_the_next_year(self):
        self.assertTrue(any('New Year' in t for t in self.texts(date(2026, 12, 31))))

    def test_whole_month_event_and_last_monday(self):
        self.assertIn("March is Women's Month.", self.texts(date(2026, 3, 3)))
        self.assertTrue(any('National Heroes Day, August 31' in t for t in self.texts(date(2026, 8, 10))))
        self.assertEqual(seasonal_news.event_date(2026, 8, -1), date(2026, 8, 31))

    def test_every_month_has_something_and_the_cap_holds(self):
        for month in range(1, 13):
            for day in (1, 15, 28):
                with self.subTest(month=month, day=day):
                    items = seasonal_news.auto_news(date(2026, month, day))
                    self.assertTrue(1 <= len(items) <= seasonal_news.MAX_AUTO)
                    self.assertTrue(all(i['icon'] and i['text'] and i['auto'] for i in items))

    def test_manila_date_is_used(self):
        utc_evening = datetime.fromisoformat('2026-10-30T17:00:00+00:00')       # already Oct 31 in Manila
        self.assertEqual(seasonal_news.today_in_manila(utc_evening), date(2026, 10, 31))


class TickerTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        patcher = patch.dict(os.environ, ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = self.make_app()
        self.admin = self.app.test_client()
        self.admin.post('/admin/login', data={'username': 'Admin', 'password': 'admin-pass'})
        self.visitor = self.app.test_client()

    def frozen(self, day):
        return patch('Chatbot.auto_news', lambda: seasonal_news.auto_news(day))

    def test_public_ticker_has_own_announcements_then_the_calendar(self):
        self.admin.put('/api/admin/news', json={'news': [{'icon': '🎉', 'text': 'Enrollment is open'}]})
        news = self.visitor.get('/api/news').json['news']
        self.assertEqual(news[0], {'icon': '🎉', 'text': 'Enrollment is open'})
        self.assertGreater(len(news), 1)
        self.assertTrue(all(set(n) == {'icon', 'text'} for n in news))

    def test_admin_edits_only_their_own_list_and_sees_the_auto_ones(self):
        self.admin.put('/api/admin/news', json={'news': [{'icon': '🎉', 'text': 'Own'}]})
        data = self.admin.get('/api/admin/news').json
        self.assertEqual([n['text'] for n in data['news']], ['Own'])
        self.assertTrue(data['auto'] and data['auto_on'] and not data['auto_forced_off'])
        self.assertEqual(self.admin.put('/api/admin/news', json={'news': data['news']}).json['news'], data['news'])

    def test_switch_off_and_on(self):
        self.assertEqual(self.admin.put('/api/admin/news/auto', json={'on': False}).json['auto_on'], False)
        self.assertEqual([n['text'] for n in self.visitor.get('/api/news').json['news']], [n['text'] for n in self.admin.get('/api/admin/news').json['news']])
        self.admin.put('/api/admin/news/auto', json={'on': True})
        self.assertTrue(self.visitor.get('/api/news').json['news'])
        for bad in ({}, {'on': 'yes'}, {'on': 1}):
            self.assertEqual(self.admin.put('/api/admin/news/auto', json=bad).status_code, 400)

    def test_switch_needs_the_admin_and_env_can_force_it_off(self):
        self.assertEqual(self.visitor.put('/api/admin/news/auto', json={'on': True}).status_code, 401)
        with patch.dict(os.environ, {'AUTO_NEWS': '0'}):
            self.assertTrue(self.admin.get('/api/admin/news').json['auto_forced_off'])
            self.assertNotIn(True, ['auto' in n for n in self.visitor.get('/api/news').json['news']])

    def test_the_ticker_follows_the_date(self):
        with self.frozen(date(2026, 10, 20)):
            self.assertTrue(any('Halloween' in n['text'] for n in self.visitor.get('/api/news').json['news']))
        with self.frozen(date(2026, 11, 20)):
            self.assertTrue(any('Christmas' in n['text'] for n in self.visitor.get('/api/news').json['news']))
