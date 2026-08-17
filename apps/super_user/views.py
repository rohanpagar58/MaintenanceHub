import json
import re
import datetime
from functools import wraps

from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.conf import settings
from pymongo import MongoClient
from pymongo.errors import PyMongoError


# ─────────────────────────────────────────────
#  MongoDB helper
# ─────────────────────────────────────────────

def get_mongo_db():
    """Return the PDF database client."""
    client = MongoClient(
        getattr(settings, 'MONGO_URI', 'mongodb://localhost:27017/'),
        serverSelectionTimeoutMS=3000
    )
    db_name = getattr(settings, 'MONGO_DB_NAME', 'PDF')
    return client[db_name]


def get_mongo_collection():
    """Return the companies collection from PDF database."""
    return get_mongo_db()['companies']


# ─────────────────────────────────────────────
#  Super-user session decorator
# ─────────────────────────────────────────────

def super_user_required(view_func):
    """Redirect to login if the user is not authenticated as super_user."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.session.get('is_super_user'):
            return redirect('/auth/')
        return view_func(request, *args, **kwargs)
    return wrapper


# ─────────────────────────────────────────────
#  Login
# ─────────────────────────────────────────────

def login_page(request):
    """Render the login page."""
    if request.session.get('is_super_user'):
        return redirect('/auth/register-apartment/')
    return render(request, 'auth/login.html')



@require_http_methods(["POST"])
def login_view(request):
    """
    Handle login form submission.
    Verifies email & password against the MongoDB PDF > companies collection.
    Sets session flag for super_user on success.
    """
    try:
        data     = json.loads(request.body)
        email    = data.get('email', '').strip().lower()
        password = data.get('password', '').strip()

        if not email or not password:
            return JsonResponse({'success': False, 'error': 'Email and password are required.'}, status=400)

        collection = get_mongo_collection()
        company    = collection.find_one({'email': email})

        if not company:
            return JsonResponse({'success': False, 'error': 'No account found with this email.'}, status=401)

        stored_password = company.get('password', '')
        if stored_password != password:
            return JsonResponse({'success': False, 'error': 'Invalid password.'}, status=401)

        # Set super_user session
        request.session['is_super_user']    = True
        request.session['super_user_email'] = email
        request.session['super_user_name']  = company.get('name', email)

        return JsonResponse({
            'success':  True,
            'message':  f"Welcome, {company.get('name', email)}!",
            'redirect': '/auth/register-apartment/',
        })

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid request data.'}, status=400)
    except PyMongoError:
        return JsonResponse({'success': False, 'error': 'Database connection error. Please ensure MongoDB service is running.'}, status=500)
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Server error: {str(e)}'}, status=500)


# ─────────────────────────────────────────────
#  Logout
# ─────────────────────────────────────────────

def logout_view(request):
    """Clear super_user session and redirect to login."""
    request.session.flush()
    return redirect('/auth/')


# ─────────────────────────────────────────────
#  Register Apartment (super_user only)
# ─────────────────────────────────────────────

@super_user_required
def register_apartment_page(request):
    """Render the Register Apartment page."""
    return render(request, 'super_user/register_apartment.html', {
        'super_user_name': request.session.get('super_user_name', 'Super User'),
    })


@super_user_required
@require_http_methods(["POST"])
def register_apartment_view(request):
    """
    Save a new apartment entry to the companies collection in MongoDB.
    Fields saved: society_id (auto-generated), society_name, block, chairman_email, registered_at.
    """
    try:
        data            = json.loads(request.body)
        society_name    = data.get('society_name', '').strip()
        block           = data.get('block', '').strip()         # optional
        chairman_email  = data.get('chairman_email', '').strip().lower()
        password        = data.get('password', '').strip()

        if not society_name or not chairman_email:
            return JsonResponse({'success': False, 'error': 'Society name and chairman email are required.'}, status=400)

        if not password or len(password) < 6:
            return JsonResponse({'success': False, 'error': 'Password must be at least 6 characters.'}, status=400)

        collection = get_mongo_collection()

        # Auto-generate unique society_id (e.g. 1, 2, 3, ...)
        count = collection.count_documents({}) + 1
        society_id = f"{count:03d}"
        while collection.find_one({'society_id': society_id}):
            count += 1
            society_id = f"{count:03d}"

        apartment_doc = {
            'society_id':     society_id,
            'society_name':   society_name,
            'block':          block,
            'chairman_email': chairman_email,
            'password':       password,
            'registered_at':  datetime.datetime.utcnow().isoformat(),
        }

        result = collection.insert_one(apartment_doc)

        return JsonResponse({
            'success': True,
            'message': f'Apartment in {block}, {society_name} (ID: {society_id}) registered successfully!',
            'id':      str(result.inserted_id),
            'society_id': society_id,
            'apartment': {
                'id': str(result.inserted_id),
                'society_id': society_id,
                'society_name': society_name,
                'block': block,
                'chairman_email': chairman_email,
                'registered_at': apartment_doc['registered_at'],
            }
        })

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid request data.'}, status=400)
    except PyMongoError:
        return JsonResponse({'success': False, 'error': 'Database connection error. Please ensure MongoDB service is running.'}, status=500)
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Server error: {str(e)}'}, status=500)


@super_user_required
@require_http_methods(["GET"])
def get_apartments_list_view(request):
    """
    Fetch all registered apartments from MongoDB companies collection.
    Also returns aggregated stats and unique society names for dynamic features.
    """
    try:
        collection = get_mongo_collection()
        query = {'society_id': {'$exists': True}}
        
        # Optional search filter from query param
        search_q = request.GET.get('q', '').strip()
        if search_q:
            regex_pat = re.compile(re.escape(search_q), re.IGNORECASE)
            query['$or'] = [
                {'society_name': regex_pat},
                {'block': regex_pat},
                {'chairman_email': regex_pat},
                {'society_id': regex_pat},
            ]

        cursor = collection.find(query)
        apartments = []
        all_docs = list(collection.find({'society_id': {'$exists': True}}))

        unique_societies = set()
        unique_chairmen = set()

        for doc in all_docs:
            soc_name = doc.get('society_name', '')
            ch_email = doc.get('chairman_email', '')
            if soc_name:
                unique_societies.add(soc_name)
            if ch_email:
                unique_chairmen.add(ch_email)

        for doc in cursor:
            apartments.append({
                'id': str(doc['_id']),
                'society_id': doc.get('society_id', ''),
                'society_name': doc.get('society_name', ''),
                'block': doc.get('block', ''),
                'chairman_email': doc.get('chairman_email', ''),
                'registered_at': doc.get('registered_at', ''),
            })

        # Sort apartments by society_id / registered_at descending
        apartments.sort(key=lambda x: x.get('registered_at', '') or x.get('society_id', ''), reverse=True)

        stats = {
            'total_apartments': len(all_docs),
            'total_societies': len(unique_societies),
            'unique_chairmen': len(unique_chairmen),
        }

        return JsonResponse({
            'success': True,
            'apartments': apartments,
            'stats': stats,
            'societies': sorted(list(unique_societies)),
        })
    except PyMongoError:
        return JsonResponse({'success': False, 'error': 'Database error while fetching apartments.'}, status=500)
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Server error: {str(e)}'}, status=500)


@super_user_required
@require_http_methods(["GET"])
def check_duplicate_view(request):
    """
    Check if an apartment with the specified society_name and block already exists.
    """
    try:
        society_name = request.GET.get('society_name', '').strip()
        block = request.GET.get('block', '').strip()

        if not society_name or not block:
            return JsonResponse({'success': True, 'exists': False})

        collection = get_mongo_collection()
        soc_pattern = re.compile(f"^{re.escape(society_name)}$", re.IGNORECASE)
        block_pattern = re.compile(f"^{re.escape(block)}$", re.IGNORECASE)

        existing = collection.find_one({
            'society_name': soc_pattern,
            'block': block_pattern,
            'society_id': {'$exists': True}
        })

        return JsonResponse({
            'success': True,
            'exists': bool(existing),
            'existing_id': existing.get('society_id') if existing else None
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@super_user_required
@require_http_methods(["DELETE", "POST"])
def delete_apartment_view(request, society_id):
    """
    Delete an apartment entry from MongoDB by society_id.
    """
    try:
        collection = get_mongo_collection()
        result = collection.delete_one({'society_id': society_id})

        if result.deleted_count > 0:
            return JsonResponse({
                'success': True,
                'message': f'Apartment ID {society_id} deleted successfully.',
                'society_id': society_id,
            })
        else:
            return JsonResponse({'success': False, 'error': 'Apartment not found.'}, status=404)
    except PyMongoError:
        return JsonResponse({'success': False, 'error': 'Database error during deletion.'}, status=500)
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Server error: {str(e)}'}, status=500)


