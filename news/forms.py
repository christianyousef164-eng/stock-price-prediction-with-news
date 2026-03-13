# newsapp/forms.py

from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from .models import UserStockList, Stock # IMPORT Stock here!

class CustomUserCreationForm(UserCreationForm):
    """
    Custom form for user registration that automatically creates 
    a corresponding UserStockList upon successful user creation
    and populates it with 5 default stocks.
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['password1'].help_text = None 
        self.fields['password2'].help_text = None
    
    class Meta:
        model = User
        fields = ("username",)

    def save(self, commit=True):
        # 1. Call the parent save method to create the User object
        user = super().save(commit=True)
        
        # 2. Create the linked UserStockList for the new user
        user_stock_list = UserStockList.objects.create(user=user)
        
        # 3. Define the 5 default famous stocks
        default_stocks = [
            {'symbol': 'AAPL', 'company_name': 'Apple Inc.'},
            {'symbol': 'MSFT', 'company_name': 'Microsoft Corp.'},
            {'symbol': 'GOOGL', 'company_name': 'Alphabet Inc.'},
            {'symbol': 'AMZN', 'company_name': 'Amazon.com Inc.'},
            {'symbol': 'TSLA', 'company_name': 'Tesla Inc.'},
        ]
        
        # 4. Get or create these stocks in the DB, then add to the user's watchlist
        for stock_data in default_stocks:
            stock_obj, created = Stock.objects.get_or_create(
                symbol=stock_data['symbol'],
                defaults={'company_name': stock_data['company_name']}
            )
            # Link the stock to the user's newly created watchlist
            user_stock_list.stocks.add(stock_obj)
        
        return user