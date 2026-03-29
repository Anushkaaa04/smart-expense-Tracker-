import io
import csv
import calendar 
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, Response
from app import db, bcrypt
from app.models import Expense, User, Event, QuickExpense, WishlistItem, BudgetHistory
from flask_login import login_user, current_user, logout_user, login_required
from sqlalchemy import func

# --- MODULAR IMPORTS ---
from app.main.gamification import streaks_dashboard
from app.main.analysis import monthly_analysis 
from app.main.updates import get_processed_date  # Logic for past dates

main = Blueprint('main', __name__)

# ==========================================
# --- 1. CORE DASHBOARD ---
# ==========================================
@main.route('/')
@login_required
def index():
    today = datetime.utcnow().date()
    
    if not current_user.last_budget_update or current_user.last_budget_update.month != today.month:
        return redirect(url_for('main.monthly_reset'))

    search_query = request.args.get('search')
    category_filter = request.args.get('category')
    is_daily = current_user.income_type == 'daily'

    query = Expense.query.filter_by(user_id=current_user.id).filter(
        Expense.event_id == None   # exclude trip expenses from main dashboard
    )

    if is_daily:
        # Daily mode: only show today's expenses
        query = query.filter(
            func.extract('year',  Expense.date_posted) == today.year,
            func.extract('month', Expense.date_posted) == today.month,
            func.extract('day',   Expense.date_posted) == today.day
        )
    else:
        # Monthly mode: show this month's expenses
        query = query.filter(
            func.extract('month', Expense.date_posted) == today.month,
            func.extract('year',  Expense.date_posted) == today.year
        )
    
    if search_query:
        query = query.filter(Expense.title.contains(search_query))
    if category_filter and category_filter != 'All' and category_filter is not None:
        query = query.filter(Expense.category == category_filter)
    
    expenses = query.order_by(Expense.date_posted.desc()).all()

    needs_total = sum(e.amount for e in expenses if e.category == 'Need')
    wants_total = sum(e.amount for e in expenses if e.category == 'Want')
    savings_total = sum(e.amount for e in expenses if e.category == 'Savings')
    chart_data = [float(needs_total), float(wants_total), float(savings_total)]

    total_spent = sum(e.amount for e in expenses if e.category in ['Need', 'Want'])
    # Use separate budget fields for each mode
    budget = current_user.daily_budget if is_daily else current_user.budget

    # In daily mode, override display totals to show TODAY only
    if is_daily:
        today_expenses = [e for e in expenses if e.date_posted.date() == datetime.utcnow().date()]
        display_spent   = sum(e.amount for e in today_expenses if e.category in ['Need', 'Want'])
        display_savings = sum(e.amount for e in today_expenses if e.category == 'Savings')
        display_needs   = sum(e.amount for e in today_expenses if e.category == 'Need')
        display_wants   = sum(e.amount for e in today_expenses if e.category == 'Want')
        display_chart   = [float(display_needs), float(display_wants), float(display_savings)]
    else:
        display_spent   = total_spent
        display_savings = savings_total
        display_chart   = chart_data

    # --- FORECASTING & ADVICE ---
    _, last_day = calendar.monthrange(today.year, today.month)
    current_day = today.day
    days_remaining = last_day - current_day + 1

    if is_daily:
        # Daily mode: budget = per-day allowance
        # Compare today's spending against daily budget
        today_spent = display_spent  # already filtered to today above
        daily_budget = budget
        remaining_today = daily_budget - today_spent
        monthly_equivalent = daily_budget * last_day

        if today_spent == 0:
            advice = f"Today's budget: ₹{daily_budget:,.2f}. You haven't spent anything yet — great start!"
        elif today_spent > daily_budget:
            over = today_spent - daily_budget
            advice = f"🚨 Over today's limit by ₹{over:,.2f}! Try to stay within ₹{daily_budget:,.2f}/day."
        else:
            advice = f"₹{remaining_today:,.2f} left for today. You've used {(today_spent/daily_budget*100):.0f}% of your daily budget."

        if today_spent == 0:
            health_color = "green"
        elif today_spent > daily_budget:
            health_color = "red"
        elif today_spent > daily_budget * 0.8:
            health_color = "yellow"
        else:
            health_color = "green"

        # For progress bar: today's spending vs daily budget
        progress_spent = today_spent
        progress_budget = daily_budget
        budget_label = "Daily Budget"
        spent_label = f"{int(today_spent/daily_budget*100) if daily_budget > 0 else 0}% of today"
        progress_pct = min((today_spent / daily_budget * 100), 100) if daily_budget > 0 else 0

        # Notifications for daily mode
        notifications = []
        if daily_budget > 0:
            pct = (today_spent / daily_budget) * 100
            if today_spent > daily_budget:
                notifications.append({'level': 'danger', 'icon': '🚨',
                    'msg': f"Over today's daily limit by ₹{today_spent - daily_budget:,.2f}!"})
            elif pct >= 90:
                notifications.append({'level': 'warning', 'icon': '⚠️',
                    'msg': f"Almost at today's limit! ₹{remaining_today:,.2f} remaining."})
            elif pct >= 75:
                notifications.append({'level': 'caution', 'icon': '🔔',
                    'msg': f"Used {pct:.0f}% of today's budget. Slow down a bit."})

        # Over-budget IDs: only TODAY's expenses that pushed past daily limit
        over_budget_ids = set()
        high_expense_ids = set()
        medium_expense_ids = set()
        if daily_budget > 0:
            # Only mark over-budget for today
            running = 0
            for e in sorted(
                [x for x in expenses if x.date_posted.date() == today and x.category in ['Need', 'Want']],
                key=lambda x: x.date_posted
            ):
                running += e.amount
                if running > daily_budget:
                    over_budget_ids.add(e.id)
            # Color each expense by its own amount vs daily budget thresholds
            for e in expenses:
                if e.category not in ['Need', 'Want']:
                    continue
                if e.amount >= daily_budget * 0.5:
                    high_expense_ids.add(e.id)
                elif e.amount >= daily_budget * 0.25:
                    medium_expense_ids.add(e.id)

        high_threshold = daily_budget * 0.5
        medium_threshold = daily_budget * 0.25

    else:
        # Monthly mode (original logic)
        if current_day < 3:
            projected_total = total_spent + ((budget / last_day) * days_remaining)
        else:
            burn_rate = total_spent / current_day if current_day > 0 else 0
            projected_total = burn_rate * last_day

        remaining_budget = budget - total_spent
        daily_allowance = remaining_budget / days_remaining if days_remaining > 0 else 0

        if total_spent == 0:
            advice = f"Month started! Your daily safe-to-spend limit is ₹{daily_allowance:,.2f}."
        elif projected_total > budget and current_day >= 3:
            over_by = projected_total - budget
            advice = f"🚨 On track to exceed budget by ₹{over_by:,.2f}. Limit daily spending to ₹{max(0, daily_allowance):,.2f}!"
        else:
            advice = f"₹{remaining_budget:,.2f} left this month. Daily limit: ₹{daily_allowance:,.2f}."

        if total_spent == 0:
            health_color = "green"
        elif projected_total > budget:
            health_color = "red"
        elif projected_total > (budget * 0.8):
            health_color = "yellow"
        else:
            health_color = "green"

        progress_spent = total_spent
        progress_budget = budget
        budget_label = "Monthly Budget"
        spent_label = f"{int(total_spent/budget*100) if budget > 0 else 0}% of budget"
        progress_pct = min((total_spent / budget * 100), 100) if budget > 0 else 0

        notifications = []
        if budget > 0:
            pct = (total_spent / budget) * 100
            remaining_budget = budget - total_spent
            if total_spent > budget:
                notifications.append({'level': 'danger', 'icon': '🚨',
                    'msg': f"Exceeded budget by ₹{total_spent - budget:,.2f}! Stop spending immediately."})
            elif pct >= 90:
                notifications.append({'level': 'warning', 'icon': '⚠️',
                    'msg': f"Critical: {pct:.0f}% of budget used. Only ₹{remaining_budget:,.2f} left!"})
            elif pct >= 80:
                notifications.append({'level': 'caution', 'icon': '🔔',
                    'msg': f"Heads up: Used {pct:.0f}% of budget. Slow down on spending."})
            if current_day >= 3 and projected_total > budget and total_spent <= budget:
                notifications.append({'level': 'warning', 'icon': '📈',
                    'msg': f"At this rate you'll exceed budget by ₹{projected_total - budget:,.2f} by month end."})

        over_budget_ids = set()
        if budget > 0:
            running = 0
            for e in sorted(expenses, key=lambda x: x.date_posted):
                if e.category in ['Need', 'Want']:
                    running += e.amount
                    if running > budget:
                        over_budget_ids.add(e.id)

        high_threshold = budget * 0.20
        medium_threshold = budget * 0.10
        # In monthly mode, color by amount vs budget thresholds (same as before)
        high_expense_ids = set()
        medium_expense_ids = set()
        for e in expenses:
            if e.category not in ['Need', 'Want']:
                continue
            if e.amount >= high_threshold:
                high_expense_ids.add(e.id)
            elif e.amount >= medium_threshold:
                medium_expense_ids.add(e.id)

    all_dates = db.session.query(func.date(Expense.date_posted)).filter_by(user_id=current_user.id)\
        .distinct().order_by(func.date(Expense.date_posted).desc()).all()
    
    current_streak = 0
    if all_dates:
        latest_date = all_dates[0][0]
        if not hasattr(latest_date, 'day'):
            latest_date = datetime.strptime(str(latest_date).split(' ')[0], '%Y-%m-%d').date()
            
        if latest_date == today or latest_date == (today - timedelta(days=1)):
            check_date = latest_date
            for d in all_dates:
                d_obj = d[0] if hasattr(d[0], 'day') else datetime.strptime(str(d[0]).split(' ')[0], '%Y-%m-%d').date()
                if d_obj == check_date:
                    current_streak += 1
                    check_date -= timedelta(days=1)
                else:
                    break

    quick_expenses = QuickExpense.query.filter_by(user_id=current_user.id).all()

    # --- PREVIOUS MONTHS BUDGET HISTORY ---
    monthly_history = []
    for i in range(1, 7):  # last 6 months
        first_of_current = today.replace(day=1)
        target = (first_of_current - timedelta(days=i * 30)).replace(day=1)
        m, y = target.month, target.year
        spent = db.session.query(func.sum(Expense.amount)).filter(
            Expense.user_id == current_user.id,
            Expense.category.in_(['Need', 'Want']),
            func.extract('month', Expense.date_posted) == m,
            func.extract('year', Expense.date_posted) == y
        ).scalar() or 0
        saved = db.session.query(func.sum(Expense.amount)).filter(
            Expense.user_id == current_user.id,
            Expense.category == 'Savings',
            func.extract('month', Expense.date_posted) == m,
            func.extract('year', Expense.date_posted) == y
        ).scalar() or 0
        bh = BudgetHistory.query.filter_by(
            user_id=current_user.id, month=m, year=y
        ).first()
        month_budget = bh.budget if bh else None
        if spent > 0 or saved > 0 or month_budget:
            monthly_history.append({
                'label': target.strftime('%B %Y'),
                'spent': round(float(spent), 2),
                'saved': round(float(saved), 2),
                'budget': round(float(month_budget), 2) if month_budget else None,
            })

    # --- MARK EXPENSES THAT PUSHED TOTAL OVER BUDGET ---
    # (handled above in mode-specific logic)

    # --- SMART INSIGHTS ---
    insights = []
    if len(monthly_history) >= 1:
        prev = monthly_history[0]['spent']
        if prev > 0 and total_spent > 0:
            diff_pct = ((total_spent - prev) / prev) * 100
            if diff_pct > 10:
                insights.append({'icon':'📈','color':'red',  'text': f"Spending up {diff_pct:.0f}% vs last month"})
            elif diff_pct < -10:
                insights.append({'icon':'📉','color':'green','text': f"Spending down {abs(diff_pct):.0f}% vs last month — great job!"})

    if expenses:
        from collections import Counter
        cat_counts = Counter(e.category for e in expenses if e.category in ['Need','Want'])
        if cat_counts:
            top_cat = cat_counts.most_common(1)[0][0]
            insights.append({'icon':'🏷️','color':'accent','text': f"Top category this month: {top_cat}"})

    remaining = budget - total_spent
    if budget > 0 and remaining > 0 and not is_daily:
        insights.append({'icon':'✅','color':'green','text': f"₹{remaining:,.0f} remaining — you're on track!"})

    # --- WISHLIST PREVIEW (top 3 active) ---
    total_savings_all = db.session.query(func.sum(Expense.amount)).filter_by(
        user_id=current_user.id, category='Savings'
    ).scalar() or 0
    wishlist_preview = []
    for item in WishlistItem.query.filter_by(user_id=current_user.id, purchased=False).limit(3).all():
        pct = min((float(total_savings_all) / item.target_amount) * 100, 100) if item.target_amount > 0 else 0
        wishlist_preview.append({'item': item, 'pct': round(pct, 1)})

    # --- LIMIT EXCEEDED POPUP ---
    show_limit_popup = False
    popup_overage = 0
    if is_daily:
        today_spent_val = sum(
            e.amount for e in expenses
            if e.category in ['Need', 'Want'] and e.date_posted.date() == today
        )
        if budget > 0 and today_spent_val > budget:
            show_limit_popup = True
            popup_overage = round(today_spent_val - budget, 2)
    else:
        if budget > 0 and total_spent > budget:
            show_limit_popup = True
            popup_overage = round(total_spent - budget, 2)

    return render_template('index.html', 
                           expenses=expenses, 
                           total_spent=display_spent, 
                           budget=budget,
                           is_daily=is_daily,
                           budget_label=budget_label,
                           spent_label=spent_label,
                           progress_pct=progress_pct,
                           advice=advice, 
                           chart_data=display_chart,
                           streak_count=current_streak,
                           health_color=health_color,
                           quick_expenses=quick_expenses,
                           notifications=notifications,
                           over_budget_ids=over_budget_ids,
                           monthly_history=monthly_history,
                           high_threshold=high_threshold,
                           medium_threshold=medium_threshold,
                           high_expense_ids=high_expense_ids,
                           medium_expense_ids=medium_expense_ids,
                           show_limit_popup=show_limit_popup,
                           popup_overage=popup_overage,
                           insights=insights,
                           wishlist_preview=wishlist_preview,
                           total_savings_all=float(total_savings_all),
                           events=Event.query.filter_by(user_id=current_user.id).all(),
                           active_trip=Event.query.filter_by(user_id=current_user.id, is_active=True).first())

