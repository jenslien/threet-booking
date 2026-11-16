"""Constants for the 3T integration."""

from datetime import timedelta

DOMAIN = "threet"

CONF_RULES = "rules"

# Keep the load on 3T's API low: bookings hourly, the (rarely changing) timetable every 6 h.
UPDATE_INTERVAL = timedelta(hours=1)
TIMETABLE_INTERVAL = timedelta(hours=6)
# Training statistics: 1 call per refresh (+ a few once a day).
STATS_INTERVAL = timedelta(hours=6)
# While on a waiting list, poll bookings more often as the class gets closer:
# (time left until class start, polling interval). 3T stops moving people up
# from the waiting list 2 hours before start, so fast polling ends there.
WAITLIST_POLLING = (
    (timedelta(hours=3), timedelta(minutes=1)),
    (timedelta(hours=5), timedelta(minutes=5)),
    (timedelta(hours=24), timedelta(minutes=15)),
)
WAITLIST_CLOSES_BEFORE = timedelta(hours=2)
# Classes open 7 days ahead at 08:00; 8 days ahead covers every opening within TIMETABLE_INTERVAL.
LOOKAHEAD_DAYS = 8
HISTORY_DAYS = 365
BOOKING_SLOTS = 6  # max 5 bookings + some room for waiting lists

# Fire slightly after bookableEarliest, then retry with these delays (seconds).
BOOKING_OFFSET_SECONDS = 1
RETRY_DELAYS = (5, 20, 60)

EVENT_BOOKED = f"{DOMAIN}_booked"
EVENT_BOOKING_FAILED = f"{DOMAIN}_booking_failed"

# Rule status values (sensor enum states)
STATUS_PLANNED = "planlagt"
STATUS_BOOKING = "bookes_naa"
STATUS_BOOKED = "booket"
STATUS_WAITLIST = "venteliste"
STATUS_PAUSED = "pauset"
STATUS_SKIPPED = "hoppet_over"
STATUS_CANCELLED = "avbestilt"
STATUS_FAILED = "feilet"
STATUS_CLOSED = "stengt"
STATUS_NO_CLASS = "ingen_time"

RULE_STATUSES = [
    STATUS_PLANNED,
    STATUS_BOOKING,
    STATUS_BOOKED,
    STATUS_WAITLIST,
    STATUS_PAUSED,
    STATUS_SKIPPED,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_CLOSED,
    STATUS_NO_CLASS,
]
