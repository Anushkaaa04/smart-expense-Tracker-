from flask import render_template
from flask_login import current_user, login_required
from app.models import Expense
from app import db
from sqlalchemy import func
from datetime import datetime, timedelta
import calendar

@login_required
def streaks_dashboard():
    today = datetime.utcnow().date()
    
    # 1. Calculate Current Streak Count
    all_dates = db.session.query(func.date(Expense.date_posted)).filter_by(user_id=current_user.id)\
        .distinct().order_by(func.date(Expense.date_posted).desc()).all()
    
    # Standardize dates into a list of date objects
    date_list = []
    for d in all_dates:
        if hasattr(d[0], 'day'):
            date_list.append(d[0])
        else:
            date_list.append(datetime.strptime(str(d[0]).split(' ')[0], '%Y-%m-%d').date())

    current_streak = 0
    if date_list:
        yesterday = today - timedelta(days=1)
        # Only start counting if active today or yesterday
        if date_list[0] == today or date_list[0] == yesterday:
            check_date = date_list[0]
            for d in date_list:
                if d == check_date:
                    current_streak += 1
                    check_date -= timedelta(days=1)
                else:
                    break

    # 2. Weekly Spending Logic (Bar Graph)
    start_of_week = today - timedelta(days=6)
    weekly_data = db.session.query(
        func.date(Expense.date_posted), 
        func.sum(Expense.amount)
    ).filter(
        Expense.user_id == current_user.id,
        Expense.date_posted >= start_of_week
    ).group_by(func.date(Expense.date_posted)).all()

    days = [(start_of_week + timedelta(days=i)) for i in range(7)]
    day_labels = [d.strftime('%a') for d in days]
    spending_map = {str(val[0]): float(val[1]) for val in weekly_data}
    bar_values = [spending_map.get(str(d), 0) for d in days]

    # 3. Calendar Logic
    year, month = today.year, today.month
    cal = calendar.monthcalendar(year, month)
    active_days = [d.day for d in date_list if d.month == month and d.year == year]

    return render_template('streaks.html', 
                           day_labels=day_labels, 
                           bar_values=bar_values, 
                           cal=cal, 
                           active_days=active_days,
                           month_name=calendar.month_name[month],
                           streak_count=current_streak)