from flask import Flask, render_template, request, redirect, url_for
import os

from extensions import db

app = Flask(__name__)
app.config['SECRET_KEY'] = 'dev-secret-key-change-in-production'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///app.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

from models import Habit, MoodEntry, Expense, Recipe  # noqa: E402

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

@app.route('/mood-journal', methods=['GET', 'POST'])
def mood_journal():
    if request.method == 'POST':
        mood = request.form.get('mood', '').strip()
        notes = request.form.get('notes', '').strip()

        if mood:
            entry = MoodEntry(mood=mood, notes=notes or None)
            db.session.add(entry)
            db.session.commit()

        return redirect(url_for('mood_journal'))

    entries = MoodEntry.query.order_by(MoodEntry.created_at.desc()).all()
    return render_template('apps/mood_journal/index.html', page_id='mood-journal', entries=entries)

@app.route('/expense-splitter', methods=['GET', 'POST'])
def expense_splitter():
    if request.method == 'POST':
        description = request.form.get('description', '').strip()
        amount_raw = request.form.get('amount', '').strip()
        payer = request.form.get('payer', '').strip()
        participants = request.form.get('participants', '').strip()

        try:
            amount = float(amount_raw)
        except ValueError:
            amount = None

        if description and amount is not None and payer:
            expense = Expense(
                description=description,
                amount=amount,
                payer=payer,
                participants=participants or None
            )
            db.session.add(expense)
            db.session.commit()

        return redirect(url_for('expense_splitter'))

    expenses = Expense.query.order_by(Expense.created_at.desc()).all()
    expense_views = []
    for expense in expenses:
        people = [p.strip() for p in (expense.participants or '').split(',') if p.strip()]
        share = expense.amount / len(people) if people else None
        expense_views.append({
            'model': expense,
            'participants': people,
            'share': share
        })

    return render_template(
        'apps/expense_splitter/index.html',
        page_id='expense-splitter',
        expenses=expense_views
    )

@app.route('/recipe-assistant', methods=['GET', 'POST'])
def recipe_assistant():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        ingredients = request.form.get('ingredients', '').strip()
        instructions = request.form.get('instructions', '').strip()
        prep_time_raw = request.form.get('prep_time', '').strip()

        try:
            prep_time = int(prep_time_raw)
            if prep_time < 0:
                prep_time = None
        except ValueError:
            prep_time = None

        if name and ingredients and instructions:
            recipe = Recipe(
                name=name,
                ingredients=ingredients,
                instructions=instructions,
                prep_time=prep_time
            )
            db.session.add(recipe)
            db.session.commit()

        return redirect(url_for('recipe_assistant'))

    recipes = Recipe.query.order_by(Recipe.created_at.desc()).all()
    return render_template(
        'apps/recipe_assistant/index.html',
        page_id='recipe-assistant',
        recipes=recipes
    )

def init_db():
    with app.app_context():
        db.create_all()

if __name__ == '__main__':
    if not os.path.exists('app.db'):
        init_db()
    app.run(debug=True)
