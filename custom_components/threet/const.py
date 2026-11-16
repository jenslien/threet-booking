"""Constants for the 3T integration."""

from datetime import timedelta

DOMAIN = "threet"

CONF_RULES = "rules"

UPDATE_INTERVAL = timedelta(minutes=15)
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