# ==========================================
# --- 2. EXPENSE MANAGEMENT ---
# ==========================================

@main.route('/add', methods=['GET', 'POST'])
@login_required
def add_expense():
    # Detect if user is trying to save a broken streak
    save_streak = request.args.get('save_streak')
    default_date = None
    
    if save_streak == 'true':
        # Automatically suggest yesterday's date
        default_date = (datetime.utcnow() - timedelta(days=1)).strftime('%Y-%m-%d')
        flash("Quick! Log yesterday's expense to revive your streak! 🔥", "info")

    if request.method == 'POST':
        try:
            date_posted = get_processed_date() # From updates.py
            event_id = request.form.get('event_id')
            
            new_expense = Expense(
                title=request.form.get('title'),
                amount=float(request.form.get('amount')),
                category=request.form.get('category'),
                date_posted=date_posted,
                author=current_user,
                event_id=event_id if event_id and event_id != "None" else None
            )
            db.session.add(new_expense)
            db.session.commit()
            return redirect(url_for('main.index'))
        except ValueError:
            flash('Invalid amount entered.', 'danger')
            
    events = Event.query.filter_by(user_id=current_user.id).all()
    return render_template('add_expense.html', events=events, default_date=default_date)

@main.route('/edit/<int:expense_id>', methods=['GET', 'POST'])
@login_required
def edit_expense(expense_id):
    expense = Expense.query.get_or_404(expense_id)
    if expense.author != current_user:
        flash('Permission denied.', 'danger')
        return redirect(url_for('main.index'))
        
    if request.method == 'POST':
        try:
            expense.title = request.form.get('title')
            expense.amount = float(request.form.get('amount'))
            expense.category = request.form.get('category')
            expense.date_posted = get_processed_date() # From updates.py
            
            event_id = request.form.get('event_id')
            expense.event_id = event_id if event_id and event_id != "None" else None
            
            db.session.commit()
            flash('Expense updated!', 'success')
            return redirect(url_for('main.index'))
        except ValueError:
            flash('Error updating expense.', 'danger')
            
    events = Event.query.filter_by(user_id=current_user.id).all()
    return render_template('add_expense.html', title='Edit Expense', expense=expense, events=events)

