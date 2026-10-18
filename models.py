from datetime import datetime

from extensions import db

FREQUENCY_CHOICES = ('daily', 'weekly', 'monthly')


class Habit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    target = db.Column(db.Integer)
    frequency = db.Column(db.String(20))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_dates = db.Column(db.Text)