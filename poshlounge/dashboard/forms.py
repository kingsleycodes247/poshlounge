from django import forms
from core.models import Product, Category, User, Table
from decimal import Decimal

class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ['name', 'category', 'sku', 'description', 'current_price', 
                  'stock_quantity', 'min_stock_level', 'unit_of_measure', 
                  'is_available', 'requires_kitchen']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
        }

class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ['name', 'description', 'icon', 'is_active']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 2}),
        }

class InventoryAdjustmentForm(forms.Form):
    ADJUSTMENT_TYPES = [
        ('purchase', 'Purchase/Restock'),
        ('adjustment', 'Manual Adjustment'),
        ('wastage', 'Wastage/Loss'),
    ]
    
    quantity = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal('0.01')
    )
    adjustment_type = forms.ChoiceField(choices=ADJUSTMENT_TYPES)
    notes = forms.CharField(widget=forms.Textarea(attrs={'rows': 3}), required=False)