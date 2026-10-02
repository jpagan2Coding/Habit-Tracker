from flask import Flask, render_template, request, redirect, url_for
import os

from extensions import db

app = Flask(__name__)
app.config['SECRET_KEY'] = 'dev-secret-key-change-in-production'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///app.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

from models import Habit  # noqa: E402

@app.route('/')
def home():
    return render_template('home/index.html', page_id='home')

@app.route('/habit-tracker', methods=['GET', 'POST'])
def habit_tracker():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()

        if name:
            habit = Habit(name=name, description=description or None)
            db.session.add(habit)
            db.session.commit()

        return redirect(url_for('habit_tracker'))

    habits = Habit.query.order_by(Habit.created_at.desc()).all()
    return render_template('apps/habit_tracker/index.html', page_id='habit-tracker', habits=habits)


def init_db():
    with app.app_context():
        db.create_all()

if __name__ == '__main__':
    if not os.path.exists('app.db'):
        init_db()
    app.run(debug=True)