@main.route('/delete/<int:expense_id>', methods=['POST'])
@login_required
def delete_expense(expense_id):
    expense = Expense.query.get_or_404(expense_id)
    db.session.delete(expense)
    db.session.commit()
    return redirect(url_for('main.index'))

@main.route("/update_budget", methods=['POST'])
@login_required
def update_budget():
    new_budget = request.form.get('new_budget')
    if new_budget:
        now = datetime.utcnow()
        val = float(new_budget)
        if current_user.income_type == 'daily':
            current_user.daily_budget = val
        else:
            current_user.budget = val
            current_user.last_budget_update = now
            # Record in monthly budget history
            existing = BudgetHistory.query.filter_by(
                user_id=current_user.id, month=now.month, year=now.year
            ).first()
            if existing:
                existing.budget = val
            else:
                db.session.add(BudgetHistory(
                    user_id=current_user.id,
                    month=now.month, year=now.year,
                    budget=val
                ))
        db.session.commit()
    return redirect(url_for('main.index'))

# ==========================================
# --- 3. ANALYTICS & EVENTS ---
# ==========================================
@main.route('/history')
@login_required
def history():
    search_query = request.args.get('search')
    query = Expense.query.filter_by(user_id=current_user.id)
    if search_query:
        query = query.filter(Expense.title.contains(search_query))
    all_expenses = query.order_by(Expense.date_posted.desc()).all()

    today = datetime.utcnow()
    trend_labels = []
    trend_values = []

    for i in range(5, -1, -1):
        first_of_current = today.replace(day=1)
        target_date = first_of_current - timedelta(days=i*30) 
        month = target_date.month
        year = target_date.year
        
        month_total = db.session.query(func.sum(Expense.amount)).filter(
            Expense.user_id == current_user.id,
            func.extract('month', Expense.date_posted) == month,
            func.extract('year', Expense.date_posted) == year
        ).scalar() or 0
        
        trend_labels.append(target_date.strftime('%b %Y'))
        trend_values.append(float(month_total))

    return render_template('history.html', 
                           all_expenses=all_expenses,
                           trend_labels=trend_labels,
                           trend_values=trend_values)

