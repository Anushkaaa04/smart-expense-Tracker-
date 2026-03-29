from flask import render_template
from flask_login import current_user
from app.models import Expense
from app import db
from sqlalchemy import func
from datetime import datetime, timedelta
import calendar

def monthly_analysis():
    today = datetime.utcnow()
    
    # 1. Define Months
    current_month = today.month
    current_year = today.year
    
    last_month_date = today.replace(day=1) - timedelta(days=1)
    last_month = last_month_date.month
    last_year = last_month_date.year

    # 2. Query Logic: EXCLUDE 'Savings' to show real spending
    current_total = db.session.query(func.sum(Expense.amount)).filter(
        Expense.user_id == current_user.id,
        func.extract('month', Expense.date_posted) == current_month,
        func.extract('year', Expense.date_posted) == current_year,
        Expense.category != 'Savings' 
    ).scalar() or 0

    last_total = db.session.query(func.sum(Expense.amount)).filter(
        Expense.user_id == current_user.id,
        func.extract('month', Expense.date_posted) == last_month,
        func.extract('year', Expense.date_posted) == last_year,
        Expense.category != 'Savings'
    ).scalar() or 0

    # 3. Enhanced Calculation Logic
    # We calculate the absolute difference for display
    abs_diff = abs(last_total - current_total)
    percent_change = 0
    
    if last_total > 0:
        percent_change = (abs_diff / last_total) * 100
    elif current_total > 0:
        percent_change = 100 # If last month was 0 and this month isn't

    # 4. Status and Message Logic
    if current_total == 0:
        # If no spending happened, it's always Saving Mode
        status = "SAVING MODE ✅"
        message = "You haven't spent anything yet this month. Amazing discipline!"
        is_overspending = False
    elif current_total <= last_total:
        status = "SAVING MODE ✅"
        message = f"You've spent ₹{abs_diff:,.2f} less than last month! Great discipline."
        is_overspending = False
    else:
        status = "OVERSPENDING ⚠️"
        message = f"Your spending is up by ₹{abs_diff:,.2f} compared to {calendar.month_name[last_month]}."
        is_overspending = True

    return render_template('analysis.html', 
                           current_total=float(current_total),
                           last_total=float(last_total),
                           abs_diff=float(abs_diff),
                           percent_change=round(percent_change, 1),
                           status=status,
                           message=message,
                           is_overspending=is_overspending,
                           last_month_name=calendar.month_name[last_month],
                           current_month_name=calendar.month_name[current_month])