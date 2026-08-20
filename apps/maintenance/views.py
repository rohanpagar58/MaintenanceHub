import json
import re
import datetime
from django.shortcuts import render
from django.http import HttpResponse, Http404, JsonResponse
from django.views.decorators.http import require_http_methods
from apps.super_user.views import apartment_user_required, get_mongo_db, get_mongo_collection, get_user_collection
from apps.maintenance.pdf_generator import build_duration_display, generate_receipt_pdf
from bson.objectid import ObjectId

def get_receipt_collection():
    return get_mongo_db()['reciept']

@apartment_user_required
def create_receipt_page(request):
    society_id = request.session.get('apartment_society_id')
    user_col = get_user_collection()
    
    # Fetch all members for the dropdown
    members_cursor = user_col.find({'society_id': society_id}).sort('index_id', 1)
    members = []
    for m in members_cursor:
        members.append({
            'id': str(m['_id']),
            'index_id': m.get('index_id', ''),
            'name': m.get('name', ''),
            'flat_number': m.get('flat_number', ''),
        })
        
    context = {
        'society_name': request.session.get('apartment_society'),
        'society_id': society_id,
        'apartment_block': request.session.get('apartment_block'),
        'financial_year_start_month': request.session.get('financial_year_start_month', 4),
        'members': members,
    }
    return render(request, 'maintenance/receipt_form.html', context)

@apartment_user_required
@require_http_methods(["POST"])
def save_receipt_view(request):
    try:
        data = json.loads(request.body)
        society_id = request.session.get('apartment_society_id')
        
        member_id = data.get('member_id', '').strip()
        duration_mode = data.get('duration_mode', '').strip()
        duration_details = data.get('duration_details', {})
        amount = data.get('amount')
        payment_mode = data.get('payment_mode', '').strip()
        payment_date = data.get('payment_date', '').strip()
        remarks = data.get('remarks', '').strip()
        
        if not member_id or not duration_mode or not amount or not payment_mode or not payment_date:
            return JsonResponse({'success': False, 'error': 'Please fill all required fields.'}, status=400)
            
        try:
            amount = float(amount)
            if amount <= 0:
                raise ValueError
        except ValueError:
            return JsonResponse({'success': False, 'error': 'Amount must be a positive number.'}, status=400)
            
        user_col = get_user_collection()
        member = user_col.find_one({'_id': ObjectId(member_id), 'society_id': society_id})
        if not member:
            return JsonResponse({'success': False, 'error': 'Member not found.'}, status=404)
            
        receipt_col = get_receipt_collection()
        
        last_receipt = receipt_col.find_one(
            {'society_id': society_id},
            sort=[('receipt_no', -1)]
        )
        receipt_no = 1
        if last_receipt and 'receipt_no' in last_receipt:
            receipt_no = last_receipt['receipt_no'] + 1
            
        receipt_doc = {
            'society_id': society_id,
            'receipt_no': receipt_no,
            'member_id': member_id,
            'index_id': member.get('index_id'),
            'member_name': member.get('name'),
            'flat_number': member.get('flat_number'),
            'duration_mode': duration_mode,
            'duration_details': duration_details,
            'amount': amount,
            'payment_mode': payment_mode,
            'payment_date': payment_date,
            'remarks': remarks,
            'created_at': datetime.datetime.utcnow().isoformat(),
            'created_by_email': request.session.get('apartment_email')
        }
        
        result = receipt_col.insert_one(receipt_doc)
        
        return JsonResponse({
            'success': True,
            'message': f'Receipt #{receipt_no:04d} created successfully!',
            'receipt_id': str(result.inserted_id)
        })
        
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid request data.'}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Server error: {str(e)}'}, status=500)

@apartment_user_required
@require_http_methods(["GET"])
def member_receipt_history_view(request, member_id):
    try:
        society_id = request.session.get('apartment_society_id')
        receipt_col = get_receipt_collection()
        
        # Query receipts for this member and this society
        receipts_cursor = receipt_col.find(
            {'society_id': society_id, 'member_id': member_id}
        ).sort('created_at', -1)
        
        receipts = []
        for r in receipts_cursor:
            receipts.append({
                'id': str(r['_id']),
                'receipt_no': r.get('receipt_no'),
                'amount': r.get('amount'),
                'payment_date': r.get('payment_date'),
                'payment_mode': r.get('payment_mode'),
                'duration_mode': r.get('duration_mode'),
                'duration_details': r.get('duration_details'),
                'created_at': r.get('created_at'),
            })
            
        return JsonResponse({'success': True, 'receipts': receipts})
        
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Server error: {str(e)}'}, status=500)

@apartment_user_required
@require_http_methods(["GET"])
def download_receipt_pdf_view(request, receipt_id):
    try:
        society_id = request.session.get('apartment_society_id')
        receipt_col = get_receipt_collection()

        receipt = receipt_col.find_one({'_id': ObjectId(receipt_id), 'society_id': society_id})
        if not receipt:
            raise Http404("Receipt not found")

        society = get_mongo_collection().find_one({'society_id': society_id})
        if not society:
            raise Http404("Society not found")

        pdf_buffer = generate_receipt_pdf(receipt, society)

        response = HttpResponse(pdf_buffer.read(), content_type='application/pdf')

        # Build safe filename from member name and duration details
        member_name = str(receipt.get('member_name', 'Member')).strip()
        safe_member_name = re.sub(r'[^A-Za-z0-9_.-]', '', member_name.replace(' ', '_'))

        display_str = build_duration_display(receipt, society, month_sep="-", year_sep="_")
        safe_duration = re.sub(r'[^A-Za-z0-9_.-]', '', display_str)
        filename = f"{safe_member_name}_{safe_duration}.pdf"
        
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
        
    except Http404:
        raise
    except ImportError as ie:
        return JsonResponse({'success': False, 'error': str(ie)}, status=501)
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Server error: {str(e)}'}, status=500)