@main.route('/savings')
@login_required
def savings_dashboard():
    savings_entries = Expense.query.filter_by(user_id=current_user.id, category='Savings').order_by(Expense.date_posted.desc()).all() 
    total = sum(e.amount for e in savings_entries)
    
    labels = []
    values = []
    running_total = 0
    chart_data_sorted = sorted(savings_entries, key=lambda x: x.date_posted)
    for e in chart_data_sorted:
        running_total += e.amount
        labels.append(e.date_posted.strftime('%d %b'))
        values.append(float(running_total))
        
    return render_template('savings.html', 
                           labels=labels or ["Start"], 
                           values=values or [0], 
                           total_savings=total, 
                           savings_entries=savings_entries)

@main.route('/events')
@login_required
def list_events():
    events_raw = Event.query.filter_by(user_id=current_user.id).all()
    events_data = []
    completed_data = []
    for event in events_raw:
        event.total_budget = float(event.total_budget or 0)
        event.member_count = int(event.member_count or 1)
        event.contribution_per_member = float(event.contribution_per_member or 0)
        event.trip_type = event.trip_type or 'solo'
        event.is_completed = bool(event.is_completed)

        total_spent = sum(float(e.amount) for e in event.expenses if float(e.amount) > 0)
        remaining = event.total_budget - total_spent
        percent = (total_spent / event.total_budget * 100) if event.total_budget > 0 else 0
        row = {
            'obj': event,
            'total_spent': total_spent,
            'remaining': remaining,
            'percent': min(percent, 100),
            'over_budget': total_spent > event.total_budget
        }
        if event.is_completed:
            completed_data.append(row)
        else:
            events_data.append(row)
    return render_template('list_events.html', events=events_data, completed=completed_data)

