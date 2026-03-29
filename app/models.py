from datetime import datetime
from app import db, login_manager
from flask_login import UserMixin

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(20), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(60), nullable=False)
    budget = db.Column(db.Float, default=0.0)
    daily_budget = db.Column(db.Float, default=0.0)
    income_type = db.Column(db.String(20), nullable=True)  # 'monthly' or 'daily'
    
    # Supports the Monthly Reset feature
    last_budget_update = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    expenses = db.relationship('Expense', backref='author', lazy=True)
    events = db.relationship('Event', backref='organizer', lazy=True)
    quick_expenses = db.relationship('QuickExpense', backref='owner', lazy=True)
    wishlist_items = db.relationship('WishlistItem', backref='owner', lazy=True)

    def __repr__(self):
        return f"User('{self.username}', '{self.email}', Budget: '{self.budget}')"

class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    # This field correctly handles both automatic current dates and manual past dates
    date_posted = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    amount = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(20), nullable=False) # 'Need', 'Want', 'Savings'
    
    # Foreign Keys
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=True)

    def __repr__(self):
        return f"Expense('{self.title}', '{self.amount}', '{self.date_posted}')"

class QuickExpense(db.Model):
    """Saved daily expense templates for one-click logging."""
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(20), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    def __repr__(self):
        return f"QuickExpense('{self.title}', '{self.amount}')"

class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=True)
    total_budget = db.Column(db.Float, nullable=False)
    date_created = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    trip_type = db.Column(db.String(10), default='solo')       # 'solo' or 'group'
    member_count = db.Column(db.Integer, default=1)
    contribution_per_member = db.Column(db.Float, default=0.0)
    is_active = db.Column(db.Boolean, default=False)
    is_completed = db.Column(db.Boolean, default=False)
    completed_at = db.Column(db.DateTime, nullable=True)

    # Foreign Keys
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    # Relationship to expenses within this event/trip
    expenses = db.relationship('Expense', backref='event_parent', lazy=True)

    def __repr__(self):
        return f"Event('{self.name}', Budget: '{self.total_budget}')"

class WishlistItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    emoji = db.Column(db.String(10), nullable=False, default='🎯')
    target_amount = db.Column(db.Float, nullable=False)
    purchased = db.Column(db.Boolean, default=False)
    date_added = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    def __repr__(self):
        return f"WishlistItem('{self.name}', '{self.target_amount}')"

class BudgetHistory(db.Model):
    """Stores the budget set for each month per user."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    month = db.Column(db.Integer, nullable=False)
    year = db.Column(db.Integer, nullable=False)
    budget = db.Column(db.Float, nullable=False)
    recorded_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"BudgetHistory(user={self.user_id}, {self.month}/{self.year}, ₹{self.budget})"
