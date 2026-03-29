from datetime import datetime
from flask import request

def get_processed_date():
    """Extracts and converts the date from form input, defaulting to current UTC time."""
    date_str = request.form.get('date')
    if date_str:
        try:
            return datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            return datetime.utcnow()
    return datetime.utcnow()

def populate_edit_form(expense):
    """Helper to return data formatted for an edit form."""
    return {
        'title': expense.title,
        'amount': expense.amount,
        'category': expense.category,
        'date': expense.date_posted.strftime('%Y-%m-%d')
    }