@main.route('/delete_event/<int:event_id>', methods=['POST'])
@login_required
def delete_event(event_id):
    event = Event.query.get_or_404(event_id)
    db.session.delete(event)
    db.session.commit()
    return redirect(url_for('main.list_events'))

@main.route('/event/new', methods=['GET', 'POST'])
@login_required
def new_event():
    total_savings = db.session.query(func.sum(Expense.amount)).filter_by(
        user_id=current_user.id, category='Savings'
    ).scalar() or 0
    total_savings = float(total_savings)

    # Money left = budget minus what's already spent this month
    today = datetime.utcnow().date()
    spent_this_month = db.session.query(func.sum(Expense.amount)).filter(
        Expense.user_id == current_user.id,
        Expense.category.in_(['Need', 'Want']),
        func.extract('month', Expense.date_posted) == today.month,
        func.extract('year', Expense.date_posted) == today.year
    ).scalar() or 0
    money_left = max(0, float(current_user.budget) - float(spent_this_month))

    if request.method == 'POST':
        name = request.form.get('name')
        description = request.form.get('description')
        trip_type = request.form.get('trip_type', 'solo')
        funding_source = request.form.get('funding_source', 'monthly')

        # Calculate total budget
        if trip_type == 'group':
            member_count = int(request.form.get('member_count', 1))
            contribution = float(request.form.get('contribution_per_member', 0))
            total_budget = member_count * contribution  # full group budget shown on trip
            user_pays = contribution                    # only deduct user's share from their funds
        else:
            total_budget = float(request.form.get('total_budget', 0))
            member_count = 1
            contribution = total_budget
            user_pays = total_budget

        if total_budget <= 0:
            flash('❌ Budget must be greater than zero.', 'danger')
            return render_template('create_event.html', money_left=money_left, total_savings=total_savings)

        # --- VALIDATION (against user's share only) ---
        if funding_source == 'savings' and user_pays > total_savings:
            flash(f'❌ Not enough savings! You have ₹{total_savings:,.0f} but your share is ₹{user_pays:,.0f}.', 'danger')
            return render_template('create_event.html', money_left=money_left, total_savings=total_savings)
        if funding_source == 'monthly' and user_pays > money_left:
            flash(f'❌ Not enough budget left! You have ₹{money_left:,.0f} but your share is ₹{user_pays:,.0f}.', 'danger')
            return render_template('create_event.html', money_left=money_left, total_savings=total_savings)

        event = Event(
            name=name, description=description,
            total_budget=total_budget, organizer=current_user,
            trip_type=trip_type, member_count=member_count,
            contribution_per_member=contribution
        )
        db.session.add(event)

        if funding_source == 'savings':
            db.session.add(Expense(
                title=f'✈️ Trip Fund: {name}', amount=-user_pays,
                category='Savings', date_posted=datetime.utcnow(), author=current_user
            ))
            # Also log as personal PICNIC expense so it shows in recent expenses
            db.session.add(Expense(
                title=f'🏕️ Picnic: {name}', amount=user_pays,
                category='Picnic', date_posted=datetime.utcnow(), author=current_user
            ))
            flash(f'✈️ Trip "{name}" created! Your share ₹{user_pays:,.0f} deducted from Piggy Bank. Group total: ₹{total_budget:,.0f}.', 'success')
        else:
            current_user.budget = max(0, current_user.budget - user_pays)
            # Log as personal PICNIC expense so it shows in recent expenses
            db.session.add(Expense(
                title=f'🏕️ Picnic: {name}', amount=user_pays,
                category='Picnic', date_posted=datetime.utcnow(), author=current_user
            ))
            flash(f'✈️ Trip "{name}" created! Your share ₹{user_pays:,.0f} allocated from monthly budget. Group total: ₹{total_budget:,.0f}.', 'success')

        db.session.commit()
        return redirect(url_for('main.list_events'))

    return render_template('create_event.html', money_left=money_left, total_savings=total_savings)


