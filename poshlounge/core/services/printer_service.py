from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.db import transaction
from django.utils import timezone
from django.core.cache import cache
from decimal import Decimal, InvalidOperation

from core.models import (
    User, Order, OrderItem, Product, Table, Category,
    Payment, Shift, AuditLog
)
import uuid
import logging

logger = logging.getLogger(__name__)



def generate_escpos_receipt(data):
    """
    Generate ESC/POS commands for thermal printer.
    Returns byte array of printer commands.
    """
    # ESC/POS command constants
    ESC = b'\x1b'
    GS = b'\x1d'
    
    commands = []
    
    # Initialize printer
    commands.append(ESC + b'@')  # Initialize
    
    # Set alignment center
    commands.append(ESC + b'a' + b'\x01')
    
    # Business name (double height, bold)
    commands.append(ESC + b'!' + b'\x38')  # Double height + bold
    commands.append(data['business_name'].encode('utf-8') + b'\n')
    
    # Reset formatting
    commands.append(ESC + b'!' + b'\x00')
    
    # Address and tax ID
    commands.append(data['address'].encode('utf-8') + b'\n')
    commands.append(data['tax_id'].encode('utf-8') + b'\n')
    
    # Separator
    commands.append(b'-' * 42 + b'\n')
    
    # Receipt details (left aligned)
    commands.append(ESC + b'a' + b'\x00')  # Left align
    
    commands.append(f"Receipt #: {data['receipt_number']}\n".encode('utf-8'))
    commands.append(f"Order #: {data['order_number']}\n".encode('utf-8'))
    commands.append(f"Date: {data['date']}\n".encode('utf-8'))
    commands.append(f"Table: {data['table']}\n".encode('utf-8'))
    commands.append(f"Waiter: {data['waiter']}\n".encode('utf-8'))
    
    commands.append(b'-' * 42 + b'\n')
    
    # Items header
    commands.append(ESC + b'!' + b'\x08')  # Bold
    commands.append(b'Item                 Qty    Price   Total\n')
    commands.append(ESC + b'!' + b'\x00')  # Reset
    
    # Items
    for item in data['items']:
        name = item['name'][:20].ljust(20)
        qty = f"{item['quantity']:.1f}".rjust(6)
        price = f"{item['unit_price']:.0f}".rjust(7)
        total = f"{item['subtotal']:.0f}".rjust(7)
        
        line = f"{name}{qty}{price}{total}\n"
        commands.append(line.encode('utf-8'))
    
    commands.append(b'-' * 42 + b'\n')
    
    # Totals (right aligned)
    commands.append(ESC + b'a' + b'\x02')  # Right align
    
    commands.append(f"Subtotal:    {data['subtotal']:.2f} FCFA\n".encode('utf-8'))
    commands.append(f"Tax (19.25%): {data['tax']:.2f} FCFA\n".encode('utf-8'))
    
    # Total (bold, larger)
    commands.append(ESC + b'!' + b'\x18')  # Bold + double height
    commands.append(f"TOTAL:       {data['total']:.2f} FCFA\n".encode('utf-8'))
    commands.append(ESC + b'!' + b'\x00')  # Reset
    
    # Payment info
    commands.append(f"\nPaid ({data['payment_method']}): {data['amount_paid']:.2f} FCFA\n".encode('utf-8'))
    
    if data['transaction_ref']:
        commands.append(f"Ref: {data['transaction_ref']}\n".encode('utf-8'))
    
    # Footer (center)
    commands.append(ESC + b'a' + b'\x01')  # Center
    commands.append(b'\n')
    commands.append(b'Thank you for dining with us!\n')
    commands.append(b'Visit us again soon\n')
    
    # Cut paper
    commands.append(b'\n\n\n')
    commands.append(GS + b'V' + b'\x00')  # Full cut
    
    # Open cash drawer (for cash payments)
    if data['payment_method'] == 'Cash':
        commands.append(ESC + b'p' + b'\x00' + b'\x19' + b'\xfa')
    
    return b''.join(commands)


def send_to_printer(commands):
    """
    Send ESC/POS commands to network printer.
    Returns True if successful, False otherwise.
    """
    from django.conf import settings
    import socket
    
    try:
        # Create socket connection to printer
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        
        # Connect to printer
        sock.connect((settings.ESC_POS_PRINTER_IP, settings.ESC_POS_PRINTER_PORT))
        
        # Send commands
        sock.sendall(commands)
        
        # Close connection
        sock.close()
        
        logger.info("Receipt sent to printer successfully")
        return True
        
    except socket.timeout:
        logger.error("Printer connection timeout")
        return False
    except socket.error as e:
        logger.error(f"Printer socket error: {str(e)}")
        return False
    except Exception as e:
        logger.error(f"Printer error: {str(e)}")
        return False