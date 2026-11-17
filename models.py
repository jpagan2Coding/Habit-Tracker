# Ordered CSV columns shared by .habit imports and exports.
HABIT_FILE_FIELDS = [
    "habit_id",
    "name",
    "description",
    "target",
    "frequency",
    "created_at",
    "completed_date",
    "status",
]

FREQUENCY_CHOICES = ("daily", "weekly", "monthly")

# Break lengths offered when pausing a habit (days -> label).
PAUSE_DURATIONS = {
    1: "1 day",
    3: "3 days",
    7: "1 week",
    14: "2 weeks",
    30: "30 days",
}