@main.route('/event/<int:event_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_event(event_id):
    event = Event.query.get_or_404(event_id)
    if event.organizer != current_user:
        flash('Permission denied.', 'danger')
        return redirect(url_for('main.list_events'))

    # Cast stored string values to correct types
    event.total_budget = float(event.total_budget or 0)
    event.member_count = int(event.member_count or 1)
    event.contribution_per_member = float(event.contribution_per_member or 0)
    event.trip_type = event.trip_type or 'solo'

    if request.method == 'POST':
        event.name = request.form.get('name')
        event.description = request.form.get('description')
        trip_type = request.form.get('trip_type', 'solo')
        event.trip_type = trip_type
        if trip_type == 'group':
            event.member_count = int(request.form.get('member_count', 1))
            event.contribution_per_member = float(request.form.get('contribution_per_member', 0))
            # total_budget = user's share (contribution); full group total is members × contribution
            event.total_budget = event.contribution_per_member
        else:
            event.total_budget = float(request.form.get('total_budget', 0))
            event.member_count = 1
            event.contribution_per_member = event.total_budget
        db.session.commit()
        flash(f'Trip "{event.name}" updated!', 'success')
        return redirect(url_for('main.list_events'))

    return render_template('edit_event.html', event=event)


@main.route('/event/<int:event_id>/activate', methods=['POST'])
@login_required
def activate_trip(event_id):
    # Deactivate any currently active trip first
    Event.query.filter_by(user_id=current_user.id, is_active=True).update({'is_active': False})
    event = Event.query.get_or_404(event_id)
    if event.organizer != current_user:
        flash('Permission denied.', 'danger')
        return redirect(url_for('main.list_events'))
    event.is_active = True
    db.session.commit()
    flash(f'🚀 Trip "{event.name}" is now active! All trip expenses will be tracked separately.', 'success')
    return redirect(url_for('main.trip_detail', event_id=event_id))


@main.route('/event/<int:event_id>/end', methods=['POST'])
@login_required
def end_trip(event_id):
    event = Event.query.get_or_404(event_id)
    if event.organizer != current_user:
        flash('Permission denied.', 'danger')
        return redirect(url_for('main.list_events'))
    event.is_active = False
    event.is_completed = True
    event.completed_at = datetime.utcnow()
    db.session.commit()
    flash(f'🎉 Trip "{event.name}" completed! Great memories!', 'success')
    return redirect(url_for('main.list_events'))


@main.route('/event/<int:event_id>/detail')
@login_required
def trip_detail(event_id):
    event = Event.query.get_or_404(event_id)
    if event.organizer != current_user:
        flash('Permission denied.', 'danger')
        return redirect(url_for('main.list_events'))

    event.total_budget = float(event.total_budget or 0)
    event.member_count = int(event.member_count or 1)
    event.contribution_per_member = float(event.contribution_per_member or 0)
    event.trip_type = event.trip_type or 'solo'

    trip_expenses = Expense.query.filter_by(
        user_id=current_user.id, event_id=event_id
    ).order_by(Expense.date_posted.desc()).all()

    total_spent = sum(float(e.amount) for e in trip_expenses if e.amount > 0)
    remaining = event.total_budget - total_spent
    pct = min((total_spent / event.total_budget * 100), 100) if event.total_budget > 0 else 0

    return render_template('trip_detail.html',
                           event=event,
                           trip_expenses=trip_expenses,
                           total_spent=total_spent,
                           remaining=remaining,
                           pct=round(pct, 1))

# ==========================================
# --- 4. OTHER UTILITIES ---
# ==========================================
@main.route('/export-csv')
@login_required
def export_csv():
    expenses = Expense.query.filter_by(user_id=current_user.id).order_by(Expense.date_posted.desc()).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Date', 'Title', 'Category', 'Amount (₹)'])
    for e in expenses:
        writer.writerow([e.date_posted.strftime('%Y-%m-%d'), e.title, e.category, e.amount])
    output.seek(0)
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-disposition": "attachment; filename=Expense_Report.csv"})

@main.route('/advice')
@login_required
def get_advice():
    today = datetime.utcnow().date()
    budget = current_user.budget
    expenses = Expense.query.filter_by(user_id=current_user.id).filter(
        func.extract('month', Expense.date_posted) == today.month,
        func.extract('year', Expense.date_posted) == today.year
    ).all()

    needs_actual = sum(e.amount for e in expenses if e.category == 'Need')
    wants_actual = sum(e.amount for e in expenses if e.category == 'Want')
    savings_actual = sum(e.amount for e in expenses if e.category == 'Savings')

    targets = {'needs': budget * 0.50, 'wants': budget * 0.30, 'savings': budget * 0.20}
    return render_template('advice.html', targets=targets, needs=needs_actual, wants=wants_actual, savings=savings_actual)

@main.route('/analysis')
@login_required
def analysis():
    return monthly_analysis()

@main.route('/monthly-reset', methods=['GET', 'POST'])
@login_required
def monthly_reset():
    today = datetime.utcnow()
    last_month = (today.replace(day=1) - timedelta(days=1))
    lm, ly = last_month.month, last_month.year

    # Calculate last month's spending
    last_month_spent = db.session.query(func.sum(Expense.amount)).filter(
        Expense.user_id == current_user.id,
        Expense.category.in_(['Need', 'Want']),
        func.extract('month', Expense.date_posted) == lm,
        func.extract('year', Expense.date_posted) == ly
    ).scalar() or 0

    leftover = round(float(current_user.budget) - float(last_month_spent), 2)

    if request.method == 'POST':
        # Auto-save leftover to piggy bank if positive
        if leftover > 0:
            saving = Expense(
                title=f'💰 Budget Rollover — {last_month.strftime("%B %Y")}',
                amount=leftover,
                category='Savings',
                date_posted=today,
                author=current_user
            )
            db.session.add(saving)
            flash(f'🎉 ₹{leftover:,.2f} leftover from {last_month.strftime("%B")} saved to your Piggy Bank!', 'success')

        current_user.last_budget_update = today
        db.session.commit()
        return redirect(url_for('main.index'))

    return render_template('monthly_reset.html',
                           today=today,
                           leftover=leftover,
                           last_month_name=last_month.strftime('%B %Y'),
                           last_month_spent=last_month_spent)

@main.route('/streaks')
@login_required
def streaks():
    return streaks_dashboard()

@main.route("/logout")
def logout():
    logout_user()
    return redirect(url_for('main.login'))

# ==========================================
# --- AUTH ---
# ==========================================

@main.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        if User.query.filter_by(email=email).first():
            flash('Email already registered.', 'danger')
            return redirect(url_for('main.register'))
        hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
        user = User(username=username, email=email, password=hashed_pw)
        db.session.add(user)
        db.session.commit()
        login_user(user)
        return redirect(url_for('main.onboarding'))
    return render_template('register.html')

@main.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        if user and bcrypt.check_password_hash(user.password, password):
            login_user(user)
            if not user.income_type:
                return redirect(url_for('main.onboarding'))
            return redirect(url_for('main.index'))
        flash('Invalid email or password.', 'danger')
    return render_template('login.html')

@main.route('/onboarding', methods=['GET', 'POST'])
@login_required
def onboarding():
    if request.method == 'POST':
        income_type = request.form.get('income_type')
        if income_type in ['monthly', 'daily']:
            current_user.income_type = income_type
            db.session.commit()
            flash('Profile set up! Now set your budget to get started.', 'success')
            return redirect(url_for('main.index'))
    return render_template('onboarding.html')

@main.route('/switch-income-type', methods=['POST'])
@login_required
def switch_income_type():
    if current_user.income_type == 'monthly':
        current_user.income_type = 'daily'
        db.session.commit()
        flash('Switched to Daily / Pocket Money mode. 🎒', 'info')
        # Always prompt for daily budget since it's a different value
        return redirect(url_for('main.index', set_budget='daily'))
    else:
        current_user.income_type = 'monthly'
        db.session.commit()
        flash('Switched to Monthly Income mode. 💼', 'info')
        # Only prompt if no budget set yet
        if current_user.budget == 0:
            return redirect(url_for('main.index', set_budget='monthly'))
        return redirect(url_for('main.index'))

# ==========================================
# --- 5. RECEIVED GIFT MONEY ---
# ==========================================

@main.route('/gift-money', methods=['POST'])
@login_required
def gift_money():
    description = request.form.get('description', '').strip() or 'Gift Money'
    amount = request.form.get('amount')
    if not amount:
        flash('Please enter an amount.', 'danger')
        return redirect(url_for('main.index'))
    try:
        expense = Expense(
            title=description,
            amount=float(amount),
            category='Savings',
            date_posted=datetime.utcnow(),
            author=current_user
        )
        db.session.add(expense)
        db.session.commit()
        flash(f'🎁 ₹{float(amount):,.2f} added to your Piggy Bank!', 'success')
    except ValueError:
        flash('Invalid amount.', 'danger')
    return redirect(url_for('main.index'))

# ==========================================
# --- 6. QUICK ADD ---
# ==========================================

@main.route('/quick-add', methods=['GET', 'POST'])
@login_required
def quick_add():
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        amount = request.form.get('amount')
        category = request.form.get('category')
        if title and amount and category:
            qe = QuickExpense(title=title, amount=float(amount), category=category, owner=current_user)
            db.session.add(qe)
            db.session.commit()
            flash(f'"{title}" added to Quick Add list.', 'success')
        else:
            flash('All fields are required.', 'danger')
    return redirect(url_for('main.index'))

@main.route('/quick-add/<int:qe_id>/log', methods=['POST'])
@login_required
def log_quick_expense(qe_id):
    qe = QuickExpense.query.get_or_404(qe_id)
    if qe.owner != current_user:
        flash('Permission denied.', 'danger')
        return redirect(url_for('main.index'))
    expense = Expense(
        title=qe.title,
        amount=qe.amount,
        category=qe.category,
        date_posted=datetime.utcnow(),
        author=current_user
    )
    db.session.add(expense)
    db.session.commit()
    flash(f'"{qe.title}" logged for ₹{qe.amount:.2f}!', 'success')
    return redirect(url_for('main.index'))

@main.route('/quick-add/<int:qe_id>/delete', methods=['POST'])
@login_required
def delete_quick_expense(qe_id):
    qe = QuickExpense.query.get_or_404(qe_id)
    if qe.owner != current_user:
        flash('Permission denied.', 'danger')
        return redirect(url_for('main.index'))
    db.session.delete(qe)
    db.session.commit()
    flash('Quick Add entry removed.', 'info')
    return redirect(url_for('main.index'))

# ==========================================
# --- 7. WISHLIST ---
# ==========================================

@main.route('/wishlist')
@login_required
def wishlist():
    items = WishlistItem.query.filter_by(user_id=current_user.id, purchased=False).order_by(WishlistItem.date_added.desc()).all()
    total_savings = db.session.query(func.sum(Expense.amount)).filter_by(
        user_id=current_user.id, category='Savings'
    ).scalar() or 0

    wishlist_data = []
    newly_achieved = []
    for item in items:
        pct = min((total_savings / item.target_amount) * 100, 100) if item.target_amount > 0 else 0
        achieved = total_savings >= item.target_amount
        if achieved:
            newly_achieved.append(item)
        wishlist_data.append({
            'item': item,
            'pct': round(pct, 1),
            'achieved': achieved,
            'saved': round(min(total_savings, item.target_amount), 2),
            'remaining': round(max(item.target_amount - total_savings, 0), 2)
        })

    return render_template('wishlist.html',
                           wishlist_data=wishlist_data,
                           total_savings=total_savings,
                           newly_achieved=newly_achieved,
                           purchased_items=WishlistItem.query.filter_by(
                               user_id=current_user.id, purchased=True
                           ).order_by(WishlistItem.date_added.desc()).all())

@main.route('/wishlist/add', methods=['POST'])
@login_required
def add_wishlist_item():
    name = request.form.get('name', '').strip()
    emoji = request.form.get('emoji', '🎯').strip() or '🎯'
    target = request.form.get('target_amount')
    if not name or not target:
        flash('Name and target amount are required.', 'danger')
        return redirect(url_for('main.wishlist'))
    try:
        item = WishlistItem(name=name, emoji=emoji, target_amount=float(target), owner=current_user)
        db.session.add(item)
        db.session.commit()
        flash(f'"{name}" added to your wishlist!', 'success')
    except ValueError:
        flash('Invalid amount.', 'danger')
    return redirect(url_for('main.wishlist'))

@main.route('/wishlist/<int:item_id>/purchased', methods=['POST'])
@login_required
def mark_purchased(item_id):
    item = WishlistItem.query.get_or_404(item_id)
    if item.owner != current_user:
        flash('Permission denied.', 'danger')
        return redirect(url_for('main.wishlist'))

    # Deduct the item's cost from piggy bank savings
    deduction = Expense(
        title=f'🛍️ Purchased: {item.name}',
        amount=-item.target_amount,   # negative = deduction
        category='Savings',
        date_posted=datetime.utcnow(),
        author=current_user
    )
    db.session.add(deduction)

    item.purchased = True
    db.session.commit()
    flash(f'🎉 Congrats on your {item.name}! ₹{item.target_amount:,.0f} deducted from Piggy Bank.', 'success')
    return redirect(url_for('main.wishlist'))

@main.route('/wishlist/<int:item_id>/delete', methods=['POST'])
@login_required
def delete_wishlist_item(item_id):
    item = WishlistItem.query.get_or_404(item_id)
    if item.owner != current_user:
        flash('Permission denied.', 'danger')
        return redirect(url_for('main.wishlist'))
    db.session.delete(item)
    db.session.commit()
    return redirect(url_for('main.wishlist'))
