"""Ticker announcements made from the calendar: this month's events and a look at next month's.

Nothing is stored. The list is built from today's date (Philippine time, UTC+8, no daylight saving) every time the
ticker asks, so it changes by itself on the 1st of each month and counts the days down. Dates are fixed ones only;
movable days (Holy Week, Eid) are left out rather than guessed. The admin can switch the whole feature off.
"""
import calendar
from datetime import date, datetime, timedelta, timezone

MANILA = timezone(timedelta(hours=8))
MAX_AUTO = 5

# (month, day, icon, name, greeting). day 0 = the whole month; -1 = last Monday of the month.
EVENTS = (
    (1, 1, '🎆', "New Year's Day", 'Happy New Year!'),
    (2, 14, '💝', "Valentine's Day", "Happy Valentine's Day!"),
    (2, 25, '🇵🇭', 'EDSA People Power Anniversary', 'Remembering the EDSA People Power Revolution today.'),
    (3, 0, '🌸', "Women's Month", "March is Women's Month."),
    (4, 9, '🇵🇭', 'Araw ng Kagitingan', 'Araw ng Kagitingan (Day of Valor) today.'),
    (5, 1, '🛠️', 'Labor Day', "Happy Labor Day!"),
    (6, 12, '🇵🇭', 'Independence Day', 'Maligayang Araw ng Kalayaan! Happy Independence Day!'),
    (7, 0, '🥗', 'Nutrition Month', 'July is Nutrition Month.'),
    (8, 0, '🗣️', 'Buwan ng Wika', 'Agosto ay Buwan ng Wika (National Language Month).'),
    (8, -1, '🦸', 'National Heroes Day', 'National Heroes Day today.'),
    (9, 1, '🎄', 'Start of the "Ber" months', 'The "Ber" months are here: Christmas season is starting!'),
    (10, 5, '🍎', "World Teachers' Day", "Happy World Teachers' Day to our teachers!"),
    (10, 31, '🎃', 'Halloween', 'Happy Halloween!'),
    (11, 1, '🕯️', "All Saints' Day", "All Saints' Day today."),
    (11, 2, '🕯️', "All Souls' Day", "All Souls' Day today."),
    (11, 30, '🇵🇭', 'Bonifacio Day', 'Bonifacio Day today.'),
    (12, 25, '🎄', 'Christmas Day', 'Merry Christmas!'),
    (12, 30, '🇵🇭', 'Rizal Day', 'Rizal Day today.'),
    (12, 31, '🎆', "New Year's Eve", "Happy New Year's Eve!"),
)


def today_in_manila(now=None):
    return (now or datetime.now(MANILA)).astimezone(MANILA).date()


def event_date(year, month, day):
    """The date of an event in a year, or None for a whole-month event (day 0)."""
    if day == 0:
        return None
    if day == -1:
        last = calendar.monthrange(year, month)[1]
        end = date(year, month, last)
        return end - timedelta(days=end.weekday())          # the last Monday
    return date(year, month, day)


def days_text(days):
    return 'tomorrow' if days == 1 else f'in {days} days'


def auto_news(today=None):
    """[{"icon", "text", "auto": True}]: today's and later events this month, then the next month's first events."""
    today = today or today_in_manila()
    out = []
    for month, day, icon, name, greeting in EVENTS:
        if month != today.month:
            continue
        when = event_date(today.year, month, day)
        if when is None:
            out.append((0, icon, greeting))
        elif when == today:
            out.append((0, icon, greeting))
        elif when > today:
            out.append(((when - today).days, icon, f'Coming up: {name}, {when:%B} {when.day} ({days_text((when - today).days)}).'))
    out.sort(key=lambda item: item[0])
    items = [{'icon': icon, 'text': text, 'auto': True} for _, icon, text in out[:3]]
    following = date(today.year + (today.month == 12), today.month % 12 + 1, 1)
    coming = []
    for month, day, icon, name, _ in EVENTS:
        if month != following.month:
            continue
        when = event_date(following.year, month, day)
        coming.append((when or following, icon, name, when))
    coming.sort(key=lambda item: item[0])
    for start, icon, name, when in coming[:2]:
        if when is None:
            text = f'Next month: {name}.'
        else:
            days = (when - today).days
            text = f'{"Coming up" if days <= 7 else "Next month"}: {name}, {when:%B} {when.day} ({days_text(days)}).'
        items.append({'icon': icon, 'text': text, 'auto': True})
    return items[:MAX_AUTO]
